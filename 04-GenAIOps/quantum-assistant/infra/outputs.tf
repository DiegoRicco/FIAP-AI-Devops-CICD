locals {
  base_url = trimsuffix(aws_apigatewayv2_stage.default.invoke_url, "/")
}

output "assistant_url" {
  description = "Base do assistente LIVE (o que o cliente usa). Pergunte com POST <url>/assistant/ask."
  value       = local.base_url
}

output "candidate_url" {
  description = "Base da versao CANDIDATA. O smoke e o golden pos-deploy rodam aqui antes da promocao."
  value       = "${local.base_url}/candidate"
}

output "orders_url" {
  description = "API de pedidos ficticia. GET <url>/Q-1001 com Authorization: Bearer lab-token-ana."
  value       = "${local.base_url}/orders"
}

output "function_name" {
  description = "Lambda do assistente (usada por promote.sh e rollback.sh)."
  value       = aws_lambda_function.assistant.function_name
}

output "candidate_version" {
  description = "Versao publicada por este apply; o alias candidate aponta para ela."
  value       = aws_lambda_function.assistant.version
}
