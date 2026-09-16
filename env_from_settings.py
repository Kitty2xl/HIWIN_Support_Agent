#!/usr/bin/env python3
"""Sync the backend `.env` from `pipeline/settings.json`.

The backend (`.env`) and the pipeline (`pipeline/settings.json`) are two separate
apps that must AGREE on the database, the image folder and the inference server.
Rather than typing those twice, this copies the SHARED values across (mapping the
differing key names) so you configure them once in settings.json:

    settings.json            ->  .env
    DB_HOST/PORT/NAME/USER   ->  DB_HOST/PORT/NAME/USER
    DB_PASS                  ->  DB_PASSWORD
    DB_SCHEMA                ->  DB_SCHEMA
    IMAGE_TARGET_ROOT        ->  IMAGE_STATIC_ROOT
    LLM_BASE_URL (minus /v1) ->  INFERENCE_HOST
    EMBED_MODEL              ->  EMBEDDING_MODEL

Everything ELSE in an existing `.env` is left exactly as it is - the tuning keys
(COMPLETENESS_*, CHAT_TIMEOUT, RERANK_DOC_MAX_CHARS, ...) and your comments survive.
Only the shared keys above are rewritten in place; missing ones are appended.
If there is no `.env` yet, a complete one is written from the template below.

Run from the repo root:   python env_from_settings.py
A backup of the previous file is written to `.env.bak`.
"""

import json
import os
import re
import shutil

import sys

# Console-safe output on every OS (a Windows console/redirect with a legacy code page
# such as cp950 would otherwise raise UnicodeEncodeError on non-ASCII text).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE = os.path.dirname(os.path.abspath(__file__))
SETTINGS = os.path.join(BASE, "pipeline", "settings.json")
ENV = os.path.join(BASE, ".env")

# Backend serving-model names (router [section] names). The chat and reranker
# names are backend-only, so they live here; the embedding model is shared with
# the pipeline (settings.json EMBED_MODEL) because both sides MUST use the same one.
LANGUAGE_MODEL = "Support_Agent_Qwen3.6"
RERANKER_MODEL = "Reranker_Qwen3.6"

TEMPLATE = """\
# Backend config. The DB / image / inference-host values are SHARED with the
# pipeline (pipeline/settings.json): edit them there and run
#     python env_from_settings.py
# which rewrites only those shared keys here and leaves the rest of this file alone.

# --- Inference server (llama.cpp router, OpenAI-compatible endpoint) ---
INFERENCE_HOST={INFERENCE_HOST}
LANGUAGE_MODEL={LANGUAGE_MODEL}
EMBEDDING_MODEL={EMBEDDING_MODEL}
RERANKER_MODEL={RERANKER_MODEL}

# --- Postgres (must match what the pipeline wrote) ---
DB_NAME={DB_NAME}
DB_USER={DB_USER}
DB_PASSWORD={DB_PASSWORD}
DB_HOST={DB_HOST}
DB_PORT={DB_PORT}
DB_SCHEMA={DB_SCHEMA}
DB_SSLMODE=prefer

# --- Images (same folder the pipeline copies figures into) ---
IMAGE_STATIC_ROOT={IMAGE_STATIC_ROOT}

# --- Chat logging ---
CHAT_LOG_ENABLED=true

# --- Separate frontend (see docs/API.md) ---
CORS_ALLOW_ORIGINS=*
PUBLIC_BASE_URL=

# --- Answer quality / latency tuning (backend only; see README "Configuration") ---
COMPLETENESS_PASS_ENABLED=true
COMPLETENESS_MODE=pre_draft
COMPLETENESS_MODEL=Support_Agent_Aux
COMPLETENESS_CONCURRENCY=4
KEEP_RECENT_TOOL_RESULTS=-1
GRADING_ENABLED=false
CHAT_TIMEOUT=30000
RERANK_DOC_MAX_CHARS=1000
"""


def shared_values(s: dict) -> dict:
    """Map settings.json fields onto the .env keys they feed."""
    base = (s.get("LLM_BASE_URL") or "http://localhost:11400").rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3].rstrip("/")
    return {
        "INFERENCE_HOST": base,
        "EMBEDDING_MODEL": s.get("EMBED_MODEL") or "Embedding_Qwen3.6",
        "DB_NAME": s.get("DB_NAME", "hiwin_rag_db"),
        "DB_USER": s.get("DB_USER", "postgres"),
        "DB_PASSWORD": s.get("DB_PASS", ""),
        "DB_HOST": s.get("DB_HOST", "localhost"),
        "DB_PORT": str(s.get("DB_PORT", "5432")),
        "DB_SCHEMA": s.get("DB_SCHEMA", "hiwin_rag"),
        "IMAGE_STATIC_ROOT": s.get("IMAGE_TARGET_ROOT", ""),
    }


def update_in_place(text: str, values: dict) -> tuple[str, list, list]:
    """Rewrite `KEY=value` lines for the shared keys; append keys that are absent.
    Comments and every other key are untouched. Returns (new_text, changed, added)."""
    changed, added = [], []
    lines = text.splitlines()
    seen = set()
    for i, line in enumerate(lines):
        m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$", line)
        if not m:
            continue
        key = m.group(1)
        if key in values and key not in seen:
            seen.add(key)
            if m.group(2).strip() != str(values[key]):
                lines[i] = f"{key}={values[key]}"
                changed.append(key)
    missing = [k for k in values if k not in seen]
    if missing:
        lines.append("")
        lines.append("# --- added by env_from_settings.py ---")
        for k in missing:
            lines.append(f"{k}={values[k]}")
            added.append(k)
    return "\n".join(lines) + "\n", changed, added


def main():
    if not os.path.exists(SETTINGS):
        raise SystemExit(f"Not found: {SETTINGS}\n"
                         "Run this from the repo root; pipeline/settings.json must exist.")
    with open(SETTINGS, "r", encoding="utf-8") as f:
        s = json.load(f)
    values = shared_values(s)

    if os.path.exists(ENV):
        shutil.copyfile(ENV, ENV + ".bak")
        with open(ENV, "r", encoding="utf-8") as f:
            old = f.read()
        new, changed, added = update_in_place(old, values)
        with open(ENV, "w", encoding="utf-8", newline="\n") as f:
            f.write(new)
        print(f"Updated {ENV} (backup: .env.bak)")
        print(f"  rewritten: {', '.join(changed) or '(nothing changed)'}")
        if added:
            print(f"  appended : {', '.join(added)}")
    else:
        new = TEMPLATE.format(LANGUAGE_MODEL=LANGUAGE_MODEL, RERANKER_MODEL=RERANKER_MODEL,
                              **values)
        with open(ENV, "w", encoding="utf-8", newline="\n") as f:
            f.write(new)
        print(f"Wrote a new {ENV}")

    if not values["DB_PASSWORD"]:
        print("WARNING: DB_PASS is empty in settings.json - set it there and re-run.")
    if "/path/to/" in json.dumps(values):
        print("WARNING: placeholder path(s) copied - edit settings.json (paths) and re-run.")
    print("Next: python doctor.py")


if __name__ == "__main__":
    main()
