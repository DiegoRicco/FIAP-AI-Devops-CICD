#!/usr/bin/env bash
# runner-recover.sh - recupera o GitLab Runner da Quantum Commerce a partir do
# diagnostico do runner-preflight.sh, com o menor passo que resolve.
#
# Uso:
#   ./runner-recover.sh            # padrao = --dry-run: so mostra o plano, nao muda nada
#   ./runner-recover.sh --apply    # executa a acao do diagnostico
#
# Acoes (da mais barata para a mais cara; recriar e o ULTIMO recurso):
#   reuse       nada a fazer
#   restart     systemctl restart gitlab-runner via SSM
#   start       aws ec2 start-instances + espera o SSM ficar Online
#   reconfigure playbook do Modulo 02 (instala o que faltar e registra SE preciso)
#   recreate    terraform init/plan/apply do Modulo 02 + playbook
#   blocked     nao age: imprime o motivo e a dica de quem resolve
#
# Garantias:
#   - Nunca registra duas vezes. Este script nao chama 'gitlab-runner register':
#     quem registra e o playbook, que so registra quando o token do SSM NAO esta
#     no config.toml e apaga o config antigo antes (fica sempre 1 [[runners]]).
#   - Nunca usa 'terraform destroy', 'git reset' nem Elastic IP, e nao mexe no
#     Security Group (o do Modulo 02 nao tem porta 22 nem outra entrada).
#   - Um plano Terraform que APAGUE ou SUBSTITUA recurso AWS e abortado.
#   - Idempotente: cada execucao recomeca do preflight; com o runner saudavel o
#     diagnostico e 'reuse' e nada acontece.
#
# IP publico: reboot mantem o IP; stop/start troca o IP. Nada no runner depende
# do IP (ele faz polling de saida para o gitlab.com), entao nao ha o que corrigir.
#
# Saida: stdout = resultado (chave=valor); stderr = progresso.
# Variaveis: as do preflight (AWS_REGION, RUNNER_TAG, STATE_BUCKET, STATE_KEY,
# TOKEN_SSM_PATH) e ainda:
#   RUNNER_TF_DIR   pasta Terraform do runner (padrao: a do Modulo 02 neste repo)
#   ANSIBLE_DIR     pasta do playbook (padrao: a do Modulo 02 neste repo)
#   RUNNER_NAME     descricao do runner no GitLab (padrao: quantum-ai-gitlab-runner)
set -euo pipefail

log() { printf '[recover] %s\n' "$*" >&2; }
out() { printf '%s=%s\n' "$1" "$2"; }

MODE=dry-run
case "${1:-}" in
  ""|--dry-run) MODE=dry-run ;;
  --apply)      MODE=apply ;;
  -h|--help)    sed -n '2,36p' "$0" >&2; exit 0 ;;
  *) log "opcao desconhecida: $1 (use --dry-run ou --apply)"; exit 64 ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
MOD02="$REPO_ROOT/02-Ansible/01-provisionando-gitlab-runner"
RUNNER_TF_DIR="${RUNNER_TF_DIR:-$MOD02/terraform-gitlab-runner}"
ANSIBLE_DIR="${ANSIBLE_DIR:-$MOD02/ansible-gitlab-runner}"
RUNNER_NAME="${RUNNER_NAME:-quantum-ai-gitlab-runner}"
STATE_KEY="${STATE_KEY:-gitlab-runner-fleet}"
export STATE_KEY

PF=""
preflight() {
  local rc=0
  PF="$("$SCRIPT_DIR/runner-preflight.sh")" || rc=$?
  [ "$rc" -le 3 ] || { log "o preflight falhou (rc=$rc)."; exit 1; }
}
pf() { sed -n "s/^$1=//p" <<<"$PF" | tail -1; }

ssm_run() {
  local instance="$1" script="$2" timeout_s="${3:-90}" params cmd status i
  params="$(jq -Rs '{commands: [.]}' <<<"$script")"
  cmd="$(aws ssm send-command --instance-ids "$instance" --document-name AWS-RunShellScript \
    --comment "runner-recover" --parameters "$params" --query 'Command.CommandId' --output text)" || return 1
  status=Pending
  for ((i = 0; i < timeout_s; i += 3)); do
    sleep 3
    status="$(aws ssm get-command-invocation --command-id "$cmd" --instance-id "$instance" \
      --query Status --output text 2>/dev/null || echo Pending)"
    case "$status" in Pending|InProgress|Delayed) ;; *) break ;; esac
  done
  aws ssm get-command-invocation --command-id "$cmd" --instance-id "$instance" \
    --query StandardOutputContent --output text
  [ "$status" = Success ]
}

wait_ssm_online() {
  local instance="$1" i ping
  for i in $(seq 1 60); do
    ping="$(aws ssm describe-instance-information --filters "Key=InstanceIds,Values=$instance" \
      --query 'InstanceInformationList[0].PingStatus' --output text 2>/dev/null || true)"
    [ "$ping" = Online ] && { log "    SSM Online apos ~$((i * 5)) s"; return 0; }
    sleep 5
  done
  log "    SSM nao ficou Online em 5 min (confira o LabInstanceProfile da EC2)."
  return 1
}

# O playbook roda no Codespaces e conecta na EC2 pelo SSM. O inventario e gerado
# num arquivo temporario com o instance id ATUAL; o 'hosts' versionado nao muda.
run_playbook() {
  local instance="$1" bucket="$2" region="$3" playbook hosts
  playbook="$(command -v ansible-playbook || true)"
  [ -n "$playbook" ] || playbook="$HOME/venv/bin/ansible-playbook"
  [ -x "$playbook" ] || { log "ERRO: ansible-playbook nao encontrado (no Codespaces fica em ~/venv/bin)."; return 1; }
  command -v session-manager-plugin >/dev/null || { log "ERRO: falta o session-manager-plugin (passo 3 do Modulo 02)."; return 1; }
  local galaxy; galaxy="$(dirname "$playbook")/ansible-galaxy"
  for c in community.aws amazon.aws; do
    if ! "$galaxy" collection list "$c" 2>/dev/null | grep -q "^$c "; then
      log "    instalando a collection $c (faltava)..."
      "$galaxy" collection install "$c" >&2
    fi
  done
  hosts="$(mktemp)"
  printf '[runner]\n%s\n\n[all:vars]\nansible_connection=community.aws.aws_ssm\nansible_aws_ssm_bucket_name=%s\nansible_aws_ssm_region=%s\nansible_become=true\n' \
    "$instance" "$bucket" "$region" >"$hosts"
  log "    ansible-playbook play.yaml em $instance (alguns minutos)..."
  local rc=0
  ( cd "$ANSIBLE_DIR" && "$playbook" -i "$hosts" --extra-vars "gitlab_runner_name=$RUNNER_NAME" play.yaml >&2 ) || rc=$?
  rm -f "$hosts"
  return "$rc"
}

# init com o bucket e a key que o preflight acabou de confirmar no S3 e mostrar
# na tela. Roda numa COPIA temporaria da pasta do Modulo 02: o state e remoto
# (S3), entao o resultado e o mesmo, e o repositorio nao ganha .terraform.lock.hcl
# nem arquivo de plano. -reconfigure so aponta para onde o state esta; nunca migra.
TF_WORK=""
trap 'if [ -n "$TF_WORK" ]; then rm -rf "$TF_WORK"; fi' EXIT
tf_plan() {
  local bucket="$1" region="$2"
  TF_WORK="$(mktemp -d)"
  cp "$RUNNER_TF_DIR"/*.tf "$RUNNER_TF_DIR"/*.sh "$TF_WORK"/
  log "    copia de trabalho do Terraform: $TF_WORK"
  ( cd "$TF_WORK" &&
    terraform init -input=false -reconfigure -backend-config="bucket=$bucket" \
      -backend-config="key=$STATE_KEY" -backend-config="region=$region" >&2 &&
    terraform plan -input=false -out=recover.tfplan >&2 )
  # So conta recurso AWS real: o terraform_data.bootstrap e "substituido" sempre
  # que a EC2 muda (ele apenas reexecuta o bootstrap), e isso nao apaga nada.
  local deletes
  deletes="$(cd "$TF_WORK" && terraform show -json recover.tfplan \
    | jq '[.resource_changes[]? | select((.type | startswith("aws_")) and (.change.actions | index("delete")))] | length')"
  out plan_deletes "$deletes"
  out plan_creates "$(cd "$TF_WORK" && terraform show -json recover.tfplan \
    | jq -r '[.resource_changes[]? | select(.change.actions | index("create")) | .address] | join(",")')"
  if [ "$deletes" -gt 0 ]; then
    rm -rf "$TF_WORK"
    log "ERRO: o plano apagaria/substituiria $deletes recurso(s) AWS. Abortado: recuperar nunca destroi."
    return 1
  fi
}

log "modo: $MODE (nada muda sem --apply)"
DONE=" "
for round in 1 2 3 4; do
  preflight
  D="$(pf diagnosis)"; R="$(pf reason)"
  ID="$(pf instance_id)"; BUCKET="$(pf state_bucket)"; REGION="$(pf region)"
  export AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION"
  log "rodada $round: diagnostico=$D ($R) runners_in_config=$(pf runners_in_config)"

  if [ "$D" = reuse ]; then
    out action reuse
    TAKEN="${DONE# }"; TAKEN="${TAKEN% }"; TAKEN="${TAKEN// /,}"
    out actions_taken "${TAKEN:-none}"
    out runners_in_config "$(pf runners_in_config)"
    out public_ip "$(pf public_ip)"
    out result ok
    [ "$round" -eq 1 ] && log "nada a fazer: o runner esta pronto. Rode runner-test.sh para ver um job de verdade."
    exit 0
  fi
  if [ "$D" = blocked ]; then
    out action blocked; out reason "$R"; out result blocked
    log "bloqueado ($R): leia a dica do preflight acima. Este script nao contorna isso sozinho."
    exit 3
  fi
  # Cada acao roda no maximo uma vez: se ela nao resolveu, repetir nao vai resolver.
  if [[ "$DONE" == *" $D "* ]]; then
    out action "$D"; out result failed
    log "a acao '$D' ja foi executada e o diagnostico nao mudou. Pare e chame o professor."
    exit 1
  fi

  case "$D" in
    restart)
      log "PLANO: via SSM em $ID -> systemctl restart gitlab-runner; conferir is-active."
      if [ "$MODE" = apply ]; then
        ssm_run "$ID" 'systemctl restart gitlab-runner; sleep 3; systemctl is-active gitlab-runner' 60 >&2 \
          || log "    o restart nao voltou 'active'; o proximo preflight mostra o porque."
      fi ;;
    start)
      log "PLANO: aws ec2 start-instances $ID; esperar running e SSM Online (~1 min). O IP publico vai mudar, e tudo bem."
      if [ "$MODE" = apply ]; then
        aws ec2 start-instances --instance-ids "$ID" --query 'StartingInstances[0].CurrentState.Name' --output text >&2
        aws ec2 wait instance-running --instance-ids "$ID"
        wait_ssm_online "$ID" || true
      fi ;;
    reconfigure)
      log "PLANO: ansible-playbook do Modulo 02 em $ID via SSM (bucket $BUCKET). Registra so se o token do SSM nao estiver no config.toml."
      if [ "$MODE" = apply ]; then run_playbook "$ID" "$BUCKET" "$REGION"; fi ;;
    recreate)
      log "PLANO: terraform init/plan do Modulo 02 (s3://$BUCKET/$STATE_KEY); apply so se o plano nao apagar nada; depois o playbook."
      tf_plan "$BUCKET" "$REGION"
      if [ "$MODE" = apply ]; then
        ( cd "$TF_WORK" && terraform apply -input=false recover.tfplan >&2 )
        ID="$(cd "$TF_WORK" && terraform output -raw instance_id)"
        log "    EC2 nova: $ID"
        run_playbook "$ID" "$BUCKET" "$REGION"
      fi
      rm -rf "$TF_WORK" ;;
    *) log "diagnostico inesperado: $D"; exit 1 ;;
  esac

  if [ "$MODE" = dry-run ]; then
    out action "$D"; out result planned
    log "dry-run: nada foi alterado. Para executar: $0 --apply"
    exit 0
  fi
  DONE="$DONE$D "
done
out result failed
log "o runner nao convergiu em 4 rodadas. Pare e chame o professor."
exit 1
