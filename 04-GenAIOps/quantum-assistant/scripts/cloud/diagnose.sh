#!/usr/bin/env bash
# Mostra, sem mudar nada, por que um ambiente do ECS esta (ou nao) atendendo.
#
# Responde na ordem em que o problema costuma aparecer:
#   1. o service quer quantas tasks e quantas estao rodando? qual revisao?
#   2. as tasks: status, saude de cada conteiner, motivo de parada;
#   3. o target group: o ALB considera a task saudavel? se nao, por que?
#   4. os ultimos eventos do service (CannotPullContainer, unhealthy...);
#   5. as ultimas linhas de log do app e do Neo4j.
# Nada aqui imprime segredo: as variaveis da task NAO sao listadas, e linhas
# de log com password/secret/token/authorization sao cortadas.
#
# Uso: bash scripts/cloud/diagnose.sh [live|candidate] [--log-lines N]
# Saida: stdout = relatorio legivel; stderr = progresso.
set -euo pipefail

export AWS_PAGER=""
AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
export AWS_REGION
PROJECT="${PROJECT:-qc-assistant}"
CLUSTER="${CLUSTER:-$PROJECT}"
ENV_NAME="${1:-live}"; shift || true
LINES=20
while [ $# -gt 0 ]; do
  case "$1" in --log-lines) LINES="$2"; shift 2 ;; *) echo "diagnose: opcao desconhecida: $1" >&2; exit 2 ;; esac
done
case "$ENV_NAME" in live) TG_NAME="$PROJECT-live-tg"; PREFIX="" ;; candidate) TG_NAME="$PROJECT-cand-tg"; PREFIX="/candidate" ;;
  *) echo "uso: diagnose.sh [live|candidate]" >&2; exit 2 ;; esac
SERVICE="$PROJECT-$ENV_NAME"

# Esconde o ID da conta (12 digitos) e qualquer linha que pareca carregar segredo.
mask() { sed -E 's/[0-9]{12}/****/g' | grep -viE 'password|secret|token|authorization|hmac' || true; }
h() { printf '\n== %s\n' "$*"; }

echo "diagnostico de $SERVICE (cluster $CLUSTER, $AWS_REGION) em $(date -u +%Y-%m-%dT%H:%M:%SZ)"

h "service"
aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" --output json \
  --query 'services[0].{status:status, desired:desiredCount, running:runningCount, pending:pendingCount,
           deployments:deployments[].{revision:taskDefinition, rollout:rolloutState, running:runningCount, failed:failedTasks, reason:rolloutStateReason}}' \
  | jq -r '"status=\(.status) desired=\(.desired) running=\(.running) pending=\(.pending)",
           (.deployments[] | "  deployment revisao=\(.revision|split("/")|last) rollout=\(.rollout) running=\(.running) falhas=\(.failed)\n    motivo: \(.reason)")' | mask

h "imagem da revisao em uso"
TD="$(aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" --query 'services[0].taskDefinition' --output text)"
aws ecs describe-task-definition --task-definition "$TD" --output json \
  --query 'taskDefinition.{cpu:cpu, memory:memory, containers:containerDefinitions[].{name:name, image:image}}' \
  | jq -r '"cpu=\(.cpu) memoria=\(.memory)MB", (.containers[] | "  \(.name): \(.image)")' | mask

h "tasks (rodando e paradas recentemente)"
TASKS="$( { aws ecs list-tasks --cluster "$CLUSTER" --service-name "$SERVICE" --query 'taskArns' --output text;
            aws ecs list-tasks --cluster "$CLUSTER" --service-name "$SERVICE" --desired-status STOPPED --query 'taskArns[:3]' --output text; } | tr '\t' '\n' | grep -v None || true)"
if [ -z "$TASKS" ]; then
  echo "nenhuma task (desired=0? rode make start ENV=$ENV_NAME, ou make deploy-all se nunca houve deploy)"
else
  # shellcheck disable=SC2086
  aws ecs describe-tasks --cluster "$CLUSTER" --tasks $TASKS --output json \
    --query 'tasks[].{id:taskArn, last:lastStatus, health:healthStatus, az:availabilityZone, started:startedAt, stopped:stoppedReason,
             containers:containers[].{n:name, s:lastStatus, h:healthStatus, exit:exitCode, why:reason}}' \
    | jq -r '.[] | "task \(.id|split("/")|last) status=\(.last) saude=\(.health) zona=\(.az) inicio=\(.started)\(if .stopped then "\n  PAROU: \(.stopped)" else "" end)",
             (.containers[] | "  conteiner \(.n): \(.s) saude=\(.h) exit=\(.exit) \(.why // "")")' | mask
fi

h "target group $TG_NAME (visao do ALB)"
TG_ARN="$(aws elbv2 describe-target-groups --names "$TG_NAME" --query 'TargetGroups[0].TargetGroupArn' --output text)"
aws elbv2 describe-target-health --target-group-arn "$TG_ARN" --output json \
  --query 'TargetHealthDescriptions[].{ip:Target.Id, estado:TargetHealth.State, motivo:TargetHealth.Reason, detalhe:TargetHealth.Description}' \
  | jq -r 'if length == 0 then "nenhum alvo registrado" else .[] | "  \(.ip) \(.estado) \(.motivo // "") \(.detalhe // "")" end'

h "GET $PREFIX/ready pelo ALB"
ALB_URL="$(aws ssm get-parameter --name "/$PROJECT/ecs/alb-url" --query Parameter.Value --output text 2>/dev/null || echo "")"
if [ -n "$ALB_URL" ]; then
  curl -s -m 10 -w '\nHTTP %{http_code} em %{time_total}s\n' "$ALB_URL$PREFIX/ready" | head -c 1500; echo
fi

h "ultimos eventos do service"
aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" --query 'services[0].events[:8].[createdAt,message]' --output text | mask

h "logs recentes (ultimas $LINES linhas de cada conteiner)"
for c in app neo4j; do
  # A API nao aceita prefixo e ordenacao por data juntos: filtramos pelo
  # prefixo e escolhemos o stream com o evento mais recente.
  STREAM="$(aws logs describe-log-streams --log-group-name "/$PROJECT/ecs" --log-stream-name-prefix "$ENV_NAME-$c/" \
    --query 'sort_by(logStreams, &to_number(lastEventTimestamp || `0`))[-1].logStreamName' --output text 2>/dev/null || echo None)"
  echo "-- $c (${STREAM##*/})"
  if [ "$STREAM" != "None" ] && [ -n "$STREAM" ]; then
    aws logs get-log-events --log-group-name "/$PROJECT/ecs" --log-stream-name "$STREAM" --limit "$LINES" \
      --query 'events[].message' --output text | tr '\t' '\n' | cut -c1-300 | mask
  else
    echo "   sem log ainda"
  fi
done
