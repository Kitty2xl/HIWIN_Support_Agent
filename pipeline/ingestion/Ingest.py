import os
import re
import json
import shutil
from datetime import datetime, timezone

import psycopg2
from psycopg2 import pool as pg_pool

# LlamaIndex & Postgres
from llama_index.core import Document, VectorStoreIndex, StorageContext, Settings
from llama_index.core.node_parser import MarkdownNodeParser
from llama_index.core.schema import TextNode
from llama_index.vector_stores.postgres import PGVectorStore
from llama_index.embeddings.openai_like import OpenAILikeEmbedding

from core.config import (
    ROOT_PATH, FINAL_OUTPUT_ROOT, IMAGE_TARGET_ROOT, PROCESS_ROOT,
    LLM_BASE_URL,
    DB_NAME, DB_USER, DB_PASS, DB_HOST, DB_PORT, EMBED_DIM, DB_SCHEMA,
    EMBED_BATCH_SIZE, INGEST_BY_PAGE,
)
from core.checkpoint_manager import CheckpointManager
from core.fs_utils import to_long_path

# Checkpoint pass name used to mark a document as already ingested into the DB.
INGEST_PASS = "ingest"


def _load_doc_checkpoint_map() -> dict:
    """
    Build a {(product, sub_folder, language): checkpoint_filename} lookup from
    pipeline_mapping.json (written by Pipeline.py in Phase 5).

    Lets standalone ingestion runs resolve each Final_Output document back to the
    checkpoint key its passes were recorded under, so the 'ingest' flag lands in
    the same per-document entry.  Returns {} if the mapping is missing/unreadable.
    """
    path = os.path.join(PROCESS_ROOT, "pipeline_mapping.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            mapping = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, IOError):
        return {}
    out = {}
    for ckpt_filename, info in mapping.items():
        key = (info.get("product"), info.get("sub_folder"), info.get("language"))
        out[key] = ckpt_filename
    return out

PRODUCT_TYPES = []  # Will be dynamically detected

_pg_pool: pg_pool.SimpleConnectionPool | None = None

def _get_pool() -> pg_pool.SimpleConnectionPool:
    global _pg_pool
    if _pg_pool is None:
        _pg_pool = pg_pool.SimpleConnectionPool(
            minconn=1, maxconn=3,
            dbname=DB_NAME, user=DB_USER, password=DB_PASS,
            host=DB_HOST, port=DB_PORT,
        )
    return _pg_pool

def get_pg_connection():
    """Return a connection from the shared pool."""
    return _get_pool().getconn()

def _release_pg_connection(conn) -> None:
    _get_pool().putconn(conn)


def ensure_schema_exists(schema_name: str) -> None:
    """Create the schema if it does not already exist."""
    if not re.fullmatch(r'[a-z_][a-z0-9_]*', schema_name):
        raise ValueError(f"Invalid schema name: {schema_name}")
    # FIX (Bug 8): Replace the two-connection check-then-create pattern with a
    # single atomic CREATE SCHEMA IF NOT EXISTS, eliminating the TOCTOU race
    # condition that could cause failures when multiple processes run simultaneously.
    conn = get_pg_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(f'CREATE SCHEMA IF NOT EXISTS {schema_name}')
                print(f"  Ensured schema exists: {schema_name}")
    finally:
        _release_pg_connection(conn)


def get_existing_version(table_name: str, file_name: str) -> int | None:
    physical_table = f"{DB_SCHEMA}.data_{table_name}"
    sql = f"""
        SELECT MAX((metadata_->>'version')::int)
        FROM   {physical_table}
        WHERE  metadata_->>'file_name' = %s
    """
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (file_name,))
            result = cur.fetchone()
            return result[0] if result else None
    except psycopg2.errors.UndefinedTable:
        return None
    finally:
        _release_pg_connection(conn)


def delete_existing_nodes(table_name: str, file_name: str) -> int:
    physical_table = f"{DB_SCHEMA}.data_{table_name}"
    sql = f"""
        DELETE FROM {physical_table}
        WHERE  metadata_->>'file_name' = %s
    """
    conn = get_pg_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql, (file_name,))
                deleted = cur.rowcount
    finally:
        _release_pg_connection(conn)
    return deleted

def run_ingestion_stage(checkpoint_manager=None, doc_checkpoint_map=None,
                        force: bool = False, skip_asset_copy: bool = False) -> None:
    """Run the RAG ingestion pipeline (Phase 7).

    Scans Final_Output for markdown files, migrates assets to the web
    static root, and ingests parsed nodes into PostgreSQL using LlamaIndex.

    Checkpointing
    -------------
    Each document records an 'ingest' step in the checkpoint once it has been
    successfully ingested.  On subsequent runs an already-ingested document is
    skipped entirely (no parsing, embedding, or DB writes) unless *force* is True.

    checkpoint_manager  — a CheckpointManager to read/record the 'ingest' flag.
                          When None, one is created from ROOT_PATH/checkpoint.json
                          so standalone runs are checkpoint-aware too.
    doc_checkpoint_map   — {(product, sub_folder, language): checkpoint_filename}.
                          When None, it is loaded from pipeline_mapping.json.
    force                — re-ingest even documents already marked 'ingest'.
    skip_asset_copy      — DB-only mode: skip copying Figures/Tables into the web
                          static root (IMAGE_TARGET_ROOT).  Markdown image links
                          are still rewritten to their web URLs, so the stored
                          text points at assets served separately.  Use this when
                          the assets are already in place (the old ingest_db.py
                          behavior).
    """

    if checkpoint_manager is None:
        checkpoint_manager = CheckpointManager(
            os.path.join(ROOT_PATH, "checkpoint.json")
        )
    if doc_checkpoint_map is None:
        doc_checkpoint_map = _load_doc_checkpoint_map()

    if not skip_asset_copy:
        os.makedirs(IMAGE_TARGET_ROOT, exist_ok=True)

    print("\nScanning nested directories for markdown files...")

    processed_md_files_by_table: dict[str, list[dict]] = {}

    # ---------------------------------------------------------
    # PHASE 1 & 2: DYNAMIC DIRECTORY WALKING & ASSET MIGRATION
    # ---------------------------------------------------------
    # Pipeline.py always writes Final_Output with exactly 3 levels:
    #   {product.lower()} / {sub_folder_name} / {language}
    #
    # Where sub_folder_name is all intermediate YAML path levels
    # (doc_type, sub_product, extra_levels) joined with underscores and
    # lowercased. For example:
    #   ballspline / user_manual_somemodel / en
    #   linear_guideway / general / zh
    #
    # os.walk will also descend into Figures/ and Tables/ subdirectories,
    # but those contain no .md files and are naturally skipped below.
    for root, dirs, files in os.walk(FINAL_OUTPUT_ROOT):

        # 1. Check if there are any .md files in this specific folder
        md_files = [f for f in files if f.lower().endswith('.md')]
        if not md_files:
            continue

        # 2. Extract metadata based on the folder hierarchy
        # Example root:     C:\...\Final_Output\Ballspline\user_manual_somemodel\en
        # Example rel_path: Ballspline\user_manual_somemodel\en
        rel_path = os.path.relpath(root, FINAL_OUTPUT_ROOT)
        path_parts = rel_path.split(os.sep)

        # Pipeline.py always produces exactly 3 levels: product / sub_folder / language.
        # Any other depth indicates an unexpected or malformed output.
        if len(path_parts) != 3:
            print(f" [WARNING] Skipping '{rel_path}': Expected 3 directory levels "
                  f"(product/sub_folder/language), got {len(path_parts)}.")
            continue

        product_type = path_parts[0]
        # sub_folder is the collapsed combination of doc_type, sub_product, and any
        # extra YAML levels, joined with underscores by Pipeline.py.
        # e.g. "user_manual", "user_manual_somemodel", "general"
        sub_folder   = path_parts[1]
        language_code = path_parts[2]

        # Ensure this is a product type we actually want to process
        matched_p_type = product_type
        if not matched_p_type:
            continue

        # Sanitize product type for valid table name (alphanumeric + underscore only)
        target_table = re.sub(r'[^a-z0-9_]', '_', matched_p_type.lower())

        # Create a unique document ID to group these specific files in the DB.
        # e.g. "Ballspline_user_manual_somemodel_en"
        unique_doc_id = f"{matched_p_type}_{sub_folder}_{language_code}"

        print(f"\n--- Processing Assets: {unique_doc_id} ---")

        # 3. Migrate Images (skipped in DB-only mode — assets served separately).
        # Target path: .../static/HIWIN/{ProductType}/{sub_folder}/{language}/
        if not skip_asset_copy:
            target_folder_path = os.path.join(IMAGE_TARGET_ROOT, matched_p_type, sub_folder, language_code)
            os.makedirs(to_long_path(target_folder_path), exist_ok=True)

            for asset_folder in ["Figures", "Tables"]:
                src_img_dir = os.path.join(root, asset_folder)
                dst_img_dir = os.path.join(target_folder_path, asset_folder)
                if os.path.exists(to_long_path(src_img_dir)):
                    print(f"  [Migrating] {asset_folder} -> web static root...")
                    shutil.copytree(to_long_path(src_img_dir), to_long_path(dst_img_dir),
                                    dirs_exist_ok=True)
        else:
            print("  [DB-only] Skipping asset copy to web static root.")

        md_filepaths = [os.path.join(root, f) for f in md_files]
        web_base_path = f"/static/HIWIN/{matched_p_type}/{sub_folder}/{language_code}"

        processed_md_files_by_table.setdefault(target_table, []).append({
            "md_filepaths":  md_filepaths,
            "process_dir":   os.path.join(PROCESS_ROOT, product_type, sub_folder, language_code),
            "file_name":     unique_doc_id,  # Used for DB deduplication
            "product_type":  product_type,   # original-case product (for checkpoint key)
            "language_code": language_code,
            "sub_folder":    sub_folder,
            "web_base_path": web_base_path
        })


    # ---------------------------------------------------------
    # PHASE 3: INITIALISE API MODELS
    # ---------------------------------------------------------
    print("\nConnecting to Local API Models (Embeddings & LLM)...")

    # NOTE: if you change this embedding model, its output dimension probably
    # changes too — update EMBED_DIM in core/config.py to match and rebuild the
    # DB table, or pgvector inserts will fail (dimension mismatch).
    embed_model = OpenAILikeEmbedding(
        api_base=LLM_BASE_URL,
        api_key="llama-swap",
        model_name="Embedding_Qwen3.6",
        timeout=120000,
        embed_batch_size=EMBED_BATCH_SIZE,
    )

    Settings.embed_model = embed_model

    if INGEST_BY_PAGE:
        print("Ingestion mode: per-page (one embedding node per page)")
        md_parser = None
    else:
        print("Ingestion mode: by section (MarkdownNodeParser)")
        md_parser = MarkdownNodeParser()

    ensure_schema_exists(DB_SCHEMA)


    # ---------------------------------------------------------
    # PHASE 4: CHUNKING & INGESTION BY TABLE
    # ---------------------------------------------------------
    for table_name, file_infos in processed_md_files_by_table.items():
        if not file_infos:
            continue

        print(f"\n{'=' * 55}")
        print(f" DATABASE INGESTION: TABLE '{table_name.upper()}'")
        print(f"{'=' * 55}")

        vector_store = PGVectorStore.from_params(
            database=DB_NAME, host=DB_HOST, password=DB_PASS,
            port=DB_PORT, user=DB_USER,
            table_name=table_name, schema_name=DB_SCHEMA, embed_dim=EMBED_DIM
        )
        storage_context = StorageContext.from_defaults(vector_store=vector_store)
        index = VectorStoreIndex.from_vector_store(
            vector_store=vector_store,
            embed_model=embed_model,
            storage_context=storage_context
        )

        for file_info in file_infos:
            md_paths      = file_info["md_filepaths"]
            file_name     = file_info["file_name"]   # e.g. "Ballspline_user_manual_somemodel_en"
            lang_code     = file_info["language_code"]
            sub_folder    = file_info["sub_folder"]
            web_base_path = file_info["web_base_path"]

            # Resolve this document's checkpoint key and skip it if already
            # ingested (unless forced).  Docs with no checkpoint mapping (e.g.
            # left over from older runs) fall through and ingest as before.
            ckpt_key = doc_checkpoint_map.get(
                (file_info["product_type"], sub_folder, lang_code)
            )
            if ckpt_key and not force and \
                    checkpoint_manager.is_done(ckpt_key, INGEST_PASS):
                print(f"\n--- Skipping (already ingested): {file_name} ---")
                continue

            print(f"\n--- Chunking & Ingesting: {file_name} ---")
            print(f"    Found {len(md_paths)} markdown file(s).")

            existing_version = get_existing_version(table_name, file_name)
            next_version = (existing_version or 0) + 1
            date_ingested = datetime.now(timezone.utc).isoformat()
            all_nodes_to_insert = []
            parsing_failed = False

            if INGEST_BY_PAGE:
                # ---------------------------------------------------------
                # PER-PAGE MODE: one TextNode per page from Pass_3b / Pass_3
                # ---------------------------------------------------------
                # Use to_long_path everywhere: these PROCESS paths often exceed
                # Windows' 260-char limit, so a plain os.path.exists/listdir would
                # report a directory that exists as missing and wrongly skip it.
                process_dir  = file_info["process_dir"]
                pass_3b_dir  = os.path.join(process_dir, "Pass_3b")
                pass_3_dir   = os.path.join(process_dir, "Pass_3")
                page_src_dir = (pass_3b_dir if os.path.exists(to_long_path(pass_3b_dir))
                                else pass_3_dir)

                if not os.path.exists(to_long_path(page_src_dir)):
                    print(f"  [WARNING] No per-page source dir found for '{file_name}' — skipping.")
                    continue

                page_files = sorted(
                    [f for f in os.listdir(to_long_path(page_src_dir)) if f.lower().endswith('.md')],
                    key=lambda n: int(m.group(1)) if (m := re.search(r'page(\d+)', n)) else 0,
                )
                print(f"  -> Per-page: {len(page_files)} page(s) from {os.path.basename(page_src_dir)}")

                for page_file in page_files:
                    page_num = int(m.group(1)) if (m := re.search(r'page(\d+)', page_file)) else 0
                    try:
                        with open(to_long_path(os.path.join(page_src_dir, page_file)),
                                  'r', encoding='utf-8') as f:
                            page_text = f.read()
                        # Rewrite Cropped_Figure / Cropped_Table paths used in per-page files
                        page_text = re.sub(
                            r'!\[([^\]]*)\]\((?:[^)]*?/)Cropped_Figure/([^)]+)\)',
                            rf'![\1]({web_base_path}/Figures/\2)',
                            page_text,
                        )
                        page_text = re.sub(
                            r'!\[([^\]]*)\]\((?:[^)]*?/)Cropped_Table/([^)]+)\)',
                            rf'![\1]({web_base_path}/Tables/\2)',
                            page_text,
                        )
                        # Also handle already-normalised Figures/Tables paths
                        page_text = re.sub(
                            r'!\[([^\]]*)\]\((?:.*?/)?((?:Figures|Tables)/[^)]+)\)',
                            rf'![\1]({web_base_path}/\2)',
                            page_text,
                        )
                        page_text = f"Page {page_num}\n\n" + page_text
                        all_nodes_to_insert.append(TextNode(
                            text=page_text,
                            metadata={
                                "file_name":     file_name,
                                "source_md":     page_file,
                                "page_number":   page_num,
                                "language_code": lang_code,
                                "product_type":  table_name,
                                "sub_folder":    sub_folder,
                                "version":       next_version,
                                "date_ingested": date_ingested,
                            },
                        ))
                    except Exception as e:
                        print(f"  [ERROR] Failed to process page '{page_file}': {e}")
                        parsing_failed = True

            else:
                # ---------------------------------------------------------
                # SECTION MODE: split merged document by markdown headings
                # ---------------------------------------------------------
                for md_path in md_paths:
                    base_md_name = os.path.basename(md_path)
                    print(f"  -> Parsing: {base_md_name}...")
                    try:
                        with open(to_long_path(md_path), 'r', encoding='utf-8') as f:
                            md_text = f.read()
                        # Rewrite local image paths to web-accessible static routes.
                        md_text = re.sub(
                            r'!\[([^\]]*)\]\((?:.*?/)?((?:Figures|Tables)/[^)]+)\)',
                            rf'![\1]({web_base_path}/\2)',
                            md_text,
                        )
                        llama_document = Document(
                            text=md_text,
                            metadata={
                                "file_name":     file_name,
                                "source_md":     base_md_name,
                                "language_code": lang_code,
                                "product_type":  table_name,
                                "sub_folder":    sub_folder,
                                "version":       next_version,
                                "date_ingested": date_ingested,
                            }
                        )
                        nodes = md_parser.get_nodes_from_documents([llama_document], show_progress=True)
                        all_nodes_to_insert.extend(nodes)
                    except Exception as e:
                        print(f"  [ERROR] Failed to process '{base_md_name}': {e}")
                        parsing_failed = True
                        continue
            # 2. SAFEGUARD CHECK
            if parsing_failed:
                print(f"  [WARNING] Some files failed to parse for '{file_name}', but continuing with successfully parsed files.")
            if not all_nodes_to_insert:
                print(f"  [WARNING] No valid text/objects extracted for '{file_name}'.")
                continue

            # 3. DATABASE MODIFICATION
            if existing_version is not None:
                deleted_count = delete_existing_nodes(table_name, file_name)
                print(f"  [Dedup] Removed {deleted_count} old rows (v{existing_version}). Re-ingesting as v{next_version}.")
            else:
                print(f"  [Dedup] No prior ingestion found. Ingesting as v{next_version}.")

            index.insert_nodes(all_nodes_to_insert)
            print(f"  -> Successfully ingested {len(all_nodes_to_insert)} nodes for: {file_name} (v{next_version}, {date_ingested})")

            # Record the 'ingest' checkpoint so future runs skip this document.
            # Only mark it when every page/file parsed cleanly — a partial
            # failure stays unmarked so the next run retries it.
            if ckpt_key and not parsing_failed:
                checkpoint_manager.mark_done(ckpt_key, INGEST_PASS)
            elif ckpt_key and parsing_failed:
                print(f"  [Checkpoint] Not marking '{file_name}' as ingested "
                      f"(some files failed to parse) — it will retry next run.")

    print("\n✅ All product types fully processed and stored in their respective tables!")


# --- Standalone entry point ---
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Embed Final_Output markdown and ingest it into PostgreSQL.",
    )
    parser.add_argument(
        "--db-only", action="store_true",
        help="DB-only ingestion: do not copy Figures/Tables into the web static "
             "root (assets are served separately); markdown links are still "
             "rewritten to their web URLs.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-ingest even documents already marked 'ingest' in the checkpoint.",
    )
    args = parser.parse_args()

    run_ingestion_stage(force=args.force, skip_asset_copy=args.db_only)
