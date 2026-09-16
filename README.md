# HIWIN Support Agent

**English** · [繁體中文](README.zh-Hant.md)

A self-hosted **support agent** for HIWIN industrial products: a client sends a
question and a language code to `POST /chat`; the service runs an agentic
retrieval flow against a local **PostgreSQL + pgvector** knowledge base and a
local **llama.cpp** inference server, and returns a markdown answer with
structured citations and inline technical figures. The repository also contains
the **ingestion pipeline** that builds that knowledge base from HIWIN's PDF
catalogues, so one repo covers both *building* and *serving*.

> **Inherited this project? Start here.**
> 1. Read [§2 How the pieces fit](#2-how-the-pieces-fit) — three config files must agree, and most problems are one of them being wrong.
> 2. Run `python doctor.py --all` — it checks the whole stack and tells you exactly what to fix.
> 3. For everyday work (tuning answers, adding PDFs, backups) read [docs/MAINTENANCE.md](docs/MAINTENANCE.md).
> 4. [AGENTS.md](AGENTS.md) is a condensed brief for AI coding agents and new developers; [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) explains the design.

---

## Table of contents

1. [What it does](#1-what-it-does)
2. [How the pieces fit](#2-how-the-pieces-fit)
3. [Current deployment snapshot](#3-current-deployment-snapshot)
4. [Prerequisites](#4-prerequisites)
5. [Install](#5-install)
6. [Path A — serve the existing database](#6-path-a--serve-the-existing-database)
7. [Path B — move to another machine](#7-path-b--move-to-another-machine)
8. [Path C — rebuild the database from PDFs](#8-path-c--rebuild-the-database-from-pdfs)
9. [PostgreSQL and pgvector](#9-postgresql-and-pgvector)
10. [Configuration reference](#10-configuration-reference)
11. [Using the API](#11-using-the-api)
12. [Data ingestion pipeline](#12-data-ingestion-pipeline)
13. [Maintenance](#13-maintenance)
14. [Troubleshooting](#14-troubleshooting)
15. [Known issues](#15-known-issues)
16. [Project structure](#16-project-structure)
17. [License](#17-license)

---

## 1. What it does

- **Single `POST /chat` API** — `{prompt, language}` in; JSON out with a markdown
  `response`, structured `sources`, a tool-call `trace` and timing `metrics`.
- **Agentic state machine** — an LLM tool-calling loop drives *routing →
  retrieval → formatting → fallback*, defined in natural language in
  `prompts/System_prompt.md` plus one skill file per language.
- **Multilingual** — English (`en`), Japanese (`jp`), Traditional Chinese (`tc`),
  Simplified Chinese (`sc`); the answer mirrors the user's character set.
- **Full retrieval pipeline** — embed → pgvector similarity search (per product
  table, in parallel) → rerank → optional vision analysis of retrieved figures →
  a **completeness pass** that re-reads each passage so long spec tables are
  reproduced in full instead of summarised.
- **Structured citations** — built from each chunk's `metadata_`, pruned to the
  pages the answer actually cites.
- **Image serving** — figures are served at `/static/HIWIN` so the markdown
  image links resolve same-origin.
- **Chat logging** — every exchange (prompt, answer, sources, trace, timings,
  tokens) is stored in `hiwin_cs_db.chat_logs` for analytics and debugging.
- **Demo frontend with a debug panel** at `/` — illustrative only; the product
  is the API.

```
POST /chat {prompt, language}
  └─ inject "[Language Code: xx]" into the prompt
  └─ system prompt = System_prompt.md + prompts/skills/<language>.md
  └─ agent loop (LLM + tool schemas):
        ├─ db_get_available_product_tables   (discover data_* tables)
        ├─ db_search_technical_manuals       (embed → pgvector → rerank → vision → completeness)
        ├─ db_search_certifications          (certificates + web_path)
        └─ db_search_product_urls            (download / CAD links)
  └─ return { response (markdown), sources, trace, metrics }
```

## 2. How the pieces fit

Two applications share one database and one inference server:

```
            PDFs ─▶ pipeline/ (layout → VLM transcribe → validate → embed) ─▶ PostgreSQL + pgvector
                                                                                   │  hiwin_rag.data_* tables
                                                                                   ▼
client ─▶ POST /chat ─▶ backend (agent loop + 4 retrieval tools) ◀─────────── embedding / rerank / chat
                                                                       llama.cpp router (config.ini)
```

**Three files configure the whole system, and they must agree.** This is the
single most common source of "it doesn't work":

| File | Configures | Key contents |
|---|---|---|
| [`config.ini`](config.ini) | the **llama.cpp router** (which GGUF files, context size, sampling, GPU layers) | one `[section]` per model **name**; absolute paths to the GGUF files |
| [`.env`](.env) | the **backend** | `INFERENCE_HOST`, the model **names** it asks for, `DB_*`, `IMAGE_STATIC_ROOT`, answer-quality tuning |
| [`pipeline/settings.json`](pipeline/settings.json) | the **pipeline** | `ROOT_PATH`, `IMAGE_TARGET_ROOT`, `DB_*`, the model **names** per pass, `EMBED_MODEL` |

The links that must hold:

```
config.ini [Support_Agent_Qwen3.6]  ══ .env LANGUAGE_MODEL
config.ini [Embedding_Qwen3.6]      ══ .env EMBEDDING_MODEL      ══ settings.json EMBED_MODEL   (same model that built the DB!)
config.ini [Reranker_Qwen3.6]       ══ .env RERANKER_MODEL
config.ini [Support_Agent_Aux]      ══ .env COMPLETENESS_MODEL
config.ini [RAG_Pipeline_Pass34]    ══ settings.json MODEL_PASS_2 / 2B / 3
config.ini [RAG_Pipeline_Pass5Ingest] ══ settings.json MODEL_PASS_3B / 4
settings.json DB_HOST/PORT/NAME/USER/PASS/SCHEMA ══ .env DB_HOST/PORT/NAME/USER/PASSWORD/SCHEMA
settings.json IMAGE_TARGET_ROOT     ══ .env IMAGE_STATIC_ROOT
```

Two helpers keep this honest:

- `python env_from_settings.py` — copies the shared DB / image / host / embedding
  values from `settings.json` into `.env` **in place** (your tuning keys and
  comments in `.env` are left alone). Edit shared values in `settings.json`, run
  this, done.
- `python doctor.py --all` — verifies every link above, every file path in
  `config.ini`, the database (version, pgvector, tables, language codes, vector
  width), the router's model list, and more. **Run it first, always.**

Everything else — prompts, retrieval caps, ports — has sane committed defaults.

## 3. Current deployment snapshot

Facts about the machine this repo was audited on (2026-09-16, Windows Server
2022). On a new machine these are the paths you change; `doctor.py` will point
at each one.

| Item | Value |
|---|---|
| Repo | `C:\Users\User_11\Desktop\HIWIN\HIWIN_Dem\HIWIN_Support_Agent` (venv `.venv`). Python 3.12.7 is the Anaconda install `C:\ProgramData\anaconda3\python.exe` and is **not on PATH** — the launchers find it; for manual commands use the full path. |
| Backend | `http://localhost:8079` (uvicorn) |
| Inference | llama.cpp router at `http://localhost:11400`, binary `C:\Users\User_11\Desktop\llama\llama-server.exe`, GGUFs in `C:\Users\User_11\Desktop\HIWIN\Models` |
| PostgreSQL | **18.1 on port 5432** — database `hiwin_rag_db`, schema `hiwin_rag` (28 `data_*` tables, ~29.7k vectorised pages, 1.4 GB), chat log `hiwin_cs_db.chat_logs`. pgvector 0.8.1. A **second cluster (PostgreSQL 17) on port 5433 belongs to another user** — not ours. |
| DB password | in `.env` (`DB_PASSWORD`) and `pipeline/settings.json` (`DB_PASS`), committed on purpose (internal repo) |
| Data root (`ROOT_PATH`) | `C:\Users\User_11\Desktop\HIWIN` — `PDFs/`, `PDF_Config.yaml`, `PP-DocLayout-PlusL.onnx`, `checkpoint.json`. The pipeline's `Process_Files/` and `Final_Output/` are **archived** there as `.7z` files, not extracted. |
| Images | **not present** — `IMAGE_STATIC_ROOT` (`…\HIWIN\web_static\HIWIN`) does not exist yet, so answers have no figures and the vision step is skipped. To restore: [MAINTENANCE.md → Restoring the figures](docs/MAINTENANCE.md#restoring-the-figures). |
| GPUs | 2× NVIDIA RTX A6000 48 GB, **shared with other users**; the router is pinned to GPU 0 by the launchers (`CUDA_VISIBLE_DEVICES=0`). The serving set needs ~47 GB. |
| Languages in DB | `en`, `jp`, `tc`, `sc` (served) plus a few `kr` rows and NULL rows the agent never reaches — harmless. |

## 4. Prerequisites

> **Can my machine run this?** The serving set is four GGUF models kept resident
> together: a 35B-A3B chat/vision model (150k context), a 4B embedding model, a
> 4B reranker and a 4B "aux" model — about **47 GB of VRAM** as configured. It
> *can* run on CPU or with fewer GPU layers, but expect minutes per answer. Tens
> of GB of disk for the GGUFs, plus ~5 GB for the database and figures.

**Operating system.** Windows 10/11/Server or Linux (Ubuntu/Debian/RHEL family);
macOS works for the backend. Both launchers exist (`start.bat` / `start.sh`).
Line endings are pinned by `.gitattributes`, so a copy made on Windows still
runs on Linux.

**Python 3.12.x** (tested 3.12.7, pinned in `.python-version`).
- Windows: python.org installer (tick "Add to PATH"), or `py -3.12`.
- Ubuntu/Debian: `sudo apt install python3.12 python3.12-venv` (+ `python3-tk`
  only if you want the pipeline GUI). RHEL: `dnf install python3.12`.

**PostgreSQL 13 – 18 with the pgvector extension.** Any of these versions works;
see [§9](#9-postgresql-and-pgvector) for pre-installed / older / multiple
installations and how to get pgvector onto them.

**llama.cpp `llama-server`**, a build recent enough for **router mode**
(`--models-preset`). Download a release for your OS/GPU (CUDA build for NVIDIA)
from https://github.com/ggml-org/llama.cpp/releases or build from source. One
process serves every model.

**Model files (GGUF)** — the names in `config.ini` / `pipeline/models.json`:

| Role | File (as deployed) |
|---|---|
| Chat + vision (backend `Support_Agent_Qwen3.6`, pipeline `RAG_Pipeline_Pass34`) | `Qwen3.6-35B-A3B-UD-Q4_K_XL_MTP.gguf` + `mmproj-35BA3B.gguf` |
| Embedding (`Embedding_Qwen3.6`, **must be the model that built the DB**) | `Qwen3-Embedding-4B.i1-Q4_K_S.gguf` (2560-dim) |
| Reranker (`Reranker_Qwen3.6`) | `Qwen3-Reranker-4B.i1-Q4_K_S.gguf` |
| Small text model (backend `Support_Agent_Aux`, pipeline `RAG_Pipeline_Pass5Ingest`) | `gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf` (+ optional draft `mtp-gemma-4-E4B-it.gguf`) |
| Chat template for the Qwen model | `templates/qwen.jinja` (in this repo) |

**Pipeline only:** the layout-detection ONNX model `PP-DocLayout-PlusL.onnx`
(PaddleOCR PP-DocLayout_plus-L, https://huggingface.co/PaddlePaddle/PP-DocLayout_plus-L)
placed directly in `ROOT_PATH`, and the source PDFs under `ROOT_PATH/PDFs/`.

## 5. Install

```bash
git clone https://github.com/Kitty2xl/HIWIN_Support_Agent
cd HIWIN_Support_Agent

# Windows
python -m venv .venv && .venv\Scripts\activate
# Linux / macOS
python3 -m venv .venv && source .venv/bin/activate

pip install -r requirements.txt          # backend + pipeline in one file
# If a fresh install pulls a broken newer package:  pip install -r requirements.lock.txt
```

`requirements.lock.txt` holds the exact versions this project was last verified
with. The launchers (`start.*`, `run.*`) create the venv and install for you on
first run, so the manual steps above are optional.

> **`python` is not recognised?** Windows often has Python installed but not on
> PATH (on this server it is `C:\ProgramData\anaconda3\python.exe`). Use the full
> path for the `-m venv` step, or `py -3.12` if the launcher is installed. The
> launchers try `python`, `py -3.12` and the usual install folders themselves;
> `set PYTHON=C:\path\to\python.exe` (Linux: `export PYTHON=...`) overrides.

Linux notes: OpenCV is the *headless* build (no `libGL` needed). The pipeline
GUI needs `python3-tk`; the TUI and the backend need no display.

## 6. Path A — serve the existing database

You have the database (this machine, or a restored dump) and the GGUF files.

1. **Point the launcher at llama-server.** Open `start.bat` (Windows) or
   `start.sh` (Linux) and set `LLAMA_SERVER` at the top (or export it as an
   environment variable). Optional: `MODELS_MAX`, `CUDA_VISIBLE_DEVICES`.
2. **Check `config.ini` paths** — every `model =`, `mmproj =`, `model-draft =`,
   `chat-template-file =` must point at a real file on this machine.
3. **Check `.env`** — `DB_*` for your Postgres, `IMAGE_STATIC_ROOT` for the figure
   folder (see §3 if it does not exist yet).
4. **Preflight:** `python doctor.py` (fix FAIL lines top-down).
5. **Start everything:**
   ```bash
   start.bat        # Windows (double-click works)
   ./start.sh       # Linux / macOS
   ```
   The launcher creates the venv, installs requirements, starts the router with
   all serving models resident, waits until it answers, runs `doctor.py`, then
   starts the backend on port 8079. Postgres must already be running.
6. **Smoke test:**
   ```bash
   curl -X POST http://localhost:8079/chat -H "Content-Type: application/json" \
     -d '{"prompt": "HGW20 load capacity", "language": "en"}'
   ```
   Or open `http://localhost:8079/` for the demo page with the debug panel.

`run.bat` / `run.sh` start only the backend (router already running). To run the
router by hand:

```bash
llama-server --models-preset config.ini --host 127.0.0.1 --port 11400 --models-max 4
```

### Verify each layer (bottom-up)

```bash
curl http://localhost:8079/health                 # 1. backend up -> {"status":"ok"}
curl http://localhost:11400/v1/models             # 2. router up -> lists the model names
python inspect_metadata.py                        # 3. DB reachable, tables + language codes
python doctor.py --live                           # 4. real embed / rerank / chat calls
curl -X POST http://localhost:8079/chat -H "Content-Type: application/json" \
  -d '{"prompt": "HGW20 load capacity", "language": "en"}'   # 5. end to end
```

The first failing step names the layer to fix; cross-reference [§14](#14-troubleshooting).

## 7. Path B — move to another machine

Moving the running system (Windows → Linux or the reverse) without re-processing
the PDFs:

1. **Copy the repo** (clone, or copy the folder). Thanks to `.gitattributes` the
   shell scripts stay LF; if you copied by hand and Linux complains about
   `bash\r`, run `sed -i 's/\r$//' start.sh run.sh`.
2. **Copy the GGUF files** and `templates/qwen.jinja`; install `llama-server`.
   Edit the paths in `config.ini` (Linux paths are fine: `/srv/models/x.gguf`).
3. **Install PostgreSQL + pgvector** on the target ([§9](#9-postgresql-and-pgvector)),
   set `DB_*` in `pipeline/settings.json`, run `python env_from_settings.py`, then
   `python setup_db.py` (creates the database, the `vector` extension and both
   schemas; idempotent).
4. **Dump on the old machine, restore on the new one** — commands in
   [§9 Backups and moving the database](#backups-and-moving-the-database).
5. **Restore the figures** (`IMAGE_STATIC_ROOT`): copy the folder from the old
   machine, or rebuild it from the archived `Final_Output` with
   `python -m ingestion.Ingest --assets-only`
   ([MAINTENANCE.md](docs/MAINTENANCE.md#restoring-the-figures)).
6. `python doctor.py --all`, then Path A.

The ONNX model, PDFs and `PDF_Config.yaml` only need to travel if you intend to
run the pipeline on the new machine.

## 8. Path C — rebuild the database from PDFs

**install → prepare Postgres → start the router → build the DB with the pipeline → serve.**

1. Install ([§5](#5-install)).
2. Configure the DB in `pipeline/settings.json`, run `python env_from_settings.py`,
   then `python setup_db.py`.
3. Start the router (`start.bat` / `start.sh` starts the backend too, which is
   fine; or run `llama-server --models-preset config.ini … --models-max 6` so the
   two pipeline models can stay resident alongside the serving ones).
4. Put the ONNX model and PDFs in `ROOT_PATH`, then run the pipeline
   ([§12](#12-data-ingestion-pipeline)). The first run generates
   `PDF_Config.yaml` and stops for review; the second run processes.
5. `python doctor.py --all` and serve (Path A).

> ⚠️ Before re-ingesting documents that are **already** in the database, read
> [docs/INGESTION_DEFECTS.md](docs/INGESTION_DEFECTS.md): several pages were
> repaired by hand *in the database* and a re-ingest would reintroduce the defects.

## 9. PostgreSQL and pgvector

**What the project needs from Postgres:** the `vector` type and the `<=>`
cosine operator (pgvector), `json` columns, and a user who can `CREATE SCHEMA`
/ `CREATE TABLE`. The tables have **no vector index** (plain btree on `id` and
`ref_doc_id`), so nothing depends on HNSW/IVFFlat features. `CREATE EXTENSION
vector` needs a superuser (once per database).

**Version compatibility.**

| PostgreSQL | pgvector | Status |
|---|---|---|
| 18, 17, 16, 15, 14, 13 | 0.8.x (0.8.1 deployed) | supported; 18.1 is what runs today |
| 12 | ≤ 0.7.4 | works; pgvector 0.8 dropped PG 12 |
| ≤ 11 | — | not supported by current pgvector; upgrade Postgres |

`setup_db.py` and `doctor.py` print the server version and refuse anything older
than 12.

**A PostgreSQL is already installed (possibly older).** That is fine as long as
it is 13+ (12 with an old pgvector). Steps:

1. Find its port: Windows `services.msc` → the `postgresql-x64-NN` service, and
   `port = …` in `<data dir>\postgresql.conf`; Linux `pg_lsclusters` (Debian) or
   `ss -ltnp | grep postgres`. **Several installed versions each listen on their
   own port** (5432, 5433, …) — this machine has 18 on 5432 and someone else's 17
   on 5433. Put the right one in `DB_PORT`.
2. Make sure pgvector is available *for that version* (below).
3. Set `DB_*` in `pipeline/settings.json` → `python env_from_settings.py` →
   `python setup_db.py --admin-user postgres --admin-password -`.

**Installing pgvector** (server-side extension; `pip` cannot do it):

- Debian/Ubuntu with the PGDG repo: `sudo apt install postgresql-16-pgvector`
  (match the major version). RHEL family: `sudo dnf install pgvector_16`.
- From source (any Linux/macOS): `git clone --branch v0.8.1 https://github.com/pgvector/pgvector && cd pgvector && make && sudo make install`
  (needs `postgresql-server-dev-NN`).
- Docker: the image `pgvector/pgvector:pg16` (or `pg17`, `pg18`) has it built in.
- **Windows:** no official binaries. Build with Visual Studio Build Tools + `nmake`
  per the pgvector README, or — simplest when another same-major install already
  has it — copy `lib\vector.dll` and `share\extension\vector.control` +
  `share\extension\vector--*.sql` from that install into
  `C:\Program Files\PostgreSQL\NN\`, then restart the service. Both clusters on
  this machine already have 0.8.1.
- Then: `python setup_db.py` (runs `CREATE EXTENSION IF NOT EXISTS vector`).

Verify at any time: `python doctor.py` → the PostgreSQL section.

### Backups and moving the database

Use the `pg_dump` / `pg_restore` that ship with your Postgres (Windows:
`C:\Program Files\PostgreSQL\18\bin\`). Only the two application schemas are
needed; the `bak_*` / `repair_backup_*` / `link_backup_*` snapshot tables in
`hiwin_rag` are manual-repair history and can be excluded to keep the dump small.

```bash
# Dump (source machine). Custom format, both schemas, without the repair snapshots:
pg_dump -h localhost -p 5432 -U postgres -d hiwin_rag_db -Fc \
  -n hiwin_rag -n hiwin_cs_db \
  -T 'hiwin_rag.bak_*' -T 'hiwin_rag.repair_backup_*' -T 'hiwin_rag.link_backup_*' \
  -f hiwin_rag_db.dump

# Restore (target machine) - after `python setup_db.py` created the DB + extension:
pg_restore -h localhost -p 5432 -U postgres -d hiwin_rag_db --no-owner --no-privileges hiwin_rag_db.dump
```

- **Same or newer PostgreSQL on the target:** the commands above are all you need.
- **Older PostgreSQL on the target** (e.g. 18 → 16): a `pg_restore` older than
  the `pg_dump` that wrote the archive may refuse it ("unsupported version in
  file header"). Use a plain-SQL dump instead and load it with `psql`; delete any
  `SET transaction_timeout = 0;` / `SET …` lines the older server rejects:
  ```bash
  pg_dump -h localhost -p 5432 -U postgres -d hiwin_rag_db -Fp -n hiwin_rag -n hiwin_cs_db \
    -T 'hiwin_rag.bak_*' -T 'hiwin_rag.repair_backup_*' -T 'hiwin_rag.link_backup_*' -f hiwin_rag_db.sql
  psql -h localhost -p 5432 -U postgres -d hiwin_rag_db -v ON_ERROR_STOP=0 -f hiwin_rag_db.sql
  ```
  Always run `pg_dump` from the **newer** version's `bin` folder (a newer
  `pg_dump` can read an older server; the reverse is refused).
- After restoring: `python doctor.py` must show the same 28 `data_*` tables and
  `embedding dimension in DB - 2560`.

A dump is also the right nightly backup. The pipeline can always rebuild the
database from the PDFs, but that takes many GPU-hours and would undo the manual
repairs.

## 10. Configuration reference

### `.env` (backend)

Loaded automatically from the repo root (a hidden file — `dir /a` / `ls -a`).
Shared values come from `settings.json` via `env_from_settings.py`; the rest is
backend-only tuning. The committed file is the production configuration.

| Variable | Default | Description |
|---|---|---|
| `INFERENCE_HOST` | `http://localhost:11400` | Router base URL. Use the server IP when the backend runs elsewhere. |
| `LANGUAGE_MODEL` | `Support_Agent_Qwen3.6` | Chat + vision model name (a `config.ini` section). |
| `EMBEDDING_MODEL` | `Embedding_Qwen3.6` | Embedding model — **must be the model that populated the DB.** |
| `RERANKER_MODEL` | `Reranker_Qwen3.6` | Reranker model name. |
| `COMPLETENESS_MODEL` | `Support_Agent_Aux` (in the committed `.env`; code default = `LANGUAGE_MODEL`) | Small resident model for the per-passage enumeration. |
| `DB_HOST` / `DB_PORT` | `localhost` / `5432` | Postgres host / port (**pick the right cluster's port**). |
| `DB_NAME` / `DB_USER` / `DB_PASSWORD` | `hiwin_rag_db` / `postgres` / *(required)* | Credentials. |
| `DB_SCHEMA` | `hiwin_rag` | Schema holding the `data_*` tables. |
| `DB_SSLMODE` | `prefer` | libpq SSL mode (`disable` / `require` when debugging handshakes). |
| `DB_POOL_MAX` | `12` | Max pooled Postgres connections. |
| `IMAGE_STATIC_ROOT` | *(none)* | Folder served at `/static/HIWIN` and read by the vision step. |
| `DEFAULT_LANGUAGE` | `tc` | Used when the request omits `language`. |
| `TEMPERATURE` | `0` | Per-request temperature for the chat model (overrides the preset). |
| `CHAT_TIMEOUT` | `120` (committed: `30000`) | Per-call timeout (s). Large on purpose: cold loads and long enumerations must not be cut. |
| `MAX_AGENT_ITERS` | `8` | Max tool-calling rounds per request. |
| `CHAT_LOG_ENABLED` / `CHAT_LOG_SCHEMA` / `CHAT_LOG_TABLE` | `true` / `hiwin_cs_db` / `chat_logs` | Chat logging (see §11). |

Retrieval quality vs. speed:

| Variable | Default | Description |
|---|---|---|
| `VECTOR_TOP_K` | `15` | pgvector rows pulled per table. |
| `RERANK_CANDIDATE_K` | `20` | Passages fed to the reranker. |
| `RERANK_TOP_K` | `6` | Passages kept after reranking (manual search). |
| `CERT_RERANK_TOP_K` | `10` | Passages kept for certification search. |
| `RERANK_DOC_MAX_CHARS` | `2000` (committed: `1000`) | Per-passage chars sent to the reranker for scoring only. Lower = faster rerank. |
| `FIGURE_TEXT_MAX_CHARS` | `120` | Clip verbose figure captions before the model sees them. `-1` disables. |
| `KEEP_RECENT_TOOL_RESULTS` | `4` (committed: `-1`) | Keep only the N latest tool results in full in context. `-1` = keep all (required for `pre_draft`). |
| `PARALLEL_TOOL_CALLS` | `true` | Run a turn's tool calls concurrently. |
| `DB_SEARCH_CONCURRENCY` | `8` | Concurrent per-table vector searches inside one tool call. |
| `GRADING_ENABLED` | `false` | LLM YES/NO relevance filter per passage. Off: it fights exhaustive recall. |
| `COMPLETENESS_PASS_ENABLED` | `true` | Re-read each passage to recover rows the draft would summarise away. |
| `COMPLETENESS_MODE` | `post_draft` (committed: `pre_draft`) | `pre_draft`: extract rows right after each search, draft once. `post_draft`: draft, then enumerate and reconcile. |
| `COMPLETENESS_CONCURRENCY` | `1` (committed: `4`) | Enumeration calls in flight. With `parallel = 1` on the aux model they just queue. |
| `COMPLETENESS_TIMEOUT` | `120` | Per-enumeration timeout (s). |
| `COMPLETENESS_EXPLAIN_NONE` | `true` | A non-matching passage explains why (debug aid, visible in the debug panel). |
| `VISION_MAX_IMAGES` | `4` | Max figures sent to the vision model per search. |
| `TRACE_RESULT_MAX_CHARS` | `600` | How much of each tool result the `trace` keeps. `0` = all. |

### `pipeline/settings.json` (pipeline)

Edited by the GUI/TUI settings screen or by hand. Common fields: `ROOT_PATH`,
`IMAGE_TARGET_ROOT`, `DB_*`, `EMBED_MODEL`; advanced: `LLM_BASE_URL`,
`MODEL_PASS_*`, Pass-1 detection settings, concurrency, behaviour toggles. The
defaults and documentation for every key live in `pipeline/core/config.py`.
`EMBED_DIM` (2560) is deliberately **not** editable there — it is tied to the
embedding model and the existing tables.

### `config.ini` (router)

One `[section]` per model; keys are `llama-server` flags without `--`. Edit the
file, restart the router. The comments explain every non-default value (they
record real incidents — keep them). Two you will touch:

- **Temperature** (`temp`): `0` = deterministic. The backend sends its own
  `TEMPERATURE` per request for the chat model, so the preset's value applies to
  the pipeline passes and the aux model.
- **Context size** (`ctx-size`): prompt + output tokens per request; a VRAM
  setting. `ctx-size` is shared across `parallel` slots.

`--models-max` (launcher variable `MODELS_MAX`, default 4) is how many models
may be resident at once; the four serving models fill it, so pipeline models
evict one unless you raise it to 6.

## 11. Using the API

### `POST /chat`

```bash
curl -X POST http://localhost:8079/chat -H "Content-Type: application/json" \
  -d '{"prompt": "HGW20 load capacity", "language": "en"}'
```

```json
{
  "response": "### HGW20 Load Capacity … (markdown, may include ![](/static/HIWIN/…) images)",
  "language": "en",
  "sources": [{"product_type": "linear_guideway", "page_number": 6, "file_name": "…", "language_code": "en", "rerank_score": 0.93}],
  "trace":   [{"tool": "db_get_available_product_tables", "args": {}, "result": "…"}, {"tool": "db_search_technical_manuals", "args": {"…"}, "result": "…"}],
  "metrics": {"latency_ms": 41230, "llm_calls": 5, "tool_calls": 3, "completeness_calls": 6, "total_tokens": 38112, "by_kind": {"agent": {"calls": 3, "ms": 21000}, "completeness_enum": {…}}, "completeness": [{"source": "[SOURCE: …]", "status": "found", "output": "…"}]}
}
```

- `language` is optional (defaults to `DEFAULT_LANGUAGE`); values `en` / `jp` / `tc` / `sc`.
- `response` is GitHub-flavoured markdown; image links resolve against `/static/HIWIN`.
- `sources` are pruned to the pages the answer cites; `trace` shows every tool call; `metrics` shows where the time went.

| Endpoint | Purpose |
|---|---|
| `GET /` | Demo frontend (`frontend/index.html`) with a debug panel. |
| `GET /health` | Liveness → `{"status": "ok"}`. |
| `GET /static/HIWIN/...` | Figures from `IMAGE_STATIC_ROOT`. |

### Batch runner and inspection

```bash
python run_prompts.py                          # every examples/*.json -> results_<timestamp>.json
python run_prompts.py examples/tc_load_capacity.json --url http://host:8079/chat --timeout 600
python inspect_metadata.py --sample            # tables, row counts, language codes, a metadata_ sample
```

`run_prompts.py` prints per-request latency by call kind and a batch summary —
the tool for before/after comparisons when tuning.

### Chat logging

Every `/chat` exchange is inserted into `hiwin_cs_db.chat_logs` (created on
first use): `created_at`, `language`, `prompt`, `response`, `sources`, `trace`
(jsonb), `latency_ms`, `llm_calls`, `agent_iterations`, `tool_calls`,
`prompt_tokens`, `completion_tokens`, `total_tokens`, `generations` (per-call
timings). Best-effort: a DB hiccup is printed and ignored. Disable with
`CHAT_LOG_ENABLED=false`.

### Demo frontend and debug panel

`frontend/index.html` is served at `/`: type a prompt, pick a language, see the
rendered answer, sources and — with **Debug view** on — the metrics strip, the
tool-call timeline with arguments and results, the completeness pass per passage
(`✓ found` / `NONE` with reason / `⚠ error`), and the raw JSON. It renders
markdown with `marked` from a CDN (vendor it if offline); LaTeX is not rendered.

## 12. Data ingestion pipeline

`pipeline/` turns PDFs into the database:

```
PDFs ─▶ Pass 1 layout detection (ONNX, CPU) ─▶ Pass 2 page → markdown (VLM) ─▶ Pass 2b figure captions
     ─▶ Pass 3 table → markdown (VLM) ─▶ Pass 3b table summaries ─▶ Pass 4 validation/merge
     ─▶ Phase 5 organise Final_Output ─▶ Phase 7 embed + insert (per page) + copy figures to IMAGE_TARGET_ROOT
```

Checkpointed per document and pass (`<ROOT_PATH>/checkpoint.json`), so an
interrupted run resumes; timed-out passes are retried once at the end.

### `ROOT_PATH` layout

```
<ROOT_PATH>/
├── PP-DocLayout-PlusL.onnx   # YOU PROVIDE (exact name, directly here)
├── PDFs/                     # YOU PROVIDE: <product>/<sub-folder…>/<file>.pdf
├── PDF_Config.yaml           # which PDFs/pages to process (auto-generated on first run, then reviewed)
├── checkpoint.json           # progress (auto)
├── Process_Files/            # per-pass intermediate output (auto) - per-page markdown lives here
└── Final_Output/             # <product>/<sub_folder>/<lang>/document.md + Figures/ + Tables/ (auto)
```

The `<product>` folder name becomes the table `data_<product>` (lower-cased,
non-alphanumerics → `_`).

### `PDF_Config.yaml`

If absent, the first run scans `PDFs/`, writes one entry per PDF with every page
included and the language guessed from the filename, then **stops** so you can
review it. Format:

```yaml
Ballscrew:                          # product -> PDFs/Ballscrew/, table data_ballscrew
  Ballscrew-(C).pdf:                # leaf = the PDF filename
    language: tc                    # en | jp | tc | sc  (= the DB language_code)
    pages_to_exclude: [0, 1, 2]     # 0-indexed pages to skip
  Ballscrew-(E).pdf:
    language: en
    pages_to_include: [5, 6, 7]     # if non-empty, ONLY these pages run
```

Any depth of grouping folders is allowed between product and leaf; they are
joined into `sub_folder`. Auto-detection maps any Chinese filename to `tc` —
edit Simplified editions to `sc` by hand (the live config does).

### Running it

```bash
# activate the venv first
cd pipeline
python gui.py                 # Tk GUI: settings screen + live per-PDF monitor (EN / 繁中 toggle)
python tui.py                 # same in the terminal (rich)
python Pipeline.py            # headless, uses settings.json
python -m ingestion.Ingest                # (re)embed + insert Final_Output only
python -m ingestion.Ingest --db-only      # …without copying figures
python -m ingestion.Ingest --assets-only  # ONLY copy figures/tables into IMAGE_TARGET_ROOT (no DB)
python -m ingestion.Ingest --force        # re-ingest documents already marked done
```

`gui.py`, `tui.py`, `Pipeline.py` and `ingestion/Ingest.py` can be started from
any working directory; the `-m` form still wants `cd pipeline`. The settings
screen saves to `settings.json`; the DB password field is masked. Per-page
ingestion (`INGEST_BY_PAGE = true`, the production mode) reads the per-page
markdown from `Process_Files/…/Pass_3b`, so re-ingesting needs that folder, not
just `Final_Output`.

**Pipeline models vs. serving models.** The pipeline talks to the same router the
backend uses. Its two models are not `load-on-startup`; when the first page is
sent, the router loads them and — with the default `--models-max 4` — evicts the
least-recently-used serving models (the embedding and reranker went first in
testing; peak use was 48.3 GB of the 49 GB card, and it worked). Afterwards the
serving set is incomplete until the next `/chat` request reloads what it needs
(about 30 s extra on that first answer), so ingest during quiet hours, or set
`MODELS_MAX=6` in the launcher only if the GPU really has room for all six.

Tested end to end on 2026-09-16 from a fresh clone: one 2-page PDF took about
4 minutes including model loads (Pass 1 in 3 s, Passes 2–4 on the router, then
ingest); the rows appeared in `data_controller_drive` and the backend answered
from them.

### Model files

`pipeline/models.json` lists the GGUF filenames and `model_dir`. The GUI
(**Download models…**) / TUI (`m`) check which are present and can download
missing ones from Hugging Face once you fill in `repo_id`s. `doctor.py
--pipeline` verifies the same list.

## 13. Maintenance

[docs/MAINTENANCE.md](docs/MAINTENANCE.md) is the operations manual. It covers:

- **What you may change freely vs. what must stay consistent** (the three
  config files, the embedding model, `EMBED_DIM`).
- **Tuning answers**: where each prompt lives (system prompt, language skills,
  completeness and vision prompts), the retrieval knobs, how to measure with
  `run_prompts.py` and the chat log, and what changed in the past and why.
- **Adding, updating or removing documents** in the knowledge base, including
  the warning about hand-repaired pages ([INGESTION_DEFECTS.md](docs/INGESTION_DEFECTS.md)).
- **Restoring the figures**, backups, rotating the DB password, updating models
  or llama.cpp, and a routine checklist.

## 14. Troubleshooting

Run `python doctor.py --all` first; then:

| Symptom | Likely cause / fix |
|---|---|
| `[FAIL] router not reachable` / connection refused on `:11400` | Router not running or `INFERENCE_HOST` wrong. `start.bat` / `start.sh`, then `curl http://localhost:11400/v1/models`. |
| Router exits at startup | A path in `config.ini` is wrong (`doctor.py` lists each), or not enough VRAM — lower `n-gpu-layers` / `ctx-size`, or pin one GPU (`CUDA_VISIBLE_DEVICES`). |
| `password authentication failed` | Wrong `DB_PASSWORD`, **or `DB_PORT` points at a different Postgres cluster** (several versions installed). |
| `fe_sendauth: no password supplied` | `DB_PASSWORD` empty; `.env` missing or not loading (`pip install -r requirements.txt`). |
| `extension "vector" is not available` | pgvector not installed for *that* Postgres version ([§9](#9-postgresql-and-pgvector)). |
| `python setup_db.py` → `permission denied to create extension` | Run as a superuser: `--admin-user postgres --admin-password -`. |
| Restore fails with `unsupported version in file header` | Target Postgres older than the `pg_dump` used; use a plain-SQL dump ([§9](#backups-and-moving-the-database)). |
| `/usr/bin/env: 'bash\r': No such file` (Linux) | CRLF script: `sed -i 's/\r$//' start.sh run.sh`. |
| `ImportError: libGL.so.1` (Linux) | Old venv with `opencv-python`; `pip install -r requirements.txt` (now headless) or `apt install libgl1`. |
| `python3 -m venv` fails (Debian/Ubuntu) | `sudo apt install python3.12-venv`. |
| Agent loops (many `db_search_*` calls) then a garbled non-answer | Every retrieval errored or returned nothing (usually DB auth). Open the debug panel / `trace`. |
| `No results for language …` | The `language_code` sent does not match the DB (`inspect_metadata.py`). |
| `Reranking failed: HTTP 500 … increase the physical batch size` | Raise `ubatch-size` / `batch-size` on the reranker section (4096 shipped) or lower `RERANK_DOC_MAX_CHARS`. |
| Empty answer after a very long wait | Runaway reasoning; `reasoning-budget = 8192` in `config.ini` prevents it — make sure the router runs *this* preset. |
| Answers vary between identical runs | Embedding/reranker `parallel` > 1 gives non-identical vectors; the preset pins `parallel = 1`. |
| Latency jumped from ~1 min to 30+ min | The model got split across GPUs, one of them busy: pin one GPU (`CUDA_VISIBLE_DEVICES=0`, or `device = CUDA0`). |
| Answer lists only some products / drops rows | Keep `COMPLETENESS_PASS_ENABLED=true`, `GRADING_ENABLED=false`; check the completeness section of the debug panel for `NONE` with a wrong reason. |
| Responses very slow | Mostly the completeness pass. Point `COMPLETENESS_MODEL` at the small aux model (default), lower `RERANK_TOP_K`, or raise `parallel` on the aux section and `COMPLETENESS_CONCURRENCY`. |
| Images 404 / no vision analysis | `IMAGE_STATIC_ROOT` unset or the folder does not exist ([MAINTENANCE → Restoring the figures](docs/MAINTENANCE.md#restoring-the-figures)). |
| Images don't show in a separate frontend | Not same-origin: reverse-proxy both, or make image URLs absolute. |
| Pipeline: `Final document not found` / files "missing" that exist (Windows) | Paths over 260 chars; the code uses `\\?\` long paths — make sure you run the repo's current pipeline, not an old copy. |
| Pipeline GUI: `No module named tkinter` | `sudo apt install python3-tk`, or use `tui.py`. |
| Pipeline ends with `UnicodeEncodeError: 'cp950' codec can't encode…` after `Successfully ingested` | An old copy of the pipeline printing an emoji to a legacy Windows console; the data *was* ingested. Current code forces UTF-8 output — update the copy you run. |
| `doctor.py`: `EMBEDDING_MODEL … is unloaded` right after running the pipeline | The pipeline models evicted it. The next `/chat` reloads it automatically; or restart the router. |
| `doctor.py --wait-models` says a model `stayed unloaded` | Same eviction, or the section lacks `load-on-startup = true`. Harmless: it loads on first use. |

## 15. Known issues

- **Mixed catalogue revisions across languages.** Different editions were
  ingested per language, so some values legitimately differ between languages.
  A data matter, not a backend bug.
- **Hand-repaired pages.** Three ballscrew catalogue pages were corrected
  directly in the database; the pipeline would reintroduce the defects
  ([INGESTION_DEFECTS.md](docs/INGESTION_DEFECTS.md)).
- **Figures absent on the current machine** (§3).
- **`kr` and NULL language rows** exist in a few tables and are never retrieved.
- **LaTeX in the demo frontend** renders as literal text.

## 16. Project structure

```
HIWIN_Support_Agent/
├── main.py                 # FastAPI app: /chat, /health, /, /static/HIWIN
├── agent.py                # tool-calling loop + completeness pass
├── rag_tools.py            # the 4 retrieval tools (pgvector → rerank → vision)
├── tool_schemas.py         # function-calling schemas + dispatch
├── inference.py            # chat / embeddings / rerank HTTP wrappers (+ per-call timing)
├── db.py                   # Postgres pool + SQL + chat-log writer
├── prompts.py              # system prompt = System_prompt.md + skill
├── config.py               # env-driven settings (loads .env)
├── doctor.py               # preflight / diagnostics  (run this first)
├── setup_db.py             # create DB + pgvector extension + schemas (idempotent)
├── env_from_settings.py    # sync shared values settings.json -> .env (in place)
├── inspect_metadata.py     # what is in the DB (tables, counts, language codes)
├── run_prompts.py          # batch tester with latency breakdown
├── config.ini              # llama.cpp router preset (edit model paths)
├── templates/qwen.jinja    # chat template referenced by config.ini
├── start.bat / start.sh    # router + backend in one command
├── run.bat / run.sh        # backend only
├── .env                    # backend config (committed; internal deployment)
├── requirements.txt / requirements.lock.txt
├── prompts/                # System_prompt.md + skills/<lang>.md
├── frontend/index.html     # demo page with debug panel
├── examples/               # sample /chat payloads (regression set)
├── docs/                   # ARCHITECTURE, MAINTENANCE, INGESTION_DEFECTS (en + zh-Hant)
├── reference/              # original open-WebUI filter + tool (provenance only)
├── AGENTS.md               # brief for AI agents / new developers
└── pipeline/               # PDF -> pgvector ingestion
    ├── Pipeline.py         #   orchestrator (passes 1-4, organise, ingest)
    ├── gui.py / tui.py     #   settings screen + live monitor (Tk / terminal)
    ├── pdf_passes/         #   Pass_1 (ONNX layout) … Pass_4 (validation) - prompts live here
    ├── ingestion/Ingest.py #   embed + insert; --db-only / --assets-only / --force
    ├── core/               #   config defaults, settings schema, i18n, checkpoints, utils
    ├── settings.json       #   committed pipeline config (this machine's paths)
    ├── models.json         #   GGUF manifest for the downloader
    └── PDF_Config.example.yaml
```

## 17. License

Proprietary / internal use only — see [LICENSE](LICENSE). Third-party
components and models are listed in [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md);
note **PyMuPDF** (AGPL-3.0, pipeline only) and the **Gemma** model terms before
any external distribution.
