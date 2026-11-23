#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [[ "${1:-}" == "stop" ]]; then
  exec docker compose down
fi
docker compose up -d --build --wait
printf 'Cellscope http://127.0.0.1:%s/\n' "${CELLSCOPE_PORT:-19885}"
