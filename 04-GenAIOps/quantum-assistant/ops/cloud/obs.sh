#!/usr/bin/env bash
# Observabilidade da Demo 5.2 na AWS: EC2 qc-assistant-obs + Grafana no ALB.
#
#   bash ops/cloud/obs.sh up         # secrets + sync + terraform apply + deploy (tudo, idempotente)
#   bash ops/cloud/obs.sh secrets    # cria a senha do Grafana no SSM (se faltar), sem mostrar
#   bash ops/cloud/obs.sh sync       # envia a configuracao e o codigo do probe para o S3
#   bash ops/cloud/obs.sh apply      # terraform apply de infra-obs/ e grava o endpoint OTLP no SSM
#   bash ops/cloud/obs.sh deploy     # sync + roda o bootstrap na EC2 via SSM (sem SSH)
#   bash ops/cloud/obs.sh status     # conteineres na EC2 e saude do Grafana pelo ALB
#   bash ops/cloud/obs.sh url        # imprime a URL do Grafana
#   bash ops/cloud/obs.sh destroy [--apply]   # padrao: so mostra o plano de destruicao
#
# Roda a partir da raiz do projeto (quantum-assistant/ ou a copia do aluno).
# Bucket: --bucket, TF_STATE_BUCKET ou o unico base-config-* da conta.
# Saida: stdout = resultado (URL, JSON de status); stderr = progresso.
set -euo pipefail

export AWS_PAGER=""
export AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
TF_DIR="$ROOT/infra-obs"
PROJECT="qc-assistant"
CLUSTER="${CLUSTER:-$PROJECT}"
ALB_NAME="${QC_ALB_NAME:-$PROJECT-alb}"
SSM_GRAFANA="/$PROJECT/obs/grafana-admin"
SSM_OTLP="/$PROJECT/obs/otlp-endpoint"
BUCKET="${TF_STATE_BUCKET:-}"
APPLY=0

log() { echo "obs: $*" >&2; }
die() { log "ERRO: $*"; exit 1; }

cmd="${1:-}"; [ -n "$cmd" ] || { sed -n '4,12p' "$0" >&2; exit 2; }
shift
while [ $# -gt 0 ]; do
  case "$1" in
    --bucket) BUCKET="$2"; shift 2 ;;
    --apply) APPLY=1; shift ;;
    *) die "opcao desconhecida: $1" ;;
  esac
done

need_aws() {
  aws sts get-caller-identity >/dev/null 2>&1 || die "credencial AWS invalida ou expirada (Start Lab e copie de novo)"
}

resolve_bucket() {
  [ -n "$BUCKET" ] && return
  local found
  found=$(aws s3api list-buckets --query 'Buckets[].Name' --output text | tr '\t' '\n' | grep '^base-config-' || true)
  [ "$(printf '%s\n' "$found" | grep -c .)" = 1 ] || die "nao achei UM bucket base-config-* (passe --bucket base-config-<SEU RM>)"
  BUCKET="$found"
}

alb_exists() {
  aws elbv2 describe-load-balancers --names "$ALB_NAME" >/dev/null 2>&1
}

instance_id() {
  aws ec2 describe-instances --filters "Name=tag:Name,Values=$PROJECT-obs" \
    "Name=instance-state-name,Values=pending,running,stopping,stopped" \
    --query 'Reservations[].Instances[].InstanceId' --output text | awk '{print $1}'
}

do_secrets() {
  # Senha do admin do Grafana: aleatoria, SecureString, criada pela CLI e nao
  # pelo Terraform (recurso Terraform gravaria o valor no state, em texto).
  # Idempotente: se ja existe, nao troca (o Grafana guarda a senha no primeiro boot).
  if [ "$(aws ssm describe-parameters --parameter-filters "Key=Name,Values=$SSM_GRAFANA" \
        --query 'length(Parameters)' --output text)" = "1" ]; then
    log "$SSM_GRAFANA ja existe (nao alterado)"
    echo "$SSM_GRAFANA exists"
    return
  fi
  local tmp
  tmp="$(mktemp)"; chmod 600 "$tmp"
  # Valor por arquivo (file://): argumento de linha de comando aparece no `ps`.
  od -An -N18 -tx1 /dev/urandom | tr -d ' \n' > "$tmp"
  aws ssm put-parameter --name "$SSM_GRAFANA" --type SecureString --value "file://$tmp" \
    --description "Senha do admin do Grafana da Demo 5.2 (usuario admin)" >/dev/null
  rm -f "$tmp"
  aws ssm add-tags-to-resource --resource-type Parameter --resource-id "$SSM_GRAFANA" \
    --tags Key=Project,Value=quantum-commerce Key=Lab,Value=05-2 Key=Owner,Value=lab >/dev/null
  echo "$SSM_GRAFANA created"
}

do_sync() {
  resolve_bucket
  local dest="s3://$BUCKET/quantum-assistant/obs-config"
  log "enviando configuracao e codigo do probe para $dest/"
  # So o que a EC2 usa: configs do stack, o probe e o que ele importa (core,
  # evals, corpus como referencia). Nada de state, venv, resultados locais ou segredo.
  local d
  for d in ops/cloud core evals corpus config graph; do
    [ -d "$ROOT/$d" ] || continue
    # --exact-timestamps: sem ele, arquivo do mesmo tamanho (ex.: so a largura
    # de um painel mudou) pode nao ser reenviado.
    aws s3 sync --only-show-errors --delete --exact-timestamps "$ROOT/$d" "$dest/$d" \
      --exclude '*__pycache__*' --exclude '*.pyc' --exclude '.terraform/*' --exclude 'results/*'
  done
  # Do stack local reaproveitamos os arquivos, sem copia: Jaeger/Loki iguais e
  # as regras de julgamento do simulador e do monitor da Demo 5.2 local.
  local f
  for f in jaeger.yaml loki.yaml simulator.py quality_monitor.py; do
    aws s3 cp --only-show-errors "$ROOT/ops/$f" "$dest/ops/$f"
  done
  echo "$dest/"
}

tf() { terraform -chdir="$TF_DIR" "$@"; }

tf_init() {
  resolve_bucket
  tf init -input=false -reconfigure -backend-config="bucket=$BUCKET" >&2
}

tf_vars() {
  local attach=false
  alb_exists && attach=true
  printf -- '-var=config_bucket=%s -var=attach_alb=%s' "$BUCKET" "$attach"
}

do_apply() {
  alb_exists || log "AVISO: ALB $ALB_NAME ausente; a EC2 sobe sem a regra /grafana (rode make up, passo 5 da Demo 5.1)"
  tf_init
  # shellcheck disable=SC2046
  tf apply -input=false -auto-approve $(tf_vars) >&2
  local ip new old
  ip=$(tf output -raw private_ip)
  new="http://$ip:4318"
  old=$(aws ssm get-parameter --name "$SSM_OTLP" --query Parameter.Value --output text 2>/dev/null || true)
  # O parametro pertence ao infra-ecs/ (nasce "disabled"); aqui so trocamos o valor.
  aws ssm put-parameter --name "$SSM_OTLP" --type String --overwrite \
    --value "$new" >/dev/null
  if [ "$old" = "$new" ]; then
    log "$SSM_OTLP = $new (sem mudanca: live e candidate nao reiniciados)"
    return
  fi
  log "$SSM_OTLP = $new (era ${old:-ausente})"
  restart_for_otlp
}

RESTARTED=""

restart_for_otlp() {
  # A task le o endpoint via "secrets" do SSM, que o ECS so resolve quando a
  # task sobe: sem reiniciar, o live e o candidate da 5.1 seguem com "disabled"
  # e os paineis com metricas do app ficam vazios ate o proximo deploy.
  # --force-new-deployment reusa a revisao atual do service: mesma imagem, mesmo
  # digest e mesmo QC_RELEASE_ID; o release nao muda, so o valor lido no boot.
  # So dispara: a espera fica em wait_restarted, para o `up` rodar o bootstrap da
  # EC2 enquanto o ECS troca as tasks (um nao depende do outro).
  local svc desired
  for svc in "$PROJECT-live" "$PROJECT-candidate"; do
    desired=$(aws ecs describe-services --cluster "$CLUSTER" --services "$svc" \
      --query "services[?status=='ACTIVE'].desiredCount | [0]" --output text 2>/dev/null || true)
    case "$desired" in
      ""|None|0) log "$svc desligado ou ausente (desired=${desired:-?}): nao reiniciado"; continue ;;
    esac
    aws ecs update-service --cluster "$CLUSTER" --service "$svc" --force-new-deployment \
      --query 'service.serviceName' --output text >/dev/null
    RESTARTED="$RESTARTED $svc"
  done
  [ -n "$RESTARTED" ] || return 0
  log "reiniciando${RESTARTED} na mesma revisao: a task so le o endpoint OTLP novo quando sobe (cold start ~3-5 min, em paralelo)"
}

wait_restarted() {
  [ -n "$RESTARTED" ] || return 0
  log "esperando${RESTARTED} estabilizar com o endpoint OTLP novo"
  # shellcheck disable=SC2086
  aws ecs wait services-stable --cluster "$CLUSTER" --services $RESTARTED \
    || die "${RESTARTED# } nao estabilizou em 10 min. O endpoint ja esta gravado, entao repetir o obs.sh nao reinicia de novo; depois de make diagnose ENV=live e make diagnose ENV=candidate, para cada um: aws ecs update-service --cluster $CLUSTER --service <nome> --force-new-deployment"
  log "${RESTARTED# } estavel(is) com o endpoint OTLP novo (mesma imagem, mesmo release)"
}

wait_ssm_online() {
  local iid="$1" _
  for _ in $(seq 1 60); do
    if [ "$(aws ssm describe-instance-information --filters "Key=InstanceIds,Values=$iid" \
          --query 'InstanceInformationList[0].PingStatus' --output text 2>/dev/null)" = "Online" ]; then
      return 0
    fi
    sleep 10
  done
  die "EC2 $iid nao ficou Online no SSM em 10 min"
}

ssm_run() {
  # Roda um comando na EC2 via SSM e devolve a saida (stdout do comando).
  local iid="$1" script="$2" timeout="${3:-900}" cid status
  cid=$(aws ssm send-command --instance-ids "$iid" --document-name AWS-RunShellScript \
    --comment "qc-obs" --timeout-seconds "$timeout" \
    --parameters "$(python3 -c 'import json,sys; print(json.dumps({"commands":[sys.argv[1]],"executionTimeout":[sys.argv[2]]}))' "$script" "$timeout")" \
    --query Command.CommandId --output text)
  while :; do
    status=$(aws ssm get-command-invocation --command-id "$cid" --instance-id "$iid" \
      --query Status --output text 2>/dev/null || echo Pending)
    case "$status" in Pending|InProgress|Delayed) sleep 5 ;; *) break ;; esac
  done
  aws ssm get-command-invocation --command-id "$cid" --instance-id "$iid" \
    --query StandardOutputContent --output text
  [ "$status" = "Success" ] || { log "comando SSM terminou em $status"; \
    aws ssm get-command-invocation --command-id "$cid" --instance-id "$iid" \
      --query StandardErrorContent --output text >&2; return 1; }
}

do_deploy() {
  do_sync >/dev/null
  local iid
  iid=$(instance_id); [ -n "$iid" ] || die "EC2 $PROJECT-obs nao existe (rode: obs.sh apply)"
  wait_ssm_online "$iid"
  log "rodando o bootstrap na $iid via SSM (primeira vez: ~4 min de docker pull)"
  ssm_run "$iid" "set -e; test -f /opt/qc-obs/instance.env || { echo 'user_data ainda nao terminou'; exit 1; }; . /opt/qc-obs/instance.env; aws s3 cp --only-show-errors s3://\$QC_CONFIG_BUCKET/quantum-assistant/obs-config/ops/cloud/bootstrap.sh /opt/qc-obs/bootstrap.sh; bash /opt/qc-obs/bootstrap.sh 2>&1 | tail -25" 1200 >&2
}

do_url() {
  local dns
  dns=$(aws elbv2 describe-load-balancers --names "$ALB_NAME" --query 'LoadBalancers[0].DNSName' --output text)
  echo "http://$dns/grafana/"
}

do_status() {
  local iid url code anon
  iid=$(instance_id); [ -n "$iid" ] || die "EC2 $PROJECT-obs nao existe"
  url=$(do_url)
  code=$(curl -s -o /dev/null -w '%{http_code}' "${url}api/health" || true)
  # Anonimo precisa ser NEGADO: sem login, a API de busca devolve 401.
  anon=$(curl -s -o /dev/null -w '%{http_code}' "${url}api/search" || true)
  log "conteineres na EC2:"
  ssm_run "$iid" "cd /opt/qc-obs/app/ops/cloud && docker compose --profile probe ps --format '{{.Name}} {{.Status}}'; free -m | sed -n 1,2p" 120 >&2 || true
  python3 -c 'import json,sys; print(json.dumps({"instance_id":sys.argv[1],"grafana_url":sys.argv[2],"grafana_health_http":sys.argv[3],"anonymous_api_search_http":sys.argv[4]}))' \
    "$iid" "$url" "$code" "$anon"
}

do_destroy() {
  tf_init
  if [ "$APPLY" != 1 ]; then
    log "DRY-RUN: plano de destruicao SO do state quantum-assistant/obs (nada foi apagado)"
    # shellcheck disable=SC2046
    tf plan -destroy -input=false $(tf_vars) -no-color
    log "para apagar de verdade: bash ops/cloud/obs.sh destroy --apply"
    return
  fi
  # shellcheck disable=SC2046
  tf destroy -input=false -auto-approve $(tf_vars) >&2
  # Sem collector, o app volta a nao exportar (o envio ja e opcional, mas assim
  # o proximo deploy nem tenta).
  aws ssm put-parameter --name "$SSM_OTLP" --type String --overwrite --value disabled >/dev/null
  log "stack de observabilidade removida; $SSM_OTLP = disabled. A senha do Grafana continua no SSM."
}

need_aws
case "$cmd" in
  secrets) do_secrets ;;
  sync) do_sync ;;
  apply) do_apply; wait_restarted ;;
  deploy) do_deploy ;;
  up)
    # Sem ALB o apply passaria com attach_alb=false e o Grafana sumiria do
    # endereco publico sem erro nenhum. destroy e status nao checam: precisam
    # funcionar com o ALB ja apagado.
    alb_exists || die "ALB ausente: rode make up (passo 5 da Demo 5.1)"
    do_secrets >/dev/null; do_sync >/dev/null; do_apply; do_deploy; wait_restarted; do_url ;;
  status) do_status ;;
  url) do_url ;;
  destroy) do_destroy ;;
  *) die "comando desconhecido: $cmd" ;;
esac
