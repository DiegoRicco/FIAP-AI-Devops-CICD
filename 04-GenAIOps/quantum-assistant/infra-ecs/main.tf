variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "project" {
  type        = string
  description = "Prefixo dos recursos. O mesmo da API de pedidos (qc-assistant): tudo da Aula 5 aparece junto no console."
  default     = "qc-assistant"
}

variable "vpc_name" {
  type        = string
  description = "Tag Name da rede compartilhada criada no Modulo 01 (Demo 02). Este Terraform so LE a rede."
  default     = "fiap-lab"
}

variable "runner_sg_name" {
  type        = string
  description = "Nome do security group da EC2 do GitLab Runner. So ele pode falar com a porta admin (8001) da task."
  default     = "quantum-ai-gitlab-runner"
}

variable "excluded_az_ids" {
  type        = list(string)
  description = "Zonas fisicas sem suporte a Fargate em us-east-1 (use1-az3). O nome (us-east-1e) muda de conta para conta; o ID da zona nao."
  default     = ["use1-az3"]
}

variable "task_cpu" {
  type        = number
  description = "CPU da task em unidades (1024 = 1 vCPU). Combinacoes validas no Fargate: 1024 aceita 2048 a 8192 MB."
  default     = 1024
}

variable "task_memory" {
  type        = number
  description = "Memoria da task em MB. Neo4j (heap 512 MB + page cache 256 MB) e o modelo de embedding dividem a mesma task."
  default     = 3072
}

variable "neo4j_memory_limit" {
  type        = number
  description = "Limite rigido de memoria do conteiner neo4j (MB). Se passar disso, o Neo4j e reiniciado, nao a task inteira por falta de memoria."
  default     = 1536
}

variable "desired_count" {
  type        = map(number)
  description = "Tasks por ambiente na CRIACAO do service. Comeca em 0 porque ainda nao ha imagem; quem liga e o scripts/cloud/deploy.sh."
  default     = { live = 0, candidate = 0 }
}

variable "app_image_digest" {
  type        = map(string)
  description = "Digest (sha256:...) da imagem do app por ambiente, para quem quiser implantar so com Terraform. Vazio = o deploy.sh define a imagem."
  default     = { live = "", candidate = "" }

  validation {
    condition     = alltrue([for d in values(var.app_image_digest) : d == "" || can(regex("^sha256:[0-9a-f]{64}$", d))])
    error_message = "app_image_digest aceita so digest completo (sha256: + 64 hex). Tag mutavel nao e identidade de release."
  }
}

variable "data_bundle" {
  type = map(object({
    uri    = string
    sha256 = string
  }))
  description = "Pacote de dados (corpus + seed do grafo) por ambiente, gerado pelo pipeline no S3. Vazio = o app usa os dados embutidos na imagem."
  default = {
    live      = { uri = "", sha256 = "" }
    candidate = { uri = "", sha256 = "" }
  }

  validation {
    condition = alltrue([for b in values(var.data_bundle) :
    (b.uri == "" && b.sha256 == "") || (startswith(b.uri, "s3://") && can(regex("^[0-9a-f]{64}$", b.sha256)))])
    error_message = "data_bundle: uri s3://... e sha256 (64 hex) juntos, ou os dois vazios. Pacote sem hash nao e release."
  }
}

variable "log_retention_days" {
  type        = number
  description = "Retencao dos logs das tasks. Curta de proposito: e laboratorio e log guardado custa."
  default     = 3
}

variable "wait_for_steady_state" {
  type        = bool
  description = "Faz o apply esperar o service estabilizar. Desligado porque quem espera (e mede) e o deploy.sh."
  default     = false
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = "quantum-commerce"
      Lab       = "05-1"
      ManagedBy = "terraform"
      Owner     = "lab"
    }
  }
}

data "aws_caller_identity" "current" {}

# LabRole e a unica role utilizavel no Learner Lab (nao criamos role). Ela
# confia em ecs-tasks.amazonaws.com, entao serve de execution role (puxar a
# imagem do ECR, ler o SSM, escrever log) e de task role.
data "aws_iam_role" "lab" {
  name = "LabRole"
}

data "aws_vpc" "lab" {
  filter {
    name   = "tag:Name"
    values = [var.vpc_name]
  }
}

data "aws_subnets" "public" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.lab.id]
  }
  filter {
    name   = "tag:Tier"
    values = ["Public"]
  }
}

data "aws_subnet" "public" {
  for_each = toset(data.aws_subnets.public.ids)
  id       = each.value
}

data "aws_security_group" "runner" {
  vpc_id = data.aws_vpc.lab.id
  filter {
    name   = "group-name"
    values = [var.runner_sg_name]
  }
}

locals {
  account_id = data.aws_caller_identity.current.account_id
  # Subnets publicas fora das zonas sem Fargate, em ordem estavel (o plan nao
  # muda so porque a API devolveu outra ordem).
  subnet_ids = sort([for s in data.aws_subnet.public : s.id if !contains(var.excluded_az_ids, s.availability_zone_id)])

  neo4j = jsondecode(file("${path.module}/neo4j-image.json"))

  # Um ambiente = um service + uma task com app e Neo4j proprios. O candidate
  # nunca le nem escreve o grafo do live.
  environments = {
    live = {
      path_prefix  = ""
      health_path  = "/ready"
      target_group = "${var.project}-live-tg"
    }
    candidate = {
      path_prefix  = "/candidate"
      health_path  = "/candidate/ready"
      target_group = "${var.project}-cand-tg"
    }
  }

  ssm_arn_prefix = "arn:aws:ssm:${var.aws_region}:${local.account_id}:parameter"
}
