#!/usr/bin/env bash
# Liga ou desliga as tasks de um ambiente sem apagar nada (stop.sh/start.sh).
#
# Por que existe: a task do Fargate cobra por hora ligada; o ALB, o ECR e as
# revisoes nao somem. Desligar ao fim da aula e ligar no comeco da proxima
# custa minutos de boot, nao um deploy novo. A revisao (imagem + dados) que
# volta e exatamente a que estava rodando.
#
# Uso: bash scripts/cloud/scale.sh <0|1> [live|candidate|all]
# Saida: stdout = "<service> desired=<n>" por ambiente; stderr = progresso.
set -euo pipefail
export AWS_PAGER=""
AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
export AWS_REGION
PROJECT="${PROJECT:-qc-assistant}"
CLUSTER="${CLUSTER:-$PROJECT}"
COUNT="${1:-}"; TARGET="${2:-all}"
case "$COUNT" in 0|1) ;; *) echo "uso: scale.sh <0|1> [live|candidate|all]" >&2; exit 2 ;; esac
case "$TARGET" in live|candidate) ENVS="$TARGET" ;; all) ENVS="live candidate" ;; *) echo "ambiente invalido: $TARGET" >&2; exit 2 ;; esac
for env in $ENVS; do
  svc="$PROJECT-$env"
  td="$(aws ecs describe-services --cluster "$CLUSTER" --services "$svc" --query 'services[0].taskDefinition' --output text)"
  # Ligar o template (imagem placeholder) nao serve para nada: exige deploy antes.
  if [ "$COUNT" = 1 ] && [ "$(aws ecs describe-task-definition --task-definition "$td" \
      --query "taskDefinition.containerDefinitions[?name=='app'].environment[] | [?name=='QC_IMAGE_DIGEST'].value | [0]" --output text)" = "template" ]; then
    echo "scale: $svc ainda esta no template (sem imagem do app). Rode make deploy-live (ou deploy.sh) primeiro." >&2
    exit 1
  fi
  aws ecs update-service --cluster "$CLUSTER" --service "$svc" --desired-count "$COUNT" --query 'service.serviceName' --output text >/dev/null
  echo "$svc desired=$COUNT"
done
if [ "$COUNT" = 1 ]; then
  echo "scale: tasks subindo; o /ready leva alguns minutos (make ready ou make diagnose)." >&2
else
  echo "scale: tasks desligando; ALB, ECR e revisoes continuam (custo do ALB segue por hora)." >&2
fi
