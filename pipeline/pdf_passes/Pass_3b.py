import os
import re
import hashlib
import asyncio
from tqdm.asyncio import tqdm
from openai import AsyncOpenAI
from core.progress import async_gather_with_progress, iter_with_progress, make_log_fn
from core.timeout_cache import is_timeout_error, make_timeout_noter
from core.config import LLM_BASE_URL, LLM_API_KEY, LLM_TIMEOUT, LLM_MAX_RETRIES


TABLE_SUMMARY_PROMPT = """\
You are a technical document analyst. The text below is a Markdown table extracted from a \
technical manual.

Write a concise description (1-3 sentences) of what this table shows. Focus on:
- What type of data it contains
- What it describes or compares
- Key parameters, units, or values if clearly visible

Output ONLY the description. No introductory text, no bullet points, no formatting.\
"""

# Matches a complete Markdown table: header row + separator row (must contain dashes)
# + zero or more data rows.  The separator distinguishes real tables from stray | usage.
TABLE_PATTERN = re.compile(
    r'(\|[^\n]+\n\|[ \t]*[-:]+[-| :\t]*\n(?:\|[^\n]+\n?)*)',
    re.MULTILINE,
)

_MAX_TABLE_LINES = 40   # cap rows sent to LLM to avoid token limit issues


def _table_key(table_text: str) -> str:
    """Stable 12-char hash of table content — used for deduplication across pages."""
    return hashlib.md5(table_text.strip().encode()).hexdigest()[:12]


def _truncate_table(table_text: str) -> str:
    lines = table_text.strip().split('\n')
    if len(lines) <= _MAX_TABLE_LINES:
        return table_text
    remaining = len(lines) - _MAX_TABLE_LINES
    return '\n'.join(lines[:_MAX_TABLE_LINES]) + f'\n| ... ({remaining} more rows) |'


async def _summarize_table(client, table_text, semaphore, model_name,
                           max_retries=3, log_fn=None, note_timeout=None):
    """Send a single Markdown table to the LLM and return its summary."""
    async with semaphore:
        last_exc = None
        for attempt in range(max_retries):
            try:
                response = await client.chat.completions.create(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": TABLE_SUMMARY_PROMPT},
                        {"role": "user",   "content": _truncate_table(table_text)},
                    ],
                )
                return table_text, (response.choices[0].message.content or "").strip()
            except Exception as e:
                last_exc = e
                if attempt < max_retries - 1:
                    wait = 2 ** attempt
                    _log = log_fn or tqdm.write
                    _log(
                        f"  Retry {attempt + 1}/{max_retries - 1} summarising table "
                        f"in {wait}s — {e}"
                    )
                    await asyncio.sleep(wait)
        _log = log_fn or tqdm.write
        _log(f"Error summarising table: {last_exc}")
        if note_timeout and is_timeout_error(last_exc):
            note_timeout()
        return table_text, None


async def _run_pass_3b(md_files, markdown_dir, output_directory, max_concurrent, model_name,
                       checkpoint_manager, checkpoint_filename, client=None, callback=None,
                       timeout_registry=None, semaphore=None):
    """
    Core async logic:
      Step 1 — Collect all unique Markdown tables across all files (dedup by content hash).
      Step 2 — Summarise every unique table in parallel.
      Step 3 — Inject summaries as italic caption lines immediately below each table instance.

    If `client` is provided it is used as-is and not closed on exit (caller owns it).
    """
    _owns_client = client is None
    if _owns_client:
        client = AsyncOpenAI(
            base_url=LLM_BASE_URL,
            api_key=LLM_API_KEY,
            timeout=LLM_TIMEOUT,
        )
    log_fn = make_log_fn(callback)

    try:
        # ----------------------------------------------------------------
        # Step 1: Collect unique tables across all Markdown files
        # ----------------------------------------------------------------
        file_contents = {}
        unique_tables = {}   # key -> table_text (first occurrence wins)

        for md_file in md_files:
            file_path = os.path.join(markdown_dir, md_file)
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            file_contents[md_file] = content

            for match in TABLE_PATTERN.finditer(content):
                table_text = match.group(1)
                key = _table_key(table_text)
                if key not in unique_tables:
                    unique_tables[key] = table_text

        if not unique_tables:
            # No tables found — copy files through unchanged
            for md_file in md_files:
                output_path = os.path.join(output_directory, md_file)
                with open(output_path, 'w', encoding='utf-8') as f:
                    f.write(file_contents[md_file])
            if checkpoint_manager and checkpoint_filename:
                checkpoint_manager.mark_done(checkpoint_filename, "pass_3b")
            return []

        # ----------------------------------------------------------------
        # Step 2: Summarise all unique tables in parallel
        # ----------------------------------------------------------------
        note_timeout = make_timeout_noter(timeout_registry, checkpoint_filename, "pass_3b")
        # Use the caller's shared semaphore when provided (pipeline: one cap across
        # all documents); otherwise a local one for standalone/CLI use.
        sem = semaphore if semaphore is not None else asyncio.Semaphore(max_concurrent)
        tasks = [
            _summarize_table(client, table_text, sem, model_name,
                             max_retries=LLM_MAX_RETRIES, log_fn=log_fn,
                             note_timeout=note_timeout)
            for table_text in unique_tables.values()
        ]

        results = await async_gather_with_progress(
            tasks, len(tasks), "Summarising Tables",
            pass_name="pass_3b", callback=callback,
            unit="table", mininterval=0.5,
        )

        summaries: dict[str, str] = {
            _table_key(table_text): summary
            for table_text, summary in results
            if summary is not None
        }

        # ----------------------------------------------------------------
        # Step 3: Inject summaries into each Markdown file
        # ----------------------------------------------------------------
        processed_files = []

        for md_file in iter_with_progress(
            md_files, "Injecting Table Summaries",
            pass_name="pass_3b", callback=callback,
            unit="file", mininterval=0.3,
        ):
            content = file_contents[md_file]

            def replacer(match):
                table_text = match.group(1)
                summary = summaries.get(_table_key(table_text))
                if summary is None:
                    return table_text
                return table_text.rstrip('\n') + f"\n*{summary}*\n"

            new_content = TABLE_PATTERN.sub(replacer, content)

            output_path = os.path.join(output_directory, md_file)
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(new_content)

            processed_files.append(output_path)

        if checkpoint_manager and checkpoint_filename:
            checkpoint_manager.mark_done(checkpoint_filename, "pass_3b")

        return processed_files

    finally:
        if _owns_client:
            await client.close()


def pass_3b(markdown_dir, max_concurrent, output_directory=None, model_name="local_model",
            checkpoint_manager=None, checkpoint_filename=None):
    """
    Pass 3b: Table Summarisation.

    Reads Markdown tables from the Pass 3 output, generates a concise LLM summary
    for each unique table, and injects it as a plain-text italic caption below the
    table so it is semantically searchable in the RAG index — eliminating the need
    for MarkdownElementNodeParser's LLM calls at ingestion time.
    """
    if checkpoint_manager and checkpoint_filename and \
            checkpoint_manager.is_done(checkpoint_filename, "pass_3b"):
        return []

    if output_directory:
        os.makedirs(output_directory, exist_ok=True)
    else:
        output_directory = markdown_dir

    try:
        md_files = sorted(f for f in os.listdir(markdown_dir) if f.lower().endswith('.md'))
    except FileNotFoundError:
        return []

    if not md_files:
        return []

    return asyncio.run(_run_pass_3b(
        md_files, markdown_dir, output_directory, max_concurrent, model_name,
        checkpoint_manager, checkpoint_filename,
    ))
