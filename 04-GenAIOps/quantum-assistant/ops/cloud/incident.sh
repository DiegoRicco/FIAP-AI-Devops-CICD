#!/usr/bin/env bash
# Incidente controlado da Demo 5.2 no live da AWS, SEM commit e SEM pipeline.
#
#   bash ops/cloud/incident.sh state   [--env live|candidate]
#   bash ops/cloud/incident.sh apply   [--scenario stale_graph_policy] [--env live]
#   bash ops/cloud/incident.sh restore [--scenario stale_graph_policy] [--env live]
#   bash ops/cloud/incident.sh --teacher-reset       # restore em live E candidate (idempotente)
#
# Como funciona (e por que assim):
#   - A porta admin (8001) da task NAO passa pelo ALB: o SG da task so a aceita
#     vinda do SG do GitLab Runner. Entao este script, do SEU terminal, pede ao
#     runner (via `aws ssm send-command`, sem SSH) para chamar a 8001.
#   - A chave HMAC e lida do SSM DENTRO do runner e vive so no ambiente do
#     processo de la: nunca passa pelo seu terminal, pelo log do SSM nem pelo Git.
#   - O IP privado da task e descoberto agora (aws ecs describe-tasks): a task
#     muda de IP a cada deploy/restart.
#   - O cliente que assina e chama e o web/admin_client.py do proprio projeto,
#     enviado ao runner junto com o comando (o runner nao precisa do repositorio).
#   - stale_graph_policy muda SO o metadado no Neo4j do live (v2 volta a active e
#     passa a "substituir" a v3). O corpus, a imagem e o Git ficam iguais: e um
#     "sync de catalogo defeituoso". restore re-sincroniza o grafo da fonte que
#     esta na imagem (seed + front matter do corpus).
# Saida: stdout = JSON (graph_version antes/depois, mutacoes ativas); stderr = progresso.
set -euo pipefail

export AWS_PAGER=""
export AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
CLIENT="$ROOT/web/admin_client.py"
PROJECT="qc-assistant"
CLUSTER="${QC_CLUSTER:-$PROJECT}"
RUNNER_NAME="${QC_RUNNER_NAME:-quantum-ai-gitlab-runner}"
SCENARIO="stale_graph_policy"
ENV_NAME="live"
ACTION=""

log() { echo "incident: $*" >&2; }
die() { log "ERRO: $*"; exit 1; }
usage() { sed -n '4,8p' "$0" >&2; exit 2; }

while [ $# -gt 0 ]; do
  case "$1" in
    state|apply|restore) ACTION="$1"; shift ;;
    --teacher-reset) ACTION="teacher-reset"; shift ;;
    --scenario) SCENARIO="$2"; shift 2 ;;
    --env) ENV_NAME="$2"; shift 2 ;;
    -h|--help) usage ;;
    *) die "argumento desconhecido: $1" ;;
  esac
done
[ -n "$ACTION" ] || usage
case "$SCENARIO" in stale_graph_policy|remove_campaign_edge) ;; *) die "cenario invalido: $SCENARIO" ;; esac
case "$ENV_NAME" in live|candidate) ;; *) die "--env deve ser live ou candidate" ;; esac
[ -f "$CLIENT" ] || die "nao achei $CLIENT (rode a partir da raiz do projeto)"

aws sts get-caller-identity >/dev/null 2>&1 || die "credencial AWS invalida ou expirada (Start Lab e copie de novo)"

runner_id() {
  local id
  id=$(aws ec2 describe-instances --filters "Name=tag:Name,Values=$RUNNER_NAME" "Name=instance-state-name,Values=running" \
    --query 'Reservations[].Instances[].InstanceId' --output text | awk '{print $1}')
  [ -n "$id" ] && [ "$id" != "None" ] || die "runner $RUNNER_NAME nao esta running (ligue-o no console EC2)"
  echo "$id"
}

task_ip() {
  # Durante um deploy o service roda a task velha e a nova juntas (max 200%):
  # a primeira da lista pode ser a velha, que some em seguida. Por isso so vale
  # a task RUNNING da revisao que o service esta implantando agora.
  local env="$1" svc="$PROJECT-$1" td arns ip
  td=$(aws ecs describe-services --cluster "$CLUSTER" --services "$svc" \
    --query 'services[0].taskDefinition' --output text)
  [ -n "$td" ] && [ "$td" != "None" ] || die "service $svc nao encontrado no cluster $CLUSTER"
  arns=$(aws ecs list-tasks --cluster "$CLUSTER" --service-name "$svc" --desired-status RUNNING \
    --query 'taskArns' --output text)
  [ -n "$arns" ] && [ "$arns" != "None" ] || die "nenhuma task RUNNING em $svc"
  # shellcheck disable=SC2086
  ip=$(aws ecs describe-tasks --cluster "$CLUSTER" --tasks $arns \
    --query "tasks[?taskDefinitionArn=='$td' && lastStatus=='RUNNING'] | [0].attachments[0].details[?name=='privateIPv4Address'].value | [0]" \
    --output text)
  [ -n "$ip" ] && [ "$ip" != "None" ] || die "nenhuma task RUNNING de $svc na revisao atual (${td##*/}); deploy em andamento? tente de novo em 1 min ou rode make diagnose ENV=$env"
  echo "$ip"
}

# Executa o admin_client.py no runner. O que vai no comando do SSM: o codigo do
# cliente (base64), o IP, o metodo, o caminho e o corpo. A CHAVE NAO VAI: o
# runner a busca no SSM com a propria role (LabInstanceProfile).
admin_call() {
  local env="$1" method="$2" path="$3" body="${4:-}" rid ip client_b64 script params cid status out
  rid=$(runner_id)
  ip=$(task_ip "$env")
  client_b64=$(base64 < "$CLIENT" | tr -d '\n')
  script=$(cat <<EOS
set -eu
d=\$(mktemp -d); trap 'rm -rf "\$d"' EXIT
echo '$client_b64' | base64 -d > "\$d/admin_client.py"
QC_ADMIN_HMAC_KEY=\$(aws ssm get-parameter --region $AWS_REGION --name /$PROJECT/$env/admin-hmac --with-decryption --query Parameter.Value --output text)
export QC_ADMIN_HMAC_KEY
python3 "\$d/admin_client.py" --host $ip $method $path '$body'
EOS
)
  params=$(python3 -c 'import json,sys; print(json.dumps({"commands":[sys.argv[1]],"executionTimeout":["180"]}))' "$script")
  log "$env: $method $path via runner $rid -> task $ip:8001"
  cid=$(aws ssm send-command --instance-ids "$rid" --document-name AWS-RunShellScript \
    --comment "qc-incident $method $path ($env)" --timeout-seconds 300 --parameters "$params" \
    --query Command.CommandId --output text)
  while :; do
    status=$(aws ssm get-command-invocation --command-id "$cid" --instance-id "$rid" --query Status --output text 2>/dev/null || echo Pending)
    case "$status" in Pending|InProgress|Delayed) sleep 2 ;; *) break ;; esac
  done
  out=$(aws ssm get-command-invocation --command-id "$cid" --instance-id "$rid" --query StandardOutputContent --output text)
  if [ "$status" != "Success" ]; then
    aws ssm get-command-invocation --command-id "$cid" --instance-id "$rid" --query StandardErrorContent --output text | tail -5 >&2
    die "chamada admin falhou ($status)"
  fi
  printf '%s\n' "$out"
}

summary() {
  # Resume o JSON da API admin: o aluno le graph_version antes/depois e as mutacoes.
  python3 -c '
import json, sys
env, action, scenario, t0, t1 = sys.argv[1:6]
raw = sys.stdin.read().strip().splitlines()
d = json.loads(raw[-1]) if raw else {}
print(json.dumps({"environment": env, "action": action, "scenario": scenario if action != "state" else None,
  "started_at": t0, "finished_at": t1,
  "graph_version_before": d.get("graph_version_before"), "graph_version": d.get("graph_version"),
  "graph_seed_version": d.get("graph_seed_version"), "in_sync": d.get("in_sync"),
  "active_mutations": d.get("active_mutations"), "diff": d.get("diff"), "counts": d.get("counts")},
  ensure_ascii=False))' "$@"
}

now() { date -u +%Y-%m-%dT%H:%M:%SZ; }

run_one() {
  local env="$1" action="$2" t0 out
  t0=$(now)
  if [ "$action" = state ]; then
    out=$(admin_call "$env" GET /admin/graph/state)
  else
    out=$(admin_call "$env" POST /admin/graph/scenario "{\"scenario\":\"$SCENARIO\",\"action\":\"$action\"}")
  fi
  printf '%s\n' "$out" | summary "$env" "$action" "$SCENARIO" "$t0" "$(now)"
}

case "$ACTION" in
  state|apply|restore) run_one "$ENV_NAME" "$ACTION" ;;
  teacher-reset)
    # Volta a turma ao baseline: restaura os dois cenarios nos dois ambientes.
    # restore e re-sincronizar da fonte, entao rodar de novo nao muda nada.
    for env in live candidate; do
      if ! aws ecs list-tasks --cluster "$CLUSTER" --service-name "$PROJECT-$env" --desired-status RUNNING \
           --query 'length(taskArns)' --output text | grep -qv '^0$'; then
        log "$env sem task RUNNING: nada a restaurar"
        continue
      fi
      for SCENARIO in stale_graph_policy remove_campaign_edge; do
        run_one "$env" restore
      done
    done
    ;;
esac
