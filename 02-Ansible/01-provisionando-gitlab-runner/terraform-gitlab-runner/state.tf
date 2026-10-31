terraform {
  backend "s3" {
    # Troque pelo bucket criado no setup inicial (Modulo 01).
    # Nome de bucket S3 nao pode ter espacos nem maiusculas.
    bucket = "base-config-SEU-RM"
    # A key e mantida porque o Trabalho Final (provisionamento/subir-runner.sh)
    # reaproveita esta pasta e precisa encontrar o mesmo state do runner.
    key    = "gitlab-runner-fleet"
    region = "us-east-1"
  }
}
