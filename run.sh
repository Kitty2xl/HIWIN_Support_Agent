#!/usr/bin/env bash
# Backend-only launcher (Linux/macOS): use when the llama.cpp router is ALREADY running.
# Creates the venv on first use, installs requirements, runs the preflight checks,
# then serves POST /chat on port 8079. Run from the repo root: ./run.sh
# For router + backend in one go use ./start.sh instead.
set -e
cd "$(dirname "$0")"

# Find Python 3.12 (override with PYTHON=/path/to/python3.12).
PYTHON="${PYTHON:-$(command -v python3.12 || command -v python3 || command -v python || true)}"
if [ -z "$PYTHON" ]; then
  echo "ERROR: no python3 found. Ubuntu/Debian: sudo apt install python3.12 python3.12-venv"; exit 1
fi
if [ ! -d ".venv" ]; then
  echo "Creating virtual environment with $PYTHON ..."
  "$PYTHON" -m venv .venv || {
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

python doctor.py || {
  echo
  echo "WARNING: doctor.py reported failures - starting anyway; fix them if /chat misbehaves."
  echo
}

uvicorn main:app --host 0.0.0.0 --port 8079
