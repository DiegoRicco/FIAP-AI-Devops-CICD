# Dois security groups e nenhuma porta interna aberta para a internet.
#   ALB  <- 0.0.0.0/0 na 80 (a unica porta publica do laboratorio)
#   task <- ALB na 8000 (interface e API) e runner na 8001 (admin/incidente)
# Neo4j (7474/7687) nao aparece em regra nenhuma: so existe dentro da task.

resource "aws_security_group" "alb" {
  name        = "${var.project}-alb-sg"
  description = "ALB publico do assistente: so HTTP 80"
  vpc_id      = data.aws_vpc.lab.id
  tags        = { Name = "${var.project}-alb-sg" }
}

resource "aws_vpc_security_group_ingress_rule" "alb_http" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTP publico (sem dominio no Learner Lab, entao sem certificado TLS)"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
}

resource "aws_vpc_security_group_egress_rule" "alb_to_task" {
  security_group_id            = aws_security_group.alb.id
  description                  = "ALB so encaminha para a porta do app nas tasks"
  referenced_security_group_id = aws_security_group.task.id
  ip_protocol                  = "tcp"
  from_port                    = 8000
  to_port                      = 8000
}

resource "aws_vpc_security_group_egress_rule" "alb_to_vpc" {
  security_group_id = aws_security_group.alb.id
  description       = "Outros alvos do ALB dentro da VPC (Grafana da Demo 5.2)"
  cidr_ipv4         = data.aws_vpc.lab.cidr_block
  ip_protocol       = "tcp"
  from_port         = 3000
  to_port           = 3000
}

resource "aws_security_group" "task" {
  name        = "${var.project}-task-sg"
  description = "Tasks do assistente: 8000 so do ALB, 8001 so do runner"
  vpc_id      = data.aws_vpc.lab.id
  tags        = { Name = "${var.project}-task-sg" }
}

resource "aws_vpc_security_group_ingress_rule" "task_from_alb" {
  security_group_id            = aws_security_group.task.id
  description                  = "Interface e API publica, so pelo ALB"
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = 8000
  to_port                      = 8000
}

resource "aws_vpc_security_group_ingress_rule" "task_admin_from_runner" {
  security_group_id            = aws_security_group.task.id
  description                  = "Porta admin (HMAC) so a partir do GitLab Runner, nunca pelo ALB"
  referenced_security_group_id = data.aws_security_group.runner.id
  ip_protocol                  = "tcp"
  from_port                    = 8001
  to_port                      = 8001
}

resource "aws_vpc_security_group_egress_rule" "task_out" {
  security_group_id = aws_security_group.task.id
  description       = "Saida para ECR, SSM, CloudWatch, API de pedidos e collector OTel (sem NAT, via IP publico)"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_lb" "this" {
  name               = "${var.project}-alb"
  load_balancer_type = "application"
  internal           = false
  security_groups    = [aws_security_group.alb.id]
  subnets            = local.subnet_ids

  # Cabecalho HTTP malformado e descartado no ALB, nao chega ao app.
  drop_invalid_header_fields = true
  idle_timeout               = 60
}

resource "aws_lb_target_group" "env" {
  for_each = local.environments

  name        = each.value.target_group
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = data.aws_vpc.lab.id
  target_type = "ip"

  # Troca de task mais rapida no deploy (o padrao de 300 s so atrasa a aula).
  deregistration_delay = 15

  # /ready so responde 200 com Neo4j conectado, indice vetorial ONLINE, modelo
  # e grafo carregados. Um app "vivo" mas sem grafo nao recebe cliente.
  health_check {
    path                = each.value.health_path
    matcher             = "200"
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"

  # Tudo que nao for /candidate vai para o live: e o que o cliente usa.
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.env["live"].arn
  }
}

# O ALB nao remove o prefixo: o candidate recebe /candidate/... e o app tira o
# prefixo sozinho (QC_PATH_PREFIX=/candidate).
resource "aws_lb_listener_rule" "candidate" {
  listener_arn = aws_lb_listener.http.arn
  priority     = 10

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.env["candidate"].arn
  }

  condition {
    path_pattern {
      values = ["/candidate", "/candidate/*"]
    }
  }
}
