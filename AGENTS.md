# AGENTS.md — working notes for AI coding agents (and new humans)

This file is for whoever (or whatever) picks up this repository next. Read it
before changing anything. The human-facing manual is [README.md](README.md)
(繁體中文: [README.zh-Hant.md](README.zh-Hant.md)); day-to-day operations are in
[docs/MAINTENANCE.md](docs/MAINTENANCE.md); the design is in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## 1. What this repo is

Two applications sharing one PostgreSQL + pgvector database and one llama.cpp
inference server (router mode, one endpoint, several models by name):

| Half | Entry point | Reads | Writes |
|---|---|---|---|
| **Backend** (serve answers) | `main.py` → `uvicorn main:app --port 8079` | `.env`, `prompts/`, DB `data_*` tables, images at `IMAGE_STATIC_ROOT` | DB `hiwin_cs_db.chat_logs` |
| **Pipeline** (build the DB from PDFs) | `pipeline/gui.py` / `tui.py` / `Pipeline.py` | `pipeline/settings.json`, `<ROOT_PATH>/PDFs`, `PDF_Config.yaml`, ONNX model | DB `data_*` tables, figures into `IMAGE_TARGET_ROOT` |

Both talk to `llama-server --models-preset config.ini`. Nothing loads GGUF files
directly.

## 2. First command on any machine

```bash
python doctor.py --all
```

It checks Python, packages, `.env`, `config.ini` paths, launch-script line
endings, PostgreSQL version + pgvector, the ingested tables and their language
codes, the router's model list, pipeline paths, and the parity between the
pipeline and backend configs. Every FAIL line carries a fix. Do not debug by
guessing before running it.

## 3. Invariants — do not break these

1. **Three files must agree on model names.** `config.ini` `[section]` names ==
   `.env` `LANGUAGE_MODEL` / `EMBEDDING_MODEL` / `RERANKER_MODEL` /
   `COMPLETENESS_MODEL` == `pipeline/settings.json` `MODEL_PASS_*` / `EMBED_MODEL`.
   `doctor.py` enforces it.
2. **Pipeline and backend must agree on the database.** `settings.json` `DB_*` and
   `EMBED_MODEL` are the source of truth; `python env_from_settings.py` copies the
   shared keys into `.env` *in place* (it does not touch the tuning keys).
3. **The embedding model is welded to the data.** Every `data_*` table has an
   `embedding vector(2560)` column produced by `Embedding_Qwen3.6`
   (Qwen3-Embedding-4B). Changing the embedding model or its quantisation means
   re-ingesting *everything* and updating `EMBED_DIM` in `pipeline/core/config.py`.
   Do not "just switch the model name".
4. **`db.py` keeps the original open-WebUI SQL base64-encoded on purpose**
   (provenance). Add new queries as plain text next to them; do not decode/rewrite
   the originals.
5. **Language codes are `en` / `jp` / `tc` / `sc`** (config.py `LANGUAGE_SKILL_MAP`).
   They must match `metadata_->>'language_code'` in the DB. The DB also contains
   `kr` rows (multi-axis / single-axis robot) and NULL rows (`data_product_blurb`)
   that no skill reaches — known, harmless.
6. **The router preset carries hard-won settings with comments explaining
   incidents** (reasoning-budget cap, `parallel = 1` for deterministic embeddings,
   single-GPU pinning). Keep the comments when you edit `config.ini`.
7. **Secrets are committed on purpose** (`.env`, `pipeline/settings.json`,
   `pipeline/models.json`). This is an internal ACPIE_Lab repo; the
   `.gitignore` explains it. Do not scrub them in a "cleanup" — but if the repo
   ever goes public, rotate the DB password first.
8. **Line endings:** `.gitattributes` pins `*.sh` to LF and `*.bat` to CRLF. Do not
   commit a CRLF `start.sh`.

## 4. Where behaviour lives (what to edit for what)

| You want to change… | Edit |
|---|---|
| How the agent reasons / routes / formats answers | `prompts/System_prompt.md` (state machine), `prompts/skills/<lang>.md` (per-language routing + out-of-scope template) |
| The "list everything" completeness behaviour | `agent.py` (`_enumerate_passage`, `_completeness_pass` prompts), `.env` `COMPLETENESS_*` |
| Vision description of retrieved figures | `rag_tools.py` `_run_vision_analysis` |
| Which/how many passages are retrieved | `.env` `VECTOR_TOP_K`, `RERANK_CANDIDATE_K`, `RERANK_TOP_K`, `RERANK_DOC_MAX_CHARS`, `FIGURE_TEXT_MAX_CHARS` |
| Tool descriptions the model sees | `tool_schemas.py` |
| Sampling / context size / which GGUF | `config.ini` (restart the router) — the backend overrides only `temperature` |
| PDF transcription prompts (pipeline) | `pipeline/pdf_passes/Pass_2.py` `PROMPT`, `Pass_2b.py` `CAPTION_PROMPT`, `Pass_3.py` `SYSTEM_PROMPT`, `Pass_3b.py` `TABLE_SUMMARY_PROMPT`, `Pass_4.py` `PROMPT` |
| Which PDFs / pages are ingested | `<ROOT_PATH>/PDF_Config.yaml` |
| Chunking (per page vs per section) | `settings.json` `INGEST_BY_PAGE` |
| The demo page | `frontend/index.html` (illustrative only; the product is the API) |
| Frontend integration (CORS, absolute image URLs) | `.env` `CORS_ALLOW_ORIGINS`, `PUBLIC_BASE_URL`; contract in `docs/API.md` |

## 5. How to verify a change

```bash
# static
python -m compileall -q . -x '\.venv'
python -m pyflakes *.py pipeline/*.py pipeline/core/*.py pipeline/ingestion/*.py pipeline/pdf_passes/*.py
python -c "import main"                       # backend boots (loads .env, prompts)
cd pipeline && python -c "import Pipeline, gui, tui, ingestion.Ingest" && cd ..

# environment
python doctor.py --all                        # needs Postgres + router up for a full pass

# behaviour (needs the whole stack up)
python run_prompts.py                         # every examples/*.json -> results_<ts>.json
python run_prompts.py examples/tc_load_capacity.json
```

`run_prompts.py` prints latency per call kind (agent / vision / completeness /
embed / rerank); the same numbers are in the `metrics` field of every `/chat`
response and in `hiwin_cs_db.chat_logs`. Compare before/after when tuning.

There is no unit-test suite. The `examples/` payloads are the regression set;
the chat log is the production record.

## 6. Live deployment facts (as of 2026-09-16, this Windows Server box)

- Repo: `C:\Users\User_11\Desktop\HIWIN\HIWIN_Dem\HIWIN_Support_Agent`, venv `.venv`, Python 3.12.7
  (= `C:\ProgramData\anaconda3\python.exe`; `python`/`py` are NOT on PATH here - the launchers
  search for it, manual commands need the full path or the venv's python).
- Data root (`ROOT_PATH`): `C:\Users\User_11\Desktop\HIWIN` — `PDFs/`, `PDF_Config.yaml`,
  `PP-DocLayout-PlusL.onnx`, `checkpoint.json`. `Process_Files/` and `Final_Output/`
  are **archived** as `*.7z` there (not extracted).
- Models: `C:\Users\User_11\Desktop\HIWIN\Models` (GGUFs, `qwen.jinja`, the live
  `config.ini` this repo's copy mirrors). llama-server: `C:\Users\User_11\Desktop\llama\llama-server.exe`.
- PostgreSQL: **18.1 on port 5432 is the live one** (`hiwin_rag_db`, password in
  `.env`). A PostgreSQL **17 cluster on 5433 belongs to another user** — ignore it.
  pgvector 0.8.1 is installed in both.
- GPUs: 2× RTX A6000 48 GB, shared with other users; the router is pinned to GPU 0
  (`CUDA_VISIBLE_DEVICES=0` in the launchers).
- The image folder (`IMAGE_STATIC_ROOT`) does **not** exist on this machine; the
  backend runs without figures/vision. See MAINTENANCE §"Restoring the figures".
- `hiwin_rag` also holds many `bak_*`, `repair_backup_*`, `link_backup_*` tables:
  manual data repairs (see `docs/INGESTION_DEFECTS.md`). The `LIKE 'data\_%'`
  filter keeps them out of retrieval. Do not drop them without asking.
- The `public` schema of the same database holds legacy open-WebUI tables. Unused.

## 7. Things that look like bugs but are not

- `CHAT_TIMEOUT=30000` (seconds) in `.env` is deliberate: cold loads and long
  enumerations must never time out mid-answer.
- `COMPLETENESS_CONCURRENCY=4` with `parallel = 1` on `Support_Agent_Aux` just
  queues; it is not a misconfiguration.
- Right after a pipeline run, `doctor.py` reports the embedding/reranker/aux models
  as `unloaded`: the pipeline models evicted them (`--models-max 4`). The next `/chat`
  reloads them; verified 2026-09-16 (peak 48.3 GB on the 49 GB card, no OOM).
- A rebuild from a fresh clone was exercised end to end on 2026-09-16 (2-page PDF,
  separate `hiwin_rag_sim` database, then dropped): about 4 min including model loads.
- `inspect_metadata.py`/`doctor.py` report `data_product_cad_urls` and
  `data_product_information_urls` as "plain tables": correct, they are flat URL
  lists read by `db_search_product_urls`, not vector tables.
- The pipeline's `_detect_language` maps any Chinese filename to `tc`; the live
  `PDF_Config.yaml` was hand-edited to use `sc` for Simplified editions.

## 8. Conventions

- Keep scripts runnable from any working directory (they insert their own folder
  into `sys.path`); document exceptions (`python -m ingestion.Ingest` still wants
  `cd pipeline`).
- New config → add to `config.py` (backend) or `pipeline/core/config.py` +
  `settings.py` `SCHEMA` + `i18n.py` `FIELDS` (pipeline), then document it in both
  READMEs.
- Every user-facing doc exists in English and Traditional Chinese; update both.
- Commit messages: imperative, explain *why*. Do not commit `results_*.json`,
  `.env.bak`, `__pycache__`.
