# API HTTP (apigatewayv2): mais simples e barata que a REST API classica, e
# suficiente para expor a Lambda. O stage "$default" com auto_deploy publica a
# API numa URL https assim que o apply termina.
resource "aws_apigatewayv2_api" "http" {
  name          = "${var.project}-api"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "lambda" {
  api_id                 = aws_apigatewayv2_api.http.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.api.invoke_arn
  integration_method     = "POST"
  payload_format_version = "2.0"
}

# Rotas da API. Cada uma encaminha para a mesma Lambda (proxy), que decide o que
# fazer pelo metodo e pelo path.
resource "aws_apigatewayv2_route" "routes" {
  for_each = toset([
    "GET /pedidos",
    "GET /pedidos/{id}",
    "PUT /pedidos/{id}",
    "POST /assistente/perguntas",
  ])
  api_id    = aws_apigatewayv2_api.http.id
  route_key = each.value
  target    = "integrations/${aws_apigatewayv2_integration.lambda.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.http.id
  name        = "$default"
  auto_deploy = true
}
