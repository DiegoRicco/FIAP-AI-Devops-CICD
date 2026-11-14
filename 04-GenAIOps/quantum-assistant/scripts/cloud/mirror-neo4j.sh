#!/usr/bin/env bash
# Copia a imagem oficial do Neo4j (Docker Hub) para o ECR do laboratorio, com o
# MESMO digest, lendo tag e digest de infra-ecs/neo4j-image.json.
#
# Por que espelhar: a task nao depende do Docker Hub ao subir (limite de pull
# anonimo, indisponibilidade) e a versao fica pinada por digest no nosso ECR.
# Idempotente: se a tag ja existe no ECR com o digest certo, nao faz nada.
#
# Precisa de Docker com buildx (Codespaces/Mac) OU roda no runner via SSM.
# Uso: bash scripts/cloud/mirror-neo4j.sh
# Saida: stdout = digest no ECR (sha256:...); stderr = progresso.
set -euo pipefail
export AWS_PAGER=""
AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
PROJECT="${PROJECT:-qc-assistant}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SPEC="$ROOT/infra-ecs/neo4j-image.json"
TAG="$(jq -r .tag "$SPEC")"; DIGEST="$(jq -r .digest "$SPEC")"; SRC="docker.io/library/$(jq -r .repository "$SPEC")"
log() { echo "mirror-neo4j: $*" >&2; }

if aws ecr describe-images --repository-name "$PROJECT-neo4j" --image-ids imageDigest="$DIGEST" >/dev/null 2>&1; then
  log "o ECR ja tem $TAG ($DIGEST); nada a fazer"
  echo "$DIGEST"; exit 0
fi
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
REG="$ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com"
aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$REG" >/dev/null 2>&1
if docker buildx version >/dev/null 2>&1; then
  # Copia de registro para registro, so a plataforma amd64: o digest do
  # manifesto nao muda (e o que a task definition referencia).
  log "copiando $SRC@$DIGEST -> $PROJECT-neo4j:$TAG (buildx imagetools)"
  docker buildx imagetools create -t "$REG/$PROJECT-neo4j:$TAG" "$SRC@$DIGEST" >&2
else
  log "copiando com docker pull/tag/push"
  docker pull --platform linux/amd64 "$SRC@$DIGEST" >&2
  docker tag "$SRC@$DIGEST" "$REG/$PROJECT-neo4j:$TAG"
  docker push "$REG/$PROJECT-neo4j:$TAG" >&2
fi
docker logout "$REG" >/dev/null 2>&1 || true
aws ecr describe-images --repository-name "$PROJECT-neo4j" --image-ids imageDigest="$DIGEST" >/dev/null \
  || { log "ERRO: o digest $DIGEST nao apareceu no ECR"; exit 1; }
echo "$DIGEST"
