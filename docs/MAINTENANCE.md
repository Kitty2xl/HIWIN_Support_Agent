# Maintenance guide

[English] · [繁體中文](MAINTENANCE.zh-Hant.md)

The operations manual for whoever keeps the HIWIN Support Agent running. Setup
is in the [README](../README.md); design in [ARCHITECTURE.md](ARCHITECTURE.md).

Contents: [What you may change](#1-what-you-may-change-and-what-must-stay-consistent) ·
[Tuning answers](#2-tuning-the-answers) · [Knowledge base](#3-changing-the-knowledge-base) ·
[Restoring the figures](#restoring-the-figures) · [Backups](#4-backups) ·
[Models and llama.cpp](#5-updating-models-or-llamacpp) · [Passwords](#6-rotating-the-database-password) ·
[Routine checklist](#7-routine-checklist) · [Change history](#8-what-was-changed-in-the-past-and-why)

---

## 1. What you may change, and what must stay consistent

**Change freely (no other file depends on it):**

| Thing | Where | Effect |
|---|---|---|
| Wording, tone, routing rules, out-of-scope templates | `prompts/System_prompt.md`, `prompts/skills/*.md` | next request (no restart needed — files are read per request) |
| Retrieval depth / speed knobs | `.env` (`VECTOR_TOP_K`, `RERANK_*`, `COMPLETENESS_*`, `FIGURE_TEXT_MAX_CHARS` …) | restart the backend |
| Sampling, context size, GPU layers, which quantisation of a model | `config.ini` | restart the router |
| Tool descriptions the model reads | `tool_schemas.py` | restart the backend |
| Demo page | `frontend/index.html` | reload the page |
| Which PDFs / pages are ingested | `<ROOT_PATH>/PDF_Config.yaml` | next pipeline run |

**Must stay consistent (change all at once, then run `python doctor.py --all`):**

| Change | Also change |
|---|---|
| A model's **name** (`[section]` in `config.ini`) | `.env` (`LANGUAGE_MODEL` / `EMBEDDING_MODEL` / `RERANKER_MODEL` / `COMPLETENESS_MODEL`) and/or `pipeline/settings.json` (`MODEL_PASS_*`, `EMBED_MODEL`) |
| Database host / port / name / user / password / schema | `pipeline/settings.json` **then** `python env_from_settings.py` (updates `.env` in place) |
| Image folder | `settings.json` `IMAGE_TARGET_ROOT` → `env_from_settings.py` → `.env` `IMAGE_STATIC_ROOT` |
| **The embedding model** (or its quantisation) | `EMBED_MODEL` + `EMBEDDING_MODEL`, `EMBED_DIM` in `pipeline/core/config.py`, and **re-ingest every document** — old vectors are incompatible with a new model. Never change only the name. |
| Language codes | `config.py` `LANGUAGE_SKILL_MAP` + a skill file + the `language` values in `PDF_Config.yaml` |

**Do not touch without a reason:** the base64 SQL constants in `db.py`
(provenance), the `[SOURCE: …]` tagging in `rag_tools.py` (citations depend on
it), the `data_` prefix logic in the system prompt and `db.resolve_tables`.

## 2. Tuning the answers

### Where behaviour is defined

| Layer | File | What to edit there |
|---|---|---|
| Global behaviour, state machine, recall rules, citation and image rules | `prompts/System_prompt.md` | tone, how exhaustive to be, output layout (bullets vs table), fallback conditions |
| Per-language routing | `prompts/skills/english_support.md`, `japanese_support.md`, `traditional_chinese_support.md`, `simplified_chinese_support.md` | the 12 question categories, which are answered (1–2), the contact-form templates and URL |
| Completeness enumeration + reconciliation prompts | `agent.py` → `_enumerate_passage`, `_completeness_pass` | how rows are extracted and merged; the `NONE — reason` behaviour |
| Vision prompt for retrieved figures | `rag_tools.py` → `_run_vision_analysis` | what the vision model is asked to read off diagrams |
| Relevance grading prompt (off by default) | `rag_tools.py` → `_grade_passage` | |
| What the model believes each tool does | `tool_schemas.py` | descriptions and parameter hints |
| Sampling | `.env` `TEMPERATURE` (chat model, per request); `config.ini` `temp` (everything else) | |

The prompts are plain markdown/text; keep the section names in
`System_prompt.md` (STATE 0–4) because the skills refer to them.

### The knobs, in order of impact

1. **`COMPLETENESS_MODE` / `COMPLETENESS_PASS_ENABLED`** — the difference between
   "three example rows" and "every row". `pre_draft` (production) is denser and
   one main-model pass; `post_draft` is two passes with a reconciliation step.
2. **`RERANK_TOP_K`** (6) — more passages = more recall, more context, slower.
   Raise `RERANK_CANDIDATE_K` with it.
3. **`VECTOR_TOP_K`** (15 per table) — only matters when a product spans many
   pages on the asked attribute.
4. **`RERANK_DOC_MAX_CHARS`** (1000) — halving it halved rerank time with no
   measurable ranking loss; the signal is in a passage's opening.
5. **`FIGURE_TEXT_MAX_CHARS`** (120) and **`KEEP_RECENT_TOOL_RESULTS`** (−1 = keep
   all) — context-size control. If you see `n_ctx` overflows, lower `RERANK_TOP_K`
   first, then set `KEEP_RECENT_TOOL_RESULTS=4` (only sensible with `post_draft`).
6. **`GRADING_ENABLED`** — leave off; it is a precision filter and drops relevant
   type pages.
7. **`reasoning-budget`** (`config.ini`, chat model) — 8192 stops runaway thinking.
   Do not remove it.

### How to measure

```bash
python run_prompts.py                                  # all examples/*.json
python run_prompts.py my_questions.jsonl --timeout 900  # one {"prompt","language"} per line
```

Each run writes `results_<timestamp>.json` with the full response (answer,
sources, trace, metrics) and prints latency per call kind (`agent`, `vision`,
`completeness_enum`, `completeness_merge`, `embed`, `rerank`). Compare a run
before and after a change. The production record is the chat log:

```sql
SELECT created_at, language, latency_ms, llm_calls, tool_calls, total_tokens, left(prompt, 60)
FROM hiwin_cs_db.chat_logs ORDER BY created_at DESC LIMIT 50;
```

The demo page's **Debug view** is the fastest way to see *why* an answer came out
as it did: which tables and language were searched, what each search returned,
and per passage whether the completeness enumeration found rows or answered
`NONE — <reason>` (a wrong reason means over-filtering).

## 3. Changing the knowledge base

### How documents are stored

One row per PDF page in `hiwin_rag.data_<product>`; `metadata_.file_name` is the
document id `<Product>_<sub_folder>_<lang>` (e.g. `Ballscrew_ballscrew-(c)_tc`),
`page_number` the page, `language_code` the language, `version` the ingest
generation. Re-ingesting a document deletes its old rows and inserts the new
ones with `version + 1`. Figures/tables referenced by the text live under
`IMAGE_TARGET_ROOT/<product>/<sub_folder>/<lang>/Figures|Tables/`.

### Adding a new PDF

1. Copy it to `<ROOT_PATH>/PDFs/<Product>/[<sub-folder>/]<file>.pdf`. A new
   `<Product>` folder = a new `data_<product>` table (created automatically).
2. Add an entry to `<ROOT_PATH>/PDF_Config.yaml` (language `en|jp|tc|sc`, pages to
   exclude such as covers). Or delete the YAML and let the pipeline regenerate
   it — but then re-check every existing entry.
3. Start the router with room for the pipeline models (`MODELS_MAX=6` in the
   launcher, or run the pipeline while the backend is idle).
4. Run the pipeline (`cd pipeline && python gui.py` / `tui.py` / `Pipeline.py`).
   Already-completed documents are skipped via `checkpoint.json`; only the new
   one is processed and ingested.
5. `python inspect_metadata.py` — the new document's rows appear; ask the agent
   a question about it. `db_get_available_product_tables` picks up new tables
   automatically.

### Re-ingesting (updating) a document

- **First read [INGESTION_DEFECTS.md](INGESTION_DEFECTS.md).** The ballscrew
  catalogue pages 12, 13 and 43 (tc) and page 12 (en) were repaired by hand in the
  database. Re-ingesting `Ballscrew` restores the defective transcription; you
  would have to re-apply the repairs (the pre-repair snapshots are the
  `hiwin_rag.repair_backup_*` tables; the repaired rows are the current ones —
  copy them aside before re-ingesting).
- To force a re-run, remove the document's passes from
  `<ROOT_PATH>/checkpoint.json` (or the whole entry) and run the pipeline; to
  re-embed only, `python -m ingestion.Ingest --force` (needs `Process_Files`).
- A newer catalogue edition: replace the PDF (same name) and re-run; or keep
  both under different names and remove the old rows (below).

### Removing a document

```sql
DELETE FROM hiwin_rag.data_ballscrew WHERE metadata_->>'file_name' = 'Ballscrew_ballscrew-(c)_tc';
```

Then delete its `checkpoint.json` entry so a later pipeline run does not
consider it done, and remove its figure folder if you want.

### Adding a language

Add `prompts/skills/<lang>_support.md`, map the code in `config.py`
`LANGUAGE_SKILL_MAP`, use that code in `PDF_Config.yaml`, ingest. The frontend's
language `<select>` is a hard-coded list.

### Restoring the figures

The backend serves `IMAGE_STATIC_ROOT` at `/static/HIWIN` and reads the same
files for the vision step. The folder must contain
`<product>/<sub_folder>/<lang>/Figures|Tables/*.jpg` exactly as the markdown
links reference them. If it is missing (as on the current machine):

1. Extract `Final_Output.7z` (deployment folder; 4.5 GB, 57k files) so that
   `<ROOT_PATH>/Final_Output/<Product>/…` exists.
2. Make sure `settings.json` `IMAGE_TARGET_ROOT` == `.env` `IMAGE_STATIC_ROOT`.
3. `cd pipeline && python -m ingestion.Ingest --assets-only` — copies every
   `Figures/` and `Tables/` folder into place; no embedding, no DB writes.
4. Restart the backend; `python doctor.py` should show `[PASS] IMAGE_STATIC_ROOT`,
   and answers start carrying images and vision analysis.

Alternatively copy the folder from a machine that has it.

## 4. Backups

- **Database** (the expensive part): `pg_dump` as in the README
  ([§9](../README.md#backups-and-moving-the-database)). Schedule it; keep the
  custom-format dump next to the GGUFs.
- **Figures**: the `IMAGE_STATIC_ROOT` folder, or the `Final_Output.7z` archive.
- **Inputs**: `PDFs/`, `PDF_Config.yaml`, the ONNX model — needed only to rebuild.
- **Config**: this repository (`.env`, `settings.json`, `config.ini` are committed).
- The `bak_*` / `repair_backup_*` / `link_backup_*` tables in `hiwin_rag` are
  historic snapshots from manual repairs (2026-08/09). They are not read by the
  application; keep them until the corresponding fixes land in the pipeline.

## 5. Updating models or llama.cpp

- **New llama.cpp release:** replace the binary, restart, `python doctor.py --live`.
  Router mode (`--models-preset`) and the `/v1/rerank` endpoint must still be
  supported; if the preset format changes, follow the release notes.
- **New quantisation of the chat / aux / reranker model:** edit the `model =`
  line, restart, run `run_prompts.py` to compare. No data impact.
- **New embedding model or quantisation:** treat as a rebuild — change
  `EMBED_MODEL` / `EMBEDDING_MODEL`, set `EMBED_DIM`, drop or rename the old
  `data_*` tables, re-ingest everything. `doctor.py --live` compares the model's
  output width with the DB column and fails loudly on mismatch.
- **GPU changes:** keep the total resident set within one card if the machine is
  shared (`CUDA_VISIBLE_DEVICES` in the launcher). Check `nvidia-smi` after
  driver changes — CUDA indices can move.

## 6. Rotating the database password

```sql
ALTER USER postgres WITH PASSWORD 'new-password';
```

Then `pipeline/settings.json` → `DB_PASS`, `python env_from_settings.py`
(updates `.env`), restart the backend, commit. Both files are committed on
purpose (internal repository); rotate before the repository is ever shared
outside.

## 7. Routine checklist

- **Weekly:** `python doctor.py --live`; glance at `hiwin_cs_db.chat_logs` for
  latency spikes or errors; disk space for the DB and dumps.
- **After any config edit:** `python doctor.py --all`, restart the affected
  process (router for `config.ini`, backend for `.env`/prompts, nothing for
  prompt files).
- **After adding documents:** `inspect_metadata.py`, a test question, and a
  fresh `pg_dump`.
- **Before handing the machine to someone else:** update README §3 (deployment
  snapshot) and AGENTS.md §6.

## 8. What was changed in the past, and why

Kept here because the comments in `config.ini` / `.env` only tell part of it.

| When | Change | Why |
|---|---|---|
| 2026-07 | Router mode replaced llama-swap; all four serving models resident | no model swapping between chat / embed / rerank / aux calls |
| 2026-07 | `COMPLETENESS_MODE=pre_draft`, aux model `Support_Agent_Aux` for enumeration | exhaustive spec-table answers at acceptable latency |
| 2026-07 | `RERANK_DOC_MAX_CHARS` 2000 → 1000 | rerank prefill was ~15 s per call; halving it halved that with no ranking loss |
| 2026-08-14 | `reasoning-budget = 8192` on the chat model | three questions generated 112k–145k reasoning tokens and returned empty answers |
| 2026-08-18 | Hand repairs of ballscrew pages 12/13/43 in the DB | transcription defects (see INGESTION_DEFECTS.md); pipeline not yet fixed |
| 2026-08-26 | Pin the router to one GPU | a slice of the model landed on a card running another user's job; latency 71 s → 36 min |
| 2026-08-28 | `parallel = 1` on embedding / reranker / aux | identical requests returned different vectors across slots; rankings wobbled |
| 2026-09-11/13 | `bak_data_*` snapshots of every table | before further batch repairs |
| 2026-09-16 | Handoff audit: `doctor.py`, `setup_db.py`, in-place `env_from_settings.py`, `--assets-only`, `EMBED_MODEL` setting, `.gitattributes`, headless OpenCV, docs rewrite | make the project rebuildable by someone new on Windows or Linux |
| 2026-09-16 | Newcomer rebuild simulation from a fresh clone (2-page PDF → `hiwin_rag_sim`): launchers now find Python when it is not on PATH, set `CUDA_DEVICE_ORDER=PCI_BUS_ID`, wait for models to finish loading; UTF-8 console output in the pipeline (the final emoji print crashed on cp950); `import pymupdf`; doctor warns on evicted/loading models | every step of Path C was exercised and each stumble fixed |
