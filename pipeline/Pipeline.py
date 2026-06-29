import os
import re
import asyncio
import shutil
import yaml
import httpx
import threading
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

logging.getLogger("httpx").setLevel(logging.WARNING)

from pdf_passes.Pass_1 import pass_1
from pdf_passes.Pass_4 import pass_4, pass_4_async
from core.checkpoint_manager import CheckpointManager
from core.timeout_cache import TimeoutRegistry, LLM_PASS_ORDER
from core.fs_utils import to_long_path
from core.config import (
    MODEL_PATH_PASS_1, SCORE_THRESHOLD,
    MODEL_PASS_2, MODEL_PASS_2B, MODEL_PASS_3, MODEL_PASS_3B, MODEL_PASS_4,
    LLAMA_SWAP_URL, PASS34_NODE_SWAP_URLS,
    BATCH_SIZE_PASS_1, ORT_INTRA_THREADS, PASS_1_RENDER_DPI,
    LLM_BASE_URL, PASS34_NODE_URLS, LLM_API_KEY, LLM_TIMEOUT, LLM_PREHEAT_TIMEOUT,
    ROOT_PATH, PDF_CONFIG_PATH, PROCESS_ROOT, FINAL_OUTPUT_ROOT,
    CONCURRENCY, CONCURRENCY_PASS_4, TIMEOUT_RETRY_ENABLED, MAX_PDF_WORKERS,
    DEFAULT_LANGUAGE,
)

# Fallback lock used only in CLI mode (no shared pool).  When run_pipeline is
# called normally a single shared NodePool replaces this, spreading every
# page/figure/table request across all Pass34 nodes (page-level work-stealing).
_gpu_lock = threading.Lock()


def unload_model(model_name, swap_url=LLAMA_SWAP_URL):
    """Unload a model from VRAM via the llama-swap API."""
    try:
        response = httpx.post(
            f"{swap_url}/api/models/unload/{model_name}", timeout=100000.0
        )
        if response.status_code == 200:
            print(f"  Unloaded: {model_name}")
        else:
            print(f"  Failed to unload {model_name} (HTTP {response.status_code})")
        return response
    except httpx.RequestError as e:
        print(f"  Error unloading {model_name}: {e}")
        return None


def _parse_page_spec(spec):
    """
    Normalise a page selection from the YAML into a set of 0-indexed page nums.

    Accepts a list, or a JSON-encoded string (e.g. "[0, 1, 5]").  Missing or
    unparseable values yield an empty set.
    """
    if isinstance(spec, str):
        spec = spec.strip()
        try:
            spec = json.loads(spec) if spec else []
        except json.JSONDecodeError:
            spec = []
    return set(spec or [])


def get_pdf_tasks(config_dict, current_path=None):
    """
    Recursively traverse the YAML config to find leaf nodes that contain a
    'language' plus page-selection info, yielding one task tuple per PDF:
        (path_list, key, language, pages_to_exclude, pages_to_include)

    A leaf qualifies if it has 'language' and either 'pages_to_exclude' or
    'pages_to_include'.  When 'pages_to_include' is present and non-empty it
    takes precedence — only those pages are processed and 'pages_to_exclude'
    is ignored (handled downstream in pass_1).
    """
    if current_path is None:
        current_path = []
    for key, value in config_dict.items():
        if isinstance(value, dict):
            if 'language' in value and \
                    ('pages_to_exclude' in value or 'pages_to_include' in value):
                yield (current_path + [key], key, value['language'],
                       value.get('pages_to_exclude', []),
                       value.get('pages_to_include', []))
            else:
                yield from get_pdf_tasks(value, current_path + [key])


def _detect_language(title: str, default: str) -> str:
    """Guess a language code (en / jp / tc) from a PDF's filename ('title').

    1. Japanese kana present       -> 'jp'
    2. Han / CJK ideographs present -> 'tc' (Traditional Chinese — the only Chinese
       skill here). NOTE: a kanji-only Japanese title can't be distinguished from
       Chinese this way, so it lands on 'tc' — fix it in the generated file.
    3. Otherwise (all-ASCII), look for a language keycode token in the name
       (en / jp / zh / zh-tw / zh-cn / tc …).
    4. Fall back to *default*.
    """
    if re.search(r"[぀-ヿ]", title):     # hiragana/katakana
        return "jp"
    if re.search(r"[一-鿿]", title):     # CJK ideographs
        return "tc"
    low = title.lower()
    if re.search(r"(?<![a-z])(jp|ja|jpn|japanese)(?![a-z])", low):
        return "jp"
    if re.search(r"(?<![a-z])(tc|tw|zh[-_]?tw|zh[-_]?hant|traditional)(?![a-z])", low):
        return "tc"
    if re.search(r"(?<![a-z])(zh|cn|zh[-_]?cn|zh[-_]?ch|chinese|simplified)(?![a-z])", low):
        return "tc"        # only Traditional Chinese is supported -> map any Chinese to tc
    if re.search(r"(?<![a-z])(en|eng|english)(?![a-z])", low):
        return "en"
    return default


def _generate_default_config(pdfs_root: str, config_path: str,
                             default_language: str = "tc") -> dict:
    """Build a PDF_Config.yaml by scanning *pdfs_root* when none exists yet.

    Mirrors the on-disk layout (PDFs/<product>/<sub...>/<file>.pdf) into a nested
    dict. Each PDF's language is auto-detected from its filename (see
    _detect_language); 'pages_to_exclude' is left empty so EVERY page is ingested.
    Writes the file (with a 'review & edit before running' header) to *config_path*
    and returns the dict. An empty/missing PDFs/ folder yields an empty config.
    """
    config: dict = {}
    if os.path.isdir(pdfs_root):
        for dirpath, _dirs, files in os.walk(pdfs_root):
            for fn in sorted(files):
                if not fn.lower().endswith(".pdf"):
                    continue
                rel = os.path.relpath(os.path.join(dirpath, fn), pdfs_root)
                node = config
                for part in rel.split(os.sep)[:-1]:        # product / sub-folders
                    node = node.setdefault(part, {})
                fname = os.path.basename(rel)
                node[fname] = {
                    "language": _detect_language(os.path.splitext(fname)[0], default_language),
                    "pages_to_exclude": [],
                }
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(
                "# AUTO-GENERATED from the PDFs/ folder. REVIEW & EDIT BEFORE RUNNING:\n"
                "#   * 'language' was guessed from each filename (en/jp/tc) - verify it.\n"
                "#   * 'pages_to_exclude: []' means EVERY page is ingested; add page\n"
                "#     numbers (0-indexed) to skip covers/blank pages.\n"
                "# Then run the pipeline again to process these PDFs.\n\n"
            )
            yaml.safe_dump(config, f, allow_unicode=True, sort_keys=True)
    except OSError:
        pass
    return config


class _PersistentAsyncRunner:
    """
    Owns a single background event loop and a single AsyncOpenAI client that
    live for the lifetime of an entire pipeline phase.

    Worker threads call .run(coro) to execute coroutines on that loop without
    ever creating or closing their own event loop or client — so llama-swap
    sees one continuous connection and never idles long enough to unload the
    model between documents.

    Usage:
        runner = _PersistentAsyncRunner(base_url, api_key, timeout)
        runner.run(some_coroutine(..., client=runner.client))
        runner.close()
    """

    def __init__(self, base_url: str, api_key: str, timeout: int, concurrency: int = 0):
        from openai import AsyncOpenAI

        self._loop   = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()

        # Create the client and a shared semaphore inside the loop so both are
        # bound to the right loop.  The semaphore caps total Pass 3b/4 requests to
        # the local model across *all* documents running on this loop — without it
        # each document gets its own budget, so the local node sees
        # concurrency × (overlapping documents) requests and returns 429s.
        async def _mk():
            client = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=timeout)
            semaphore = asyncio.Semaphore(concurrency) if concurrency else None
            return client, semaphore

        self.client, self.semaphore = asyncio.run_coroutine_threadsafe(
            _mk(), self._loop).result()

    def run(self, coro):
        """Submit *coro* to the persistent loop and block until it completes."""
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def close(self):
        """Close the client and stop the event loop."""
        async def _shutdown():
            await self.client.close()

        asyncio.run_coroutine_threadsafe(_shutdown(), self._loop).result()
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)


class _MultiNodePool:
    """
    A persistent background event loop that owns one AsyncOpenAI client per
    Pass34 node plus a shared NodePool spread across all of them.

    Worker threads submit a PDF's Pass 2/2b/3 coroutine via .run(); inside it
    every page, figure and table request draws from the shared NodePool, so a
    single PDF fans its work out across all nodes and multiple in-flight PDFs
    share the same pool.  Replaces the older one-node-per-PDF queue, which
    pinned a whole PDF to a single node.

    Total simultaneous Pass34 requests = node_count * concurrency.
    """

    def __init__(self, base_urls, api_key, timeout, concurrency):
        from openai import AsyncOpenAI
        from core.node_pool import NodePool

        self.node_count = len(base_urls)
        self._loop   = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()

        # Build the clients and pool inside the loop so they are bound to it.
        async def _mk():
            clients = [
                AsyncOpenAI(base_url=url, api_key=api_key, timeout=timeout)
                for url in base_urls
            ]
            return clients, NodePool(clients, concurrency, labels=base_urls)

        self._clients, self.pool = asyncio.run_coroutine_threadsafe(
            _mk(), self._loop
        ).result()

    def run(self, coro):
        """Submit *coro* to the persistent loop and block until it completes."""
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def close(self):
        """Close every node client and stop the event loop."""
        async def _shutdown():
            for client in self._clients:
                await client.close()

        asyncio.run_coroutine_threadsafe(_shutdown(), self._loop).result()
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)


async def _combined_pass_2_2b_3(
    processed_pages_dir, cropped_figure_dir, concurrency,
    pass_2_dir, pass_2b_dir, pass_3_dir,
    model_name_2, model_name_2b, model_name_3,
    checkpoint_manager, checkpoint_filename,
    progress_callback=None,
    pool=None,
    timeout_registry=None,
):
    """
    Run Passes 2, 2b, and 3.

    Pass 2  — page image → Markdown (one file per page)
    Pass 2b — caption each cropped figure, inject into Pass 2 Markdown
    Pass 3  — table image → Markdown table, inject into Pass 2b Markdown

    If *pool* is provided the caller owns the underlying clients' lifecycle and
    nothing is closed here — this is the normal path when called via
    _MultiNodePool, which spreads every page/figure/table request across all
    Pass34 nodes so a single PDF fans out over every GPU.
    """
    from openai import AsyncOpenAI
    from core.node_pool import NodePool
    from pdf_passes.Pass_2  import _run_pass_2
    from pdf_passes.Pass_2b import _run_pass_2b
    from pdf_passes.Pass_3  import _run_pass_3

    cb = progress_callback  # shorthand

    def _emit(event: dict):
        if cb:
            cb(event)

    _owns_pool = pool is None
    client = None
    if _owns_pool:
        client = AsyncOpenAI(
            base_url=PASS34_NODE_URLS[0],   # CLI fallback: single local node
            api_key=LLM_API_KEY,
            timeout=LLM_TIMEOUT,
        )
        pool = NodePool([client], concurrency)
    try:
        # --- Pass 2 ---
        p2_done = checkpoint_manager and checkpoint_filename and \
                  checkpoint_manager.is_done(checkpoint_filename, "pass_2")
        pass_2_md_dir = os.path.join(pass_2_dir, "Markdown_Pages")
        if p2_done:
            _emit({"type": "icon_skip", "pass": "pass_2"})
        else:
            os.makedirs(pass_2_md_dir, exist_ok=True)
            try:
                page_images = sorted(
                    [f for f in os.listdir(processed_pages_dir)
                     if f.lower().endswith(('.jpg', '.jpeg', '.png'))],
                    key=lambda n: int(m.group(1)) if (m := re.search(r'page(\d+)', n)) else 0,
                )
            except FileNotFoundError:
                page_images = []
            if page_images:
                _emit({"type": "icon_start", "pass": "pass_2"})
                await _run_pass_2(
                    processed_pages_dir, pass_2_md_dir, page_images, concurrency,
                    model_name_2, checkpoint_manager, checkpoint_filename,
                    pool=pool, callback=cb, timeout_registry=timeout_registry,
                )
                _emit({"type": "icon_done", "pass": "pass_2"})
            else:
                _emit({"type": "icon_skip", "pass": "pass_2"})

        # --- Pass 2b — figure captioning ---
        pass_2b_md_dir = os.path.join(pass_2b_dir, "Markdown_Pages")
        p2b_done = checkpoint_manager and checkpoint_filename and \
                   checkpoint_manager.is_done(checkpoint_filename, "pass_2b")
        if p2b_done:
            _emit({"type": "icon_skip", "pass": "pass_2b"})
        elif os.path.exists(pass_2_md_dir):
            md_files = sorted(
                f for f in os.listdir(pass_2_md_dir) if f.lower().endswith('.md')
            )
            if md_files:
                os.makedirs(pass_2b_md_dir, exist_ok=True)
                _emit({"type": "icon_start", "pass": "pass_2b"})
                await _run_pass_2b(
                    cropped_figure_dir, md_files, pass_2_md_dir, pass_2b_md_dir,
                    concurrency, model_name_2b, checkpoint_manager, checkpoint_filename,
                    pool=pool, callback=cb, timeout_registry=timeout_registry,
                )
                _emit({"type": "icon_done", "pass": "pass_2b"})
            else:
                _emit({"type": "icon_skip", "pass": "pass_2b"})
        else:
            _emit({"type": "icon_skip", "pass": "pass_2b"})

        # --- Pass 3 — table transcription ---
        p3_done = checkpoint_manager and checkpoint_filename and \
                  checkpoint_manager.is_done(checkpoint_filename, "pass_3")
        source_md_dir = pass_2b_md_dir if os.path.exists(pass_2b_md_dir) else pass_2_md_dir
        if p3_done:
            _emit({"type": "icon_skip", "pass": "pass_3"})
        elif os.path.exists(source_md_dir):
            md_files = sorted(
                f for f in os.listdir(source_md_dir) if f.lower().endswith('.md')
            )
            if md_files:
                os.makedirs(pass_3_dir, exist_ok=True)
                _emit({"type": "icon_start", "pass": "pass_3"})
                await _run_pass_3(
                    md_files, source_md_dir, pass_3_dir, concurrency,
                    model_name_3, checkpoint_manager, checkpoint_filename,
                    pool=pool, callback=cb, timeout_registry=timeout_registry,
                )
                _emit({"type": "icon_done", "pass": "pass_3"})
            else:
                _emit({"type": "icon_skip", "pass": "pass_3"})
        else:
            _emit({"type": "icon_skip", "pass": "pass_3"})
    finally:
        if _owns_pool and client is not None:
            await client.close()


def _dir_map(task, process_root):
    """Return the per-PDF directory paths used across both pipeline phases."""
    p = task['product']
    s = task['sub_folder_name']
    l = task['language']
    base = os.path.join(process_root, p, s, l)
    return {
        'pass_1':   os.path.join(base, 'Pass_1'),
        'pass_2':   os.path.join(base, 'Pass_2'),
        'pass_2b':  os.path.join(base, 'Pass_2b'),
        'pass_3':   os.path.join(base, 'Pass_3'),
        'pass_3b':  os.path.join(base, 'Pass_3b'),
        'pass_4':   os.path.join(base, 'Pass_4'),
    }


def _run_passes_1_to_3(task, checkpoint_manager, process_root, concurrency, pdf_workers,
                       progress_queue=None, phase_a=None, timeout_registry=None):
    """
    Phase A — Passes 1, 2, 2b, 3 for a single PDF.

    Called concurrently across all PDFs.  Pass 1 is CPU-only and runs freely.
    For Passes 2/2b/3 the thread submits the work to the shared _MultiNodePool's
    event loop and blocks until it finishes; inside, every page/figure/table
    request is spread across all Pass34 nodes via the shared NodePool, so a
    single PDF fans out over every GPU and multiple PDFs share the same pool.

    Phase B (Pass 3b/4) is submitted to its own runner as soon as this returns,
    overlapping with other PDFs still in Phase A.
    """
    product    = task['product']
    language   = task['language']
    sub_folder = task['sub_folder_name']
    ckpt       = task['checkpoint_filename']
    label      = f"{product}/{sub_folder}/{language}"
    dirs       = _dir_map(task, process_root)

    # Build a per-PDF callback that tags every event with this PDF's label.
    if progress_queue is not None:
        def callback(event: dict):
            progress_queue.put({**event, "pdf_label": label})
    else:
        callback = None

    os.makedirs(dirs['pass_1'], exist_ok=True)

    # ------------------------------------------------------------------
    # Pass 1 — CPU only; runs freely alongside other PDFs' Pass 1
    # ------------------------------------------------------------------
    if callback:
        p1_done = checkpoint_manager.is_done(ckpt, "pass_1")
        callback({"type": "icon_skip" if p1_done else "icon_start", "pass": "pass_1"})
    else:
        print(f"  [{label}] Pass 1 →")

    pass_1(
        task['pdf_path'],
        task['excluded_pages'],
        dirs['pass_1'],
        model_path=MODEL_PATH_PASS_1,
        score_threshold=SCORE_THRESHOLD,
        batch_size=BATCH_SIZE_PASS_1,
        render_dpi=PASS_1_RENDER_DPI,
        pdf_workers=pdf_workers,
        ort_intra_threads=ORT_INTRA_THREADS,
        checkpoint_manager=checkpoint_manager,
        checkpoint_filename=ckpt,
        progress_callback=callback,
        included_pages=task.get('included_pages'),
    )
    if callback:
        callback({"type": "icon_done", "pass": "pass_1"})

    # ------------------------------------------------------------------
    # Passes 2 / 2b / 3 — GPU (MODEL_PASS_2/3)
    # Claim any free Pass34 node from the pool, run all three passes on it,
    # then release it so another PDF can use it.
    # ------------------------------------------------------------------
    processed_pages_dir = os.path.join(dirs['pass_1'], 'Processed_Page')
    cropped_figure_dir  = os.path.join(dirs['pass_1'], 'Cropped_Figure')

    def _make_coro(pool):
        return _combined_pass_2_2b_3(
            processed_pages_dir, cropped_figure_dir, concurrency,
            dirs['pass_2'], dirs['pass_2b'], dirs['pass_3'],
            MODEL_PASS_2, MODEL_PASS_2B, MODEL_PASS_3,
            checkpoint_manager, ckpt,
            progress_callback=callback,
            pool=pool,
            timeout_registry=timeout_registry,
        )

    if phase_a is not None:
        # Spread this PDF's pages across every node via the shared pool.
        if not callback:
            print(f"  [{label}] Pass 2/2b/3 → (running across {phase_a.node_count} node(s))")
        phase_a.run(_make_coro(phase_a.pool))
    else:
        # CLI fallback: no shared pool — build a single-node pool inside the coro.
        if not callback:
            print(f"  [{label}] Pass 2/2b/3 → (queued for GPU)")
        with _gpu_lock:
            if not callback:
                print(f"  [{label}] Pass 2/2b/3 → (running)")
            asyncio.run(_make_coro(None))

    if not callback:
        print(f"  [{label}] Passes 1-3 complete")
    return task


async def _run_pass_4_coro(task, checkpoint_manager, process_root,
                           progress_queue, stop_event, shared_client,
                           timeout_registry=None, semaphore=None):
    """
    Async coroutine for a single PDF's Pass 4.  Designed to run concurrently
    with other PDFs via asyncio.gather inside Phase B's _PersistentAsyncRunner,
    so all PDFs share one event loop and one HTTP connection to the GPU model.
    """
    product    = task['product']
    language   = task['language']
    sub_folder = task['sub_folder_name']
    ckpt       = task['checkpoint_filename']
    label      = f"{product}/{sub_folder}/{language}"
    dirs       = _dir_map(task, process_root)

    if progress_queue is not None:
        def callback(event: dict):
            progress_queue.put({**event, "pdf_label": label})
    else:
        callback = None

    _result = {
        'product':             product,
        'sub_folder':          sub_folder,
        'language':            language,
        'figures_dir':         os.path.join(dirs['pass_1'], 'Cropped_Figure'),
        'tables_dir':          os.path.join(dirs['pass_1'], 'Cropped_Table'),
        'filename':            task['filename'],
        'checkpoint_filename': ckpt,
    }

    if not os.path.exists(dirs['pass_3']):
        if callback:
            callback({"type": "icon_skip", "pass": "pass_3b"})
            callback({"type": "icon_skip", "pass": "pass_4"})
        else:
            print(f"  [{label}] Pass 3b/4 → SKIP (no Pass_3 output)")
        return _result

    # --- Pass 3b: table summarisation ---
    from pdf_passes.Pass_3b import _run_pass_3b

    p3b_done = checkpoint_manager and checkpoint_manager.is_done(ckpt, "pass_3b")
    if p3b_done:
        if callback:
            callback({"type": "icon_skip", "pass": "pass_3b"})
        else:
            print(f"  [{label}] Pass 3b → SKIP (already done)")
    else:
        try:
            md_files_3b = sorted(
                f for f in os.listdir(dirs['pass_3']) if f.lower().endswith('.md')
            )
        except FileNotFoundError:
            md_files_3b = []

        if md_files_3b:
            os.makedirs(dirs['pass_3b'], exist_ok=True)
            if callback:
                callback({"type": "icon_start", "pass": "pass_3b"})
            else:
                print(f"  [{label}] Pass 3b → (summarising tables)")
            await _run_pass_3b(
                md_files_3b, dirs['pass_3'], dirs['pass_3b'],
                CONCURRENCY_PASS_4, MODEL_PASS_3B,
                checkpoint_manager, ckpt,
                client=shared_client, callback=callback,
                timeout_registry=timeout_registry, semaphore=semaphore,
            )
            if callback:
                callback({"type": "icon_done", "pass": "pass_3b"})
            else:
                print(f"  [{label}] Pass 3b complete")
        else:
            if callback:
                callback({"type": "icon_skip", "pass": "pass_3b"})

    # --- Pass 4: validation & merging ---
    # Prefer Pass 3b output (with table summaries); fall back to Pass 3 if 3b was skipped.
    pass_4_input = dirs['pass_3b'] if os.path.exists(dirs['pass_3b']) else dirs['pass_3']

    squashed_base = f"{product}_{sub_folder}_{language}".replace(' ', '_').lower()
    output_file   = os.path.join(dirs['pass_4'], squashed_base + ".md")

    if callback:
        p4_done = checkpoint_manager.is_done(ckpt, "pass_4")
        callback({"type": "icon_skip" if p4_done else "icon_start", "pass": "pass_4"})
    else:
        print(f"  [{label}] Pass 4 → (running on GPU)")

    await pass_4_async(
        pass_4_input, output_file, MODEL_PASS_4,
        checkpoint_manager=checkpoint_manager,
        checkpoint_filename=ckpt,
        progress_callback=callback,
        concurrency=CONCURRENCY_PASS_4,
        stop_event=stop_event,
        shared_client=shared_client,
        timeout_registry=timeout_registry,
        semaphore=semaphore,
    )

    if callback:
        callback({"type": "icon_done", "pass": "pass_4"})
    else:
        print(f"  [{label}] Pass 4 complete")
    return _result




def _preheat_model(model_name: str, emit_fn=None, base_url=LLM_BASE_URL):
    """
    Send a minimal request to llama-swap so it loads *model_name* into VRAM
    before real processing begins.  Called once per phase so every document
    in that phase skips the cold-load penalty entirely.

    Pass base_url=PASS34_NODE_URLS[n] for a specific Pass34 node or leave
    as the default LLM_BASE_URL for Pass5Ingest (local machine).
    """
    try:
        from openai import OpenAI
        client = OpenAI(
            base_url=base_url,
            api_key=LLM_API_KEY,
            timeout=LLM_PREHEAT_TIMEOUT,
        )
        client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": "Hi"}],
            max_tokens=1,
        )
        if emit_fn:
            emit_fn({"type": "log", "level": "info",
                     "message": f"Model pre-warmed: {model_name}"})
        else:
            print(f"  Pre-warmed: {model_name}")
    except Exception as e:
        msg = f"Pre-warm failed for {model_name}: {e} (continuing anyway)"
        if emit_fn:
            emit_fn({"type": "log", "level": "warning", "message": msg})
        else:
            print(f"  Warning: {msg}")


def _run_timeout_retry_phase(timeout_registry, pdf_tasks, checkpoint_manager,
                             progress_queue, stop_event, emit_fn):
    """
    Re-run the passes that timed out, still within the same pipeline run.

    For every document with recorded timeouts, reset its checkpoint from the
    earliest timed-out LLM pass downward — later passes consumed the missing
    output, so they must be rebuilt too — then re-run that document through the
    normal Phase A + Phase B machinery.  Each pass's per-item resume checks mean
    only the missing/failed items are actually redone.

    Runs a single retry sweep: anything that times out *again* is re-recorded and
    reported, but not retried a second time (the persisted cache lets a future
    run pick it up).  Returns the list of Phase B result dicts produced here so
    the caller can refresh pdf_infos for the organise/ingest phases.
    """
    timed_out = timeout_registry.docs()              # {ckpt: {pass_names}}
    if not timed_out:
        return []

    task_by_ckpt = {t['checkpoint_filename']: t for t in pdf_tasks}
    retry_tasks  = []                                # (task, earliest_pass)
    for ckpt, passes in timed_out.items():
        task = task_by_ckpt.get(ckpt)
        if task is None:
            continue
        earliest = next((p for p in LLM_PASS_ORDER if p in passes), None)
        if earliest is None:
            continue
        # Clear the earliest timed-out pass and everything after it so they re-run.
        for p in LLM_PASS_ORDER[LLM_PASS_ORDER.index(earliest):]:
            checkpoint_manager.reset_pass(ckpt, p)
        retry_tasks.append((task, earliest))

    if not retry_tasks:
        return []

    labels = ", ".join(
        f"{t['product']}/{t['sub_folder_name']}/{t['language']} (from {e})"
        for t, e in retry_tasks
    )
    emit_fn({"type": "log", "level": "warning",
             "message": f"Timeout retry — re-running {len(retry_tasks)} document(s): {labels}"})
    if progress_queue is None:
        print(f"\n--- Retry phase: {len(retry_tasks)} document(s) had timeouts ---")

    # Forget the recorded timeouts for these docs; any that time out again during
    # the retry will be freshly re-recorded.
    tasks_only = [t for t, _e in retry_tasks]
    for task in tasks_only:
        timeout_registry.clear_doc(task['checkpoint_filename'])

    retry_pool   = _MultiNodePool(PASS34_NODE_URLS, LLM_API_KEY, LLM_TIMEOUT, CONCURRENCY)
    retry_runner = _PersistentAsyncRunner(LLM_BASE_URL, LLM_API_KEY, LLM_TIMEOUT,
                                          CONCURRENCY_PASS_4)
    retry_infos  = []
    retry_workers = MAX_PDF_WORKERS or (os.cpu_count() or 4)
    retry_workers = max(1, min(retry_workers, len(tasks_only)))
    try:
        with ThreadPoolExecutor(max_workers=retry_workers) as executor:
            fut_to_task = {
                executor.submit(
                    _run_passes_1_to_3, task, checkpoint_manager, PROCESS_ROOT,
                    CONCURRENCY, retry_workers, progress_queue, retry_pool,
                    timeout_registry,
                ): task
                for task in tasks_only
            }
            b_futs = []
            for future in as_completed(fut_to_task):
                task = fut_to_task[future]
                try:
                    future.result()
                    b_fut = asyncio.run_coroutine_threadsafe(
                        _run_pass_4_coro(task, checkpoint_manager, PROCESS_ROOT,
                                         progress_queue, stop_event, retry_runner.client,
                                         timeout_registry, retry_runner.semaphore),
                        retry_runner._loop,
                    )
                    b_futs.append((task, b_fut))
                except Exception as exc:
                    emit_fn({"type": "log", "level": "error",
                             "message": f"{task['filename']} retry Phase A failed: {exc}"})
            for task, b_fut in b_futs:
                try:
                    retry_infos.append(b_fut.result())
                except Exception as exc:
                    emit_fn({"type": "log", "level": "error",
                             "message": f"{task['filename']} retry Phase B failed: {exc}"})
    finally:
        retry_pool.close()
        retry_runner.close()

    still = timeout_registry.docs()
    if still:
        emit_fn({"type": "log", "level": "warning",
                 "message": f"{len(still)} document(s) still timed out after the retry "
                            f"sweep — left in the cache for a future run."})

    return retry_infos


def _apply_user_settings(emit_fn=None):
    """
    Overlay settings.json onto core.config, then refresh this module's imported
    config globals from it.  Pipeline did `from core.config import X` at import
    time, so those names are bound copies; re-pulling every UPPERCASE config name
    here lets GUI/CLI edits take effect on the next run without restarting.
    """
    try:
        from core import settings as _settings
        _settings.apply(_settings.load())
        import core.config as _cfg
        g = globals()
        for name in dir(_cfg):
            if name.isupper() and name in g:
                g[name] = getattr(_cfg, name)
    except Exception as exc:  # never let settings issues abort a run
        if emit_fn:
            emit_fn({"type": "log", "level": "warning",
                     "message": f"Could not apply settings.json: {exc}"})


def run_pipeline(progress_queue=None, stop_event=None):
    """
    Execute the full pipeline (Phases A, B, 5, 6, 7).

    progress_queue — optional queue.Queue; when provided, structured progress
                     events are posted to it instead of printing to stdout.
                     Pass None for normal CLI / tqdm output.
    stop_event     — optional threading.Event; set it from another thread to
                     request a graceful stop at the next phase boundary.
    """

    def _emit(event: dict):
        if progress_queue is not None:
            progress_queue.put(event)

    def _stopped() -> bool:
        return stop_event is not None and stop_event.is_set()

    # Pick up any settings.json edits made in the GUI before this run.
    _apply_user_settings(_emit)

    if not os.path.exists(PDF_CONFIG_PATH):
        # No config yet: generate one from the PDFs folder, then STOP so the user
        # can review/edit it (especially the auto-detected languages) before any
        # processing happens. The next run picks up the edited file and proceeds.
        pdfs_root = os.path.join(ROOT_PATH, "PDFs")
        config = _generate_default_config(pdfs_root, PDF_CONFIG_PATH, DEFAULT_LANGUAGE)
        n = sum(1 for _ in get_pdf_tasks(config))
        msg = (f"Generated PDF_Config.yaml at {PDF_CONFIG_PATH} from {n} PDF(s) "
               f"(every page; language auto-detected per filename). REVIEW and EDIT "
               f"it (languages / pages to exclude), then run again.")
        if progress_queue is None:
            print("\n" + msg + "\n")
        else:
            _emit({"type": "log", "level": "warning", "message": msg})
            _emit({"type": "pipeline_error", "message": msg})
        return

    with open(PDF_CONFIG_PATH, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f) or {}

    pdf_tasks = []
    for path_list, filename, language, excluded_pages, included_pages in get_pdf_tasks(config):
        pdf_file_path   = os.path.join(ROOT_PATH, 'PDFs', *path_list[:-1], filename)
        product         = path_list[0]
        sub_folder_name = '_'.join([os.path.splitext(p)[0] for p in path_list[1:]]).lower()

        excluded_set = _parse_page_spec(excluded_pages)
        included_set = _parse_page_spec(included_pages)

        checkpoint_filename = f"{product}__{sub_folder_name}__{os.path.splitext(filename)[0]}"
        pdf_tasks.append({
            'pdf_path':            pdf_file_path,
            'filename':            filename,
            'product':             product,
            'sub_folder_name':     sub_folder_name,
            'language':            language,
            'excluded_pages':      excluded_set,
            'included_pages':      included_set,   # non-empty → overrides excluded
            'path_list':           path_list,
            'checkpoint_filename': checkpoint_filename,
        })

    if not pdf_tasks:
        _emit({"type": "pipeline_error", "message": "No PDF tasks found in config."})
        return

    checkpoint_file    = os.path.join(ROOT_PATH, 'checkpoint.json')
    checkpoint_manager = CheckpointManager(checkpoint_file)

    # Records (document, pass) pairs whose LLM calls timed out, so the retry
    # phase after Phase B can re-run just the affected passes.
    timeout_registry = TimeoutRegistry(os.path.join(ROOT_PATH, 'timeout_retries.json'))

    _emit({"type": "pipeline_start",
           "pdf_labels": [f"{t['product']}/{t['sub_folder_name']}/{t['language']}"
                          for t in pdf_tasks]})

    if progress_queue is None:
        print("\n" + "=" * 70)
        print(f"Pipeline — {len(pdf_tasks)} PDF(s)")
        print(f"  Pass 1 batch size  : {BATCH_SIZE_PASS_1} pages")
        print(f"  Pass 2/2b/3 workers: {CONCURRENCY} concurrent async workers (shared client)")
        print(f"  GPU model phasing  : Pass34 (Phase A) → Pass5Ingest (Phase B)")
        print("=" * 70 + "\n")

    # ── Phases A + B (pipelined) ──────────────────────────────────────────
    # A single _MultiNodePool owns one client per Pass34 node and a shared
    # NodePool spread across them.  Each PDF worker submits its Passes 2/2b/3
    # to the pool's loop and blocks until done; inside, every page/figure/table
    # request is distributed across all nodes, so one PDF fans out over every
    # GPU and multiple PDFs share the same pool.  Phase B is submitted as soon
    # as each PDF finishes Phase A, overlapping Pass5Ingest and Pass34.
    _emit({"type": "phase_start", "phase": "A", "total": len(pdf_tasks)})
    if progress_queue is None:
        print(f"--- Phase A+B (pipelined): {len(PASS34_NODE_URLS)} Pass34 node(s) ---")

    phase_a = _MultiNodePool(PASS34_NODE_URLS, LLM_API_KEY, LLM_TIMEOUT, CONCURRENCY)
    # Local runner with one shared semaphore so Pass 3b/4 across ALL documents is
    # globally capped at CONCURRENCY_PASS_4 (not per-document).
    phase_b_runner = _PersistentAsyncRunner(LLM_BASE_URL, LLM_API_KEY, LLM_TIMEOUT,
                                            CONCURRENCY_PASS_4)

    completed_tasks  = []
    phase_b_futures  = []   # list of (task, concurrent.futures.Future)
    phase_a_done     = 0

    # Cap how many PDFs run Phase A at once.  Starting all of them together spawns
    # one ONNX Pass-1 job + a 16-thread page pool per PDF, thrashing the CPU and
    # freezing the machine at startup.  A few keep the GPU pool saturated anyway.
    pdf_workers = MAX_PDF_WORKERS or (os.cpu_count() or 4)
    pdf_workers = max(1, min(pdf_workers, len(pdf_tasks)))

    try:
        with ThreadPoolExecutor(max_workers=pdf_workers) as executor:
            future_to_task = {
                executor.submit(
                    _run_passes_1_to_3, task, checkpoint_manager, PROCESS_ROOT,
                    CONCURRENCY, pdf_workers, progress_queue, phase_a,
                    timeout_registry,
                ): task
                for task in pdf_tasks
            }
            for future in (tqdm(as_completed(future_to_task), total=len(pdf_tasks),
                                desc="Phase A complete", unit="PDF")
                           if progress_queue is None
                           else as_completed(future_to_task)):
                task = future_to_task[future]
                try:
                    completed_tasks.append(future.result())
                    phase_a_done += 1
                    _emit({"type": "phase_progress", "phase": "A",
                           "current": phase_a_done, "total": len(pdf_tasks)})
                    # Immediately kick off Phase B for this PDF on the shared runner.
                    b_fut = asyncio.run_coroutine_threadsafe(
                        _run_pass_4_coro(task, checkpoint_manager, PROCESS_ROOT,
                                         progress_queue, stop_event, phase_b_runner.client,
                                         timeout_registry, phase_b_runner.semaphore),
                        phase_b_runner._loop,
                    )
                    phase_b_futures.append((task, b_fut))
                except Exception as exc:
                    _emit({"type": "log", "level": "error",
                           "message": f"{task['filename']} Phase A failed: {exc}"})
                    if progress_queue is None:
                        print(f"\n  [ERROR] '{task['filename']}' Phase A failed: {exc}")
                if _stopped():
                    _emit({"type": "log", "level": "warning",
                           "message": "Stop requested — finishing in-flight PDFs then halting."})
                    break
    finally:
        # Report how Pass34 requests were split across nodes (balance check).
        node_stats = phase_a.pool.stats()
        total_reqs = sum(node_stats.values())
        if total_reqs:
            split = ", ".join(
                f"{lbl}: {n} ({100 * n / total_reqs:.0f}%)"
                for lbl, n in node_stats.items()
            )
            _emit({"type": "log", "level": "info",
                   "message": f"Pass34 request split — {split}"})
            if progress_queue is None:
                print(f"  Pass34 request split — {split}")
        phase_a.close()

    _emit({"type": "phase_done", "phase": "A"})

    if _stopped():
        _emit({"type": "pipeline_stopped"})
        phase_b_runner.close()
        return

    # ── Collect Phase B results ───────────────────────────────────────────
    _emit({"type": "phase_start", "phase": "B", "total": len(phase_b_futures)})
    if progress_queue is None:
        print("\n--- Phase B: waiting for any remaining 3b/4 work ---")

    pdf_infos    = []
    phase_b_done = 0

    if not phase_b_futures:
        _emit({"type": "log", "level": "warning",
               "message": "No PDFs completed Phase A — skipping Phase B."})

    try:
        for task, b_fut in phase_b_futures:
            try:
                result = b_fut.result()
                pdf_infos.append(result)
            except Exception as exc:
                _emit({"type": "log", "level": "error",
                       "message": f"{task['filename']} Phase B failed: {exc}"})
                if progress_queue is None:
                    print(f"\n  [ERROR] '{task['filename']}' Phase B failed: {exc}")
            phase_b_done += 1
            _emit({"type": "phase_progress", "phase": "B",
                   "current": phase_b_done, "total": len(phase_b_futures)})
            if _stopped():
                break
    finally:
        phase_b_runner.close()

    _emit({"type": "phase_done", "phase": "B"})

    if _stopped():
        _emit({"type": "pipeline_stopped"})
        return

    # ── Retry phase: re-run any passes that timed out ─────────────────────
    if TIMEOUT_RETRY_ENABLED and timeout_registry.any():
        retry_infos = _run_timeout_retry_phase(
            timeout_registry, pdf_tasks, checkpoint_manager,
            progress_queue, stop_event, _emit,
        )
        if retry_infos:
            # Refresh pdf_infos with the retried documents' fresh results so the
            # organise/ingest phases use the regenerated output.
            by_ckpt = {i['checkpoint_filename']: i for i in pdf_infos}
            for info in retry_infos:
                by_ckpt[info['checkpoint_filename']] = info
            pdf_infos = list(by_ckpt.values())
        if _stopped():
            _emit({"type": "pipeline_stopped"})
            return

    # ── Phase 5: organise outputs ─────────────────────────────────────────
    _emit({"type": "log", "level": "info", "message": "Phase 5 — Organising Final Outputs"})
    mapping_file = os.path.join(PROCESS_ROOT, 'pipeline_mapping.json')
    with open(mapping_file, 'w', encoding='utf-8') as f:
        json.dump({info['checkpoint_filename']: info for info in pdf_infos}, f, indent=2)

    # All copies use to_long_path: the squashed per-document filenames push these
    # paths past Windows' 260-char MAX_PATH, so a plain os.path.exists would
    # report files that exist as missing ("Final document not found").
    def _copy_dir(src, dst):
        src_lp, dst_lp = to_long_path(src), to_long_path(dst)
        if os.path.exists(src_lp):
            if os.path.exists(dst_lp):
                shutil.rmtree(dst_lp)
            shutil.copytree(src_lp, dst_lp)

    for info in pdf_infos:
        final_dir = os.path.join(
            FINAL_OUTPUT_ROOT, info['product'], info['sub_folder'], info['language']
        )
        os.makedirs(to_long_path(final_dir), exist_ok=True)
        _copy_dir(info['figures_dir'], os.path.join(final_dir, 'Figures'))
        _copy_dir(info['tables_dir'],  os.path.join(final_dir, 'Tables'))

        squashed_base = (
            f"{info['product']}_{info['sub_folder']}_{info['language']}"
            .replace(' ', '_').lower()
        )
        src_md = os.path.join(
            PROCESS_ROOT, info['product'], info['sub_folder'], info['language'],
            'Pass_4', squashed_base + ".md"
        )
        if os.path.exists(to_long_path(src_md)):
            shutil.copy2(to_long_path(src_md),
                         to_long_path(os.path.join(final_dir, 'document.md')))
        else:
            _emit({"type": "log", "level": "warning",
                   "message": f"Final document not found: {src_md}"})

    if _stopped():
        _emit({"type": "pipeline_stopped"})
        return

    # ── Phase 6: unload models ────────────────────────────────────────────
    _emit({"type": "log", "level": "info", "message": "Phase 6 — Unloading models"})
    for swap_url in PASS34_NODE_SWAP_URLS:                     # all Pass34 nodes
        for model in sorted({MODEL_PASS_2, MODEL_PASS_2B, MODEL_PASS_3}):
            unload_model(model, swap_url=swap_url)
    for model in sorted({MODEL_PASS_3B, MODEL_PASS_4}):
        unload_model(model, swap_url=LLAMA_SWAP_URL)           # local only

    # ── Phase 7: RAG ingestion ────────────────────────────────────────────
    # Pass the live checkpoint manager plus a map from each document's
    # (product, sub_folder, language) to its checkpoint key, so ingestion can
    # record an 'ingest' flag per document and skip any already ingested.
    _emit({"type": "log", "level": "info", "message": "Phase 7 — RAG Ingestion"})
    from ingestion.Ingest import run_ingestion_stage
    doc_ckpt_map = {
        (info['product'], info['sub_folder'], info['language']):
            info['checkpoint_filename']
        for info in pdf_infos
    }
    run_ingestion_stage(checkpoint_manager=checkpoint_manager,
                        doc_checkpoint_map=doc_ckpt_map)

    _emit({"type": "pipeline_done"})


if __name__ == "__main__":
    run_pipeline(progress_queue=None)

