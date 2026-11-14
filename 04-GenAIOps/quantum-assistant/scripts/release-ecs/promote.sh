#!/usr/bin/env bash
# promote-live (runtime ECS): o live passa a rodar o MESMO digest que o
# candidate provou no smoke, e a promocao vira historico no S3.
#
# Antes de mexer no live, tres conferencias (qualquer uma falhando aborta):
#   1. o registro esta selado (manifest_sha256) e veio do deploy-candidate;
#   2. o /candidate/ready AGORA serve exatamente o digest do registro (ninguem
#      reimplantou o candidate entre o smoke e este job);
#   3. o digest do registro e o mesmo que o smoke aprovou (deploy.candidate).
# Depois: deploy do live com o digest, /ready do live pelo ALB com o digest e
# as versoes do registro, e o registro vai para history/ e live.json.
#
# Uso: scripts/release-ecs/promote.sh --record dist/release-record.json [--approved-by NOME]
# Saida: stdout = registro final; stderr = progresso.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
export AWS_PAGER=""
PROJECT="${PROJECT:-qc-assistant}"
RECORD=""; APPROVED_BY=""
while [ $# -gt 0 ]; do
  case "$1" in
    --record) RECORD="$2"; shift 2 ;;
    --approved-by) APPROVED_BY="$2"; shift 2 ;;
    *) echo "promote: opcao desconhecida: $1" >&2; exit 2 ;;
  esac
done
log() { echo "promote: $*" >&2; }
[ -f "$RECORD" ] || { log "registro nao encontrado: $RECORD (vem do job deploy-candidate)"; exit 2; }
python3 "$ROOT/scripts/release-ecs/manifest.py" verify --record "$RECORD" >/dev/null \
  || { log "manifest_sha256 nao confere: registro alterado"; exit 1; }

DIGEST="$(jq -r .image_digest "$RECORD")"
RELEASE_ID="$(jq -r .release_id "$RECORD")"
CAND_DIGEST="$(jq -r '.deploy.candidate.served_digest // empty' "$RECORD")"
[ "$CAND_DIGEST" = "$DIGEST" ] || { log "o registro nao tem deploy de candidate com este digest: promote abortado"; exit 1; }
SMOKE="$(jq -r '.postdeploy.verdict // empty' "$RECORD")"
[ "$SMOKE" = "PASS" ] || { log "o registro nao tem smoke aprovado (postdeploy=$SMOKE): promote abortado"; exit 1; }

ALB_URL="$(aws ssm get-parameter --name "/$PROJECT/ecs/alb-url" --query Parameter.Value --output text)"
NOW_CAND="$(curl -s -m 15 --retry 3 "$ALB_URL/candidate/ready" | jq -r '.versions.image_digest // empty')"
if [ "$NOW_CAND" != "$DIGEST" ]; then
  log "o candidate no ar mudou desde o smoke (no ar=$NOW_CAND, registro=$DIGEST): promote abortado"
  exit 1
fi

BEFORE="$(curl -s -m 15 "$ALB_URL/ready" | jq -c '{release_id: .versions.release_id, image_digest: .versions.image_digest}' 2>/dev/null || echo null)"
PREV="$(python3 "$ROOT/scripts/release-ecs/manifest.py" get --key live 2>/dev/null || echo null)"
log "live antes: $BEFORE"
log "promovendo $RELEASE_ID (${DIGEST:0:19}...) para o live"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
bash "$ROOT/scripts/cloud/deploy.sh" live "$DIGEST" --release-id "$RELEASE_ID" > "$WORK/deploy.json"

# Prova pelo endereco do CLIENTE: o /ready do live com o digest e as versoes do registro.
curl -s -m 15 --retry 3 "$ALB_URL/ready" > "$WORK/live-ready.json"
python3 - "$RECORD" "$WORK/live-ready.json" <<'PY'
import json, sys
rec = json.load(open(sys.argv[1])); ready = json.load(open(sys.argv[2]))
v = ready.get("versions") or {}
keys = ("image_digest", "corpus_version", "graph_seed_version", "vector_index_version",
        "embedding_model_id", "embedding_model_revision")
bad = {k: (rec.get(k), v.get(k)) for k in keys if rec.get(k) is not None and str(rec.get(k)) != str(v.get(k))}
if not ready.get("ready") or v.get("release_id") != rec["release_id"] or bad:
    print(f"promote: live NAO confere com o registro: ready={ready.get('ready')} release={v.get('release_id')} divergencias={bad}", file=sys.stderr)
    sys.exit(1)
print(f"promote: live /ready confere: {rec['release_id']} {v['image_digest'][:19]}... graph={v.get('graph_version')}", file=sys.stderr)
PY

NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
PREV_ID="$(echo "$PREV" | jq -r '.release_id // empty' 2>/dev/null || true)"
PREV_TD="$(echo "$PREV" | jq -r '.deploy.live.task_definition_revision // empty' 2>/dev/null || true)"
APPROVED_BY="${APPROVED_BY:-$(jq -r '.approval.approved_by // "politica-automatica"' "$RECORD")}"
jq --slurpfile d "$WORK/deploy.json" --arg at "$NOW" --arg by "$APPROVED_BY" --arg pid "$PREV_ID" \
   --arg ptd "$PREV_TD" --argjson before "$BEFORE" \
  '.deploy.live = ($d[0] + {deployed_at: $at})
   | .timestamps.promoted_at = $at
   | .approval.approved_by = $by
   | .promotion = {event: "promote", promoted_at: $at, promoted_by: $by,
                   previous_release_id: (if $pid == "" then null else $pid end),
                   previous_task_definition_revision: (if $ptd == "" then null else $ptd end),
                   live_before: $before}' "$RECORD" > "$WORK/rec.json"
cp "$WORK/rec.json" "$RECORD"
python3 "$ROOT/scripts/release-ecs/manifest.py" seal --record "$RECORD" >/dev/null
python3 "$ROOT/scripts/release-ecs/manifest.py" publish --record "$RECORD" --kind live >/dev/null
log "live = $RELEASE_ID (revisao $(jq -r .task_definition_revision "$WORK/deploy.json")); registro em releases-ecs/history/"
cat "$RECORD"
