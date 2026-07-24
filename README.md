# HIWIN Support Agent Backend

**English** · [繁體中文](README.zh-Hant.md)

A minimal, standalone Python/FastAPI backend for the HIWIN industrial-products
**support agent** (a Knowledge Retrieval Assistant). A client sends a prompt and
a language code over HTTP; the service runs an agentic retrieval flow against a
local Postgres + pgvector store and a local llama.cpp inference server, then
returns a markdown answer with structured citations and inline technical figures.

It is **API-first** — any client (a web app, another service, `curl`) can call
it. A small HTML page is bundled purely as an **example/demo** frontend. It
replaces an earlier [open-WebUI](https://github.com/open-webui/open-webui)
deployment with a small, transparent service.

The repo also bundles the **ingestion pipeline** (`pipeline/`) that turns source
PDFs into the Postgres + pgvector database the backend reads — so this one repo
covers both **building** and **serving** the knowledge base. If you are starting
from an empty database of your own, follow the [Quick start](#quick-start-from-scratch).

---

## Table of contents

- [Features](#features)
- [How it works](#how-it-works)
- [Fastest start (serve an existing database)](#fastest-start-serve-an-existing-database)
- [Quick start (from scratch)](#quick-start-from-scratch)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Model parameters](#model-parameters)
- [Running](#running)
- [Usage](#usage)
- [Chat logging](#chat-logging)
- [Example demo frontend](#example-demo-frontend)
- [Data ingestion pipeline](#data-ingestion-pipeline)
- [Project structure](#project-structure)
- [Troubleshooting](#troubleshooting)
- [Known issues](#known-issues)
- [License](#license)

---

## Features

- **Single `POST /chat` API** — `{prompt, language}` in, JSON out (markdown
  `response`, structured `sources`, and a tool `trace`).
- **Agentic state machine** — an LLM tool-calling loop drives a routing →
  retrieval → formatting → fallback flow defined in `prompts/System_prompt.md`.
- **Multilingual** — English (`en`), Japanese (`jp`), Traditional Chinese (`tc`),
  Simplified Chinese (`sc`), with character-set mirroring in the answer.
- **Full retrieval pipeline** — embedding → pgvector similarity search →
  reranking → vision analysis of retrieved diagrams.
- **Structured citations** — a deduplicated `sources` array built from each
  retrieved chunk's `metadata_` (page / file / `web_path`).
- **Image serving** — the backend serves the HIWIN figures at `/static/HIWIN`,
  so the markdown image links resolve same-origin.
- **Example demo frontend** — an optional single-page HTML demo at `/`
  (`frontend/index.html`) for trying prompts and previewing rendered answers.
  It is illustrative only; the service is API-first and you can replace it with
  your own frontend.

## How it works

```
POST /chat {prompt, language}
  └─ inject "[Language Code: xx]" into the prompt
  └─ build system prompt = System_prompt.md + the per-language skill
  └─ agent loop (LLM with tool schemas):
        ├─ db_get_available_product_tables   (discover tables)
        ├─ db_search_technical_manuals       (embed → pgvector → rerank → vision)
        ├─ db_search_certifications          (certificates + web_path)
        └─ db_search_product_urls            (download / CAD links)
  └─ return { response (markdown), sources, trace }
```

The LLM, following the system prompt, chooses which tools to call and when —
exactly as open-WebUI's native function calling did. See
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full design.

When a request enumerates a spec table ("list all…"), a **completeness pass** runs
after the draft: each retrieved passage is re-read on its own to extract every
matching row, then the answer is rebuilt to include them all. It's accurate but
costs extra model calls (see [Configuration](#configuration) — `COMPLETENESS_*` —
to tune or turn it off).

## Fastest start (serve an existing database)

Already have a populated `hiwin_rag` database and the GGUF models on disk? This is
the shortest path to a live `/chat` API — no pipeline, no rebuild:

1. **Clone + enter the repo:**
   ```bash
   git clone https://github.com/Kitty2xl/HIWIN_Support_Agent
   cd HIWIN_Support_Agent
   ```
2. **Point the launcher at your inference server.** Open `start.bat` (Windows) or
   `start.sh` (Linux/macOS) and set two values at the top: `LLAMA_SERVER` (your
   `llama-server` binary) and `PRESET` (a router preset — copy
   [`router.example.ini`](router.example.ini) and edit its `model` / `mmproj`
   paths to your GGUFs).
3. **Fill in `.env`** — at least `IMAGE_STATIC_ROOT` and `DB_PASSWORD` (see
   [Configuration](#configuration)).
4. **Run it** (creates the venv, starts the router with all models resident, waits
   for it, then starts the backend):
   ```bash
   start.bat        # Windows
   ./start.sh       # Linux/macOS
   ```
   You're now serving on `http://localhost:8079`. Smoke-test it:
   ```bash
   curl -X POST http://localhost:8079/chat -H "Content-Type: application/json" \
     -d '{"prompt": "HGW20 load capacity", "language": "en"}'
   ```

> Postgres must already be running — the launcher only **warns** if it can't reach
> it. **Building the database from PDFs instead?** Use
> [Quick start (from scratch)](#quick-start-from-scratch) below.
> (`run.bat` / `run.sh` are backend-only launchers, for when the router is already up.)

## Quick start (from scratch)

Starting with an empty database of your own, the end-to-end path is:
**install → prepare Postgres → start the inference server → build the DB with the
pipeline → serve with the backend.**

1. **Install** — clone, make a venv, and `pip install -r requirements.txt` (this
   one file covers both the backend and the pipeline). See [Installation](#installation).

2. **Prepare your Postgres database.** Use any database you like (new or existing);
   enable pgvector and create the schema in it:
   ```sql
   \c YOUR_DATABASE_NAME_HERE
   CREATE EXTENSION IF NOT EXISTS vector;
   CREATE SCHEMA IF NOT EXISTS hiwin_rag;
   ```
   You choose the name / user / password / schema — the pipeline also creates the
   schemas (`hiwin_rag` for data, `hiwin_cs_db` for chat logs) and `data_*` tables
   on first run if they're missing, given a user with `CREATE` privilege. (`\c` is
   a `psql`-only command; in a GUI client like pgAdmin/DBeaver, just connect to the
   database and run the other two lines.)

3. **Start your inference server** (llama.cpp `llama-server` in router mode) with the models in
   [Prerequisites](#prerequisites). The GUI/TUI can fetch the GGUFs for you —
   see [Downloading model files](#downloading-model-files-optional).

4. **Build the database** — point the pipeline at your PDFs and your DB, then run
   it. See [Data ingestion pipeline](#data-ingestion-pipeline).

5. **Serve answers** — put the **same** DB credentials in `.env` and start the
   backend. See [Configuration](#configuration) and [Running](#running).

> ⚠️ Steps 4 and 5 have **separate** config files — `pipeline/settings.json` and
> `.env`. The **database name, user, password, schema, and embedding model must
> match** between them, because the pipeline *writes* exactly what the backend
> *reads*.

## Prerequisites

> **Can my machine run this?** The three inference models (chat+vision, embedding,
> reranker — plus the pipeline's vision/text passes) are multi-billion-parameter
> GGUFs, several launched at a large context (`--ctx-size 100000`). Realistically
> you need a **CUDA or Metal GPU with enough VRAM to hold the models you run at
> once** (router mode keeps them resident together) and **tens of GB of free disk**
> for the GGUFs. It *can* run CPU-only, but expect answers in minutes, not seconds
> — a GPU is strongly recommended for anything past a first smoke test.

- **Python 3.12.x** (the [`.python-version`](.python-version) file pins **3.12.7**
  for `pyenv` users — `pyenv install 3.12.7` then it's selected automatically;
  without `pyenv`, any 3.12.x already on your PATH is fine). Runs on
  **Windows, Linux, and macOS**. The pipeline GUI also needs Tk (bundled with
  Python on Windows/macOS; on Linux: `sudo apt install python3-tk`). The pipeline
  TUI and the backend need no display.
- **PostgreSQL** with the **pgvector** extension. Bring your own database — the
  pipeline creates the schema (default `hiwin_rag`), `data_*` tables, and the
  `metadata_` jsonb column inside it. **pgvector must be installed into Postgres
  first** — it is a server-side extension, not a pip package: install the pgvector
  OS package (e.g. `postgresql-16-pgvector`), use a pgvector-enabled Docker image
  (`pgvector/pgvector:pg16`), or build from https://github.com/pgvector/pgvector.
  Running `CREATE EXTENSION vector` normally needs a **superuser** (or a role
  granted `CREATE` on the database).
- A local **OpenAI-compatible inference server** — **llama.cpp `llama-server` in
  router mode**. Started with `--models-preset` (and **no** `-m`), one
  `llama-server` process hosts **every** model behind a single endpoint and routes
  each request by its `model` field. Models marked `load-on-startup = true` stay
  **resident** together (no swapping between the chat / embedding / reranker /
  small models). Point the clients at that endpoint: the backend's `INFERENCE_HOST`
  and the pipeline's `LLM_BASE_URL`.

> **The repo ships a ready-to-edit router preset —
> [`router.example.ini`](router.example.ini).** Each `[section]` is a routable
> model name (must equal the names in `.env` / `pipeline/settings.json` — see
> [How the model names link up](#how-the-model-names-link-up)); keys are
> `llama-server`'s long flags without the leading `--` (`--n-gpu-layers 99` →
> `n-gpu-layers = 99`). Edit the `model` / `mmproj` paths to your GGUFs, then:
>
> ```sh
> llama-server --models-preset router.example.ini --host 127.0.0.1 --port 11400
> ```
>
> The `start.bat` / `start.sh` launchers run exactly this for you. See the
> llama.cpp server docs (**"Using multiple models"**) for the full preset schema
> and `--models-max` (how many models stay co-resident, default 4).

  The two halves use the server for different model roles:

**To serve answers (backend)** — needs `/v1/chat/completions`, `/v1/embeddings`,
and `/v1/rerank`, serving:
  - a chat/vision model (default `Support_Agent_Qwen3.6`),
  - an embedding model (default `Embedding_Qwen3.6`),
  - a reranker model (default `Reranker_Qwen3.6`);
  plus the HIWIN **static image folder** on disk (served at `/static/HIWIN`).

**To build the database (pipeline)** — additionally needs, under your `ROOT_PATH`:
  - a **`PDFs/`** folder of source PDFs and a **`PDF_Config.yaml`** describing them,
  - the layout-detection ONNX model **`PP-DocLayout-PlusL.onnx`** (PaddleOCR
    PP-DocLayout_plus-L, RT-DETR-L, 800×800, 20 layout classes —
    https://huggingface.co/PaddlePaddle/PP-DocLayout_plus-L);
  and on the inference server: vision + text models (defaults `RAG_Pipeline_Pass34`
  and `RAG_Pipeline_Pass5Ingest`) plus the **same embedding model** as the backend
  (default `Embedding_Qwen3.6`), so the stored vectors are comparable at query time.

> **Tip:** the simplest deployment runs everything **on one machine** (backend,
> Postgres, inference server), so all hosts are `localhost`.

## Installation

```bash
# 1. Clone and enter the repo
git clone https://github.com/Kitty2xl/HIWIN_Support_Agent
cd HIWIN_Support_Agent

# 2. Create and activate a virtual environment
python -m venv .venv     # use python3 on Linux/macOS if `python` isn't found
# Windows (cmd):       .venv\Scripts\activate.bat
# Windows (PowerShell): .venv\Scripts\Activate.ps1
# Windows (Git Bash):   source .venv/Scripts/activate
# Linux / macOS:       source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Edit the committed .env — set IMAGE_STATIC_ROOT (see Configuration).
#    .env ships pre-filled for the internal deployment; no copy step needed.
```

Or use the convenience launcher, which does steps 2–3 and starts the server:
`run.bat` (Windows) / `./run.sh` (Linux/macOS).

## Configuration

All settings are read from environment variables; the committed `.env` file is
loaded automatically. It ships pre-filled for the internal deployment — edit
`IMAGE_STATIC_ROOT` (and anything else your setup differs on).

> **Where is `.env`?** It lives in the **repo root** — the same folder as
> `main.py`, `config.py`, and `run.bat`. Its name starts with a dot, so it is a
> **hidden file**: Windows Explorer and some editors/file pickers don't show it by
> default. Reveal it with `dir /a` (Windows) or `ls -a` (Linux/macOS), or open the
> folder in an editor like VS Code.

If `.env` is missing (e.g. your clone didn't include it), create a file named
exactly `.env` in the repo root with at least the keys below. An empty or missing
`DB_PASSWORD` is what produces `fe_sendauth: no password supplied` at query time —
set it to the **same** value as `pipeline/settings.json` → `DB_PASS`:

```dotenv
INFERENCE_HOST=http://localhost:11400
LANGUAGE_MODEL=Support_Agent_Qwen3.6
EMBEDDING_MODEL=Embedding_Qwen3.6
RERANKER_MODEL=Reranker_Qwen3.6
DB_NAME=hiwin_rag_db
DB_USER=postgres
DB_PASSWORD=your_db_password          # REQUIRED; must match pipeline/settings.json DB_PASS
DB_HOST=localhost
DB_PORT=5432
DB_SCHEMA=hiwin_rag
IMAGE_STATIC_ROOT=/path/to/open_webui/static/HIWIN
CHAT_LOG_ENABLED=true
```

**Shortcut — generate `.env` from the pipeline config:** rather than filling the
DB/server/image values in twice, run `python env_from_settings.py`. It reads
`pipeline/settings.json` (which you already set up for ingestion) and writes a
matching `.env`, handling the differing key names (`DB_PASS` → `DB_PASSWORD`,
`IMAGE_TARGET_ROOT` → `IMAGE_STATIC_ROOT`, base URL minus `/v1`). Re-run it
whenever those shared values change. (An existing `.env` is backed up to `.env.bak`.)

Key settings:

| Variable | Default | Description |
|---|---|---|
| `INFERENCE_HOST` | `http://localhost:11400` | Base URL of the inference server. Use the server IP when running remotely. |
| `LANGUAGE_MODEL` | `Support_Agent_Qwen3.6` | Chat + vision model name. |
| `EMBEDDING_MODEL` | `Embedding_Qwen3.6` | Embedding model — **must match** the one that populated the DB. |
| `RERANKER_MODEL` | `Reranker_Qwen3.6` | Reranker model name. |
| `DB_HOST` / `DB_PORT` | `localhost` / `5432` | Postgres host/port. |
| `DB_NAME` / `DB_USER` / `DB_PASSWORD` | `hiwin_rag_db` / `postgres` / *(none)* | Postgres credentials. **`DB_PASSWORD` is required.** |
| `DB_SCHEMA` | `hiwin_rag` | Schema holding the `data_*` tables. |
| `DB_SSLMODE` | `prefer` | libpq SSL mode (`prefer` / `disable` / `require`). |
| `IMAGE_STATIC_ROOT` | *(none)* | Filesystem path to the HIWIN image folder. **Required** for images. |
| `DEFAULT_LANGUAGE` | `tc` | Language used when the request omits one. |
| `TEMPERATURE` | `0` | Decoding temperature (0 = deterministic). |
| `RERANK_DOC_MAX_CHARS` | `2000` | Per-passage char budget sent to the reranker. |
| `MAX_AGENT_ITERS` | `8` | Max tool-calling rounds per request. |
| `CHAT_LOG_ENABLED` | `true` | Log each `/chat` exchange to the `hiwin_cs_db` schema (see [Chat logging](#chat-logging)). |
| `CHAT_TIMEOUT` | `120` | Per-call timeout (s) to the inference server. Raise it if cold model loads exceed it (500s / read timeouts). |

The retrieval quality and cost are tunable too — sensible defaults ship, so you
only touch these to trade recall vs. speed:

| Variable | Default | Description |
|---|---|---|
| `VECTOR_TOP_K` | `15` | pgvector rows pulled per table. |
| `RERANK_CANDIDATE_K` | `20` | passages fed into the reranker. |
| `RERANK_TOP_K` | `6` | passages kept after reranking (per manual search). |
| `CERT_RERANK_TOP_K` | `10` | passages kept for certification search. |
| `GRADING_ENABLED` | `false` | LLM YES/NO relevance filter on each passage. Off — it's a *precision* filter and conflicts with exhaustive recall. |
| `COMPLETENESS_PASS_ENABLED` | `true` | Re-read each passage to recover rows the draft summarized away. |
| `COMPLETENESS_MODEL` | `LANGUAGE_MODEL` | Model for the per-passage enumeration — point at a small resident model (e.g. `Support_Agent_Aux`) to speed it up. |
| `COMPLETENESS_BASE_URL` | `INFERENCE_BASE_URL` | Endpoint for that model (only if served separately). |
| `COMPLETENESS_CONCURRENCY` | `1` | Enumeration calls in flight at once. Raise if the server has spare parallel slots. |
| `COMPLETENESS_TIMEOUT` | `120` | Per-enumeration timeout (s). |
| `COMPLETENESS_EXPLAIN_NONE` | `true` | Have a non-matching passage explain *why* (debugging aid). |
| `FIGURE_TEXT_MAX_CHARS` | `120` | Truncate verbose figure descriptions before they reach the model (context saver). |
| `KEEP_RECENT_TOOL_RESULTS` | `4` | Keep only the N most recent tool results in full in the running context; stub older ones. `-1` disables. |
| `TRACE_RESULT_MAX_CHARS` | `600` | How much of each tool result the debug trace keeps. `0` = no truncation. |

> **Language codes** (`en` / `jp` / `tc` / `sc`) must match the values stored in the
> DB's `metadata_->>'language_code'`. Confirm with `python inspect_metadata.py`.

> **Building the DB too?** Set the **same** `DB_*` values and embedding model in
> `pipeline/settings.json` — the backend reads what the pipeline writes. See
> [Data ingestion pipeline](#data-ingestion-pipeline).

> **Internal DB password.** HIWIN's internal deployment uses the database password
> **`hiwinpassword`** — put it in `.env` (`DB_PASSWORD`) and, for the pipeline, in
> `pipeline/settings.json` (`DB_PASS`). It is intentionally left in
> `reference/tools/database_query.py` (preserved open-WebUI provenance) rather than
> scrubbed. This repository is proprietary / internal use only (see
> [LICENSE](LICENSE)); **rotate this password** if the project is ever made public
> or shared externally.

## Model parameters

Most generation settings live on the **inference server** — each model's section
in your `--models-preset` (`router.example.ini`) — not in this repo. The two
that matter most:

- **Temperature** (`--temp`, e.g. `--temp 0.7`). How random the output is: `0` is
  deterministic (same input → same answer), higher values (`0.7`–`1.0`) give more
  varied wording. Keep it **low/zero for accuracy-sensitive work** — transcribing
  exact numbers from spec tables — and higher only where natural phrasing matters.
  - The **backend** additionally sends a per-request `temperature` (the
    `TEMPERATURE` env var, default `0`; `inference.py`), which **overrides** the
    server's `--temp` for the support agent.
  - The **pipeline** passes don't override it, so they use whatever `temp` you
    set per model in the preset.
- **Context size** (`--ctx-size`, e.g. `--ctx-size 100000`). The maximum number of
  tokens (prompt **+** generated output) the model can process in one request.
  It's a launch-time / VRAM setting: a bigger context needs more GPU memory. Set
  it large enough for your longest page or table plus the model's answer; if a
  request exceeds it, the server truncates the input or errors out. The apps never
  change this — it's fixed by how you launch the model.

Example preset entry (in `router.example.ini`): `ctx-size = 100000`, `temp = 0.7`,
… Change these in the preset, then restart the router; no change to this repo is
needed.

## Running

Run the backend **from the repo root** (the folder with `main.py`), with your
virtual environment **activated** — `uvicorn` must import `main`, so the working
directory has to be the repo root:

```bash
cd /path/to/HIWIN_Support_Agent      # the repo root (where main.py lives)
# activate the venv:  Windows: .venv\Scripts\activate   |   Linux/macOS: source .venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8079
```

(Or just use the launcher: `run.bat` on Windows / `./run.sh` on Linux/macOS — it
activates the venv and starts uvicorn for you.)

- `--host 0.0.0.0` makes it reachable from other machines (mind the firewall).
- Omit it (or use `--host 127.0.0.1`) to keep it local to the server.
- Then open `http://localhost:8079/` for the demo, or POST to `/chat` (see [Usage](#usage)).

### Verify each layer (bottom-up smoke test)

If `/chat` misbehaves, test the stack from the bottom up — each command only
succeeds if the ones before it do, so the **first one that fails points at the
layer to fix**:

```bash
# 1. Backend process is up:
curl http://localhost:8079/health                 # -> {"status":"ok"}

# 2. Inference server reachable (match host/port to INFERENCE_HOST):
curl http://localhost:11400/v1/models             # -> lists your model aliases

# 3. Postgres reachable + data ingested with the expected language codes:
python inspect_metadata.py                         # -> prints sample metadata_ rows

# 4. Full end-to-end flow:
curl -X POST http://localhost:8079/chat \
  -H "Content-Type: application/json" \
  -d '{"prompt": "HGW20 load capacity", "language": "en"}'
```

Then cross-reference the failing layer in [Troubleshooting](#troubleshooting).

## Usage

### `POST /chat`

```bash
curl -X POST http://localhost:8079/chat \
  -H "Content-Type: application/json" \
  -d '{"prompt": "HGW20 load capacity", "language": "en"}'
```

Response:

```json
{
  "response": "### HGW20 Load Capacity …  (markdown, may include ![](/static/HIWIN/…) images)",
  "language": "en",
  "sources": [
    {"product_type": "linear_guideway", "page_number": 6, "file_name": "…", "language_code": "en"}
  ],
  "trace": [
    {"tool": "db_get_available_product_tables", "args": {}, "result": "…"},
    {"tool": "db_search_technical_manuals", "args": {"…"}, "result": "…"}
  ]
}
```

- **`response`** — the answer as GitHub-flavored markdown. Render it with any
  markdown library; inline `![](...)` images resolve against `/static/HIWIN`.
- **`language`** — optional in the request; defaults to `DEFAULT_LANGUAGE`.
- **`sources`** — deduplicated citation metadata for the retrieved chunks.
- **`trace`** — the tools the agent called, in order (handy for debugging).

### Other endpoints

| Endpoint | Purpose |
|---|---|
| `GET /` | Example demo frontend (served from `frontend/index.html`). |
| `GET /health` | Liveness check → `{"status": "ok"}`. |
| `GET /static/HIWIN/...` | Serves the HIWIN figures from `IMAGE_STATIC_ROOT`. |

### Batch runner

`run_prompts.py` sends a set of requests and saves a markdown + JSON report:

```bash
python run_prompts.py                          # every examples/*.json
python run_prompts.py examples/tc_load_capacity.json
export CHAT_URL=http://localhost:8079/chat     # override the endpoint (Linux/macOS)
set CHAT_URL=http://localhost:8079/chat        # override the endpoint (Windows cmd)
```

### Inspecting the DB

`python inspect_metadata.py` prints the `metadata_` of a few sample rows — use
it to confirm the real `language_code` values and citation fields.

## Chat logging

Every `POST /chat` exchange is recorded to a Postgres table for analytics and
debugging. It is **on by default** (`CHAT_LOG_ENABLED=true`) and stored in a
**separate schema — `hiwin_cs_db`** — inside the same database, so it's isolated
from the RAG data. Logging is **best-effort**: a DB hiccup is printed and ignored,
never breaking a chat response. The schema and table are created automatically on
first use (the DB user needs `CREATE` privilege the first time).

Each row records:

| Column | Meaning |
|---|---|
| `created_at` | timestamp |
| `language`, `prompt`, `response` | request language, user prompt, final answer |
| `sources`, `trace` | citation list + tool-call trace (jsonb) |
| `latency_ms` | total wall-clock time for the request |
| `llm_calls`, `agent_iterations`, `tool_calls` | model generations / agent rounds / tool calls used |
| `prompt_tokens`, `completion_tokens`, `total_tokens` | token usage, summed across generations |
| `generations` | jsonb array, per-generation `{duration_ms, prompt_tokens, completion_tokens}` |

Settings (in `.env`): `CHAT_LOG_ENABLED` (default `true`), `CHAT_LOG_SCHEMA`
(default `hiwin_cs_db`), `CHAT_LOG_TABLE` (default `chat_logs`). Set
`CHAT_LOG_ENABLED=false` to turn it off.

## Example demo frontend

`frontend/index.html` is a small, self-contained HTML page served at `/`, meant
purely to **demonstrate** the API — type a prompt, pick a language, and see the
markdown answer, inline images, and sources rendered. Treat it as a starting
point or reference, not a production UI; swap in your own frontend whenever you
like.

Because it is served from the **same origin** as `/chat` and `/static/HIWIN`,
the root-relative image links in answers resolve to this backend with no extra
configuration. (A separate-origin frontend would instead need a reverse proxy or
absolute image URLs.) It renders markdown with `marked` from a CDN — vendor that
file locally if the browser is offline; LaTeX (`$M_R$`) needs KaTeX, not included.

### Debug panel

The demo page has a **Debug view** toggle (on by default) — the fastest way to see
*why* an answer came out the way it did, and the first place to look when
troubleshooting. Under each answer it shows:

- **Metrics strip** — language, agent iterations, tool/LLM/completeness calls,
  token usage, and end-to-end latency.
- **Trace timeline** — every tool call in order, each with its **args** (the
  `language_code` / `product_table` / query the model searched) and its **result**
  inline; empty results are flagged, so a bad language filter or a missed table
  jumps out. (`TRACE_RESULT_MAX_CHARS` controls how much of each result is kept —
  set it to `0` to read full passages.)
- **Completeness pass** — per retrieved passage, whether the enumeration `✓ found`
  rows, returned `NONE` (with the reason, if `COMPLETENESS_EXPLAIN_NONE`), or hit an
  `⚠ error` (e.g. a timeout).
- **Sources** — each with its reranker `score`, and a **Raw response JSON** dump.

Everything shown here is also in the raw `/chat` response (`trace`, `sources`,
`metrics`) and the [chat log](#chat-logging), so you get the same insight from the
API without the demo page.

## Data ingestion pipeline

This backend **reads** a Postgres + pgvector database; it does not build it. The
[`pipeline/`](pipeline/) folder is the **upstream** half that does:

```
PDFs ──▶ pipeline/ (layout detect → VLM transcribe → validate → embed) ──▶ Postgres/pgvector ──▶ this backend
```

It detects page layout with an ONNX model, transcribes pages/figures/tables with
a local vision model, validates the markdown, then embeds each page/section and
inserts it into the same `hiwin_rag` schema (`data_*` tables, `metadata_` jsonb)
that the backend's retrieval tools query. The embedding model **must match** the
backend's `EMBEDDING_MODEL`, and the figure/table images it copies into the web
static root are what the backend serves at `/static/HIWIN`.

### What you must edit before the first run

Almost everything is pre-filled. With your own database you only need to set a few
**machine-specific paths** (the committed config ships with `/path/to/...`
placeholders):

| File | Field(s) | Set to |
|---|---|---|
| `pipeline/settings.json` | `ROOT_PATH` | your HIWIN data folder (holds `PDFs/`, `PDF_Config.yaml`, the ONNX model) |
| `pipeline/settings.json` | `IMAGE_TARGET_ROOT` | the web static folder the backend serves at `/static/HIWIN` |
| `pipeline/models.json` | `model_dir` | the folder llama.cpp loads GGUFs from (the `model` paths in your router preset) |
| `pipeline/models.json` | each `repo_id` | the Hugging Face repo per GGUF (only if you'll use the downloader) |
| `<ROOT_PATH>/PDF_Config.yaml` | — | optional — the first run **generates it from `PDFs/` then stops** for review (languages auto-detected from filenames; every page). Check it, then run again. Pre-create from `pipeline/PDF_Config.example.yaml` to skip this. |
| `.env` (backend) | `IMAGE_STATIC_ROOT` | the same folder as the pipeline's `IMAGE_TARGET_ROOT` (DB password & model names already filled in) |

DB credentials, model names, ports, and tuning are already set for the internal
deployment — change them only if your setup differs. (On Windows you can use
either `C:\...` or `C:/...`; forward slashes work on every OS.)

### What goes in the project root (`ROOT_PATH`)

`ROOT_PATH` is the pipeline's data folder. You create it and put **two** things
inside (the rest is generated automatically):

```
<ROOT_PATH>/
├── PP-DocLayout-PlusL.onnx   # YOU PROVIDE: the layout-detection model (Pass 1).
│                             #   Must sit directly in ROOT_PATH with this exact name.
├── PDFs/                     # YOU PROVIDE: source PDFs, nested product/sub-folder/file
│   └── <product>/<sub-folder>/<file>.pdf
├── PDF_Config.yaml           # which PDFs/pages to process (auto-created on 1st run, then review & re-run)
├── Process_Files/            # intermediate per-pass output  (auto-created)
└── Final_Output/             # final markdown + Figures/Tables (auto-created)
```

So the **only files you place by hand** are `PP-DocLayout-PlusL.onnx` (directly in
`ROOT_PATH`) and your PDFs under `PDFs/`. The ONNX model is the PaddleOCR
PP-DocLayout_plus-L file (see [Prerequisites](#prerequisites)).

### Configuring which PDFs to process (`PDF_Config.yaml`)

The pipeline is driven by a YAML file at `<ROOT_PATH>/PDF_Config.yaml` that lists
your PDFs.

**If it doesn't exist, the pipeline generates it and then stops** — so you can
review it *before* anything is processed. On a run with no `PDF_Config.yaml` it
scans `<ROOT_PATH>/PDFs/` and writes one entry per PDF, **every page included**,
with each PDF's **language auto-detected from its filename**:

- Japanese kana in the name → `jp`; Chinese characters → `tc`;
- otherwise a language keycode in the name (e.g. `_en`, `_jp`, `zh-tw`, `zh-cn`);
- falling back to `DEFAULT_LANGUAGE` (default `tc`; override via the
  `PIPELINE_DEFAULT_LANGUAGE` env var).

It then **halts with a message asking you to review and edit the generated file**
(the file also carries a header comment saying so) — confirm each `language` and
add any `pages_to_exclude` — and **run again** to actually process. Auto-detection
is best-effort (a kanji-only Japanese title can look Chinese), so a quick check is
worth it.

You can also write it yourself: copy [`pipeline/PDF_Config.example.yaml`](pipeline/PDF_Config.example.yaml)
to `<ROOT_PATH>/PDF_Config.yaml` and edit it. It's a tree of nested folders ending
in one **leaf per PDF**:

```yaml
ballspline:                  # product — top folder & DB table (data_ballspline)
  user_manual:               # any number of grouping sub-folders (any depth)
    HG_Series.pdf:           # leaf key = the actual PDF file name
      language: en           # en | jp | tc | sc  (must match the DB language_code)
      pages_to_exclude: [0, 1]      # 0-indexed pages to skip (cover, blanks…)
    LM_Guide.pdf:
      language: tc
      pages_to_include: [5, 6, 7]   # if present & non-empty, ONLY these pages
                                    # run (pages_to_exclude is then ignored)
linear_guideway:
  catalog.pdf:
    language: en
    pages_to_exclude: []            # [] = process every page
```

How it's interpreted:

- A node is a **PDF task** when it has a `language` key plus `pages_to_include`
  **or** `pages_to_exclude`. Everything above it is just folder nesting.
- The PDF file must exist on disk at
  `<ROOT_PATH>/PDFs/<product>/<…sub-folders…>/<leaf filename>`.
- Pages are **0-indexed**. `pages_to_include` wins over `pages_to_exclude` when
  non-empty. Each may be a list (`[0, 2, 5]`) or a JSON string (`"[0, 2, 5]"`).
- Output is written to `Final_Output/<product>/<sub-folders joined by _>/<language>/`
  and ingested into the `data_<product>` table. `Process_Files/` and
  `Final_Output/` are created automatically.

### Running the pipeline

`pipeline/settings.json` and `pipeline/models.json` are **committed** (already
filled in for this deployment). Before the first run you only need to **edit the
file paths** in them for your machine/OS (see
[What you must edit](#what-you-must-edit-before-the-first-run)). Then:

Run all pipeline commands **from inside the `pipeline/` folder**, with the venv
**activated** (they import `core` / `Pipeline`, so the working directory must be
`pipeline/`):

```bash
# 1. activate the venv (from the repo root):
#    Windows:      .venv\Scripts\activate
#    Linux/macOS:  source .venv/bin/activate
# 2. enter the pipeline folder:
cd pipeline
# 3. pick one:
python gui.py                 # GUI: settings screen + live per-PDF monitor
python tui.py                 # TUI: same, in the terminal (best in Windows Terminal, not cmd.exe)
python Pipeline.py            # headless: full pipeline (no UI; uses settings.json/env)
python -m ingestion.Ingest    # (re)ingest Final_Output markdown into the DB only
python -m ingestion.Ingest --db-only   # ingest without copying images to the web root
```

Use **either** front-end — the graphical `gui.py` or the terminal `tui.py`; they
are interchangeable. Both open on a **settings screen** (an editor over the same
fields, including a masked **DB password**) and then show a live monitor of every
PDF and pass. The committed `settings.json` already contains the internal DB
password; editing it on the settings screen (or setting the `DB_PASS` env var)
overrides it. Whatever you change on that screen is saved back to `settings.json`.

Both UIs default to **繁體中文** and carry an **English ⇄ 繁體中文 toggle**: click
the `中`/`EN` button in the GUI header, or press `l` on the TUI settings screen.
The setting fields, status, phase, and summary labels all switch language live.
For fully unattended automation, run `python Pipeline.py` instead (no UI; reads
`settings.json` / environment variables).

### Downloading model files (optional)

The pipeline and backend call your inference server over HTTP — they never load
GGUFs themselves. As a convenience, the GUI/TUI can check whether the GGUF files
your server needs are present in a folder and download the missing ones from
**Hugging Face**, with your confirmation:

1. `pipeline/models.json` is committed and already lists the GGUF **filenames**.
   Edit it to set:
   - `model_dir` — the folder your llama.cpp server loads GGUFs from
     (must match the `model` paths in your router preset);
   - each model's Hugging Face `repo_id` (the repo hosting that exact `.gguf`)
     — `hf_token` is optional, for gated/private repos (`HF_TOKEN` env also works).
2. Trigger the check/download:
   - **GUI** — click **Download models…** on the settings screen;
   - **TUI** — press `m` on the settings screen.

   It reports which files are present, lists any missing, and — only on your
   confirmation — downloads them into `model_dir` (resumable, via `huggingface_hub`).

This only provisions the *files*; you still configure and run the inference
server yourself. The model **names** in settings are your router preset section
names, so the manifest is what maps each name to a real Hugging Face repo/file.

Dependencies for the pipeline are merged into the root
[`requirements.txt`](requirements.txt) (the heavier ML/vision block), so a single
`pip install -r requirements.txt` provisions both halves. Configuration is **not**
shared with the backend's `.env`: the pipeline reads `pipeline/settings.json`
(GUI-editable; overlays the defaults in `pipeline/core/config.py`). Keep the DB
name/schema and embedding model in sync between the two.

### How the model names link up

Beyond **downloading** the GGUFs (GUI/TUI) and **hosting** them (llama.cpp router
mode), no extra wiring is needed — the names chain together automatically:

```
models.json ──(filename)──> the .gguf in model_dir
                                   │  the router preset section loads it by name
                                   ▼
router preset [section] name ──used by──> pipeline MODEL_PASS_* (settings.json)
                                          backend LANGUAGE/EMBEDDING/RERANKER_MODEL (.env)
```

Two name-sets must match — and they already do, as shipped:

1. **Model names** — the `[section]` names in your router preset equal the names
   the apps request: pipeline `MODEL_PASS_2/2B/3` = `RAG_Pipeline_Pass34`,
   `MODEL_PASS_3B/4` = `RAG_Pipeline_Pass5Ingest`; backend
   `LANGUAGE_MODEL` / `EMBEDDING_MODEL` / `RERANKER_MODEL` =
   `Support_Agent_Qwen3.6` / `Embedding_Qwen3.6` / `Reranker_Qwen3.6`.
2. **Filenames** — each `models.json` `filename` equals the `model` file its
   preset section loads, both inside `model_dir`.

So `MODEL_PASS_3` (and the rest) resolve correctly out of the box: the app
requests the model name, the router loads that section's GGUF. If you ever rename
a model, change it in **both** places (the router preset and the settings/`.env`),
and keep `model_dir` pointing at the folder the preset loads from.

## Project structure

```
HIWIN_Support_Agent/
├── main.py             # FastAPI app: /chat, /health, /, /static/HIWIN
├── agent.py            # the LLM tool-calling loop (the STATE machine driver)
├── rag_tools.py        # the 4 retrieval tools (pgvector → rerank → vision)
├── tool_schemas.py     # OpenAI function-calling schemas + dispatch
├── inference.py        # chat / embeddings / rerank HTTP wrappers
├── db.py               # Postgres access + SQL
├── prompts.py          # builds system prompt = System_prompt.md + skill
├── config.py           # env-driven settings (loads .env)
├── router.example.ini  # llama.cpp router preset (edit model paths, then launch)
├── start.bat / start.sh# one-command launch: router (all models) + backend
├── run.bat / run.sh    # backend-only launch (router already running)
├── run_prompts.py      # batch tester
├── inspect_metadata.py # DB metadata inspector
├── frontend/index.html # example demo frontend
├── prompts/
│   ├── System_prompt.md
│   └── skills/         # english / japanese / traditional_chinese / simplified_chinese
├── examples/           # sample request payloads
├── docs/               # ARCHITECTURE (en + zh-Hant)
├── reference/          # the original open-WebUI filter + tool, for provenance
└── pipeline/           # data ingestion pipeline (PDF → pgvector DB)
    ├── Pipeline.py     #   orchestrator (Phase A passes → ingestion)
    ├── gui.py          #   Tkinter GUI: settings + live monitor
    ├── tui.py          #   terminal UI (rich): same settings + live monitor
    ├── pdf_passes/     #   Pass 1 (ONNX layout) → 2/2b/3/3b (VLM) → 4 (validate)
    ├── ingestion/      #   embed markdown → Postgres/pgvector (Ingest.py)
    ├── core/           #   config, settings, i18n (en/繁中), checkpointing, utils
    ├── settings.json          # committed config — edit the file paths per machine
    ├── models.json            # committed GGUF manifest — edit model_dir + repo_ids
    └── PDF_Config.example.yaml # copy to <ROOT_PATH>/PDF_Config.yaml; lists the PDFs
```

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| Connection refused / errors calling the inference server (`:11400`) | the router (`llama-server --models-preset`) isn't running, or `INFERENCE_HOST` points at the wrong host/port. Start it (`start.bat`/`start.sh`) and check `curl http://localhost:11400/v1/models` lists your models. |
| `CREATE EXTENSION vector` fails: *"extension \"vector\" is not available"* | pgvector isn't installed into Postgres (it's a server-side extension, not a pip package). Install the pgvector OS package or use a `pgvector/pgvector` Docker image, then retry. Creating the extension needs a **superuser**. |
| `fe_sendauth: no password supplied` | `DB_PASSWORD` is empty/unset. Set it in `.env` (repo root — a hidden file; see [Configuration](#configuration)) to the same value as `pipeline/settings.json` → `DB_PASS`, then restart. If it *is* set, `.env` isn't loading — confirm `python-dotenv` is installed (`pip install -r requirements.txt`). |
| Agent loops (many `db_search_*` calls) then returns a garbled non-answer | Every retrieval is erroring (often the DB-auth issue above) or returning nothing, so the model retries until `MAX_AGENT_ITERS`. Open the **Debug panel** (or the raw `trace`) and the backend console to see what each search returned. |
| `connection ... server closed the connection unexpectedly` | Postgres only trusts local connections. Run the backend **on the DB server** (use `localhost`), or enable remote access in `pg_hba.conf` / `postgresql.conf`. |
| `db_search_*` returns *"No results for language…"* | The `language_code` you send doesn't match the DB. Check with `inspect_metadata.py`. |
| `Reranking failed: HTTP 500 … input is too large … increase the physical batch size` | The reranker's `ubatch-size` is too small. In the router preset set `ubatch-size = 4096` / `batch-size = 4096` on the reranker section, or lower `RERANK_DOC_MAX_CHARS`. |
| Answers come back but the agent never calls tools | The inference server isn't returning OpenAI-style `tool_calls`. Confirm it supports tool calling; adapt `agent.py` if its format differs. |
| Answer is incomplete — lists only some products / drops table rows | Check the **Debug panel**: if searches returned the data, it's under-enumeration — keep `COMPLETENESS_PASS_ENABLED=true` and `GRADING_ENABLED=false` (grading can drop relevant pages). If a passage shows `NONE` with a wrong reason, the enumeration is over-filtering. |
| Responses are very slow (minutes) | Mostly the completeness pass (one model call per retrieved passage) plus cold model loads. Point `COMPLETENESS_MODEL` at a small resident model and raise `COMPLETENESS_CONCURRENCY`; raise `CHAT_TIMEOUT`; or lower `RERANK_TOP_K`. The Debug panel's `completeness_calls` + latency show where the time goes. |
| `read timeout` / n_ctx overflow on multi-search queries | Raise `CHAT_TIMEOUT` for cold loads; for context overflow lower `RERANK_TOP_K`, `FIGURE_TEXT_MAX_CHARS`, or `KEEP_RECENT_TOOL_RESULTS` (trims old tool results from context). |
| Images 404 in the demo | `IMAGE_STATIC_ROOT` is unset or points at the wrong folder. Open a `/static/HIWIN/...` URL directly to verify. |
| Images don't show in a separate frontend | The frontend isn't same-origin. Serve it from this backend, put both behind one reverse proxy, or make image URLs absolute. |

## Known issues

- **Mixed catalog revisions across languages.** The DB currently holds different
  catalog editions per language (e.g. English/Chinese from a newer revision,
  Japanese from an older one), so some spec values legitimately differ between
  languages. This is a **data** matter (fix it in the ingestion pipeline), not a
  backend bug.
- **LaTeX in the demo frontend.** `$...$` math (e.g. `$M_R$`) renders as literal
  text; add KaTeX to the demo page if needed.

## License

Proprietary / internal use only. See [LICENSE](LICENSE).

This project depends on third-party software and models; their licenses are listed
in [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md). Two carry obligations worth
noting before any external distribution: **PyMuPDF** (AGPL-3.0, pipeline only) and
the **Gemma** model (Google's Gemma Terms of Use).
