#!/usr/bin/env bash
# Preflight da Demo 5.1: confere, SEM mudar nada, se da para seguir.
#
# Por que antes de tudo: um pipeline que falha no minuto 8 por credencial
# vencida ou bucket errado ensina a coisa errada ("o gate reprovou"). Aqui a
# falha de ambiente aparece em segundos e com a dica de correcao.
#
# Dois contextos, porque cada maquina tem um papel diferente:
#   codespaces  onde o aluno trabalha: aws, terraform, python, bucket, state.tf
#               e a rede fiap-lab. Checkov e TFLint so aparecem como INFO,
#               porque os gates rodam no runner, nao aqui.
#   runner      a EC2 do GitLab Runner: alem do basico, exige pytest, checkov
#               e tflint, que os jobs usam.
# O contexto e detectado sozinho (/opt/venv so existe no runner) ou forcado com --context.
#
# Uso: bash scripts/preflight.sh [--context codespaces|runner] [--bucket base-config-SEU-RM] [--runner]
#   --runner  tambem chama ../shared/runner/runner-preflight.sh (somente leitura)
# Saida: stdout = uma linha [PASS]/[FAIL]/[INFO] por item, a ultima e o veredito.
# Exit 0 sem nenhum [FAIL]; 1 se algo bloqueia.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUCKET="${TF_STATE_BUCKET:-}"
CONTEXT=""
CHECK_RUNNER=0
FAIL=0
PLACEHOLDER="base-config-SEU-RM"

while [ $# -gt 0 ]; do
  case "$1" in
    --bucket) BUCKET="$2"; shift 2 ;;
    --context) CONTEXT="$2"; shift 2 ;;
    --runner) CHECK_RUNNER=1; shift ;;
    *) echo "preflight: opcao desconhecida: $1" >&2; exit 2 ;;
  esac
done
if [ -z "$CONTEXT" ]; then
  if [ -d /opt/venv ]; then CONTEXT="runner"; else CONTEXT="codespaces"; fi
fi
case "$CONTEXT" in codespaces|runner) ;; *) echo "preflight: --context deve ser codespaces ou runner" >&2; exit 2 ;; esac

pass() { echo "[PASS] $1"; }
info() { echo "[INFO] $1"; }
fail() { echo "[FAIL] $1 -> $2"; FAIL=1; }

info "contexto: $CONTEXT"

# ---------------------------------------------------------------- ferramentas
for tool in python3 terraform aws jq git; do
  if command -v "$tool" >/dev/null 2>&1; then pass "ferramenta $tool"; else fail "ferramenta $tool" "instale $tool (no Codespaces ele vem do devcontainer; no runner, do playbook do modulo 02)"; fi
done
if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
  pass "python $(python3 -c 'import platform; print(platform.python_version())') (>= 3.10)"
else
  fail "python >= 3.10" "o core precisa de Python 3.10 ou mais novo"
fi
tf=$(terraform version -json 2>/dev/null | jq -r .terraform_version 2>/dev/null || echo "")
case "$tf" in
  1.1[0-9].*|1.[2-9][0-9].*) pass "terraform $tf (use_lockfile exige >= 1.10)" ;;
  *) fail "terraform >= 1.10" "Terraform '$tf': use_lockfile no backend S3 exige 1.10 ou mais novo" ;;
esac
for pkg in pytest checkov tflint; do
  found=""
  if command -v "$pkg" >/dev/null 2>&1; then found="yes"; elif [ -x "/opt/venv/bin/$pkg" ]; then found="yes"; fi
  if [ "$CONTEXT" = "runner" ]; then
    if [ -n "$found" ]; then pass "ferramenta $pkg"; else fail "ferramenta $pkg" "no runner: source /opt/venv/bin/activate (pytest/checkov) ou rode o playbook do modulo 02 (tflint)"; fi
  else
    # No Codespaces nao e obrigatorio: quem roda os gates e o runner.
    info "$pkg: roda no runner ($( [ -n "$found" ] && echo "tambem presente aqui" || echo "ausente aqui, e tudo bem"))"
  fi
done

# ----------------------------------------------------------------- credencial
if ARN=$(aws sts get-caller-identity --query Arn --output text 2>/dev/null); then
  # Nao imprime o ARN: ele contem o ID da conta.
  pass "credenciais AWS validas"
  case "$ARN" in *voclabs*|*LabRole*) pass "conta do AWS Academy (voclabs/LabRole)" ;; *) info "nao parece Learner Lab; confira a conta" ;; esac
else
  fail "credenciais AWS" "ExpiredToken ou sem credencial: no Learner Lab, Start Lab e copie as credenciais de novo (a sessao dura 4 h)"
fi
REGION=$(aws configure get region 2>/dev/null || true)
REGION="${REGION:-${AWS_DEFAULT_REGION:-${AWS_REGION:-us-east-1}}}"
if [ "$REGION" = "us-east-1" ]; then pass "regiao us-east-1"; else fail "regiao" "regiao $REGION; o lab usa us-east-1 (export AWS_DEFAULT_REGION=us-east-1)"; fi

# --------------------------------------------------------------------- bucket
# Ordem: --bucket > TF_STATE_BUCKET > infra/state.tf > descoberta na conta.
STATE_TF_BUCKET=$(sed -n 's/^ *bucket *= *"\(.*\)".*/\1/p' "$ROOT/infra/state.tf" | head -1)
if [ -z "$BUCKET" ] && [ "$STATE_TF_BUCKET" != "$PLACEHOLDER" ]; then BUCKET="$STATE_TF_BUCKET"; fi
if [ -z "$BUCKET" ]; then
  BUCKET=$(aws s3 ls 2>/dev/null | awk '{print $3}' | grep '^base-config' | head -1 || true)
fi
if [ -z "$BUCKET" ]; then
  fail "bucket do state" "nenhum bucket base-config- na conta: ele e criado no setup do modulo 01 (ou passe --bucket)"
elif aws s3api head-bucket --bucket "$BUCKET" >/dev/null 2>&1; then
  pass "bucket do state $BUCKET"
else
  fail "bucket do state $BUCKET" "nao existe ou sem acesso; confira o nome (o mesmo do setup do modulo 01)"
fi
# No Codespaces o aluno roda terraform output localmente: o state.tf precisa
# apontar para o bucket dele. No runner o pipeline passa -backend-config.
if [ "$CONTEXT" = "codespaces" ]; then
  if [ "$STATE_TF_BUCKET" = "$PLACEHOLDER" ]; then
    fail "infra/state.tf" "ainda tem $PLACEHOLDER: rode o passo 3 (troca pelo seu bucket)"
  else
    pass "infra/state.tf aponta para $STATE_TF_BUCKET"
  fi
fi

# ------------------------------------------------------------------- servicos
aws lambda list-functions --max-items 1 >/dev/null 2>&1 && pass "Lambda acessivel" || fail "Lambda" "inacessivel nesta sessao (credencial ou regiao)"
aws apigatewayv2 get-apis --max-results 1 >/dev/null 2>&1 && pass "API Gateway acessivel" || fail "API Gateway" "inacessivel nesta sessao"
aws dynamodb list-tables --max-items 1 >/dev/null 2>&1 && pass "DynamoDB acessivel" || fail "DynamoDB" "inacessivel nesta sessao"
# A Lambda nao usa VPC, mas o runner vive na fiap-lab: sem ela, nao ha pipeline.
VPC=$(aws ec2 describe-vpcs --filters Name=tag:Name,Values=fiap-lab --query 'Vpcs[0].VpcId' --output text 2>/dev/null || echo "")
if [ -n "$VPC" ] && [ "$VPC" != "None" ]; then pass "rede fiap-lab existe"; else fail "rede fiap-lab nao encontrada" "a rede do modulo 01 sumiu (lab resetado): rode bash /workspaces/FIAP-AI-Devops-CICD/01-Terraform/scripts/recriar-rede.sh"; fi

# ------------------------------------------------- o que ja existe (so leitura)
if LIVE=$(aws lambda get-alias --function-name qc-assistant-api --name live --query FunctionVersion --output text 2>/dev/null); then
  info "assistente ja implantado: alias live na versao $LIVE"
else
  info "assistente ainda nao implantado (normal antes do primeiro pipeline)"
fi

if [ "$CHECK_RUNNER" = 1 ]; then
  if bash "$ROOT/../shared/runner/runner-preflight.sh" >/dev/null; then pass "runner (runner-preflight.sh)"; else fail "runner" "rode ../shared/runner/runner-preflight.sh para ver o diagnostico"; fi
fi

if [ "$FAIL" = 0 ]; then echo "[PASS] preflight ok ($CONTEXT)"; exit 0; else echo "[FAIL] preflight: corrija os itens [FAIL] acima"; exit 1; fi
