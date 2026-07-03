"""Postgres access for the HIWIN RAG store.

The SQL is kept base64-encoded exactly as in the open-WebUI tool so the queries
are byte-for-byte identical; the decoded form is shown in a comment above each
constant for readability.
"""

import base64
import re

import psycopg2
from psycopg2.extras import Json

import config


# Postgres identifier guard for the (config-supplied) chat-log schema/table names.
_IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


# SELECT table_name FROM information_schema.tables
#   WHERE table_schema = %s AND table_name LIKE 'data_%%';
SQL_LIST_DATA_TABLES = base64.b64decode(
    "U0VMRUNUIHRhYmxlX25hbWUgRlJPTSBpbmZvcm1hdGlvbl9zY2hlbWEudGFibGVzIFdIRVJFIHRhYmxlX3NjaGVtYSA9ICVzIEFORCB0YWJsZV9uYW1lIExJS0UgJ2RhdGFfJSUnOw=="
).decode()

# SELECT table_name FROM information_schema.tables
#   WHERE table_schema = %s AND table_name = %s;
SQL_TABLE_EXISTS = base64.b64decode(
    "U0VMRUNUIHRhYmxlX25hbWUgRlJPTSBpbmZvcm1hdGlvbl9zY2hlbWEudGFibGVzIFdIRVJFIHRhYmxlX3NjaGVtYSA9ICVzIEFORCB0YWJsZV9uYW1lID0gJXM7"
).decode()

# SELECT {content}, {embedding} <=> %s::vector AS distance
#   FROM {schema}.{table}
#   WHERE metadata_->>'language_code' = %s
#   ORDER BY distance ASC LIMIT {limit};
SQL_VECTOR_SEARCH = base64.b64decode(
    "U0VMRUNUIHtjb250ZW50fSwge2VtYmVkZGluZ30gPD0+ICVzOjp2ZWN0b3IgQVMgZGlzdGFuY2UgRlJPTSB7c2NoZW1hfS57dGFibGV9IFdIRVJFIG1ldGFkYXRhXy0+PidsYW5ndWFnZV9jb2RlJyA9ICVzIE9SREVSIEJZIGRpc3RhbmNlIEFTQyBMSU1JVCB7bGltaXR9Ow=="
).decode()

# SELECT {content}, 0.0 AS distance FROM {schema}.{table}
#   WHERE metadata_->>'language_code' = %s;
SQL_VECTOR_BYPASS = base64.b64decode(
    "U0VMRUNUIHtjb250ZW50fSwgMC4wIEFTIGRpc3RhbmNlIEZST00ge3NjaGVtYX0ue3RhYmxlfSBXSEVSRSBtZXRhZGF0YV8tPj4nbGFuZ3VhZ2VfY29kZScgPSAlczs="
).decode()

# SELECT * FROM {schema}.{table}
SQL_FETCH_ALL = base64.b64decode("U0VMRUNUICogRlJPTSB7c2NoZW1hfS57dGFibGV9").decode()

# Metadata-inclusive variants of the vector search — same as the base64 queries
# above but they also return the `metadata_` jsonb column (for structured
# citations). Plain text since they're an extension, not part of the original
# tool. Row shape: (content, metadata, distance).
SQL_VECTOR_SEARCH_META = (
    "SELECT {content}, metadata_, {embedding} <=> %s::vector AS distance "
    "FROM {schema}.{table} WHERE metadata_->>'language_code' = %s "
    "ORDER BY distance ASC LIMIT {limit};"
)
SQL_VECTOR_BYPASS_META = (
    "SELECT {content}, metadata_, 0.0 AS distance "
    "FROM {schema}.{table} WHERE metadata_->>'language_code' = %s;"
)


def get_connection():
    return psycopg2.connect(
        dbname=config.DB_NAME,
        user=config.DB_USER,
        password=config.DB_PASSWORD,
        host=config.DB_HOST,
        port=config.DB_PORT,
        sslmode=config.DB_SSLMODE,
    )


def fetch_existing_tables():
    """Return the list of real data_* tables, or [] on failure. Blocking."""
    conn = None
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(SQL_LIST_DATA_TABLES, (config.DB_SCHEMA,))
            return [row[0] for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        if conn is not None:
            conn.close()


def resolve_tables(requested: list, existing: list):
    """Map model-supplied product_table values onto real table names.

    The model frequently passes near-miss names ('linear_guideway' instead of
    'data_linear_guideway', wrong casing, a series name, etc.). Strict equality
    rejects those and forces a blind retry loop that burns the token budget.
    This resolves each requested name against the real `existing` tables, in
    order of preference:
      1. exact match
      2. case-insensitive, with/without the 'data_' prefix
      3. alphanumeric fold (drops '_', spaces, casing) e.g. 'Ball Screw' -> 'data_ballscrew'

    Returns (resolved, unmatched). `resolved` is order-preserving and de-duplicated.
    """

    def fold(s: str) -> str:
        s = s.lower()
        if s.startswith("data_"):
            s = s[len("data_") :]
        return re.sub(r"[^a-z0-9]", "", s)

    folded_existing = {}
    for t in existing:
        folded_existing.setdefault(fold(t), t)

    existing_set = set(existing)
    existing_ci = {t.lower(): t for t in existing}

    resolved, unmatched = [], []
    for p in requested:
        if not isinstance(p, str) or not p.strip():
            continue
        cand = p.strip()
        if cand in existing_set:
            resolved.append(cand)
            continue
        ci = cand.lower()
        if ci in existing_ci:
            resolved.append(existing_ci[ci])
            continue
        if ("data_" + ci) in existing_ci:
            resolved.append(existing_ci["data_" + ci])
            continue
        f = fold(cand)
        if f in folded_existing:
            resolved.append(folded_existing[f])
            continue
        unmatched.append(p)

    seen = set()
    resolved = [t for t in resolved if not (t in seen or seen.add(t))]
    return resolved, unmatched


# --------------------------------------------------------------------------- #
# Chat logging
# --------------------------------------------------------------------------- #

def _chat_log_target() -> tuple[str, str]:
    """Return (schema, table) for the chat log, validated as safe identifiers."""
    schema, table = config.CHAT_LOG_SCHEMA, config.CHAT_LOG_TABLE
    if not _IDENT_RE.match(schema) or not _IDENT_RE.match(table):
        raise ValueError(f"Invalid chat-log schema/table name: {schema}.{table}")
    return schema, table


def _ensure_chat_log_table(cur, schema: str, table: str) -> None:
    """Create the chat-log schema + table if they don't exist (idempotent)."""
    cur.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.{table} (
            id                BIGSERIAL PRIMARY KEY,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            language          TEXT,
            prompt            TEXT,
            response          TEXT,
            sources           JSONB,
            trace             JSONB,
            latency_ms        DOUBLE PRECISION,
            llm_calls         INTEGER,
            agent_iterations  INTEGER,
            tool_calls        INTEGER,
            prompt_tokens     INTEGER,
            completion_tokens INTEGER,
            total_tokens      INTEGER,
            generations       JSONB
        )
        """
    )


def log_chat(prompt, language, response, sources, trace, metrics, latency_ms) -> bool:
    """Best-effort insert of one /chat exchange into the chat-log table.

    Creates the schema/table on first use. Never raises — a logging failure must
    not break serving, so all errors are swallowed (and printed) and False
    returned. `metrics` is the dict populated by agent.run().
    """
    metrics = metrics or {}
    conn = None
    try:
        schema, table = _chat_log_target()
        conn = get_connection()
        with conn:                       # commits on success, rolls back on error
            with conn.cursor() as cur:
                _ensure_chat_log_table(cur, schema, table)
                cur.execute(
                    f"""
                    INSERT INTO {schema}.{table}
                      (language, prompt, response, sources, trace, latency_ms,
                       llm_calls, agent_iterations, tool_calls,
                       prompt_tokens, completion_tokens, total_tokens, generations)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        language, prompt, response,
                        Json(sources or []), Json(trace or []), latency_ms,
                        metrics.get("llm_calls"), metrics.get("agent_iterations"),
                        metrics.get("tool_calls"), metrics.get("prompt_tokens"),
                        metrics.get("completion_tokens"), metrics.get("total_tokens"),
                        Json(metrics.get("generations") or []),
                    ),
                )
        return True
    except Exception as e:               # noqa: BLE001 — best-effort by design
        print(f"WARNING: chat log write failed: {e}")
        return False
    finally:
        if conn is not None:
            conn.close()
