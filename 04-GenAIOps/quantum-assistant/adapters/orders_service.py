"""Servico local de pedidos da Demo 5.2 (porta 8081), com falhas injetaveis.

Por que existe: na 5.2 queremos ver um trace DISTRIBUIDO real (assistente ->
pedidos) e provocar a degradacao da API de pedidos sem tocar na AWS. A regra de
autorizacao e a mesma da Lambda da 5.1 (core.orders.serve_lookup) e os dados vem
do mesmo arquivo de pedidos ficticios (orders/fixtures.json).

Endpoints:
  GET  /orders/{order_id}   Authorization: Bearer <token de laboratorio>
  GET  /health
  GET  /admin/faults        mostra as falhas ativas
  POST /admin/faults        {"latency_ms": 800, "error_rate": 0.3, "timeout_rate": 0.1, "seed": 7}
  POST /admin/faults/reset  volta ao normal

As falhas sao sorteadas com random.Random(seed): a mesma seed produz a mesma
sequencia de erros, o que permite repetir o incidente e o replay.
So escuta em 127.0.0.1: e um servico de laboratorio, sem nada a expor.
"""
import argparse
import json
import os
import random
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core import telemetry  # noqa: E402
from core.orders import serve_lookup  # noqa: E402

SERVICE_NAME = "quantum-orders-api"
NORMAL = {"latency_ms": 0, "error_rate": 0.0, "timeout_rate": 0.0, "seed": None}


class FaultState:
    """Falhas ativas, protegidas por lock porque o servidor atende em threads."""

    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        with self.lock:
            self.config = dict(NORMAL)
            self.rng = random.Random(0)

    def update(self, payload):
        with self.lock:
            for key in ("latency_ms", "error_rate", "timeout_rate", "seed"):
                if key in payload:
                    self.config[key] = payload[key]
            # Aceita o nome curto usado no enunciado da demo.
            if "5xx_rate" in payload:
                self.config["error_rate"] = payload["5xx_rate"]
            self.config["latency_ms"] = max(0, int(self.config["latency_ms"] or 0))
            self.config["error_rate"] = min(1.0, max(0.0, float(self.config["error_rate"] or 0)))
            self.config["timeout_rate"] = min(1.0, max(0.0, float(self.config["timeout_rate"] or 0)))
            self.rng = random.Random(self.config["seed"] if self.config["seed"] is not None else 0)
            return dict(self.config)

    def draw(self):
        """Decide o destino desta requisicao: 'ok', 'error' ou 'timeout'."""
        with self.lock:
            cfg = dict(self.config)
            roll = self.rng.random()
        if roll < cfg["timeout_rate"]:
            return cfg, "timeout"
        if roll < cfg["timeout_rate"] + cfg["error_rate"]:
            return cfg, "error"
        return cfg, "ok"


def load_fixtures(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def make_handler(fixtures, faults, timeout_hold_s):
    class Handler(BaseHTTPRequestHandler):
        server_version = "QuantumOrders/1.0"

        def log_message(self, fmt, *args):
            # O log de acesso padrao do http.server vai para stderr sem trace_id;
            # usamos o log JSON da telemetria no lugar.
            pass

        def _send(self, code, body):
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            try:
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                # O cliente desistiu (timeout dele): nada a responder.
                pass

        def _json_body(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            return json.loads(raw.decode("utf-8") or "{}")

        def do_GET(self):
            if self.path == "/health":
                return self._send(200, {"status": "ok", "service": SERVICE_NAME})
            if self.path == "/admin/faults":
                return self._send(200, faults.config)
            if self.path.startswith("/orders/"):
                return self._lookup(self.path[len("/orders/"):].split("?")[0])
            return self._send(404, {"error": "not_found"})

        def do_POST(self):
            if self.path == "/admin/faults":
                try:
                    cfg = faults.update(self._json_body())
                except (ValueError, TypeError) as err:
                    return self._send(400, {"error": f"payload invalido: {err}"})
                telemetry.log("orders.faults_updated", **cfg)
                return self._send(200, cfg)
            if self.path == "/admin/faults/reset":
                faults.reset()
                telemetry.log("orders.faults_reset")
                return self._send(200, faults.config)
            return self._send(404, {"error": "not_found"})

        def _lookup(self, order_id):
            started = time.monotonic()
            auth = self.headers.get("Authorization", "")
            token = auth[len("Bearer "):] if auth.startswith("Bearer ") else ""
            with telemetry.server_span(
                "orders.get", self.headers,
                **{"http.request.method": "GET", "http.route": "/orders/{order_id}",
                   "order_id": order_id, "request_id": self.headers.get("X-Request-Id")},
            ) as sp:
                cfg, fate = faults.draw()
                sp.set("fault.fate", fate)
                if cfg["latency_ms"]:
                    time.sleep(cfg["latency_ms"] / 1000.0)
                if fate == "timeout":
                    # Segura a conexao alem do timeout do cliente: e assim que um
                    # timeout real aparece (o cliente desiste, nao recebe 5xx).
                    time.sleep(timeout_hold_s)
                    code, body = 504, {"error": "simulated_timeout"}
                elif fate == "error":
                    code, body = 503, {"error": "simulated_5xx"}
                else:
                    code, body = serve_lookup(fixtures, order_id, token)
                sp.set("http.response.status_code", code)
                latency_ms = (time.monotonic() - started) * 1000
                telemetry.log("orders.lookup", order_id=order_id, status_code=code,
                              fault=fate, latency_ms=round(latency_ms, 1),
                              request_id=self.headers.get("X-Request-Id"))
                telemetry.record_request("/orders/{order_id}", code, latency_ms)
                return self._send(code, body)

    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(description="Servico local de pedidos com falhas injetaveis")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("ORDERS_PORT", "8081")))
    parser.add_argument("--fixtures", default=os.path.join(ROOT, "orders", "fixtures.json"))
    parser.add_argument("--timeout-hold-s", type=float, default=3.0,
                        help="quanto o fault 'timeout' segura a conexao")
    args = parser.parse_args(argv)

    fixtures = load_fixtures(args.fixtures)
    telemetry.configure(SERVICE_NAME, "1.0.0", os.environ.get("QC_ENVIRONMENT", "local-demo"),
                        os.environ.get("QC_RELEASE_ID", "local"))
    telemetry.prime_request_series(["/orders/{order_id}"], [200, 403, 404, 503, 504])
    faults = FaultState()
    httpd = ThreadingHTTPServer((args.host, args.port), make_handler(fixtures, faults, args.timeout_hold_s))
    print(f"orders_service ouvindo em http://{args.host}:{args.port} "
          f"(telemetria {'ligada' if telemetry.enabled() else 'desligada'})", file=sys.stderr, flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        telemetry.shutdown()


if __name__ == "__main__":
    main()
