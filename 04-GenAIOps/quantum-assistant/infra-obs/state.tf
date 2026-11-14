terraform {
  backend "s3" {
    # O bucket e passado no init (ops/cloud/obs.sh faz isso):
    #   terraform -chdir=infra-obs init -backend-config="bucket=base-config-<SEU RM>"
    bucket = "base-config-SEU-RM"
    # Key PROPRIA da observabilidade: destruir esta stack nunca toca no state da
    # API de pedidos (quantum-assistant/terraform.tfstate) nem no runtime ECS.
    key          = "quantum-assistant/obs/terraform.tfstate"
    region       = "us-east-1"
    use_lockfile = true
  }
}
