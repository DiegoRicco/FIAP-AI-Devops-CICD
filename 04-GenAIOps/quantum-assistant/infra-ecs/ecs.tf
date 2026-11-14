resource "aws_ecs_cluster" "this" {
  name = var.project

  # Container Insights cobra metrica por task. Desligado: a observabilidade da
  # aula e a stack open source (OTel + Prometheus + Grafana) da Demo 5.2.
  setting {
    name  = "containerInsights"
    value = "disabled"
  }
}

resource "aws_ecs_cluster_capacity_providers" "this" {
  cluster_name       = aws_ecs_cluster.this.name
  capacity_providers = ["FARGATE"]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
  }
}

resource "aws_cloudwatch_log_group" "ecs" {
  name              = "/${var.project}/ecs"
  retention_in_days = var.log_retention_days
}

locals {
  app_repo   = aws_ecr_repository.repo["app"].repository_url
  neo4j_repo = aws_ecr_repository.repo["neo4j"].repository_url
  # Sem digest informado, a task definition do Terraform e um TEMPLATE: o
  # deploy.sh copia tudo (env, secrets, Neo4j) e troca so a imagem do app.
  # O placeholder aponta para o espelho do Neo4j porque ele existe no ECR, mas
  # o service nasce com 0 tasks e nunca roda o template.
  app_image = {
    for env, _ in local.environments : env => (
      var.app_image_digest[env] != "" ? "${local.app_repo}@${var.app_image_digest[env]}" : "${local.neo4j_repo}@${local.neo4j.digest}"
    )
  }
}

# As definicoes dos conteineres ficam num local (e nao direto no jsonencode)
# para o terraform test conseguir conferir portas, env e secrets no plan, mesmo
# antes de o ECR existir (a URL do repositorio so e conhecida depois do apply).
locals {
  containers = {
    for env, cfg in local.environments : env => [
      {
        name      = "neo4j"
        image     = "${local.neo4j_repo}@${local.neo4j.digest}"
        essential = true
        memory    = var.neo4j_memory_limit
        # Disco efemero: o grafo e reconstruido a partir da imagem do app a cada
        # boot. Restart da task = grafo limpo, igual ao do release.
        # Sem portMappings: o Bolt (7687) e o browser (7474) so existem dentro
        # da task. O app fala com bolt://localhost:7687; de fora nao ha caminho.
        environment = [
          # Bolt e HTTP escutam so em localhost: mesmo que alguem abrisse o SG,
          # o processo nao aceitaria conexao de fora da task.
          { name = "NEO4J_server_default__listen__address", value = "127.0.0.1" },
          { name = "NEO4J_server_memory_heap_initial__size", value = "512m" },
          { name = "NEO4J_server_memory_heap_max__size", value = "512m" },
          { name = "NEO4J_server_memory_pagecache_size", value = "256m" },
          # Sem relatorio de uso para a Neo4j: a task nao manda dado para fora.
          { name = "NEO4J_dbms_usage__report_enabled", value = "false" },
        ]
        # NEO4J_AUTH = "neo4j/<senha>", montado pelo SSM (o valor nao passa pelo state).
        secrets = [
          { name = "NEO4J_AUTH", valueFrom = "${local.ssm_arn_prefix}/${var.project}/${env}/neo4j-auth" },
        ]
        healthCheck = {
          # wget existe na imagem oficial; a porta 7474 so responde em localhost.
          command = ["CMD-SHELL", "wget -q -O /dev/null http://127.0.0.1:7474 || exit 1"]
          # O ECS aceita no maximo 10 retries; o startPeriod cobre o boot do
          # Neo4j (falhas nesse periodo nao contam).
          interval    = 10
          timeout     = 5
          retries     = 10
          startPeriod = 60
        }
        logConfiguration = {
          logDriver = "awslogs"
          options = {
            awslogs-group         = aws_cloudwatch_log_group.ecs.name
            awslogs-region        = var.aws_region
            awslogs-stream-prefix = "${env}-neo4j"
          }
        }
      },
      {
        name              = "app"
        image             = local.app_image[env]
        essential         = true
        memoryReservation = var.task_memory - var.neo4j_memory_limit
        portMappings = [
          { containerPort = 8000, protocol = "tcp", name = "http" },
          { containerPort = 8001, protocol = "tcp", name = "admin" },
        ]
        # O app so sobe com o Neo4j saudavel: o bootstrap do grafo precisa dele.
        dependsOn = [{ containerName = "neo4j", condition = "HEALTHY" }]
        environment = [
          { name = "QC_ENVIRONMENT", value = env },
          { name = "QC_PATH_PREFIX", value = cfg.path_prefix },
          { name = "QC_NEO4J_URI", value = "bolt://localhost:7687" },
          { name = "QC_NEO4J_USER", value = "neo4j" },
          { name = "QC_DEFAULT_STRATEGY", value = "graph" },
          # Preenchidos de verdade pelo deploy.sh (release e digest do momento).
          { name = "QC_RELEASE_ID", value = "template" },
          { name = "QC_IMAGE_DIGEST", value = var.app_image_digest[env] != "" ? var.app_image_digest[env] : "template" },
          # Release = imagem (codigo + modelo) + pacote de dados (corpus + grafo).
          # Com os dois vazios, o app usa os dados que vieram dentro da imagem.
          { name = "QC_DATA_BUNDLE_URI", value = var.data_bundle[env].uri },
          { name = "QC_DATA_BUNDLE_SHA256", value = var.data_bundle[env].sha256 },
        ]
        secrets = [
          { name = "NEO4J_PASSWORD", valueFrom = "${local.ssm_arn_prefix}/${var.project}/${env}/neo4j-password" },
          { name = "QC_ADMIN_HMAC_KEY", valueFrom = "${local.ssm_arn_prefix}/${var.project}/${env}/admin-hmac" },
          { name = "QC_ORDERS_BASE_URL", valueFrom = "${local.ssm_arn_prefix}/${var.project}/orders-base-url" },
          { name = "OTEL_EXPORTER_OTLP_ENDPOINT", valueFrom = aws_ssm_parameter.otlp_endpoint.arn },
        ]
        logConfiguration = {
          logDriver = "awslogs"
          options = {
            awslogs-group         = aws_cloudwatch_log_group.ecs.name
            awslogs-region        = var.aws_region
            awslogs-stream-prefix = "${env}-app"
          }
        }
      },
    ]
  }
}

resource "aws_ecs_task_definition" "env" {
  for_each = local.environments

  family                   = "${var.project}-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = data.aws_iam_role.lab.arn
  task_role_arn            = data.aws_iam_role.lab.arn

  # O runner que builda e x86_64, entao a imagem e amd64.
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode(local.containers[each.key])
}

resource "aws_ecs_service" "env" {
  for_each = local.environments

  name             = "${var.project}-${each.key}"
  cluster          = aws_ecs_cluster.this.id
  task_definition  = aws_ecs_task_definition.env[each.key].arn
  desired_count    = var.desired_count[each.key]
  launch_type      = "FARGATE"
  platform_version = "1.4.0"

  # Uma task por ambiente, sem pretensao de alta disponibilidade: durante o
  # deploy a task velha so sai quando a nova estiver saudavel (100% minimo).
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  # O Neo4j sobe e o app carrega o modelo e o grafo antes do /ready: sem
  # carencia, o ALB mataria a task no meio do boot.
  health_check_grace_period_seconds = 240

  # Se a task nova nunca ficar saudavel, o ECS desiste e volta para a anterior.
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets         = local.subnet_ids
    security_groups = [aws_security_group.task.id]
    # Subnet publica sem NAT: o IP publico e so de SAIDA (puxar imagem do ECR,
    # ler SSM, mandar log). A entrada continua fechada pelo SG.
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.env[each.key].arn
    container_name   = "app"
    container_port   = 8000
  }

  wait_for_steady_state = var.wait_for_steady_state

  # Quem escolhe a revisao em execucao e quantas tasks rodam e o deploy.sh
  # (e o promote/rollback do pipeline). Sem isto, qualquer apply "voltaria" o
  # live para o template, como o ignore_changes do alias live da Lambda.
  lifecycle {
    ignore_changes = [task_definition, desired_count]
  }

  depends_on = [aws_lb_listener_rule.candidate]
}
