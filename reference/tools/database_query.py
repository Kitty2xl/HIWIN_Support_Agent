import os
import psycopg2
import json
import re
import base64
import requests
import asyncio
from urllib.parse import quote
from pydantic import BaseModel, Field
from typing import Callable, Any, List, Optional


class Tools:
    class Valves(BaseModel):
        PERSIST_DIR: str = Field(
            default=r"C:\Users\User_11\Desktop\HIWIN\Sheet_Processing\RAG"
        )
        LANGUAGE_MODEL: str = Field(default="Support_Agent_Qwen3.6")
        EMBEDDING_MODEL: str = Field(default="Embedding_Qwen3.6")
        RERANKER_MODEL: str = Field(default="Reranker_Qwen3.6")
        DB_NAME: str = Field(default="hiwin_rag_db")
        DB_USER: str = Field(default="postgres")
        DB_PASSWORD: str = Field(default="hiwinpassword")
        DB_HOST: str = Field(default="localhost")
        DB_PORT: str = Field(default="5432")
        DB_SCHEMA: str = Field(default="hiwin_rag")
        CONTENT_COLUMN: str = Field(default="text")
        EMBEDDING_COLUMN: str = Field(default="embedding")
        RERANKER_URL: str = Field(default="http://localhost:11400/v1/rerank")
        IMAGE_STATIC_ROOT: str = Field(
            default=r"C:\Users\User_11\Desktop\OpenWebUI\.venv\Lib\site-packages\open_webui\static\HIWIN",
            description="Filesystem path served as /static/HIWIN/ — used to resolve image URLs for vision analysis.",
        )
        VISION_MAX_IMAGES: int = Field(
            default=4,
            description="Maximum number of images to pass to the vision LLM per query.",
        )

    def __init__(self):
        self.valves = self.Valves()
        self.headers = {"Authorization": "Bearer", "Content-Type": "application/json"}

    # --- HELPER FOR STATUS UPDATES ---
    async def _emit_status(
        self, emitter: Callable[[dict], Any], message: str, done: bool = False
    ):
        if emitter:
            await emitter(
                {"type": "status", "data": {"description": message, "done": done}}
            )

    # --- HELPER FOR DB CONNECTION ---
    def _get_db_connection(self):
        return psycopg2.connect(
            dbname=self.valves.DB_NAME,
            user=self.valves.DB_USER,
            password=self.valves.DB_PASSWORD,
            host=self.valves.DB_HOST,
            port=self.valves.DB_PORT,
        )

    # --- HELPER FOR FORGIVING TABLE-NAME RESOLUTION ---
    def _resolve_tables(self, requested: list, existing: list):
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

        # Pre-index existing tables by their folded form for fuzzy lookup.
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
            # 1. exact
            if cand in existing_set:
                resolved.append(cand)
                continue
            # 2. case-insensitive, prefix optional
            ci = cand.lower()
            if ci in existing_ci:
                resolved.append(existing_ci[ci])
                continue
            if ("data_" + ci) in existing_ci:
                resolved.append(existing_ci["data_" + ci])
                continue
            # 3. alphanumeric fold
            f = fold(cand)
            if f in folded_existing:
                resolved.append(folded_existing[f])
                continue
            unmatched.append(p)

        # De-duplicate while preserving order.
        seen = set()
        resolved = [t for t in resolved if not (t in seen or seen.add(t))]
        return resolved, unmatched

    def _fetch_existing_tables(self):
        """Return the list of real data_* tables, or [] on failure. Sync (run in a thread)."""
        conn = None
        try:
            conn = self._get_db_connection()
            with conn.cursor() as cur:
                sql = base64.b64decode(
                    "U0VMRUNUIHRhYmxlX25hbWUgRlJPTSBpbmZvcm1hdGlvbl9zY2hlbWEudGFibGVzIFdIRVJFIHRhYmxlX3NjaGVtYSA9ICVzIEFORCB0YWJsZV9uYW1lIExJS0UgJ2RhdGFfJSUnOw=="
                ).decode()
                cur.execute(sql, (self.valves.DB_SCHEMA,))
                return [row[0] for row in cur.fetchall()]
        except Exception:
            return []
        finally:
            if conn is not None:
                conn.close()

    # --- HELPER FOR IMAGE PATH ENCODING ---
    def _encode_image_paths(self, text: str) -> str:
        def encode_match(m):
            alt = m.group(1)
            path = m.group(2)
            encoded = quote(path, safe="/:")
            return f"![{alt}]({encoded})"

        return re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", encode_match, text)

    def _call_chat_completion(self, messages, model, timeout=30):
        url = "http://localhost:11400/v1/chat/completions"
        response = requests.post(
            url,
            json={"model": model, "messages": messages, "stream": False},
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]

    def _get_embedding(self, text, model):
        url = "http://localhost:11400/v1/embeddings"
        response = requests.post(url, json={"model": model, "input": text}, timeout=30)
        response.raise_for_status()
        return response.json()["data"][0]["embedding"]

    def _rerank_passages(self, query: str, passages: list, top_n: int) -> list:
        if not passages:
            return []
        try:
            payload = {
                "model": self.valves.RERANKER_MODEL,
                "query": query,
                "documents": passages,
                "top_n": top_n,
            }
            response = requests.post(self.valves.RERANKER_URL, json=payload, timeout=30)
            response.raise_for_status()
            rerank_data = response.json()
            if "results" in rerank_data:
                return [passages[res["index"]] for res in rerank_data["results"]]
        except Exception as e:
            print(f"Reranking failed: {e}")
        return passages[:top_n]

    # --- VISION HELPERS ---

    def _extract_image_static_paths(self, passages: list) -> list:
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

    def _static_to_filesystem(self, static_path: str) -> str:
        """Map /static/HIWIN/a/b.jpg → IMAGE_STATIC_ROOT\\a\\b.jpg."""
        # Strip leading /static/HIWIN/ and re-join with the local root
        relative = re.sub(r"^/static/HIWIN/", "", static_path)
        return os.path.join(self.valves.IMAGE_STATIC_ROOT, *relative.split("/"))

    def _load_image_b64(self, fs_path: str):
        """Load an image and return (mime_type, base64_string), or None on failure."""
        try:
            ext = os.path.splitext(fs_path)[1].lower().lstrip(".")
            mime = "jpeg" if ext in ("jpg", "jpeg") else ext
            with open(fs_path, "rb") as f:
                return mime, base64.b64encode(f.read()).decode()
        except Exception:
            return None

    async def _run_vision_analysis(self, query: str, passages: list) -> str:
        """
        Extract images from retrieved passages, send them to the vision LLM alongside
        the query, and return a focused analysis of the visual content.
        Returns an empty string if no images are found or the call fails.
        """
        static_paths = self._extract_image_static_paths(passages)[
            : self.valves.VISION_MAX_IMAGES
        ]
        if not static_paths:
            return ""

        content = []
        for sp in static_paths:
            result = self._load_image_b64(self._static_to_filesystem(sp))
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
                self._call_chat_completion,
                [{"role": "user", "content": content}],
                self.valves.LANGUAGE_MODEL,
                120,  # vision calls need more time than text-only
            )
        except Exception as e:
            print(f"Vision analysis failed: {e}")
            return ""

    # --- TOOL 1: MANUAL SEARCH ---

    async def db_search_technical_manuals(
        self,
        query: str,
        language_code: str,
        product_table: List[str],
        __event_emitter__: Callable[[dict], Any] = None,
    ) -> str:
        """
        Description: This tool returns ONLY a list of information relevant to the subject queried. DOES NOT CONTAIN DOWNLOAD LINKS, DOWNLOAD INFORMATION, OR DOWNLOAD URLS.
        Search all general technical and product manuals.

        :param language_code: The language code used to filter database results (e.g. "en", "tc", "ja").
                              Must match the language_code value stored in the document metadata.

        IMPORTANT: The `product_table` parameter must be the full table name(s) as returned by db_get_available_product_tables (e.g. "data_linear_guideway", "data_ballscrew"). Do NOT pass a product series or model name (e.g. do NOT pass "EG" or "WE").
        Product series names (EG, WE, etc.) should be included in the `query` parameter instead.

        Example correct usage:
          query="EG series linear guideway load specifications"
          language_code="en"
          product_table=["data_linear_guideway"]
        """
        try:
            initial_top_K = 15  # per-table vector-search LIMIT
            rerank_candidate_K = 15  # passages fed into the reranker
            final_top_K = 5  # passages returned after reranking

            await self._emit_status(
                __event_emitter__, f"Embedding query (language: {language_code})..."
            )

            query_vector = await asyncio.to_thread(
                self._get_embedding,
                f"search_query: {query}",
                self.valves.EMBEDDING_MODEL,
            )

            if not product_table:
                await self._emit_status(
                    __event_emitter__, "No product specified.", done=True
                )
                return (
                    "Invalid Product Table. Call db_get_available_product_tables first to retrieve "
                    "valid product tables, then retry with a product_table value."
                )

            await self._emit_status(__event_emitter__, "Searching product tables...")

            def fetch_from_db(lang: str):
                conn = None
                results = []
                try:
                    conn = self._get_db_connection()
                    with conn.cursor() as cur:
                        sql_get_tables = base64.b64decode(
                            "U0VMRUNUIHRhYmxlX25hbWUgRlJPTSBpbmZvcm1hdGlvbl9zY2hlbWEudGFibGVzIFdIRVJFIHRhYmxlX3NjaGVtYSA9ICVzIEFORCB0YWJsZV9uYW1lIExJS0UgJ2RhdGFfJSUnOw=="
                        ).decode()
                        cur.execute(sql_get_tables, (self.valves.DB_SCHEMA,))
                        existing_tables = [row[0] for row in cur.fetchall()]

                        # Forgiving resolution: map the model's guesses onto real
                        # table names (case / prefix / punctuation tolerant), instead
                        # of strict equality which forces a blind retry loop.
                        table_list, unmatched = self._resolve_tables(
                            product_table, existing_tables
                        )
                        if unmatched:
                            print(
                                f"Unmatched product_table values ignored: {unmatched}"
                            )

                        # Bypass table: match by RESOLVED name against the real table,
                        # whatever its exact spelling. Fixes the old data_allproducts
                        # vs data_all_products mismatch (the bypass never used to fire).
                        is_bypass_table = {
                            t
                            for t in table_list
                            if t.lower() in ("data_all_products", "data_allproducts")
                        }

                        # Return None as a sentinel so the caller can distinguish
                        # "no tables matched" from "tables matched but returned no rows".
                        if not table_list:
                            return None

                        sql_template = base64.b64decode(
                            "U0VMRUNUIHtjb250ZW50fSwge2VtYmVkZGluZ30gPD0+ICVzOjp2ZWN0b3IgQVMgZGlzdGFuY2UgRlJPTSB7c2NoZW1hfS57dGFibGV9IFdIRVJFIG1ldGFkYXRhXy0+PidsYW5ndWFnZV9jb2RlJyA9ICVzIE9SREVSIEJZIGRpc3RhbmNlIEFTQyBMSU1JVCB7bGltaXR9Ow=="
                        ).decode()
                        sql_template_bypass = base64.b64decode(
                            "U0VMRUNUIHtjb250ZW50fSwgMC4wIEFTIGRpc3RhbmNlIEZST00ge3NjaGVtYX0ue3RhYmxlfSBXSEVSRSBtZXRhZGF0YV8tPj4nbGFuZ3VhZ2VfY29kZScgPSAlczs="
                        ).decode()

                        for table_name in table_list:
                            try:
                                if table_name in is_bypass_table:
                                    sql = sql_template_bypass.format(
                                        content=self.valves.CONTENT_COLUMN,
                                        schema=self.valves.DB_SCHEMA,
                                        table=table_name,
                                    )
                                    cur.execute(sql, (lang,))
                                    results.extend(cur.fetchall())
                                else:
                                    sql = sql_template.format(
                                        content=self.valves.CONTENT_COLUMN,
                                        embedding=self.valves.EMBEDDING_COLUMN,
                                        schema=self.valves.DB_SCHEMA,
                                        table=table_name,
                                        limit=initial_top_K,
                                    )
                                    cur.execute(sql, (json.dumps(query_vector), lang))
                                    results.extend(cur.fetchall())
                            except Exception as table_err:
                                print(f"Error querying table {table_name}: {table_err}")
                                conn.rollback()
                except Exception as db_err:
                    # Re-raise so the caller surfaces an infrastructure failure as an
                    # error, rather than masking it as "no results" (which would also
                    # trigger a pointless English-retry).
                    print(f"Database connection error: {db_err}")
                    raise
                finally:
                    if conn is not None:
                        conn.close()
                return results

            all_results = await asyncio.to_thread(fetch_from_db, language_code)

            if all_results is None:
                await self._emit_status(
                    __event_emitter__, "No matching product tables found.", done=True
                )
                # Hand back the REAL table names so the model can self-correct in ONE
                # turn instead of guessing blindly (which exhausts its token budget
                # and produces an empty answer).
                available = await asyncio.to_thread(self._fetch_existing_tables)
                listing = ", ".join(available) if available else "(none found)"
                return (
                    f"None of {product_table} matched a real table. "
                    f"Choose EXACTLY ONE OR MORE from this list and call again: {listing}"
                )

            if not all_results:
                if language_code != "en":
                    await self._emit_status(
                        __event_emitter__,
                        f"No results for '{language_code}', retrying with English...",
                    )
                    all_results = await asyncio.to_thread(fetch_from_db, "en")
                    if not all_results:
                        await self._emit_status(
                            __event_emitter__,
                            "No results found in English either.",
                            done=True,
                        )
                        return (
                            f"No results found for language '{language_code}' or 'en' "
                            f"in the available tables for products: {product_table}."
                        )
                else:
                    await self._emit_status(
                        __event_emitter__, "No technical manuals found.", done=True
                    )
                    return (
                        f"No results found in the available tables. "
                        f"You MUST retry using ONLY these exact product table: {product_table}. "
                        f"Do not attempt to use any other product names."
                    )

            all_results.sort(key=lambda x: x[1])
            candidates = [res[0] for res in all_results[:rerank_candidate_K]]

            await self._emit_status(__event_emitter__, "Running Reranker model...")
            reranked = await asyncio.to_thread(
                self._rerank_passages, query, candidates, final_top_K
            )

            encoded = [self._encode_image_paths(passage) for passage in reranked]

            await self._emit_status(
                __event_emitter__, "Running vision analysis on retrieved images..."
            )
            vision_analysis = await self._run_vision_analysis(query, reranked)
            if vision_analysis:
                encoded.append(
                    f"[Visual Analysis of Retrieved Diagrams]\n{vision_analysis}"
                )

            await self._emit_status(__event_emitter__, "Retrieval complete.", done=True)
            return "\n---\n".join(encoded)

        except Exception as e:
            await self._emit_status(
                __event_emitter__, "Manual search failed.", done=True
            )
            return f"Manual Search Error: {e}"

    # --- TOOL 2: GET AVAILABLE TABLES ---

    async def db_get_available_product_tables(
        self,
        __event_emitter__: Callable[[dict], Any] = None,
    ) -> str:
        """
        Description: This tool returns ONLY a list of all available product tables currently in the database.
        """
        try:
            await self._emit_status(
                __event_emitter__, "Fetching available product tables..."
            )

            def fetch_tables():
                conn = None
                try:
                    conn = self._get_db_connection()
                    with conn.cursor() as cur:
                        sql_get_tables = base64.b64decode(
                            "U0VMRUNUIHRhYmxlX25hbWUgRlJPTSBpbmZvcm1hdGlvbl9zY2hlbWEudGFibGVzIFdIRVJFIHRhYmxlX3NjaGVtYSA9ICVzIEFORCB0YWJsZV9uYW1lIExJS0UgJ2RhdGFfJSUnOw=="
                        ).decode()
                        cur.execute(sql_get_tables, (self.valves.DB_SCHEMA,))
                        return [row[0] for row in cur.fetchall()]
                except Exception as e:
                    return f"Database error: {e}"
                finally:
                    if conn is not None:
                        conn.close()

            tables = await asyncio.to_thread(fetch_tables)

            if isinstance(tables, str):
                await self._emit_status(
                    __event_emitter__, "Failed to fetch tables.", done=True
                )
                return tables

            if not tables:
                await self._emit_status(
                    __event_emitter__, "No data tables found.", done=True
                )
                return "No product tables starting with 'data_' were found in the database."

            await self._emit_status(
                __event_emitter__, f"Found {len(tables)} tables.", done=True
            )

            response = "The following product tables are available to search:\n"
            for table in tables:
                product_name = table.replace("data_", "")
                response += f"- {product_name} (Table: {table})\n"

            return response

        except Exception as e:
            await self._emit_status(
                __event_emitter__, "Error fetching tables.", done=True
            )
            return f"Error: {e}"

    # --- TOOL 3: SEARCH CERTIFICATIONS ---

    async def db_search_certifications(
        self,
        query: str,
        language_code: str = "en",
        __event_emitter__: Callable[[dict], Any] = None,
    ) -> str:
        """
        Description: This tool returns a list of certificates and their respective image file paths.
        Search for certificates, certifications, compliance documents, or any proof of conformance.

        :param language_code: The language code used to filter results (e.g. "en", "tc", "ja").
        """
        TARGET_TABLE = "data_certificates"

        try:
            await self._emit_status(
                __event_emitter__, "Embedding query for certifications..."
            )

            query_vector = await asyncio.to_thread(
                self._get_embedding,
                f"search_query: {query}",
                self.valves.EMBEDDING_MODEL,
            )

            def fetch_from_db(lang: str):
                conn = None
                try:
                    conn = self._get_db_connection()
                    with conn.cursor() as cur:
                        sql_exists = base64.b64decode(
                            "U0VMRUNUIHRhYmxlX25hbWUgRlJPTSBpbmZvcm1hdGlvbl9zY2hlbWEudGFibGVzIFdIRVJFIHRhYmxlX3NjaGVtYSA9ICVzIEFORCB0YWJsZV9uYW1lID0gJXM7"
                        ).decode()
                        cur.execute(sql_exists, (self.valves.DB_SCHEMA, TARGET_TABLE))
                        if cur.fetchone() is None:
                            return None  # Sentinel: table doesn't exist

                        sql_search = base64.b64decode(
                            "U0VMRUNUIHtjb250ZW50fSwge2VtYmVkZGluZ30gPD0+ICVzOjp2ZWN0b3IgQVMgZGlzdGFuY2UgRlJPTSB7c2NoZW1hfS57dGFibGV9IFdIRVJFIG1ldGFkYXRhXy0+PidsYW5ndWFnZV9jb2RlJyA9ICVzIE9SREVSIEJZIGRpc3RhbmNlIEFTQyBMSU1JVCB7bGltaXR9Ow=="
                        ).decode()
                        cur.execute(
                            sql_search.format(
                                content=self.valves.CONTENT_COLUMN,
                                embedding=self.valves.EMBEDDING_COLUMN,
                                schema=self.valves.DB_SCHEMA,
                                table=TARGET_TABLE,
                                limit=50,
                            ),
                            (json.dumps(query_vector), lang),
                        )
                        return cur.fetchall()
                except Exception as e:
                    print(f"Certifications DB error: {e}")
                    return []
                finally:
                    if conn is not None:
                        conn.close()

            await self._emit_status(
                __event_emitter__, "Searching certifications table..."
            )
            all_results = await asyncio.to_thread(fetch_from_db, language_code)

            if all_results is None:
                await self._emit_status(
                    __event_emitter__, "Certifications table not found.", done=True
                )
                return "The certifications table does not exist in the database."

            if not all_results:
                if language_code != "en":
                    await self._emit_status(
                        __event_emitter__,
                        f"No results for '{language_code}', retrying with English...",
                    )
                    all_results = await asyncio.to_thread(fetch_from_db, "en")
                    if not all_results:
                        await self._emit_status(
                            __event_emitter__, "No certifications found.", done=True
                        )
                        return "No certifications or compliance documents were found for this query."
                else:
                    await self._emit_status(
                        __event_emitter__, "No certifications found.", done=True
                    )
                    return "No certifications or compliance documents were found for this query."

            await self._emit_status(__event_emitter__, "Running Reranker model...")
            passages = [row[0] for row in all_results]
            reranked = await asyncio.to_thread(
                self._rerank_passages, query, passages, 10
            )

            encoded = [self._encode_image_paths(p) for p in reranked]

            await self._emit_status(
                __event_emitter__, "Running vision analysis on retrieved images..."
            )
            vision_analysis = await self._run_vision_analysis(query, reranked)
            if vision_analysis:
                encoded.append(
                    f"[Visual Analysis of Retrieved Diagrams]\n{vision_analysis}"
                )

            await self._emit_status(__event_emitter__, "Retrieval complete.", done=True)
            return "\n---\n".join(encoded)

        except Exception as e:
            await self._emit_status(
                __event_emitter__, "Certification search failed.", done=True
            )
            return f"Certification Search Error: {e}"

    # --- TOOL 4: SEARCH PRODUCT URLs ---

    async def db_search_product_urls(
        self,
        __event_emitter__: Callable[[dict], Any] = None,
    ) -> str:
        """
        Description: This tool returns records from the product information and CAD URL tables.
        ONLY CALL THIS TOOL ONCE. The information provided is sufficient enough to answer all queries.
        Search for product information links and URLs, or CAD download links and URLs.
        Returns all records from both tables.
        """
        INFO_TABLE = "data_product_information_urls"
        CAD_TABLE = "data_product_cad_urls"
        TABLES = [INFO_TABLE, CAD_TABLE]

        try:
            await self._emit_status(
                __event_emitter__, "Fetching data from URL tables..."
            )

            def fetch_all_data():
                conn = None
                try:
                    conn = self._get_db_connection()
                    results = {}

                    sql_exists = base64.b64decode(
                        "U0VMRUNUIHRhYmxlX25hbWUgRlJPTSBpbmZvcm1hdGlvbl9zY2hlbWEudGFibGVzIFdIRVJFIHRhYmxlX3NjaGVtYSA9ICVzIEFORCB0YWJsZV9uYW1lID0gJXM7"
                    ).decode()
                    sql_fetch_all = base64.b64decode(
                        "U0VMRUNUICogRlJPTSB7c2NoZW1hfS57dGFibGV9"
                    ).decode()

                    with conn.cursor() as cur:
                        for table in TABLES:
                            cur.execute(sql_exists, (self.valves.DB_SCHEMA, table))
                            if cur.fetchone() is None:
                                continue

                            cur.execute(
                                sql_fetch_all.format(
                                    schema=self.valves.DB_SCHEMA, table=table
                                )
                            )

                            rows = cur.fetchall()
                            if rows:
                                col_names = [desc[0] for desc in cur.description]
                                results[table] = [
                                    dict(zip(col_names, row)) for row in rows
                                ]

                    return results
                except Exception as e:
                    return f"Database error: {e}"
                finally:
                    if conn is not None:
                        conn.close()

            results = await asyncio.to_thread(fetch_all_data)

            if isinstance(results, str):
                await self._emit_status(
                    __event_emitter__, "Failed to fetch data.", done=True
                )
                return results

            if not results:
                await self._emit_status(__event_emitter__, "No data found.", done=True)
                return "No data found in the tables."

            await self._emit_status(
                __event_emitter__, "Data retrieval complete.", done=True
            )

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
            await self._emit_status(__event_emitter__, "Data search failed.", done=True)
            return f"Data Search Error: {e}"
