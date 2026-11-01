# Empacota o codigo da Lambda (pasta src/) num zip. O archive_file roda no
# proprio Terraform — sem etapa de build externa. O hash do zip vira o
# source_code_hash, entao alterar handler.py, rag.py ou uma politica .md reimplanta a funcao no proximo apply.
data "archive_file" "lambda_zip" {
  type        = "zip"
  source_dir  = "${path.module}/src"
  output_path = "${path.module}/build/lambda.zip"
  # .pyc gerado ao rodar os testes mudaria o hash e redeployaria a Lambda a toa.
  excludes = ["__pycache__", "__pycache__/*"]
}

resource "aws_lambda_function" "api" {
  function_name = "${var.project}-api"
  role          = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/LabRole"
  runtime       = "python3.12"
  handler       = "handler.handler"
  timeout       = 10
  memory_size   = 128

  filename         = data.archive_file.lambda_zip.output_path
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256

  environment {
    variables = {
      TABLE_NAME = aws_dynamodb_table.pedidos.name
    }
  }
}

# Permite que o API Gateway HTTP invoque a Lambda.
resource "aws_lambda_permission" "apigw" {
  statement_id  = "AllowInvokeFromHttpApi"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http.execution_arn}/*/*"
}
