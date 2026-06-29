"""
settings.py — user-editable settings that override the defaults in config.py.

config.py holds the baseline defaults.  Anything the user changes in the GUI's
startup Settings screen is written to a settings.json file next to the app and
re-applied on top of config.py at startup and at the start of every pipeline run
(so edits take effect on the next run without touching the .py file).

Flow:
    from core import settings as st
    st.apply(st.load())          # overlay saved overrides onto core.config
    values = st.effective()      # {KEY: current value} for populating the form
    st.save(st.coerce_form(...)) # persist the edited values
"""

import os
import sys
import json

import core.config as cfg


# --- where settings.json lives -------------------------------------------------
def _base_dir() -> str:
    # Next to the frozen .exe when bundled, else the project root (parent of core/).
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


SETTINGS_PATH = os.path.join(_base_dir(), "settings.json")


# --- schema: which config keys are editable, grouped for the form --------------
# Each field: (KEY, label, kind)
# kind ∈ {str, int, int_opt, float, bool, dir, password, list}
# Sections are split into two tiers in the UI: the everyday ones a new user must
# set ("common") come first, and the rest ("advanced") follow. COMMON_SECTIONS
# lists the common ones; anything not listed is automatically advanced.
SCHEMA: list[tuple[str, list[tuple[str, str, str]]]] = [
    # ---- Common ----
    ("Paths", [
        ("ROOT_PATH",         "Project root (HIWIN folder)", "dir"),
        ("IMAGE_TARGET_ROOT", "Web static image target",     "dir"),
    ]),
    ("Database & ingestion", [
        ("DB_HOST",            "DB host",                  "str"),
        ("DB_PORT",            "DB port",                  "str"),
        ("DB_NAME",            "DB name",                  "str"),
        ("DB_USER",            "DB user",                  "str"),
        ("DB_PASS",            "DB password",              "password"),
        ("DB_SCHEMA",          "DB schema",                "str"),
        # EMBED_DIM is intentionally NOT editable — it is locked to the embedding
        # model's output dimension in core.config (changing it would corrupt the
        # pgvector column). See core/config.py.
        ("EMBED_BATCH_SIZE",   "Embedding batch size",     "int"),
        ("INGEST_NUM_WORKERS", "Ingestion workers",        "int"),
        ("INGEST_BY_PAGE",     "Ingest one node per page", "bool"),
    ]),
    # ---- Advanced ----
    ("LLM Server (local — Pass 3b/4 & ingest)", [
        ("LLM_BASE_URL",        "LLM base URL",            "str"),
        ("LLAMA_SWAP_URL",      "llama-swap URL",          "str"),
        ("LLM_API_KEY",         "API key",                 "str"),
        ("LLM_TIMEOUT",         "Request timeout (ms)",    "int"),
        ("LLM_PREHEAT_TIMEOUT", "Preheat timeout (s)",     "int"),
        ("LLM_MAX_RETRIES",     "Max retries per call",    "int"),
    ]),
    ("Pass34 inference nodes (Pass 2/2b/3)", [
        ("PASS34_NODE_URLS",      "Node URLs (one per line)",       "list"),
        ("PASS34_NODE_SWAP_URLS", "Node llama-swap URLs (one per line)", "list"),
    ]),
    ("Models", [
        ("MODEL_PASS_2",  "Pass 2 model (page → markdown)",  "str"),
        ("MODEL_PASS_2B", "Pass 2b model (figure caption)",  "str"),
        ("MODEL_PASS_3",  "Pass 3 model (table → markdown)",  "str"),
        ("MODEL_PASS_3B", "Pass 3b model (table summary)",   "str"),
        ("MODEL_PASS_4",  "Pass 4 model (validation)",       "str"),
    ]),
    ("Pass 1 — layout detection", [
        ("SCORE_THRESHOLD",   "Detection confidence (0–1)", "float"),
        ("PASS_1_RENDER_DPI", "Render DPI",                 "int"),
        ("BATCH_SIZE_PASS_1", "Pages per batch",            "int"),
        ("ORT_INTRA_THREADS", "ONNX intra-op threads (blank = auto)", "int_opt"),
    ]),
    ("Concurrency", [
        ("MAX_PDF_WORKERS",   "PDFs processed at once (0 = auto)", "int"),
        ("CONCURRENCY",       "Pass 2/2b/3 async workers",         "int"),
        ("CONCURRENCY_PASS_4", "Pass 3b/4 concurrent calls",       "int"),
    ]),
    ("Behaviour toggles", [
        ("PASS_2_SKIP_BLANK_PAGES", "Skip blank pages in Pass 2",        "bool"),
        ("PASS_2_BLANK_DRY_RUN",    "Blank detection dry-run (log only)", "bool"),
        ("PASS_4_GATE_VALIDATION",  "Only validate malformed pages",      "bool"),
        ("TIMEOUT_RETRY_ENABLED",   "Retry timed-out passes after run",   "bool"),
    ]),
]

# Which sections are "common" (shown first, above the Advanced divider). Anything
# not listed here is treated as advanced.
COMMON_SECTIONS = ("Paths", "Database & ingestion")


def section_tier(section: str) -> str:
    """Return 'common' or 'advanced' for a SCHEMA section title."""
    return "common" if section in COMMON_SECTIONS else "advanced"

# Flat {key: kind} for quick lookups.
KIND = {key: kind for _section, fields in SCHEMA for key, _label, kind in fields}

# Paths derived from ROOT_PATH; recomputed in apply() unless explicitly overridden.
_DERIVED_FROM_ROOT = {
    "PDF_CONFIG_PATH":   "PDF_Config.yaml",
    "PROCESS_ROOT":      "Process_Files",
    "FINAL_OUTPUT_ROOT": "Final_Output",
    "MODEL_PATH_PASS_1": "PP-DocLayout-PlusL.onnx",
}


# --- persistence ---------------------------------------------------------------
def load() -> dict:
    """Return saved overrides ({} if the file is absent or unreadable)."""
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, IOError):
        return {}


def save(overrides: dict) -> None:
    """Persist overrides to settings.json (only known schema keys are kept)."""
    clean = {k: v for k, v in overrides.items() if k in KIND}
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(clean, f, indent=2)


# --- apply ---------------------------------------------------------------------
def apply(overrides: dict) -> None:
    """Overlay *overrides* onto the live core.config module, then recompute any
    ROOT_PATH-derived paths that weren't themselves overridden."""
    for key, value in overrides.items():
        if key in KIND:
            setattr(cfg, key, value)
    root = cfg.ROOT_PATH
    for key, leaf in _DERIVED_FROM_ROOT.items():
        if key not in overrides:
            setattr(cfg, key, os.path.join(root, leaf))


def effective() -> dict:
    """Current value for every editable key: a saved override if present,
    otherwise the live default from core.config."""
    overrides = load()
    out = {}
    for key in KIND:
        out[key] = overrides[key] if key in overrides else getattr(cfg, key, None)
    return out


# --- coercion ------------------------------------------------------------------
def coerce(kind: str, raw):
    """Convert a raw form value (string / bool / list) to the typed value."""
    if kind == "bool":
        return bool(raw)
    if kind == "list":
        if isinstance(raw, list):
            items = raw
        else:
            items = [p.strip() for line in str(raw).splitlines()
                     for p in line.split(",")]
        return [s for s in (i.strip() for i in items) if s]
    if kind == "int":
        return int(str(raw).strip())
    if kind == "int_opt":
        s = str(raw).strip()
        return None if s == "" or s.lower() == "auto" else int(s)
    if kind == "float":
        return float(str(raw).strip())
    return str(raw).strip()


def coerce_form(raw_values: dict) -> dict:
    """Coerce a {key: raw} dict from the form into typed overrides."""
    out = {}
    for key, raw in raw_values.items():
        if key in KIND:
            out[key] = coerce(KIND[key], raw)
    return out
