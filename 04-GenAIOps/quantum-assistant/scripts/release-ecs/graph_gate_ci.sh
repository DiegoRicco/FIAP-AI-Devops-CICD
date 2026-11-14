#!/usr/bin/env bash
# graph-policy-gate no runner: seed primeiro, depois golden_graph contra um
# Neo4j EFEMERO local.
#
# Por que efemero e local: o gate nao pode depender do candidate (ele ainda nao
# existe nesta fase) nem mexer no banco de ninguem. O Neo4j sobe so neste job,
# escuta so em 127.0.0.1, usa senha aleatoria gerada aqui (arquivo chmod 600,
# nunca impressa) e e apagado no fim, com sucesso ou falha.
#
# Por que a mesma imagem do Neo4j do release: o espelho no ECR (pull e leitura,
# permitido a LabRole) e o mesmo digest que a task do ECS usa.
#
# Saida: reports/graph-gate.json, reports/junit-graph.xml (sempre).
# Exit 0 aprovado, 1 reprovado (seed incoerente, contagem critica ou media).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
export AWS_PAGER=""
AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
PROJECT="${PROJECT:-qc-assistant}"
REPORTS="${REPORTS:-$ROOT/reports}"
VENV="${QC_ENGINE_VENV:-$HOME/.cache/qc-assistant/venv-engine}"
export QC_EMBEDDING_MODEL_DIR="${QC_EMBEDDING_MODEL_DIR:-$HOME/.cache/qc-assistant/models/paraphrase-multilingual-MiniLM-L12-v2@e8f8c21}"
NAME="qc-gate-neo4j-${CI_JOB_ID:-local}"
mkdir -p "$REPORTS"

log() { echo "graph-gate-ci: $*" >&2; }

# ---------------------------------------------------------- 1) seed (segundos)
# Seed incoerente reprova aqui, sem banco e sem modelo: e o Cenario B da aula.
set +e
python3 evals/graph_gate.py --target seed --out "$REPORTS/graph-seed.json" --junit "$REPORTS/junit-graph.xml"
rc=$?
set -e
if [ "$rc" -ne 0 ]; then
  log "seed/front matter incoerente: o golden de grafo nem roda (o JUnit acima mostra o achado)"
  cp "$REPORTS/graph-seed.json" "$REPORTS/graph-gate.json"
  exit 1
fi

# ------------------------------------------- 2) dependencias do motor (cache)
# venv proprio, fora do /opt/venv do Ansible: o motor precisa de onnxruntime e
# do driver neo4j, e nao pode quebrar checkov/pytest da Aula 3. Fica no HOME do
# runner e so e recriado quando requirements-engine.txt muda.
REQ_SHA="$(sha256sum requirements-engine.txt | cut -c1-12)"
if [ ! -f "$VENV/.req-$REQ_SHA" ]; then
  log "criando venv do motor em $VENV (requirements-engine.txt $REQ_SHA)..."
  rm -rf "$VENV"
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q --disable-pip-version-check -r requirements-engine.txt >&2
  touch "$VENV/.req-$REQ_SHA"
fi
if ! "$VENV/bin/python" -m vector.encoder --verify "$QC_EMBEDDING_MODEL_DIR" >/dev/null 2>&1; then
  log "baixando o modelo de embedding pinado (revisao fixa, sha256 conferido)..."
  "$VENV/bin/python" -m vector.encoder --download "$QC_EMBEDDING_MODEL_DIR" >/dev/null
fi

# ------------------------------------------------------- 3) Neo4j efemero
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
REGISTRY="$ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com"
NEO4J_DIGEST="$(python3 -c 'import json;print(json.load(open("infra-ecs/neo4j-image.json"))["digest"])')"
IMAGE="$REGISTRY/$PROJECT-neo4j@$NEO4J_DIGEST"
SECRET_DIR="$(mktemp -d)"
chmod 700 "$SECRET_DIR"
cleanup() {
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  rm -rf "$SECRET_DIR"
}
trap cleanup EXIT
python3 -c 'import secrets;print(secrets.token_hex(16))' > "$SECRET_DIR/neo4j.pw"
chmod 600 "$SECRET_DIR/neo4j.pw"
# Arquivo de ambiente, e nao -e NEO4J_AUTH=...: a senha nao aparece no ps nem no log.
printf 'NEO4J_AUTH=neo4j/%s\n' "$(cat "$SECRET_DIR/neo4j.pw")" > "$SECRET_DIR/neo4j.env"
chmod 600 "$SECRET_DIR/neo4j.env"

aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$REGISTRY" >/dev/null 2>&1
docker rm -f "$NAME" >/dev/null 2>&1 || true
# Porta alta aleatoria so em 127.0.0.1: nada fica exposto na rede da VPC.
PORT="$(python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()')"
log "subindo Neo4j efemero ($PROJECT-neo4j@${NEO4J_DIGEST:7:12}) em 127.0.0.1:$PORT..."
docker run -d --name "$NAME" -p "127.0.0.1:$PORT:7687" --env-file "$SECRET_DIR/neo4j.env" \
  -e NEO4J_server_memory_heap_max__size=512m -e NEO4J_server_memory_pagecache_size=128m \
  "$IMAGE" >/dev/null
docker logout "$REGISTRY" >/dev/null 2>&1 || true

set +e
"$VENV/bin/python" evals/graph_gate.py --target local --neo4j-uri "bolt://127.0.0.1:$PORT" \
  --neo4j-password-file "$SECRET_DIR/neo4j.pw" --bootstrap-timeout 300 \
  --out "$REPORTS/graph-gate.json" --junit "$REPORTS/junit-graph.xml"
rc=$?
set -e
log "graph-policy-gate terminou com exit $rc"
exit $rc
