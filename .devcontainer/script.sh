#!/bin/bash
set -euo pipefail
set -x

# Resolve a pasta do proprio script: funciona qualquer que seja o nome do fork/clone.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

sudo apt-get update -y
# jq e usado pelos labs que provisionam via SSM (envia o script e le o log/status
# do comando). python3-venv garante o modulo venv no Python do sistema.
sudo apt-get install -y jq python3-venv

mkdir -p ~/.aws/
cp "${SCRIPT_DIR}/config" ~/.aws/config

# Mesmo tooling que o lab 02-Ansible instala a mao (venv ~/venv + Ansible + boto3
# + collections). Ansible e boto3 precisam estar no MESMO Python para o connection
# plugin aws_ssm funcionar; por isso tudo vai para o venv, nunca para o sistema.
# Os passos manuais do lab continuam funcionando e viram no-op.
[ -d ~/venv ] || python3 -m venv ~/venv
~/venv/bin/pip install --quiet --upgrade pip
~/venv/bin/pip install --quiet ansible boto3 botocore
~/venv/bin/ansible-galaxy collection install community.aws amazon.aws

# Deixa ansible/ansible-playbook no PATH de qualquer terminal (login e interativo)
# sem exigir o "source ~/venv/bin/activate" em toda aula.
for rc in ~/.bashrc ~/.profile; do
  grep -q 'venv/bin' "$rc" 2>/dev/null || echo 'export PATH="$HOME/venv/bin:$PATH"' >> "$rc"
done

# session-manager-plugin: e o binario que o connection plugin aws_ssm do Ansible
# chama para abrir a sessao com a EC2 do runner (Modulo 02 e 'make runner' da
# Aula 5). Sem ele o playbook para no meio. Versao exata em vez de "latest" para
# todo aluno receber o mesmo binario; a AWS mantem as URLs versionadas no ar.
# A pasta muda por arquitetura: Codespaces e amd64, Mac com Apple Silicon e arm64.
# Fica depois do Ansible: se o download falhar, o venv ja esta pronto e o
# runner-recover.sh mostra o comando para instalar o plugin a mao.
SMP_VERSION=1.2.896.0
if ! command -v session-manager-plugin >/dev/null; then
  SMP_ARCH=ubuntu_64bit
  if [ "$(dpkg --print-architecture)" = arm64 ]; then SMP_ARCH=ubuntu_arm64; fi
  curl -fsSL "https://s3.amazonaws.com/session-manager-downloads/plugin/${SMP_VERSION}/${SMP_ARCH}/session-manager-plugin.deb" \
    -o /tmp/session-manager-plugin.deb
  sudo dpkg -i /tmp/session-manager-plugin.deb
  rm -f /tmp/session-manager-plugin.deb
fi

# Confirma as ferramentas de que os labs dependem.
aws --version
jq --version
terraform -version
~/venv/bin/ansible --version
session-manager-plugin --version
