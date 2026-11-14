#!/usr/bin/env bash
# Package do runtime ECS: builda a imagem linux/amd64 NESTE runner, envia ao
# ECR e devolve o DIGEST (sha256:...), que e a identidade do release.
#
# Por que um invólucro fino: o build de verdade e o scripts/cloud/build-push.sh,
# o mesmo que o aluno usa no make image. Uma implementacao so; aqui so se
# amarra o commit do pipeline e se confere o formato do digest.
#
# Caminho alternativo (QC_IMAGE_SOURCE=ssm): se o runner nao puder fazer push
# no ECR, usa o digest publicado do terminal (make image grava em
# SSM /qc-assistant/image-digest). O manifesto registra de onde o digest veio.
#
# Saida: stdout = sha256:... (uma linha); stderr = progresso.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
export AWS_PAGER=""
PROJECT="${PROJECT:-qc-assistant}"
SOURCE="${QC_IMAGE_SOURCE:-build}"
GIT_SHA="${CI_COMMIT_SHA:-$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || echo sem-git)}"

log() { echo "release-ecs/build: $*" >&2; }

case "$SOURCE" in
  build)
    DIGEST="$(bash "$ROOT/scripts/cloud/build-push.sh" --git-sha "$GIT_SHA" --context "$ROOT")"
    ;;
  ssm)
    DIGEST="$(aws ssm get-parameter --name "/$PROJECT/image-digest" --query Parameter.Value --output text)"
    log "QC_IMAGE_SOURCE=ssm: digest lido de /$PROJECT/image-digest (imagem buildada fora do pipeline)"
    ;;
  *) log "QC_IMAGE_SOURCE deve ser build ou ssm"; exit 2 ;;
esac
[[ "$DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]] || { log "digest invalido: '$DIGEST'"; exit 1; }
# A origem vai junto para o manifesto (manifest.py le esta variavel do arquivo).
mkdir -p "$ROOT/reports"
echo "$SOURCE" > "$ROOT/reports/image-source.txt"
log "release = $PROJECT-app@$DIGEST (origem: $SOURCE, commit ${GIT_SHA:0:12})"
echo "$DIGEST"
