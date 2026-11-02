variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "project" {
  type        = string
  description = "Prefixo dos recursos. Diferente de quantum-ai (modulo 03) para as duas APIs conviverem."
  default     = "qc-assistant"
}

variable "bundle_path" {
  type        = string
  description = "Zip gerado por release/build_bundle.py. O Terraform NAO empacota codigo: so implanta o pacote ja avaliado."
  default     = "../dist/bundle.zip"
}

variable "log_retention_days" {
  type        = number
  description = "Retencao dos logs. Curta de proposito: e laboratorio e log guardado custa."
  default     = 14
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = "quantum-commerce"
      ManagedBy = "terraform"
      Lab       = "04-genaiops"
    }
  }
}

# LabRole e a unica role utilizavel no Learner Lab: nao criamos role nova.
data "aws_caller_identity" "current" {}

locals {
  lab_role_arn = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/LabRole"
  # Pedidos ficticios: a mesma fonte do teste local carrega a tabela da nuvem.
  fixtures = jsondecode(file("${path.module}/../orders/fixtures.json"))
}
