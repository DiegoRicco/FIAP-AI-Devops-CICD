"""API de pedidos e Assistente de Politicas da Quantum Commerce.

Uma unica Lambda atras de um API Gateway HTTP, persistindo em DynamoDB:
  GET  /pedidos                -> lista os status registrados
  GET  /pedidos/{id}           -> status de um pedido
  PUT  /pedidos/{id}           -> registra/atualiza o status (body JSON: {"status": "..."})
  POST /assistente/perguntas   -> responde duvidas com base nas politicas (rag.py)
                                  body JSON: {"pergunta": "...", "pedido_id": "opcional"}

O nome da tabela vem da variavel de ambiente TABLE_NAME (injetada pelo Terraform).
"""
import base64
import json
import os
import re

import boto3

import rag

TABLE_NAME = os.environ["TABLE_NAME"]
_dynamodb = boto3.resource("dynamodb")
_table = _dynamodb.Table(TABLE_NAME)

# Status aceitos para um pedido. Validamos no PUT para nao gravar lixo.
VALID_STATUS = {"confirmado", "em_transito", "em_separacao", "entregue"}

# Limites de entrada do assistente: pergunta gigante ou id estranho nao chegam
# ao indice nem ao DynamoDB.
MAX_PERGUNTA = 500
PEDIDO_ID_VALIDO = re.compile(r"^[A-Za-z0-9-]{1,40}$")


def _response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json; charset=utf-8"},
        # ensure_ascii=False mantem os acentos legiveis no curl do aluno.
        "body": json.dumps(body, ensure_ascii=False),
    }


def handler(event, context):
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    path_params = event.get("pathParameters") or {}
    pedido_id = path_params.get("id")

    # routeKey e a rota declarada no apigw.tf; nao depende do nome do stage na URL.
    if event.get("routeKey") == "POST /assistente/perguntas":
        return _perguntar(_ler_body(event))
    if method == "GET" and pedido_id:
        return _get_one(pedido_id)
    if method == "GET":
        return _list_all()
    if method == "PUT" and pedido_id:
        return _put_one(pedido_id, _ler_body(event))

    return _response(405, {"error": "metodo nao suportado", "method": method})


def _ler_body(event):
    raw = event.get("body")
    # O API Gateway entrega em base64 quando o cliente nao manda Content-Type de texto.
    if raw and event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    return raw


def _list_all():
    items = _table.scan().get("Items", [])
    return _response(200, {"count": len(items), "pedidos": items})


def _get_one(pedido_id):
    item = _table.get_item(Key={"pedido_id": pedido_id}).get("Item")
    if not item:
        return _response(404, {"error": "pedido nao encontrado", "pedido_id": pedido_id})
    return _response(200, item)


def _put_one(pedido_id, raw_body):
    try:
        payload = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError:
        return _response(400, {"error": "body nao e um JSON valido"})

    status = payload.get("status")
    if status not in VALID_STATUS:
        return _response(
            400,
            {"error": "status invalido", "aceitos": sorted(VALID_STATUS)},
        )

    item = {"pedido_id": pedido_id, "status": status}
    _table.put_item(Item=item)
    return _response(200, {"updated": item})


def _perguntar(raw_body):
    try:
        payload = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError:
        return _response(400, {"error": "body nao e um JSON valido"})

    pergunta = payload.get("pergunta")
    if not isinstance(pergunta, str) or not pergunta.strip() or len(pergunta) > MAX_PERGUNTA:
        return _response(400, {"error": f"informe 'pergunta' com 1 a {MAX_PERGUNTA} caracteres"})

    pedido = None
    pedido_id = payload.get("pedido_id")
    if pedido_id is not None:
        if not isinstance(pedido_id, str) or not PEDIDO_ID_VALIDO.match(pedido_id):
            return _response(400, {"error": "pedido_id invalido (use letras, numeros e hifen)"})
        # Status do pedido e dado transacional: vem sempre da tabela, nunca do indice.
        item = _table.get_item(Key={"pedido_id": pedido_id}).get("Item")
        pedido = {
            "pedido_id": pedido_id,
            "encontrado": item is not None,
            "status": item.get("status") if item else None,
        }

    return _response(200, rag.responder(pergunta, pedido))
