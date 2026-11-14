# terraform test: roda um plan e confere as regras de seguranca e de release
# de que o deploy.sh e o pipeline dependem. Nao cria nada na AWS, mas precisa
# de credencial valida (le a rede fiap-lab, a LabRole e o SG do runner).

run "contrato_do_runtime" {
  command = plan

  assert {
    condition     = alltrue([for r in aws_ecr_repository.repo : r.image_tag_mutability == "IMMUTABLE"])
    error_message = "Repositorios ECR com tag IMMUTABLE: uma tag nunca pode passar a apontar para outra imagem."
  }

  assert {
    condition     = alltrue([for r in aws_ecr_repository.repo : r.image_scanning_configuration[0].scan_on_push])
    error_message = "scan_on_push ligado: toda imagem nova e escaneada no push."
  }

  assert {
    condition     = alltrue([for t in aws_ecs_task_definition.env : t.execution_role_arn == data.aws_iam_role.lab.arn && t.task_role_arn == data.aws_iam_role.lab.arn])
    error_message = "Learner Lab: execution role e task role sao a LabRole (nao criamos role)."
  }

  assert {
    condition     = alltrue([for t in aws_ecs_task_definition.env : t.runtime_platform[0].cpu_architecture == "X86_64"])
    error_message = "A imagem e buildada no runner amd64: a task precisa ser X86_64."
  }

  assert {
    condition     = alltrue([for s in aws_ecs_service.env : s.deployment_circuit_breaker[0].enable && s.deployment_circuit_breaker[0].rollback])
    error_message = "Circuit breaker com rollback: task que nunca fica saudavel volta para a anterior sozinha."
  }

  assert {
    condition     = aws_ecs_service.env["live"].name == "qc-assistant-live" && aws_ecs_service.env["candidate"].name == "qc-assistant-candidate"
    error_message = "Nomes dos services sao contrato do deploy.sh, do promote e do rollback."
  }

  assert {
    condition     = aws_lb_target_group.env["live"].health_check[0].path == "/ready" && aws_lb_target_group.env["candidate"].health_check[0].path == "/candidate/ready"
    error_message = "O ALB so manda cliente para task com /ready 200 (Neo4j, indice, modelo e grafo prontos)."
  }

  assert {
    condition     = anytrue([for c in aws_lb_listener_rule.candidate.condition : contains(try(tolist(c.path_pattern[0].values), []), "/candidate/*")])
    error_message = "A regra /candidate/* leva ao target group do candidate."
  }

  # Nenhuma regra de entrada da task aceita a internet: 8000 so do ALB e 8001 so do runner.
  assert {
    condition     = aws_vpc_security_group_ingress_rule.task_from_alb.cidr_ipv4 == null && aws_vpc_security_group_ingress_rule.task_admin_from_runner.cidr_ipv4 == null
    error_message = "A task nao pode ter porta aberta para 0.0.0.0/0."
  }

  assert {
    condition     = aws_vpc_security_group_ingress_rule.task_admin_from_runner.from_port == 8001 && aws_vpc_security_group_ingress_rule.task_admin_from_runner.referenced_security_group_id == data.aws_security_group.runner.id
    error_message = "A porta admin (8001) so aceita o SG do GitLab Runner."
  }

  assert {
    condition     = aws_vpc_security_group_ingress_rule.alb_http.from_port == 80 && aws_vpc_security_group_ingress_rule.alb_http.to_port == 80
    error_message = "O ALB so expoe a porta 80."
  }

  # Neo4j sem portMappings: Bolt (7687) e browser (7474) nunca saem da task.
  assert {
    condition = alltrue([for env, cs in local.containers :
    length(try([for c in cs : c if c.name == "neo4j"][0].portMappings, [])) == 0])
    error_message = "O conteiner neo4j nao pode mapear porta: o banco so e acessivel dentro da task."
  }

  # Segredos so por referencia ao SSM (valueFrom), nunca em texto no environment.
  assert {
    condition = alltrue([for env, cs in local.containers : alltrue([for c in cs :
    length([for e in try(c.environment, []) : e if contains(["NEO4J_AUTH", "NEO4J_PASSWORD", "QC_ADMIN_HMAC_KEY"], e.name)]) == 0])])
    error_message = "Senha e chave HMAC entram pela lista secrets (SSM), nunca por environment."
  }

  # A imagem do Neo4j trata TODA variavel NEO4J_* como configuracao e recusa as
  # desconhecidas: NEO4J_PASSWORD no conteiner neo4j derruba o banco no boot.
  # O neo4j recebe so NEO4J_AUTH; a senha avulsa vai so para o app.
  assert {
    condition = alltrue([for env, cs in local.containers :
      !contains([for x in concat(try([for c in cs : c if c.name == "neo4j"][0].secrets, []), try([for c in cs : c if c.name == "neo4j"][0].environment, [])) : x.name], "NEO4J_PASSWORD")
    ])
    error_message = "NEO4J_PASSWORD nunca vai para o conteiner neo4j (ele so aceita NEO4J_AUTH)."
  }

  assert {
    condition     = aws_cloudwatch_log_group.ecs.retention_in_days <= 7
    error_message = "Log de laboratorio com retencao curta (custo)."
  }

  assert {
    condition     = !contains(local.subnet_ids, "") && length(local.subnet_ids) >= 2
    error_message = "O ALB precisa de pelo menos 2 subnets publicas com Fargate."
  }
}

run "digest_invalido_e_recusado" {
  command = plan

  variables {
    app_image_digest = { live = "latest", candidate = "" }
  }

  expect_failures = [var.app_image_digest]
}
