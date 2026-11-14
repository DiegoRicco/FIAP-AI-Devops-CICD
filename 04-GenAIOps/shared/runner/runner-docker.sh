#!/usr/bin/env bash
# runner-docker.sh - instala o Docker na EC2 do GitLab Runner, via SSM (sem SSH).
#
# Por que o runner precisa de Docker a partir da Aula 5: o release passa a ser
# uma IMAGEM de conteiner (codigo + corpus + grafo + modelo de embedding). Quem
# constroi essa imagem e o runner, porque ele e amd64 (a mesma arquitetura do
# Fargate) e ja esta na rede fiap-lab. O notebook ou o Codespaces do aluno nao
# precisa de Docker.
#
# O que o script faz (idempotente: rodar de novo nao muda nada):
#   1. instala docker.io e, se o Ubuntu tiver o pacote, docker-buildx;
#   2. cria um swap de 2 GB se a EC2 nao tiver nenhum. A t3.small tem 1,9 GB
#      de RAM; instalar as bibliotecas de embedding dentro do build passa disso
#      e, sem swap, o kernel mata um processo qualquer (as vezes o proprio
#      gitlab-runner);
#   3. coloca o usuario gitlab-runner no grupo docker (os jobs rodam com ele);
#   4. reinicia o gitlab-runner SO se o grupo mudou, porque o processo so
#      enxerga grupos novos depois de reiniciar.
#
# O que o script NAO toca: /opt/venv, o Ansible, o config.toml e o token. Nunca
# chamamos 'gitlab-runner list' (ele imprime o token na tela).
#
# Uso (no Codespaces, raiz do repositorio):
#   bash 04-GenAIOps/shared/runner/runner-docker.sh
# Saida: stdout = linhas chave=valor (a ultima e docker_runner=ok|fail);
#        stderr = progresso. Exit 0 so com 'docker info' funcionando como gitlab-runner.
set -euo pipefail

export AWS_PAGER=""
AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-$(aws configure get region 2>/dev/null || true)}}"
AWS_REGION="${AWS_REGION:-us-east-1}"
RUNNER_TAG="${RUNNER_TAG:-quantum-ai-gitlab-runner}"

log() { printf '[runner-docker] %s\n' "$*" >&2; }

log "Procurando a EC2 do runner (tag Name=$RUNNER_TAG)..."
INSTANCE_ID="$(aws ec2 describe-instances --region "$AWS_REGION" \
  --filters "Name=tag:Name,Values=$RUNNER_TAG" Name=instance-state-name,Values=running \
  --query 'Reservations[].Instances[].InstanceId' --output text)"
case "$INSTANCE_ID" in
  i-*[[:space:]]*) log "ERRO: mais de uma EC2 running com essa tag; resolva antes (runner-preflight.sh)."; exit 3 ;;
  i-*) ;;
  *) log "ERRO: nenhuma EC2 running com a tag $RUNNER_TAG. Rode runner-preflight.sh e runner-recover.sh."; exit 3 ;;
esac
log "    $INSTANCE_ID"

# Script que roda DENTRO da EC2, como root (AWS-RunShellScript).
read -r -d '' REMOTE <<'EOF' || true
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
if ! command -v docker >/dev/null 2>&1; then
  apt-get update -qq
  apt-get install -y -qq docker.io >/dev/null
  echo "docker_installed=now"
else
  echo "docker_installed=already"
fi
if ! docker buildx version >/dev/null 2>&1; then
  if apt-cache show docker-buildx >/dev/null 2>&1; then
    apt-get install -y -qq docker-buildx >/dev/null
    echo "buildx_installed=now"
  else
    echo "buildx_installed=unavailable"
  fi
else
  echo "buildx_installed=already"
fi
systemctl enable --now docker >/dev/null 2>&1
if [ -z "$(swapon --show --noheadings)" ]; then
  if [ ! -f /swapfile ]; then
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile >/dev/null
  fi
  swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  echo "swap=created"
else
  echo "swap=already"
fi
if id -nG gitlab-runner | tr ' ' '\n' | grep -qx docker; then
  echo "group_docker=already"
else
  usermod -aG docker gitlab-runner
  systemctl restart gitlab-runner
  echo "group_docker=added_and_restarted"
fi
echo "docker_version=$(docker version --format '{{.Server.Version}}')"
echo "buildx_version=$(docker buildx version 2>/dev/null | awk '{print $2}' || echo none)"
echo "docker_info_as_runner=$(sudo -u gitlab-runner docker info --format '{{.ServerVersion}}/{{.Driver}}/{{.Architecture}}')"
echo "gitlab_runner_service=$(systemctl is-active gitlab-runner)"
echo "mem_available_mb=$(free -m | awk '/^Mem:/{print $7}')"
echo "swap_total_mb=$(free -m | awk '/^Swap:/{print $2}')"
echo "disk_free=$(df -h / | awk 'NR==2{print $4}')"
EOF

log "Enviando o script de instalacao via SSM (leva 1 a 3 minutos na primeira vez)..."
# O AWS-RunShellScript executa com /bin/sh (dash no Ubuntu), que nao conhece
# 'set -o pipefail'. Mandamos o script em base64 e o entregamos ao bash.
B64="$(printf '%s\n' "$REMOTE" | base64 | tr -d '\n')"
PARAMS="$(jq -n --arg c "echo $B64 | base64 -d | bash" '{commands: [$c]}')"
CMD="$(aws ssm send-command --region "$AWS_REGION" --instance-ids "$INSTANCE_ID" \
  --document-name AWS-RunShellScript --comment "runner-docker (instala docker)" \
  --timeout-seconds 900 --parameters "$PARAMS" --query 'Command.CommandId' --output text)"

STATUS=Pending
for _ in $(seq 1 100); do
  sleep 6
  STATUS="$(aws ssm get-command-invocation --region "$AWS_REGION" --command-id "$CMD" \
    --instance-id "$INSTANCE_ID" --query Status --output text 2>/dev/null || echo Pending)"
  case "$STATUS" in Pending|InProgress|Delayed) ;; *) break ;; esac
done

aws ssm get-command-invocation --region "$AWS_REGION" --command-id "$CMD" \
  --instance-id "$INSTANCE_ID" --query StandardOutputContent --output text
if [ "$STATUS" != "Success" ]; then
  log "ERRO: o comando terminou com status $STATUS. Ultimas linhas de erro:"
  aws ssm get-command-invocation --region "$AWS_REGION" --command-id "$CMD" \
    --instance-id "$INSTANCE_ID" --query StandardErrorContent --output text | tail -5 >&2
  echo "docker_runner=fail"
  exit 1
fi
echo "docker_runner=ok"
log "Pronto: o gitlab-runner ja consegue rodar 'docker build'."
