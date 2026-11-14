#!/usr/bin/env bash
# Implanta uma imagem (por DIGEST) em um ambiente do ECS e so devolve sucesso
# quando o ALB entrega /ready 200 para a task nova.
#
# Como funciona (o mesmo caminho para deploy, promote e rollback):
#   1. le a task definition TEMPLATE do ambiente (criada pelo Terraform, com
#      env, secrets e o conteiner do Neo4j);
#   2. troca so a imagem do conteiner "app" por <repo>@sha256:... e preenche
#      QC_RELEASE_ID, QC_IMAGE_DIGEST e o pacote de dados (opcional);
#   3. registra uma revisao nova e manda o service usa-la (desired 1);
#   4. espera o service estabilizar, o target ficar healthy e GET /ready = 200
#      pelo ALB, conferindo que quem respondeu e o digest pedido.
# Por que digest e nao tag: o digest e a identidade do binario. O que o
# pipeline avaliou e o que roda sao a mesma imagem, byte a byte.
#
# Uso:
#   scripts/cloud/deploy.sh <live|candidate> <sha256:...> [--release-id ID]
#                           [--data-bundle-uri s3://... --data-bundle-sha256 HEX]
#   scripts/cloud/deploy.sh <live|candidate> --task-definition-arn <arn>   # rollback exato
# Saida: stdout = 1 JSON com o resultado; stderr = progresso.
# Exit 0 so com /ready 200 pelo ALB servido pela revisao nova.
set -euo pipefail

export AWS_PAGER=""
AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
export AWS_REGION
PROJECT="${PROJECT:-qc-assistant}"
CLUSTER="${CLUSTER:-$PROJECT}"
READY_TIMEOUT="${READY_TIMEOUT:-600}"

log() { echo "deploy: $*" >&2; }
die() { log "ERRO: $*"; exit 1; }

ENV_NAME="${1:-}"; shift || true
case "$ENV_NAME" in live|candidate) ;; *) die "uso: deploy.sh <live|candidate> <sha256:...> [--release-id ID] | --task-definition-arn ARN" ;; esac

DIGEST=""; RELEASE_ID=""; TD_ARN=""; BUNDLE_URI=""; BUNDLE_SHA=""
if [ $# -gt 0 ] && [[ "$1" == sha256:* ]]; then DIGEST="$1"; shift; fi
while [ $# -gt 0 ]; do
  case "$1" in
    --release-id) RELEASE_ID="$2"; shift 2 ;;
    --task-definition-arn) TD_ARN="$2"; shift 2 ;;
    --data-bundle-uri) BUNDLE_URI="$2"; shift 2 ;;
    --data-bundle-sha256) BUNDLE_SHA="$2"; shift 2 ;;
    *) die "opcao desconhecida: $1" ;;
  esac
done
if [ -z "$TD_ARN" ]; then
  [[ "$DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]] || die "informe o digest completo (sha256: + 64 hex). Tag nao e identidade de release."
  if [ -n "$BUNDLE_URI$BUNDLE_SHA" ]; then
    [[ "$BUNDLE_URI" == s3://* && "$BUNDLE_SHA" =~ ^[0-9a-f]{64}$ ]] || die "--data-bundle-uri s3://... e --data-bundle-sha256 (64 hex) andam juntos"
  fi
fi

SERVICE="$PROJECT-$ENV_NAME"
ALB_URL="$(aws ssm get-parameter --name "/$PROJECT/ecs/alb-url" --query Parameter.Value --output text 2>/dev/null)" \
  || die "parametro /$PROJECT/ecs/alb-url ausente: rode antes o terraform do infra-ecs (make up)"
if [ "$ENV_NAME" = live ]; then PREFIX=""; else PREFIX="/candidate"; fi
URL="$ALB_URL$PREFIX"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

if [ -z "$TD_ARN" ]; then
  TEMPLATE="$(aws ssm get-parameter --name "/$PROJECT/ecs/$ENV_NAME/taskdef-template-arn" --query Parameter.Value --output text 2>/dev/null)" \
    || die "template do $ENV_NAME ausente no SSM: rode o terraform do infra-ecs (make up)"
  REPO="$(aws ecr describe-repositories --repository-names "$PROJECT-app" --query 'repositories[0].repositoryUri' --output text)"
  # A imagem precisa existir no ECR antes: falhar aqui e mais claro que um
  # CannotPullContainerError cinco minutos depois.
  aws ecr describe-images --repository-name "$PROJECT-app" --image-ids imageDigest="$DIGEST" >/dev/null 2>&1 \
    || die "digest $DIGEST nao existe em $PROJECT-app (rode make image antes)"
  RELEASE_ID="${RELEASE_ID:-rel-${DIGEST:7:12}}"
  log "ambiente=$ENV_NAME imagem=$PROJECT-app@${DIGEST:0:19}... release=$RELEASE_ID"

  aws ecs describe-task-definition --task-definition "$TEMPLATE" --query taskDefinition > "$WORK/template.json"
  # So os campos aceitos pelo register-task-definition; troca imagem e envs do app.
  jq --arg img "$REPO@$DIGEST" --arg dig "$DIGEST" --arg rel "$RELEASE_ID" \
     --arg buri "$BUNDLE_URI" --arg bsha "$BUNDLE_SHA" '
    def setenv($k; $v): (map(select(.name != $k)) + [{name: $k, value: $v}]);
    {family, taskRoleArn, executionRoleArn, networkMode, requiresCompatibilities,
     cpu, memory, runtimePlatform,
     containerDefinitions: (.containerDefinitions | map(
       if .name == "app" then
         .image = $img
         | .environment = (.environment
             | setenv("QC_IMAGE_DIGEST"; $dig) | setenv("QC_RELEASE_ID"; $rel)
             | setenv("QC_DATA_BUNDLE_URI"; $buri) | setenv("QC_DATA_BUNDLE_SHA256"; $bsha))
       else . end))}
    ' "$WORK/template.json" > "$WORK/new.json"
  TD_ARN="$(aws ecs register-task-definition --cli-input-json "file://$WORK/new.json" \
    --tags key=Project,value=quantum-commerce key=Lab,value=05-1 key=Owner,value=lab key=ReleaseId,value="$RELEASE_ID" \
    --query taskDefinition.taskDefinitionArn --output text)"
  log "revisao registrada: ${TD_ARN##*/}"
else
  log "ambiente=$ENV_NAME revisao EXATA ${TD_ARN##*/} (rollback/promocao por revisao)"
fi

# Le de volta o que a revisao vai rodar (vale tambem para o --task-definition-arn).
aws ecs describe-task-definition --task-definition "$TD_ARN" --query taskDefinition > "$WORK/td.json"
IMAGE="$(jq -r '.containerDefinitions[] | select(.name=="app") | .image' "$WORK/td.json")"
appenv() { jq -r --arg k "$1" '.containerDefinitions[] | select(.name=="app") | .environment[] | select(.name==$k) | .value' "$WORK/td.json"; }
DIGEST="$(appenv QC_IMAGE_DIGEST)"; RELEASE_ID="$(appenv QC_RELEASE_ID)"
BUNDLE_URI="$(appenv QC_DATA_BUNDLE_URI)"; BUNDLE_SHA="$(appenv QC_DATA_BUNDLE_SHA256)"
[[ "$DIGEST" =~ ^sha256: ]] || die "a revisao ${TD_ARN##*/} nao tem QC_IMAGE_DIGEST (e o template?); use um digest"

START=$(date +%s)
CUR_DESIRED="$(aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" --query 'services[0].desiredCount' --output text)"
if [ "$CUR_DESIRED" = "0" ]; then
  # Service desligado: troca a revisao ANTES de ligar. Com troca e desired 1
  # na mesma chamada, o ECS manteria o minimo saudavel da revisao antiga e
  # subiria uma task inutil do template (ou da revisao velha) junto.
  aws ecs update-service --cluster "$CLUSTER" --service "$SERVICE" --task-definition "$TD_ARN" \
    --desired-count 0 --query 'service.serviceName' --output text >/dev/null
fi
aws ecs update-service --cluster "$CLUSTER" --service "$SERVICE" --task-definition "$TD_ARN" \
  --desired-count 1 --query 'service.deployments[0].id' --output text >/dev/null
log "update-service enviado; esperando a task nova ficar saudavel (Neo4j + grafo + modelo levam alguns minutos)..."

TG_ARN="$(aws elbv2 describe-target-groups --names "$PROJECT-$( [ "$ENV_NAME" = live ] && echo live || echo cand )-tg" \
  --query 'TargetGroups[0].TargetGroupArn' --output text)"

# Espera ativa com tres condicoes. Sozinho, o "services-stable" nao basta: ele
# so diz que o ECS esta satisfeito, nao que o cliente recebe a versao nova.
READY_HTTP=000; ROLLOUT=""; SERVED=""
while :; do
  now=$(date +%s); el=$((now - START))
  [ "$el" -gt "$READY_TIMEOUT" ] && break
  ROLLOUT="$(aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" \
    --query "services[0].deployments[?taskDefinition=='$TD_ARN'].rolloutState | [0]" --output text)"
  [ "$ROLLOUT" = "FAILED" ] && break
  HEALTHY="$(aws elbv2 describe-target-health --target-group-arn "$TG_ARN" \
    --query "length(TargetHealthDescriptions[?TargetHealth.State=='healthy'])" --output text)"
  body="$(curl -s -m 10 -w '\n%{http_code}' "$URL/ready" || true)"
  READY_HTTP="$(tail -1 <<<"$body")"
  SERVED="$(sed '$d' <<<"$body" | jq -r '.versions.image_digest // .image_digest // empty' 2>/dev/null || true)"
  log "t=${el}s rollout=$ROLLOUT healthy_targets=$HEALTHY /ready=$READY_HTTP digest_servido=${SERVED:0:19}"
  if [ "$ROLLOUT" = "COMPLETED" ] && [ "$READY_HTTP" = "200" ] && [ "$SERVED" = "$DIGEST" ]; then break; fi
  sleep 10
done
COLD=$(( $(date +%s) - START ))

OK=false
[ "$ROLLOUT" = "COMPLETED" ] && [ "$READY_HTTP" = "200" ] && [ "$SERVED" = "$DIGEST" ] && OK=true
jq -n --arg env "$ENV_NAME" --arg svc "$SERVICE" --arg td "$TD_ARN" --arg img "$IMAGE" --arg dig "$DIGEST" \
  --arg rel "$RELEASE_ID" --arg buri "$BUNDLE_URI" --arg bsha "$BUNDLE_SHA" --arg http "$READY_HTTP" \
  --arg roll "$ROLLOUT" --arg url "$URL" --argjson cold "$COLD" --argjson ok "$OK" --arg served "$SERVED" \
  '{ok: $ok, environment: $env, service: $svc, task_definition_arn: ($td | sub(":[0-9]{12}:"; ":****:")),
    task_definition_revision: ($td | split("/") | last), image: ($img | sub("^[0-9]{12}"; "****")),
    image_digest: $dig, served_digest: $served, release_id: $rel, data_bundle_uri: $buri,
    data_bundle_sha256: $bsha, rollout_state: $roll, ready_http: ($http | tonumber? // 0),
    cold_start_s: $cold, url: $url}'

if [ "$OK" != true ]; then
  log "o ambiente $ENV_NAME NAO ficou pronto em ${COLD}s (rollout=$ROLLOUT, /ready=$READY_HTTP)."
  log "diagnostico: bash scripts/cloud/diagnose.sh $ENV_NAME"
  exit 1
fi
log "pronto em ${COLD}s: $URL"
