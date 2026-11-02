# Uma Lambda para o assistente e outra para a API de pedidos, as duas com o
# MESMO bundle. Por que duas funcoes: pedidos e um sistema de origem separado,
# que o assistente consulta por HTTP como faria com o ERP real. Se a API de
# pedidos falhar, o assistente recusa com order_api_unavailable em vez de cair.

resource "aws_cloudwatch_log_group" "assistant" {
  name              = "/aws/lambda/${var.project}-api"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "orders" {
  name              = "/aws/lambda/${var.project}-orders"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "assistant" {
  function_name = "${var.project}-api"
  role          = local.lab_role_arn
  runtime       = "python3.12"
  handler       = "adapters.lambda_handler.handler"
  timeout       = 15
  memory_size   = 256

  filename         = var.bundle_path
  source_code_hash = fileexists(var.bundle_path) ? filebase64sha256(var.bundle_path) : null

  # publish = true: cada apply com bundle ou release novo cria uma VERSAO
  # imutavel (1, 2, 3...). A versao e o conjunto coerente codigo + corpus +
  # prompts + config que o pipeline avaliou; e ela que o alias aponta.
  publish = true

  # O release_id NAO vem daqui: ele esta dentro do bundle (release/build_info.json),
  # para que versao da Lambda e release sejam sempre o mesmo conjunto.
  environment {
    variables = {
      QC_ENVIRONMENT     = "aws-lambda"
      QC_ORDERS_BASE_URL = aws_apigatewayv2_api.http.api_endpoint
    }
  }

  depends_on = [aws_cloudwatch_log_group.assistant]

  lifecycle {
    precondition {
      condition     = fileexists(var.bundle_path)
      error_message = "Bundle nao encontrado. Rode antes: python3 release/build_bundle.py"
    }
  }
}

# candidate acompanha SEMPRE a ultima versao publicada pelo apply: e nela que
# o smoke e o golden pos-deploy rodam antes de qualquer cliente ver.
resource "aws_lambda_alias" "candidate" {
  name             = "candidate"
  function_name    = aws_lambda_function.assistant.function_name
  function_version = aws_lambda_function.assistant.version
}

# live e o que o cliente usa. Nasce na primeira versao e depois so muda por
# promote.sh ou rollback.sh. O ignore_changes impede que um apply qualquer
# "promova" sem passar pelos gates.
resource "aws_lambda_alias" "live" {
  name             = "live"
  function_name    = aws_lambda_function.assistant.function_name
  function_version = aws_lambda_function.assistant.version

  lifecycle {
    ignore_changes = [function_version]
  }
}

resource "aws_lambda_function" "orders" {
  function_name = "${var.project}-orders"
  role          = local.lab_role_arn
  runtime       = "python3.12"
  handler       = "adapters.lambda_handler.orders_handler"
  timeout       = 5
  # Cold start medido na AWS (256 MB, boto3 importado dentro do handler): a 1a
  # consulta levava 2,75 a 2,8 s, acima do timeout de 2 s que o assistente usa
  # para chamar pedidos (config/settings.json), e so respondia no retry. Por isso
  # o boto3 e a tabela sao criados no carregamento do modulo (fase INIT; ver
  # adapters/lambda_handler.py). Mais CPU (memoria maior) tambem encurta o INIT.
  memory_size = 256

  filename         = var.bundle_path
  source_code_hash = fileexists(var.bundle_path) ? filebase64sha256(var.bundle_path) : null

  environment {
    variables = {
      QC_ORDERS_TABLE = aws_dynamodb_table.orders.name
    }
  }

  depends_on = [aws_cloudwatch_log_group.orders]
}

# Cada alias precisa da propria permissao: o API Gateway invoca o ARN
# qualificado (funcao:alias), nao a funcao "solta".
resource "aws_lambda_permission" "assistant_alias" {
  for_each      = { live = aws_lambda_alias.live.name, candidate = aws_lambda_alias.candidate.name }
  statement_id  = "AllowHttpApi-${each.key}"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.assistant.function_name
  qualifier     = each.value
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http.execution_arn}/*/*"
}

resource "aws_lambda_permission" "orders" {
  statement_id  = "AllowHttpApi"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.orders.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http.execution_arn}/*/*"
}
