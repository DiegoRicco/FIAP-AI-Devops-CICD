#!/usr/bin/env bash
# rollback (runtime ECS): volta o live para o release aprovado ANTERIOR.
#
# Por que pelo historico e nao "revisao - 1": a revisao anterior da task
# definition pode ser um candidate que reprovou. history/ em releases-ecs/ so
# tem PROMOCOES (e rollbacks), em ordem cronologica. O alvo e o release que
# estava no ar antes da promocao do release atual (previous_release_id do
# registro do live), e ele volta pela MESMA revisao exata da task definition:
# imagem (codigo + corpus + grafo + modelo + cache de embeddings) volta inteira,
# e o Neo4j da task nova e reconstruido do seed daquela imagem.
#
# Prova: /ready do live com image_digest, corpus, graph_seed e vector_index do
# alvo, graph_version == graph_seed_version (grafo da fonte), e uma resposta
# conhecida (troca de fone em 2026-11-14 = politica-trocas v3, 30 dias).
#
# Uso: scripts/release-ecs/rollback.sh [--reason TEXTO] [--dry-run]
# Saida: stdout = registro do rollback; stderr = progresso.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
export AWS_PAGER=""
PROJECT="${PROJECT:-qc-assistant}"
REASON="${ROLLBACK_REASON:-rollback manual}"
DRY_RUN=0
while [ $# -gt 0 ]; do
  case "$1" in
    --reason) REASON="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    *) echo "rollback: opcao desconhecida: $1" >&2; exit 2 ;;
  esac
done
log() { echo "rollback: $*" >&2; }
M="$ROOT/scripts/release-ecs/manifest.py"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
python3 "$M" get --key live > "$WORK/live.json" || { log "nenhum live registrado em releases-ecs/: nada para voltar"; exit 1; }
python3 "$M" history > "$WORK/history.json"
CUR_ID="$(jq -r .release_id "$WORK/live.json")"
ALB_URL="$(aws ssm get-parameter --name "/$PROJECT/ecs/alb-url" --query Parameter.Value --output text)"
SERVED="$(curl -s -m 15 "$ALB_URL/ready" | jq -r '.versions.release_id // empty')"
if [ "$SERVED" != "$CUR_ID" ]; then
  log "o live no ar ($SERVED) nao e o live registrado ($CUR_ID): alguem mexeu fora do pipeline; rollback manual"
  exit 1
fi
TARGET_ID="$(jq -r '.promotion.previous_release_id // empty' "$WORK/live.json")"
[ -n "$TARGET_ID" ] || { log "o live atual ($CUR_ID) foi o primeiro promovido: nao ha release anterior aprovado"; exit 1; }
# O alvo tem que ter sido PROMOVIDO de verdade (estar no historico).
TARGET_KEY="$(jq -r --arg id "$TARGET_ID" '[.[] | select(.release_id == $id and .task_definition != null)] | last | .key // empty' "$WORK/history.json")"
[ -n "$TARGET_KEY" ] || { log "$TARGET_ID nao esta no historico de promocoes: nao e destino valido"; exit 1; }
BUCKET="${TF_STATE_BUCKET:-$(aws s3 ls | awk '{print $3}' | grep '^base-config' | head -1)}"
aws s3 cp --quiet "s3://$BUCKET/quantum-assistant/releases-ecs/$TARGET_KEY" "$WORK/target.json"
python3 "$M" verify --record "$WORK/target.json" >/dev/null || { log "registro do alvo adulterado (sha256)"; exit 1; }
TD="$(jq -r .deploy.live.task_definition_revision "$WORK/target.json")"

log "live hoje: $CUR_ID"
log "volta para: $TARGET_ID (revisao exata $TD, registro $TARGET_KEY)"
for k in image_digest git_sha corpus_version graph_seed_version vector_index_version embedding_model_revision eval_set_version; do
  log "  $k: $(jq -r ".$k" "$WORK/live.json") -> $(jq -r ".$k" "$WORK/target.json")"
done
if [ "$DRY_RUN" = 1 ]; then log "--dry-run: nada foi alterado"; exit 0; fi

# O registro guarda "familia:revisao" (sem o ID da conta); o deploy precisa do ARN completo.
TD_ARN="$(aws ecs describe-task-definition --task-definition "$TD" --query taskDefinition.taskDefinitionArn --output text)"
bash "$ROOT/scripts/cloud/deploy.sh" live --task-definition-arn "$TD_ARN" > "$WORK/deploy.json"

curl -s -m 15 --retry 3 "$ALB_URL/ready" > "$WORK/ready.json"
curl -s -m 20 --retry 2 -X POST "$ALB_URL/lab/compare" -H 'Content-Type: application/json' \
  -H 'X-QC-Scenario: rollback-check' \
  -d '{"question":"Comprei um fone de ouvido, qual o prazo para trocar?","as_of_date":"2026-11-14","tenant":"quantum","user_role":"customer","strategies":["graph"]}' \
  > "$WORK/answer.json"
python3 - "$WORK/target.json" "$WORK/ready.json" "$WORK/answer.json" <<'PY'
import json, sys
t = json.load(open(sys.argv[1])); r = json.load(open(sys.argv[2])); a = json.load(open(sys.argv[3]))
v = r.get("versions") or {}
keys = ("image_digest", "corpus_version", "graph_seed_version", "vector_index_version")
bad = {k: (t.get(k), v.get(k)) for k in keys if str(t.get(k)) != str(v.get(k))}
g = (a.get("strategies") or {}).get("graph") or {}
cites = [f"{c['doc_id']}@v{c['version']}" for c in g.get("citations") or []]
answer_ok = "30 dias" in (g.get("answer") or "") and "politica-trocas@v3" in cites
problems = []
if not r.get("ready") or v.get("release_id") != t["release_id"]:
    problems.append(f"ready={r.get('ready')} release={v.get('release_id')} (esperado {t['release_id']})")
if bad:
    problems.append(f"versoes divergentes {bad}")
if v.get("graph_version") != v.get("graph_seed_version"):
    problems.append("grafo em runtime diferente do seed da imagem")
if not answer_ok:
    problems.append(f"resposta conhecida falhou: cites={cites}")
if problems:
    print("rollback: live NAO voltou ao conjunto do alvo: " + "; ".join(problems), file=sys.stderr)
    sys.exit(1)
print(f"rollback: live /ready = {t['release_id']} {v['image_digest'][:19]}... corpus={v['corpus_version']} "
      f"graph={v['graph_version']} index={v['vector_index_version']}; fone 2026-11-14 -> {cites[0]} 30 dias", file=sys.stderr)
PY

NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
jq --slurpfile d "$WORK/deploy.json" --slurpfile ready "$WORK/ready.json" --arg at "$NOW" --arg from "$CUR_ID" \
   --arg reason "$REASON" --arg by "${GITLAB_USER_LOGIN:-terminal}" \
   --arg prev "$(jq -r '.promotion.previous_release_id // empty' "$WORK/target.json")" \
  '.deploy.live = ($d[0] + {deployed_at: $at})
   | .promotion = {event: "rollback", promoted_at: $at, promoted_by: $by, rolled_back_from: $from,
                   reason: $reason, previous_release_id: (if $prev == "" then null else $prev end)}
   | .rollback_proof = {ready_versions: $ready[0].versions}' "$WORK/target.json" > "$WORK/rec.json"
python3 "$M" seal --record "$WORK/rec.json" >/dev/null
python3 "$M" publish --record "$WORK/rec.json" --kind live >/dev/null
log "live = $TARGET_ID de novo; evento de rollback gravado em releases-ecs/history/"
cat "$WORK/rec.json"
