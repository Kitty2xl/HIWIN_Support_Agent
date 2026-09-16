"""Central configuration for the HIWIN Support Agent Backend.

Values are read from environment variables (a local `.env` file is loaded
automatically if present). The defaults are safe for running ON the model/DB
server itself; secrets (DB password) and machine-specific paths
(IMAGE_STATIC_ROOT) have NO default and must be supplied via `.env`.

The repo ships a pre-filled `.env` (internal deployment); edit IMAGE_STATIC_ROOT
(and anything your setup differs on) before running.
"""

import os

# Load a local .env file if python-dotenv is installed (optional dependency).
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(BASE_DIR, ".env"))
except ImportError:
    pass


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _bool_env(key: str, default: str) -> bool:
    return _env(key, default).strip().lower() in ("1", "true", "yes", "on")


# --- Inference server (llama.cpp router mode, OpenAI-compatible) ---
# One endpoint serves every model; requests route by the model name below.
# Default assumes the backend runs ON the model/DB server, so localhost works.
# To run from a remote dev machine, set INFERENCE_HOST=http://<server-ip>:11400
INFERENCE_HOST = _env("INFERENCE_HOST", "http://localhost:11400")
INFERENCE_BASE_URL = _env("INFERENCE_BASE_URL", f"{INFERENCE_HOST}/v1")
RERANK_URL = _env("RERANK_URL", f"{INFERENCE_HOST}/v1/rerank")
LANGUAGE_MODEL = _env("LANGUAGE_MODEL", "Support_Agent_Qwen3.6")
EMBEDDING_MODEL = _env("EMBEDDING_MODEL", "Embedding_Qwen3.6")
RERANKER_MODEL = _env("RERANKER_MODEL", "Reranker_Qwen3.6")
# Per-passage char budget sent to the reranker for SCORING only (the full
# passage still goes to the LLM). Keeps query+doc under the reranker's physical
# batch size (--ubatch-size); llama.cpp returns 500 on oversized input.
RERANK_DOC_MAX_CHARS = int(_env("RERANK_DOC_MAX_CHARS", "2000"))
CHAT_TIMEOUT = int(_env("CHAT_TIMEOUT", "120"))

# --- Retrieval caps (how many passages flow through each stage) ---
# Vector search pulls VECTOR_TOP_K per table; the pooled best RERANK_CANDIDATE_K
# are sent to the reranker, which keeps RERANK_TOP_K. If you raise RERANK_TOP_K,
# raise RERANK_CANDIDATE_K to match so the reranker has enough to choose from.
VECTOR_TOP_K       = int(_env("VECTOR_TOP_K", "15"))       # per-table SQL LIMIT
RERANK_CANDIDATE_K = int(_env("RERANK_CANDIDATE_K", "20"))  # passages fed to reranker
RERANK_TOP_K       = int(_env("RERANK_TOP_K", "6"))        # passages kept (manuals)
CERT_RERANK_TOP_K  = int(_env("CERT_RERANK_TOP_K", "10"))  # passages kept (certifications)
# When a manual search spans several product_tables, their vector searches run in
# parallel, bounded by this many concurrent DB queries (each borrows a pooled
# connection — keep it <= DB_POOL_MAX).
DB_SEARCH_CONCURRENCY = int(_env("DB_SEARCH_CONCURRENCY", "8"))

# --- Context control (keep the running conversation from overflowing n_ctx) ---
# Verbose auto-generated figure descriptions are truncated to this many chars
# before a passage is sent to the model — they duplicate across image alt-text +
# caption and dominate passage size, while the spec tables (the real data) are
# untouched. -1 disables; 0 leaves only a short placeholder.
FIGURE_TEXT_MAX_CHARS = int(_env("FIGURE_TEXT_MAX_CHARS", "120"))
# Keep only the most recent N tool results in full in the running conversation;
# older ones are stubbed so many-search queries don't pile every passage into
# context. The full passages are still handled by the completeness pass, so the
# final answer loses nothing. -1 disables trimming; 0 stubs all prior results.
KEEP_RECENT_TOOL_RESULTS = int(_env("KEEP_RECENT_TOOL_RESULTS", "4"))
# Deterministic decoding for the chat/vision model — minimizes numeric drift
# when the model transcribes values out of dense spec tables.
TEMPERATURE = float(_env("TEMPERATURE", "0"))
EMBED_TIMEOUT = int(_env("EMBED_TIMEOUT", "30"))

# --- Postgres (pgvector) ---
DB_NAME = _env("DB_NAME", "hiwin_rag_db")
DB_USER = _env("DB_USER", "postgres")
DB_PASSWORD = _env("DB_PASSWORD", "")  # REQUIRED — set in .env, never commit
DB_HOST = _env("DB_HOST", "localhost")  # backend runs on the DB server; override for remote dev
DB_PORT = _env("DB_PORT", "5432")
DB_SCHEMA = _env("DB_SCHEMA", "hiwin_rag")
DB_SSLMODE = _env("DB_SSLMODE", "prefer")  # try "disable" or "require" to debug remote handshakes
CONTENT_COLUMN = _env("CONTENT_COLUMN", "text")
EMBEDDING_COLUMN = _env("EMBEDDING_COLUMN", "embedding")
# Max connections in the shared pool. Sized for parallel table searches ×
# parallel tool calls; raise if you see the pool block under heavy concurrency.
DB_POOL_MAX = int(_env("DB_POOL_MAX", "12"))

# --- Images / vision ---
# Filesystem path to the HIWIN static image folder (served at /static/HIWIN and
# read by the vision step). REQUIRED for images — set in .env.
IMAGE_STATIC_ROOT = _env("IMAGE_STATIC_ROOT", "")
VISION_MAX_IMAGES = int(_env("VISION_MAX_IMAGES", "4"))

# --- Agent / routing ---
MAX_AGENT_ITERS = int(_env("MAX_AGENT_ITERS", "8"))
DEFAULT_LANGUAGE = _env("DEFAULT_LANGUAGE", "tc")  # per System_prompt STATE 0
# When the model emits several tool calls in one turn (e.g. multiple keyword
# searches), run them concurrently instead of one-by-one. Set false to serialize
# if your inference server / DB can't handle the concurrent load.
PARALLEL_TOOL_CALLS = _bool_env("PARALLEL_TOOL_CALLS", "true")
# Max chars of each tool result kept in the trace (debug panel + chat log).
# 0 = no truncation. Raise this to inspect full retrieved passages when
# debugging recall; keep it modest in production to bound chat-log row size.
TRACE_RESULT_MAX_CHARS = int(_env("TRACE_RESULT_MAX_CHARS", "600"))


# --- Relevance grading (per-passage "does this help?" second pass) ---
# After reranking, each surviving passage is graded YES/NO by the LLM and the
# NOs are dropped. Costs one LLM call per passage (run concurrently). Default
# OFF: this is a PRECISION filter, which conflicts with the exhaustive-recall
# design (it can drop relevant type-pages and starve the completeness pass).
# Enable only if noisy retrieval is a bigger problem than missing results.
GRADING_ENABLED       = _bool_env("GRADING_ENABLED", "false")
GRADING_DOC_MAX_CHARS = int(_env("GRADING_DOC_MAX_CHARS", "2000"))
GRADING_TIMEOUT       = int(_env("GRADING_TIMEOUT", "30"))


# --- Completeness pass (focused per-table enumeration) ---
# After the draft answer, each retrieved passage is re-read in ISOLATION to
# extract every entry matching the user's request, then the draft is rewritten
# to include them all. This fights the model's tendency to summarize long spec
# tables down to a few representative rows. Format-agnostic (no column-schema
# assumptions). Costs one LLM call per unique passage + one to reconcile; set
# COMPLETENESS_PASS_ENABLED=false if latency matters more than exhaustiveness.
COMPLETENESS_PASS_ENABLED = _bool_env("COMPLETENESS_PASS_ENABLED", "true")
COMPLETENESS_TIMEOUT      = int(_env("COMPLETENESS_TIMEOUT", "120"))
# Max enumeration calls in flight at once. The local inference server processes
# large requests roughly serially, so firing every passage concurrently makes
# them queue past their timeout — keep this near the server's real parallel slot
# count (1 for a single-GPU llama.cpp with --parallel 1). Raise it if your server
# can genuinely handle more at once; lowering trades latency for reliability.
COMPLETENESS_CONCURRENCY  = int(_env("COMPLETENESS_CONCURRENCY", "1"))
# Debugging aid: when a passage matches nothing, have the enumeration append a
# one-line reason (what the passage actually contains) instead of a bare NONE,
# so false negatives can be told apart from genuine no-matches in the trace.
COMPLETENESS_EXPLAIN_NONE = _bool_env("COMPLETENESS_EXPLAIN_NONE", "true")
# The per-passage enumeration is a narrow, mechanical task, so it can run on a
# SMALLER, faster model than LANGUAGE_MODEL. With the router keeping both models
# resident behind one endpoint, point this at the small model (e.g.
# Support_Agent_Aux) — then COMPLETENESS_CONCURRENCY can safely go above 1.
# Reconciliation stays on LANGUAGE_MODEL. COMPLETENESS_BASE_URL only needs setting
# if you serve the small model at a different endpoint; it defaults to the main one.
COMPLETENESS_MODEL    = _env("COMPLETENESS_MODEL", LANGUAGE_MODEL)
COMPLETENESS_BASE_URL = _env("COMPLETENESS_BASE_URL", INFERENCE_BASE_URL)
# WHERE the enumeration runs relative to the main draft:
#   post_draft (default) — draft first, then re-read all passages and reconcile
#     (two main-LLM passes; the raw passages are a safety net for the reconcile).
#   pre_draft — extract matching rows from each reranked passage BEFORE the draft
#     and append them to the tool result, so the model drafts ONCE from the raw
#     passages + the extracted rows (no separate reconciliation). Denser context,
#     one main-LLM pass. Best paired with KEEP_RECENT_TOOL_RESULTS=-1 so the
#     extracted rows aren't trimmed out of context before the draft.
COMPLETENESS_MODE     = _env("COMPLETENESS_MODE", "post_draft")


# --- Serving a separate frontend (API-first deployment) ---
# Browsers block cross-origin calls unless the backend sends CORS headers. A
# comma-separated list of allowed origins, e.g. "http://10.0.0.5:3000,https://support.example.com";
# "*" allows any origin (fine on an internal network); empty string disables CORS.
CORS_ALLOW_ORIGINS = _env("CORS_ALLOW_ORIGINS", "*")
# Answers contain root-relative image links (/static/HIWIN/...) and certificate
# web_paths that only resolve when the page is served by THIS backend. Set this to
# the URL clients reach the backend at (e.g. http://10.0.0.5:8079) and every such
# link in /chat responses is rewritten to an absolute URL. Empty = leave as-is.
PUBLIC_BASE_URL = _env("PUBLIC_BASE_URL", "").rstrip("/")

# --- Chat logging ---
# Persist each /chat request (prompt, answer, sources, trace, timing & token
# metrics) to a table. Stored in a SEPARATE schema in the same database, so it's
# isolated from the RAG data. Best-effort: a logging failure never breaks /chat.
CHAT_LOG_ENABLED = _bool_env("CHAT_LOG_ENABLED", "true")
CHAT_LOG_SCHEMA = _env("CHAT_LOG_SCHEMA", "hiwin_cs_db")
CHAT_LOG_TABLE = _env("CHAT_LOG_TABLE", "chat_logs")

# --- Prompt assets (resolved relative to this file, so cwd doesn't matter) ---
SYSTEM_PROMPT_PATH = _env(
    "SYSTEM_PROMPT_PATH", os.path.join(BASE_DIR, "prompts", "System_prompt.md")
)

# Language code -> skill markdown file. Keys MUST match the
# `metadata_->>'language_code'` values stored in the DB: en / jp / tc / sc.
_SKILLS_DIR = os.path.join(BASE_DIR, "prompts", "skills")
LANGUAGE_SKILL_MAP = {
    "en": os.path.join(_SKILLS_DIR, "english_support.md"),
    "jp": os.path.join(_SKILLS_DIR, "japanese_support.md"),
    "tc": os.path.join(_SKILLS_DIR, "traditional_chinese_support.md"),
    "sc": os.path.join(_SKILLS_DIR, "simplified_chinese_support.md"),
}
