"""HttpOrders contra um servidor HTTP local de verdade (stdlib), sem AWS.

Prova: retry com backoff so em 5xx/timeout, 429 sem retry, traceparent enviado
via telemetry.inject_headers e mapeamento de 401/403/404 para o contrato.
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import orders as orders_mod  # noqa: E402
from core.orders import HttpOrders, OrderLookupError, serve_lookup  # noqa: E402

with open(os.path.join(ROOT, "orders", "fixtures.json"), encoding="utf-8") as f:
    FIXTURES = json.load(f)

TRACEPARENT = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"


class Server:
    def __init__(self):
        self.calls, self.script, self.headers = [], [], []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                server.calls.append(self.path)
                # HTTP nao diferencia caixa no nome do header; o urllib manda 'Traceparent'.
                server.headers.append({k.lower(): v for k, v in self.headers.items()})
                action = server.script.pop(0) if server.script else "normal"
                if action == "sleep":
                    threading.Event().wait(0.5)
                    return
                if isinstance(action, int):
                    code, body = action, {"error": "scripted"}
                else:
                    token = (self.headers.get("Authorization") or "").replace("Bearer ", "")
                    code, body = serve_lookup(FIXTURES, self.path.rsplit("/", 1)[-1], token)
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()


@pytest.fixture
def server():
    s = Server()
    yield s
    s.httpd.shutdown()


@pytest.fixture
def client(server):
    return HttpOrders(server.url, timeout_s=0.2, retries=2, backoff_s=0.01)


def test_ok_e_envia_traceparent(server, client, monkeypatch):
    monkeypatch.setattr(orders_mod.telemetry, "inject_headers",
                        lambda h: {**h, "traceparent": TRACEPARENT})
    order = client.lookup("Q-1001", "lab-token-ana", request_id="req-7")
    assert order == {"order_id": "Q-1001", "status": "em_transito", "updated_at": "2026-10-30T14:05:00Z"}
    assert server.headers[0].get("traceparent") == TRACEPARENT
    assert server.headers[0].get("x-request-id") == "req-7"


def test_5xx_repete_e_recupera(server, client):
    server.script = [503, 502]
    assert client.lookup("Q-1001", "lab-token-ana")["status"] == "em_transito"
    assert len(server.calls) == 3


def test_5xx_persistente_vira_indisponivel(server, client):
    server.script = [500, 500, 500]
    with pytest.raises(OrderLookupError) as err:
        client.lookup("Q-1001", "lab-token-ana")
    assert err.value.kind == "order_api_unavailable" and len(server.calls) == 3


def test_429_nao_repete(server, client):
    server.script = [429]
    with pytest.raises(OrderLookupError) as err:
        client.lookup("Q-1001", "lab-token-ana")
    assert err.value.kind == "order_api_unavailable" and len(server.calls) == 1


def test_timeout_repete_e_vira_indisponivel(server, client):
    server.script = ["sleep", "sleep", "sleep"]
    with pytest.raises(OrderLookupError) as err:
        client.lookup("Q-1001", "lab-token-ana")
    assert err.value.kind == "order_api_unavailable" and len(server.calls) == 3


@pytest.mark.parametrize("order_id,token,kind", [
    ("Q-2001", "lab-token-ana", "forbidden"),
    ("Q-1001", "token-inventado", "forbidden"),
    ("Q-9999", "lab-token-ana", "order_not_found"),
])
def test_4xx_nao_repete(server, client, order_id, token, kind):
    with pytest.raises(OrderLookupError) as err:
        client.lookup(order_id, token)
    assert err.value.kind == kind and len(server.calls) == 1


def test_api_fora_do_ar(client):
    dead = HttpOrders("http://127.0.0.1:9", timeout_s=0.2, retries=1, backoff_s=0.01)
    with pytest.raises(OrderLookupError) as err:
        dead.lookup("Q-1001", "lab-token-ana")
    assert err.value.kind == "order_api_unavailable"
