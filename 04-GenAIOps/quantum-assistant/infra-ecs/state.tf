terraform {
  backend "s3" {
    # Troque pelo seu bucket (o mesmo do setup do Modulo 01). Os scripts de
    # scripts/cloud/ passam -backend-config=bucket=..., entao nao e preciso editar.
    bucket = "base-config-SEU-RM"
    # Key PROPRIA do runtime ECS: o state da API de pedidos
    # (quantum-assistant/terraform.tfstate) e o da observabilidade
    # (quantum-assistant/obs/terraform.tfstate) ficam intocados.
    key          = "quantum-assistant/ecs/terraform.tfstate"
    region       = "us-east-1"
    use_lockfile = true
  }
}
