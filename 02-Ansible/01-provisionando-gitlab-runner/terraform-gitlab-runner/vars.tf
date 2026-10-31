variable "aws_region" {
  default = "us-east-1"
}

# Tag Name da VPC compartilhada criada na demo 01-Terraform/02-Modules.
variable "project" {
  default = "fiap-lab"
}

variable "instance_type" {
  default = "t3.small"
}
