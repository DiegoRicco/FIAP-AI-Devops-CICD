output "api_base_url" {
  description = "URL base da API de pedidos. Teste com: curl <url>/pedidos"
  # trimsuffix remove a barra final do invoke_url (.../), senao "$API_URL/pedidos"
  # viraria "//pedidos" e a API responderia 404.
  value = trimsuffix(aws_apigatewayv2_stage.default.invoke_url, "/")
}

output "pedidos_table" {
  description = "Nome da tabela DynamoDB de status dos pedidos."
  value       = aws_dynamodb_table.pedidos.name
}

output "assistente_url" {
  description = "URL completa do Assistente de Politicas (POST com {\"pergunta\": \"...\"})."
  value       = "${trimsuffix(aws_apigatewayv2_stage.default.invoke_url, "/")}/assistente/perguntas"
}
