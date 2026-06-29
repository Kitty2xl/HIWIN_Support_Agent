import os
import re
import asyncio
import aiofiles
from openai import AsyncOpenAI
from core.progress import ProgressTracker, make_log_fn
from core.timeout_cache import is_timeout_error, make_timeout_noter
from core.fs_utils import to_long_path as _to_long_path
from core.config import (
    LLM_BASE_URL, LLM_API_KEY, LLM_TIMEOUT, CONCURRENCY_PASS_4,
    PASS_4_GATE_VALIDATION,
)


def _extract_page_number(filename):
    """Extract the integer page number from a filename for natural sort ordering."""
    match = re.search(r'page(\d+)', filename)
    return int(match.group(1)) if match else 0


def _table_cell_count(line: str) -> int:
    """Number of |-delimited cells in a Markdown table row."""
    s = line.strip()
    if s.startswith('|'):
        s = s[1:]
    if s.endswith('|'):
        s = s[:-1]
    return len(s.split('|'))


def _is_separator_row(line: str) -> bool:
    """True for a Markdown table separator row, e.g. ``|---|:--:|``."""
    s = line.strip().strip('|').strip()
    return bool(s) and '-' in s and set(s) <= set('-: |\t')


def _page_needs_validation(content: str) -> bool:
    """
    Cheap heuristic: does this page's Markdown look malformed enough to be worth
    an LLM repair pass?  Returns True for unbalanced code fences / HTML comments
    or tables with a missing separator row or inconsistent column counts.
    Well-formed prose returns False so it can skip the LLM entirely.
    """
    if not content.strip():
        return False
    # Unclosed code fence or HTML comment
    if content.count('```') % 2 != 0:
        return True
    if content.count('<!--') != content.count('-->'):
        return True

    # Inspect contiguous blocks of table-looking rows (>=2 pipes)
    lines = content.split('\n')
    i, n = 0, len(lines)
    while i < n:
        if lines[i].strip().count('|') >= 2:
            block = []
            while i < n and lines[i].strip().count('|') >= 2:
                block.append(lines[i])
                i += 1
            if len(block) >= 2:
                # A real table needs a separator row as its second line …
                if not _is_separator_row(block[1]):
                    return True
                # … and consistent column counts across all non-separator rows.
                counts = {_table_cell_count(r) for k, r in enumerate(block) if k != 1}
                if len(counts) > 1:
                    return True
        else:
            i += 1
    return False


PROMPT = """
Fix any broken Markdown syntax in the text below: repair broken tables, unclosed tags, inconsistent
heading levels, broken lists, and irregular spacing.

Preserve all content exactly — do not add, remove, or rephrase anything. Your output must be
approximately the same length as the input. Preserve all HTML comments and page markers exactly.

Output ONLY the corrected Markdown. No introduction, no code block wrappers.
"""


async def _validate_page(client, md_file, raw_content, validated_pages_dir,
                          model_name, semaphore, log_fn, progress, stop_event=None,
                          note_timeout=None):
    """
    Validate a single page's Markdown via the LLM.
    Returns (raw_content, validated_chunk, status), where status is one of
    'validated', 'cached', 'gated', 'stopped', or 'error' (used for the
    end-of-pass summary).

    *raw_content* is the page's Markdown, already read by the caller.  Only
    pages that actually attempt an LLM call advance *progress* — pages returned
    early as 'cached' or 'gated' are skipped instantly and must not tick the
    bar, so it reflects real LLM work (see _run_pass_4_async's pre-scan).

    If the page was already validated in a previous run the cached result is
    returned immediately without touching the LLM.  Once a stop is requested
    any page waiting on the semaphore falls back to raw content rather than
    making a new LLM call.
    """
    validated_chunk_path = _to_long_path(os.path.join(validated_pages_dir, md_file))

    # Per-page resume: already validated in a prior run
    if os.path.exists(validated_chunk_path):
        async with aiofiles.open(validated_chunk_path, 'r', encoding='utf-8') as f:
            validated_chunk = await f.read()
        return raw_content, validated_chunk, 'cached'

    # Gate: skip the LLM for pages whose Markdown already looks well-formed.
    # Not cached to disk, so flipping PASS_4_GATE_VALIDATION off later still
    # lets these pages be validated on the next run.
    if PASS_4_GATE_VALIDATION and not _page_needs_validation(raw_content):
        return raw_content, raw_content, 'gated'

    async with semaphore:
        # Honour a stop request — skip LLM call and fall back to raw content
        if stop_event and stop_event.is_set():
            progress.update(1)
            return raw_content, raw_content, 'stopped'

        status = 'validated'
        try:
            response = await client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": PROMPT},
                    {"role": "user",   "content": raw_content},
                ],
            )
            validated_chunk = re.sub(
                r'^```markdown\s*|\s*```$',
                '',
                (response.choices[0].message.content or "").strip(),
            )
            async with aiofiles.open(validated_chunk_path, 'w', encoding='utf-8') as f:
                await f.write(validated_chunk)

        except Exception as e:
            log_fn(f"Validation error on {md_file}: {e} — using raw content as fallback.")
            validated_chunk = raw_content
            status = 'error'
            if note_timeout and is_timeout_error(e):
                note_timeout()

    progress.update(1)
    return raw_content, validated_chunk, status


async def _run_pass_4_async(md_files, markdown_dir, output_file, model_name,
                             checkpoint_manager, checkpoint_filename,
                             concurrency, progress_callback=None, stop_event=None,
                             shared_client=None, timeout_registry=None, semaphore=None):
    """
    Core async logic: validate all pages concurrently up to *concurrency*
    simultaneous LLM calls, then merge into a single master document in page
    order.

    If *shared_client* is provided it is used as-is and not closed on exit —
    the caller owns its lifecycle (same pattern as Passes 2/2b/3).
    """
    output_dir = os.path.dirname(output_file) or "."
    os.makedirs(_to_long_path(output_dir), exist_ok=True)
    validated_pages_dir = os.path.join(output_dir, "Validated_Pages")
    os.makedirs(_to_long_path(validated_pages_dir), exist_ok=True)

    log_fn       = make_log_fn(progress_callback)
    # Use the caller's shared semaphore when provided (pipeline: one cap across all
    # documents on the Phase B loop); otherwise a local one for standalone/CLI use.
    semaphore    = semaphore if semaphore is not None else asyncio.Semaphore(concurrency)
    note_timeout = make_timeout_noter(timeout_registry, checkpoint_filename, "pass_4")

    _owns_client = shared_client is None
    client = shared_client or AsyncOpenAI(
        base_url=LLM_BASE_URL,
        api_key=LLM_API_KEY,
        timeout=LLM_TIMEOUT,
    )

    # Read every page once up front so the progress bar can be sized to the
    # pages that will actually hit the LLM.  Gated (well-formed) and cached
    # pages are skipped instantly and must not inflate the bar.
    raw_contents = [None] * len(md_files)

    async def _load(idx, name):
        path = _to_long_path(os.path.join(markdown_dir, name))
        async with aiofiles.open(path, 'r', encoding='utf-8') as f:
            raw_contents[idx] = await f.read()

    await asyncio.gather(*[_load(i, name) for i, name in enumerate(md_files)])

    def _will_hit_llm(name, raw):
        # Mirrors the early-return decisions inside _validate_page.
        if os.path.exists(_to_long_path(os.path.join(validated_pages_dir, name))):
            return False  # cached from a prior run
        if PASS_4_GATE_VALIDATION and not _page_needs_validation(raw):
            return False  # well-formed — gated
        return True

    total_llm = sum(1 for name, raw in zip(md_files, raw_contents)
                    if _will_hit_llm(name, raw))

    try:
        with ProgressTracker(total_llm, "Validating & Merging Pages",
                             pass_name="pass_4", callback=progress_callback,
                             unit="page") as progress:
            coros = [
                _validate_page(client, md_file, raw, validated_pages_dir,
                               model_name, semaphore, log_fn, progress, stop_event,
                               note_timeout=note_timeout)
                for md_file, raw in zip(md_files, raw_contents)
            ]
            results = await asyncio.gather(*coros)
    finally:
        if _owns_client:
            await client.close()

    # Summarise how many pages actually hit the LLM vs were skipped.
    statuses  = [r[2] for r in results]
    validated = statuses.count('validated')
    gated     = statuses.count('gated')
    cached    = statuses.count('cached')
    errored   = statuses.count('error')
    summary = f"Pass 4: validated {validated}/{len(results)} pages via LLM; {gated} skipped as well-formed"
    if cached:
        summary += f"; {cached} cached"
    if errored:
        summary += f"; {errored} fell back to raw (errors)"
    log_fn(summary)

    # Assemble the merged documents in page order
    unvalidated_content = ""
    validated_content   = ""
    for md_file, (raw_content, validated_chunk, _status) in zip(md_files, results):
        page_match  = re.search(r'page(\d+)', md_file)
        page_label  = f"Page {page_match.group(1)}" if page_match else md_file.replace('.md', '')
        page_header = f"\n\n\n\n**[{page_label}]**\n\n"
        page_footer = "\n\n\n\n---\n\n"
        unvalidated_content += page_header + raw_content    + page_footer
        validated_content   += page_header + validated_chunk + page_footer

    # Save the unvalidated merged file as a backup reference
    unvalidated_path = _to_long_path(os.path.join(output_dir, "unvalidated_master.md"))
    async with aiofiles.open(unvalidated_path, 'w', encoding='utf-8') as f:
        await f.write(unvalidated_content)

    # Save the final validated master document
    async with aiofiles.open(_to_long_path(output_file), 'w', encoding='utf-8') as f:
        await f.write(validated_content)

    if checkpoint_manager and checkpoint_filename:
        checkpoint_manager.mark_done(checkpoint_filename, "pass_4")

    return output_file


async def pass_4_async(markdown_dir, output_file="merged_master_document.md",
                        model_name="local_model",
                        checkpoint_manager=None, checkpoint_filename=None,
                        progress_callback=None, concurrency=None, stop_event=None,
                        shared_client=None, timeout_registry=None, semaphore=None):
    """
    Async entry point for Pass 4 — use this when a shared event loop / client is
    already running (e.g. Phase B's _PersistentAsyncRunner).
    """
    if concurrency is None:
        concurrency = CONCURRENCY_PASS_4

    if checkpoint_manager and checkpoint_filename and \
            checkpoint_manager.is_done(checkpoint_filename, "pass_4"):
        return output_file

    try:
        md_files = sorted(
            [f for f in os.listdir(_to_long_path(markdown_dir)) if f.lower().endswith('.md')],
            key=_extract_page_number,
        )
    except FileNotFoundError:
        return None

    if not md_files:
        return None

    return await _run_pass_4_async(
        md_files, markdown_dir, output_file, model_name,
        checkpoint_manager, checkpoint_filename,
        concurrency, progress_callback, stop_event, shared_client,
        timeout_registry=timeout_registry, semaphore=semaphore,
    )


def pass_4(markdown_dir, output_file="merged_master_document.md", model_name="local_model",
           checkpoint_manager=None, checkpoint_filename=None, progress_callback=None,
           concurrency=None, stop_event=None):
    """
    Pass 4: Validation & Merging (sync entry point for standalone / CLI use).

    For pipeline use inside a running event loop, call pass_4_async() directly.
    """
    return asyncio.run(
        pass_4_async(
            markdown_dir, output_file, model_name,
            checkpoint_manager, checkpoint_filename,
            progress_callback, concurrency, stop_event,
        )
    )
