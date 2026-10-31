provider "aws" {
  region = var.aws_region

  # Tags de governanca aplicadas a todos os recursos deste provider, no mesmo
  # padrao das demos do Modulo 01.
  default_tags {
    tags = {
      Project   = "quantum-commerce"
      ManagedBy = "terraform"
      Lab       = "02-ansible"
    }
  }
}
