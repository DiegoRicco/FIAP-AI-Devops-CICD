output "instance_id" {
  description = "EC2 de observabilidade (use com aws ssm start-session / send-command)."
  value       = aws_instance.obs.id
}

output "private_ip" {
  description = "IP privado da EC2 (o app manda OTLP para <ip>:4318)."
  value       = aws_instance.obs.private_ip
}

output "otlp_endpoint" {
  description = "Valor que ops/cloud/obs.sh grava no SSM /qc-assistant/obs/otlp-endpoint."
  value       = "http://${aws_instance.obs.private_ip}:4318"
}

output "security_group_id" {
  value = aws_security_group.obs.id
}

output "grafana_url" {
  description = "Grafana pelo ALB (login: admin; senha no SSM /qc-assistant/obs/grafana-admin)."
  value       = var.attach_alb ? "http://${data.aws_lb.runtime[0].dns_name}/grafana/" : "(ALB ainda nao ligado: attach_alb=false)"
}
