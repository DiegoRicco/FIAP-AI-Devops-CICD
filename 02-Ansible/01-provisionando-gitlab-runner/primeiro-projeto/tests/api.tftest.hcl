# Testes nativos do Terraform (terraform test). Rodam um 'plan' e fazem asserts
# sobre os recursos planejados — sem criar nada na AWS. E o gate de "o codigo faz
# o que dizemos que faz" no pipeline de CI.

run "valida_recursos_da_api" {
  command = plan

  assert {
    condition     = aws_dynamodb_table.pedidos.billing_mode == "PAY_PER_REQUEST"
    error_message = "A tabela DynamoDB deve ser on-demand (PAY_PER_REQUEST) para ficar no free-tier."
  }

  assert {
    condition     = aws_lambda_function.api.runtime == "python3.12"
    error_message = "A Lambda deve usar o runtime python3.12."
  }

  assert {
    condition     = aws_lambda_function.api.environment[0].variables["TABLE_NAME"] == aws_dynamodb_table.pedidos.name
    error_message = "A Lambda precisa receber o nome da tabela via variavel de ambiente TABLE_NAME."
  }

  assert {
    condition     = aws_apigatewayv2_api.http.protocol_type == "HTTP"
    error_message = "A API deve ser do tipo HTTP (apigatewayv2)."
  }

  assert {
    condition     = length(aws_apigatewayv2_route.routes) == 4
    error_message = "A API deve expor exatamente 4 rotas (lista, consulta, atualizacao e assistente)."
  }

  assert {
    condition     = contains(keys(aws_apigatewayv2_route.routes), "POST /assistente/perguntas")
    error_message = "A rota do Assistente de Politicas (POST /assistente/perguntas) precisa existir."
  }
}
