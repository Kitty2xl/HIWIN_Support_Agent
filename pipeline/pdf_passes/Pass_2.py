import os
import base64
import asyncio
import re
import aiofiles
from tqdm.asyncio import tqdm
from openai import AsyncOpenAI
from core.progress import async_gather_with_progress, make_log_fn
from core.md_utils import normalize_image_placeholders
from core.node_pool import NodePool
from core.timeout_cache import is_timeout_error, make_timeout_noter
from core.config import (
    LLM_BASE_URL, LLM_API_KEY, LLM_TIMEOUT, LLM_MAX_RETRIES,
    PASS_2_SKIP_BLANK_PAGES, PASS_2_BLANK_DRY_RUN,
)


async def _encode_image(image_path):
    """Encode an image file to a base64 string asynchronously."""
    async with aiofiles.open(image_path, "rb") as f:
        content = await f.read()
    return base64.b64encode(content).decode('utf-8')


async def _transcribe_page(pool, image_path, output_path, prompt, model_name,
                           max_retries=3, log_fn=None, note_timeout=None):
    """Transcribe a single page image to Markdown and write the result to disk."""
    # Skip pages that were already transcribed in a previous run.
    if os.path.exists(output_path):
        return output_path

    # Skip pages Pass 1 flagged as blank (no real content) — avoids a vision call.
    if PASS_2_SKIP_BLANK_PAGES and os.path.exists(os.path.splitext(image_path)[0] + ".blank"):
        _log = log_fn or tqdm.write
        if PASS_2_BLANK_DRY_RUN:
            _log(f"Would skip blank page: {os.path.basename(image_path)}")
        else:
            _log(f"Skipping blank page: {os.path.basename(image_path)}")
            async with aiofiles.open(output_path, "w", encoding="utf-8") as f:
                await f.write("")
            return output_path

    async with pool.slot() as client:
        base64_image = await _encode_image(image_path)
        last_exc = None
        for attempt in range(max_retries):
            try:
                response = await client.chat.completions.create(
                    model=model_name,
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}},
                            {"type": "text",      "text": prompt},
                        ],
                    }],
                )
                content = response.choices[0].message.content or ""
                # The VLM is asked to convert "[Tables/x.jpg]" placeholders into
                # "![](Tables/x.jpg)" image tags but doesn't always comply, so
                # enforce it deterministically before any downstream pass reads it.
                content = normalize_image_placeholders(content)
                async with aiofiles.open(output_path, "w", encoding="utf-8") as f:
                    await f.write(content)
                return output_path
            except Exception as e:
                last_exc = e
                if attempt < max_retries - 1:
                    # llama-swap returns transient errors while swapping models in/out
                    # (e.g. "process was already starting", "upstream command exited").
                    # A short exponential backoff is enough for the model to finish
                    # loading before the next attempt.
                    wait = 2 ** attempt          # 1 s, then 2 s
                    _log = log_fn or tqdm.write
                    _log(
                        f"  Retry {attempt + 1}/{max_retries - 1} for "
                        f"{os.path.basename(image_path)} in {wait}s — {e}"
                    )
                    await asyncio.sleep(wait)

        _log = log_fn or tqdm.write
        _log(f"Error transcribing {os.path.basename(image_path)}: {last_exc}")
        if note_timeout and is_timeout_error(last_exc):
            note_timeout()
        return None


async def _run_pass_2(pages_dir, markdown_dir, page_images, max_concurrent, model_name,
                      checkpoint_manager, checkpoint_filename, pool=None, callback=None,
                      timeout_registry=None):
    """Core async loop: transcribe all page images to Markdown in parallel.

    If `pool`     is provided it is used as-is and the caller owns its clients'
                  lifecycle (nothing is closed here); requests are spread across
                  every node in the pool.  If omitted, a single-node pool on
                  LLM_BASE_URL is created and closed on exit.
    If `callback` is provided, progress events are posted instead of using tqdm.
    """
    _owns_pool = pool is None
    if _owns_pool:
        client = AsyncOpenAI(
            base_url=LLM_BASE_URL,
            api_key=LLM_API_KEY,
            timeout=LLM_TIMEOUT,
        )
        pool = NodePool([client], max_concurrent)
    log_fn = make_log_fn(callback)

    PROMPT = """
    Parse the provided technical document image into Markdown.

    1. Read strictly top-to-bottom. Interleave text, tables, and image placeholders in exact visual
       order. Do NOT group image paths together.

    2. Use semantic headings (#, ##, ###) matching the visual hierarchy. Preserve exact typographical
       symbols (e.g. ◀ not - or |). Render math and technical notation in LaTeX (e.g. $equation$).

    3. Image placeholders appear as red text in the image (e.g. [Tables/file.jpg] or [Figures/file.jpg]).
       Convert each to: ![Figure 1](Figures/file.jpg) or ![Table 1](Tables/file.jpg).
       Never use the raw file path as alt-text — use a simple sequential identifier.

    4. Silently drop page numbers, decorative lines, UI icons, watermarks, and clear OCR glitches.
       Preserve all technical metadata, units, and drawing tags (e.g. Unit : mm, D-D VIEW) exactly
       as positioned, even if isolated or partially cut off.

    Output ONLY valid Markdown. No introduction, no summary, no code block wrappers.
    """

    note_timeout = make_timeout_noter(timeout_registry, checkpoint_filename, "pass_2")
    tasks = []

    for image_name in page_images:
        image_path  = os.path.join(pages_dir, image_name)
        output_path = os.path.join(markdown_dir, os.path.splitext(image_name)[0] + ".md")
        tasks.append(_transcribe_page(pool, image_path, output_path, PROMPT,
                                      model_name, max_retries=LLM_MAX_RETRIES, log_fn=log_fn,
                                      note_timeout=note_timeout))

    try:
        results = await async_gather_with_progress(
            tasks, len(tasks), "Transcribing Pages",
            pass_name="pass_2", callback=callback,
            unit="page", mininterval=0.5,
        )
        successful = [r for r in results if r is not None]

        if checkpoint_manager and checkpoint_filename:
            checkpoint_manager.mark_done(checkpoint_filename, "pass_2")

        return successful
    finally:
        if _owns_pool:
            await client.close()


def pass_2(pages_dir, max_concurrent, output_directory=None, model_name="local_model",
           checkpoint_manager=None, checkpoint_filename=None):
    """
    Pass 2: Visual-to-Markdown Transcription.

    Sends each processed page image (with red placeholder text) to the VLM and
    writes the resulting Markdown to the output directory.
    """
    if checkpoint_manager and checkpoint_filename and \
            checkpoint_manager.is_done(checkpoint_filename, "pass_2"):
        return []

    markdown_dir = (
        os.path.join(output_directory, "Markdown_Pages") if output_directory
        else os.path.join(pages_dir, "Markdown_Pages")
    )
    # Preserve existing files on resume — do not wipe the directory
    os.makedirs(markdown_dir, exist_ok=True)

    try:
        page_images = sorted(
            [f for f in os.listdir(pages_dir) if f.lower().endswith(('.jpg', '.jpeg', '.png'))],
            key=lambda n: int(m.group(1)) if (m := re.search(r'page(\d+)', n)) else 0,
        )
    except FileNotFoundError:
        return []

    if not page_images:
        return []

    return asyncio.run(_run_pass_2(
        pages_dir, markdown_dir, page_images, max_concurrent, model_name,
        checkpoint_manager, checkpoint_filename,
    ))