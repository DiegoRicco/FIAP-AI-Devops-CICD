"""Adapter Lambda: contrato HTTP sobre o MESMO core, sem AWS.

O DynamoDB e trocado por uma tabela falsa em memoria, e a API de pedidos que o
assistente chama por HTTP e trocada por InMemoryOrders (mesma regra do servidor).
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from adapters import lambda_handler as lh  # noqa: E402
from core.assistant import Assistant  # noqa: E402
from core.orders import InMemoryOrders  # noqa: E402

with open(os.path.join(ROOT, "orders", "fixtures.json"), encoding="utf-8") as f:
    FIXTURES = json.load(f)


class Ctx:
    def __init__(self, alias="candidate", version="7"):
        self.invoked_function_arn = f"arn:aws:lambda:us-east-1:000000000000:function:qc-assistant-api:{alias}"
        self.function_version = version


class FakeTable:
    """Stub do boto3 Table: so o get_item que o handler usa."""

    def __init__(self, orders):
        self.orders = orders
        self.calls = []

    def get_item(self, Key):
        self.calls.append(Key)
        item = self.orders.get(Key["order_id"])
        return {"Item": dict(item, order_id=Key["order_id"])} if item else {}


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(lh, "_assistant", Assistant({"as_of_date": "2026-11-01", "release_id": "rel-teste"},
                                                    orders=InMemoryOrders(FIXTURES)))
    table = FakeTable(FIXTURES["orders"])
    monkeypatch.setattr(lh, "_orders_table", table)
    return table


def ask(body, route="POST /assistant/ask", headers=None, alias="live"):
    event = {"routeKey": route, "headers": headers or {}, "body": json.dumps(body)}
    resp = lh.handler(event, Ctx(alias))
    return resp["statusCode"], json.loads(resp["body"])


def test_health_traz_versoes_e_release():
    resp = lh.handler({"routeKey": "GET /candidate/health", "headers": {}}, Ctx("candidate", "7"))
    body = json.loads(resp["body"])
    assert resp["statusCode"] == 200
    assert body["release_id"] == "rel-teste"
    assert body["environment"] == "candidate"
    assert body["function_version"] == "7"
    assert set(body["versions"]) == {"application_version", "prompt_version", "corpus_version",
                                     "index_version", "eval_set_version", "model_version"}


def test_ask_politica_cita_fonte_vigente():
    code, body = ask({"question": "Qual o prazo para trocar uma roupa que comprei?"})
    assert code == 200
    assert body["mode"] == "deterministic" and body["route"] == "policy"
    assert body["environment"] == "live"
    assert body["citations"] and body["citations"][0]["doc_id"] == "politica-trocas"
    assert body["citations"][0]["version"] == "3"


def test_ask_candidate_usa_mesmo_codigo():
    code, body = ask({"question": "Qual o prazo para trocar uma roupa que comprei?"},
                     route="POST /candidate/assistant/ask", alias="candidate")
    assert code == 200 and body["environment"] == "candidate"


def test_pedido_com_bearer_vem_da_origem_transacional():
    code, body = ask({"question": "Qual o status do pedido Q-1001?", "order_id": "Q-1001"},
                     headers={"Authorization": "Bearer lab-token-ana"})
    assert code == 200
    assert body["route"] == "order_status"
    assert body["order"]["status"] == "em_transito"


def test_canal_publico_ignora_tenant_e_papel_do_corpo():
    # Pedir user_role=internal pelo corpo nao pode liberar o manual interno.
    _, publico = ask({"question": "Qual o procedimento interno de reembolso?"})
    _, forjado = ask({"question": "Qual o procedimento interno de reembolso?",
                      "user_role": "internal", "tenant": "parceiro-x"})
    cited = {c["doc_id"] for c in forjado["citations"]}
    assert cited == {c["doc_id"] for c in publico["citations"]}
    assert not any("interno" in d for d in cited)


@pytest.mark.parametrize("body,expected", [
    ({}, 400),
    ({"question": ""}, 400),
    ({"question": "x" * 501}, 400),
    ({"question": "status", "order_id": "Q 1001; drop"}, 400),
])
def test_entrada_invalida_e_400(body, expected):
    code, payload = ask(body)
    assert code == expected and "error" in payload


def test_json_invalido_e_400():
    resp = lh.handler({"routeKey": "POST /assistant/ask", "headers": {}, "body": "{nao-json"}, Ctx())
    assert resp["statusCode"] == 400


def test_erro_interno_nao_vaza_stack(monkeypatch):
    class Quebrado:
        def answer(self, *a, **k):
            raise RuntimeError("segredo interno")
    monkeypatch.setattr(lh, "_assistant", Quebrado())
    code, body = ask({"question": "Qual o prazo de troca?"})
    assert code == 500 and "segredo" not in json.dumps(body)


@pytest.mark.parametrize("order_id,token,code", [
    ("Q-1001", "lab-token-ana", 200),
    ("Q-2001", "lab-token-ana", 403),   # pedido de outro cliente
    ("Q-9999", "lab-token-ana", 404),
    ("Q-1001", None, 401),
    ("Q-5000", "lab-token-ana", 503),   # falha planejada
])
def test_orders_handler_autoriza_no_servidor(order_id, token, code, isolated):
    headers = {"authorization": f"Bearer {token}"} if token else {}
    resp = lh.orders_handler({"routeKey": "GET /orders/{id}", "headers": headers,
                              "pathParameters": {"id": order_id}}, Ctx())
    assert resp["statusCode"] == code
    if code == 200:
        assert json.loads(resp["body"])["status"] == "em_transito"


def test_orders_handler_le_da_tabela(isolated):
    lh.orders_handler({"headers": {"authorization": "Bearer lab-token-ana"},
                       "pathParameters": {"id": "Q-1002"}}, Ctx())
    assert isolated.calls == [{"order_id": "Q-1002"}]


def test_orders_handler_rejeita_id_invalido():
    resp = lh.orders_handler({"headers": {}, "pathParameters": {"id": "../../etc"}}, Ctx())
    assert resp["statusCode"] == 400
