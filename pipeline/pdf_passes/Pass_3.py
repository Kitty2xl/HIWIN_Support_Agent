import os
import re
import base64
import asyncio
import cv2
import aiofiles
import numpy as np
from tqdm.asyncio import tqdm
from openai import AsyncOpenAI
from core.progress import async_gather_with_progress, iter_with_progress, make_log_fn
from core.md_utils import normalize_image_placeholders
from core.node_pool import NodePool
from core.timeout_cache import is_timeout_error, make_timeout_noter
from core.config import LLM_BASE_URL, LLM_API_KEY, LLM_TIMEOUT


async def _encode_image(image_path):
    """Encode an image file to a base64 string asynchronously."""
    async with aiofiles.open(image_path, "rb") as f:
        content = await f.read()
    return base64.b64encode(content).decode('utf-8')


def _crop_sub_images(markdown_content, image_path):
    """
    Scan the transcribed table for <!-- Image (x1, y1, x2, y2) --> comments,
    crop each referenced region from the source image, save it to disk alongside
    the original table crop, and replace the comment with a Markdown image tag.

    Runs synchronously so it can be safely dispatched to a thread pool.
    """
    pattern = r"<!--\s*Image\s*\((\d+),\s*(\d+),\s*(\d+),\s*(\d+)\)\s*-->"
    matches = list(re.finditer(pattern, markdown_content))
    if not matches:
        return markdown_content

    table_img = cv2.imdecode(np.fromfile(image_path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if table_img is None:
        return markdown_content

    table_dir  = os.path.dirname(image_path)
    table_base = os.path.splitext(os.path.basename(image_path))[0]
    h, w       = table_img.shape[:2]

    parts       = []
    last_end    = 0
    sub_counter = 1

    for match in matches:
        x1, y1, x2, y2 = map(int, match.groups())
        cx1, cy1 = max(0, x1), max(0, y1)
        cx2, cy2 = min(w, x2), min(h, y2)

        parts.append(markdown_content[last_end:match.start()])

        if cx2 > cx1 and cy2 > cy1:
            sub_img      = table_img[cy1:cy2, cx1:cx2]
            sub_filename = f"{table_base}_sub_{sub_counter}.jpg"
            sub_path     = os.path.abspath(os.path.join(table_dir, sub_filename))
            cv2.imencode('.jpg', sub_img)[1].tofile(sub_path)
            parts.append(f"![Nested Image](Tables/{sub_filename})")
            sub_counter += 1
        else:
            parts.append(match.group(0))  # Fallback: keep original comment if coords are invalid

        last_end = match.end()

    parts.append(markdown_content[last_end:])
    return ''.join(parts)


async def _transcribe_table(pool, image_path, system_prompt, model_name,
                            log_fn=None, note_timeout=None):
    """Transcribe a single table image to a Markdown table."""
    async with pool.slot() as client:
        base64_image = await _encode_image(image_path)
        try:
            response = await client.chat.completions.create(
                model=model_name,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}},
                        {"type": "text",      "text": system_prompt},
                    ],
                }],
            )
            markdown_content = (response.choices[0].message.content or "").strip()

            # Dispatch sub-image cropping to a thread to avoid blocking the event loop
            loop             = asyncio.get_running_loop()
            markdown_content = await loop.run_in_executor(
                None, _crop_sub_images, markdown_content, image_path
            )

            return image_path, markdown_content
        except Exception as e:
            _log = log_fn or tqdm.write
            _log(f"Error transcribing table {os.path.basename(image_path)}: {e}")
            if note_timeout and is_timeout_error(e):
                note_timeout()
            return image_path, None


async def _run_pass_3(md_files, markdown_dir, output_directory, max_concurrent, model_name,
                      checkpoint_manager, checkpoint_filename, pool=None, callback=None,
                      timeout_registry=None):
    """
    Core async logic:
      Step 1 — Collect all unique table image paths referenced across all Markdown files.
      Step 2 — Transcribe every unique table in parallel.
      Step 3 — Inject the transcribed tables back into the per-page Markdown files.

    If `pool` is provided it is used as-is and the caller owns its clients'
    lifecycle (nothing is closed here); requests spread across every node.
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

    SYSTEM_PROMPT = """
    Extract the table from the provided image and output it as a valid Markdown table.

    1. Map all columns, rows, and header structures before transcribing.
    2. Transcribe every cell exactly — do not fix or guess missing data.
    3. If a cell contains a sub-image, output: <!-- Image (xmin, ymin, xmax, ymax) -->. Do not describe it.
    4. Use | to separate columns and - for the header separator row.
    5. Represent blank cells as |   |. Never skip columns.

    Output ONLY the raw Markdown table. No introduction, no explanation, no code block wrappers.
    """

    image_tag_pattern = r'!\[([^\]]*)\]\(([^)]+)\)'

    # Resolve Cropped_Table from the Pass 1 output directory, which sits two levels
    # above the Markdown_Pages directory passed in as markdown_dir.
    cropped_table_dir = os.path.join(
        os.path.dirname(os.path.dirname(markdown_dir)), 'Pass_1', 'Cropped_Table'
    )

    # -------------------------------------------------------------------------
    # Step 1: Collect all unique table image paths across all Markdown files
    # -------------------------------------------------------------------------
    unique_table_images = set()
    file_contents       = {}

    for md_file in md_files:
        file_path = os.path.join(markdown_dir, md_file)
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        # Heal any bare "[Tables/x.jpg]" placeholders the VLM failed to convert,
        # so existing output is fixed on re-run without redoing Pass 2.
        content = normalize_image_placeholders(content)
        file_contents[md_file] = content

        for _, img_path in re.findall(image_tag_pattern, content):
            if 'Tables' not in img_path:
                continue
            img_filename = os.path.basename(img_path.replace('\\', '/')).replace('-', '_')
            true_path    = os.path.join(cropped_table_dir, img_filename)
            if os.path.exists(true_path):
                unique_table_images.add(true_path)
            else:
                log_fn(f"Warning: table image not found: {true_path}  (referenced in {md_file})")

    # -------------------------------------------------------------------------
    # Step 2: Transcribe all unique tables in parallel
    # -------------------------------------------------------------------------
    note_timeout = make_timeout_noter(timeout_registry, checkpoint_filename, "pass_3")
    tasks = [
        _transcribe_table(pool, img_path, SYSTEM_PROMPT, model_name,
                          log_fn=log_fn, note_timeout=note_timeout)
        for img_path in unique_table_images
    ]

    try:
        transcription_results = {}

        if tasks:
            results = await async_gather_with_progress(
                tasks, len(tasks), "Transcribing Tables",
                pass_name="pass_3", callback=callback,
                unit="table", mininterval=0.5,
            )
            # FIX (remove CSV): unpack only 2-tuple; csv_results removed entirely.
            for img_path, md_table in results:
                if md_table is not None:
                    transcription_results[img_path] = md_table

        # -------------------------------------------------------------------------
        # Step 3: Inject transcribed tables back into each Markdown file
        # -------------------------------------------------------------------------
        processed_files = []

        for md_file in iter_with_progress(
            md_files, "Injecting Tables",
            pass_name="pass_3", callback=callback,
            unit="file", mininterval=0.3,
        ):

            content = file_contents[md_file]

            def match_replacer(match):
                img_path     = match.group(2)
                if 'Tables' not in img_path:
                    return match.group(0)
                # FIX (Bug 1 + remove CSV): apply the same hyphen→underscore normalisation
                # used when building transcription_results, then inline the markdown table
                # directly instead of linking to a (now-removed) CSV file.
                img_filename = os.path.basename(img_path.replace('\\', '/')).replace('-', '_')
                true_path    = os.path.join(cropped_table_dir, img_filename)
                if true_path in transcription_results:
                    return transcription_results[true_path]
                return match.group(0)

            new_content = re.sub(image_tag_pattern, match_replacer, content)

            output_path = os.path.join(output_directory, md_file)
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(new_content)

            processed_files.append(output_path)

        if checkpoint_manager and checkpoint_filename:
            checkpoint_manager.mark_done(checkpoint_filename, "pass_3")

        return processed_files
    finally:
        if _owns_pool:
            await client.close()


def pass_3(markdown_dir, max_concurrent, output_directory=None, model_name="local_model",
           checkpoint_manager=None, checkpoint_filename=None):
    """
    Pass 3: Table Extraction & Injection.

    Finds all table image references in the Pass 2 Markdown files, transcribes each
    table via OCR, and injects the resulting Markdown tables back in place of the
    image tags.
    """
    if checkpoint_manager and checkpoint_filename and \
            checkpoint_manager.is_done(checkpoint_filename, "pass_3"):
        return []

    if output_directory:
        # Preserve existing files on resume — do not wipe the directory
        os.makedirs(output_directory, exist_ok=True)
    else:
        output_directory = markdown_dir

    try:
        md_files = sorted(f for f in os.listdir(markdown_dir) if f.lower().endswith('.md'))
    except FileNotFoundError:
        return []

    if not md_files:
        return []

    return asyncio.run(_run_pass_3(
        md_files, markdown_dir, output_directory, max_concurrent, model_name,
        checkpoint_manager, checkpoint_filename,
    ))
