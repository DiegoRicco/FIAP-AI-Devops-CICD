#!/usr/bin/env bash
# runner-preflight.sh - diagnostico SOMENTE LEITURA do GitLab Runner da Quantum Commerce.
#
# Responde a pergunta "o runner da aula passada ainda serve?" sem mudar nada:
# nao liga EC2, nao reinicia servico, nao roda Terraform nem Ansible. Quem age e
# o runner-recover.sh, que usa este diagnostico.
#
# Saida (contrato):
#   stdout = resultado, linhas chave=valor (a ultima e diagnosis=...). Exemplo:
#            instance_id=i-0abc...  instance_state=running  diagnosis=reuse
#   stderr = progresso e explicacoes para o aluno.
#   exit   = 0 reuse (pode usar) | 2 precisa de acao (restart/start/reconfigure/recreate)
#            | 3 blocked (algo que o script nao resolve sozinho; leia a dica)
#
# Diagnosticos (arvore do playbook da Aula 5):
#   reuse       EC2 running + SSM Online + servico active + verify valido + 1 [[runners]]
#   restart     servico parado, mas o registro esta valido
#   start       EC2 stopped (volume e config.toml preservados)
#   reconfigure EC2 ok, mas falta binario/config/registro, ou ha token NOVO no SSM
#   recreate    nao existe EC2 do runner (terminada ou nunca criada)
#   blocked     credencial expirada, SSM offline, token revogado sem token novo etc.
#
# IP publico: reboot mantem o IP; stop/start troca o IP. Nada no runner depende
# do IP, porque ele faz polling de SAIDA para o gitlab.com. IP novo nao e problema.
#
# Por que NUNCA chamamos 'gitlab-runner list': ele imprime o campo Token= de cada
# runner. Log de terminal, print de tela e saida do SSM ficam guardados; um token
# exposto deixa qualquer pessoa pegar os jobs do projeto. Para contar runners,
# contamos os blocos [[runners]] do config.toml, sem mostrar o conteudo.
#
# Variaveis opcionais (os padroes servem para a aula):
#   AWS_REGION      regiao (padrao: aws configure get region, senao us-east-1)
#   RUNNER_TAG      tag Name da EC2 (padrao: quantum-ai-gitlab-runner)
#   STATE_BUCKET    bucket do state (padrao: descoberto com 'aws s3 ls | grep ^base-config')
#   STATE_KEY       key do state do runner (padrao: gitlab-runner-fleet, a do Modulo 02)
#   TOKEN_SSM_PATH  parametro do token (padrao: /fiap/gitlab-runner/token)
set -euo pipefail

log()  { printf '[preflight] %s\n' "$*" >&2; }
hint() { printf '[preflight]   dica: %s\n' "$*" >&2; }
out()  { printf '%s=%s\n' "$1" "$2"; }

RUNNER_TAG="${RUNNER_TAG:-quantum-ai-gitlab-runner}"
STATE_KEY="${STATE_KEY:-gitlab-runner-fleet}"
TOKEN_SSM_PATH="${TOKEN_SSM_PATH:-/fiap/gitlab-runner/token}"

finish() {
  # $1 diagnostico, $2 motivo curto (vai no stdout para o recover ler)
  out reason "$2"
  out diagnosis "$1"
  log "DIAGNOSTICO: $1 ($2)"
  case "$1" in
    reuse)   exit 0 ;;
    blocked) exit 3 ;;
    *)       exit 2 ;;
  esac
}

# Executa um script no runner via SSM Run Command (sem SSH, sem porta 22) e
# imprime o stdout remoto. Retorna 1 se o comando nao terminar com Success.
ssm_run() {
  local instance="$1" script="$2" timeout_s="${3:-90}" params cmd status i
  params="$(jq -Rs '{commands: [.]}' <<<"$script")"
  cmd="$(aws ssm send-command --region "$AWS_REGION" --instance-ids "$instance" \
    --document-name AWS-RunShellScript --comment "runner-preflight (somente leitura)" \
    --parameters "$params" --query 'Command.CommandId' --output text)" || return 1
  status="Pending"
  for ((i = 0; i < timeout_s; i += 3)); do
    sleep 3
    status="$(aws ssm get-command-invocation --region "$AWS_REGION" --command-id "$cmd" \
      --instance-id "$instance" --query Status --output text 2>/dev/null || echo Pending)"
    case "$status" in Pending|InProgress|Delayed) ;; *) break ;; esac
  done
  aws ssm get-command-invocation --region "$AWS_REGION" --command-id "$cmd" \
    --instance-id "$instance" --query StandardOutputContent --output text
  [ "$status" = "Success" ]
}

# ---------------------------------------------------------------- 1. credenciais
log "1/6 Conferindo as credenciais AWS (sts get-caller-identity)..."
if ! STS="$(aws sts get-caller-identity --query Account --output text 2>&1)"; then
  case "$STS" in
    *ExpiredToken*|*expired*)
      log "ERRO: as credenciais do Learner Lab expiraram (ExpiredToken)."
      hint "a sessao do AWS Academy dura ~4h. Clique em Start Lab, abra AWS Details e cole o bloco novo em ~/.aws/credentials." ;;
    *"Unable to locate credentials"*|*NoCredentials*)
      log "ERRO: nenhuma credencial AWS neste terminal."
      hint "cole o bloco de AWS Details do Learner Lab em ~/.aws/credentials." ;;
    *InvalidClientTokenId*|*SignatureDoesNotMatch*)
      log "ERRO: credencial invalida (copiada pela metade ou de uma sessao antiga)."
      hint "copie o bloco inteiro de novo, incluindo aws_session_token." ;;
    *) log "ERRO: falha ao chamar a AWS: $(head -c 200 <<<"$STS")" ;;
  esac
  out aws_credentials invalid
  finish blocked aws_credentials
fi
out aws_account "****${STS: -4}"
out aws_credentials ok

AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-$(aws configure get region 2>/dev/null || true)}}"
AWS_REGION="${AWS_REGION:-us-east-1}"
export AWS_REGION AWS_DEFAULT_REGION="$AWS_REGION"
out region "$AWS_REGION"
log "    conta ****${STS: -4}, regiao $AWS_REGION"

# ---------------------------------------------------------------- 2. state
# Lemos o state direto do S3 (somente leitura). 'terraform state list' exigiria
# 'terraform init' na pasta do Modulo 02, que escreve arquivos locais.
log "2/6 Procurando o state Terraform do runner (key $STATE_KEY)..."
if [ -z "${STATE_BUCKET:-}" ]; then
  BUCKETS="$(aws s3 ls | awk '{print $3}' | grep '^base-config' || true)"
  STATE_BUCKET="$(head -1 <<<"$BUCKETS")"
  if [ "$(grep -c . <<<"$BUCKETS" || true)" -gt 1 ]; then
    log "    aviso: mais de um bucket base-config-*; usando $STATE_BUCKET (defina STATE_BUCKET para escolher)."
  fi
fi
STATE_INSTANCE=""
if [ -z "$STATE_BUCKET" ]; then
  log "    nenhum bucket base-config-* encontrado."
  hint "o bucket do state e criado no setup do Modulo 01."
  out state_bucket none
  out state none
elif aws s3api head-object --bucket "$STATE_BUCKET" --key "$STATE_KEY" >/dev/null 2>&1; then
  STATE_INSTANCE="$(aws s3 cp "s3://$STATE_BUCKET/$STATE_KEY" - --quiet \
    | jq -r '[.resources[]? | select(.type=="aws_instance") | .instances[]?.attributes.id] | first // empty')"
  out state_bucket "$STATE_BUCKET"
  out state present
  out state_instance_id "${STATE_INSTANCE:-none}"
  log "    state encontrado em s3://$STATE_BUCKET/$STATE_KEY (instancia no state: ${STATE_INSTANCE:-nenhuma})"
else
  out state_bucket "$STATE_BUCKET"
  out state none
  log "    sem state em s3://$STATE_BUCKET/$STATE_KEY"
fi

# ---------------------------------------------------------------- 3. token no SSM
# So conferimos se o parametro EXISTE e quando mudou. O valor nunca e lido aqui.
log "3/6 Conferindo o parametro do token ($TOKEN_SSM_PATH), sem ler o valor..."
TOKEN_MODIFIED="$(aws ssm describe-parameters --parameter-filters "Key=Name,Values=$TOKEN_SSM_PATH" \
  --query 'Parameters[0].LastModifiedDate' --output text 2>/dev/null || true)"
if [ -z "$TOKEN_MODIFIED" ] || [ "$TOKEN_MODIFIED" = "None" ]; then
  TOKEN_PARAM=missing
  out token_param missing
  TOKEN_EPOCH=0
  log "    parametro ausente."
else
  TOKEN_PARAM=present
  out token_param present
  TOKEN_EPOCH="$(python3 -c 'import sys,datetime;print(int(datetime.datetime.fromisoformat(sys.argv[1]).timestamp()))' "$TOKEN_MODIFIED" 2>/dev/null || echo 0)"
  log "    parametro presente (ultima alteracao $TOKEN_MODIFIED)"
fi

# ---------------------------------------------------------------- 4. EC2
log "4/6 Procurando a EC2 com tag Name=$RUNNER_TAG..."
INSTANCES="$(aws ec2 describe-instances \
  --filters "Name=tag:Name,Values=$RUNNER_TAG" "Name=instance-state-name,Values=pending,running,stopping,stopped" \
  --query 'Reservations[].Instances[].[InstanceId,State.Name,PublicIpAddress,LaunchTime]' --output text)"
COUNT="$(grep -c . <<<"$INSTANCES" || true)"
out instance_count "$COUNT"

if [ "$COUNT" -eq 0 ]; then
  out instance_state none
  log "    nenhuma EC2 viva com essa tag (terminada ou nunca criada)."
  if [ "$TOKEN_PARAM" = missing ]; then
    hint "antes de recriar, crie o runner na UI do GitLab (tags shell,terraform) e grave o token glrt- no SSM:"
    hint "aws ssm put-parameter --name $TOKEN_SSM_PATH --type SecureString --value 'glrt-SEU-TOKEN' --overwrite"
    finish blocked token_missing
  fi
  if [ -z "$STATE_BUCKET" ]; then finish blocked state_bucket_missing; fi
  hint "recreate = terraform apply do Modulo 02 + playbook. Custa ~25 min; confira antes com runner-recover.sh (dry-run)."
  finish recreate instance_missing
fi
if [ "$COUNT" -gt 1 ]; then
  log "    ERRO: $COUNT EC2 vivas com a mesma tag. O script nao escolhe sozinho qual e a certa."
  hint "veja no console EC2 qual esta no state ($STATE_INSTANCE) e encerre a outra manualmente, com cuidado."
  finish blocked duplicate_instances
fi

read -r INSTANCE_ID STATE PUBLIC_IP _ <<<"$INSTANCES"
if [ "$PUBLIC_IP" = "None" ]; then PUBLIC_IP=none; fi
out instance_id "$INSTANCE_ID"
out instance_state "$STATE"
out public_ip "$PUBLIC_IP"
log "    $INSTANCE_ID estado=$STATE ip=$PUBLIC_IP"
if [ -n "$STATE_INSTANCE" ] && [ "$STATE_INSTANCE" != "$INSTANCE_ID" ]; then
  log "    aviso: a EC2 encontrada pela tag ($INSTANCE_ID) nao e a do state ($STATE_INSTANCE)."
fi

case "$STATE" in
  stopped)  hint "EC2 desligada: o disco e o config.toml continuam la; ligar resolve (o IP muda, e tudo bem)."
            finish start instance_stopped ;;
  stopping) hint "a EC2 esta desligando. Espere 1 minuto e rode o preflight de novo."
            finish blocked instance_stopping ;;
  pending)  hint "a EC2 esta ligando. Espere 1 minuto e rode o preflight de novo."
            finish blocked instance_pending ;;
esac

# ---------------------------------------------------------------- 5. SSM
log "5/6 Conferindo o agente SSM da EC2..."
PING="$(aws ssm describe-instance-information --filters "Key=InstanceIds,Values=$INSTANCE_ID" \
  --query 'InstanceInformationList[0].PingStatus' --output text 2>/dev/null || true)"
if [ -z "$PING" ] || [ "$PING" = "None" ]; then PING=unknown; fi
out ssm_ping "$PING"
log "    PingStatus=$PING"
if [ "$PING" != "Online" ]; then
  hint "SSM offline: logo depois de ligar a EC2 leva ~30 s. Se passar de 5 min, confira se a EC2 usa o LabInstanceProfile."
  finish blocked ssm_offline
fi

# ---------------------------------------------------------------- 6. runner na EC2
log "6/6 Conferindo o gitlab-runner na EC2 via SSM (servico, verify, [[runners]])..."
# O script remoto imprime so chave=valor. A saida do verify NAO sai da EC2: ela
# tem o prefixo do token (runner=xxxx). Guardamos so o veredito. O SSM executa
# com /bin/sh (dash), entao o script remoto e POSIX, sem recursos do bash.
# shellcheck disable=SC2016  # as variaveis expandem na EC2, nao aqui
REMOTE='set +e
C=/etc/gitlab-runner/config.toml
if command -v gitlab-runner >/dev/null; then echo binary=present; else echo binary=missing; fi
echo "service=$(systemctl is-active gitlab-runner 2>/dev/null)"
if [ -f "$C" ]; then
  echo config=present
  echo "runners=$(grep -c "^\[\[runners\]\]" "$C")"
  echo "config_mtime=$(stat -c %Y "$C")"
else
  echo config=missing; echo runners=0; echo config_mtime=0
fi
if command -v gitlab-runner >/dev/null && [ -f "$C" ]; then
  V="$(timeout 60 gitlab-runner verify 2>&1)"
  if echo "$V" | grep -qiE "is not valid|is removed|403 forbidden"; then echo verify=invalid
  elif echo "$V" | grep -q "is valid"; then echo verify=valid
  else echo verify=error; fi
else
  echo verify=skipped
fi'
if ! REMOTE_OUT="$(ssm_run "$INSTANCE_ID" "$REMOTE" 120)"; then
  log "    ERRO: o comando SSM nao terminou com sucesso."
  hint "rode de novo em 1 minuto; se repetir, veja Systems Manager > Run Command no console."
  finish blocked ssm_command_failed
fi
val() { sed -n "s/^$1=//p" <<<"$REMOTE_OUT" | head -1; }
BINARY="$(val binary)"; SERVICE="$(val service)"; CONFIG="$(val config)"
RUNNERS="$(val runners)"; CONFIG_MTIME="$(val config_mtime)"; VERIFY="$(val verify)"
out runner_binary "$BINARY"; out runner_service "${SERVICE:-unknown}"; out runner_config "$CONFIG"
out runners_in_config "${RUNNERS:-0}"; out runner_verify "$VERIFY"
log "    binario=$BINARY servico=$SERVICE config=$CONFIG [[runners]]=$RUNNERS verify=$VERIFY"

TOKEN_IS_NEWER=no
if [ "$TOKEN_EPOCH" -gt "${CONFIG_MTIME:-0}" ] && [ "$CONFIG" = "present" ]; then TOKEN_IS_NEWER=yes; fi
out token_newer_than_config "$TOKEN_IS_NEWER"

if [ "${RUNNERS:-0}" -gt 1 ]; then
  log "    o config.toml tem $RUNNERS entradas [[runners]]: alguem registrou duas vezes."
  hint "o playbook nao apaga entradas com o token atual. Remova os runners extras na UI do GitLab e avise o professor."
  finish blocked duplicate_registration
fi
if [ "$BINARY" = "missing" ] || [ "$CONFIG" = "missing" ] || [ "${RUNNERS:-0}" -eq 0 ]; then
  if [ "$TOKEN_PARAM" = missing ]; then
    hint "grave o token glrt- do GitLab no SSM antes: aws ssm put-parameter --name $TOKEN_SSM_PATH --type SecureString --value 'glrt-SEU-TOKEN' --overwrite"
    finish blocked token_missing
  fi
  hint "a EC2 existe mas o runner nao esta instalado/registrado: o playbook do Modulo 02 resolve."
  finish reconfigure runner_not_configured
fi
if [ "$VERIFY" = "invalid" ]; then
  if [ "$TOKEN_IS_NEWER" = "yes" ]; then
    hint "o token do config.toml foi revogado e ha um token NOVO no SSM: o playbook troca o registro."
    finish reconfigure token_rotated
  fi
  log "    o GitLab recusou o token do runner (revogado ou runner apagado na UI)."
  hint "crie um runner novo na UI (Settings > CI/CD > Runners, tags shell,terraform), grave o glrt- novo no SSM com --overwrite e rode o recover."
  finish blocked token_revoked
fi
if [ "$VERIFY" = "error" ]; then
  log "    o verify nao conseguiu falar com o gitlab.com (rede, DNS ou instabilidade do GitLab)."
  hint "a EC2 precisa de saida 443 para gitlab.com. Tente de novo em alguns minutos."
  finish blocked gitlab_unreachable
fi
if [ "$SERVICE" != "active" ]; then
  hint "o registro esta valido, so o servico esta parado: reiniciar resolve."
  finish restart "service_${SERVICE:-unknown}"
fi
if [ "$TOKEN_IS_NEWER" = "yes" ]; then
  log "    aviso: o token do SSM e mais novo que o config.toml, mas o runner atual esta valido. Nada sera trocado."
fi
log "    runner pronto. 'verify' prova que o token vale; so um job prova que ele executa: use runner-test.sh."
finish reuse runner_online
