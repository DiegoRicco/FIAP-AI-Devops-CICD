terraform {
  # Mesmas versoes do infra/ (API de pedidos): 1.10 e a primeira com
  # use_lockfile no backend S3, e o provider AWS 6.x e o do resto do modulo.
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}
