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


# --- Inference server (llama.cpp, OpenAI-compatible) ---
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

# --- Images / vision ---
# Filesystem path to the HIWIN static image folder (served at /static/HIWIN and
# read by the vision step). REQUIRED for images — set in .env.
IMAGE_STATIC_ROOT = _env("IMAGE_STATIC_ROOT", "")
VISION_MAX_IMAGES = int(_env("VISION_MAX_IMAGES", "4"))

# --- Agent / routing ---
MAX_AGENT_ITERS = int(_env("MAX_AGENT_ITERS", "8"))
DEFAULT_LANGUAGE = _env("DEFAULT_LANGUAGE", "tc")  # per System_prompt STATE 0


def _bool_env(key: str, default: str) -> bool:
    return _env(key, default).strip().lower() in ("1", "true", "yes", "on")


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
# `metadata_->>'language_code'` values stored in the DB: en / jp / tc.
_SKILLS_DIR = os.path.join(BASE_DIR, "prompts", "skills")
LANGUAGE_SKILL_MAP = {
    "en": os.path.join(_SKILLS_DIR, "english_support.md"),
    "jp": os.path.join(_SKILLS_DIR, "japanese_support.md"),
    "tc": os.path.join(_SKILLS_DIR, "traditional_chinese_support.md"),
}
