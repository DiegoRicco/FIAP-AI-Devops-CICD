# Atalhos da Aula 5 (5.1 e 5.2). Cada alvo so chama um script de scripts/cloud/
# (ou do repositorio da disciplina): o Makefile nao tem logica propria, entao o
# que o aluno roda aqui e o mesmo que o pipeline roda.
#
# Rode na RAIZ do projeto quantum-ai-assistant (a copia desta pasta):
#   make help
#
# Variaveis:
#   RM=12345            seu RM (so no make bootstrap, se o bucket ainda nao existir)
#   BUCKET=base-config-12345   bucket do state (padrao: descoberto na conta)
#   COURSE_REPO=/workspaces/FIAP-AI-Devops-CICD   repositorio da disciplina
#   ENV=live|candidate  ambiente do deploy/diagnose (padrao: live)
#   DIGEST=sha256:...   imagem do deploy (padrao: a ultima do make image, no SSM)

SHELL := /bin/bash
.DEFAULT_GOAL := help

COURSE_REPO ?= /workspaces/FIAP-AI-Devops-CICD
ENV ?= live
AWS_REGION ?= us-east-1
export AWS_REGION AWS_PAGER=
export COURSE_REPO

# Bucket: o do parametro BUCKET, senao o base-config-* da conta.
BUCKET ?= $(shell aws s3api list-buckets --query 'Buckets[].Name' --output text 2>/dev/null | tr '\t' '\n' | grep '^base-config-' | head -1)
TF_STATE_BUCKET := $(BUCKET)
export TF_STATE_BUCKET

RUNNER_DIR := $(COURSE_REPO)/04-GenAIOps/shared/runner
TF_ECS := terraform -chdir=infra-ecs
# Copia de trabalho do Terraform fora do projeto: o init nao deixa .terraform
# nem lock no repositorio do aluno.
export TF_DATA_DIR := $(HOME)/.cache/qc-assistant/tf-ecs

.PHONY: down-dry-run help bootstrap preflight runner runner-docker orders-up secrets mirror-neo4j up image deploy deploy-live deploy-candidate ready diagnose down start plan destroy-dry-run course-repo

help: ## Mostra os alvos
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-18s %s\n", $$1, $$2}'

course-repo:
	@[ -d "$(COURSE_REPO)/04-GenAIOps/shared/runner" ] || { echo "Nao achei o repositorio da disciplina em $(COURSE_REPO)."; echo "Clone-o ou rode com COURSE_REPO=/caminho/do/FIAP-AI-Devops-CICD"; exit 1; }

bootstrap: course-repo ## Prepara a conta: bucket, rede fiap-lab, API de pedidos, segredos (idempotente)
	@aws sts get-caller-identity >/dev/null 2>&1 || { echo "Credencial AWS invalida/expirada: Start Lab e cole AWS Details em ~/.aws/credentials"; exit 1; }
	@if [ -z "$(BUCKET)" ]; then \
	  [ -n "$(RM)" ] || { echo "Nenhum bucket base-config-* na conta. Rode: make bootstrap RM=<seu-rm>"; exit 1; }; \
	  echo "Criando o bucket base-config-$(RM)..."; \
	  aws s3api create-bucket --bucket "base-config-$(RM)" --region us-east-1 >/dev/null; \
	  aws s3api put-bucket-versioning --bucket "base-config-$(RM)" --versioning-configuration Status=Enabled; \
	  aws s3api put-public-access-block --bucket "base-config-$(RM)" --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true; \
	else echo "Bucket do state: $(BUCKET)"; fi
	@n=$$(aws ec2 describe-vpcs --filters Name=tag:Name,Values=fiap-lab --query 'length(Vpcs)' --output text); \
	if [ "$$n" = "0" ]; then echo "Rede fiap-lab ausente: recriando..."; bash "$(COURSE_REPO)/01-Terraform/scripts/recriar-rede.sh"; else echo "Rede fiap-lab: ok"; fi
	@$(MAKE) --no-print-directory orders-up BUCKET="$${BUCKET:-base-config-$(RM)}"
	@$(MAKE) --no-print-directory secrets
	@echo "Bootstrap ok. Proximo: make runner e depois make preflight."

preflight: ## Confere a conta sem mudar nada (credencial, rede, runner, quotas, API de pedidos)
	@bash scripts/cloud/preflight.sh $(if $(BUCKET),--bucket $(BUCKET))

runner: course-repo ## Garante 1 GitLab Runner pronto (reusa o da Aula 3; senao usa o token do SSM)
	@rc=0; bash "$(RUNNER_DIR)/runner-preflight.sh" >/tmp/qc-runner-preflight.txt || rc=$$?; \
	d=$$(sed -n 's/^diagnosis=//p' /tmp/qc-runner-preflight.txt); echo "diagnostico do runner: $$d"; \
	case "$$d" in \
	  reuse) echo "Runner da EC2 pronto. No projeto NOVO: Settings > CI/CD > Runners > habilite o runner existente (tags shell,terraform)." ;; \
	  blocked) echo "Bloqueado: leia a dica acima. Sem runner, crie um no projeto novo (tags shell,terraform) e grave o glrt- em /fiap/gitlab-runner/token"; exit 1 ;; \
	  *) echo "Recuperando o runner (menor passo: $$d)..."; bash "$(RUNNER_DIR)/runner-recover.sh" --apply ;; \
	esac
	@$(MAKE) --no-print-directory runner-docker

runner-docker: course-repo ## Instala o Docker no runner (necessario para buildar a imagem)
	@bash "$(RUNNER_DIR)/runner-docker.sh"

orders-up: ## Implanta a API de pedidos (Lambda + DynamoDB + API Gateway) e grava a URL no SSM
	@bash scripts/cloud/orders-up.sh $(if $(BUCKET),--bucket $(BUCKET))

secrets: ## Cria as senhas do Neo4j e a chave admin no SSM (nunca exibe valor)
	@bash scripts/cloud/secrets.sh

mirror-neo4j: ## Copia o Neo4j pinado (infra-ecs/neo4j-image.json) para o ECR
	@bash scripts/cloud/mirror-neo4j.sh

up: ## Cria ECR, cluster ECS, ALB e services (Terraform, state quantum-assistant/ecs/)
	@$(TF_ECS) init -input=false -backend-config=bucket=$(BUCKET) >/dev/null
	@$(TF_ECS) apply -input=false
	@$(MAKE) --no-print-directory mirror-neo4j

image: ## Builda a imagem do app NO RUNNER e envia ao ECR (digest no SSM /qc-assistant/image-digest)
	@bash scripts/cloud/remote-build.sh $(if $(BUCKET),--bucket $(BUCKET))

deploy: ## Implanta DIGEST (ou o ultimo make image) em ENV=live|candidate e espera /ready
	@d="$(DIGEST)"; [ -n "$$d" ] || d=$$(aws ssm get-parameter --name /qc-assistant/image-digest --query Parameter.Value --output text); \
	bash scripts/cloud/deploy.sh $(ENV) "$$d"

deploy-live: ## Atalho: make deploy ENV=live
	@$(MAKE) --no-print-directory deploy ENV=live

deploy-candidate: ## Atalho: make deploy ENV=candidate
	@$(MAKE) --no-print-directory deploy ENV=candidate

ready: ## GET /ready do ambiente ENV pelo ALB
	@u=$$(aws ssm get-parameter --name /qc-assistant/ecs/alb-url --query Parameter.Value --output text); \
	p=$$([ "$(ENV)" = candidate ] && echo /candidate); curl -s -w '\nHTTP %{http_code}\n' "$$u$$p/ready"

diagnose: ## Estado do service, tasks, target group, eventos e logs (sem segredos)
	@bash scripts/cloud/diagnose.sh $(ENV)

down: ## Desliga as tasks (desired 0) dos dois ambientes; nada e apagado
	@bash scripts/cloud/stop.sh all

start: ## Liga de novo a task do ENV na mesma revisao
	@bash scripts/cloud/start.sh $(ENV)

plan: ## terraform plan do runtime (deve dizer "No changes" depois do up)
	@$(TF_ECS) init -input=false -backend-config=bucket=$(BUCKET) >/dev/null
	@$(TF_ECS) plan -input=false

destroy-dry-run: ## Lista o que o cleanup apagaria (so o state do runtime ECS)
	@bash scripts/cloud/cleanup.sh --dry-run $(if $(BUCKET),--bucket $(BUCKET))

down-dry-run: destroy-dry-run
