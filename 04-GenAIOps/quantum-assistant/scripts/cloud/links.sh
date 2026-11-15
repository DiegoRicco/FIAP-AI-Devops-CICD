#!/usr/bin/env bash
# Imprime, ja montados, os links que o aluno abre nas Demos 5.1 e 5.2.
#
# Por que existe: trocar SEU-USUARIO a mao em cada URL do GitLab e catar o DNS
# do ALB no meio do log atrasa a aula e gera erro de digitacao sem ensinar nada.
# O usuario do GitLab vem da propria chave SSH (mesmo truque do lab 02.1) e o
# endereco do ALB vem do SSM, gravado pelo `make up`.
#
# Uso: bash scripts/cloud/links.sh [nome]   (ou: make links / make links central-live)
# Saida sem nome: stdout = "nome  url" por linha; stderr = avisos do que faltou.
# Saida com nome: stdout = so a instrucao de abrir e a URL desse link; rc!=0 se
#                 o nome nao existe ou se o link ainda nao pode ser montado.
set -uo pipefail
export AWS_PAGER=""
PROJECT_SLUG="${PROJECT_SLUG:-quantum-ai-assistant}"
GITLAB_LINKS="projeto pipelines runners novo-runner"
ALB_LINKS="central-live central-candidate grafana painel-helena painel-diego painel-investigacao"

log() { echo "$*" >&2; }

if [ $# -gt 1 ]; then log "uso: make links [nome]   (um nome por vez)"; exit 2; fi
WANT="${1:-}"
FOUND=""

# Valida o nome antes de chamar ssh/aws: erro de digitacao responde na hora.
case " $GITLAB_LINKS $ALB_LINKS " in
  *" $WANT "*|"  "*) ;;
  *) [ -z "$WANT" ] || { log "link desconhecido: $WANT. Disponiveis: $GITLAB_LINKS $ALB_LINKS"; exit 2; } ;;
esac
want_in() { [ -z "$WANT" ] || case " $1 " in *" $WANT "*) true ;; *) false ;; esac; }

emit() {
  if [ -z "$WANT" ]; then printf '%-22s %s\n' "$1" "$2"
  elif [ "$1" = "$WANT" ]; then FOUND="$2"; fi
}

# Pedir um link do ALB nao deve depender da chave SSH do GitLab (e vice-versa).
if want_in "$GITLAB_LINKS"; then
  # O GitLab responde "Welcome to GitLab, @usuario!" a quem tem a chave cadastrada.
  GLUSER="${GITLAB_USER:-$(ssh -o StrictHostKeyChecking=accept-new -o BatchMode=yes -T git@gitlab.com 2>&1 \
    | sed -n 's/.*@\([A-Za-z0-9._-]*\)!.*/\1/p')}"
  if [ -n "$GLUSER" ]; then
    G="https://gitlab.com/$GLUSER/$PROJECT_SLUG"
    emit "projeto" "$G"
    emit "pipelines" "$G/-/pipelines"
    emit "runners" "$G/-/settings/ci_cd#js-runners-settings"
    emit "novo-runner" "$G/-/runners/new"
  else
    log "links: nao descobri seu usuario do GitLab pela chave SSH."
    log "       carregue a chave (eval \$(ssh-agent -s) && ssh-add -k /home/vscode/.ssh/gitlab) e rode de novo,"
    log "       ou informe direto: GITLAB_USER=seu-usuario make links${WANT:+ $WANT}"
  fi
fi

if want_in "$ALB_LINKS"; then
  ALB="$(aws ssm get-parameter --name /qc-assistant/ecs/alb-url --query Parameter.Value --output text 2>/dev/null)"
  if [ -n "$ALB" ] && [ "$ALB" != "None" ]; then
    emit "central-live" "$ALB/"
    emit "central-candidate" "$ALB/candidate/"
    emit "grafana" "$ALB/grafana/"
    # UIDs fixos dos dashboards provisionados em ops/cloud/grafana/dashboards/.
    emit "painel-helena" "$ALB/grafana/d/qc-negocio"
    emit "painel-diego" "$ALB/grafana/d/quantum-demo52"
    emit "painel-investigacao" "$ALB/grafana/d/qc-investigacao"
  else
    log "links: o ALB ainda nao existe nesta conta (rode make up) ou a credencial AWS expirou."
  fi
fi

if [ -n "$WANT" ]; then
  [ -n "$FOUND" ] || exit 1
  echo "Abra no navegador (Ctrl+clique; no Mac, Cmd+clique):"
  echo "  $FOUND"
fi
