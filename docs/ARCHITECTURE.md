# Architecture

[English] · [繁體中文](ARCHITECTURE.zh-Hant.md)

This document explains how the **HIWIN Support Agent Backend** is put together
and why. For setup and usage see the [README](../README.md).

## 1. Request lifecycle

```
client ──POST /chat {prompt, language}──▶ main.py
  1. resolve language (request value, else DEFAULT_LANGUAGE)
  2. prompts.build_system(language)         → System_prompt.md + skills/<lang>.md
  3. build_user_message(prompt, language)   → "[Language Code: xx]\n<prompt>"
  4. agent.run(system, user_msg)            → tool-calling loop
  5. return ChatResponse {response, language, sources, trace}
```

Two side channels travel alongside the loop: a `trace` list (every tool call +
a preview of its result) and a `sources` list (deduplicated citation metadata).

## 2. The state machine

The behaviour is defined in natural language in `prompts/System_prompt.md`,
which the model executes as a state machine:

| State | Purpose | Tool(s) |
|---|---|---|
| **0 — Routing** | Detect language, load the matching skill, classify the question, choose a path. | — |
| **1 — DB query** | Retrieve every on-dimension fact for a product family. | `db_get_available_product_tables`, `db_search_technical_manuals` |
| **2 — URL retrieval** | Return download / CAD links. | `db_search_product_urls` |
| **3 — Format response** | Build a complete value table + highlight + citations, in the user's language. | — |
| **4 — Fallback** | Emit the skill's contact-form template for out-of-scope or empty results. | — |

The **skills** (`prompts/skills/*.md`) carry the per-language routing rules:
they classify a question into 12 categories and answer only types 1–2, otherwise
returning a localized "please use the contact form" template.

Crucially, the model is *not* orchestrated step-by-step in Python — it decides
which tool to call and when, via OpenAI-style function calling. The Python side
just executes the tools and feeds results back.

## 3. Modules

| Module | Responsibility |
|---|---|
| `main.py` | FastAPI app; `/chat`, `/health`, demo frontend at `/`, image mount at `/static/HIWIN`. |
| `agent.py` | The tool-calling loop: send messages + schemas, run any `tool_calls`, append results, repeat up to `MAX_AGENT_ITERS`. |
| `tool_schemas.py` | OpenAI function schemas for the 4 tools + a name→callable `DISPATCH` map. |
| `rag_tools.py` | The 4 tools: pgvector search, table listing, certifications, URL listing — plus reranking and vision helpers. |
| `inference.py` | HTTP wrappers for `/v1/chat/completions`, `/v1/embeddings`, `/v1/rerank`. |
| `db.py` | Postgres connection, SQL constants, forgiving table-name resolution. |
| `prompts.py` | Concatenate the system prompt with the active skill. |
| `config.py` | Env-driven settings (loads `.env`). |

## 4. The agent loop (`agent.py`)

```
messages = [system, user]
repeat up to MAX_AGENT_ITERS:
    resp = inference.chat(messages, tools=TOOL_SCHEMAS)
    msg  = resp.choices[0].message
    append msg
    if no msg.tool_calls:        → return msg.content     # final answer
    for each tool_call:
        text, sources = run the tool
        accumulate sources (deduplicated)
        append {role: "tool", tool_call_id, content: text}
# iteration cap reached → one more call without tools, return its content
```

Tools are `async` (they offload blocking DB/HTTP work via `asyncio.to_thread`).
Retrieval tools return a `{"text", "sources"}` dict — `text` goes back to the
model, `sources` is harvested for the response. The other tools return a plain
string.

## 5. Retrieval pipeline (`rag_tools.py`)

`db_search_technical_manuals` is the core path:

1. **Embed** the query as `search_query: <query>` (instruction prefix the
   embedding model expects) via `/v1/embeddings`.
2. **Resolve tables** — the model's `product_table` guesses are mapped onto real
   `data_*` table names (case/prefix/punctuation tolerant) by `db.resolve_tables`.
3. **Vector search** — for each table, a pgvector cosine query
   (`embedding <=> %s::vector`) filtered by `metadata_->>'language_code'`,
   `LIMIT 15`. A special `data_all_products` "bypass" returns rows without a
   distance sort.
4. **English fallback** — if the target language yields nothing, retry in `en`.
5. **Rerank** — the top candidates go to `/v1/rerank`; the top 5 are kept. Each
   passage is truncated to `RERANK_DOC_MAX_CHARS` **for scoring only** (the full
   passage is preserved), to stay under the reranker's physical batch size.
6. **Vision** — any `/static/HIWIN/...` images in the kept passages are loaded,
   base64-encoded, and sent to the vision model for a focused description, which
   is appended to the tool output.
7. **Sources** — the `metadata_` of each kept passage is whitelisted into a
   citation dict (page / file / `web_path` / …).

`db_search_certifications` is the same shape against `data_certificates` (whose
metadata includes a ready-to-use `web_path`). `db_search_product_urls` simply
returns all rows of the two URL tables.

### SQL note

The original open-WebUI tool stored its SQL base64-encoded; those constants are
preserved byte-for-byte in `db.py` (decoded form shown in comments). The
metadata-returning variants (`SQL_VECTOR_SEARCH_META`, `SQL_VECTOR_BYPASS_META`)
are added as plain text for the citations feature.

## 6. Citations and images

- **Citations.** `sources` is built from `metadata_`, not from the model's prose,
  so it's reliable even if the model mis-types a page number. It is deduplicated
  in the agent loop across all tool calls.
- **Images.** The model emits markdown `![alt](/static/HIWIN/...)` per the
  system prompt's image rules. `main.py` mounts `IMAGE_STATIC_ROOT` at
  `/static/HIWIN`, so those root-relative URLs resolve to this backend — provided
  the frontend is served from the **same origin** (the bundled example demo
  frontend at `/` satisfies this). For a separate frontend, use a reverse proxy
  or rewrite the image URLs to absolute.

## 7. Lineage from open-WebUI

| open-WebUI artifact | Replaced by |
|---|---|
| Model system prompt | `prompts/System_prompt.md` |
| `Filter.inlet` (language injection) | `build_user_message` in `main.py` |
| `Tools` class (4 functions) | `rag_tools.py` (emitter/Valves stripped) |
| Skills / routing rules | `prompts/skills/*.md` |
| Native function calling | `agent.py` tool-calling loop |
| Static file serving (`/static/HIWIN`) | FastAPI `StaticFiles` mount in `main.py` |

The originals are kept under `reference/` for provenance.

## 8. Key decisions & gotchas

- **Deploy on the DB/model server.** Postgres trusts only local connections and
  has SSL off; running the backend on the same host avoids `pg_hba.conf` changes.
- **Reranker batch size.** Reranking/embedding models pool over the whole input
  in a single physical batch, so a long passage overflows the default
  `--ubatch-size 512` and returns HTTP 500. Mitigated by truncation here and by
  raising `--ubatch-size` on the server.
- **Language codes.** The DB uses `en` / `jp` / `tc`; these must match
  `LANGUAGE_SKILL_MAP` and what clients send. Confirm with `inspect_metadata.py`.
- **`temperature: 0`.** Deterministic decoding reduces numeric drift when the
  model transcribes values from dense spec tables.
