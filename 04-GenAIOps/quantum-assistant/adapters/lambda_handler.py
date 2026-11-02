"""Adapter AWS Lambda (API Gateway HTTP, payload 2.0) do assistente Quantum Commerce.

O mesmo core.Assistant que roda no teste, no pipeline e no runtime local da 5.2.
Este arquivo so traduz HTTP <-> Python; nenhuma regra de negocio mora aqui.

Duas funcoes Lambda usam o MESMO bundle, cada uma com seu ponto de entrada:
  handler         (qc-assistant-api)    POST /assistant/ask, GET /health
                                        e as mesmas rotas sob /candidate
  orders_handler  (qc-assistant-orders) GET /orders/{id}
Por que separar pedidos em outra funcao: pedidos e um sistema de origem, como
o ERP real. O assistente o consulta por HTTP (core.orders.HttpOrders), com
timeout e retry; se a API de pedidos cair, o assistente recusa com
order_api_unavailable em vez de cair junto. Mesmo bundle = um so artefato
versionado; funcoes separadas = falhas e logs separados.
"""
import base64
import json
import os
import re
import sys
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core import telemetry  # noqa: E402
from core.orders import serve_lookup  # noqa: E402

MAX_QUESTION = 500
ORDER_ID_RE = re.compile(r"^[A-Za-z0-9-]{1,40}$")
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")
BUILD_INFO = os.path.join(ROOT, "release", "build_info.json")

# Canal publico: o servidor decide quem e o usuario. tenant, user_role e
# as_of_date enviados no corpo sao IGNORADOS, senao qualquer um pediria
# user_role=internal e leria o manual interno, ou "viajaria no tempo" para
# ler uma politica que ainda nao entrou em vigor.
PUBLIC_IDENTITY = {"tenant": "quantum", "user_role": "customer"}

_assistant = None
_orders_table = None


def _json(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json; charset=utf-8"},
        "body": json.dumps(body, ensure_ascii=False),
    }


def _log(**fields):
    # Uma linha JSON por requisicao no CloudWatch. Sem pergunta, token ou cliente.
    print(json.dumps(fields, ensure_ascii=False, default=str), flush=True)


def build_info():
    """Versoes gravadas pelo build_bundle.py. Fora do bundle (teste local), nao existe."""
    if os.path.exists(BUILD_INFO):
        with open(BUILD_INFO, encoding="utf-8") as f:
            return json.load(f)
    return {}


def get_assistant():
    """Uma instancia por container: o indice BM25 e montado so no cold start."""
    global _assistant
    if _assistant is None:
        from core.assistant import Assistant
        info = build_info()
        overrides = {}
        if info.get("release_id"):
            overrides["release_id"] = info["release_id"]
        if info.get("eval_set_version"):
            overrides["eval_set_version"] = info["eval_set_version"]
        _assistant = Assistant(overrides)
    return _assistant


def _alias(context):
    """live ou candidate, lido do ARN invocado (arn:...:function:nome:alias)."""
    arn = getattr(context, "invoked_function_arn", "") or ""
    parts = arn.split(":")
    return parts[7] if len(parts) == 8 else "unqualified"


def _body(event):
    raw = event.get("body") or ""
    if raw and event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    return json.loads(raw) if raw else {}


def _bearer(event):
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    value = headers.get("authorization", "")
    return value[7:].strip() if value.lower().startswith("bearer ") else None


def _health(context):
    a = get_assistant()
    return _json(200, {
        "status": "ok",
        "release_id": a.settings.get("release_id"),
        "environment": _alias(context),
        "function_version": getattr(context, "function_version", None),
        "git_sha": build_info().get("git_sha"),
        "versions": dict(a.versions),
        "mode": "deterministic",
    })


def _ask(event, context, request_id):
    try:
        payload = _body(event)
    except (ValueError, UnicodeDecodeError):
        return _json(400, {"error": "corpo nao e um JSON valido", "request_id": request_id})
    if not isinstance(payload, dict):
        return _json(400, {"error": "corpo precisa ser um objeto JSON", "request_id": request_id})

    question = payload.get("question")
    if not isinstance(question, str) or not question.strip() or len(question) > MAX_QUESTION:
        return _json(400, {"error": f"informe 'question' com 1 a {MAX_QUESTION} caracteres",
                           "request_id": request_id})
    order_id = payload.get("order_id")
    if order_id is not None and (not isinstance(order_id, str) or not ORDER_ID_RE.match(order_id)):
        return _json(400, {"error": "order_id invalido (letras, numeros e hifen)", "request_id": request_id})

    auth_token = _bearer(event) or payload.get("auth_token")
    scenario_id = payload.get("scenario_id") if isinstance(payload.get("scenario_id"), str) else None
    response = get_assistant().answer(
        question, customer_id=payload.get("customer_id"), auth_token=auth_token, order_id=order_id,
        scenario_id=scenario_id, request_id=request_id, **PUBLIC_IDENTITY)
    # Containers sao por VERSAO, nao por alias: depois do promote, live e
    # candidate podem dividir o mesmo container. Por isso o ambiente e decidido
    # por requisicao, a partir do alias que foi invocado.
    response["environment"] = _alias(context)
    return _json(200, response)


def handler(event, context):
    """Assistente: rotas live e /candidate, mesmo codigo; o alias decide o ambiente."""
    started = time.monotonic()
    route_key = event.get("routeKey", "")
    # "POST /candidate/assistant/ask" -> "/assistant/ask": mesma rota, outro alias.
    path = route_key.split(" ", 1)[-1]
    if path.startswith("/candidate/"):
        path = path[len("/candidate"):]
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    request_id = headers.get("x-request-id", "")
    if not REQUEST_ID_RE.match(request_id):
        request_id = uuid.uuid4().hex
    # Span de entrada do contrato. Na Lambda o OTel nao esta configurado (X-Ray e
    # bloqueado no Learner Lab), entao vira no-op; o tracing real e o da 5.2, local.
    with telemetry.server_span("http.request", headers, route=path) as sp:
        try:
            if path == "/health":
                resp = _health(context)
            elif path == "/assistant/ask":
                resp = _ask(event, context, request_id)
            else:
                resp = _json(404, {"error": "rota desconhecida", "route": route_key})
        except Exception as err:  # noqa: BLE001
            # Nunca devolver stack trace ao cliente; o tipo do erro vai para o log.
            _log(event="assistant.error", request_id=request_id, error=type(err).__name__)
            resp = _json(500, {"error": "erro interno", "request_id": request_id})
        sp.set("http.response.status_code", resp["statusCode"])
    _log(event="http.request", route=route_key, status=resp["statusCode"], request_id=request_id,
         environment=_alias(context), latency_ms=round((time.monotonic() - started) * 1000, 1))
    return resp


# ------------------------------------------------------------------ pedidos
def get_orders_table():
    """Tabela DynamoDB dos pedidos. Nos testes, um objeto falso substitui esta."""
    global _orders_table
    if _orders_table is None:
        import boto3  # ja vem no runtime python3.12 da Lambda
        _orders_table = boto3.resource("dynamodb").Table(os.environ["QC_ORDERS_TABLE"])
    return _orders_table


def _bundled_fixtures():
    # Tokens de laboratorio e falhas planejadas vem do bundle (nao sao segredo);
    # o pedido em si vem da tabela, que e a origem transacional.
    with open(os.path.join(ROOT, "orders", "fixtures.json"), encoding="utf-8") as f:
        return json.load(f)


def orders_handler(event, context):
    """GET /orders/{id}: a MESMA regra de autorizacao do servico local (serve_lookup)."""
    started = time.monotonic()
    order_id = (event.get("pathParameters") or {}).get("id", "")
    if not ORDER_ID_RE.match(order_id or ""):
        return _json(400, {"error": "order_id invalido"})
    try:
        fixtures = _bundled_fixtures()
        item = get_orders_table().get_item(Key={"order_id": order_id}).get("Item")
        view = {"tokens": fixtures.get("tokens", {}), "faults": fixtures.get("faults", {}),
                "orders": {order_id: item} if item else {}}
        code, body = serve_lookup(view, order_id, _bearer(event))
    except Exception as err:  # noqa: BLE001
        _log(event="orders.error", error=type(err).__name__)
        code, body = 500, {"error": "erro interno"}
    _log(event="orders.lookup", status=code, latency_ms=round((time.monotonic() - started) * 1000, 1))
    return _json(code, body)
