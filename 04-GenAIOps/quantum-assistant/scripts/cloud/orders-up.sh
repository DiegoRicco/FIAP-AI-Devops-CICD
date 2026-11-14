#!/usr/bin/env bash
# Implanta a API de pedidos (Lambda qc-assistant-orders + DynamoDB + API
# Gateway) com o MESMO Terraform da Demo 04 (pasta infra/) e grava a URL base
# no SSM /qc-assistant/orders-base-url, de onde a task do ECS a le.
#
# Por que reaproveitar o infra/ em vez de um Terraform novo: o status do
# pedido vem do sistema de origem (DynamoDB atras de uma API autenticada),
# nunca do corpus nem do grafo. E o mesmo contrato das aulas anteriores.
#
# O Terraform roda numa COPIA em diretorio temporario com
# -backend-config=bucket=...: o state.tf versionado nao e editado e o projeto
# nao ganha .terraform. Key do state: quantum-assistant/terraform.tfstate.
# Idempotente: com a API no ar, o apply nao muda nada.
#
# Uso: bash scripts/cloud/orders-up.sh [--bucket base-config-SEU-RM]
# Saida: stdout = URL base da API; stderr = progresso e smoke.
set -euo pipefail

export AWS_PAGER=""
AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
export AWS_REGION
PROJECT="${PROJECT:-qc-assistant}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BUCKET="${TF_STATE_BUCKET:-}"
while [ $# -gt 0 ]; do
  case "$1" in --bucket) BUCKET="$2"; shift 2 ;; *) echo "orders-up: opcao desconhecida: $1" >&2; exit 2 ;; esac
done
log() { echo "orders-up: $*" >&2; }
[ -n "$BUCKET" ] || BUCKET="$(aws s3api list-buckets --query 'Buckets[].Name' --output text | tr '\t' '\n' | grep '^base-config-' | head -1)"
[ -n "$BUCKET" ] || { log "ERRO: nenhum bucket base-config-* (rode make bootstrap RM=<seu-rm>)"; exit 1; }

WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/infra"
cp "$ROOT"/infra/*.tf "$WORK/infra/"
cp -R "$ROOT/orders" "$WORK/orders"
log "empacotando o bundle da Lambda (release/build_bundle.py)"
python3 "$ROOT/release/build_bundle.py" --out "$WORK/dist" >/dev/null

log "terraform init/apply da API de pedidos (state s3://$BUCKET/quantum-assistant/terraform.tfstate)"
terraform -chdir="$WORK/infra" init -input=false -backend-config="bucket=$BUCKET" >&2
terraform -chdir="$WORK/infra" apply -input=false -auto-approve >&2
BASE="$(terraform -chdir="$WORK/infra" output -raw assistant_url)"

# Smoke autenticado: dono ve o pedido, outro cliente e negado, sem token e 401.
code() { curl -s -o /dev/null -w '%{http_code}' -m 15 "$@"; }
c1="$(code -H 'Authorization: Bearer lab-token-ana' "$BASE/orders/Q-1001")"
# A primeira chamada pode pegar o cold start da Lambda: uma segunda tentativa.
[ "$c1" = 200 ] || { sleep 3; c1="$(code -H 'Authorization: Bearer lab-token-ana' "$BASE/orders/Q-1001")"; }
c2="$(code -H 'Authorization: Bearer lab-token-ana' "$BASE/orders/Q-2001")"
c3="$(code "$BASE/orders/Q-1001")"
log "smoke: Q-1001 (dona)=$c1 esperado 200 | Q-2001 (outro cliente)=$c2 esperado 403 | sem token=$c3 esperado 401"
[ "$c1" = 200 ] && [ "$c2" = 403 ] && [ "$c3" = 401 ] || { log "ERRO: a API de pedidos nao respondeu como esperado"; exit 1; }

aws ssm put-parameter --name "/$PROJECT/orders-base-url" --type String --value "$BASE" --overwrite >/dev/null
aws ssm add-tags-to-resource --resource-type Parameter --resource-id "/$PROJECT/orders-base-url" \
  --tags Key=Project,Value=quantum-commerce Key=Lab,Value=05-1 Key=Owner,Value=lab >/dev/null
log "URL gravada em /$PROJECT/orders-base-url"
echo "$BASE"
