"""Runtime HTTP local da Demo 5.2 (porta 8080): o MESMO core.Assistant da Lambda.

Por que um servidor local: a Lambda do Learner Lab nao exporta OpenTelemetry
(X-Ray bloqueado, ver ADR-003). Aqui o assistente roda no Codespaces/Mac com o
mesmo codigo, o mesmo corpus e as mesmas versoes, e o trace e real.

Endpoints:
  POST /assistant/ask   {"question", "auth_token"?, "order_id"?, "request_id"?, "scenario_id"?}
                        (tenant/user_role/as_of_date do corpo sao ignorados: ver ASK_FIELDS)
  GET  /health          status + versoes (application, prompt, corpus, index, eval_set, model)
  POST /admin/reload    rele corpus e config (reindexa) sem reiniciar o processo

/admin/reload existe porque o incidente da 5.2 e de CONTEUDO: reiniciar o
processo com o mesmo corpus nao resolve nada; o que resolve e reindexar com o
corpus correto. O corpus ativo vem de ops/.runtime/overlay.json quando o
inject.py criou um overlay; sem esse arquivo, vale o corpus do repositorio.
So escuta em 127.0.0.1.
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core import telemetry, versions  # noqa: E402
from core.assistant import Assistant  # noqa: E402

SERVICE_NAME = "quantum-assistant"
RUNTIME_DIR = os.environ.get("QC_RUNTIME_DIR", os.path.join(ROOT, "ops", ".runtime"))
# Cenarios conhecidos viram label; qualquer outro valor vira "other" para que
# um cliente nao crie series novas no Prometheus mandando texto livre.
KNOWN_SCENARIOS = {"baseline", "stale_policy", "order_api_degradation", "document_injection", "eval"}
ORDER_OUTCOME = {None: "ok", "order_api_unavailable": "unavailable", "order_not_found": "not_found",
                 "forbidden": "forbidden", "insufficient_evidence": "missing_order_id"}
# Campos aceitos do CORPO. tenant, user_role e as_of_date NAO estao aqui: no
# canal publico quem decide a identidade e a data e o servidor (mesmo padrao do
# adapters/lambda_handler.py). Se o corpo pudesse mandar user_role=agent, qualquer
# cliente leria o manual interno; com as_of_date, "viajaria no tempo" para ler
# uma politica que ainda nao vigora. Os probes da 5.2 so usam a identidade
# publica, entao nao existe canal de teste para trocar identidade.
ASK_FIELDS = ("customer_id", "auth_token", "order_id", "scenario_id", "request_id")
PUBLIC_IDENTITY = {"tenant": "quantum", "user_role": "customer"}


def default_release_id():
    """release_id = commit do repositorio. Nao muda quando so o corpus muda:
    e isso que permite ver, no incidente, 'mesmo release, outro corpus'."""
    try:
        sha = subprocess.run(["git", "-C", ROOT, "rev-parse", "--short", "HEAD"], capture_output=True,
                             text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        sha = ""
    return f"local-{sha}" if sha else "local-dev"


def read_overlay():
    path = os.path.join(RUNTIME_DIR, "overlay.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


class Runtime:
    """Guarda o Assistant atual e troca por um novo no reload (sem derrubar o servidor)."""

    def __init__(self, orders_url, release_id, environment):
        self.orders_url = orders_url
        self.release_id = release_id
        self.environment = environment
        self.lock = threading.Lock()
        self.started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.assistant, self.overlay = self._build()

    def _build(self):
        overlay = read_overlay()
        root = overlay["root"] if overlay else ROOT
        settings = {"orders": {"backend": "http", "base_url": self.orders_url},
                    "release_id": self.release_id, "environment": self.environment}
        assistant = Assistant(settings, root=root)
        telemetry.set_default_labels(corpus_version=assistant.versions["corpus_version"])
        return assistant, overlay

    def reload(self):
        with self.lock:
            before = dict(self.assistant.versions)
            self.assistant, self.overlay = self._build()
            after = dict(self.assistant.versions)
        changed = {k: {"antes": before[k], "depois": after[k]} for k in after if before.get(k) != after[k]}
        return before, after, changed

    def health(self):
        a = self.assistant
        return {
            "status": "ok",
            "service": SERVICE_NAME,
            "mode": "deterministic",
            "environment": self.environment,
            "release_id": self.release_id,
            "versions": dict(a.versions),
            "corpus_source": "overlay" if self.overlay else "repositorio",
            "overlay": self.overlay,
            "orders_base_url": self.orders_url,
            "telemetry": "otlp" if telemetry.enabled() else "off",
            "started_at": self.started_at,
        }


def make_handler(runtime):
    class Handler(BaseHTTPRequestHandler):
        server_version = "QuantumAssistant/1.0"

        def log_message(self, fmt, *args):
            pass

        def _send(self, code, body):
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _json_body(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            return json.loads(raw.decode("utf-8") or "{}")

        def do_GET(self):
            if self.path == "/health":
                return self._send(200, runtime.health())
            return self._send(404, {"error": "not_found"})

        def do_POST(self):
            if self.path == "/assistant/ask":
                return self._ask()
            if self.path == "/admin/reload":
                before, after, changed = runtime.reload()
                telemetry.log("runtime.reload", changed=sorted(changed), corpus_version=after["corpus_version"],
                              index_version=after["index_version"])
                return self._send(200, {"reloaded": True, "versions": after, "changed": changed,
                                        "corpus_source": runtime.health()["corpus_source"]})
            return self._send(404, {"error": "not_found"})

        def _ask(self):
            started = time.monotonic()
            request_id = self.headers.get("X-Request-Id") or uuid.uuid4().hex
            with telemetry.server_span(
                "http.request", self.headers,
                **{"http.request.method": "POST", "http.route": "/assistant/ask", "request_id": request_id},
            ) as sp:
                status, body, labels = 200, None, {}
                try:
                    payload = self._json_body()
                    question = payload.get("question")
                    if not isinstance(question, str) or not question.strip():
                        raise ValueError("campo 'question' obrigatorio")
                except (ValueError, json.JSONDecodeError) as err:
                    status, body = 400, {"error": f"requisicao invalida: {err}"}
                if body is None:
                    kwargs = {k: payload.get(k) for k in ASK_FIELDS if payload.get(k) is not None}
                    kwargs.update(PUBLIC_IDENTITY)
                    kwargs.setdefault("request_id", request_id)
                    scenario = payload.get("scenario_id")
                    try:
                        body = runtime.assistant.answer(question, **kwargs)
                        labels = {"abstained": body["abstained"], "abstain_reason": body["abstain_reason"]}
                        if body["route"] == "order_status":
                            labels["order_lookup"] = ORDER_OUTCOME.get(body["abstain_reason"], "other")
                        sp.set("route", body["route"])
                        sp.set("abstain_reason", body["abstain_reason"] or "none")
                        sp.set("corpus_version", body["versions"]["corpus_version"])
                        sp.set("index_version", body["versions"]["index_version"])
                    except Exception as err:  # noqa: BLE001 - qualquer falha vira 500 visivel no trace
                        status, body = 500, {"error": "erro interno", "kind": type(err).__name__}
                        sp.event("exception", type=type(err).__name__)
                    labels["scenario"] = scenario if scenario in KNOWN_SCENARIOS else (
                        "other" if scenario else "none")
                route = (body or {}).get("route", "invalid")
                sp.set("http.response.status_code", status)
                latency_ms = (time.monotonic() - started) * 1000
                telemetry.record_request(route, status, latency_ms, **labels)
                telemetry.log("http.request", path="/assistant/ask", status_code=status, route=route,
                              latency_ms=round(latency_ms, 1), request_id=request_id,
                              scenario_id=labels.get("scenario"))
            self._send(status, body)

    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(description="Runtime HTTP local do assistente (Demo 5.2)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("QC_PORT", "8080")))
    parser.add_argument("--orders-url", default=os.environ.get("QC_ORDERS_BASE_URL", "http://127.0.0.1:8081"))
    parser.add_argument("--release-id", default=os.environ.get("QC_RELEASE_ID") or default_release_id())
    parser.add_argument("--environment", default=os.environ.get("QC_ENVIRONMENT", "local-demo"))
    args = parser.parse_args(argv)

    telemetry.configure(SERVICE_NAME, versions.APPLICATION_VERSION, args.environment, args.release_id)
    runtime = Runtime(args.orders_url, args.release_id, args.environment)
    telemetry.prime_request_series(["policy", "faq", "order_status", "unsupported"], [200, 400, 500],
                                   ["ok", "unavailable", "not_found", "forbidden"])
    httpd = ThreadingHTTPServer((args.host, args.port), make_handler(runtime))
    h = runtime.health()
    print(f"http_server ouvindo em http://{args.host}:{args.port} release={h['release_id']} "
          f"corpus={h['versions']['corpus_version']} telemetria={h['telemetry']}", file=sys.stderr, flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        telemetry.shutdown()


if __name__ == "__main__":
    main()
