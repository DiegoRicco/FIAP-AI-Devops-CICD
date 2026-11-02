#!/usr/bin/env bash
# Preflight da Demo 5.1: confere, SEM mudar nada, se da para rodar o pipeline.
#
# Por que antes de tudo: um pipeline que falha no minuto 8 por credencial
# vencida ou bucket errado ensina a coisa errada ("o gate reprovou"). Aqui a
# falha de ambiente aparece em segundos e com a dica de correcao.
#
# Uso: scripts/preflight.sh [--bucket base-config-SEU-RM] [--runner]
#   --runner  tambem chama ../shared/runner/runner-preflight.sh (somente leitura)
# Saida: stdout = linhas chave=valor (a ultima e preflight=ok|fail); stderr = explicacoes.
# Exit 0 tudo ok, 1 algo bloqueia.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUCKET="${TF_STATE_BUCKET:-}"
CHECK_RUNNER=0
FAIL=0

while [ $# -gt 0 ]; do
  case "$1" in
    --bucket) BUCKET="$2"; shift 2 ;;
    --runner) CHECK_RUNNER=1; shift ;;
    *) echo "preflight: opcao desconhecida: $1" >&2; exit 2 ;;
  esac
done
# Sem --bucket, usa o que esta no backend do Terraform (o aluno ja trocou la).
if [ -z "$BUCKET" ]; then
  BUCKET=$(sed -n 's/^ *bucket *= *"\(.*\)".*/\1/p' "$ROOT/infra/state.tf" | head -1)
fi

ok()   { echo "$1=ok"; }
bad()  { echo "$1=fail"; echo "  -> $2" >&2; FAIL=1; }

echo "preflight: ferramentas" >&2
for tool in python3 terraform aws jq git; do
  if command -v "$tool" >/dev/null 2>&1; then ok "tool_$tool"; else bad "tool_$tool" "instale $tool (no runner ele vem do playbook do modulo 02)"; fi
done
if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
  ok "python_310_plus"
else
  bad "python_310_plus" "o core precisa de Python 3.10 ou mais novo"
fi
tf=$(terraform version -json 2>/dev/null | jq -r .terraform_version 2>/dev/null || echo "")
case "$tf" in
  1.1[0-9].*|1.[2-9][0-9].*) ok "terraform_use_lockfile" ;;
  *) bad "terraform_use_lockfile" "Terraform $tf: use_lockfile no backend S3 exige 1.10 ou mais novo" ;;
esac
for pkg in pytest checkov; do
  if command -v "$pkg" >/dev/null 2>&1; then ok "tool_$pkg"; else bad "tool_$pkg" "ative o venv do runner: source /opt/venv/bin/activate"; fi
done

echo "preflight: credenciais AWS" >&2
if ARN=$(aws sts get-caller-identity --query Arn --output text 2>/dev/null); then
  # Nao imprime o ARN: ele contem o ID da conta.
  ok "aws_credentials"
  case "$ARN" in *voclabs*|*LabRole*) ok "aws_academy_role" ;; *) echo "aws_academy_role=unknown"; echo "  -> nao parece Learner Lab; confira a conta" >&2 ;; esac
else
  bad "aws_credentials" "credencial vencida? No Learner Lab, 'Start Lab' e copie as credenciais de novo (a sessao dura 4 h)"
fi
REGION=$(aws configure get region 2>/dev/null || echo "${AWS_DEFAULT_REGION:-${AWS_REGION:-}}")
if [ "${REGION:-us-east-1}" = "us-east-1" ]; then ok "aws_region"; else bad "aws_region" "regiao $REGION; o lab usa us-east-1"; fi

echo "preflight: bucket do state ($BUCKET)" >&2
if [ "$BUCKET" = "base-config-SEU-RM" ] || [ -z "$BUCKET" ]; then
  bad "state_bucket" "troque base-config-SEU-RM pelo seu bucket em infra/state.tf (ou passe --bucket)"
elif aws s3api head-bucket --bucket "$BUCKET" >/dev/null 2>&1; then
  ok "state_bucket"
else
  bad "state_bucket" "bucket $BUCKET nao existe ou sem acesso; ele foi criado no setup do modulo 01"
fi

echo "preflight: servicos usados pela demo" >&2
aws lambda list-functions --max-items 1 >/dev/null 2>&1 && ok "lambda_api" || bad "lambda_api" "Lambda inacessivel nesta sessao"
aws apigatewayv2 get-apis --max-results 1 >/dev/null 2>&1 && ok "apigw_api" || bad "apigw_api" "API Gateway inacessivel nesta sessao"
aws dynamodb list-tables --max-items 1 >/dev/null 2>&1 && ok "dynamodb_api" || bad "dynamodb_api" "DynamoDB inacessivel nesta sessao"

echo "preflight: o que ja existe (para nao surpreender ninguem)" >&2
if aws lambda get-alias --function-name qc-assistant-api --name live >/dev/null 2>&1; then
  echo "assistant_deployed=yes"
  echo "assistant_live_version=$(aws lambda get-alias --function-name qc-assistant-api --name live --query FunctionVersion --output text)"
else
  echo "assistant_deployed=no"
fi

if [ "$CHECK_RUNNER" = 1 ]; then
  echo "preflight: runner (somente leitura)" >&2
  if bash "$ROOT/../shared/runner/runner-preflight.sh" >/dev/null; then ok "runner"; else bad "runner" "rode ../shared/runner/runner-preflight.sh para ver o diagnostico"; fi
fi

if [ "$FAIL" = 0 ]; then echo "preflight=ok"; exit 0; else echo "preflight=fail"; exit 1; fi
