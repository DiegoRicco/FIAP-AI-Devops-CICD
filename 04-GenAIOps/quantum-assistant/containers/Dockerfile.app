# Imagem do assistente = o RELEASE: codigo + corpus + seed do grafo + modelo de
# embedding + cache de embeddings. O digest desta imagem no ECR e a identidade
# do release; rollback = voltar para o digest anterior (tudo volta junto).
#
# Build (contexto = raiz do projeto quantum-assistant, no runner amd64):
#   docker build --platform linux/amd64 --provenance=false \
#     -f containers/Dockerfile.app --build-arg QC_GIT_SHA=<commit> .
# Camadas na ordem do que MENOS muda para o que MAIS muda: dependencias ->
# modelo -> codigo/dados. Mudar so o corpus reaproveita as duas primeiras do
# cache e o rebuild leva segundos.

# Base por digest (python:3.12.16-slim, indice multiarquitetura): a tag pode
# ser reapontada no Docker Hub; o digest nao.
FROM python:3.12.16-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 1. Dependencias (pins == nos requirements). So muda quando um requirements muda.
COPY requirements-engine.txt requirements-web.txt requirements-ops.txt ./
RUN pip install --no-cache-dir -r requirements-engine.txt -r requirements-web.txt -r requirements-ops.txt

# 2. Modelo de embedding, baixado do Hugging Face NA REVISAO PINADA, com sha256
# conferido arquivo a arquivo (vector/encoder.py). Fica dentro da imagem: a
# task nunca baixa nada ao subir.
ENV QC_EMBEDDING_MODEL_DIR=/app/models/paraphrase-multilingual-MiniLM-L12-v2@e8f8c21
COPY vector/__init__.py vector/encoder.py vector/
RUN python -m vector.encoder --download "$QC_EMBEDDING_MODEL_DIR" \
 && python -m vector.encoder --verify "$QC_EMBEDDING_MODEL_DIR"

# 3. Codigo e dados versionados (mudam a cada commit).
COPY core/ core/
COPY corpus/ corpus/
COPY evals/ evals/
COPY prompts/ prompts/
COPY config/ config/
COPY orders/ orders/
COPY adapters/ adapters/
COPY graph/ graph/
COPY vector/ vector/
COPY lab/ lab/
COPY web/ web/

# 4. Cache dos embeddings dos chunks, com o MESMO modelo da camada 2: o boot da
# task so le este arquivo em vez de calcular os embeddings.
RUN python -m vector.build_cache

# Identidade do build, visivel em /health e /ready (versions.git_sha).
ARG QC_GIT_SHA=unknown
ENV QC_GIT_SHA=$QC_GIT_SHA

# Versoes estaticas do release (corpus, indice, modelo, grafo, git_sha) num
# arquivo dentro da imagem: o pipeline le daqui sem precisar subir o Neo4j.
RUN mkdir -p /app/release && python -m web.app.build_info > /app/release/build_info.json

# Usuario sem privilegio: o app so escreve em /tmp (pacote de dados opcional).
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin qc
USER 10001
ENV QC_DATA_DIR=/tmp/qc-data

# 8000 = interface e API (via ALB); 8001 = admin com HMAC (so o runner alcanca).
EXPOSE 8000 8001
CMD ["python", "-m", "web.app.serve"]
