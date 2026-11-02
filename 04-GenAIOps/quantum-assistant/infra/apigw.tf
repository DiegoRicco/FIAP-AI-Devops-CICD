# Uma API HTTP com tres destinos: live (cliente), candidate (verificacao antes
# de promover) e orders (sistema de pedidos ficticio).
resource "aws_apigatewayv2_api" "http" {
  name          = var.project
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "live" {
  api_id                 = aws_apigatewayv2_api.http.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_alias.live.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_integration" "candidate" {
  api_id                 = aws_apigatewayv2_api.http.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_alias.candidate.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_integration" "orders" {
  api_id                 = aws_apigatewayv2_api.http.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.orders.invoke_arn
  payload_format_version = "2.0"
}

locals {
  # rota -> integracao. As rotas /candidate/* existem so para o pipeline
  # testar a versao nova na nuvem antes de o cliente receber.
  routes = {
    "POST /assistant/ask"           = aws_apigatewayv2_integration.live.id
    "GET /health"                   = aws_apigatewayv2_integration.live.id
    "POST /candidate/assistant/ask" = aws_apigatewayv2_integration.candidate.id
    "GET /candidate/health"         = aws_apigatewayv2_integration.candidate.id
    "GET /orders/{id}"              = aws_apigatewayv2_integration.orders.id
  }
}

resource "aws_apigatewayv2_route" "routes" {
  for_each  = local.routes
  api_id    = aws_apigatewayv2_api.http.id
  route_key = each.key
  target    = "integrations/${each.value}"
}

resource "aws_cloudwatch_log_group" "api_access" {
  name              = "/aws/apigateway/${var.project}"
  retention_in_days = var.log_retention_days
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.http.id
  name        = "$default"
  auto_deploy = true

  # Limite de taxa: protege a conta do Learner Lab de um loop de teste.
  default_route_settings {
    throttling_burst_limit = 20
    throttling_rate_limit  = 10
  }

  # Log de acesso sem corpo da requisicao (sem pergunta, sem token).
  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_access.arn
    format = jsonencode({
      requestId = "$context.requestId"
      routeKey  = "$context.routeKey"
      status    = "$context.status"
      latencyMs = "$context.responseLatency"
    })
  }
}
