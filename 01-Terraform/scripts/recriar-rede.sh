#!/usr/bin/env bash
# Recria a rede compartilhada fiap-lab (Lab 01.2) quando ela sumiu da conta,
# por exemplo depois de um reset do Learner Lab. Usa o mesmo Terraform do
# 02-Modules (vpc-call e depois RT-call) e so cria o que estiver faltando.
#
# Uso:
#   bash /workspaces/FIAP-AI-Devops-CICD/01-Terraform/scripts/recriar-rede.sh
#
# Resultado (stdout): uma linha "REDE_OK <vpc-id> <numero-de-subnets>".
# Progresso e mensagens do Terraform vao para stderr, para nao misturar.
# Rodar de novo e seguro: com a rede completa, o script nao muda nada.
set -euo pipefail

REGIAO="${REGIAO:-${AWS_REGION:-us-east-1}}"

# O script mora em <repo>/01-Terraform/scripts. Descobrir o repo a partir
# dele evita depender da pasta em que o aluno abriu o terminal.
if [ -z "${REPO:-}" ]; then
  REPO="$(cd "$(dirname "$0")/../.." && pwd)"
  [ -d "$REPO/01-Terraform/demos/02-Modules" ] || REPO=/workspaces/FIAP-AI-Devops-CICD
fi
MODULOS="$REPO/01-Terraform/demos/02-Modules"

# Sem pager: a AWS CLI abriria o "less" e o script ficaria parado esperando tecla.
export AWS_PAGER=""

log() { echo "$*" >&2; }
falha() { log ""; log "ERRO: $*"; exit 1; }
chame_professor() {
  log ""
  log "PARE AQUI: $*"
  log "O script nao mexeu em nada. Chame o professor e mostre esta mensagem."
  exit 2
}

aws_txt() { aws "$@" --region "$REGIAO" --output text; }
contar_vpcs() { aws_txt ec2 describe-vpcs --filters Name=tag:Name,Values=fiap-lab --query 'length(Vpcs)'; }
id_vpc() { aws_txt ec2 describe-vpcs --filters Name=tag:Name,Values=fiap-lab --query 'Vpcs[0].VpcId'; }
contar_subnets() {
  aws_txt ec2 describe-subnets --filters Name=vpc-id,Values="$1" Name=tag:Tier,Values=Public --query 'length(Subnets)'
}
contar_igw() {
  aws_txt ec2 describe-internet-gateways --filters Name=attachment.vpc-id,Values="$1" --query 'length(InternetGateways)'
}

# Situacao da route table to-igw-fiap-lab: "ok", "falta", "varias" ou
# "incompleta <rtb-id>". Completa = rota 0.0.0.0/0 para um igw- e todas as
# subnets publicas associadas a ela.
situacao_rt() {
  local linhas n_subnets rtb gw assoc
  linhas="$(aws_txt ec2 describe-route-tables \
    --filters Name=vpc-id,Values="$1" Name=tag:Name,Values=to-igw-fiap-lab \
    --query "RouteTables[].[RouteTableId, Routes[?DestinationCidrBlock=='0.0.0.0/0'].GatewayId|[0], length(Associations[?SubnetId])]")"
  if [ -z "$linhas" ]; then echo falta; return; fi
  case "$linhas" in *$'\n'*) echo varias; return ;; esac
  n_subnets="$(contar_subnets "$1")"
  read -r rtb gw assoc <<< "$linhas"
  case "$gw" in
    igw-*) [ "$assoc" = "$n_subnets" ] && { echo ok; return; } ;;
  esac
  echo "incompleta $rtb"
}

# O state local guarda os IDs que o Terraform desta maquina criou. Rodar o
# apply numa pasta cujo state nao conhece o recurso criaria um SEGUNDO
# recurso igual (e as demos passariam a falhar com "multiple EC2 VPCs matched").
state_conhece() { grep -qs "\"$2\"" "$MODULOS/$1/terraform.tfstate"; }

aplicar() {
  log ""
  log ">> Terraform em $1 (regiao $REGIAO)"
  terraform -chdir="$MODULOS/$1" init -input=false >&2 \
    || falha "o terraform init falhou em $1. Leia a mensagem acima e rode o script de novo."
  terraform -chdir="$MODULOS/$1" apply -auto-approve -input=false -var "aws_region=$REGIAO" >&2 \
    || falha "o terraform apply falhou em $1. Leia a mensagem acima e rode o script de novo: ele continua de onde parou."
}

[ -d "$MODULOS/vpc-call" ] && [ -d "$MODULOS/RT-call" ] \
  || falha "nao achei $MODULOS. Rode o script de dentro do Codespace do repositorio FIAP-AI-Devops-CICD."

log ">> Conferindo as credenciais AWS..."
if ! erro="$(aws sts get-caller-identity 2>&1 >/dev/null)"; then
  falha "as credenciais AWS venceram ou nao estao configuradas.
Resposta da AWS: $(printf '%s' "$erro" | tr '\n' ' ')
Atualize as credenciais (00-create-codespaces/Inicio-de-aula.md) e rode o script de novo."
fi

log ">> Procurando a VPC fiap-lab em $REGIAO..."
qtd="$(contar_vpcs)"
case "$qtd" in
  0)
    log "Nenhuma VPC fiap-lab encontrada: criando a rede do zero (leva cerca de 1 minuto)."
    aplicar vpc-call
    aplicar RT-call
    ;;
  1)
    vpc="$(id_vpc)"
    log "A VPC $vpc ja existe: nenhuma VPC nova sera criada. Conferindo subnets e rotas..."
    n_subnets="$(contar_subnets "$vpc")"
    n_igw="$(contar_igw "$vpc")"
    if [ "$n_subnets" = 0 ] || [ "$n_igw" = 0 ]; then
      # VPC pela metade, tipicamente um apply interrompido. So da para completar
      # pelo Terraform se foi este Codespace que comecou a cria-la.
      state_conhece vpc-call "$vpc" \
        || chame_professor "a VPC $vpc existe, mas esta sem subnets publicas ou sem internet gateway, e este Codespace nao tem o state que a criou."
      aplicar vpc-call
    fi
    rt="$(situacao_rt "$vpc")"
    case "$rt" in
      ok) log "Subnets e route table completas: nada a fazer." ;;
      falta) aplicar RT-call ;;
      varias) chame_professor "existe mais de uma route table to-igw-fiap-lab na VPC $vpc." ;;
      *)
        state_conhece RT-call "${rt#incompleta }" \
          || chame_professor "a route table ${rt#incompleta } esta sem a rota para a internet ou sem todas as subnets, e este Codespace nao tem o state que a criou."
        aplicar RT-call
        ;;
    esac
    ;;
  *)
    log ""
    log "Existem $qtd VPCs com o nome fiap-lab em $REGIAO. As demos seguintes param com o erro"
    log "\"multiple EC2 VPCs matched\", porque o Terraform nao sabe qual delas usar."
    log "Escolher qual fica exige olhar o que roda em cada uma, por isso o script nao apaga nenhuma:"
    aws ec2 describe-vpcs --region "$REGIAO" --filters Name=tag:Name,Values=fiap-lab \
      --query 'Vpcs[].{VpcId:VpcId,CIDR:CidrBlock,Estado:State}' --output table >&2
    chame_professor "ha $qtd VPCs fiap-lab em $REGIAO."
    ;;
esac

log ""
log ">> Validacao final..."
[ "$(contar_vpcs)" = 1 ] || falha "esperava 1 VPC fiap-lab em $REGIAO e encontrei $(contar_vpcs)."
vpc="$(id_vpc)"
n_subnets="$(contar_subnets "$vpc")"
[ "$n_subnets" -gt 0 ] || falha "a VPC $vpc nao tem subnets publicas (Tier=Public)."
[ "$(situacao_rt "$vpc")" = ok ] \
  || falha "a route table to-igw-fiap-lab nao tem a rota 0.0.0.0/0 para o internet gateway ou nao esta associada a todas as subnets."
log "Rede pronta: VPC $vpc com $n_subnets subnets publicas e rota para a internet. Volte ao seu lab."
echo "REDE_OK $vpc $n_subnets"
