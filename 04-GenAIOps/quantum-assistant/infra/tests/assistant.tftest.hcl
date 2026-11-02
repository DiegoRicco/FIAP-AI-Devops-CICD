# terraform test: roda um plan e confere as regras que o pipeline depende.
# Nao cria nada na AWS. Precisa do bundle (python3 release/build_bundle.py).

run "contrato_do_release" {
  command = plan

  assert {
    condition     = aws_lambda_function.assistant.publish == true
    error_message = "A Lambda do assistente precisa de publish = true: sem versao imutavel nao ha rollback."
  }

  assert {
    condition     = aws_lambda_alias.candidate.name == "candidate" && aws_lambda_alias.live.name == "live"
    error_message = "Os aliases candidate e live sao o contrato do promote.sh e do rollback.sh."
  }

  assert {
    condition     = aws_lambda_function.assistant.runtime == "python3.12" && aws_lambda_function.orders.runtime == "python3.12"
    error_message = "As duas Lambdas usam python3.12."
  }

  assert {
    condition     = startswith(aws_lambda_function.assistant.function_name, "qc-assistant-")
    error_message = "Prefixo qc-assistant-: nao pode colidir com a API do modulo 03 (quantum-ai-*)."
  }

  assert {
    condition     = aws_dynamodb_table.orders.billing_mode == "PAY_PER_REQUEST"
    error_message = "Tabela on-demand para nao pagar capacidade parada."
  }

  assert {
    condition     = length(aws_dynamodb_table_item.fixtures) == length(local.fixtures.orders)
    error_message = "Todos os pedidos ficticios de orders/fixtures.json precisam ir para a tabela."
  }

  assert {
    condition = alltrue([for r in [
      "POST /assistant/ask", "GET /health", "POST /candidate/assistant/ask",
      "GET /candidate/health", "GET /orders/{id}"
    ] : contains(keys(aws_apigatewayv2_route.routes), r)])
    error_message = "A API precisa das 5 rotas do contrato (live, candidate e orders)."
  }

}
