#!/usr/bin/env bash
# runner-test.sh - prova que o runner EXECUTA um job, nao so que esta registrado.
#
# 'gitlab-runner verify' so prova que o token vale. Este script empurra um commit
# para a branch propria 'runner-smoke' do seu repositorio GitLab. Essa branch tem
# so um .gitlab-ci.yml com UM job 'runner-smoke' (tags: [shell]), entao o pipeline
# do projeto (main) nao roda. Depois acompanha o job pelo journal do gitlab-runner
# na EC2, via SSM, e mostra 'Job succeeded' ou o erro.
#
# Por que o journal e nao a API do GitLab: a API publica devolve 429 (Too Many
# Requests) se consultada a cada poucos segundos. O journal da EC2 nao tem limite
# e mostra o que o runner fez de fato.
#
# Uso:
#   GITLAB_REPO_SSH=git@gitlab.com:SEU-USUARIO/primeiro-projeto.git ./runner-test.sh
#
# Variaveis opcionais: RUNNER_TAG (padrao quantum-ai-gitlab-runner), AWS_REGION,
# SMOKE_BRANCH (padrao runner-smoke), SMOKE_TIMEOUT_S (padrao 600).
#
# Saida: stdout = resultado (chave=valor); stderr = progresso.
# exit 0 = Job succeeded | 1 = job falhou | 2 = job nao chegou ao runner (timeout)
#
# Repetir e seguro: cada execucao acrescenta um commit na branch runner-smoke
# (push normal, sem --force). A branch pode ficar no projeto; ela nao afeta a main.
set -euo pipefail

log()  { printf '[runner-test] %s\n' "$*" >&2; }
hint() { printf '[runner-test]   dica: %s\n' "$*" >&2; }
out()  { printf '%s=%s\n' "$1" "$2"; }

: "${GITLAB_REPO_SSH:?defina GITLAB_REPO_SSH, ex.: git@gitlab.com:SEU-USUARIO/primeiro-projeto.git}"
RUNNER_TAG="${RUNNER_TAG:-quantum-ai-gitlab-runner}"
SMOKE_BRANCH="${SMOKE_BRANCH:-runner-smoke}"
SMOKE_TIMEOUT_S="${SMOKE_TIMEOUT_S:-600}"
AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-$(aws configure get region 2>/dev/null || true)}}"
AWS_REGION="${AWS_REGION:-us-east-1}"
export AWS_REGION AWS_DEFAULT_REGION="$AWS_REGION"

log "1/4 Localizando a EC2 do runner (tag Name=$RUNNER_TAG)..."
INSTANCE_ID="$(aws ec2 describe-instances \
  --filters "Name=tag:Name,Values=$RUNNER_TAG" "Name=instance-state-name,Values=running" \
  --query 'Reservations[].Instances[].InstanceId' --output text)"
if [ -z "$INSTANCE_ID" ] || [ "$(wc -w <<<"$INSTANCE_ID")" -ne 1 ]; then
  log "ERRO: esperava 1 EC2 running com essa tag e encontrei: '${INSTANCE_ID:-nenhuma}'."
  hint "rode antes ./runner-preflight.sh e ./runner-recover.sh --apply."
  exit 2
fi
log "    $INSTANCE_ID"

# ------------------------------------------------------------ commit na branch
SMOKE_ID="smoke-$(date -u +%Y%m%dT%H%M%SZ)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
log "2/4 Preparando o commit $SMOKE_ID na branch $SMOKE_BRANCH (copia temporaria em $WORK)..."
git -C "$WORK" init -q
git -C "$WORK" remote add origin "$GITLAB_REPO_SSH"
if git -C "$WORK" ls-remote --exit-code --heads origin "$SMOKE_BRANCH" >/dev/null 2>&1; then
  git -C "$WORK" fetch -q --depth 1 origin "$SMOKE_BRANCH"
  git -C "$WORK" checkout -q -b "$SMOKE_BRANCH" FETCH_HEAD
else
  # Branch orfa: nao herda nenhum arquivo da main, so o job de teste.
  git -C "$WORK" checkout -q --orphan "$SMOKE_BRANCH"
fi

# O job imprime so informacao publica da maquina. Nada de variavel de CI com token.
cat >"$WORK/.gitlab-ci.yml" <<'YAML'
# Pipeline minimo da branch runner-smoke, gerado pelo runner-test.sh.
# Prova que o runner com a tag "shell" pega e executa um job de verdade.
runner-smoke:
  tags: [shell]
  script:
    - echo "SMOKE_ID=$(cat SMOKE_ID)"
    - echo "host=$(hostname) usuario=$(whoami)"
    - terraform version | head -1
    - python3 --version
    - test -x /opt/venv/bin/python && /opt/venv/bin/python -c "import checkov, pytest; print('venv ok')"
YAML
echo "$SMOKE_ID" >"$WORK/SMOKE_ID"
git -C "$WORK" add .gitlab-ci.yml SMOKE_ID
NAME="$(git config user.name || true)"; MAIL="$(git config user.email || true)"
git -C "$WORK" -c user.name="${NAME:-Aluno Quantum Commerce}" -c user.email="${MAIL:-aluno@example.invalid}" \
  commit -q -m "ci(smoke): teste do runner $SMOKE_ID"

# Marca o inicio ANTES do push: so contam linhas do journal posteriores a isto.
SINCE="$(date -u +%s)"
log "3/4 git push origin $SMOKE_BRANCH (sem --force)..."
if ! git -C "$WORK" push -q origin "HEAD:refs/heads/$SMOKE_BRANCH" 2>"$WORK/push.err"; then
  log "ERRO: o push falhou: $(tr '\n' ' ' <"$WORK/push.err" | head -c 300)"
  hint "Permission denied (publickey) = a chave SSH deste terminal nao esta no GitLab (Modulo 02)."
  hint "rejected (fetch first) = outra pessoa empurrou na branch ao mesmo tempo; rode de novo."
  exit 1
fi
out smoke_id "$SMOKE_ID"
out branch "$SMOKE_BRANCH"
out commit "$(git -C "$WORK" rev-parse --short HEAD)"

# ------------------------------------------------------------ acompanhar o job
# O script remoto (POSIX, roda com /bin/sh) le o journal a partir do push e so
# devolve linhas do job runner-smoke. O gitlab-runner escreve com codigos de cor
# ANSI entre a chave e o '=' (job_name<ESC>[0;m=...): removemos as cores ANTES de
# filtrar, senao nem o filtro nem a mascara de 'runner=' (prefixo do token) casam.
log "4/4 Acompanhando o journal do gitlab-runner via SSM (ate ${SMOKE_TIMEOUT_S}s)..."
REMOTE="journalctl -u gitlab-runner --since @$SINCE -o cat --no-pager \
 | sed -E 's/\\x1b\\[[0-9;]*m//g' \
 | grep -E 'job_name=runner-smoke' \
 | sed -E 's/runner=[A-Za-z0-9_-]+/runner=***/g; s/glrt-[A-Za-z0-9_-]+/glrt-***/g' | tail -20"
PARAMS="$(jq -Rs '{commands: [.]}' <<<"$REMOTE")"
START="$(date +%s)"; RECEIVED=no; LINES=""
while [ $(( $(date +%s) - START )) -lt "$SMOKE_TIMEOUT_S" ]; do
  sleep 10
  CMD="$(aws ssm send-command --instance-ids "$INSTANCE_ID" --document-name AWS-RunShellScript \
    --comment "runner-test (le o journal)" --parameters "$PARAMS" --query Command.CommandId --output text)"
  sleep 3
  LINES="$(aws ssm get-command-invocation --command-id "$CMD" --instance-id "$INSTANCE_ID" \
    --query StandardOutputContent --output text 2>/dev/null || true)"
  if [ "$RECEIVED" = no ] && grep -q 'job_name=runner-smoke' <<<"$LINES"; then
    RECEIVED=yes
    log "    o runner recebeu o job apos $(( $(date +%s) - START ))s"
  fi
  if grep -q 'Job succeeded.*job_name=runner-smoke' <<<"$LINES"; then
    LINE="$(grep 'Job succeeded.*job_name=runner-smoke' <<<"$LINES" | tail -1)"
    out job_id "$(sed -nE 's/.* job=([0-9]+).*/\1/p' <<<"$LINE")"
    out pipeline_id "$(sed -nE 's/.*pipeline_id=([0-9]+).*/\1/p' <<<"$LINE")"
    out duration_s "$(sed -nE 's/.*duration_s=([0-9]+).*/\1/p' <<<"$LINE")"
    out result "Job succeeded"
    log "Job succeeded: o runner pegou o job pela tag 'shell' e executou o script."
    exit 0
  fi
  if grep -qE 'Job failed.*job_name=runner-smoke|job_name=runner-smoke.*Job failed' <<<"$LINES"; then
    grep -E 'Job failed' <<<"$LINES" | tail -3 >&2
    out result "Job failed"
    log "o runner executou o job, mas um comando falhou. Veja o log do job na UI (CI/CD > Jobs)."
    hint "'prepare environment: exit status 1' = .bash_logout do gitlab-runner (o playbook do Modulo 02 corrige)."
    hint "'terraform: command not found' / 'venv' = ferramentas faltando: ./runner-recover.sh nao reinstala um runner saudavel; rode o playbook do Modulo 02."
    exit 1
  fi
  if [ "$RECEIVED" = no ] && [ $(( $(date +%s) - START )) -ge 120 ]; then
    log "    ainda nada no runner apos $(( $(date +%s) - START ))s (job Pending no GitLab?)"
  fi
done

out result timeout
log "o job nao terminou em ${SMOKE_TIMEOUT_S}s."
if [ "$RECEIVED" = no ]; then
  hint "Pending = nenhum runner online com a tag 'shell' para este projeto. Confira em Settings > CI/CD > Runners:"
  hint "  bolinha verde (online)? tags shell,terraform? o runner esta habilitado NESTE projeto?"
  hint "  'This job is stuck because ... tags' = tags incompativeis entre o job e o runner."
  hint "token revogado: o preflight mostra verify=invalid; crie runner novo na UI e grave o glrt- no SSM."
  hint "o runner roda 1 job por vez (concurrent=1): se outro pipeline estiver rodando, o smoke espera na fila."
fi
exit 2
