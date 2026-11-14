# SG da EC2 de observabilidade. Nenhuma regra de entrada vem da internet:
#   3000 (Grafana)   so do SG do ALB  -> o aluno entra por http://<alb>/grafana, com login
#   4318 (OTLP HTTP) so do SG das tasks ECS e do runner -> telemetria do app e dos jobs
#   16686/9090/3100  (Jaeger/Prometheus/Loki) NAO tem regra: so o Grafana (na mesma
#                    rede do Docker) os consulta; o aluno usa SSM port-forward.
# A saida e livre porque a EC2 esta em subnet publica SEM NAT: o IP publico serve
# so para sair (docker pull, S3, SSM, ALB do probe). Sem SSH e sem key pair.
resource "aws_security_group" "obs" {
  name        = "${local.name}-sg"
  description = "Observabilidade qc-assistant: Grafana so do ALB, OTLP so das tasks e do runner"
  vpc_id      = data.aws_vpc.lab.id
  tags        = { Name = "${local.name}-sg" }
}

resource "aws_vpc_security_group_egress_rule" "obs_all" {
  security_group_id = aws_security_group.obs.id
  description       = "Saida para docker pull, S3, SSM e o ALB que o probe testa"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_ingress_rule" "grafana_from_alb" {
  for_each                     = var.attach_alb ? toset(data.aws_lb.runtime[0].security_groups) : toset([])
  security_group_id            = aws_security_group.obs.id
  description                  = "Grafana (3000) so a partir do ALB"
  ip_protocol                  = "tcp"
  from_port                    = 3000
  to_port                      = 3000
  referenced_security_group_id = each.value
}

resource "aws_vpc_security_group_ingress_rule" "otlp_from_sources" {
  for_each                     = toset(concat(data.aws_security_groups.task.ids, data.aws_security_groups.runner.ids))
  security_group_id            = aws_security_group.obs.id
  description                  = "OTLP HTTP (4318) das tasks ECS e do runner"
  ip_protocol                  = "tcp"
  from_port                    = 4318
  to_port                      = 4318
  referenced_security_group_id = each.value
}
