#!/usr/bin/env bash
# Remove SO os recursos da Demo 5.1 (prefixo qc-assistant-). Padrao: --dry-run.
#
# O que NUNCA e tocado: a VPC fiap-lab (compartilhada pelas outras demos), a
# API do modulo 03 (quantum-ai-*), o runner e o bucket de state.
# Por que nao rodar no fim da aula por padrao: a Demo 5.2 pode usar o release
# publicado (bootstrap --from-live). Destrua so quando terminar as duas.
#
# Uso:
#   scripts/cleanup.sh                       # mostra o plano de destruicao, nao muda nada
#   scripts/cleanup.sh --apply --bucket base-config-SEU-RM
#                                            # destroi os recursos da demo e o registro de releases
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APPLY=0
BUCKET="${TF_STATE_BUCKET:-}"
log() { echo "cleanup: $*" >&2; }

while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY=1; shift ;;
    --dry-run) APPLY=0; shift ;;
    --bucket) BUCKET="$2"; shift 2 ;;
    *) log "opcao desconhecida: $1"; exit 2 ;;
  esac
done

cd "$ROOT"
# O destroy precisa de um bundle para avaliar a configuracao; nao muda nada na AWS.
[ -f dist/bundle.zip ] || python3 release/build_bundle.py >/dev/null
cd infra
INIT_ARGS=()
[ -n "$BUCKET" ] && INIT_ARGS+=("-backend-config=bucket=$BUCKET")
# Forma segura de expandir array vazio com set -u (bash 3.2 do macOS reclama).
terraform init -input=false ${INIT_ARGS[@]+"${INIT_ARGS[@]}"} >&2

log "recursos que seriam destruidos (todos com prefixo qc-assistant-):"
# "|| true" so no filtro de exibicao: o grep sai com 1 quando nao ha nada para
# destruir. O codigo do terraform plan continua valendo (pipefail).
terraform plan -destroy -input=false -no-color -out=destroy.tfplan > destroy-plan.txt
grep -E '^  # |Plan:|No changes' destroy-plan.txt >&2 || true
rm -f destroy-plan.txt

if [ "$APPLY" = 0 ]; then
  rm -f destroy.tfplan
  log "--dry-run: nada foi destruido. Para destruir: scripts/cleanup.sh --apply --bucket <seu-bucket>"
  exit 0
fi

read -r -p "Digite 'destruir qc-assistant' para confirmar: " answer
[ "$answer" = "destruir qc-assistant" ] || { log "confirmacao diferente; nada foi destruido"; rm -f destroy.tfplan; exit 1; }
terraform apply -input=false destroy.tfplan
rm -f destroy.tfplan
if [ -n "$BUCKET" ]; then
  log "apagando o registro de releases s3://$BUCKET/quantum-assistant/releases/ (o state continua)"
  aws s3 rm --recursive "s3://$BUCKET/quantum-assistant/releases/" >&2
fi
log "pronto. VPC fiap-lab, API do modulo 03 e runner nao foram tocados."
