output "active_mode" {
  description = "Modo de execucao homologado neste laboratorio."
  value       = "ecs_fargate"
}

output "app_url" {
  description = "Endereco do assistente LIVE (o que o cliente usa). Abra no navegador."
  value       = "http://${aws_lb.this.dns_name}"
}

output "candidate_url" {
  description = "Endereco da versao CANDIDATA, avaliada antes da promocao."
  value       = "http://${aws_lb.this.dns_name}/candidate"
}

output "alb_dns_name" {
  description = "DNS do ALB."
  value       = aws_lb.this.dns_name
}

output "alb_listener_arn" {
  description = "Listener HTTP:80. A Demo 5.2 pendura a regra /grafana nele."
  value       = aws_lb_listener.http.arn
}

output "alb_sg_id" {
  description = "Security group do ALB (a 5.2 libera o Grafana a partir dele)."
  value       = aws_security_group.alb.id
}

output "task_sg_id" {
  description = "Security group das tasks (8000 do ALB, 8001 do runner)."
  value       = aws_security_group.task.id
}

output "cluster" {
  description = "Cluster ECS."
  value       = aws_ecs_cluster.this.name
}

output "services" {
  description = "Services por ambiente."
  value       = { for k, s in aws_ecs_service.env : k => s.name }
}

output "target_groups" {
  description = "Target groups por ambiente."
  value       = { for k, t in aws_lb_target_group.env : k => t.name }
}

output "taskdef_template_arns" {
  description = "Revisao template de cada ambiente (o deploy.sh copia e troca so a imagem)."
  value       = { for k, t in aws_ecs_task_definition.env : k => t.arn }
}

output "ecr_app_repo_url" {
  description = "Repositorio ECR da imagem do app (o release)."
  value       = aws_ecr_repository.repo["app"].repository_url
}

output "ecr_neo4j_repo_url" {
  description = "Repositorio ECR do espelho do Neo4j."
  value       = aws_ecr_repository.repo["neo4j"].repository_url
}

output "neo4j_image" {
  description = "Tag e digest do Neo4j usados pela task."
  value       = "${local.neo4j.tag} (${local.neo4j.digest})"
}

output "subnet_ids" {
  description = "Subnets publicas usadas (sem use1-az3, que nao tem Fargate)."
  value       = local.subnet_ids
}

output "state_bucket_key" {
  description = "Onde fica este state: so este prefixo e destruido pelo cleanup."
  value       = "quantum-assistant/ecs/terraform.tfstate"
}

output "cost_warning" {
  description = "Lembrete de custo."
  value       = "Cobra por hora: ALB (~US$ 0,0225/h) + cada task ligada (1 vCPU + 3 GB ~US$ 0,053/h). Ao terminar: make down (desliga tasks) ou make destroy-dry-run."
}
