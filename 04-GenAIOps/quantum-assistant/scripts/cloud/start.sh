#!/usr/bin/env bash
# Liga as tasks (desired 1) na revisao que ja estava implantada.
# Uso: bash scripts/cloud/start.sh [live|candidate|all]
exec bash "$(dirname "$0")/scale.sh" 1 "${1:-live}"
