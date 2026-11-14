"""Motor da Central de Evidencias (Aula 5.1): a mesma pergunta em tres estrategias.

- bm25:   o retriever legado do core (em memoria), sem nenhuma mudanca.
- vector: embedding da pergunta (modelo open source, ONNX) + indice vetorial
          nativo do Neo4j (db.index.vector.queryNodes).
- graph:  entity linking deterministico (dicionario do seed) + travessia
          multi-hop parametrizada no Neo4j: Produto -> Categoria/Campanha ->
          Politica -> Versao -> Documento -> Trecho.

Tres decisoes valem para as tres estrategias:
1. UMA regra de autorizacao: core.retrieval.check_access (status, vigencia,
   tenant, access_level), aplicada ANTES do composer. O vetor sobre-amostra
   (raw_top_k) e bloqueia no servidor; o grafo filtra os candidatos da travessia.
2. UM composer: core.composer (extrativo, sem LLM) e prompts/templates.json.
3. Status de pedido vem SO da API de pedidos (core.orders): nunca do grafo,
   nunca de documento.

A estrategia graph le status e vigencia das versoes do PROPRIO grafo em runtime.
E isso que permite simular o incidente stale_graph_policy: um sync defeituoso
muda o metadado no grafo e a resposta muda sem nenhum deploy.
"""
import datetime
import json
import os
import re
import threading
import time
import uuid

from neo4j import GraphDatabase
from neo4j.exceptions import AuthError, Neo4jError, ServiceUnavailable, SessionExpired

from core import ROOT, telemetry, versions as core_versions
from core.assistant import Assistant, load_settings
from core.composer import compose
from core.corpus import load_chunks
from core.retrieval import BM25Index, check_access, rank, tokens
from core.route import is_injection, route_intent
from graph import loader, queries
from vector import cache as vcache, encoder as venc, index as vindex

STRATEGIES = ("bm25", "vector", "graph")
SOURCES = {"bm25": "bm25-inmemory", "vector": "neo4j-vector", "graph": "neo4j-graph"}
USER_ROLES = ("customer", "agent", "admin")
AS_OF_PRESETS = ("2026-11-14", "2027-11-26", "2027-12-06")
SCENARIOS = ("stale_graph_policy", "remove_campaign_edge")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PRODUCT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
MAX_QUESTION_CHARS = 1000

# Parametros proprios das estrategias novas (o BM25 legado segue config/settings.json).
# min_cosine: abaixo disso o trecho mais parecido nao e evidencia (ex.: cashback).
DEFAULTS = {
    "vector": {"raw_top_k": 12, "k": 3, "min_cosine": 0.45},
    "graph": {"min_score": 0.5, "min_matched_terms": 1},
    "graph_version_ttl_s": 5.0,
}

# Cenario do incidente: os ids sao fixos e conhecidos; nada vem do usuario.
STALE_OLD, STALE_NEW = "politica-trocas@v2", "politica-trocas@v3"
EDGE_PRODUCT, EDGE_CAMPAIGN = "fone-bluetooth-qc", "black-friday-2027"


class InvalidInput(ValueError):
    """Entrada fora do contrato (data, tenant, papel, estrategia, produto)."""


class DependencyUnavailable(RuntimeError):
    """Neo4j (ou modelo) indisponivel: a API responde erro controlado, nunca inventa."""


def _ms(start):
    return round((time.perf_counter() - start) * 1000, 2)


def _as_chunk(props):
    """Propriedades de um no :Chunk -> dict no formato de core.corpus."""
    chunk = dict(props)
    chunk.pop("embedding", None)
    chunk["chunk_id"] = chunk.get("chunk_id") or chunk.get("id")
    for key in ("expires_date", "supersedes"):
        chunk.setdefault(key, None)
    return chunk


def static_versions(root=ROOT, data_root=None, settings=None):
    """Versoes que dependem so da imagem/dados (sem Neo4j): build_info e gate de CI."""
    data_root = data_root or root
    a = Assistant(settings, root=root)
    v = dict(a.versions)
    corpus_dir = os.path.join(data_root, "corpus")
    chunks = load_chunks(corpus_dir)
    if os.path.abspath(data_root) != os.path.abspath(root):
        r = a.settings["retrieval"]
        params = {"retrieval": r, "filters": a.settings["filters"], "composer": a.settings["composer"]}
        v["corpus_version"] = core_versions.corpus_version(corpus_dir)
        v["index_version"] = core_versions.index_version(chunks, params)
    v.update({"graph_seed_version": loader.graph_seed_version(data_root),
              "vector_index_version": vcache.vector_index_version(chunks),
              "embedding_model_id": venc.MODEL_ID, "embedding_model_revision": venc.MODEL_REVISION,
              "embedding_dim": venc.DIM, "model_version": core_versions.MODEL_VERSION})
    return v


class Engine:
    def __init__(self, neo4j_uri, neo4j_user, neo4j_password, settings=None, root=ROOT, data_root=None,
                 orders=None, model_dir=None, cache_path=None):
        self.root = root
        self.data_root = data_root or os.environ.get("QC_DATA_ROOT") or root
        self.settings = load_settings(settings, root)
        self.cfg = {**DEFAULTS, **(self.settings.get("lab") or {})}
        self.assistant = Assistant(settings, orders=orders, root=root)
        if os.path.abspath(self.data_root) != os.path.abspath(root):
            self._use_data_root()
        self.templates = self.assistant.templates
        self.chunks = self.assistant.chunks
        self.chunk_pos = {c["chunk_id"]: i for i, c in enumerate(self.chunks)}
        self.seed = loader.load_seed(self.data_root)
        self.expected_graph = loader.build_graph(self.data_root, self.seed)
        self.seed_version = loader.canonical_hash(self.expected_graph["nodes"], self.expected_graph["edges"])
        self.vector_index_version = vcache.vector_index_version(self.chunks)
        self.model_dir = model_dir or venc.default_model_dir()
        self.cache_path = cache_path or vcache.default_cache_path(self.model_dir)
        self.driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_password),
                                           connection_timeout=5.0, max_transaction_retry_time=5.0)
        self.encoder = None
        self.bootstrapped = False
        self.cache_stats = None
        self._graph_version = None
        self._graph_version_at = 0.0
        self._lock = threading.Lock()
        self._dicts = self._build_dictionaries()

    # ------------------------------------------------------------ dados
    def _use_data_root(self):
        """Corpus de outro diretorio (data bundle): BM25 e versoes recalculados sobre ele."""
        a, r = self.assistant, self.settings["retrieval"]
        corpus_dir = os.path.join(self.data_root, "corpus")
        a.chunks = load_chunks(corpus_dir)
        a.index = BM25Index(a.chunks, k1=r["bm25_k1"], b=r["bm25_b"], title_boost=r["title_boost"])
        params = {"retrieval": r, "filters": self.settings["filters"], "composer": self.settings["composer"]}
        a.versions["corpus_version"] = core_versions.corpus_version(corpus_dir)
        a.versions["index_version"] = core_versions.index_version(a.chunks, params)

    def _build_dictionaries(self):
        """Dicionario de entidades: sinonimo (ja em tokens do core) -> id.

        Usa core.retrieval.tokens, o mesmo normalizador do BM25: "fones",
        "Fone" e "fone" viram o mesmo termo, e o aluno consegue conferir.
        """
        def entries(items, key="synonyms", extra=()):
            out = []
            for item in items:
                for syn in list(item.get(key, [])) + [item.get(n) for n in extra if item.get(n)]:
                    toks = tuple(tokens(syn))
                    if toks:
                        out.append((toks, item["id"], syn))
            # Sinonimo mais longo primeiro: "fone de ouvido" ganha de "fone".
            return sorted(out, key=lambda e: -len(e[0]))
        return {
            "product": entries(self.seed["products"]),
            "category": entries(self.seed["categories"]),
            "campaign": entries(self.seed["campaigns"]),
            "topic": [(tuple(tokens(term)), t["id"], term, rank)
                      for rank, t in enumerate(self.seed.get("topics", [])) for term in t["terms"]],
        }

    # ---------------------------------------------------------- Neo4j
    def _session(self):
        return self.driver.session()

    def _neo4j(self, fn):
        try:
            with self._session() as s:
                return fn(s)
        except AuthError as err:
            # Senha errada: diz o motivo sem repetir a senha nem o usuario.
            raise DependencyUnavailable("neo4j_auth_failed: AuthError") from err
        except (ServiceUnavailable, SessionExpired, OSError) as err:
            raise DependencyUnavailable(f"neo4j_unavailable: {type(err).__name__}") from err

    def _wait_neo4j(self, deadline):
        last = None
        while time.monotonic() < deadline:
            try:
                self.driver.verify_connectivity()
                return
            except AuthError as err:
                # Repetir nao resolve senha errada: falha na hora, com motivo claro.
                raise DependencyUnavailable("neo4j_auth_failed: AuthError") from err
            except Exception as err:  # o Neo4j sobe depois da API: tentar ate o prazo
                last = err
                time.sleep(1.0)
        raise DependencyUnavailable(f"neo4j nao respondeu no prazo: {type(last).__name__ if last else 'timeout'}")

    def _load_encoder(self):
        if self.encoder is None:
            self.encoder = venc.Encoder(self.model_dir)
        return self.encoder

    def bootstrap(self, timeout_s=180):
        """Sincroniza grafo + embeddings + indice vetorial. Idempotente (MERGE)."""
        started = time.perf_counter()
        deadline = time.monotonic() + timeout_s
        with self._lock:
            self._wait_neo4j(deadline)
            encoder = self._load_encoder()
            vectors, self.cache_stats = vcache.resolve(self.chunks, encoder, vcache.load(self.cache_path))
            if self.cache_stats["recomputed"]:
                telemetry.log("engine.embedding_cache", hits=self.cache_stats["hits"],
                              recomputed=self.cache_stats["recomputed"], cache_path=self.cache_path)

            def work(s):
                loader.apply_schema(s, self.root)
                loader.sync(s, self.expected_graph)
                vindex.store_embeddings(s, vectors)
                vindex.ensure(s)
                st = vindex.wait_online(s, timeout_s=max(1.0, deadline - time.monotonic()))
                return st, loader.counts(s)
            idx, counts = self._neo4j(work)
            self.bootstrapped = True
            self._graph_version = None
        out = {"graph_version": self.graph_version(force=True), "graph_seed_version": self.seed_version,
               "counts": counts, "index": {"name": idx["name"], "dim": idx["dim"], "state": idx["state"]},
               "embedding_cache": self.cache_stats, "duration_ms": _ms(started)}
        telemetry.log("engine.bootstrap", graph_version=out["graph_version"], graph_seed_version=self.seed_version,
                      index_state=idx["state"], nodes=counts["nodes"], edges=counts["edges"],
                      duration_ms=out["duration_ms"])
        return out

    def graph_version(self, force=False):
        """Hash do grafo EM RUNTIME (lido do banco), com cache curto."""
        now = time.monotonic()
        if force or self._graph_version is None or now - self._graph_version_at > self.cfg["graph_version_ttl_s"]:
            dumped = self._neo4j(loader.dump)
            self._graph_version = loader.canonical_hash(dumped["nodes"], dumped["edges"])
            self._graph_version_at = now
        return self._graph_version

    def ready(self):
        checks = {"neo4j": False, "vector_index": "MISSING", "model_loaded": self.encoder is not None,
                  "graph_loaded": False, "corpus_version": self.assistant.versions["corpus_version"]}
        try:
            def probe(s):
                st = vindex.state(s)
                n = s.run("MATCH (p:PolicyVersion) RETURN count(p) AS n").single()["n"]
                return st, n
            st, n = self._neo4j(probe)
            checks["neo4j"] = True
            checks["vector_index"] = st["state"]
            checks["graph_loaded"] = self.bootstrapped and n > 0
        except (DependencyUnavailable, Neo4jError) as err:
            checks["neo4j_error"] = str(err).split(":")[0]
        ok = (checks["neo4j"] and checks["vector_index"] == "ONLINE" and checks["model_loaded"]
              and checks["graph_loaded"])
        return {"ready": bool(ok), "checks": checks}

    def close(self):
        self.driver.close()

    # -------------------------------------------------------- versoes
    def versions(self):
        v = dict(self.assistant.versions)
        try:
            gv = self.graph_version()
        except DependencyUnavailable:
            gv = "unavailable"
        v.update({
            "vector_index_version": self.vector_index_version,
            "graph_version": gv,
            "graph_seed_version": self.seed_version,
            "embedding_model_id": venc.MODEL_ID,
            "embedding_model_revision": venc.MODEL_REVISION,
            "embedding_dim": venc.DIM,
            "model_version": core_versions.MODEL_VERSION,
            "git_sha": os.environ.get("QC_GIT_SHA") or "unknown",
            "image_digest": os.environ.get("QC_IMAGE_DIGEST") or "unknown",
            "release_id": self.settings.get("release_id"),
            "data_bundle_uri": os.environ.get("QC_DATA_BUNDLE_URI") or None,
            "data_bundle_sha256": os.environ.get("QC_DATA_BUNDLE_SHA256") or None,
        })
        return v

    def _span_attrs(self, strategy=None, scenario_id=None, case_id=None):
        v = self.assistant.versions
        attrs = {"qc.release_id": self.settings.get("release_id"), "qc.environment": self.settings.get("environment"),
                 "qc.graph_version": self._graph_version, "qc.graph_seed_version": self.seed_version,
                 "qc.corpus_version": v["corpus_version"], "qc.vector_index_version": self.vector_index_version,
                 "qc.embedding_model_id": venc.MODEL_ID, "qc.scenario_id": scenario_id, "qc.case_id": case_id}
        if strategy:
            attrs["qc.strategy"] = strategy
        return attrs

    def meta(self):
        def index_state(s):
            return vindex.state(s), loader.counts(s)
        try:
            idx, counts = self._neo4j(index_state)
        except DependencyUnavailable:
            idx, counts = {"name": vindex.INDEX_NAME, "state": "UNAVAILABLE", "dim": None}, None
        return {
            "versions": self.versions(),
            "graph": {"counts": counts, "expected_counts": {"nodes": len(self.expected_graph["nodes"]),
                                                           "edges": len(self.expected_graph["edges"])}},
            "index": {"name": idx["name"], "dim": idx.get("dim"), "state": idx["state"],
                      "similarity": idx.get("similarity")},
            "model": venc.model_info(),
            "as_of_dates": list(AS_OF_PRESETS),
            "as_of_note": "data simulada da pergunta (nao e a data do sistema)",
            "tenants": [t["id"] for t in self.seed["tenants"]],
            "user_roles": list(USER_ROLES),
            "strategies": list(STRATEGIES),
            "products": [{"id": p["id"], "name": p["name"], "category": p["category"], "tenant": p["tenant"]}
                         for p in self.seed["products"]],
            "categories": [{"id": c["id"], "name": c["name"]} for c in self.seed["categories"]],
            "campaigns": [{"id": c["id"], "name": c["name"]} for c in self.seed["campaigns"]],
            "topics": [t["id"] for t in self.seed.get("topics", [])],
            "query_ids": [queries.POLICY_FOR_PRODUCT["id"], queries.POLICY_FOR_CATEGORY["id"],
                          queries.CHUNKS_FOR_VERSION["id"], vindex.QUERY_ID],
            "vector": {"raw_top_k": self.cfg["vector"]["raw_top_k"], "k": self.cfg["vector"]["k"],
                       "min_cosine": self.cfg["vector"]["min_cosine"]},
        }

    # ------------------------------------------------------- validacao
    def _validate(self, question=None, as_of_date=None, tenant=None, user_role=None, strategies=None):
        if question is not None:
            if not isinstance(question, str) or not question.strip():
                raise InvalidInput("question vazia")
            if len(question) > MAX_QUESTION_CHARS:
                raise InvalidInput(f"question acima de {MAX_QUESTION_CHARS} caracteres")
        if as_of_date is not None:
            if not isinstance(as_of_date, str) or not DATE_RE.match(as_of_date):
                raise InvalidInput("as_of_date deve ser AAAA-MM-DD")
            try:
                datetime.date.fromisoformat(as_of_date)
            except ValueError as err:
                raise InvalidInput("as_of_date invalida") from err
        if tenant is not None and tenant not in {t["id"] for t in self.seed["tenants"]}:
            raise InvalidInput("tenant desconhecido")
        if user_role is not None and user_role not in USER_ROLES:
            raise InvalidInput("user_role invalido")
        if strategies is not None:
            bad = [s for s in strategies if s not in STRATEGIES]
            if bad or not strategies:
                raise InvalidInput(f"estrategias validas: {list(STRATEGIES)}")

    # ------------------------------------------------------ estrategias
    def _blocked_summary(self, items):
        """[(doc_id, version, reason)] -> lista sem duplicata e SEM texto."""
        seen, out = set(), []
        for doc_id, version, reason in items:
            key = (doc_id, str(version), reason)
            if key not in seen:
                seen.add(key)
                out.append({"doc_id": doc_id, "version": str(version), "reason": reason})
        return out

    def _compose(self, question, results, overrides=None):
        settings = self.settings
        if overrides:
            settings = {**self.settings, "retrieval": {**self.settings["retrieval"], **overrides}}
        with telemetry.span("compose.deterministic") as sp:
            out = compose(question, results, self.assistant.index.idf, self.templates, settings)
            sp.set("abstain_reason", out["abstain_reason"] or "none")
            sp.set("injection_dropped", out.get("injection_dropped", 0))
        return out

    def _bm25(self, question, as_of, tenant, role, attrs):
        lat = {"embed": None, "db": None}
        t0 = time.perf_counter()
        idx, filters, k = self.assistant.index, self.settings["filters"], int(self.settings["retrieval"]["k"])
        with telemetry.span("policy.version.filter", **attrs) as sp:
            tf = time.perf_counter()
            reasons = [check_access(c, as_of, tenant, role, filters) for c in self.chunks]
            sp.set("chunks_allowed", sum(1 for r in reasons if r is None))
            lat["filter"] = _ms(tf)
        # Mesmo ranking do legado (core.retrieval.rank sobre os permitidos).
        allowed = [i for i, r in enumerate(reasons) if r is None]
        results = rank(idx, question, allowed, k=k, corpus_version=self.assistant.versions["corpus_version"])
        # Bloqueados que teriam pontuado: mostram o que o filtro impediu, sem o texto.
        terms = tokens(question)
        blocked = self._blocked_summary(
            (c["doc_id"], c["version"], reasons[i]) for i, c in sorted(
                enumerate(self.chunks), key=lambda x: (-idx.score(terms, x[0]), x[1]["chunk_id"]))
            if reasons[i] and idx.score(terms, i) > 0)
        tc = time.perf_counter()
        out = self._compose(question, results)
        lat["compose"] = _ms(tc)
        lat["total"] = _ms(t0)
        return out, {
            "results": [{"doc_id": r["doc_id"], "chunk_id": r["chunk_id"], "version": r["chunk"]["version"],
                         "status": r["chunk"]["status"], "score": r["score"], "score_type": "bm25",
                         "reason": "BM25 sobre trechos permitidos pelo filtro"} for r in results],
            "blocked": blocked, "path": None, "cypher_query_id": None, "index_name": "bm25-inmemory",
            "raw_top_k": None, "latency_ms": lat,
        }

    def _vector(self, question, as_of, tenant, role, attrs):
        vc = self.cfg["vector"]
        lat = {}
        t0 = time.perf_counter()
        with telemetry.span("vector.embed", **attrs) as sp:
            te = time.perf_counter()
            embedding = self._load_encoder().encode([question])[0]
            lat["embed"] = _ms(te)
            sp.set("dim", len(embedding))
        with telemetry.span("vector.query", **attrs, index=vindex.INDEX_NAME, raw_top_k=vc["raw_top_k"]) as sp:
            td = time.perf_counter()
            rows = self._neo4j(lambda s: vindex.query(s, embedding, vc["raw_top_k"]))
            lat["db"] = _ms(td)
            sp.set("rows", len(rows))
        with telemetry.span("policy.version.filter", **attrs) as sp:
            tf = time.perf_counter()
            allowed, blocked_raw = [], []
            for row in rows:
                chunk = _as_chunk(row["chunk"])
                # Mesma regra do BM25, no servidor, ANTES do composer: o indice ANN
                # nao filtra por metadado, entao sobre-amostramos e bloqueamos aqui.
                reason = check_access(chunk, as_of, tenant, role, self.settings["filters"])
                cosine = round(2 * row["score"] - 1, 4)
                if reason:
                    blocked_raw.append((chunk["doc_id"], chunk["version"], reason))
                else:
                    allowed.append((cosine, row["score"], chunk))
            sp.set("chunks_allowed", len(allowed))
            sp.set("chunks_blocked", len(blocked_raw))
            lat["filter"] = _ms(tf)
        top = allowed[: vc["k"]]
        results = [{"doc_id": c["doc_id"], "chunk_id": c["chunk_id"], "score": cos, "chunk": c} for cos, _, c in top]
        tc = time.perf_counter()
        # So o limiar de score muda (cosine em vez de BM25); o minimo de termos
        # em comum com a pergunta e o MESMO do legado (config/settings.json).
        out = self._compose(question, results, {"min_score": vc["min_cosine"]})
        lat["compose"] = _ms(tc)
        lat["total"] = _ms(t0)
        return out, {
            "results": [{"doc_id": c["doc_id"], "chunk_id": c["chunk_id"], "version": c["version"],
                         "status": c["status"], "score": cos, "neo4j_score": round(raw, 4), "score_type": "cosine",
                         "reason": "vizinho mais proximo no indice vetorial, permitido pelo filtro"}
                        for cos, raw, c in top],
            "blocked": self._blocked_summary(blocked_raw), "path": None, "cypher_query_id": vindex.QUERY_ID,
            "index_name": vindex.INDEX_NAME, "raw_top_k": vc["raw_top_k"],
            "recall_note": (f"{len(allowed)} de {len(rows)} vizinhos sobreviveram ao filtro"
                            if len(allowed) < vc["k"] else None),
            "latency_ms": lat,
        }

    # ------------------------------------------------------------ grafo
    def link_entities(self, question):
        """Entity linking deterministico: sequencia de tokens do sinonimo na pergunta."""
        q = tokens(question)

        def find(entries):
            found = []
            for toks, eid, syn in entries:
                n = len(toks)
                if any(tuple(q[i:i + n]) == toks for i in range(len(q) - n + 1)) and eid not in [f[0] for f in found]:
                    found.append((eid, syn))
            return found
        topics = []
        for toks, tid, term, rank in self._dicts["topic"]:
            n = len(toks)
            if any(tuple(q[i:i + n]) == toks for i in range(len(q) - n + 1)):
                topics.append((rank, tid, term))
        topics.sort()
        return {
            "products": find(self._dicts["product"]),
            "categories": find(self._dicts["category"]),
            "campaigns": find(self._dicts["campaign"]),
            # Ordem do seed = prioridade: "arrependimento" vem antes de "troca"
            # para "me arrependi, posso trocar?" nao virar politica de trocas.
            "topic": topics[0][1] if topics else None,
            "topics_found": sorted({t[1] for t in topics}),
        }

    def _graph_candidates(self, s, product_id, category_id, topic, tenant):
        if product_id:
            q = queries.POLICY_FOR_PRODUCT
            rows = queries.run(s, q, product_id=product_id, tenant=tenant, topic=topic)
        else:
            q = queries.POLICY_FOR_CATEGORY
            rows = queries.run(s, q, category_id=category_id, topic=topic)
        return q["id"], rows

    def _graph_decide(self, rows, as_of, tenant, role):
        """Aplica check_access aos metadados da versao LIDOS DO GRAFO + precedencia."""
        allowed, blocked = [], []
        for row in rows:
            pv = dict(row["version"])
            pv.setdefault("expires_date", None)
            reason = check_access(pv, as_of, tenant, role, self.settings["filters"])
            if not reason and any(n.get("status") == "active" for n in row["superseded_by"]):
                # Versao substituida por outra ativa nunca vence, mesmo se o status dela mentir.
                reason = "superseded"
            cmp = row.get("campaign")
            if not reason and cmp and not (cmp.get("start_date", "") <= as_of <= (cmp.get("end_date") or "9999-12-31")):
                reason = "campaign_not_active"
            (blocked if reason else allowed).append((row, reason))
        # Precedencia: regra de campanha (vigente e com o produto participando)
        # vence a regra geral da categoria; empate fica com a versao mais nova.
        allowed.sort(key=lambda x: (0 if x[0]["via"] != "category" else 1, -int(x[0]["version"]["version"])))
        return allowed, blocked

    def _graph(self, question, as_of, tenant, role, attrs, product_override=None, topic_override=None):
        lat = {"embed": None}
        t0 = time.perf_counter()
        with telemetry.span("entity.link", **attrs) as sp:
            ents = self.link_entities(question)
            products = [product_override] if product_override else [p[0] for p in ents["products"]]
            topic = topic_override or ents["topic"]
            sp.set("products", len(products))
            sp.set("topic", topic or "none")
        base = {"results": [], "blocked": [], "path": {"nodes": [], "edges": []}, "cypher_query_id": None,
                "index_name": None, "raw_top_k": None, "entities": ents, "latency_ms": lat}
        fallback = None
        if len(products) > 1:
            fallback = ("entity_ambiguous", f"mais de um produto na pergunta: {products}; o grafo nao escolhe por voce")
        elif not products and not ents["categories"]:
            fallback = ("entity_not_found", "nenhum produto ou categoria do catalogo foi reconhecido; "
                                            "sem entidade a travessia nao comeca (sem caminho forcado)")
        elif not topic:
            fallback = ("topic_not_found", "a pergunta nao diz qual regra (troca, arrependimento, garantia...)")
        if fallback:
            lat.update({"db": 0.0, "filter": 0.0, "compose": 0.0, "total": _ms(t0)})
            return ({"answer": self.templates["insufficient_evidence"], "citations": [], "abstained": True,
                     "abstain_reason": fallback[0], "evidence_category": None},
                    {**base, "fallback": {"reason": fallback[0], "detail": fallback[1]}})
        product_id = products[0] if products else None
        category_id = None if product_id else ents["categories"][0][0]
        with telemetry.span("graph.traverse", **attrs) as sp:
            td = time.perf_counter()

            def work(s):
                qid, rows = self._graph_candidates(s, product_id, category_id, topic, tenant)
                return qid, rows
            qid, rows = self._neo4j(work)
            lat["db"] = _ms(td)
            sp.set("cypher_query_id", qid)
            sp.set("candidates", len(rows))
        with telemetry.span("policy.version.filter", **attrs) as sp:
            tf = time.perf_counter()
            allowed, blocked = self._graph_decide(rows, as_of, tenant, role)
            sp.set("candidates_allowed", len(allowed))
            sp.set("candidates_blocked", len(blocked))
            if allowed:
                # +1: a aresta CONTAINS ate o trecho citado.
                sp.set("qc.path_hops", int(allowed[0][0]["hops"]) + 1)
                sp.set("qc.selected_doc_id", allowed[0][0]["version"]["doc_id"])
                sp.set("qc.selected_doc_version", str(allowed[0][0]["version"]["version"]))
            lat["filter"] = _ms(tf)
        base.update({"cypher_query_id": qid,
                     "blocked": self._blocked_summary((r["version"]["doc_id"], r["version"]["version"], why)
                                                      for r, why in blocked)})
        nodes, edges = {}, {}

        def add(row, role_name):
            for n in row["nodes"]:
                cur = nodes.get(n["id"])
                if cur is None or role_name == "selected":
                    nodes[n["id"]] = {"id": n["id"], "label": n["label"],
                                      "props": {k: v for k, v in n["props"].items() if v is not None},
                                      "role": role_name}
            for e in row["edges"] + [x for x in row.get("supersedes_edges", []) if x]:
                key = (e["source"], e["type"], e["target"])
                if key not in edges or role_name == "selected":
                    edges[key] = {"source": e["source"], "target": e["target"], "type": e["type"],
                                  "props": e["props"], "role": role_name}
                if e["type"] == "SUPERSEDES" and e.get("newer") and e["source"] not in nodes:
                    nodes[e["source"]] = {"id": e["source"], "label": "PolicyVersion",
                                          "props": e["newer"], "role": "context"}
        for row, _ in blocked:
            add(row, "blocked")
        if not allowed:
            if product_id and not rows:
                why = ("entity_not_in_tenant", f"produto {product_id} nao pertence ao tenant {tenant} "
                                              "ou nao ha politica do topico ligada a ele")
            else:
                why = ("no_authorized_policy", "todas as versoes encontradas foram bloqueadas pelo filtro")
            base["path"] = {"nodes": list(nodes.values()), "edges": list(edges.values())}
            lat.update({"compose": 0.0, "total": _ms(t0)})
            return ({"answer": self.templates["insufficient_evidence"], "citations": [], "abstained": True,
                     "abstain_reason": why[0], "evidence_category": None},
                    {**base, "fallback": {"reason": why[0], "detail": why[1]}})
        win = allowed[0][0]
        pv = win["version"]
        chunk_rows = self._neo4j(lambda s: queries.run(s, queries.CHUNKS_FOR_VERSION, version_id=pv["id"]))
        terms = tokens(question)
        results = []
        for cr in chunk_rows:
            chunk = _as_chunk(cr["chunk"])
            # O grafo decide QUAL versao vale; o metadado da versao (do grafo) vai
            # junto com o texto para o composer citar a versao que o grafo escolheu.
            for key in ("status", "effective_date", "expires_date", "tenant", "access_level", "version", "title"):
                chunk[key] = pv.get(key)
            pos = self.chunk_pos.get(chunk["chunk_id"])
            score = self.assistant.index.score(terms, pos) if pos is not None else 0.0
            results.append({"doc_id": chunk["doc_id"], "chunk_id": chunk["chunk_id"], "score": round(score, 4),
                            "chunk": chunk, "edge": cr["edge"]})
        # A secao marcada como regra do topico vem primeiro; o resto segue o
        # score lexico (mesmo BM25 do legado) so para desempatar.
        results.sort(key=lambda r: (topic not in (r["chunk"].get("rule_topics") or []), -r["score"], r["chunk_id"]))
        tc = time.perf_counter()
        if results and topic in (results[0]["chunk"].get("rule_topics") or []):
            # A travessia ja provou que a regra se aplica (produto -> categoria/
            # campanha -> politica -> versao vigente) e o seed diz qual secao
            # escreve a regra do topico: ela e citada mesmo que a pergunta use
            # outras palavras. O score lexico so decide uma 2a fonte opcional.
            thresholds = {"min_score": 0.0, "min_matched_terms": 0}
        else:
            thresholds = {"min_score": self.cfg["graph"]["min_score"],
                          "min_matched_terms": self.cfg["graph"]["min_matched_terms"]}
        out = self._compose(question, results, thresholds)
        lat["compose"] = _ms(tc)
        add(win, "selected")
        cited = {c["chunk_id"] for c in out["citations"]}
        for r in results:
            if r["chunk_id"] in cited:
                c = r["chunk"]
                nodes[r["chunk_id"]] = {"id": r["chunk_id"], "label": "Chunk", "role": "selected",
                                        "props": {"id": r["chunk_id"], "section": c["section"],
                                                  "doc_id": c["doc_id"], "version": c["version"]}}
                e = r["edge"]
                edges[(e["source"], e["type"], e["target"])] = {**e, "role": "selected"}
        hops = len([e for e in edges.values() if e["role"] == "selected"])
        base["path"] = {"nodes": list(nodes.values()), "edges": list(edges.values())}
        base["path_hops"] = hops
        base["selected_policy_version"] = pv["id"]
        base["selected_via"] = win["via"]
        base["results"] = [{"doc_id": r["doc_id"], "chunk_id": r["chunk_id"], "version": str(pv["version"]),
                            "status": pv.get("status"), "score": hops, "score_type": "graph-hops",
                            "lexical_score": r["score"],
                            "reason": (f"via {win['via']}: {pv['id']} ({pv.get('status')}) vence a precedencia"
                                       + ("; campanha vigente na data simulada e produto participante: "
                                          "assume compra feita durante a campanha" if win["via"] != "category" else ""))}
                           for r in results if r["chunk_id"] in cited] or [
            {"doc_id": pv["doc_id"], "chunk_id": None, "version": str(pv["version"]), "status": pv.get("status"),
             "score": hops, "score_type": "graph-hops", "reason": "versao escolhida, mas nenhum trecho responde"}]
        lat["total"] = _ms(t0)
        return out, base

    # ------------------------------------------------------------- API
    def _strategy_entry(self, name, out, detail, attrs):
        entry = {"strategy": name, "source": SOURCES[name], "answer": out["answer"],
                 "abstained": out["abstained"], "abstain_reason": out["abstain_reason"],
                 "citations": [{"doc_id": c["doc_id"], "chunk_id": c["chunk_id"], "version": str(c["version"]),
                                "section": c.get("section")} for c in out["citations"]],
                 "evidence_category": out.get("evidence_category")}
        entry.update(detail)
        return entry

    def _run_strategy(self, name, question, as_of, tenant, role, attrs):
        fn = {"bm25": self._bm25, "vector": self._vector, "graph": self._graph}[name]
        try:
            out, detail = fn(question, as_of, tenant, role, attrs)
        except (DependencyUnavailable, Neo4jError) as err:
            # Erro controlado: a estrategia nao responde; nunca inventa resposta.
            kind = "neo4j_unavailable" if isinstance(err, DependencyUnavailable) else "neo4j_error"
            telemetry.log("engine.strategy_error", strategy=name, kind=kind, error=type(err).__name__)
            return {"strategy": name, "source": SOURCES[name], "answer": None, "abstained": True,
                    "abstain_reason": "dependency_unavailable", "citations": [], "results": [], "blocked": [],
                    "path": None, "cypher_query_id": None, "index_name": None, "raw_top_k": None,
                    "error": {"kind": kind, "detail": type(err).__name__}, "latency_ms": {}}
        return self._strategy_entry(name, out, detail, attrs)

    def compare(self, question, as_of_date, tenant="quantum", user_role="customer",
                strategies=STRATEGIES, order_id=None, auth_token=None, request_id=None,
                scenario_id=None, case_id=None):
        strategies = tuple(strategies or STRATEGIES)
        self._validate(question, as_of_date, tenant, user_role, strategies)
        request_id = request_id or uuid.uuid4().hex
        try:
            self.graph_version()
        except DependencyUnavailable:
            pass
        attrs = self._span_attrs(scenario_id=scenario_id, case_id=case_id)
        # O span intent.route e do adapter web (evita span duplicado); aqui so a regra.
        intent, found_order = route_intent(question, order_id)
        trace_id = telemetry.current_trace_id()
        out = {"question_id": request_id, "request_id": request_id, "as_of_date": as_of_date,
               "tenant": tenant, "user_role": user_role, "strategies": {}, "order": None}
        if intent == "order_status":
            out["route"] = "order_status"
            t0 = time.perf_counter()
            with telemetry.span("orders.lookup", **attrs) as sp:
                res = self.assistant._order(found_order, auth_token, request_id, tenant, user_role, as_of_date)
                sp.set("outcome", res["abstain_reason"] or "ok")
            total = _ms(t0)
            out["order"] = res.get("order")
            for name in strategies:
                out["strategies"][name] = {
                    "strategy": name, "source": "orders-api", "answer": res["answer"], "abstained": res["abstained"],
                    "abstain_reason": res["abstain_reason"],
                    "citations": [{"doc_id": c["doc_id"], "chunk_id": c["chunk_id"], "version": str(c["version"]),
                                   "section": c.get("section")} for c in res["citations"]],
                    "results": [], "blocked": [], "path": None, "cypher_query_id": None, "index_name": None,
                    "raw_top_k": None, "latency_ms": {"embed": None, "db": None, "filter": None, "compose": None,
                                                      "total": total},
                    "note": "rota order_status: o status vem SO da API de pedidos; nenhuma estrategia de busca decide o status"}
        elif intent == "unsupported":
            out["route"] = "unsupported"
            reason = "injection_blocked" if is_injection(question) else "out_of_scope"
            for name in strategies:
                out["strategies"][name] = {
                    "strategy": name, "source": SOURCES[name], "answer": self.templates[reason], "abstained": True,
                    "abstain_reason": reason, "citations": [], "results": [], "blocked": [], "path": None,
                    "cypher_query_id": None, "index_name": None, "raw_top_k": None,
                    "latency_ms": {"embed": None, "db": None, "filter": None, "compose": None, "total": 0.0},
                    "note": "rota unsupported: recusa antes de qualquer busca"}
        else:
            out["route"] = "knowledge"
            for name in strategies:
                entry = self._run_strategy(name, question, as_of_date, tenant, user_role,
                                           {**attrs, "qc.strategy": name})
                entry["route"] = "faq" if entry.pop("evidence_category", None) == "faq" else "policy"
                out["strategies"][name] = entry
        for entry in out["strategies"].values():
            entry.pop("evidence_category", None)
        out["versions"] = self.versions()
        out["trace_id"] = trace_id or telemetry.current_trace_id()
        telemetry.log("lab.compare", route=out["route"], request_id=request_id, scenario_id=scenario_id,
                      case_id=case_id, strategies=",".join(strategies),
                      abstained=",".join(f"{k}:{v['abstained']}" for k, v in out["strategies"].items()),
                      graph_version=out["versions"]["graph_version"])
        return out

    def ask(self, question, strategy="graph", customer_id=None, auth_token=None, order_id=None,
            tenant="quantum", user_role="customer", scenario_id=None, request_id=None, as_of_date=None,
            case_id=None):
        """Contrato legado de Assistant.answer + campo strategy."""
        if strategy not in STRATEGIES:
            raise InvalidInput(f"strategy valida: {list(STRATEGIES)}")
        as_of = self.assistant.as_of_date(as_of_date)
        self._validate(question, as_of, tenant, user_role)
        if strategy == "bm25":
            resp = self.assistant.answer(question, customer_id=customer_id, auth_token=auth_token,
                                         order_id=order_id, tenant=tenant, user_role=user_role,
                                         scenario_id=scenario_id, request_id=request_id, as_of_date=as_of)
            resp["versions"] = self.versions()
            resp["strategy"] = "bm25"
            return resp
        cmp = self.compare(question, as_of, tenant=tenant, user_role=user_role, strategies=(strategy,),
                           order_id=order_id, auth_token=auth_token, request_id=request_id,
                           scenario_id=scenario_id, case_id=case_id)
        s = cmp["strategies"][strategy]
        if s.get("error"):
            raise DependencyUnavailable(s["error"]["kind"])
        route = cmp["route"] if cmp["route"] != "knowledge" else s.get("route", "policy")
        resp = {
            "answer": s["answer"], "mode": "deterministic", "route": route, "citations": s["citations"],
            "abstained": s["abstained"], "abstain_reason": s["abstain_reason"], "order": cmp["order"],
            "retrieval": [{"doc_id": r["doc_id"], "chunk_id": r["chunk_id"], "version": r["version"],
                           "score": r["score"], "score_type": r["score_type"],
                           "corpus_version": self.assistant.versions["corpus_version"]} for r in s["results"]],
            "versions": cmp["versions"], "request_id": cmp["request_id"], "trace_id": cmp["trace_id"],
            "release_id": self.settings.get("release_id"), "environment": self.settings.get("environment"),
            "as_of_date": as_of, "strategy": strategy,
        }
        if scenario_id:
            resp["scenario_id"] = scenario_id
        return resp

    def graph_for_product(self, product_id, as_of_date, tenant="quantum", topic="troca", user_role="customer"):
        if not isinstance(product_id, str) or not PRODUCT_RE.match(product_id) or \
                product_id not in {p["id"] for p in self.seed["products"]}:
            raise InvalidInput("product_id desconhecido")
        if topic not in {t["id"] for t in self.seed.get("topics", [])}:
            raise InvalidInput("topic desconhecido")
        self._validate(None, as_of_date, tenant, user_role)
        try:
            self.graph_version()
        except DependencyUnavailable:
            pass
        # Sem pergunta: os termos do proprio topico fazem o papel da pergunta,
        # so para o composer escolher o trecho da regra.
        terms = next(t["terms"] for t in self.seed["topics"] if t["id"] == topic)
        _, detail = self._graph(" ".join(terms), as_of_date, tenant, user_role, self._span_attrs("graph"),
                                product_override=product_id, topic_override=topic)
        return {"product_id": product_id, "as_of_date": as_of_date, "tenant": tenant, "topic": topic,
                "nodes": detail["path"]["nodes"], "edges": detail["path"]["edges"],
                "policy_version": detail.get("selected_policy_version"),
                "reason": (detail.get("fallback") or {}).get("detail") or
                          (f"via {detail.get('selected_via')}" if detail.get("selected_policy_version") else None),
                "blocked": detail["blocked"], "cypher_query_id": detail["cypher_query_id"],
                "graph_version": self._graph_version}

    # ----------------------------------------------------------- admin
    def _diff(self, dumped):
        exp_nodes = {(n["label"], n["id"]): n["props"] for n in self.expected_graph["nodes"]}
        got_nodes = {(n["label"], n["id"]): n["props"] for n in dumped["nodes"]}
        exp_edges = {(e["type"], e["source"], e["target"]): e["props"] for e in self.expected_graph["edges"]}
        got_edges = {(e["type"], e["source"], e["target"]): e["props"] for e in dumped["edges"]}
        changed = []
        for key in sorted(set(exp_nodes) & set(got_nodes)):
            a, b = exp_nodes[key], got_nodes[key]
            for prop in sorted(set(a) | set(b)):
                if a.get(prop) != b.get(prop):
                    changed.append({"node": f"{key[0]}:{key[1]}", "prop": prop, "expected": a.get(prop),
                                    "runtime": b.get(prop)})
        fmt = lambda k: f"({k[1]})-[:{k[0]}]->({k[2]})"  # noqa: E731
        return {
            "node_props_changed": changed,
            "nodes_missing": [f"{k[0]}:{k[1]}" for k in sorted(set(exp_nodes) - set(got_nodes))],
            "nodes_extra": [f"{k[0]}:{k[1]}" for k in sorted(set(got_nodes) - set(exp_nodes))],
            "edges_missing": [fmt(k) for k in sorted(set(exp_edges) - set(got_edges))],
            "edges_extra": [{"edge": fmt(k), "provenance": got_edges[k].get("provenance")}
                            for k in sorted(set(got_edges) - set(exp_edges))],
        }

    def admin_state(self):
        dumped = self._neo4j(loader.dump)
        gv = loader.canonical_hash(dumped["nodes"], dumped["edges"])
        self._graph_version, self._graph_version_at = gv, time.monotonic()
        diff = self._diff(dumped)
        active = []
        props = {(c["node"], c["prop"]) for c in diff["node_props_changed"]}
        if (f"PolicyVersion:{STALE_OLD}", "status") in props:
            active.append("stale_graph_policy")
        if f"({EDGE_PRODUCT})-[:PARTICIPATES_IN]->({EDGE_CAMPAIGN})" in diff["edges_missing"]:
            active.append("remove_campaign_edge")
        return {"graph_version": gv, "graph_seed_version": self.seed_version, "in_sync": gv == self.seed_version,
                "active_mutations": active, "diff": diff, "counts": loader.counts_from(dumped)}

    def apply_scenario(self, name, action):
        if name not in SCENARIOS or action not in ("apply", "restore"):
            raise InvalidInput(f"scenario em {list(SCENARIOS)} e action em ['apply', 'restore']")
        before = self.admin_state()["graph_version"]

        def stale_apply(s):
            # Simula um sync de catalogo defeituoso: a v2 volta a "active" e a
            # relacao SUPERSEDES chega invertida. O corpus nao muda; so o grafo.
            s.run("MATCH (v2:PolicyVersion {id: $old}) SET v2.status = 'active', "
                  "v2.status_source = 'runtime:sync-catalogo (cenario stale_graph_policy)'", old=STALE_OLD).consume()
            s.run("MATCH (:PolicyVersion {id: $new})-[r:SUPERSEDES]->(:PolicyVersion {id: $old}) DELETE r",
                  new=STALE_NEW, old=STALE_OLD).consume()
            s.run("MATCH (v2:PolicyVersion {id: $old}), (v3:PolicyVersion {id: $new}) "
                  "MERGE (v2)-[r:SUPERSEDES]->(v3) SET r.provenance = 'runtime:sync-catalogo (cenario stale_graph_policy)'",
                  old=STALE_OLD, new=STALE_NEW).consume()

        def edge_apply(s):
            s.run("MATCH (:Product {id: $p})-[r:PARTICIPATES_IN]->(:Campaign {id: $c}) DELETE r",
                  p=EDGE_PRODUCT, c=EDGE_CAMPAIGN).consume()

        def edge_restore(s):
            props = next(e["props"] for e in self.expected_graph["edges"]
                         if e["type"] == "PARTICIPATES_IN" and e["source"] == EDGE_PRODUCT and e["target"] == EDGE_CAMPAIGN)
            s.run("MATCH (p:Product {id: $p}), (c:Campaign {id: $c}) MERGE (p)-[r:PARTICIPATES_IN]->(c) SET r = $props",
                  p=EDGE_PRODUCT, c=EDGE_CAMPAIGN, props=props).consume()

        def resync(s):
            # Restaurar = re-sincronizar da FONTE (seed + front matter da imagem),
            # nao "desfazer" a mutacao na mao: desfaz qualquer outra deriva junto.
            loader.sync(s, self.expected_graph)

        fn = {("stale_graph_policy", "apply"): stale_apply, ("stale_graph_policy", "restore"): resync,
              ("remove_campaign_edge", "apply"): edge_apply, ("remove_campaign_edge", "restore"): edge_restore}[(name, action)]
        with self._lock:
            self._neo4j(fn)
        state = self.admin_state()
        telemetry.log("admin.scenario", scenario=name, action=action, graph_version_before=before,
                      graph_version_after=state["graph_version"], graph_seed_version=self.seed_version,
                      active_mutations=",".join(state["active_mutations"]) or "none")
        return {"scenario": name, "action": action, "graph_version_before": before, **state}

    def reindex(self):
        """Reconstroi grafo, embeddings e indice do zero (idempotente)."""
        def wipe(s):
            s.run(f"DROP INDEX {vindex.INDEX_NAME} IF EXISTS").consume()
            s.run("MATCH (n) WHERE any(l IN labels(n) WHERE l IN $labels) DETACH DELETE n",
                  labels=list(loader.LABELS)).consume()
        with self._lock:
            self._neo4j(wipe)
            self.bootstrapped = False
        out = self.bootstrap()
        telemetry.log("admin.reindex", graph_version=out["graph_version"], index_state=out["index"]["state"])
        return out
