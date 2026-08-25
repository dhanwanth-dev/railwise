#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> Backend deps"
cd "$ROOT/backend"
if [[ ! -d .venv ]]; then
  if command -v python3.12 >/dev/null 2>&1; then
    python3.12 -m venv .venv
  else
    python3 -m venv .venv
  fi
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q -r requirements.txt
python data/train_model.py
python data/generator.py

echo "==> Starting API on :8000"
"$ROOT/backend/.venv/bin/uvicorn" app.main:app --host 127.0.0.1 --port 8000 &
API_PID=$!
trap 'kill $API_PID 2>/dev/null || true' EXIT

sleep 2
echo "==> Frontend"
cd "$ROOT/frontend"
npm install --silent
npm run dev -- --host 127.0.0.1 --port 5173
