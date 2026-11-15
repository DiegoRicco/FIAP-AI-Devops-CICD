#!/usr/bin/env bash
# Remove SO o runtime ECS das Aulas 5.1/5.2 (state quantum-assistant/ecs/).
# Padrao: --dry-run (mostra o que seria destruido e nao muda nada).
#
# O que NUNCA e tocado, por construcao:
#   - a VPC fiap-lab e as subnets (este Terraform so as LE por data source);
#   - o GitLab Runner e o SG dele (tambem so lidos);
#   - o bucket base-config-* e os states de outras keys;
#   - a API de pedidos (state quantum-assistant/terraform.tfstate);
#   - a observabilidade da 5.2 (state quantum-assistant/obs/terraform.tfstate):
#     destrua-a antes (bash ops/cloud/obs.sh destroy --apply), porque a regra
#     /grafana dela fica pendurada no listener deste ALB; o --apply recusa se a
#     EC2 qc-assistant-obs ainda existir.
# O que sai alem do Terraform (so com --apply): as revisoes de task definition
# registradas pelo deploy.sh (familias qc-assistant-live/-candidate/-proof) e,
# com --with-secrets, os SecureString de scripts/cloud/secrets.sh.
#
# Uso:
#   bash scripts/cloud/cleanup.sh                     # dry-run
#   bash scripts/cloud/cleanup.sh --apply [--with-secrets] [--bucket base-config-SEU-RM]
# Saida: stdout = lista do que seria/foi removido; stderr = progresso.
set -euo pipefail

export AWS_PAGER=""
AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
export AWS_REGION
PROJECT="${PROJECT:-qc-assistant}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
APPLY=0; WITH_SECRETS=0; BUCKET="${TF_STATE_BUCKET:-}"
STATE_KEY="quantum-assistant/ecs/terraform.tfstate"
while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY=1; shift ;;
    --dry-run) APPLY=0; shift ;;
    --with-secrets) WITH_SECRETS=1; shift ;;
    --bucket) BUCKET="$2"; shift 2 ;;
    *) echo "cleanup: opcao desconhecida: $1" >&2; exit 2 ;;
  esac
done
log() { echo "cleanup: $*" >&2; }
[ -n "$BUCKET" ] || BUCKET="$(aws s3api list-buckets --query 'Buckets[].Name' --output text | tr '\t' '\n' | grep '^base-config-' | head -1)"
[ -n "$BUCKET" ] || { log "ERRO: nenhum bucket base-config-*"; exit 1; }

# A regra /grafana da 5.2 mora no listener deste ALB: destruir o runtime com a
# EC2 de observabilidade de pe deixa o state de infra-obs/ apontando para um
# listener que nao existe mais, e o destroy dele passa a falhar.
OBS_EC2="$(aws ec2 describe-instances --filters "Name=tag:Name,Values=$PROJECT-obs" \
  "Name=instance-state-name,Values=pending,running,stopping,stopped" \
  --query 'Reservations[].Instances[].InstanceId' --output text | awk '{print $1}')"
OBS_MSG="a EC2 $PROJECT-obs ($OBS_EC2) ainda existe: rode antes bash ops/cloud/obs.sh destroy --apply"

[ "$APPLY" = 0 ] || [ -z "$OBS_EC2" ] || { log "RECUSADO: $OBS_MSG; nada foi destruido"; exit 1; }

# Copia de trabalho fora do repositorio: o init nao deixa .terraform no projeto.
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
cp -R "$ROOT/infra-ecs/." "$WORK/"
rm -rf "$WORK/.terraform"
terraform -chdir="$WORK" init -input=false -backend-config="bucket=$BUCKET" -backend-config="key=$STATE_KEY" >&2

log "plano de destruicao do state s3://$BUCKET/$STATE_KEY:"
terraform -chdir="$WORK" plan -destroy -input=false -no-color -out=destroy.tfplan > "$WORK/plan.txt"
terraform -chdir="$WORK" show -json destroy.tfplan > "$WORK/plan.json"

# Trava de seguranca: o plano so pode apagar recursos deste lab. Qualquer
# coisa fora da lista (ou um data source virando recurso por engano) aborta.
ALLOWED='^(aws_ecr_repository|aws_ecr_lifecycle_policy|aws_ecs_cluster|aws_ecs_cluster_capacity_providers|aws_ecs_service|aws_ecs_task_definition|aws_cloudwatch_log_group|aws_lb|aws_lb_listener|aws_lb_listener_rule|aws_lb_target_group|aws_security_group|aws_vpc_security_group_ingress_rule|aws_vpc_security_group_egress_rule|aws_ssm_parameter)$'
jq -r '.resource_changes[] | select(.change.actions | index("delete")) | "\(.type)\t\(.address)\t\(.change.before.name // .change.before.id // "")"' "$WORK/plan.json" > "$WORK/deletes.tsv"
BAD="$(awk -F'\t' -v re="$ALLOWED" '$1 !~ re' "$WORK/deletes.tsv")"
if [ -n "$BAD" ]; then
  log "ABORTADO: o plano apagaria recurso fora do runtime ECS:"; echo "$BAD" >&2; exit 1
fi
if jq -e '[.resource_changes[] | select(.change.before.vpc_id? and (.type == "aws_vpc" or .type == "aws_subnet"))] | length > 0' "$WORK/plan.json" >/dev/null; then
  log "ABORTADO: o plano toca VPC/subnet"; exit 1
fi
if grep -qiE 'fiap-lab|gitlab-runner|base-config' <(cut -f3 "$WORK/deletes.tsv"); then
  log "ABORTADO: nome protegido (fiap-lab/runner/bucket) no plano"; exit 1
fi

echo "# terraform (state $STATE_KEY): $(wc -l < "$WORK/deletes.tsv" | tr -d ' ') recursos"
awk -F'\t' '{print "destroy\t" $2 "\t" $3}' "$WORK/deletes.tsv" | sed -E 's/[0-9]{12}/****/g'
echo "# fora do terraform"
for fam in "$PROJECT-live" "$PROJECT-candidate" "$PROJECT-proof"; do
  n="$(aws ecs list-task-definitions --family-prefix "$fam" --status ACTIVE --query 'length(taskDefinitionArns)' --output text)"
  printf '%s\t%s\t%s\n' "deregister+delete" "task-definition family $fam" "$n revisoes ativas"
done
if aws ecr describe-repositories --repository-names "$PROJECT-proof" >/dev/null 2>&1; then
  printf '%s\t%s\t%s\n' "delete" "ecr $PROJECT-proof" "repositorio descartavel da prova Fargate"
fi
if [ "$WITH_SECRETS" = 1 ]; then
  for env in live candidate; do for p in neo4j-password neo4j-auth admin-hmac; do printf '%s\t%s\t%s\n' "delete" "ssm /$PROJECT/$env/$p" "SecureString"; done; done
else
  printf '%s\t%s\t%s\n' "keep" "ssm /$PROJECT/<env>/{neo4j-password,neo4j-auth,admin-hmac}" "use --with-secrets para apagar"
fi
printf '%s\t%s\t%s\n' "keep" "VPC fiap-lab, runner, bucket $BUCKET, API de pedidos, observabilidade 5.2" "nunca tocados"

if [ "$APPLY" = 0 ]; then
  [ -z "$OBS_EC2" ] || log "AVISO: $OBS_MSG (o --apply vai recusar)"
  log "--dry-run: nada foi destruido. Para destruir: bash scripts/cloud/cleanup.sh --apply"
  exit 0
fi

read -r -p "Digite 'destruir runtime ecs' para confirmar: " answer
[ "$answer" = "destruir runtime ecs" ] || { log "confirmacao diferente; nada foi destruido"; exit 1; }
# Desliga as tasks antes: o destroy do service espera elas pararem.
for env in live candidate; do
  aws ecs update-service --cluster "$PROJECT" --service "$PROJECT-$env" --desired-count 0 >/dev/null 2>&1 || true
done
terraform -chdir="$WORK" apply -input=false destroy.tfplan >&2
for fam in "$PROJECT-live" "$PROJECT-candidate" "$PROJECT-proof"; do
  arns="$(aws ecs list-task-definitions --family-prefix "$fam" --status ACTIVE --query 'taskDefinitionArns' --output text)"
  for a in $arns; do aws ecs deregister-task-definition --task-definition "$a" >/dev/null; done
  arns="$(aws ecs list-task-definitions --family-prefix "$fam" --status INACTIVE --query 'taskDefinitionArns' --output text)"
  # delete-task-definitions aceita ate 10 por chamada.
  # shellcheck disable=SC2086
  [ -n "$arns" ] && printf '%s\n' $arns | xargs -n 10 aws ecs delete-task-definitions --query 'failures' --output text --task-definitions >/dev/null 2>&1 || true
done
aws ecr delete-repository --repository-name "$PROJECT-proof" --force >/dev/null 2>&1 || true
if [ "$WITH_SECRETS" = 1 ]; then
  for env in live candidate; do
    aws ssm delete-parameters --names "/$PROJECT/$env/neo4j-password" "/$PROJECT/$env/neo4j-auth" "/$PROJECT/$env/admin-hmac" >/dev/null
  done
fi
log "pronto. VPC fiap-lab, runner, bucket, API de pedidos e observabilidade nao foram tocados."
