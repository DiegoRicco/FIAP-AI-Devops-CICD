#!/usr/bin/env bash
# Constroi a imagem do app NO RUNNER, sem Docker no Codespaces.
#
# Por que no runner: ele e amd64 (a mesma arquitetura do Fargate), ja tem
# Docker (runner-docker.sh) e a LabRole dele pode enviar imagem ao ECR. Assim o
# aluno nao instala nada e o build e o mesmo do pipeline.
#
# Caminho: empacota esta pasta (sem .terraform, .venv, dist, reports,
# resultados) -> s3://<bucket>/quantum-assistant/build-context/<sha>.tgz ->
# SSM no runner: baixa, roda scripts/cloud/build-push.sh -> digest no stdout.
# O pacote e apagado do runner no final; no S3 ele fica como registro.
#
# Uso: bash scripts/cloud/remote-build.sh [--bucket base-config-SEU-RM]
# Saida: stdout = sha256:... ; stderr = progresso (inclui o log do build).
set -euo pipefail

export AWS_PAGER=""
AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
export AWS_REGION
PROJECT="${PROJECT:-qc-assistant}"
RUNNER_TAG="${RUNNER_TAG:-quantum-ai-gitlab-runner}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BUCKET="${TF_STATE_BUCKET:-}"
while [ $# -gt 0 ]; do
  case "$1" in --bucket) BUCKET="$2"; shift 2 ;; *) echo "remote-build: opcao desconhecida: $1" >&2; exit 2 ;; esac
done
log() { echo "remote-build: $*" >&2; }
[ -n "$BUCKET" ] || BUCKET="$(aws s3api list-buckets --query 'Buckets[].Name' --output text | tr '\t' '\n' | grep '^base-config-' | head -1)"
[ -n "$BUCKET" ] || { log "ERRO: nenhum bucket base-config-* (rode make bootstrap)"; exit 1; }

INSTANCE="$(aws ec2 describe-instances --filters "Name=tag:Name,Values=$RUNNER_TAG" Name=instance-state-name,Values=running \
  --query 'Reservations[].Instances[].InstanceId' --output text)"
case "$INSTANCE" in i-*[[:space:]]*|"") log "ERRO: preciso de exatamente 1 runner ligado (make runner)"; exit 1 ;; esac

GIT_SHA="$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || echo sem-git)"
# Arvore com mudanca nao commitada: o commit sozinho mentiria sobre o codigo
# da imagem. Marcamos "-dirty-<hash do contexto>" (calculado abaixo).
DIRTY=""
[ -n "$(git -C "$ROOT" status --porcelain -- . 2>/dev/null)" ] && DIRTY=1
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
# COPYFILE_DISABLE: o tar do macOS nao inclui os arquivos ._ de metadados.
COPYFILE_DISABLE=1 tar -C "$ROOT" -czf "$WORK/ctx.tgz" \
  --exclude .terraform --exclude .venv --exclude dist --exclude reports --exclude __pycache__ \
  --exclude 'ops/results' --exclude '*.tfstate*' --exclude '*.tfplan' --exclude .git .
SHA="$(shasum -a 256 "$WORK/ctx.tgz" 2>/dev/null | awk '{print $1}' || sha256sum "$WORK/ctx.tgz" | awk '{print $1}')"
KEY="quantum-assistant/build-context/$SHA.tgz"
[ -n "$DIRTY" ] && { GIT_SHA="$GIT_SHA-dirty-${SHA:0:8}"; log "arvore com mudancas nao commitadas: git_sha=$GIT_SHA"; }
log "contexto $(du -h "$WORK/ctx.tgz" | awk '{print $1}') -> s3://$BUCKET/$KEY"
aws s3 cp --quiet "$WORK/ctx.tgz" "s3://$BUCKET/$KEY"

read -r -d '' REMOTE <<EOF || true
set -euo pipefail
export AWS_REGION=$AWS_REGION AWS_PAGER=""
D=\$(mktemp -d)
trap 'rm -rf "\$D"' EXIT
aws s3 cp --quiet s3://$BUCKET/$KEY "\$D/ctx.tgz"
mkdir "\$D/ctx" && tar -xzf "\$D/ctx.tgz" -C "\$D/ctx"
chown -R gitlab-runner "\$D"
# Mesmo usuario dos jobs do pipeline: prova que o CI tambem consegue.
sudo -u gitlab-runner env AWS_REGION=$AWS_REGION PROJECT=$PROJECT bash "\$D/ctx/scripts/cloud/build-push.sh" --git-sha $GIT_SHA --context "\$D/ctx" 2>"\$D/build.log" > "\$D/digest" || { tail -40 "\$D/build.log"; exit 1; }
tail -6 "\$D/build.log"
echo "DIGEST=\$(cat "\$D/digest")"
EOF
B64="$(printf '%s\n' "$REMOTE" | base64 | tr -d '\n')"
PARAMS="$(jq -n --arg c "echo $B64 | base64 -d | bash" '{commands: [$c]}')"
CMD="$(aws ssm send-command --instance-ids "$INSTANCE" --document-name AWS-RunShellScript \
  --comment "remote-build $PROJECT-app" --timeout-seconds 1800 --parameters "$PARAMS" --query Command.CommandId --output text)"
log "build no runner $INSTANCE (SSM $CMD); 1 a 3 min..."
STATUS=Pending
for _ in $(seq 1 300); do
  sleep 6
  STATUS="$(aws ssm get-command-invocation --command-id "$CMD" --instance-id "$INSTANCE" --query Status --output text 2>/dev/null || echo Pending)"
  case "$STATUS" in Pending|InProgress|Delayed) ;; *) break ;; esac
done
OUT="$(aws ssm get-command-invocation --command-id "$CMD" --instance-id "$INSTANCE" --query StandardOutputContent --output text)"
sed -E 's/[0-9]{12}([.:])/****\1/g' <<<"$OUT" | grep -v '^DIGEST=' >&2 || true
[ "$STATUS" = Success ] || { log "ERRO: build no runner terminou com $STATUS"; exit 1; }
DIGEST="$(sed -n 's/^DIGEST=//p' <<<"$OUT")"
[[ "$DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]] || { log "ERRO: o runner nao devolveu um digest"; exit 1; }
aws ssm put-parameter --name "/$PROJECT/image-digest" --type String --value "$DIGEST" --overwrite >/dev/null
log "digest gravado em /$PROJECT/image-digest"
echo "$DIGEST"
