#!/usr/bin/env bash
# Promove a versao candidata para o alias live e registra o release no S3.
#
# Por que um script e nao o terraform apply: o alias live tem ignore_changes no
# Terraform. Ele so anda quando os gates pos-deploy passaram, e cada movimento
# fica registrado em s3://<bucket>/quantum-assistant/releases/ para o rollback
# saber exatamente para qual CONJUNTO (codigo + corpus + prompts + config) voltar.
#
# Uso: scripts/promote.sh --bucket base-config-SEU-RM [--manifest dist/manifest.json]
#                         [--function qc-assistant-api] [--approved-by nome]
# Saida (stdout): o registro JSON gravado. Progresso vai para stderr.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FUNCTION="qc-assistant-api"
MANIFEST="$ROOT/dist/manifest.json"
BUCKET="${TF_STATE_BUCKET:-}"
APPROVED_BY="${GITLAB_USER_LOGIN:-politica-automatica}"
PREFIX="quantum-assistant/releases"

log() { echo "promote: $*" >&2; }

while [ $# -gt 0 ]; do
  case "$1" in
    --bucket) BUCKET="$2"; shift 2 ;;
    --manifest) MANIFEST="$2"; shift 2 ;;
    --function) FUNCTION="$2"; shift 2 ;;
    --approved-by) APPROVED_BY="$2"; shift 2 ;;
    *) log "opcao desconhecida: $1"; exit 2 ;;
  esac
done
[ -n "$BUCKET" ] || { log "informe --bucket (ou TF_STATE_BUCKET)"; exit 2; }
[ -f "$MANIFEST" ] || { log "manifesto nao encontrado: $MANIFEST"; exit 2; }

CANDIDATE=$(aws lambda get-alias --function-name "$FUNCTION" --name candidate --query FunctionVersion --output text)
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
TMP=$(mktemp)
echo "$RECORD" > "$TMP"

# Um arquivo por release (historico imutavel) + um "live.json" com o que esta no ar.
# Nome com data na frente: listar o prefixo ja devolve em ordem cronologica.
aws s3 cp --quiet "$TMP" "s3://$BUCKET/$PREFIX/${NOW//:/}-v${CANDIDATE}-${RELEASE_ID}.json"
aws s3 cp --quiet "$TMP" "s3://$BUCKET/$PREFIX/live.json"
rm -f "$TMP"

AFTER=$(aws lambda get-alias --function-name "$FUNCTION" --name live --query FunctionVersion --output text)
log "live agora aponta para a versao $AFTER; registro em s3://$BUCKET/$PREFIX/"
echo "$RECORD"
