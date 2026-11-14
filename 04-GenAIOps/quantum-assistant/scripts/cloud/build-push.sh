#!/usr/bin/env bash
# Constroi a imagem do app (o release) para linux/amd64, envia ao ECR e devolve
# o DIGEST. Roda onde houver Docker: no job do pipeline (runner) ou no runner
# via SSM (remote-build.sh), que e o caminho do aluno sem Docker no Codespaces.
#
# Por que o digest e a saida: tag e um apelido que pode mudar; o digest e o
# hash do conteudo. Deploy, promote e rollback usam so o digest.
#
# Uso: bash scripts/cloud/build-push.sh [--git-sha SHA] [--context DIR]
# Saida: stdout = sha256:... (uma linha); stderr = progresso do build.
set -euo pipefail

export AWS_PAGER=""
AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
PROJECT="${PROJECT:-qc-assistant}"
CONTEXT="$(cd "$(dirname "$0")/../.." && pwd)"
GIT_SHA="${CI_COMMIT_SHA:-}"
while [ $# -gt 0 ]; do
  case "$1" in
    --git-sha) GIT_SHA="$2"; shift 2 ;;
    --context) CONTEXT="$2"; shift 2 ;;
    *) echo "build-push: opcao desconhecida: $1" >&2; exit 2 ;;
  esac
done
[ -n "$GIT_SHA" ] || GIT_SHA="$(git -C "$CONTEXT" rev-parse HEAD 2>/dev/null || echo sem-git)"

log() { echo "build-push: $*" >&2; }
DOCKERFILE="$CONTEXT/containers/Dockerfile.app"
[ -f "$DOCKERFILE" ] || { log "ERRO: $DOCKERFILE nao existe"; exit 1; }
command -v docker >/dev/null || { log "ERRO: docker ausente. No runner: bash \$COURSE_REPO/04-GenAIOps/shared/runner/runner-docker.sh"; exit 1; }

ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
REGISTRY="$ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com"
REPO="$REGISTRY/$PROJECT-app"
# Tag = commit curto + segundos: o repositorio e IMMUTABLE, entao rebuild do
# mesmo commit precisa de tag nova. Quem identifica o release e o digest.
SHORT="${GIT_SHA:0:12}"
case "$GIT_SHA" in *-dirty-*) SHORT="$SHORT-dirty-${GIT_SHA##*-dirty-}" ;; esac
TAG="git-$SHORT-$(date -u +%Y%m%d%H%M%S)"

aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$REGISTRY" >/dev/null 2>&1 \
  || { log "ERRO: docker login no ECR falhou (credencial expirada?)"; exit 1; }
log "build ${TAG} (linux/amd64, commit ${GIT_SHA:0:12})..."
START=$(date +%s)
# --provenance=false: uma imagem so (sem indice de atestados), entao o digest
# do push e o mesmo que a task do ECS vai puxar.
docker build --platform linux/amd64 --provenance=false \
  -f "$DOCKERFILE" --build-arg "QC_GIT_SHA=$GIT_SHA" -t "$REPO:$TAG" "$CONTEXT" >&2
BUILT=$(date +%s)
docker push "$REPO:$TAG" >&2
PUSHED=$(date +%s)
DIGEST="$(aws ecr describe-images --repository-name "$PROJECT-app" --image-ids imageTag="$TAG" \
  --query 'imageDetails[0].imageDigest' --output text)"
docker logout "$REGISTRY" >/dev/null 2>&1 || true
log "build $((BUILT - START)) s, push $((PUSHED - BUILT)) s, tag $TAG"
[[ "$DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]] || { log "ERRO: digest nao encontrado no ECR para $TAG"; exit 1; }
echo "$DIGEST"
