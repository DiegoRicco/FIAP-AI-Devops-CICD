#!/usr/bin/env bash
# Cria as senhas do runtime ECS no SSM Parameter Store, como SecureString.
#
# Por que com a AWS CLI e nao com o Terraform: um recurso Terraform com o valor
# da senha grava esse valor no state (o arquivo no S3), em texto. Aqui a senha
# nasce aleatoria, vai direto para o SSM e nunca aparece na tela, no log nem no
# state. A task do ECS le o valor na hora de subir ("secrets" da task definition).
#
# Parametros criados, por ambiente (live e candidate):
#   /qc-assistant/<env>/neo4j-password   senha do usuario neo4j (o app usa)
#   /qc-assistant/<env>/neo4j-auth       "neo4j/<mesma senha>" (formato que a imagem do Neo4j espera)
#   /qc-assistant/<env>/admin-hmac       chave HMAC da porta admin (incidente da Demo 5.2)
#
# Idempotente: parametro que ja existe NAO e trocado (trocar a senha com a task
# rodando quebraria a conexao app -> Neo4j no proximo restart de so um deles).
# Para girar de proposito: --rotate (depois rode o deploy de novo).
#
# Uso: bash scripts/cloud/secrets.sh [--rotate]
# Saida: stdout = uma linha por parametro "<nome> created|exists|rotated" (sem valor).
set -euo pipefail

export AWS_PAGER=""
AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
PROJECT="${PROJECT:-qc-assistant}"
ROTATE=0
case "${1:-}" in
  --rotate) ROTATE=1 ;;
  "") ;;
  *) echo "secrets: opcao desconhecida: $1 (use --rotate ou nada)" >&2; exit 2 ;;
esac

log() { echo "secrets: $*" >&2; }

# 32 bytes aleatorios em hex: so [0-9a-f], sem caractere que precise de escape
# no NEO4J_AUTH ("neo4j/<senha>") nem no shell.
random_hex() { od -An -N32 -tx1 /dev/urandom | tr -d ' \n'; }

exists() {
  aws ssm describe-parameters --region "$AWS_REGION" \
    --parameter-filters "Key=Name,Values=$1" --query 'length(Parameters)' --output text
}

put_secure() {
  # O valor vai por arquivo temporario (file://) e nao por argumento: argumento
  # de linha de comando aparece no 'ps' de quem estiver na mesma maquina.
  local name="$1" value="$2" overwrite="$3" tmp
  tmp="$(mktemp)"
  chmod 600 "$tmp"
  printf '%s' "$value" > "$tmp"
  aws ssm put-parameter --region "$AWS_REGION" --name "$name" --type SecureString \
    --value "file://$tmp" $overwrite >/dev/null
  rm -f "$tmp"
  # Tags so funcionam em parametro novo pelo put-parameter; add-tags vale nos dois casos.
  aws ssm add-tags-to-resource --region "$AWS_REGION" --resource-type Parameter --resource-id "$name" \
    --tags Key=Project,Value=quantum-commerce Key=Lab,Value=05-1 Key=Owner,Value=lab >/dev/null
}

aws sts get-caller-identity >/dev/null 2>&1 || { log "credencial AWS invalida ou expirada (Start Lab e copie de novo)"; exit 1; }

for env in live candidate; do
  base="/$PROJECT/$env"
  if [ "$(exists "$base/neo4j-password")" = "1" ] && [ "$(exists "$base/neo4j-auth")" = "1" ] && [ "$ROTATE" = 0 ]; then
    echo "$base/neo4j-password exists"
    echo "$base/neo4j-auth exists"
  else
    state=created; ow=""
    [ "$ROTATE" = 1 ] && { state=rotated; ow="--overwrite"; }
    # Se so um dos dois existir (execucao interrompida), recria o par junto:
    # a senha do app e a do Neo4j precisam ser a mesma.
    [ "$(exists "$base/neo4j-password")" = "1" ] && ow="--overwrite"
    [ "$(exists "$base/neo4j-auth")" = "1" ] && ow="--overwrite"
    pw="$(random_hex)"
    put_secure "$base/neo4j-password" "$pw" "$ow"
    put_secure "$base/neo4j-auth" "neo4j/$pw" "$ow"
    unset pw
    echo "$base/neo4j-password $state"
    echo "$base/neo4j-auth $state"
  fi
  if [ "$(exists "$base/admin-hmac")" = "1" ] && [ "$ROTATE" = 0 ]; then
    echo "$base/admin-hmac exists"
  else
    ow=""; state=created
    [ "$(exists "$base/admin-hmac")" = "1" ] && { ow="--overwrite"; state=rotated; }
    put_secure "$base/admin-hmac" "$(random_hex)" "$ow"
    echo "$base/admin-hmac $state"
  fi
done
log "pronto. Os valores nao foram exibidos; a task le direto do SSM."
