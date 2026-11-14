# Dois repositorios: a imagem do app (o release) e o espelho do Neo4j.
# Por que espelhar o Neo4j: a task nao depende do Docker Hub na hora de subir
# (limite de pull anonimo, indisponibilidade) e o digest fica sob nosso controle.

variable "ecr_keep_images" {
  type        = number
  description = "Quantas imagens cada repositorio guarda. O rollback precisa do digest anterior ainda no ECR."
  default     = 10
}

resource "aws_ecr_repository" "repo" {
  for_each = toset(["app", "neo4j"])
  name     = "${var.project}-${each.key}"

  # IMMUTABLE: uma tag nunca passa a apontar para outra imagem. Junto com o
  # deploy por digest, "o que foi avaliado" e "o que roda" sao o mesmo binario.
  image_tag_mutability = "IMMUTABLE"

  # Permite o cleanup apagar o repositorio mesmo com imagens dentro.
  force_delete = true

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }
}

resource "aws_ecr_lifecycle_policy" "keep_last" {
  for_each   = aws_ecr_repository.repo
  repository = each.value.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Guarda so as ${var.ecr_keep_images} imagens mais recentes (custo de armazenamento)."
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = var.ecr_keep_images
      }
      action = { type = "expire" }
    }]
  })
}
