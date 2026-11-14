#!/usr/bin/env bash
# Desliga as tasks (desired 0). Uso: bash scripts/cloud/stop.sh [live|candidate|all]
exec bash "$(dirname "$0")/scale.sh" 0 "${1:-all}"
