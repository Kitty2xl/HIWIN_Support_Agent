"""The four RAG tools, ported from the open-WebUI `Tools` class.

Ported from the open-WebUI `Tools` class: the `search_query:` embedding prefix,
per-table vector search, `metadata_->>'language_code'` filter, English fallback,
reranking, vision analysis of retrieved diagrams, and forgiving table-name
resolution. Retrieval caps (vector LIMIT, rerank candidate pool, top-K) are now
config-driven; the reranker's relevance_score is surfaced on each source; and an
optional per-passage LLM relevance grade drops unhelpful passages after rerank.
The open-WebUI `__event_emitter__` status calls and the `Tools`/`Valves` wrapper
are removed; configuration now comes from `config`, DB access from `db`, and
inference from `inference`.

Retrieval tools return a {"text", "sources"} dict (text for the LLM, sources for
structured citations); the other tools return a plain string.
"""

import asyncio
import base64
import json
import os
import re
from urllib.parse import quote

import db
import config
import inference


# --- Image path helpers -----------------------------------------------------

def _encode_image_paths(text: str) -> str:
    def encode_match(m):
        alt = m.group(1)
        path = m.group(2)
        encoded = quote(path, safe="/:")
        return f"![{alt}]({encoded})"

    return re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", encode_match, text)


def _extract_image_static_paths(passages: list) -> list:
    """Return unique /static/HIWIN/... paths found across all passages, in order."""
    seen = set()
    paths = []
    for passage in passages:
        for m in re.finditer(r"!\[[^\]]*\]\((/static/HIWIN/[^)]+)\)", passage):
            p = m.group(1)
            if p not in seen:
                seen.add(p)
                paths.append(p)
    return paths


def _static_to_filesystem(static_path: str) -> str:
    """Map /static/HIWIN/a/b.jpg -> IMAGE_STATIC_ROOT\\a\\b.jpg."""
    relative = re.sub(r"^/static/HIWIN/", "", static_path)
    return os.path.join(config.IMAGE_STATIC_ROOT, *relative.split("/"))


def _load_image_b64(fs_path: str):
    """Load an image and return (mime_type, base64_string), or None on failure."""
    try:
        ext = os.path.splitext(fs_path)[1].lower().lstrip(".")
        mime = "jpeg" if ext in ("jpg", "jpeg") else ext
        with open(fs_path, "rb") as f:
            return mime, base64.b64encode(f.read()).decode()
    except Exception:
        return None


async def _run_vision_analysis(query: str, passages: list) -> str:
    """Extract images from retrieved passages, send them to the vision LLM with
    the query, and return a focused analysis. Empty string if none / on failure.
    """
    static_paths = _extract_image_static_paths(passages)[: config.VISION_MAX_IMAGES]
    if not static_paths:
        return ""

    content = []
    for sp in static_paths:
        result = _load_image_b64(_static_to_filesystem(sp))
        if result:
            mime, b64 = result
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/{mime};base64,{b64}"},
                }
            )

    if not content:
        return ""

    content.append(
        {
            "type": "text",
            "text": (
                f"The above image(s) were retrieved from technical documents in response "
                f'to the query: "{query}". '
                f"For each image, describe the technical information it contains that is "
                f"relevant to the query. Focus on visible dimensions, specifications, "
                f"part numbers, labels, and any data readable directly from the diagram."
            ),
        }
    )

    try:
        return await asyncio.to_thread(
            inference.chat_content,
            [{"role": "user", "content": content}],
            config.LANGUAGE_MODEL,
            120,  # vision calls need more time than text-only
        )
    except Exception as e:
        print(f"Vision analysis failed: {e}")
        return ""


# Clean metadata fields worth surfacing as a citation (drops LlamaIndex noise
# like `_node_content`, `doc_id`, etc.).
_SOURCE_KEYS = (
    "product_type",
    "page_number",
    "file_name",
    "sub_folder",
    "language_code",
    "web_path",
    "source_md",
)


def _source_from_meta(meta, score=None) -> dict:
    """Whitelist the citation-relevant fields out of a chunk's metadata_,
    optionally tagging the reranker's relevance score for tuning/inspection."""
    if not isinstance(meta, dict):
        return {}
    src = {k: meta[k] for k in _SOURCE_KEYS if meta.get(k) is not None}
    if src and score is not None:
        src["rerank_score"] = round(float(score), 4)
    return src


def _rerank_items(query: str, items: list, top_n: int) -> list:
    """Rerank (text, meta) pairs, returning the top-n as (text, meta, score) triples.

    Truncate each passage for SCORING ONLY — rerankers have a context limit and
    llama.cpp returns 500 on oversized input. The ranked indices map back to the
    original full (text, meta) pairs, so nothing is lost downstream. The reranker's
    relevance_score is carried through so callers can surface / threshold on it.
    """
    if not items:
        return []
    try:
        budget = config.RERANK_DOC_MAX_CHARS
        scoring_docs = [text[:budget] for text, _ in items]
        rerank_data = inference.rerank(query, scoring_docs, top_n)
        if "results" in rerank_data:
            return [
                (items[res["index"]][0], items[res["index"]][1], res.get("relevance_score"))
                for res in rerank_data["results"]
            ]
    except Exception as e:
        print(f"Reranking failed: {e}")
    # Fallback: reranker unavailable — keep original order, score unknown.
    return [(text, meta, None) for text, meta in items[:top_n]]


async def _grade_passage(query: str, text: str) -> bool:
    """Ask the LLM whether a single passage helps answer the query (YES/NO).

    Fail-open: if the grading call errors, keep the passage rather than silently
    dropping a possibly-relevant result.
    """
    messages = [{
        "role": "user",
        "content": (
            "You are judging whether a retrieved passage helps answer a user's "
            "question. Reply with exactly one word: YES or NO.\n\n"
            f"Question: {query}\n\n"
            f"Passage:\n{text[: config.GRADING_DOC_MAX_CHARS]}\n\n"
            "Does this passage contain information that helps answer the question?"
        ),
    }]
    try:
        answer = await asyncio.to_thread(
            inference.chat_content, messages, config.LANGUAGE_MODEL, config.GRADING_TIMEOUT
        )
        return answer.strip().upper().startswith("Y")
    except Exception as e:
        print(f"Grading failed (keeping passage): {e}")
        return True


async def _grade_items(query: str, items: list) -> list:
    """Drop passages the LLM judges unhelpful. Grades run concurrently.

    `items` are (text, meta, score) triples. If grading rejects everything, fall
    back to the single best passage — a total wipe is more likely an over-strict
    sweep than a genuinely empty result.
    """
    if not config.GRADING_ENABLED or not items:
        return items
    verdicts = await asyncio.gather(*(_grade_passage(query, it[0]) for it in items))
    kept = [it for it, keep in zip(items, verdicts) if keep]
    if not kept:
        print("Grading rejected all passages; falling back to top-1.")
        return items[:1]
    return kept


# --- TOOL 1: MANUAL SEARCH --------------------------------------------------

async def db_search_technical_manuals(
    query: str,
    language_code: str,
    product_table: list,
) -> str:
    try:
        initial_top_K = config.VECTOR_TOP_K  # per-table vector-search LIMIT
        rerank_candidate_K = config.RERANK_CANDIDATE_K  # passages fed into the reranker
        final_top_K = config.RERANK_TOP_K  # passages returned after reranking

        query_vector = await asyncio.to_thread(
            inference.embed, f"search_query: {query}", config.EMBEDDING_MODEL
        )

        if not product_table:
            return (
                "Invalid Product Table. Call db_get_available_product_tables first to retrieve "
                "valid product tables, then retry with a product_table value."
            )

        def fetch_from_db(lang: str):
            conn = None
            results = []
            try:
                conn = db.get_connection()
                with conn.cursor() as cur:
                    cur.execute(db.SQL_LIST_DATA_TABLES, (config.DB_SCHEMA,))
                    existing_tables = [row[0] for row in cur.fetchall()]

                    # Forgiving resolution: map the model's guesses onto real
                    # table names (case / prefix / punctuation tolerant).
                    table_list, unmatched = db.resolve_tables(
                        product_table, existing_tables
                    )
                    if unmatched:
                        print(f"Unmatched product_table values ignored: {unmatched}")

                    # Bypass table: match by RESOLVED name against the real table,
                    # whatever its exact spelling.
                    is_bypass_table = {
                        t
                        for t in table_list
                        if t.lower() in ("data_all_products", "data_allproducts")
                    }

                    # Sentinel: distinguish "no tables matched" from "matched but empty".
                    if not table_list:
                        return None

                    for table_name in table_list:
                        try:
                            if table_name in is_bypass_table:
                                sql = db.SQL_VECTOR_BYPASS_META.format(
                                    content=config.CONTENT_COLUMN,
                                    schema=config.DB_SCHEMA,
                                    table=table_name,
                                )
                                cur.execute(sql, (lang,))
                                results.extend(cur.fetchall())
                            else:
                                sql = db.SQL_VECTOR_SEARCH_META.format(
                                    content=config.CONTENT_COLUMN,
                                    embedding=config.EMBEDDING_COLUMN,
                                    schema=config.DB_SCHEMA,
                                    table=table_name,
                                    limit=initial_top_K,
                                )
                                cur.execute(sql, (json.dumps(query_vector), lang))
                                results.extend(cur.fetchall())
                        except Exception as table_err:
                            print(f"Error querying table {table_name}: {table_err}")
                            conn.rollback()
            except Exception as db_err:
                print(f"Database connection error: {db_err}")
                raise
            finally:
                if conn is not None:
                    conn.close()
            return results

        all_results = await asyncio.to_thread(fetch_from_db, language_code)

        if all_results is None:
            available = await asyncio.to_thread(db.fetch_existing_tables)
            listing = ", ".join(available) if available else "(none found)"
            return (
                f"None of {product_table} matched a real table. "
                f"Choose EXACTLY ONE OR MORE from this list and call again: {listing}"
            )

        if not all_results:
            if language_code != "en":
                all_results = await asyncio.to_thread(fetch_from_db, "en")
                if not all_results:
                    return (
                        f"No results found for language '{language_code}' or 'en' "
                        f"in the available tables for products: {product_table}."
                    )
            else:
                return (
                    f"No results found in the available tables. "
                    f"You MUST retry using ONLY these exact product table: {product_table}. "
                    f"Do not attempt to use any other product names."
                )

        all_results.sort(key=lambda x: x[2])  # distance is now the 3rd column
        candidate_items = [(r[0], r[1]) for r in all_results[:rerank_candidate_K]]

        reranked = await asyncio.to_thread(
            _rerank_items, query, candidate_items, final_top_K
        )
        reranked = await _grade_items(query, reranked)

        passages = [text for text, _, _ in reranked]
        encoded = [_encode_image_paths(text) for text in passages]

        vision_analysis = await _run_vision_analysis(query, passages)
        if vision_analysis:
            encoded.append(f"[Visual Analysis of Retrieved Diagrams]\n{vision_analysis}")

        sources = [s for s in (_source_from_meta(m, score) for _, m, score in reranked) if s]
        return {"text": "\n---\n".join(encoded), "sources": sources}

    except Exception as e:
        return f"Manual Search Error: {e}"


# --- TOOL 2: GET AVAILABLE TABLES -------------------------------------------

async def db_get_available_product_tables() -> str:
    try:
        tables = await asyncio.to_thread(db.fetch_existing_tables)

        if not tables:
            return "No product tables starting with 'data_' were found in the database."

        response = "The following product tables are available to search:\n"
        for table in tables:
            product_name = table.replace("data_", "")
            response += f"- {product_name} (Table: {table})\n"
        return response

    except Exception as e:
        return f"Error: {e}"


# --- TOOL 3: SEARCH CERTIFICATIONS ------------------------------------------

async def db_search_certifications(query: str, language_code: str = "en") -> str:
    TARGET_TABLE = "data_certificates"

    try:
        query_vector = await asyncio.to_thread(
            inference.embed, f"search_query: {query}", config.EMBEDDING_MODEL
        )

        def fetch_from_db(lang: str):
            conn = None
            try:
                conn = db.get_connection()
                with conn.cursor() as cur:
                    cur.execute(db.SQL_TABLE_EXISTS, (config.DB_SCHEMA, TARGET_TABLE))
                    if cur.fetchone() is None:
                        return None  # Sentinel: table doesn't exist

                    sql = db.SQL_VECTOR_SEARCH_META.format(
                        content=config.CONTENT_COLUMN,
                        embedding=config.EMBEDDING_COLUMN,
                        schema=config.DB_SCHEMA,
                        table=TARGET_TABLE,
                        limit=50,
                    )
                    cur.execute(sql, (json.dumps(query_vector), lang))
                    return cur.fetchall()
            except Exception as e:
                print(f"Certifications DB error: {e}")
                return []
            finally:
                if conn is not None:
                    conn.close()

        all_results = await asyncio.to_thread(fetch_from_db, language_code)

        if all_results is None:
            return "The certifications table does not exist in the database."

        if not all_results:
            if language_code != "en":
                all_results = await asyncio.to_thread(fetch_from_db, "en")
                if not all_results:
                    return "No certifications or compliance documents were found for this query."
            else:
                return "No certifications or compliance documents were found for this query."

        items = [(row[0], row[1]) for row in all_results]
        reranked = await asyncio.to_thread(
            _rerank_items, query, items, config.CERT_RERANK_TOP_K
        )
        reranked = await _grade_items(query, reranked)

        passages = [text for text, _, _ in reranked]
        encoded = [_encode_image_paths(text) for text in passages]

        vision_analysis = await _run_vision_analysis(query, passages)
        if vision_analysis:
            encoded.append(f"[Visual Analysis of Retrieved Diagrams]\n{vision_analysis}")

        sources = [s for s in (_source_from_meta(m, score) for _, m, score in reranked) if s]
        return {"text": "\n---\n".join(encoded), "sources": sources}

    except Exception as e:
        return f"Certification Search Error: {e}"


# --- TOOL 4: SEARCH PRODUCT URLs --------------------------------------------

async def db_search_product_urls() -> str:
    INFO_TABLE = "data_product_information_urls"
    CAD_TABLE = "data_product_cad_urls"
    TABLES = [INFO_TABLE, CAD_TABLE]

    try:
        def fetch_all_data():
            conn = None
            try:
                conn = db.get_connection()
                results = {}
                with conn.cursor() as cur:
                    for table in TABLES:
                        cur.execute(db.SQL_TABLE_EXISTS, (config.DB_SCHEMA, table))
                        if cur.fetchone() is None:
                            continue

                        cur.execute(
                            db.SQL_FETCH_ALL.format(
                                schema=config.DB_SCHEMA, table=table
                            )
                        )
                        rows = cur.fetchall()
                        if rows:
                            col_names = [desc[0] for desc in cur.description]
                            results[table] = [dict(zip(col_names, row)) for row in rows]
                return results
            except Exception as e:
                return f"Database error: {e}"
            finally:
                if conn is not None:
                    conn.close()

        results = await asyncio.to_thread(fetch_all_data)

        if isinstance(results, str):
            return results

        if not results:
            return "No data found in the tables."

        response = "Data found in the tables:\n\n"
        for table, rows in results.items():
            label = "Product Information" if table == INFO_TABLE else "CAD Download"
            response += f"**{label}** (`{table}`):\n"
            for row in rows:
                formatted = ", ".join(f"{k}: {v}" for k, v in row.items())
                response += f"  - {formatted}\n"
            response += "\n"
        return response

    except Exception as e:
        return f"Data Search Error: {e}"
