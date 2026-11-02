#!/usr/bin/env bash
# Volta o alias live para o release ANTERIOR registrado em s3://<bucket>/quantum-assistant/releases/.
#
# Por que pelo registro e nao "versao atual - 1": a versao anterior da Lambda pode
# ser uma candidata que REPROVOU no pos-deploy e nunca foi ao ar. O registro so
# tem releases que passaram pelos gates e foram promovidos.
#
# O que volta junto: a versao da Lambda e imutavel e contem codigo + corpus +
# prompts + config do bundle. Voltar a versao volta o CONJUNTO, e o script
# mostra quais versoes de corpus/indice/prompt voltaram.
#
# Uso: scripts/rollback.sh [--bucket base-config-SEU-RM] [--function qc-assistant-api] [--dry-run]
# Sem --bucket (nem TF_STATE_BUCKET), usa o bucket base-config-* da conta.
set -euo pipefail

FUNCTION="qc-assistant-api"
BUCKET="${TF_STATE_BUCKET:-}"
PREFIX="quantum-assistant/releases"
DRY_RUN=0
REASON="${ROLLBACK_REASON:-rollback manual}"

log() { echo "rollback: $*" >&2; }

while [ $# -gt 0 ]; do
  case "$1" in
    --bucket) BUCKET="$2"; shift 2 ;;
    --function) FUNCTION="$2"; shift 2 ;;
    --reason) REASON="$2"; shift 2 ;;
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

CURRENT=$(aws lambda get-alias --function-name "$FUNCTION" --name live --query FunctionVersion --output text)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# Historico de promocoes em ordem cronologica (o nome comeca com a data).
aws s3 ls "s3://$BUCKET/$PREFIX/" | awk '{print $4}' | grep -E '^[0-9]{4}-.*-v[0-9]+-.*\.json$' | sort > "$WORK/list" || true
[ -s "$WORK/list" ] || { log "nenhum release registrado em s3://$BUCKET/$PREFIX/"; exit 1; }

# Baixa o historico e monta "arquivo versao" em ordem cronologica.
: > "$WORK/hist"
while read -r name; do
  aws s3 cp --quiet "s3://$BUCKET/$PREFIX/$name" "$WORK/$name"
  echo "$name $(jq -r .lambda_version "$WORK/$name")" >> "$WORK/hist"
done < "$WORK/list"

# Alvo: o release que estava no ar ANTES da promocao da versao atual. Cada
# registro de promocao guarda previous_lambda_version, entao rollbacks seguidos
# andam para tras na mesma ordem em que as promocoes aconteceram, e uma
# candidata que reprovou (e nunca foi promovida) nunca vira destino.
CUR_FILE=$(awk -v c="$CURRENT" '$2 == c {f = $1} END {print f}' "$WORK/hist")
[ -n "$CUR_FILE" ] || { log "a versao $CURRENT no ar nao tem registro de promocao; rollback manual pelo console"; exit 1; }
PREV_V=$(jq -r .previous_lambda_version "$WORK/$CUR_FILE")
TARGET_FILE=$(awk -v p="$PREV_V" '$2 == p {f = $1} END {print f}' "$WORK/hist")
if [ "$PREV_V" = "$CURRENT" ] || [ -z "$TARGET_FILE" ]; then
  log "nao ha release anterior registrado para a versao $CURRENT (ela foi a primeira promovida)"
  exit 1
fi
TARGET_V=$(jq -r .lambda_version "$WORK/$TARGET_FILE")

log "live hoje: versao $CURRENT"
log "volta para: versao $TARGET_V (registro $TARGET_FILE)"
log "conjunto que volta (antes -> depois):"
for k in release_id git_sha application_version prompt_version corpus_version index_version eval_set_version; do
  before=$([ -n "$CUR_FILE" ] && jq -r ".$k" "$WORK/$CUR_FILE" || echo "?")
  after=$(jq -r ".$k" "$WORK/$TARGET_FILE")
  log "  $k: $before -> $after"
done

if [ "$DRY_RUN" = 1 ]; then
  log "--dry-run: nada foi alterado"
  exit 0
fi

aws lambda update-alias --function-name "$FUNCTION" --name live --function-version "$TARGET_V" >/dev/null
NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)
jq --arg at "$NOW" --arg from "$CURRENT" --arg reason "$REASON" \
  '. + {rolled_back_at: $at, rolled_back_from_version: $from, rollback_reason: $reason}' \
  "$WORK/$TARGET_FILE" > "$WORK/live.json"
# Rollback tambem e um evento do historico: o proximo rollback parte daqui.
aws s3 cp --quiet "$WORK/live.json" "s3://$BUCKET/$PREFIX/live.json"
aws s3 cp --quiet "$WORK/live.json" "s3://$BUCKET/$PREFIX/rollbacks/${NOW//:/}-v${CURRENT}-to-v${TARGET_V}.json"

AFTER=$(aws lambda get-alias --function-name "$FUNCTION" --name live --query FunctionVersion --output text)
log "live agora aponta para a versao $AFTER"
cat "$WORK/live.json"
