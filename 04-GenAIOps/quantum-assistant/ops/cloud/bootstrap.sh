#!/bin/bash
# Bootstrap da EC2 de observabilidade (roda como root, no host).
# Chamado pelo user_data no primeiro boot e por `ops/cloud/obs.sh deploy` (via
# SSM) a cada mudanca de configuracao. Idempotente: pode rodar quantas vezes
# precisar; so recria o conteiner cuja configuracao mudou.
#   1. instala Docker + compose plugin em versoes pinadas (se faltarem);
#   2. baixa do S3 a configuracao e o codigo do probe (ops/cloud, core, evals, corpus);
#   3. gera obs.env (DNS do ALB) e grafana.env (senha do admin lida do SSM, chmod 600);
#   4. sobe o compose; o probe so sobe se o ALB existir.
set -euo pipefail
exec > >(tee -a /var/log/qc-obs-bootstrap.log) 2>&1
echo "[bootstrap] inicio $(date -u +%FT%TZ)"

# shellcheck disable=SC1091
source /opt/qc-obs/instance.env
export AWS_REGION AWS_DEFAULT_REGION="$AWS_REGION"
APP=/opt/qc-obs/app
DATA=/opt/qc-obs/data
ALB_NAME="${QC_ALB_NAME:-qc-assistant-alb}"

# Versoes conferidas no repositorio oficial da Docker para Ubuntu 22.04 (jammy)
# em 2026-11-14. Pinadas para a EC2 de cada aluno ficar igual a do ensaio.
DOCKER_VERSION="5:29.9.3-1~ubuntu.22.04~jammy"
CONTAINERD_VERSION="2.4.2-4~ubuntu.22.04~jammy"
COMPOSE_VERSION="5.7.1-1~ubuntu.22.04~jammy"
BUILDX_VERSION="0.39.0-1~ubuntu.22.04~jammy"

if ! docker compose version >/dev/null 2>&1; then
  echo "[bootstrap] instalando Docker $DOCKER_VERSION"
  export DEBIAN_FRONTEND=noninteractive
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu jammy stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -q
  apt-get install -y -q "docker-ce=$DOCKER_VERSION" "docker-ce-cli=$DOCKER_VERSION" \
    "containerd.io=$CONTAINERD_VERSION" "docker-compose-plugin=$COMPOSE_VERSION" \
    "docker-buildx-plugin=$BUILDX_VERSION"
  # Sem isto um `apt upgrade` futuro trocaria a versao no meio da aula.
  apt-mark hold docker-ce docker-ce-cli containerd.io docker-compose-plugin docker-buildx-plugin
  systemctl enable --now docker
fi

echo "[bootstrap] sincronizando s3://$QC_CONFIG_BUCKET/quantum-assistant/obs-config/"
mkdir -p "$APP" "$DATA/results" "$DATA/monitor" "$DATA/incident"
# --exact-timestamps: o sync padrao so compara tamanho e, na descida, ignora
# arquivo do mesmo tamanho com data diferente. No ensaio, um dashboard com outra
# largura de painel (mesmo numero de bytes) nao chegou na EC2.
aws s3 sync --only-show-errors --delete --exact-timestamps "s3://$QC_CONFIG_BUCKET/quantum-assistant/obs-config/" "$APP/"
# O probe roda como uid 10001 (sem root) e so escreve em /data.
chown -R 10001:10001 "$DATA"

ALB_DNS=$(aws elbv2 describe-load-balancers --names "$ALB_NAME" \
  --query 'LoadBalancers[0].DNSName' --output text 2>/dev/null || true)
if [ -z "$ALB_DNS" ] || [ "$ALB_DNS" = "None" ]; then
  ALB_DNS=""
  echo "[bootstrap] ALB $ALB_NAME ainda nao existe: Grafana sobe, probe fica parado"
fi

# obs.env nao tem segredo: so enderecos. Lido pelo Grafana e pelo probe.
umask 022
{
  echo "QC_ENVIRONMENT=live"
  if [ -n "$ALB_DNS" ]; then
    echo "GF_SERVER_ROOT_URL=http://$ALB_DNS/grafana/"
    echo "QC_TARGET_URL=http://$ALB_DNS"
  else
    echo "GF_SERVER_ROOT_URL=http://localhost:3000/grafana/"
    echo "QC_TARGET_URL="
  fi
} > /opt/qc-obs/obs.env

# Senha do admin do Grafana: SecureString no SSM, criada por obs.sh secrets.
# Vai para um arquivo 600 do root, nunca para o log nem para o S3.
umask 077
pw=$(aws ssm get-parameter --name /qc-assistant/obs/grafana-admin --with-decryption \
  --query Parameter.Value --output text)
printf 'GF_SECURITY_ADMIN_PASSWORD=%s\n' "$pw" > /opt/qc-obs/grafana.env.new
unset pw
chmod 600 /opt/qc-obs/grafana.env.new
mv /opt/qc-obs/grafana.env.new /opt/qc-obs/grafana.env
umask 022

cd "$APP/ops/cloud"
profiles=()
# Probe so com alvo (ALB) e com o codigo ja sincronizado: sem isso o conteiner
# ficaria reiniciando em loop.
if [ -n "$ALB_DNS" ] && [ -f "$APP/ops/cloud/probe/probe.py" ]; then
  profiles=(--profile probe)
fi
docker compose "${profiles[@]}" up -d --build --remove-orphans

# Arquivo unico montado (bind mount) fica preso ao inode antigo quando o
# `aws s3 sync` substitui o arquivo: o conteiner continuaria com a config velha.
# Reinicia (sem recriar: Prometheus e Jaeger mantem os dados) so o servico cuja
# configuracao mudou desde o ultimo bootstrap.
declare -A cfg=([otel-collector]="otel-collector.yaml" [prometheus]="prometheus.yml alert-rules.yml"
                [jaeger]="../jaeger.yaml" [loki]="../loki.yaml" [qc-probe]="probe/probe.py ../../evals/golden_graph_v1.jsonl scenarios.json")
mkdir -p /opt/qc-obs/state
for svc in "${!cfg[@]}"; do
  # shellcheck disable=SC2086
  sum=$(cat ${cfg[$svc]} 2>/dev/null | sha256sum | cut -c1-16)
  old=$(cat "/opt/qc-obs/state/$svc.sha" 2>/dev/null || true)
  if [ -n "$old" ] && [ "$sum" != "$old" ]; then
    echo "[bootstrap] configuracao de $svc mudou: reiniciando"
    docker compose "${profiles[@]}" restart "$svc" || true
  fi
  echo "$sum" > "/opt/qc-obs/state/$svc.sha"
done
docker compose "${profiles[@]}" ps --format 'table {{.Name}}\t{{.Status}}'
echo "[bootstrap] fim $(date -u +%FT%TZ)"
