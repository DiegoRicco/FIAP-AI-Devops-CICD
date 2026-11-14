#!/usr/bin/env bash
# Imprime, ja montados, os links que o aluno abre nas Demos 5.1 e 5.2.
#
# Por que existe: trocar SEU-USUARIO a mao em cada URL do GitLab e catar o DNS
# do ALB no meio do log atrasa a aula e gera erro de digitacao sem ensinar nada.
# O usuario do GitLab vem da propria chave SSH (mesmo truque do lab 02.1) e o
# endereco do ALB vem do SSM, gravado pelo `make up`.
#
# Uso: bash scripts/cloud/links.sh   (ou: make links)
# Saida: stdout = "nome  url" por linha; stderr = avisos do que faltou.
set -uo pipefail
export AWS_PAGER=""
PROJECT_SLUG="${PROJECT_SLUG:-quantum-ai-assistant}"

log() { echo "$*" >&2; }

# O GitLab responde "Welcome to GitLab, @usuario!" a quem tem a chave cadastrada.
GLUSER="${GITLAB_USER:-$(ssh -o StrictHostKeyChecking=accept-new -o BatchMode=yes -T git@gitlab.com 2>&1 \
  | sed -n 's/.*@\([A-Za-z0-9._-]*\)!.*/\1/p')}"
if [ -n "$GLUSER" ]; then
  G="https://gitlab.com/$GLUSER/$PROJECT_SLUG"
  printf '%-22s %s\n' "projeto" "$G"
  printf '%-22s %s\n' "pipelines" "$G/-/pipelines"
  printf '%-22s %s\n' "runners" "$G/-/settings/ci_cd#js-runners-settings"
  printf '%-22s %s\n' "novo-runner" "$G/-/runners/new"
else
  log "links: nao descobri seu usuario do GitLab pela chave SSH."
  log "       carregue a chave (eval \$(ssh-agent -s) && ssh-add -k /home/vscode/.ssh/gitlab) e rode de novo,"
  log "       ou informe direto: GITLAB_USER=seu-usuario make links"
fi

ALB="$(aws ssm get-parameter --name /qc-assistant/ecs/alb-url --query Parameter.Value --output text 2>/dev/null)"
if [ -n "$ALB" ] && [ "$ALB" != "None" ]; then
  printf '%-22s %s\n' "central-live" "$ALB/"
  printf '%-22s %s\n' "central-candidate" "$ALB/candidate/"
  printf '%-22s %s\n' "grafana" "$ALB/grafana/"
  # UIDs fixos dos dashboards provisionados em ops/cloud/grafana/dashboards/.
  printf '%-22s %s\n' "painel-helena" "$ALB/grafana/d/qc-negocio"
  printf '%-22s %s\n' "painel-diego" "$ALB/grafana/d/quantum-demo52"
  printf '%-22s %s\n' "painel-investigacao" "$ALB/grafana/d/qc-investigacao"
else
  log "links: o ALB ainda nao existe nesta conta (rode make up) ou a credencial AWS expirou."
fi
