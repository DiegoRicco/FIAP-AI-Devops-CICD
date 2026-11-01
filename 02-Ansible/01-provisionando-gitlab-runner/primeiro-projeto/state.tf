terraform {
  backend "s3" {
    # Troque pelo bucket criado no setup inicial (Modulo 01).
    # Nome de bucket S3 nao pode ter espacos nem maiusculas.
    bucket = "base-config-SEU-RM"
    # Uma key por projeto: o state do assistente nao se mistura com o de outras demos.
    key    = "primeiro-projeto/terraform.tfstate"
    region = "us-east-1"
    # Trava o state com um arquivo .tflock no proprio bucket: dois pipelines ao
    # mesmo tempo nao conseguem aplicar mudancas por cima um do outro.
    use_lockfile = true
  }
}
