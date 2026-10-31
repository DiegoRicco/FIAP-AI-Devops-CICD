# A rede (VPC fiap-lab + subnets publicas) e compartilhada pelo curso inteiro e
# foi criada na demo 01-Terraform/02-Modules. Aqui ela e apenas LIDA por tag:
# este codigo nao cria, nao altera e nao destroi nada da rede.
data "aws_vpc" "vpc" {
  tags = {
    Name = var.project
  }
}

data "aws_subnets" "all" {
  filter {
    name   = "tag:Tier"
    values = ["Public"]
  }
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.vpc.id]
  }
}

data "aws_subnet" "public" {
  for_each = toset(data.aws_subnets.all.ids)
  id       = each.value
}

# O runner nao recebe conexao de entrada: o SSM Agent e o gitlab-runner falam de
# dentro para fora (SSM e gitlab.com). Por isso o Security Group so tem egress,
# sem porta 22 e sem nenhuma outra porta aberta.
resource "aws_security_group" "runner" {
  vpc_id      = data.aws_vpc.vpc.id
  name        = "quantum-ai-gitlab-runner"
  description = "GitLab Runner da Quantum Commerce - somente saida, acesso via SSM"

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "quantum-ai-gitlab-runner"
  }
}
