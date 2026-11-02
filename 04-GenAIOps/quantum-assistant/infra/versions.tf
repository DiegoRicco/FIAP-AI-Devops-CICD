terraform {
  # 1.10 e a primeira versao com use_lockfile no backend S3 (trava sem DynamoDB).
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}
