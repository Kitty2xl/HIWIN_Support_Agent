#!/usr/bin/env python3
"""Generate the backend `.env` from `pipeline/settings.json`.

The backend (`.env`) and the pipeline (`pipeline/settings.json`) are two separate
apps that must AGREE on the database, the image folder, and the inference server.
Rather than typing those twice, this copies the shared values across - mapping the
differing key names - so you configure them once in settings.json.

It does NOT blindly copy: `DB_PASS` -> `DB_PASSWORD`, `INFERENCE_HOST` is the base
URL without the trailing `/v1`, `IMAGE_TARGET_ROOT` -> `IMAGE_STATIC_ROOT`, and the
backend keeps its own model aliases (which differ from the pipeline's pass models).

Run:  python env_from_settings.py
Writes `.env` in this folder (an existing one is backed up to `.env.bak`).
"""

import json
import os
import shutil

BASE = os.path.dirname(os.path.abspath(__file__))
SETTINGS = os.path.join(BASE, "pipeline", "settings.json")
ENV = os.path.join(BASE, ".env")

# Backend model aliases (llama-swap keys). These are the SERVING models and are
# not stored in settings.json (which holds the pipeline's pass models), so keep
# them here. Edit if your llama-swap uses different names.
LANGUAGE_MODEL = "Support_Agent_Qwen3.6"
EMBEDDING_MODEL = "Embedding_Qwen3.6"
RERANKER_MODEL = "Reranker_Qwen3.6"


def main():
    if not os.path.exists(SETTINGS):
        raise SystemExit(f"Not found: {SETTINGS}\n"
                         "Run this from the repo root; pipeline/settings.json must exist.")
    with open(SETTINGS, "r", encoding="utf-8") as f:
        s = json.load(f)

    # INFERENCE_HOST = the llama-swap base URL without a trailing /v1
    base = (s.get("LLM_BASE_URL") or s.get("LLAMA_SWAP_URL") or "http://localhost:11400")
    host = base.rstrip("/")
    if host.endswith("/v1"):
        host = host[:-3].rstrip("/")

    env = f"""\
# Backend config, generated from pipeline/settings.json by env_from_settings.py.
# Re-run that script whenever you change the shared DB / image / server values.

# --- Inference server (llama-swap proxy port) ---
INFERENCE_HOST={host}
LANGUAGE_MODEL={LANGUAGE_MODEL}
EMBEDDING_MODEL={EMBEDDING_MODEL}
RERANKER_MODEL={RERANKER_MODEL}

# --- Postgres (must match what the pipeline wrote) ---
DB_NAME={s.get("DB_NAME", "hiwin_rag_db")}
DB_USER={s.get("DB_USER", "postgres")}
DB_PASSWORD={s.get("DB_PASS", "")}
DB_HOST={s.get("DB_HOST", "localhost")}
DB_PORT={s.get("DB_PORT", "5432")}
DB_SCHEMA={s.get("DB_SCHEMA", "hiwin_rag")}
DB_SSLMODE=prefer

# --- Images (same folder the pipeline copies figures into) ---
IMAGE_STATIC_ROOT={s.get("IMAGE_TARGET_ROOT", "")}

# --- Chat logging ---
CHAT_LOG_ENABLED=true
"""

    if os.path.exists(ENV):
        shutil.copyfile(ENV, ENV + ".bak")
        print(f"Backed up existing .env -> {ENV}.bak")
    with open(ENV, "w", encoding="utf-8") as f:
        f.write(env)
    print(f"Wrote {ENV}")

    if not s.get("DB_PASS"):
        print("WARNING: DB_PASS is empty in settings.json - set it there and re-run.")
    if "/path/to/" in env:
        print("WARNING: placeholder path(s) copied - edit settings.json (paths) and re-run.")


if __name__ == "__main__":
    main()
