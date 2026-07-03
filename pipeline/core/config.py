import os

# =====================================================================
# PATHS
# =====================================================================
# OS-agnostic defaults (work on Windows, Linux, macOS). These are placeholders —
# set the real locations on the GUI/TUI settings screen (saved to settings.json)
# or via environment overrides. All paths are built with os.path.join so they use
# the correct separator on every platform.
ROOT_PATH         = os.environ.get("HIWIN_ROOT") or os.path.join(os.path.expanduser("~"), "HIWIN")
PDF_CONFIG_PATH   = os.path.join(ROOT_PATH, 'PDF_Config.yaml')
PROCESS_ROOT      = os.path.join(ROOT_PATH, 'Process_Files')
FINAL_OUTPUT_ROOT = os.path.join(ROOT_PATH, 'Final_Output')
MODEL_PATH_PASS_1 = os.path.join(ROOT_PATH, 'PP-DocLayout-PlusL.onnx')
# Where Pass-stage figures/tables are copied for the web server to serve. Point
# this at your OpenWebUI / backend static root; defaults to a folder under ROOT.
IMAGE_TARGET_ROOT = os.path.join(ROOT_PATH, 'web_static')

# =====================================================================
# LLM SERVER — local  (Pass 3b / 4 — RAG_Pipeline_Pass5Ingest)
# =====================================================================
LLAMA_SWAP_URL      = "http://127.0.0.1:11400"
LLM_BASE_URL        = "http://localhost:11400/v1"
LLM_API_KEY         = "sk-no-key-required"
LLM_TIMEOUT         = 60000    # ms — async passes 2/2b/3
LLM_PREHEAT_TIMEOUT = 120      # seconds — preheat + Pass 4 sync client
LLM_MAX_RETRIES     = 3

# =====================================================================
# PASS34 INFERENCE NODES  (Passes 2 / 2b / 3 — RAG_Pipeline_Pass34)
# Add or remove entries to scale horizontally across machines.
# Each entry is one llama-swap instance serving RAG_Pipeline_Pass34.
# =====================================================================
PASS34_NODE_URLS = [
    "http://localhost:11400/v1",         # local GPU
    "http://100.68.247.41:8080/v1",     # remote GPU
]
PASS34_NODE_SWAP_URLS = [
    "http://127.0.0.1:11400",            # local llama-swap
    "http://100.68.247.41:8080",        # remote llama-swap
]

# =====================================================================
# MODELS
# =====================================================================
MODEL_PASS_2  = "RAG_Pipeline_Pass34"
MODEL_PASS_2B = "RAG_Pipeline_Pass34"   # figure captioning — same model as Pass 2/3
MODEL_PASS_3  = "RAG_Pipeline_Pass34"
MODEL_PASS_3B = "RAG_Pipeline_Pass5Ingest"  # table summarisation — text-only, same as Pass 4
MODEL_PASS_4  = "RAG_Pipeline_Pass5Ingest"

# =====================================================================
# PASS 1 (layout detection)
# =====================================================================
SCORE_THRESHOLD   = 0.5     # ONNX detection confidence threshold
PASS_1_RENDER_DPI = 200     # DPI for rendering PDF pages (also sets crop sharpness)
BATCH_SIZE_PASS_1 = 16      # pages processed concurrently within a single PDF
# None = auto-calculate from cpu_count, batch size, and pdf_workers
ORT_INTRA_THREADS = 4

# =====================================================================
# PIPELINE CONCURRENCY
# =====================================================================
CONCURRENCY      = 5    # async workers for Passes 2/2b/3 — raise if VRAM allows
CONCURRENCY_PASS_4 = 5  # concurrent LLM validation calls within Pass 4

# How many PDFs run Phase A (Pass 1-3) at once.  The pipeline used to start every
# PDF simultaneously (one ONNX Pass-1 job + a 16-thread page pool each), which
# thrashes the CPU and freezes the machine at startup with many files.  A few
# concurrent PDFs already keep the GPU pool saturated.  0 = auto (CPU count).
MAX_PDF_WORKERS  = 4

# =====================================================================
# LLM CALL REDUCTION
# =====================================================================
# Pass 2 — skip transcribing pages Pass 1 flagged as blank (no real content).
# Pass 1 writes a ".blank" marker next to each blank page image; Pass 2 then
# writes an empty Markdown file instead of making a vision call.
PASS_2_SKIP_BLANK_PAGES = True
# Dry run: log which pages WOULD be skipped but still transcribe them, so you
# can audit the blank detection before trusting it.  Set False to actually skip.
PASS_2_BLANK_DRY_RUN    = False

# Pass 4 — only send a page to the validation LLM when its Markdown looks
# malformed (broken tables, unclosed code fences / HTML comments).  Well-formed
# pages are passed through untouched.  Set False to validate every page.
PASS_4_GATE_VALIDATION  = True

# =====================================================================
# DEFERRED TIMEOUT RETRY
# =====================================================================
# When an LLM call in a pass times out (even after that pass's own per-request
# retries), the (document, pass) is recorded in a timeout cache.  After every
# pass for every document finishes, a single retry phase re-runs the affected
# passes — from the earliest timed-out pass downward — still within the same run.
# Set False to disable the retry phase (timeouts are then just logged as before).
TIMEOUT_RETRY_ENABLED = True

# =====================================================================
# DATABASE  (Ingest)
# =====================================================================
DB_NAME   = os.environ.get("DB_NAME",   "hiwin_rag_db")
DB_USER   = os.environ.get("DB_USER",   "postgres")
# REQUIRED — no default. Set it on the GUI/TUI settings screen (saved to
# settings.json), or via the DB_PASS environment variable. Never hard-code a secret.
DB_PASS   = os.environ.get("DB_PASS",   "")
DB_HOST   = os.environ.get("DB_HOST",   "localhost")
DB_PORT   = os.environ.get("DB_PORT",   "5432")
# Embedding vector dimension — defines the pgvector column width.
# IMPORTANT: this is tied to the embedding model. If you change the embedding
# model (model_name in ingestion/Ingest.py), EMBED_DIM MUST be changed to that
# model's output dimension, and the DB table/column rebuilt to match — otherwise
# inserts fail or vectors are meaningless. It is deliberately NOT user-editable
# (kept out of the settings screen) so it can't silently drift from the model.
# Default 2560 = Embedding_Qwen3.6 (Qwen3-Embedding-4B).
EMBED_DIM = 2560
DB_SCHEMA = os.environ.get("DB_SCHEMA", "hiwin_rag")

EMBED_BATCH_SIZE   = 1024
INGEST_NUM_WORKERS = 1

# True  → one embedding node per page (reads per-page files from Pass_3b / Pass_3)
# False → split by markdown heading sections using MarkdownNodeParser (default)
INGEST_BY_PAGE     = True

# Default language code stamped on entries when a PDF_Config.yaml is auto-generated
# (because none was found). Override with the PIPELINE_DEFAULT_LANGUAGE env var.
# Review the generated file and fix per-PDF languages if your PDFs aren't all this.
DEFAULT_LANGUAGE   = os.environ.get("PIPELINE_DEFAULT_LANGUAGE", "tc")

# =====================================================================
# GUI — themes
# =====================================================================
# Two full palettes the user can switch between at runtime.  Both define the
# exact same set of role keys so the GUI can re-colour every widget live.
THEMES = {
    "light": {   # Light + indigo — clean dashboard
        "name":        "light",
        "bg":          "#f1f5f9",
        "surface":     "#ffffff",
        "surface_alt": "#f8fafc",
        "border":      "#e2e8f0",
        "header_bg":   "#ffffff",
        "header_fg":   "#0f172a",
        "phase_bg":    "#ffffff",
        "phase_fg":    "#334155",
        "col_hdr_bg":  "#f8fafc",
        "col_hdr_fg":  "#64748b",
        "text":        "#0f172a",
        "subtext":     "#475569",
        "muted":       "#94a3b8",
        "log_bg":      "#0f172a",
        "log_fg":      "#e2e8f0",
        "accent":      "#6366f1",
        "accent_active": "#4f46e5",
        "track":       "#e2e8f0",
        "scrollbar":   "#cbd5e1",
        "row_running": "#eef2ff",
        "row_error":   "#fef2f2",
        "c_pending":   "#cbd5e1",
        "c_running":   "#6366f1",
        "c_done":      "#16a34a",
        "c_skip":      "#94a3b8",
        "c_error":     "#dc2626",
        "pills": {
            "idle":    ("#e2e8f0", "#475569"),
            "running": ("#6366f1", "#ffffff"),
            "done":    ("#16a34a", "#ffffff"),
            "stopped": ("#f59e0b", "#ffffff"),
            "error":   ("#dc2626", "#ffffff"),
        },
        "toggle_icon": "☾",   # click to go dark
    },
    "dark": {    # Dark slate + violet — sleek app-like
        "name":        "dark",
        "bg":          "#0f172a",
        "surface":     "#1e293b",
        "surface_alt": "#243244",
        "border":      "#334155",
        "header_bg":   "#111827",
        "header_fg":   "#f1f5f9",
        "phase_bg":    "#1e293b",
        "phase_fg":    "#e2e8f0",
        "col_hdr_bg":  "#1e293b",
        "col_hdr_fg":  "#94a3b8",
        "text":        "#f1f5f9",
        "subtext":     "#cbd5e1",
        "muted":       "#94a3b8",
        "log_bg":      "#0b1120",
        "log_fg":      "#e2e8f0",
        "accent":      "#8b5cf6",
        "accent_active": "#7c3aed",
        "track":       "#334155",
        "scrollbar":   "#475569",
        "row_running": "#2e2a4a",
        "row_error":   "#3b1f24",
        "c_pending":   "#475569",
        "c_running":   "#a78bfa",
        "c_done":      "#34d399",
        "c_skip":      "#64748b",
        "c_error":     "#f87171",
        "pills": {
            "idle":    ("#334155", "#cbd5e1"),
            "running": ("#7c3aed", "#ffffff"),
            "done":    ("#15803d", "#ffffff"),
            "stopped": ("#b45309", "#ffffff"),
            "error":   ("#b91c1c", "#ffffff"),
        },
        "toggle_icon": "☀",   # click to go light
    },
}
DEFAULT_THEME = "light"

# Per-pass status glyphs (theme-independent)
I_PENDING = "○"
I_RUNNING = "◍"
I_DONE    = "✓"
I_SKIP    = "–"
I_ERROR   = "✗"

# Pass identifiers, in display order.  This is the single source of truth —
# the pipeline emits icon events keyed by these names and the GUI builds one
# column per entry, so the two can never drift apart.
PASS_KEYS   = ["pass_1", "pass_2", "pass_2b", "pass_3", "pass_3b", "pass_4"]
PASS_LABELS = ["P1",     "P2",     "P2b",     "P3",     "P3b",      "P4"]

# Column layout — shared verbatim by the fixed header row and every PDF row so
# they line up.  Each entry: (key, heading, fixed_width_px, weight).
# weight 0 → fixed width; weight >0 → absorbs extra horizontal space.
COL_ICON_W = 46
COLUMNS = (
    [("name", "PDF", 250, 0)]
    + [(k, lbl, COL_ICON_W, 0) for k, lbl in zip(PASS_KEYS, PASS_LABELS)]
    + [("progress", "Progress", 220, 1),
       ("step",     "Step",     210, 0)]
)

ROW_H       = 36     # pixels per PDF row
HDR_H       = 30     # pixels for the column-header strip
SCROLLBAR_W = 16     # reserved gutter so the header lines up with scrolled rows
MAX_ROWS_H  = 420    # max height of the scrollable PDF grid before it clips
