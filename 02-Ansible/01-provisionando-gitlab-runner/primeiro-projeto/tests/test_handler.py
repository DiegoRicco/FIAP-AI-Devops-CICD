"""Testes do handler da Lambda com o DynamoDB simulado pelo botocore Stubber.

O Stubber intercepta a chamada do boto3 e devolve a resposta que definimos:
testamos o codigo de verdade sem criar tabela nem gastar credencial.
"""
import json

import pytest
from botocore.stub import Stubber

import handler


@pytest.fixture
def dynamo():
    with Stubber(handler._table.meta.client) as stub:
        yield stub
        stub.assert_no_pending_responses()


def evento(route_key, body=None, pedido_id=None):
    metodo, _ = route_key.split(" ", 1)
    return {
        "routeKey": route_key,
        "requestContext": {"http": {"method": metodo}},
        "pathParameters": {"id": pedido_id} if pedido_id else None,
        "body": json.dumps(body) if body is not None else None,
    }


def chamar(ev):
    r = handler.handler(ev, None)
    return r["statusCode"], json.loads(r["body"])


def test_pergunta_sem_pedido_nao_consulta_o_dynamodb(dynamo):
    status, body = chamar(evento("POST /assistente/perguntas", {"pergunta": "Qual o prazo de troca?"}))
    assert status == 200
    assert set(body) == {"resposta", "fontes", "fora_do_escopo", "pedido", "versao_politicas"}
    assert body["pedido"] is None and body["fontes"]


def test_pergunta_com_pedido_le_o_status_no_dynamodb(dynamo):
    dynamo.add_response(
        "get_item",
        {"Item": {"pedido_id": {"S": "QC-1001"}, "status": {"S": "em_transito"}}},
        {"TableName": "quantum-ai-pedidos", "Key": {"pedido_id": "QC-1001"}},
    )
    status, body = chamar(evento("POST /assistente/perguntas", {"pergunta": "Onde está meu pedido?", "pedido_id": "QC-1001"}))
    assert status == 200
    assert body["pedido"] == {"pedido_id": "QC-1001", "encontrado": True, "status": "em_transito"}
    assert body["fontes"][0]["secao"] == "em_transito"


def test_pedido_inexistente(dynamo):
    dynamo.add_response("get_item", {}, {"TableName": "quantum-ai-pedidos", "Key": {"pedido_id": "QC-9"}})
    _, body = chamar(evento("POST /assistente/perguntas", {"pergunta": "Cadê meu pedido?", "pedido_id": "QC-9"}))
    assert body["pedido"]["encontrado"] is False
    assert "Não encontrei o pedido QC-9" in body["resposta"]


@pytest.mark.parametrize("body", [{}, {"pergunta": ""}, {"pergunta": "x" * 501}, {"pergunta": "oi", "pedido_id": "a b"}])
def test_entrada_invalida_retorna_400(dynamo, body):
    status, _ = chamar(evento("POST /assistente/perguntas", body))
    assert status == 400


def test_rotas_de_pedidos_continuam_funcionando(dynamo):
    dynamo.add_response(
        "get_item",
        {"Item": {"pedido_id": {"S": "QC-1001"}, "status": {"S": "entregue"}}},
        {"TableName": "quantum-ai-pedidos", "Key": {"pedido_id": "QC-1001"}},
    )
    status, body = chamar(evento("GET /pedidos/{id}", pedido_id="QC-1001"))
    assert status == 200 and body["status"] == "entregue"

    status, _ = chamar(evento("PUT /pedidos/{id}", {"status": "perdido"}, pedido_id="QC-1001"))
    assert status == 400
