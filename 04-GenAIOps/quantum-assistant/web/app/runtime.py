"""Estado do processo web: cria o Engine, faz o bootstrap em background e guarda a prontidao.

Por que em background: o ECS so manda trafego quando o /ready do target group
responde 200. Se o bootstrap rodasse antes do servidor abrir a porta, o /health
tambem ficaria mudo e o ALB nao saberia distinguir "subindo" de "morto". Aqui o
servidor abre na hora, /health responde 200 e /ready responde 503 com os checks
ate o Neo4j aceitar conexao, o grafo carregar e o indice vetorial ficar ONLINE.
"""
import hashlib
import json
import os
import re
import shutil
import sys
import tarfile
import threading
import time

from core import ROOT

# Datas simuladas da pergunta (nao sao o relogio do sistema).
PRESET_DATES = ["2026-11-14", "2027-11-26", "2027-12-06"]
TENANTS = ["quantum", "parceiro-x"]
USER_ROLES = ["customer", "agent"]
STRATEGIES = ["bm25", "vector", "graph"]
DATA_DIR = os.environ.get("QC_DATA_DIR", "/tmp/qc-data")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def log(event, **fields):
    """Log JSON de uma linha no stdout (CloudWatch). Nunca recebe pergunta, token ou chave."""
    line = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z", "service": "qc-assistant-web",
            "event": event, "environment": os.environ.get("QC_ENVIRONMENT", "local"),
            "release_id": os.environ.get("QC_RELEASE_ID")}
    line.update(fields)
    sys.stdout.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()


def env_versions():
    """Identidade do release que vem do deploy, nao do codigo."""
    return {
        "git_sha": os.environ.get("QC_GIT_SHA") or "unknown",
        "image_digest": os.environ.get("QC_IMAGE_DIGEST") or "unknown",
        "release_id": os.environ.get("QC_RELEASE_ID") or "local-dev",
        # O ambiente tambem e identidade do deploy: sem ele, /lab/* e versions
        # nao dizem se a resposta veio do live ou do candidate.
        "environment": os.environ.get("QC_ENVIRONMENT", "local"),
    }


_static_cache = {}


def static_versions(root=ROOT, data_root=None):
    """Versoes que nao dependem do Neo4j (usadas no build_info e antes do bootstrap)."""
    key = (root, data_root)
    if key in _static_cache:
        return dict(_static_cache[key])
    out = {}
    try:
        from lab import engine as lab_engine
        if hasattr(lab_engine, "static_versions"):
            kwargs = {"root": root}
            if data_root:
                kwargs["data_root"] = data_root
            out = dict(lab_engine.static_versions(**kwargs))
    except Exception as err:  # noqa: BLE001 - engine ausente nao pode derrubar o /health
        log("versions.static_fallback", reason=type(err).__name__)
    if not out:
        # Mesmas funcoes do core legado: aplicacao, prompt, corpus, BM25 e eval set.
        from core.assistant import Assistant
        out = dict(Assistant(root=root).versions)
    out.setdefault("model_version", "none")
    _static_cache[key] = dict(out)
    return out


class BundleError(Exception):
    def __init__(self, reason, detail):
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def _safe_extract(tar_path, dest):
    """Extrai sem permitir caminho absoluto, '..', link ou dispositivo (path traversal)."""
    with tarfile.open(tar_path, "r:gz") as tar:
        for member in tar.getmembers():
            name = member.name
            if name.startswith("/") or ".." in name.split("/") or not (member.isfile() or member.isdir()):
                raise BundleError("data_bundle_unsafe_member", f"membro recusado no bundle: {name[:80]}")
        tar.extractall(dest, filter="data")


def fetch_data_bundle(uri, expected_sha, dest=DATA_DIR):
    """Baixa o bundle (s3://bucket/key.tgz), confere o sha256 e extrai. Devolve o diretorio."""
    if not uri.startswith("s3://") or "/" not in uri[5:]:
        raise BundleError("data_bundle_bad_uri", "QC_DATA_BUNDLE_URI precisa ser s3://bucket/chave.tgz")
    if not SHA256_RE.match(expected_sha or ""):
        raise BundleError("data_bundle_bad_sha", "QC_DATA_BUNDLE_SHA256 precisa ter 64 hex minusculos")
    import boto3  # so aqui: sem bundle, o boto3 nem e importado
    bucket, key = uri[5:].split("/", 1)
    os.makedirs(os.path.dirname(dest.rstrip("/")) or "/", exist_ok=True)
    tmp = dest.rstrip("/") + ".tgz"
    boto3.client("s3").download_file(bucket, key, tmp)
    digest = hashlib.sha256()
    with open(tmp, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    got = digest.hexdigest()
    if got != expected_sha:
        os.remove(tmp)
        # Nunca servir dados que nao sao os aprovados: /ready fica 503 com o motivo.
        raise BundleError("data_bundle_sha_mismatch", f"sha256 esperado {expected_sha[:12]}..., obtido {got[:12]}...")
    shutil.rmtree(dest, ignore_errors=True)
    os.makedirs(dest)
    _safe_extract(tmp, dest)
    os.remove(tmp)
    if not os.path.isdir(os.path.join(dest, "corpus")):
        raise BundleError("data_bundle_incomplete", "bundle sem a pasta corpus/")
    return dest


def default_engine_factory(data_root=None):
    from lab.engine import Engine
    kwargs = {"root": ROOT}
    if data_root:
        kwargs["data_root"] = data_root
    return Engine(os.environ.get("QC_NEO4J_URI", "bolt://localhost:7687"),
                  os.environ.get("QC_NEO4J_USER", "neo4j"),
                  os.environ.get("NEO4J_PASSWORD", ""), **kwargs)


class Runtime:
    """Um por processo. Thread-safe: o bootstrap roda numa thread, as requisicoes em outras."""

    def __init__(self, engine_factory=None, bootstrap_timeout_s=180, supervise_interval_s=5.0,
                 max_backoff_s=15.0):
        self.engine_factory = engine_factory or default_engine_factory
        self.bootstrap_timeout_s = bootstrap_timeout_s
        self.supervise_interval_s = supervise_interval_s
        self.max_backoff_s = max_backoff_s
        self.lock = threading.Lock()
        self.engine = None
        self.data_root = None
        self.bundle = {"data_bundle_uri": os.environ.get("QC_DATA_BUNDLE_URI") or None,
                       "data_bundle_sha256": os.environ.get("QC_DATA_BUNDLE_SHA256") or None}
        self.fatal = None          # erro que retry nao resolve (bundle com sha errado)
        self.last_error = None
        self.bootstrap_info = None
        self.bootstrapped = False
        self.attempts = 0
        self.started_monotonic = time.monotonic()
        self.ready_at_s = None
        self._ready_cache = {"ready": False, "checks": {"bootstrap": "pending"}}
        self._stop = threading.Event()
        self._thread = None

    # ------------------------------------------------------------ ciclo de vida
    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="qc-bootstrap", daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()

    def _prepare_data(self):
        uri, sha = self.bundle["data_bundle_uri"], self.bundle["data_bundle_sha256"]
        if not (uri and sha):
            return None
        started = time.monotonic()
        root = fetch_data_bundle(uri, sha)
        log("data_bundle.ready", data_bundle_sha256=sha, duration_ms=round((time.monotonic() - started) * 1000))
        return root

    def _run(self):
        backoff = min(1.0, self.max_backoff_s)
        while not self._stop.is_set() and not self.bootstrapped and not self.fatal:
            self.attempts += 1
            try:
                if self.engine is None:
                    self.data_root = self._prepare_data()
                    engine = self.engine_factory(data_root=self.data_root)
                    with self.lock:
                        self.engine = engine
                started = time.monotonic()
                info = self.engine.bootstrap(timeout_s=self.bootstrap_timeout_s)
                with self.lock:
                    self.bootstrap_info = info
                    self.bootstrapped = True
                    self.last_error = None
                log("bootstrap.done", attempt=self.attempts,
                    duration_ms=round((time.monotonic() - started) * 1000),
                    graph_version=(info or {}).get("graph_version"),
                    counts=(info or {}).get("counts"), index=(info or {}).get("index"))
            except BundleError as err:
                self.fatal = {"reason": err.reason, "detail": err.detail}
                log("bootstrap.fatal", reason=err.reason, detail=err.detail)
            except Exception as err:  # noqa: BLE001 - Neo4j ainda subindo: tenta de novo
                self.last_error = f"{type(err).__name__}: {str(err)[:160]}"
                log("bootstrap.retry", attempt=self.attempts, error=type(err).__name__,
                    next_retry_s=backoff)
                self._stop.wait(backoff)
                backoff = min(backoff * 2, self.max_backoff_s)
        # Supervisor: atualiza a prontidao e refaz o bootstrap se o Neo4j voltar vazio
        # (restart do conteiner neo4j na mesma task apaga o grafo, que e efemero).
        while not self._stop.is_set() and not self.fatal:
            state = self.check_ready()
            checks = state.get("checks", {})
            if checks.get("neo4j") and (not checks.get("graph_loaded") or checks.get("vector_index") == "MISSING"):
                try:
                    log("bootstrap.rebuild", reason="neo4j_sem_grafo")
                    self.bootstrap_info = self.engine.bootstrap(timeout_s=self.bootstrap_timeout_s)
                except Exception as err:  # noqa: BLE001
                    self.last_error = f"{type(err).__name__}: {str(err)[:160]}"
            elif checks.get("neo4j") and checks.get("warmed_up") is False and hasattr(self.engine, "warm_up"):
                # Neo4j voltou com o grafo (restart do processo): so falta aquecer.
                try:
                    log("warm_up.retry", reason="neo4j_reconectou")
                    self.engine.warm_up()
                except Exception as err:  # noqa: BLE001
                    self.last_error = f"{type(err).__name__}: {str(err)[:160]}"
            self._stop.wait(self.supervise_interval_s)

    # ------------------------------------------------------------- prontidao
    def check_ready(self):
        """Consulta o engine (e o Neo4j) agora e atualiza o cache usado pelos endpoints."""
        if self.fatal:
            state = {"ready": False, "reason": self.fatal["reason"],
                     "checks": {"data_bundle": self.fatal["reason"], "detail": self.fatal["detail"]}}
        elif self.engine is None:
            state = {"ready": False, "checks": {"engine": "not_created", "bootstrap": "pending",
                                                "attempts": self.attempts, "last_error": self.last_error}}
        else:
            try:
                state = dict(self.engine.ready())
                state["checks"] = dict(state.get("checks") or {})
            except Exception as err:  # noqa: BLE001
                state = {"ready": False, "checks": {"neo4j": False, "error": type(err).__name__}}
            if not self.bootstrapped:
                state["ready"] = False
                state["checks"].update(bootstrap="running", attempts=self.attempts, last_error=self.last_error)
        if state.get("ready") and self.ready_at_s is None:
            self.ready_at_s = round(time.monotonic() - self.started_monotonic, 1)
            log("ready.first", cold_start_s=self.ready_at_s, attempts=self.attempts)
        self._ready_cache = state
        return state

    def is_ready(self):
        return bool(self._ready_cache.get("ready"))

    def versions(self):
        out = {}
        if self.engine is not None:
            try:
                out = dict(self.engine.versions())
            except Exception as err:  # noqa: BLE001
                log("versions.engine_error", error=type(err).__name__)
        if not out:
            out = static_versions(data_root=self.data_root)
        out.update(env_versions())
        out.update(self.bundle)
        out.setdefault("model_version", "none")
        return out

    def known_products(self):
        """Lista fechada de produtos: o path param so passa se estiver no seed carregado."""
        base = self.data_root or ROOT
        path = os.path.join(base, "graph", "seed", "catalog.json")
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return set()
        return {p.get("id") for p in data.get("products", []) if isinstance(p, dict) and p.get("id")}
