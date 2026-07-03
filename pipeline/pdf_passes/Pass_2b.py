import os
import re
import base64
import asyncio
import aiofiles
from tqdm.asyncio import tqdm
from openai import AsyncOpenAI
from core.progress import async_gather_with_progress, iter_with_progress, make_log_fn
from core.md_utils import normalize_image_placeholders
from core.node_pool import NodePool
from core.timeout_cache import is_timeout_error, make_timeout_noter
from core.config import LLM_BASE_URL, LLM_API_KEY, LLM_TIMEOUT, LLM_MAX_RETRIES


CAPTION_PROMPT = """
You are a technical document analyst. The image shows a figure extracted from a technical manual.

Write a concise description (1-3 sentences) of what this figure shows. Focus on:
- What type of visual it is (diagram, photograph, illustration, schematic, chart, graph, etc.)
- What it depicts or demonstrates
- Key labels, dimensions, or values if clearly visible

Output ONLY the description. No introductory text, no bullet points, no formatting.
"""


async def _encode_image(image_path):
    async with aiofiles.open(image_path, "rb") as f:
        content = await f.read()
    return base64.b64encode(content).decode('utf-8')


async def _caption_figure(pool, image_path, model_name, max_retries=3,
                          log_fn=None, note_timeout=None):
    """Send a single cropped figure to the VLM and return its caption."""
    async with pool.slot() as client:
        b64 = await _encode_image(image_path)
        last_exc = None
        for attempt in range(max_retries):
            try:
                response = await client.chat.completions.create(
                    model=model_name,
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                            {"type": "text",      "text": CAPTION_PROMPT},
                        ],
                    }],
                )
                return image_path, (response.choices[0].message.content or "").strip()
            except Exception as e:
                last_exc = e
                if attempt < max_retries - 1:
                    wait = 2 ** attempt
                    _log = log_fn or tqdm.write
                    _log(
                        f"  Retry {attempt + 1}/{max_retries - 1} captioning "
                        f"{os.path.basename(image_path)} in {wait}s — {e}"
                    )
                    await asyncio.sleep(wait)

        _log = log_fn or tqdm.write
        _log(f"Error captioning {os.path.basename(image_path)}: {last_exc}")
        if note_timeout and is_timeout_error(last_exc):
            note_timeout()
        return image_path, None


async def _run_pass_2b(cropped_figure_dir, md_files, markdown_dir, output_directory,
                       max_concurrent, model_name, checkpoint_manager, checkpoint_filename,
                       pool=None, callback=None, timeout_registry=None):
    """
    Core async logic:
      Step 1 — Caption every unique figure image referenced in the Markdown files.
      Step 2 — Inject captions as alt text and as a caption paragraph below each figure tag.

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

    image_tag_pattern = r'!\[([^\]]*)\]\(([^)]+)\)'

    try:
        # -------------------------------------------------------------------------
        # Step 1: Collect unique figure paths referenced across all Markdown files
        # -------------------------------------------------------------------------
        file_contents        = {}
        unique_figure_images = set()

        for md_file in md_files:
            file_path = os.path.join(markdown_dir, md_file)
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            # Heal bare "[Figures/x.jpg]" / "[Tables/x.jpg]" placeholders the VLM
            # failed to convert so figures can be matched and captioned.
            content = normalize_image_placeholders(content)
            file_contents[md_file] = content

            for _, img_path in re.findall(image_tag_pattern, content):
                if 'Figures' not in img_path:
                    continue
                img_filename = os.path.basename(img_path.replace('\\', '/'))
                true_path    = os.path.join(cropped_figure_dir, img_filename)
                if os.path.exists(true_path):
                    unique_figure_images.add(true_path)
                else:
                    log_fn(f"Warning: figure image not found: {true_path}  (referenced in {md_file})")

        if not unique_figure_images:
            # No figures to caption — copy Markdown files to output unchanged
            for md_file in md_files:
                output_path = os.path.join(output_directory, md_file)
                with open(output_path, 'w', encoding='utf-8') as f:
                    f.write(file_contents[md_file])
            if checkpoint_manager and checkpoint_filename:
                checkpoint_manager.mark_done(checkpoint_filename, "pass_2b")
            return []

        # -------------------------------------------------------------------------
        # Step 2: Caption all unique figures in parallel
        # -------------------------------------------------------------------------
        note_timeout = make_timeout_noter(timeout_registry, checkpoint_filename, "pass_2b")
        tasks = [
            _caption_figure(pool, img_path, model_name,
                            max_retries=LLM_MAX_RETRIES, log_fn=log_fn,
                            note_timeout=note_timeout)
            for img_path in unique_figure_images
        ]

        results = await async_gather_with_progress(
            tasks, len(tasks), "Captioning Figures",
            pass_name="pass_2b", callback=callback,
            unit="figure", mininterval=0.5,
        )

        # Map basename → caption for quick lookup during injection
        captions: dict[str, str] = {}
        for img_path, caption in results:
            if caption is not None:
                captions[os.path.basename(img_path)] = caption

        # -------------------------------------------------------------------------
        # Step 3: Inject captions into each Markdown file
        # -------------------------------------------------------------------------
        processed_files = []

        for md_file in iter_with_progress(
            md_files, "Injecting Captions",
            pass_name="pass_2b", callback=callback,
            unit="file", mininterval=0.3,
        ):
            content = file_contents[md_file]

            def match_replacer(match):
                img_path = match.group(2)
                if 'Figures' not in img_path:
                    return match.group(0)
                img_filename = os.path.basename(img_path.replace('\\', '/'))
                caption = captions.get(img_filename)
                if caption is None:
                    return match.group(0)
                # Embed caption as alt text and add a caption line below so the
                # description is also present as plain text for RAG chunking.
                return f"![{caption}]({img_path})\n*{caption}*"

            new_content = re.sub(image_tag_pattern, match_replacer, content)

            output_path = os.path.join(output_directory, md_file)
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(new_content)

            processed_files.append(output_path)

        if checkpoint_manager and checkpoint_filename:
            checkpoint_manager.mark_done(checkpoint_filename, "pass_2b")

        return processed_files

    finally:
        if _owns_pool:
            await client.close()


def pass_2b(markdown_dir, cropped_figure_dir, max_concurrent, output_directory=None,
            model_name="local_model", checkpoint_manager=None, checkpoint_filename=None):
    """
    Pass 2b: Figure Captioning.

    Sends each cropped figure image to the VLM to generate a concise description,
    then injects the caption as alt text and a plain-text caption line into the
    Pass 2 Markdown files so figures are searchable in the RAG index.
    """
    if checkpoint_manager and checkpoint_filename and \
            checkpoint_manager.is_done(checkpoint_filename, "pass_2b"):
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

    return asyncio.run(_run_pass_2b(
        cropped_figure_dir, md_files, markdown_dir, output_directory,
        max_concurrent, model_name, checkpoint_manager, checkpoint_filename,
    ))
