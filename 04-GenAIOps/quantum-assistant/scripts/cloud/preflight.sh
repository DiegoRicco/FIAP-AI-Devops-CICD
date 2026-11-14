#!/usr/bin/env bash
# Preflight do runtime ECS (Aulas 5.1 e 5.2): confere, SEM mudar nada, se a
# conta AWS aguenta o laboratorio antes de criar qualquer recurso.
#
# Por que antes de tudo: um apply que falha no minuto 6 por credencial vencida,
# rede apagada ou runner desligado ensina a coisa errada. Aqui cada problema
# aparece em segundos, com a dica de correcao.
#
# Serve para o aluno (make preflight) e para o pipeline (job no runner): os
# dois usam a mesma lista de checagens.
#
# Uso: bash scripts/cloud/preflight.sh [--bucket base-config-SEU-RM]
# Saida: stdout = uma linha [PASS]/[FAIL]/[INFO] por item; a ultima e o veredito.
#        stderr = progresso. Exit 0 sem nenhum [FAIL]; 1 se algo bloqueia.
set -uo pipefail

export AWS_PAGER=""
AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-$(aws configure get region 2>/dev/null || true)}}"
AWS_REGION="${AWS_REGION:-us-east-1}"
export AWS_REGION
BUCKET="${TF_STATE_BUCKET:-}"
PROJECT="${PROJECT:-qc-assistant}"
RUNNER_TAG="${RUNNER_TAG:-quantum-ai-gitlab-runner}"
COURSE_REPO="${COURSE_REPO:-/workspaces/FIAP-AI-Devops-CICD}"
FAIL=0

while [ $# -gt 0 ]; do
  case "$1" in
    --bucket) BUCKET="$2"; shift 2 ;;
    *) echo "preflight: opcao desconhecida: $1" >&2; exit 2 ;;
  esac
done

pass() { echo "[PASS] $1"; }
info() { echo "[INFO] $1"; }
fail() { echo "[FAIL] $1 -> $2"; FAIL=1; }
step() { echo "preflight: $*" >&2; }
q() { aws "$@" --region "$AWS_REGION" --output text 2>/dev/null; }

# ----------------------------------------------------------------- ferramentas
step "ferramentas"
for tool in aws terraform jq curl; do
  if command -v "$tool" >/dev/null 2>&1; then pass "ferramenta $tool"; else fail "ferramenta $tool" "instale $tool (no Codespaces ele vem do devcontainer)"; fi
done

# ----------------------------------------------------------------- credencial
# Falha rapida: sem credencial, todo o resto daria erro confuso.
step "credenciais (sts get-caller-identity)"
if ! ARN="$(aws sts get-caller-identity --query Arn --output text 2>&1)"; then
  case "$ARN" in
    *ExpiredToken*|*expired*) fail "credenciais AWS" "expiraram: no Learner Lab, Start Lab e copie AWS Details para ~/.aws/credentials" ;;
    *) fail "credenciais AWS" "sem credencial valida: cole o bloco de AWS Details do Learner Lab em ~/.aws/credentials" ;;
  esac
  echo "[FAIL] preflight: corrija as credenciais e rode de novo"
  exit 1
fi
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
# So os 4 ultimos digitos: o ID completo da conta nao vai para log nem print.
pass "credenciais AWS validas (conta ****${ACCOUNT: -4})"
case "$ARN" in *voclabs*|*LabRole*) pass "conta do AWS Academy (voclabs/LabRole)" ;; *) info "nao parece Learner Lab; confira a conta" ;; esac
if [ "$AWS_REGION" = "us-east-1" ]; then pass "regiao us-east-1"; else fail "regiao" "regiao $AWS_REGION; o lab usa us-east-1 (export AWS_REGION=us-east-1)"; fi

# ---------------------------------------------------------------------- bucket
step "bucket do state"
if [ -z "$BUCKET" ]; then BUCKET="$(q s3api list-buckets --query 'Buckets[].Name' | tr '\t' '\n' | grep '^base-config-' | head -1)"; fi
if [ -z "$BUCKET" ]; then
  fail "bucket do state" "nenhum bucket base-config-* na conta: rode make bootstrap RM=<seu-rm>"
elif aws s3api head-bucket --bucket "$BUCKET" >/dev/null 2>&1; then
  pass "bucket do state $BUCKET"
else
  fail "bucket do state $BUCKET" "nao existe ou sem acesso; confira o nome (make bootstrap RM=<seu-rm> cria)"
fi

# -------------------------------------------------------------------- rede
step "rede fiap-lab"
VPC="$(q ec2 describe-vpcs --filters Name=tag:Name,Values=fiap-lab --query 'Vpcs[0].VpcId')"
if [ -n "$VPC" ] && [ "$VPC" != "None" ]; then
  pass "rede fiap-lab ($VPC)"
  # use1-az3 nao tem Fargate: contamos so as subnets que servem.
  NSUB="$(q ec2 describe-subnets --filters Name=vpc-id,Values="$VPC" Name=tag:Tier,Values=Public \
    --query "length(Subnets[?AvailabilityZoneId!='use1-az3' && MapPublicIpOnLaunch])")"
  if [ "${NSUB:-0}" -ge 2 ]; then pass "subnets publicas para Fargate e ALB: $NSUB"; else fail "subnets publicas" "o ALB precisa de 2 zonas; recrie a rede: bash $COURSE_REPO/01-Terraform/scripts/recriar-rede.sh"; fi
else
  fail "rede fiap-lab" "a rede do Modulo 01 sumiu (lab resetado): bash $COURSE_REPO/01-Terraform/scripts/recriar-rede.sh (ou make bootstrap)"
fi

# --------------------------------------------------------------------- IAM
step "LabRole (execution role e task role do ECS)"
TRUST="$(q iam get-role --role-name LabRole --query 'Role.AssumeRolePolicyDocument.Statement[].Principal.Service[]')"
case "$TRUST" in
  *ecs-tasks.amazonaws.com*) pass "LabRole confia em ecs-tasks (serve de execution/task role)" ;;
  "") fail "LabRole" "nao encontrada ou sem permissao de leitura; o lab nao cria role nova" ;;
  *) fail "LabRole" "nao confia em ecs-tasks.amazonaws.com; o Fargate nao conseguiria usa-la" ;;
esac

# -------------------------------------------------------- ECS, ECR, ELB, quotas
step "servicos ECS, ECR, ELB e quota do Fargate"
aws ecs list-clusters --region "$AWS_REGION" --max-items 1 >/dev/null 2>&1 && pass "ECS acessivel" || fail "ECS" "inacessivel nesta sessao"
aws ecr describe-repositories --region "$AWS_REGION" --max-items 1 >/dev/null 2>&1 && pass "ECR acessivel" || fail "ECR" "inacessivel nesta sessao"
aws elbv2 describe-load-balancers --region "$AWS_REGION" --page-size 1 >/dev/null 2>&1 && pass "Elastic Load Balancing acessivel" || fail "ELB" "inacessivel nesta sessao"
# GetServiceQuota e negado no Learner Lab; ListServiceQuotas (o mesmo dado) nao.
VCPU="$(q service-quotas list-service-quotas --service-code fargate --query "Quotas[?QuotaCode=='L-3032A538'].Value | [0]")"
if [ -n "$VCPU" ] && [ "$VCPU" != "None" ]; then
  # Cada ambiente usa 1 vCPU; live + candidate = 2.
  if awk -v v="$VCPU" 'BEGIN{exit !(v >= 2)}'; then pass "quota Fargate On-Demand vCPU: $VCPU (o lab usa 2)"; else fail "quota Fargate" "so $VCPU vCPU; o lab precisa de 2"; fi
else
  info "quota do Fargate nao pode ser lida nesta conta (seguimos; o deploy prova)"
fi
NALB="$(q elbv2 describe-load-balancers --query 'length(LoadBalancers)')"
info "load balancers ja existentes na conta: ${NALB:-0}"

# ------------------------------------------------------------------- runner
step "GitLab Runner (EC2 $RUNNER_TAG)"
RUNNER="$(q ec2 describe-instances --filters "Name=tag:Name,Values=$RUNNER_TAG" Name=instance-state-name,Values=running,stopped \
  --query 'Reservations[].Instances[].[InstanceId,State.Name]')"
case "$RUNNER" in
  "") fail "runner" "nenhuma EC2 $RUNNER_TAG: make runner (usa $COURSE_REPO/04-GenAIOps/shared/runner)" ;;
  *running*)
    RID="$(awk '{print $1; exit}' <<<"$RUNNER")"
    PING="$(q ssm describe-instance-information --filters "Key=InstanceIds,Values=$RID" --query 'InstanceInformationList[0].PingStatus')"
    if [ "$PING" = "Online" ]; then pass "runner $RID ligado e SSM Online"; else fail "runner SSM" "agente SSM $PING; espere 1 min ou rode make runner"; fi ;;
  *stopped*) fail "runner" "EC2 desligada: make runner (liga e confere)" ;;
esac
if [ -n "${RID:-}" ]; then
  RSG="$(q ec2 describe-instances --instance-ids "$RID" --query 'Reservations[0].Instances[0].SecurityGroups[0].GroupName')"
  [ "$RSG" = "$RUNNER_TAG" ] && pass "SG do runner ($RSG) existe (a porta admin 8001 so aceita ele)" || fail "SG do runner" "SG '$RSG' diferente de $RUNNER_TAG; o infra-ecs procura pelo nome"
fi

# --------------------------------------------------------- dependencias do app
step "API de pedidos e parametros do SSM"
ORDERS="$(q ssm get-parameter --name "/$PROJECT/orders-base-url" --query Parameter.Value)"
if [ -n "$ORDERS" ] && [ "$ORDERS" != "None" ]; then
  code="$(curl -s -o /dev/null -w '%{http_code}' -m 10 -H 'Authorization: Bearer lab-token-ana' "$ORDERS/orders/Q-1001" || echo 000)"
  if [ "$code" = "200" ]; then pass "API de pedidos responde (GET /orders/Q-1001 = 200)"; else fail "API de pedidos" "GET /orders/Q-1001 = $code; rode make orders-up"; fi
else
  fail "API de pedidos" "parametro /$PROJECT/orders-base-url ausente: rode make orders-up"
fi
for env in live candidate; do
  for p in neo4j-password neo4j-auth admin-hmac; do
    n="$(q ssm describe-parameters --parameter-filters "Key=Name,Values=/$PROJECT/$env/$p" --query 'length(Parameters)')"
    if [ "$n" = "1" ]; then pass "segredo /$PROJECT/$env/$p existe (valor nao lido)"; else fail "segredo /$PROJECT/$env/$p" "ausente: rode make secrets"; fi
  done
done

# ---------------------------------------------- EC2 (fallback e observabilidade)
OFF="$(q ec2 describe-instance-type-offerings --location-type availability-zone --filters Name=instance-type,Values=t3.medium --query 'length(InstanceTypeOfferings)')"
[ "${OFF:-0}" -ge 1 ] && pass "t3.medium disponivel em $OFF zonas (observabilidade da 5.2)" || info "t3.medium nao ofertada nesta regiao"

if [ "$FAIL" = 0 ]; then echo "[PASS] preflight ok"; exit 0; else echo "[FAIL] preflight: corrija os itens [FAIL] acima"; exit 1; fi
