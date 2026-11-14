# Imagem do probe de qualidade: python slim pinado + OpenTelemetry pinado.
# O CODIGO nao entra na imagem: vem montado do bundle sincronizado do S3
# (/opt/qc-obs/app), entao mudar o golden set nao exige rebuild.
FROM python:3.12.16-slim-bookworm
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt
# Sem root: o probe so precisa ler /app e escrever em /data.
RUN useradd --uid 10001 --create-home probe
USER 10001
