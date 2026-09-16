# Architecture

[English] · [繁體中文](ARCHITECTURE.zh-Hant.md)

This document explains how the **HIWIN Support Agent** is put together and why.
For setup and usage see the [README](../README.md); for day-to-day operations
see [MAINTENANCE.md](MAINTENANCE.md).

## 1. The two halves and what joins them

```
            PDFs ─▶ pipeline/ (layout → VLM transcribe → validate → embed) ─▶ PostgreSQL + pgvector
                                                                                   │  data_* tables
                                                                                   ▼
client ─▶ POST /chat ─▶ backend (agent loop + 4 retrieval tools) ◀─────────── embedding / rerank / chat
                                                                       llama.cpp router (config.ini)
```

Both halves call the same llama.cpp router by model *name*; both read/write the
same database. Their configs are separate files (`pipeline/settings.json`,
`.env`) that must agree — `env_from_settings.py` copies the shared keys, and
`doctor.py` checks them.

## 2. Request lifecycle (backend)

```
client ──POST /chat {prompt, language}──▶ main.py
  1. resolve language (request value, else DEFAULT_LANGUAGE)
  2. prompts.build_system(language)         → System_prompt.md + skills/<lang>.md
  3. build_user_message(prompt, language)   → "[Language Code: xx]\n<prompt>"
  4. agent.run(system, user_msg)            → tool-calling loop (+ completeness pass)
  5. prune `sources` to the pages the answer cites
  6. best-effort insert into hiwin_cs_db.chat_logs
  7. return ChatResponse {response, language, sources, trace, metrics}
```

Side channels travel alongside the loop: `trace` (every tool call + a preview of
its result), `sources` (deduplicated citation metadata with the reranker score),
and `metrics` (per-call timings and token usage, see §7).

## 3. The state machine (prompts)

The behaviour is defined in natural language in `prompts/System_prompt.md`,
which the model executes as a state machine:

| State | Purpose | Tool(s) |
|---|---|---|
| **0 — Routing** | Detect language, load the matching skill, classify the question, choose a path. | — |
| **1 — DB query** | Decompose the question (product scope, attribute, adjacent components), then retrieve every on-dimension fact, one keyword per call. | `db_get_available_product_tables`, `db_search_technical_manuals` |
| **2 — URL retrieval** | Return download / CAD links. | `db_search_product_urls` |
| **3 — Format response** | Bullet list of every value + highlight + citations, in the user's language, consultative tone. | — |
| **4 — Fallback** | Emit the skill's contact-form template for out-of-scope or empty results. | — |

The **skills** (`prompts/skills/*.md`) carry the per-language routing rules: they
classify a question into 12 categories and answer only types 1–2 (product /
documentation questions), otherwise returning a localized "please use the contact
form" template.

The model is *not* orchestrated step-by-step in Python — it decides which tool
to call and when, via OpenAI-style function calling. The Python side executes
the tools, feeds results back, and adds the completeness pass.

## 4. Modules

| Module | Responsibility |
|---|---|
| `main.py` | FastAPI app; `/chat`, `/health`, demo frontend at `/`, image mount at `/static/HIWIN`; cited-source pruning; chat-log dispatch. |
| `agent.py` | The tool-calling loop (parallel tool calls, tool-history trimming, iteration cap) and the **completeness pass** (`pre_draft` / `post_draft`). |
| `tool_schemas.py` | OpenAI function schemas for the 4 tools + a name→callable `DISPATCH` map. |
| `rag_tools.py` | The 4 tools: pgvector search (parallel per table), table listing, certifications, URL listing — plus reranking, optional grading and vision helpers. |
| `inference.py` | HTTP wrappers for `/v1/chat/completions`, `/v1/embeddings`, `/v1/rerank`; records every call's duration/tokens into a per-request log (contextvar). |
| `db.py` | Threaded connection pool, SQL constants (original open-WebUI queries kept base64-encoded), forgiving table-name resolution, chat-log writer. |
| `prompts.py` | Concatenate the system prompt with the active skill. |
| `config.py` | Env-driven settings (loads `.env`). |
| `doctor.py`, `setup_db.py`, `env_from_settings.py`, `inspect_metadata.py` | Operations tooling (preflight, DB bootstrap, config sync, data inspection). |

## 5. The agent loop (`agent.py`)

```
messages = [system, user]
repeat up to MAX_AGENT_ITERS:
    trim older tool results (KEEP_RECENT_TOOL_RESULTS)
    resp = inference.chat(messages, tools=TOOL_SCHEMAS)
    msg  = resp.choices[0].message; append msg
    if no msg.tool_calls:
        answer = msg.content
        if COMPLETENESS_MODE == post_draft: answer = completeness_pass(answer, retrieved passages)
        return answer
    run all tool_calls (concurrently if PARALLEL_TOOL_CALLS)
    for each result:
        collect [SOURCE:…]-tagged passages
        if COMPLETENESS_MODE == pre_draft: enumerate matching rows now, append to the tool result
        accumulate sources (dedup, keep best rerank score); append {role: tool, content}
# iteration cap reached → one more call without tools, return its content
```

Tools are `async` (blocking DB/HTTP work runs via `asyncio.to_thread`).
Retrieval tools return a `{"text", "sources"}` dict — `text` goes back to the
model, `sources` is harvested for the response. The other tools return a string.

### The completeness pass

Long spec tables get summarised by the drafting model ("HGW15…HGW65" becomes
three representative rows). The completeness pass fights that:

- **`_enumerate_passage`** re-reads ONE retrieved passage in isolation and lists
  every entry matching the request (or `NONE — <why>`). It runs on
  `COMPLETENESS_MODEL` (the small resident `Support_Agent_Aux`), bounded by a
  global semaphore (`COMPLETENESS_CONCURRENCY`).
- **`pre_draft`** (the production setting): enumeration happens *inside* the
  loop, right after each search, and the extracted rows are appended to the tool
  result, so the main model drafts once from passages + rows.
- **`post_draft`**: draft first, then enumerate everything and ask the main model
  to reconcile draft + extractions into a final answer.

Per-passage outcomes (`found` / `none` / `error` + raw output) are exposed in
`metrics.completeness` and shown in the demo page's debug panel.

## 6. Retrieval pipeline (`rag_tools.py`)

`db_search_technical_manuals` is the core path:

1. **Embed** the query as `search_query: <query>` (the instruction prefix the
   embedding model expects) via `/v1/embeddings`.
2. **Resolve tables** — the model's `product_table` guesses are mapped onto real
   `data_*` names (case/prefix/punctuation tolerant) by `db.resolve_tables`.
3. **Vector search** — for each table, in parallel (bounded by
   `DB_SEARCH_CONCURRENCY`, each on a pooled connection), a pgvector cosine query
   (`embedding <=> %s::vector`) filtered by `metadata_->>'language_code'`,
   `LIMIT VECTOR_TOP_K`. `data_all_products` is a "bypass" table returned whole.
4. **English fallback** — if the target language yields nothing, retry in `en`.
5. **Rerank** — the pooled best `RERANK_CANDIDATE_K` go to `/v1/rerank`; the top
   `RERANK_TOP_K` are kept. Each passage is truncated to `RERANK_DOC_MAX_CHARS`
   **for scoring only** (the full passage is preserved) to stay under the
   reranker's physical batch size.
6. **Grading** (off by default) — an LLM YES/NO relevance filter per passage.
7. **Tagging** — each passage gets a `[SOURCE: file=… · page=… · product=…]`
   header (so inline citations are honest), image paths are URL-encoded, and
   verbose figure captions are clipped (`FIGURE_TEXT_MAX_CHARS`).
8. **Vision** — up to `VISION_MAX_IMAGES` `/static/HIWIN/...` images from the kept
   passages are loaded from `IMAGE_STATIC_ROOT`, base64-encoded and sent to the
   vision model; the description is appended to the tool output. Skipped
   silently when the folder is absent.
9. **Sources** — the `metadata_` of each kept passage is whitelisted into a
   citation dict (page / file / `web_path` / …) with its `rerank_score`.

`db_search_certifications` is the same shape against `data_certificates` (whose
metadata includes a ready-to-use `web_path`). `db_search_product_urls` returns
all rows of the two flat URL tables.

### SQL note

The original open-WebUI tool stored its SQL base64-encoded; those constants are
preserved byte-for-byte in `db.py` (decoded form shown in comments). The
metadata-returning variants (`SQL_VECTOR_SEARCH_META`, `SQL_VECTOR_BYPASS_META`)
are added as plain text for the citations feature.

## 7. Metrics, tracing and chat logging

`inference.start_recording()` installs a per-request list in a `contextvars`
variable; every `chat` / `embed` / `rerank` call appends `{kind, model,
duration_ms, prompt_tokens, completion_tokens[, error]}`. Because contextvars
propagate into `asyncio.gather` tasks and `to_thread` workers, the vision and
completeness calls three layers down are captured too. `agent.run` folds them
into `metrics` (`by_kind`, `llm_calls`, `total_tokens`, `agent_iterations`,
`tool_calls`, `completeness_calls`, `completeness`). `main.py` adds
`latency_ms` and writes prompt, answer, sources, trace and metrics to
`hiwin_cs_db.chat_logs` (best-effort; never fails the response).

## 8. Data model (what the pipeline writes)

Each `data_<product>` table is a LlamaIndex `PGVectorStore` table:
`id`, `text`, `metadata_ jsonb`, `node_id`, `embedding vector(2560)`. Rows are
one per PDF page (`INGEST_BY_PAGE = true`), text starting with `Page N`, image
links rewritten to `/static/HIWIN/<product>/<sub_folder>/<lang>/Figures|Tables/…`.
`metadata_` carries `file_name` (the document id `<Product>_<sub_folder>_<lang>`),
`page_number`, `language_code`, `product_type`, `sub_folder`, `source_md`,
`version`, `date_ingested`. Re-ingesting a document deletes its previous rows
(by `file_name`) and bumps `version`.

The two `data_product_*_urls` tables are flat (no vectors). `data_certificates`
rows carry a `web_path` to the certificate image.

## 9. Citations and images

- **Citations.** `sources` is built from `metadata_`, not from the model's prose,
  so it is reliable even if the model mis-types a page number. The system prompt
  forbids citing pages that are not in a `[SOURCE:…]` tag, and `main.py` prunes
  `sources` to the pages actually cited (falling back to all when none parse).
- **Images.** The model emits markdown `![alt](/static/HIWIN/...)` per the system
  prompt's image rules. `main.py` mounts `IMAGE_STATIC_ROOT` at `/static/HIWIN`,
  so those root-relative URLs resolve to this backend — provided the frontend is
  served from the **same origin** (the bundled demo at `/` is). For a separate
  frontend, use a reverse proxy or rewrite the image URLs to absolute.

## 10. Lineage from open-WebUI

| open-WebUI artifact | Replaced by |
|---|---|
| Model system prompt | `prompts/System_prompt.md` |
| `Filter.inlet` (language injection) | `build_user_message` in `main.py` |
| `Tools` class (4 functions) | `rag_tools.py` (emitter/Valves stripped) |
| Skills / routing rules | `prompts/skills/*.md` |
| Native function calling | `agent.py` tool-calling loop |
| Static file serving (`/static/HIWIN`) | FastAPI `StaticFiles` mount in `main.py` |

The originals are kept under `reference/` for provenance.

## 11. Key decisions & gotchas

- **Deploy on the DB/model server.** Postgres trusts only local connections by
  default; running the backend on the same host avoids `pg_hba.conf` changes.
- **Router mode, models resident.** All four serving models stay loaded
  (`load-on-startup`), so no request waits for a model swap. Pipeline models load
  on demand and may evict a serving model unless `--models-max` is raised.
- **Deterministic embeddings.** The embedding and reranker sections run with
  `parallel = 1`: with several slots, identical requests returned slightly
  different vectors and rankings wobbled.
- **Reasoning budget.** `reasoning-budget = 8192` on the chat model stops runaway
  thinking that once consumed the whole 150k context and returned empty answers.
- **Reranker batch size.** Reranking/embedding models pool the whole input in one
  physical batch; long passages overflow the default `--ubatch-size 512` and
  return HTTP 500. Mitigated by `RERANK_DOC_MAX_CHARS` and `ubatch-size = 4096`.
- **Language codes.** The DB uses `en` / `jp` / `tc` / `sc`; these must match
  `LANGUAGE_SKILL_MAP` and what clients send. Confirm with `inspect_metadata.py`.
- **`temperature: 0`.** Deterministic decoding reduces numeric drift when the
  model transcribes values from dense spec tables.
- **Windows long paths.** Pipeline output paths exceed 260 characters; every
  filesystem call goes through `core/fs_utils.to_long_path`.
