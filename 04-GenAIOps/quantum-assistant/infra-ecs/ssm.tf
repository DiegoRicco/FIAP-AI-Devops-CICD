# Parametros NAO secretos que este Terraform publica. As senhas
# (neo4j-password, neo4j-auth, admin-hmac) sao criadas por
# scripts/cloud/secrets.sh com a AWS CLI, de proposito fora do Terraform: assim
# o valor nunca passa pelo state no S3.

# Endpoint OTLP do app. Nasce "disabled" (o app nao exporta telemetria); a
# Demo 5.2 grava aqui o IP do collector. ignore_changes: um apply desta pasta
# nao desliga a telemetria que a 5.2 ligou.
resource "aws_ssm_parameter" "otlp_endpoint" {
  name        = "/${var.project}/obs/otlp-endpoint"
  description = "Endpoint OTLP HTTP do collector (Demo 5.2) ou disabled"
  type        = "String"
  value       = "disabled"

  lifecycle {
    ignore_changes = [value]
  }
}

# O pipeline e os scripts leem daqui, sem precisar de terraform init.
resource "aws_ssm_parameter" "alb_url" {
  name  = "/${var.project}/ecs/alb-url"
  type  = "String"
  value = "http://${aws_lb.this.dns_name}"
}

resource "aws_ssm_parameter" "taskdef_template" {
  for_each = local.environments
  name     = "/${var.project}/ecs/${each.key}/taskdef-template-arn"
  type     = "String"
  value    = aws_ecs_task_definition.env[each.key].arn
}
