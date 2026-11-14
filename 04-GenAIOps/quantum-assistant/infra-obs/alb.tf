# Grafana atras do ALB do runtime, no caminho /grafana (o Grafana roda com
# GF_SERVER_SERVE_FROM_SUB_PATH, entao o ALB nao precisa reescrever o caminho).
# Prioridade baixa (5): avaliada antes de /candidate, que pertence ao INFRA.
resource "aws_lb_target_group" "grafana" {
  count       = var.attach_alb ? 1 : 0
  name        = "${var.project}-grafana-tg"
  port        = 3000
  protocol    = "HTTP"
  target_type = "instance"
  vpc_id      = data.aws_vpc.lab.id

  health_check {
    # /grafana/api/health responde 200 sem login e nao expoe dado algum.
    path                = "/grafana/api/health"
    matcher             = "200"
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

resource "aws_lb_target_group_attachment" "grafana" {
  count            = var.attach_alb ? 1 : 0
  target_group_arn = aws_lb_target_group.grafana[0].arn
  target_id        = aws_instance.obs.id
  port             = 3000
}

resource "aws_lb_listener_rule" "grafana" {
  count        = var.attach_alb ? 1 : 0
  listener_arn = data.aws_lb_listener.http[0].arn
  priority     = var.grafana_listener_priority

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.grafana[0].arn
  }

  condition {
    path_pattern {
      values = ["/grafana", "/grafana/*"]
    }
  }
}
