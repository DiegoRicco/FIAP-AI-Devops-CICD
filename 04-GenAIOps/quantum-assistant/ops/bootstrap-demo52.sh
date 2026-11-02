#!/usr/bin/env bash
# Sobe a Demo 5.2 do zero ou retoma de onde parou (idempotente).
# Independe da Demo 5.1: nao precisa de AWS, GitLab nem de artefato da 5.1.
#
#   bash ops/bootstrap-demo52.sh                    # stack + pedidos + assistente
#   bash ops/bootstrap-demo52.sh --from-live URL    # idem + compara versoes com a Lambda da 5.1
#
# O que faz, em ordem: confere docker e python -> cria/atualiza o venv com
# requirements-ops.txt -> sobe o stack (Collector, Jaeger, Prometheus, Grafana)
# -> sobe a API de pedidos (8081) e o assistente (8080) no host -> mostra URLs e
# versoes. Rodar de novo nao duplica nada: o que ja esta no ar e reaproveitado.
# Progresso vai para o stderr; o stdout recebe so o JSON final de versoes.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="$(dirname "$HERE")"
RUNTIME="${QC_RUNTIME_DIR:-$HERE/.runtime}"
# venv FORA do repositorio: nao entra no git e nao viaja junto com a pasta.
VENV="${QC_VENV:-$HOME/.venvs/quantum-demo52}"
FROM_LIVE=""
# 127.0.0.1 e nao localhost: em algumas maquinas "localhost" resolve para IPv6
# e cai em outro programa que use a mesma porta.
ASSISTANT_URL="http://127.0.0.1:8080"
ORDERS_URL="http://127.0.0.1:8081"

log() { printf '%s\n' "$*" >&2; }
die() { log "ERRO: $*"; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --from-live) FROM_LIVE="${2:-}"; [ -n "$FROM_LIVE" ] || die "--from-live precisa de uma URL"; shift 2 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) die "opcao desconhecida: $1" ;;
  esac
done

log "== 1/5 Pre-requisitos"
command -v docker >/dev/null || die "docker nao encontrado (no Codespaces ele vem do devcontainer)"
docker info >/dev/null 2>&1 || die "o Docker nao responde; espere o Codespace terminar de iniciar e rode de novo"
docker compose version >/dev/null 2>&1 || die "docker compose (v2) nao encontrado"
PY="${QC_PYTHON:-}"
if [ -z "$PY" ]; then
  for c in python3.12 python3; do command -v "$c" >/dev/null && { PY="$c"; break; }; done
fi
[ -n "$PY" ] || die "python3 nao encontrado"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || die "precisa de Python >= 3.10"
log "   docker $(docker version --format '{{.Server.Version}}'), $(docker compose version --short | sed 's/^/compose /'), $("$PY" --version)"

log "== 2/5 venv com requirements-ops.txt em $VENV"
[ -x "$VENV/bin/python" ] || "$PY" -m venv "$VENV"
"$VENV/bin/python" -m pip install -q --disable-pip-version-check -r "$APP/requirements-ops.txt"
log "   opentelemetry-sdk $("$VENV/bin/python" -c 'from importlib.metadata import version; print(version("opentelemetry-sdk"))')"

log "== 3/5 Stack de observabilidade (docker compose)"
docker compose -f "$HERE/docker-compose.yml" up -d --quiet-pull >&2
wait_http() {  # url, nome, tentativas
  for _ in $(seq 1 "${3:-60}"); do
    curl -fsS -o /dev/null "$1" 2>/dev/null && { log "   $2 ok"; return 0; }
    sleep 1
  done
  die "$2 nao respondeu em $1 (veja: docker compose -f ops/docker-compose.yml logs)"
}
wait_http "http://127.0.0.1:9090/-/ready" "Prometheus"
wait_http "http://127.0.0.1:16686/" "Jaeger"
wait_http "http://127.0.0.1:3000/api/health" "Grafana"
# O Collector nao tem endpoint de saude publicado; um POST vazio em /v1/metrics
# responde 2xx/4xx quando ele esta ouvindo (curl -s sem -f nao falha em 4xx).
for _ in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -d '{}' http://127.0.0.1:4318/v1/metrics || true)
  [ "$code" != "000" ] && { log "   OTel Collector ok (OTLP/HTTP 4318)"; break; }
  sleep 1
done

log "== 4/5 API de pedidos (8081) e assistente (8080)"
mkdir -p "$RUNTIME"
export OTEL_EXPORTER_OTLP_ENDPOINT="http://127.0.0.1:4318"
export OTEL_METRIC_EXPORT_INTERVAL=5000
export PYTHONDONTWRITEBYTECODE=1
start() {  # nome, url de saude, script
  local name="$1" health="$2" script="$3" pidf="$RUNTIME/$1.pid"
  if [ -f "$pidf" ] && kill -0 "$(cat "$pidf")" 2>/dev/null && curl -fsS -o /dev/null "$health" 2>/dev/null; then
    log "   $name ja estava no ar (pid $(cat "$pidf")); reaproveitado"
    return 0
  fi
  if curl -fsS -o /dev/null "$health" 2>/dev/null; then
    die "outra coisa ja responde em $health; pare-a ou rode ops/stop-demo52.sh"
  fi
  nohup "$VENV/bin/python" "$APP/$script" >>"$RUNTIME/$name.log" 2>&1 &
  echo $! >"$pidf"
  wait_http "$health" "$name" 30
}
start orders "$ORDERS_URL/health" adapters/orders_service.py
start assistant "$ASSISTANT_URL/health" adapters/http_server.py

log "== 5/5 Versoes do que esta atendendo"
HEALTH="$(curl -fsS "$ASSISTANT_URL/health")"
"$VENV/bin/python" - "$HEALTH" "$FROM_LIVE" <<'PY' >&2
import json, sys, urllib.request
h = json.loads(sys.argv[1]); live_url = sys.argv[2]
print(f"   ambiente={h['environment']} release_id={h['release_id']} corpus={h['corpus_source']} telemetria={h['telemetry']}")
for k, v in h["versions"].items():
    print(f"   {k:20} {v}")
if live_url:
    try:
        with urllib.request.urlopen(live_url.rstrip('/') + '/health', timeout=10) as r:
            live = json.loads(r.read().decode())
    except Exception as err:
        print(f"   --from-live: nao consegui ler {live_url}/health ({type(err).__name__}); comparacao NAO feita")
    else:
        lv = live.get("versions", {})
        print(f"   --from-live {live_url}: release_id publicado = {live.get('release_id')}")
        for k, v in h["versions"].items():
            mark = "igual" if lv.get(k) == v else "DIFERENTE"
            print(f"   {k:20} local={v:14} live={str(lv.get(k)):14} {mark}")
PY
cat >&2 <<MSG

Pronto. Abra (no Codespaces, pela aba PORTS):
   Grafana     http://127.0.0.1:3000   (dashboard "Quantum Commerce - Demo 5.2", somente leitura)
   Jaeger      http://127.0.0.1:16686  (servico quantum-assistant)
   Prometheus  http://127.0.0.1:9090   (Alerts: QualityProbeDegraded, StaleSourceServed, OrderApiDegraded, QualityNoData)
   Assistente  $ASSISTANT_URL/health
Rode os scripts da demo SEMPRE pelo wrapper (venv certo + telemetria ligada):
   ops/run simulator.py --seed 42 --scenario baseline --rate 2 --duration 25
   ops/run quality_monitor.py --prometheus http://127.0.0.1:9090
   ops/run inject.py stale_policy    |    ops/run restore.py
Logs: $RUNTIME/assistant.log e $RUNTIME/orders.log | Parar: bash ops/stop-demo52.sh
Ambiente: local-demo (runtime Python neste computador; nao e a Lambda da 5.1).
MSG
printf '%s\n' "$HEALTH"
