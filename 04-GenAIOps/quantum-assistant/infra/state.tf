terraform {
  backend "s3" {
    # Troque pelo bucket criado no setup inicial (Modulo 01).
    bucket = "base-config-SEU-RM"
    # Key propria do assistente: o state da API do modulo 03
    # (primeiro-projeto/terraform.tfstate) continua intocado.
    key    = "quantum-assistant/terraform.tfstate"
    region = "us-east-1"
    # Arquivo .tflock no bucket: dois pipelines nao aplicam ao mesmo tempo.
    use_lockfile = true
  }
}
