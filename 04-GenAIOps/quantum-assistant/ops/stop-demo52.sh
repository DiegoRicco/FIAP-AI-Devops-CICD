#!/usr/bin/env bash
# Para a Demo 5.2: assistente, API de pedidos e o stack do compose.
# NAO desfaz falha injetada: o overlay do inject.py continua em ops/.runtime/.
# De proposito: reiniciar nao conserta um corpus ruim (veja ops/runbook.md).
# Para voltar ao corpus do repositorio, use: ops/run restore.py
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME="${QC_RUNTIME_DIR:-$HERE/.runtime}"
for name in assistant orders; do
  pidf="$RUNTIME/$name.pid"
  if [ -f "$pidf" ] && kill -0 "$(cat "$pidf")" 2>/dev/null; then
    kill "$(cat "$pidf")" && echo "parado: $name (pid $(cat "$pidf"))" >&2
  fi
  rm -f "$pidf"
done
docker compose -f "$HERE/docker-compose.yml" down >&2
echo "stack parado. Overlay ativo: $([ -f "$RUNTIME/overlay.json" ] && echo sim || echo nao)" >&2
