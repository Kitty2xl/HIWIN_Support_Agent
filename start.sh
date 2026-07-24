#!/usr/bin/env bash
# =====================================================================
# One-command start for the HIWIN Support Agent (Linux/macOS).
# Brings up the llama.cpp router (all models resident) + the FastAPI
# backend, so it's ready for POST /chat. Run from the repo root.
#
# Assumes Postgres is already running (it only warns if it can't reach it).
# For a backend-only start (router already up), use run.sh instead.
# =====================================================================
set -e
cd "$(dirname "$0")"

# ---- CONFIG: edit these for your machine (or set them as env vars) ----
LLAMA_SERVER="${LLAMA_SERVER:-/path/to/llama/llama-server}"
PRESET="${PRESET:-./config.ini}"
ROUTER_HOST="${ROUTER_HOST:-127.0.0.1}"
ROUTER_PORT="${ROUTER_PORT:-11400}"
# ----------------------------------------------------------------------

if [ ! -d ".venv" ]; then
  echo "Creating virtual environment..."
  python3 -m venv .venv
fi
source .venv/bin/activate
pip install -r requirements.txt

if [ ! -f ".env" ]; then
  echo
  echo "WARNING: .env not found. It is normally committed - restore it and set IMAGE_STATIC_ROOT."
  echo
fi

# 1. Start the llama.cpp router (all load-on-startup models resident, one endpoint).
echo "Starting llama.cpp router (mode: --models-preset) on ${ROUTER_HOST}:${ROUTER_PORT} ..."
"$LLAMA_SERVER" --models-preset "$PRESET" --host "$ROUTER_HOST" --port "$ROUTER_PORT" &
ROUTER_PID=$!
trap 'kill "$ROUTER_PID" 2>/dev/null' EXIT

# 2. Wait for the router to answer.
echo -n "Waiting for the router to come up"
until curl -sf "http://${ROUTER_HOST}:${ROUTER_PORT}/v1/models" >/dev/null 2>&1; do
  echo -n "."
  sleep 2
done
echo " up (models load in the background via load-on-startup)."

# 3. Postgres reachability (warn only; the backend needs it).
if ! python -c "import psycopg2, config; psycopg2.connect(dbname=config.DB_NAME, user=config.DB_USER, password=config.DB_PASSWORD, host=config.DB_HOST, port=config.DB_PORT, sslmode=config.DB_SSLMODE).close()" 2>/dev/null; then
  echo "WARNING: could not reach Postgres - make sure it is running and .env DB_* are set."
fi

# 4. Start the backend.
echo "Starting the backend on port 8079 ..."
uvicorn main:app --host 0.0.0.0 --port 8079
