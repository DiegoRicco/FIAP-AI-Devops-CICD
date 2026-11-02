#!/usr/bin/env bash
# Promove a versao candidata para o alias live e registra o release no S3.
#
# Por que um script e nao o terraform apply: o alias live tem ignore_changes no
# Terraform. Ele so anda quando os gates pos-deploy passaram, e cada movimento
# fica registrado em s3://<bucket>/quantum-assistant/releases/ para o rollback
# saber exatamente para qual CONJUNTO (codigo + corpus + prompts + config) voltar.
#
# Uso: scripts/promote.sh [--bucket base-config-SEU-RM] [--manifest dist/manifest.json]
#                         [--function qc-assistant-api] [--approved-by nome] [--dry-run]
# Sem --bucket (nem TF_STATE_BUCKET), usa o bucket base-config-* da conta.
# Saida (stdout): o registro JSON gravado. Progresso vai para stderr.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FUNCTION="qc-assistant-api"
MANIFEST="$ROOT/dist/manifest.json"
BUCKET="${TF_STATE_BUCKET:-}"
APPROVED_BY="${GITLAB_USER_LOGIN:-politica-automatica}"
DRY_RUN=0
PREFIX="quantum-assistant/releases"

log() { echo "promote: $*" >&2; }

while [ $# -gt 0 ]; do
  case "$1" in
    --bucket) BUCKET="$2"; shift 2 ;;
    --manifest) MANIFEST="$2"; shift 2 ;;
    --function) FUNCTION="$2"; shift 2 ;;
    --approved-by) APPROVED_BY="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    *) log "opcao desconhecida: $1"; exit 2 ;;
  esac
done
# Sem --bucket nem TF_STATE_BUCKET: descobre o base-config-* da conta, o mesmo
# padrao do resto do curso (um bucket de state por aluno).
if [ -z "$BUCKET" ]; then
  BUCKET=$(aws s3 ls | awk '{print $3}' | grep '^base-config' | head -1 || true)
  [ -n "$BUCKET" ] && log "bucket descoberto na conta: $BUCKET"
fi
[ -n "$BUCKET" ] || { log "nenhum bucket base-config- encontrado: informe --bucket (ou TF_STATE_BUCKET)"; exit 2; }
CANDIDATE=$(aws lambda get-alias --function-name "$FUNCTION" --name candidate --query FunctionVersion --output text)

# No Codespaces nao existe dist/manifest.json (ele nasce no job package do
# runner). Se a candidata ja foi promovida antes (ex.: re-promover depois de um
# rollback), o manifesto dela esta no registro do S3 e serve do mesmo jeito.
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
if [ ! -f "$MANIFEST" ]; then
  REC=$(aws s3 ls "s3://$BUCKET/$PREFIX/" | awk '{print $4}' | grep -E -- "-v${CANDIDATE}-.*\.json$" | sort | tail -1 || true)
  if [ -z "$REC" ]; then
    log "manifesto nao encontrado: $MANIFEST, e a versao $CANDIDATE nao tem registro em s3://$BUCKET/$PREFIX/"
    log "baixe o manifest.json do job package e passe --manifest <arquivo>"
    exit 2
  fi
  aws s3 cp --quiet "s3://$BUCKET/$PREFIX/$REC" "$WORK/manifest.json"
  MANIFEST="$WORK/manifest.json"
  log "manifesto local ausente; usando o registro $REC do S3"
fi
PREVIOUS=$(aws lambda get-alias --function-name "$FUNCTION" --name live --query FunctionVersion --output text)
RELEASE_ID=$(jq -r .release_id "$MANIFEST")

# A versao candidata TEM que ser o pacote que o pipeline avaliou: o sha256 do
# codigo da versao na AWS precisa ser o bundle_sha256 do manifesto.
CODE_SHA_B64=$(aws lambda get-function-configuration --function-name "$FUNCTION" --qualifier "$CANDIDATE" \
  --query CodeSha256 --output text)
# A AWS devolve o sha256 em base64; o manifesto guarda em hexadecimal.
CODE_SHA_HEX=$(python3 -c 'import base64,sys; print(base64.b64decode(sys.argv[1]).hex())' "$CODE_SHA_B64")
EXPECTED_SHA=$(jq -r .bundle_sha256 "$MANIFEST")
if [ "$CODE_SHA_HEX" != "$EXPECTED_SHA" ]; then
  log "a versao candidata ($CANDIDATE) nao e o bundle do manifesto: lambda=$CODE_SHA_HEX manifesto=$EXPECTED_SHA"
  exit 1
fi

if [ "$DRY_RUN" = 1 ]; then
  log "--dry-run: moveria live $PREVIOUS -> $CANDIDATE ($RELEASE_ID); sha256 conferido; nada foi alterado"
  exit 0
fi

if [ "$CANDIDATE" = "$PREVIOUS" ]; then
  log "live ja esta na versao $CANDIDATE ($RELEASE_ID); nada a mover, so registrando"
else
  log "movendo live: versao $PREVIOUS -> $CANDIDATE ($RELEASE_ID)"
  aws lambda update-alias --function-name "$FUNCTION" --name live --function-version "$CANDIDATE" >/dev/null
fi

NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)
RECORD=$(jq --arg v "$CANDIDATE" --arg prev "$PREVIOUS" --arg by "$APPROVED_BY" --arg at "$NOW" \
  --arg fn "$FUNCTION" \
  '. + {approved_by: $by, promoted_at: $at, function_name: $fn, lambda_version: $v, previous_lambda_version: $prev}' \
  "$MANIFEST")
TMP="$WORK/record.json"
echo "$RECORD" > "$TMP"

# Um arquivo por release (historico imutavel) + um "live.json" com o que esta no ar.
# Nome com data na frente: listar o prefixo ja devolve em ordem cronologica.
aws s3 cp --quiet "$TMP" "s3://$BUCKET/$PREFIX/${NOW//:/}-v${CANDIDATE}-${RELEASE_ID}.json"
aws s3 cp --quiet "$TMP" "s3://$BUCKET/$PREFIX/live.json"

AFTER=$(aws lambda get-alias --function-name "$FUNCTION" --name live --query FunctionVersion --output text)
log "live agora aponta para a versao $AFTER; registro em s3://$BUCKET/$PREFIX/"
echo "$RECORD"
