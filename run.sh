#!/usr/bin/env bash
# Starts the simulated core bank (8181), the assistant API (8180) and the demo UI (8580).
set -euo pipefail
cd "$(dirname "$0")"
if [ -z "${PY:-}" ]; then
  for candidate in .venv/bin/python python3; do
    if command -v "$candidate" >/dev/null 2>&1 || [ -x "$candidate" ]; then PY="$candidate"; break; fi
  done
fi
trap 'kill 0' EXIT
"$PY" -m uvicorn bankassist.core_mock:app --port 8181 --log-level warning &
sleep 1
"$PY" -m uvicorn bankassist.api:app --port 8180 --log-level warning &
sleep 3
"$PY" -m streamlit run bankassist/ui.py --server.port 8580 --server.headless true
