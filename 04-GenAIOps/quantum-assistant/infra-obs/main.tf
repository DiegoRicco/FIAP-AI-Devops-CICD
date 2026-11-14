variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "project" {
  type        = string
  description = "Prefixo dos recursos, o mesmo do runtime ECS (qc-assistant-*)."
  default     = "qc-assistant"
}

variable "config_bucket" {
  type        = string
  description = "Bucket base-config-<SEU RM>. A EC2 baixa a configuracao do compose de s3://<bucket>/quantum-assistant/obs-config/."
}

variable "instance_type" {
  type = string
  # t3.medium (4 GiB). Medido na EC2 em 2026-11-14 com o probe rodando e os 3
  # dashboards abertos: ~1,2 GiB usados pelos conteineres (Grafana ~740 MiB,
  # Loki ~100, Collector ~70, Prometheus ~50, Jaeger ~35, probe ~30) e ~2,3 GiB
  # disponiveis. Os limites do compose somam ~2,4 GiB. Um t3.small (2 GiB) nao
  # caberia: so a Grafana ja passa de 700 MiB com os paineis abertos.
  default = "t3.medium"
}

variable "root_volume_gb" {
  type        = number
  description = "Disco gp3. As imagens somam ~2 GB e os dados (retencao de 6 h) ficam pequenos."
  default     = 16
}

variable "attach_alb" {
  type        = bool
  description = "Liga o Grafana ao ALB do runtime (/grafana). false enquanto o ALB qc-assistant-alb ainda nao existe."
  default     = true
}

variable "alb_name" {
  type    = string
  default = "qc-assistant-alb"
}

variable "task_sg_name" {
  type        = string
  description = "SG das tasks ECS. Unica origem, alem do runner, que pode mandar telemetria (OTLP 4318)."
  default     = "qc-assistant-task-sg"
}

variable "runner_sg_name" {
  type        = string
  description = "SG do GitLab Runner da Aula 3 (procurado pelo nome, para valer em qualquer conta)."
  default     = "quantum-ai-gitlab-runner"
}

variable "grafana_listener_priority" {
  type        = number
  description = "Prioridade da regra /grafana no listener :80. Numero baixo = avaliada antes do /candidate."
  default     = 5
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = "quantum-commerce"
      Lab       = "05-2"
      ManagedBy = "terraform"
      Owner     = "lab"
    }
  }
}

# A rede e a da Demo 02 do modulo 01 (fiap-lab): so LEMOS, nunca alteramos.
data "aws_vpc" "lab" {
  tags = { Name = "fiap-lab" }
}

# Nem toda AZ oferece todo tipo de instancia (us-east-1e nao tem t3.medium):
# so entram as subnets publicas de AZs que oferecem o tipo escolhido.
data "aws_ec2_instance_type_offerings" "obs" {
  location_type = "availability-zone"
  filter {
    name   = "instance-type"
    values = [var.instance_type]
  }
}

data "aws_subnets" "public" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.lab.id]
  }
  filter {
    name   = "availability-zone"
    values = data.aws_ec2_instance_type_offerings.obs.locations
  }
  tags = { Tier = "Public" }
}

# Ubuntu 22.04 (o mesmo SO do runner): o SSM agent ja vem instalado.
data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"]
  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }
  filter {
    name   = "architecture"
    values = ["x86_64"]
  }
}

# Plural de proposito: devolve lista vazia (e nao erro) quando o runtime ECS
# ou o runner ainda nao existem. A regra de entrada aparece no apply seguinte.
data "aws_security_groups" "task" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.lab.id]
  }
  filter {
    name   = "group-name"
    values = [var.task_sg_name]
  }
}

data "aws_security_groups" "runner" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.lab.id]
  }
  filter {
    name   = "group-name"
    values = [var.runner_sg_name]
  }
}

data "aws_lb" "runtime" {
  count = var.attach_alb ? 1 : 0
  name  = var.alb_name
}

data "aws_lb_listener" "http" {
  count             = var.attach_alb ? 1 : 0
  load_balancer_arn = data.aws_lb.runtime[0].arn
  port              = 80
}

locals {
  name = "${var.project}-obs"
  # Uma subnet fixa (a primeira em ordem alfabetica do id): o plan nao muda
  # de subnet entre execucoes e a EC2 nao e recriada a toa.
  subnet_id = sort(data.aws_subnets.public.ids)[0]
}
