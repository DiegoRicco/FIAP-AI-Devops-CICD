"""Cliente da API de pedidos (origem transacional) e a regra de autorizacao.

Por que o status nunca vem do RAG: documento explica o que um status significa;
so o sistema de pedidos sabe o status ATUAL. Quando os dois divergem, vale a API.

Contrato HTTP do servico de pedidos (Lambda da 5.1 e servico local da 5.2):
  GET {base_url}/orders/{order_id}
  Authorization: Bearer <token de laboratorio>
  traceparent: <W3C, opcional>      X-Request-Id: <opcional>
  200 {"order_id", "status", "updated_at"}   401 token ausente/invalido
  403 pedido de outro cliente                404 pedido inexistente
  429 limite de taxa                         5xx falha do servico
A autorizacao acontece NO SERVIDOR (serve_lookup): o token mapeia para um
cliente e so os pedidos desse cliente sao devolvidos. O customer_id enviado pelo
app nao autoriza nada, porque qualquer um poderia forja-lo.
"""
import json
import socket
import time
import urllib.error
import urllib.request

from . import telemetry


class OrderLookupError(Exception):
    """kind: forbidden | order_not_found | order_api_unavailable."""

    def __init__(self, kind, detail=""):
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


def serve_lookup(fixtures, order_id, auth_token):
    """Regra do SERVIDOR de pedidos, reutilizada pela Lambda e pelo servico local.

    Retorna (status_code, corpo). Pedidos listados em fixtures["faults"] devolvem
    o codigo configurado: e assim que o golden set prova a recusa por API fora.
    """
    customer = fixtures.get("tokens", {}).get(auth_token or "")
    if not customer:
        return 401, {"error": "unauthorized"}
    fault = fixtures.get("faults", {}).get(order_id)
    if fault:
        return int(fault), {"error": "simulated_fault"}
    order = fixtures.get("orders", {}).get(order_id)
    if order is None:
        return 404, {"error": "order_not_found"}
    if order["customer_id"] != customer:
        # 403 e nao 404: o laboratorio quer mostrar a negacao por autorizacao.
        return 403, {"error": "forbidden"}
    return 200, {"order_id": order_id, "status": order["status"], "updated_at": order["updated_at"]}


def _from_status(code, body):
    if code == 200:
        return {"order_id": body["order_id"], "status": body["status"], "updated_at": body["updated_at"]}
    if code in (401, 403):
        raise OrderLookupError("forbidden", f"http {code}")
    if code == 404:
        raise OrderLookupError("order_not_found", "http 404")
    raise OrderLookupError("order_api_unavailable", f"http {code}")


class OrdersClient:
    """Interface: lookup(order_id, auth_token) -> {order_id, status, updated_at}."""

    def lookup(self, order_id, auth_token, request_id=None):
        raise NotImplementedError


class InMemoryOrders(OrdersClient):
    """Mesma regra do servidor, sem rede. Usado nos testes e no eval local."""

    def __init__(self, fixtures):
        self.fixtures = fixtures

    def lookup(self, order_id, auth_token, request_id=None):
        code, body = serve_lookup(self.fixtures, order_id, auth_token)
        return _from_status(code, body)


class HttpOrders(OrdersClient):
    """GET com timeout e retry com backoff SO para 5xx e timeout.

    Por que nao repetir 429: o servidor pediu para esperar; insistir piora a
    fila. 4xx tambem nao se repete: a resposta nao muda tentando de novo.
    GET de consulta e idempotente, por isso o retry e seguro aqui.
    """

    def __init__(self, base_url, timeout_s=2.0, retries=2, backoff_s=0.2):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.retries = retries
        self.backoff_s = backoff_s

    def _get(self, order_id, auth_token, request_id):
        headers = {"Authorization": f"Bearer {auth_token}", "Accept": "application/json"}
        if request_id:
            headers["X-Request-Id"] = request_id
        headers = telemetry.inject_headers(headers)
        req = urllib.request.Request(f"{self.base_url}/orders/{order_id}", headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as err:
            return err.code, {}

    def lookup(self, order_id, auth_token, request_id=None):
        attempt = 0
        while True:
            try:
                code, body = self._get(order_id, auth_token, request_id)
                retryable = code >= 500
                detail = f"http {code}"
            except (socket.timeout, TimeoutError, urllib.error.URLError) as err:
                code, body, retryable, detail = None, {}, True, f"timeout/rede: {type(err).__name__}"
            if not retryable:
                return _from_status(code, body)
            if attempt >= self.retries:
                raise OrderLookupError("order_api_unavailable", f"{detail} apos {attempt + 1} tentativas")
            # Backoff exponencial: 0,2 s, 0,4 s... da tempo para o servico se recuperar.
            time.sleep(self.backoff_s * (2 ** attempt))
            attempt += 1
