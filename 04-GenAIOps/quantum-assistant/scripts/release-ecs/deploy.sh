#!/usr/bin/env bash
# deploy-candidate (runtime ECS): implanta o digest do registro no CANDIDATE e
# anota o resultado no registro.
#
# Por que um invólucro: quem implanta de verdade e o scripts/cloud/deploy.sh
# (nova revisao da task definition com o digest, update-service, espera o
# rollout COMPLETED e /ready 200 pelo ALB servido pelo digest pedido). Aqui se
# garante o que e regra de RELEASE: so o candidate e tocado por este job, o
# registro sai selado (sha256) e vai para releases-ecs/<release_id>.json.
#
# Uso: scripts/release-ecs/deploy.sh candidate --record dist/release-record.json [--approved-by NOME]
#      scripts/release-ecs/deploy.sh live --digest sha256:... [--release-id ID]   (manual, terminal)
# Saida: stdout = JSON do deploy; stderr = progresso. O registro e atualizado no lugar.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ENV_NAME="${1:-}"; shift || true
RECORD=""; APPROVED_BY="${GITLAB_USER_LOGIN:-politica-automatica}"; DIGEST=""; RELEASE_ID=""
while [ $# -gt 0 ]; do
  case "$1" in
    --record) RECORD="$2"; shift 2 ;;
    --approved-by) APPROVED_BY="$2"; shift 2 ;;
    --digest) DIGEST="$2"; shift 2 ;;
    --release-id) RELEASE_ID="$2"; shift 2 ;;
    *) echo "release-ecs/deploy: opcao desconhecida: $1" >&2; exit 2 ;;
  esac
done
log() { echo "release-ecs/deploy: $*" >&2; }
case "$ENV_NAME" in candidate|live) ;; *) log "uso: deploy.sh <candidate|live> --record FILE"; exit 2 ;; esac
# O live so muda por promote.sh (mesmo digest validado) ou rollback.sh.
if [ "$ENV_NAME" = live ] && [ -n "$RECORD" ]; then
  log "o live nao recebe deploy direto de registro: use promote.sh"; exit 2
fi

if [ -n "$RECORD" ]; then
  python3 "$ROOT/scripts/release-ecs/manifest.py" verify --record "$RECORD" >/dev/null \
    || { log "registro $RECORD com manifest_sha256 divergente: nao implanto"; exit 1; }
  DIGEST="$(jq -r .image_digest "$RECORD")"
  RELEASE_ID="$(jq -r .release_id "$RECORD")"
fi
[[ "$DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]] || { log "digest invalido: '$DIGEST'"; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
set +e
bash "$ROOT/scripts/cloud/deploy.sh" "$ENV_NAME" "$DIGEST" --release-id "$RELEASE_ID" > "$WORK/deploy.json"
rc=$?
set -e
cat "$WORK/deploy.json"
[ "$rc" -eq 0 ] || { log "deploy do $ENV_NAME falhou (exit $rc): o registro nao e publicado"; exit "$rc"; }

if [ -n "$RECORD" ]; then
  NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  jq --slurpfile d "$WORK/deploy.json" --arg env "$ENV_NAME" --arg at "$NOW" --arg by "$APPROVED_BY" \
    '.deploy[$env] = ($d[0] + {deployed_at: $at})
     | .timestamps["\($env)_deployed_at"] = $at
     | .approval.approved_by = $by | .approval.approved_at = $at' "$RECORD" > "$WORK/rec.json"
  cp "$WORK/rec.json" "$RECORD"
  python3 "$ROOT/scripts/release-ecs/manifest.py" seal --record "$RECORD" >/dev/null
  python3 "$ROOT/scripts/release-ecs/manifest.py" publish --record "$RECORD" --kind candidate >/dev/null
  log "candidate = $RELEASE_ID (revisao $(jq -r .task_definition_revision "$WORK/deploy.json"))"
fi
