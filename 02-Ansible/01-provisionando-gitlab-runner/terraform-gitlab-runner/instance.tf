# Busca dinamica da AMI Ubuntu 22.04 mais recente publicada pela Canonical.
# Evita AMI hardcoded, que expira e quebra o lab entre regioes/turmas.
data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

# Nem toda AZ oferta todos os tipos de instancia (em us-east-1, a us-east-1e nao
# oferta t3.small). Usamos so as subnets das AZs que ofertam o tipo escolhido e
# pegamos a primeira em ordem, para todo aluno obter o mesmo resultado.
data "aws_ec2_instance_type_offerings" "supported" {
  filter {
    name   = "instance-type"
    values = [var.instance_type]
  }
  location_type = "availability-zone"
}

locals {
  eligible_subnet_ids = sort([
    for s in data.aws_subnet.public : s.id
    if contains(data.aws_ec2_instance_type_offerings.supported.locations, s.availability_zone)
  ])
}

resource "aws_instance" "runner" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.instance_type
  iam_instance_profile   = "LabInstanceProfile"
  vpc_security_group_ids = [aws_security_group.runner.id]
  subnet_id              = local.eligible_subnet_ids[0]

  # Sem key_name: nao ha SSH. Tanto o bootstrap (abaixo) quanto o Ansible acessam
  # esta maquina via AWS Systems Manager (SSM), autorizados pelo LabInstanceProfile.

  # Disco de 30GB: o runner roda Terraform (cada provider AWS ~600MB), Checkov,
  # TFLint e varios pipelines do modulo 03. O root volume padrao da AMI (8GB)
  # enche rapido e quebra o 'terraform init' com "no space left on device".
  root_block_device {
    volume_size = 30
    volume_type = "gp3"
  }

  tags = {
    Name = "quantum-ai-gitlab-runner"
  }
}

# Bootstrap via SSM: prepara a maquina (Python, pip, awscli) para o Ansible poder
# operar nela. Envia o install-python.sh, espera terminar e ABORTA o apply se
# falhar. Sem isso, o Ansible nao conseguiria executar os modulos Python no host.
# Exige aws CLI + jq (ver pre-requisitos do lab).
resource "terraform_data" "bootstrap" {
  triggers_replace = {
    instance_id = aws_instance.runner.id
    script_hash = filesha256("${path.module}/install-python.sh")
  }

  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    command     = <<-EOT
      set -euo pipefail
      ID="${aws_instance.runner.id}"
      REGION="${var.aws_region}"

      echo "Aguardando a instancia $ID ficar online no SSM..."
      for i in $(seq 1 30); do
        PING=$(aws ssm describe-instance-information \
          --filters "Key=InstanceIds,Values=$ID" --region "$REGION" \
          --query 'InstanceInformationList[0].PingStatus' --output text 2>/dev/null || true)
        [ "$PING" = "Online" ] && break
        sleep 10
      done
      if [ "$PING" != "Online" ]; then
        echo "ERRO: a instancia $ID nao ficou online no SSM a tempo." >&2
        exit 1
      fi

      echo "Executando o bootstrap (install-python) via SSM em $ID..."
      COMMANDS_JSON=$(jq -R . "${path.module}/install-python.sh" | jq -s .)
      CMD=$(aws ssm send-command \
        --instance-ids "$ID" \
        --document-name "AWS-RunShellScript" \
        --comment "Bootstrap do GitLab Runner da Quantum Commerce (02-Ansible)" \
        --parameters "commands=$COMMANDS_JSON" \
        --region "$REGION" \
        --query 'Command.CommandId' --output text)

      # O bootstrap e demorado (apt update + instalacoes). O waiter padrao
      # do SSM expira em ~100s, entao fazemos polling proprio ate sair de Pending/
      # InProgress (ate ~10 min).
      STATUS="Pending"
      for i in $(seq 1 60); do
        STATUS=$(aws ssm get-command-invocation --command-id "$CMD" --instance-id "$ID" --region "$REGION" --query 'Status' --output text 2>/dev/null || echo "Pending")
        case "$STATUS" in
          Pending|InProgress|Delayed) sleep 10 ;;
          *) break ;;
        esac
      done

      echo "----- log do bootstrap -----"
      aws ssm get-command-invocation --command-id "$CMD" --instance-id "$ID" \
        --region "$REGION" --query 'StandardOutputContent' --output text | tail -20
      echo "----------------------------"
      if [ "$STATUS" != "Success" ]; then
        echo "ERRO: o bootstrap via SSM falhou (Status=$STATUS)." >&2
        aws ssm get-command-invocation --command-id "$CMD" --instance-id "$ID" \
          --region "$REGION" --query 'StandardErrorContent' --output text >&2
        exit 1
      fi
    EOT
  }
}
