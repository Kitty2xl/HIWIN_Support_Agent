#!/usr/bin/env bash
# Launch the HIWIN Support Agent Backend on Linux/macOS. Run from the repo root.
set -e
cd "$(dirname "$0")"

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

uvicorn main:app --host 0.0.0.0 --port 8079
