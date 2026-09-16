#!/usr/bin/env bash
# =====================================================================
# One-command start for the HIWIN Support Agent (Linux/macOS).
# Brings up the llama.cpp router (all serving models resident) + the
# FastAPI backend, so it is ready for POST /chat. Run from the repo root:
#     ./start.sh          (or: bash start.sh)
# Ctrl+C stops both.
#
# Assumes PostgreSQL is already running (it only warns if it cannot reach it).
# Backend-only start (router already up): ./run.sh
# Something wrong?                          python doctor.py
# =====================================================================
set -e
cd "$(dirname "$0")"

# ---- CONFIG: edit these for your machine (or set them as env vars) ----
LLAMA_SERVER="${LLAMA_SERVER:-/path/to/llama/llama-server}"
PRESET="${PRESET:-./config.ini}"
ROUTER_HOST="${ROUTER_HOST:-127.0.0.1}"
ROUTER_PORT="${ROUTER_PORT:-11400}"
# How many models may be resident at once. 4 = the serving set; use 6 to keep the
# two pipeline models loaded as well while ingesting (needs the VRAM).
MODELS_MAX="${MODELS_MAX:-4}"
# Which GPU(s) the router may use. On a shared multi-GPU box pin ONE card
# (index from nvidia-smi); export CUDA_VISIBLE_DEVICES="" to let llama.cpp use all.
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES-0}"
# ----------------------------------------------------------------------

if [ ! -x "$LLAMA_SERVER" ]; then
  echo "ERROR: llama-server not found/executable at '$LLAMA_SERVER'."
  echo "       Edit LLAMA_SERVER at the top of start.sh or export the env var."
  exit 1
fi
command -v curl >/dev/null || { echo "ERROR: curl is required (apt install curl)"; exit 1; }

if [ ! -d ".venv" ]; then
  echo "Creating virtual environment..."
  python3 -m venv .venv || {
    echo "ERROR: could not create a venv. On Debian/Ubuntu: sudo apt install python3.12-venv (or python3-venv)."
    exit 1
  }
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q -r requirements.txt

if [ ! -f ".env" ]; then
  echo
  echo "WARNING: .env not found. It is normally committed - restore it, or run: python env_from_settings.py"
  echo
fi

# 1. Start the llama.cpp router (all load-on-startup models resident, one endpoint).
echo "Starting llama.cpp router on ${ROUTER_HOST}:${ROUTER_PORT} (preset ${PRESET}, GPU '${CUDA_VISIBLE_DEVICES}') ..."
"$LLAMA_SERVER" --models-preset "$PRESET" --host "$ROUTER_HOST" --port "$ROUTER_PORT" --models-max "$MODELS_MAX" &
ROUTER_PID=$!
trap 'kill "$ROUTER_PID" 2>/dev/null' EXIT

# 2. Wait for the router to answer.
echo -n "Waiting for the router to come up"
until curl -sf "http://${ROUTER_HOST}:${ROUTER_PORT}/v1/models" >/dev/null 2>&1; do
  if ! kill -0 "$ROUTER_PID" 2>/dev/null; then
    echo; echo "ERROR: the router exited. Check the model paths in $PRESET (python doctor.py)."; exit 1
  fi
  echo -n "."
  sleep 2
done
echo " up (models load in the background via load-on-startup)."

# 3. Preflight (warn only; the backend starts regardless).
echo
echo "Running preflight checks (python doctor.py) ..."
python doctor.py || {
  echo
  echo "WARNING: doctor.py reported failures - the backend will start anyway, but fix them if /chat misbehaves."
  echo
}

# 4. Start the backend.
echo "Starting the backend on port 8079 ..."
uvicorn main:app --host 0.0.0.0 --port 8079
