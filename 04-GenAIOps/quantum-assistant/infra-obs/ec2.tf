# EC2 da stack de observabilidade (OTel Collector, Jaeger, Prometheus, Loki,
# Grafana e o probe de qualidade), todos em Docker Compose.
# Por que EC2 e nao ECS: Grafana + Loki + Prometheus + Jaeger precisam de disco
# local e de uma rede interna entre eles; numa EC2 com compose o aluno reconhece
# o mesmo ops/docker-compose.yml que usou no ensaio local.
resource "aws_instance" "obs" {
  ami                         = data.aws_ami.ubuntu.id
  instance_type               = var.instance_type
  subnet_id                   = local.subnet_id
  vpc_security_group_ids      = [aws_security_group.obs.id]
  associate_public_ip_address = true
  # LabInstanceProfile: a unica permitida no Learner Lab; da SSM (sem SSH) e S3.
  iam_instance_profile = "LabInstanceProfile"
  monitoring           = true
  ebs_optimized        = true

  # IMDSv2 obrigatorio e hop limit 1: um conteiner do compose nao alcanca as
  # credenciais da instancia. Quem le o SSM e o bootstrap no host.
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.root_volume_gb
    encrypted             = true
    delete_on_termination = true
  }

  # O user_data so instala a AWS CLI e chama o bootstrap que esta no S3.
  # Mudar a configuracao do compose NAO recria a EC2: ops/cloud/obs.sh deploy
  # reenvia os arquivos e roda o bootstrap de novo via SSM.
  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    config_bucket = var.config_bucket
    region        = var.aws_region
  })
  user_data_replace_on_change = false

  tags = { Name = local.name }

  lifecycle {
    # Uma AMI nova da Canonical nao deve derrubar a observabilidade no meio da aula.
    ignore_changes = [ami]
  }
}

# O endpoint OTLP (/qc-assistant/obs/otlp-endpoint) NAO e recurso deste state:
# o parametro pertence ao infra-ecs/ (nasce "disabled" com ignore_changes) e o
# ops/cloud/obs.sh grava o IP privado desta EC2 com put-parameter --overwrite.
# Dois states donos do mesmo parametro brigariam a cada apply.
