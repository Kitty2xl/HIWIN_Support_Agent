#!/usr/bin/env python3
"""doctor.py - preflight / diagnostic check for the HIWIN Support Agent.

Run it BEFORE starting the backend on a new machine, and again whenever
something misbehaves. Every line is PASS / WARN / FAIL with a concrete fix.

    python doctor.py              # serving stack: .env, Postgres, inference server, config.ini, images
    python doctor.py --pipeline   # + ingestion stack: settings.json, ROOT_PATH, ONNX model, models.json
    python doctor.py --live       # + real embed / rerank / 1-token chat calls against the router
    python doctor.py --all        # everything

Exit code 1 if anything FAILed (so the launchers can warn), 0 otherwise.
Needs only the backend requirements; runs on Windows, Linux and macOS.
"""

import argparse
import configparser
import importlib
import json
import os
import platform
import re
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PIPELINE_DIR = os.path.join(BASE_DIR, "pipeline")

# Console-safe output on every OS (Windows cmd with a non-UTF-8 code page included).
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


class Report:
    def __init__(self):
        self.fails = 0
        self.warns = 0

    def _emit(self, level, title, detail=None, fix=None):
        line = f"[{level}] {title}"
        if detail:
            line += f" - {detail}"
        print(line)
        if fix:
            print(f"       fix: {fix}")
        if level == "FAIL":
            self.fails += 1
        elif level == "WARN":
            self.warns += 1

    def ok(self, title, detail=None):
        self._emit("PASS", title, detail)

    def info(self, title, detail=None):
        self._emit("INFO", title, detail)

    def warn(self, title, detail=None, fix=None):
        self._emit("WARN", title, detail, fix)

    def fail(self, title, detail=None, fix=None):
        self._emit("FAIL", title, detail, fix)

    def section(self, name):
        print(f"\n== {name} ==")


R = Report()


def guarded(fn):
    """Never let one broken check hide the others."""
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except Exception as e:  # noqa: BLE001
            R.fail(f"{fn.__name__} crashed", f"{type(e).__name__}: {e}")
            return None
    return wrapper


# --------------------------------------------------------------------------- #
# Python + packages
# --------------------------------------------------------------------------- #

@guarded
def check_python():
    R.section("Python")
    v = sys.version_info
    ver = f"{v.major}.{v.minor}.{v.micro}"
    if v < (3, 10):
        R.fail("Python version", ver, "install Python 3.12.x (see .python-version) and recreate .venv")
    elif (v.major, v.minor) != (3, 12):
        R.warn("Python version", f"{ver} (the project is tested on 3.12.7)",
               "prefer Python 3.12.x; recreate .venv with it if you hit package build errors")
    else:
        R.ok("Python version", ver)
    if sys.prefix != getattr(sys, "base_prefix", sys.prefix):
        R.ok("virtualenv active", sys.prefix)
    else:
        R.warn("no virtualenv active", sys.executable,
               "activate it first: Windows `.venv\\Scripts\\activate`, Linux `source .venv/bin/activate`")
    R.info("platform", f"{platform.system()} {platform.release()} ({platform.machine()})")


BACKEND_PKGS = [("fastapi", "fastapi"), ("uvicorn", "uvicorn[standard]"), ("psycopg2", "psycopg2-binary"),
                ("requests", "requests"), ("dotenv", "python-dotenv")]
PIPELINE_PKGS = [("fitz", "PyMuPDF"), ("cv2", "opencv-python-headless"), ("onnxruntime", "onnxruntime"),
                 ("openai", "openai"), ("yaml", "PyYAML"), ("rich", "rich"), ("tqdm", "tqdm"),
                 ("aiofiles", "aiofiles"), ("huggingface_hub", "huggingface_hub"),
                 ("llama_index.core", "llama-index-core"),
                 ("llama_index.embeddings.openai_like", "llama-index-embeddings-openai-like"),
                 ("llama_index.vector_stores.postgres", "llama-index-vector-stores-postgres")]


@guarded
def check_packages(pipeline: bool):
    R.section("Python packages")
    missing = []
    for mod, pkg in BACKEND_PKGS + (PIPELINE_PKGS if pipeline else []):
        try:
            importlib.import_module(mod)
        except ImportError as e:
            msg = str(e)
            if mod == "cv2" and ("libGL" in msg or "libgthread" in msg or "libglib" in msg):
                R.fail("opencv import", msg,
                       "Linux server without a display: `sudo apt install libgl1 libglib2.0-0`, or "
                       "`pip uninstall opencv-python && pip install opencv-python-headless`")
                continue
            missing.append(pkg)
        except Exception as e:  # noqa: BLE001  (binary import errors, e.g. numpy ABI)
            R.fail(f"import {mod}", f"{type(e).__name__}: {e}",
                   "reinstall with the tested versions: pip install -r requirements.lock.txt")
    if missing:
        R.fail("missing packages", ", ".join(missing), "pip install -r requirements.txt")
    else:
        R.ok("all required packages import", "backend" + (" + pipeline" if pipeline else ""))
    if pipeline:
        try:
            importlib.import_module("tkinter")
            R.ok("tkinter (pipeline GUI)")
        except ImportError:
            R.warn("tkinter missing (only the pipeline GUI needs it)", None,
                   "Linux: `sudo apt install python3-tk`; or use `python tui.py` / `python Pipeline.py` instead")


# --------------------------------------------------------------------------- #
# Backend config (.env)
# --------------------------------------------------------------------------- #

@guarded
def check_env():
    R.section("Backend config (.env)")
    env_path = os.path.join(BASE_DIR, ".env")
    if not os.path.exists(env_path):
        R.fail(".env missing", env_path,
               "run `python env_from_settings.py` (creates it from pipeline/settings.json), "
               "or copy the block from README 'Configuration'")
    else:
        with open(env_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                m = re.match(r"^\s*([A-Z_]+)\s*=(.*)$", line)
                if m and "/path/to/" in m.group(2):
                    R.warn(f".env {m.group(1)} is still a placeholder", m.group(2).strip(),
                           "set it to a real path (or edit pipeline/settings.json and run env_from_settings.py)")
        R.ok(".env found", env_path)
    try:
        import config  # noqa: F401  (loads .env)
    except Exception as e:  # noqa: BLE001
        R.fail("import config.py", str(e))
        return None
    if not config.DB_PASSWORD:
        R.fail("DB_PASSWORD is empty", None,
               "set DB_PASSWORD in .env to the database password (this is what causes "
               "`fe_sendauth: no password supplied`)")
    else:
        R.ok("DB_PASSWORD set")
    R.info("inference", f"{config.INFERENCE_HOST}  models: chat={config.LANGUAGE_MODEL} "
                        f"embed={config.EMBEDDING_MODEL} rerank={config.RERANKER_MODEL} "
                        f"completeness={config.COMPLETENESS_MODEL}")
    R.info("postgres", f"{config.DB_USER}@{config.DB_HOST}:{config.DB_PORT}/{config.DB_NAME} "
                       f"schema={config.DB_SCHEMA} chat_log={config.CHAT_LOG_SCHEMA}.{config.CHAT_LOG_TABLE}")

    # prompt assets
    if os.path.exists(config.SYSTEM_PROMPT_PATH):
        R.ok("system prompt", os.path.relpath(config.SYSTEM_PROMPT_PATH, BASE_DIR))
    else:
        R.fail("system prompt missing", config.SYSTEM_PROMPT_PATH)
    for lang, path in config.LANGUAGE_SKILL_MAP.items():
        if not os.path.exists(path):
            R.fail(f"skill file for '{lang}' missing", path)
    if config.DEFAULT_LANGUAGE not in config.LANGUAGE_SKILL_MAP:
        R.fail("DEFAULT_LANGUAGE has no skill", config.DEFAULT_LANGUAGE,
               f"use one of {', '.join(config.LANGUAGE_SKILL_MAP)}")

    # images
    root = config.IMAGE_STATIC_ROOT
    if not root:
        R.warn("IMAGE_STATIC_ROOT not set", None,
               "figures will not be served and the vision pass is skipped; set it to the folder the "
               "pipeline copies Figures/Tables into (pipeline IMAGE_TARGET_ROOT)")
    elif not os.path.isdir(root):
        R.warn("IMAGE_STATIC_ROOT folder does not exist", root,
               "answers still work but images 404 and the vision pass is skipped. Create it by running "
               "`python -m ingestion.Ingest --assets-only` (from pipeline/, needs <ROOT_PATH>/Final_Output) "
               "or copy the figure folder from the old machine")
    else:
        has_file = any(files for _, _, files in os.walk(root))
        if has_file:
            R.ok("IMAGE_STATIC_ROOT", root)
        else:
            R.warn("IMAGE_STATIC_ROOT is empty", root,
                   "run `python -m ingestion.Ingest --assets-only` from pipeline/ to populate it")
    return config


# --------------------------------------------------------------------------- #
# Router preset (config.ini)
# --------------------------------------------------------------------------- #

def load_preset(path: str) -> configparser.ConfigParser:
    cp = configparser.ConfigParser(interpolation=None, strict=False, delimiters=("=",),
                                   comment_prefixes=(";", "#"), inline_comment_prefixes=None,
                                   allow_no_value=True)
    cp.optionxform = str
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        cp.read_string("[__top__]\n" + f.read())
    return cp


PATH_KEYS = ("model", "mmproj", "model-draft", "chat-template-file", "lora")


@guarded
def check_preset(required: dict, resident: set):
    """required: {model name: 'where it is configured'}; resident: names that must be load-on-startup."""
    R.section("Router preset (config.ini)")
    path = os.environ.get("PRESET") or os.path.join(BASE_DIR, "config.ini")
    if not os.path.exists(path):
        R.fail("config.ini not found", path, "restore it from git; start.bat/start.sh need it")
        return set()
    cp = load_preset(path)
    sections = [s for s in cp.sections() if s not in ("__top__", "*")]
    R.ok("preset parsed", f"{len(sections)} model section(s): {', '.join(sections)}")
    for s in sections:
        for key in PATH_KEYS:
            if cp.has_option(s, key):
                p = (cp.get(s, key) or "").strip()
                if not os.path.exists(p):
                    R.fail(f"[{s}] {key} file not found", p,
                           "edit the path in config.ini to where the file is on THIS machine "
                           "(or delete the optional model-draft / chat-template-file lines)")
    for name, where in required.items():
        if name in sections:
            if name in resident:
                los = (cp.get(name, "load-on-startup", fallback="") or "").strip().lower()
                if los not in ("true", "1", "on", "yes"):
                    R.warn(f"[{name}] has no load-on-startup = true", f"needed by {where}",
                           "add `load-on-startup = true` so the backend never waits for a cold load")
            R.ok(f"model '{name}' defined", f"used by {where}")
        else:
            R.fail(f"model '{name}' has no [section] in config.ini", f"used by {where}",
                   "the names in .env / pipeline/settings.json MUST equal the [section] names in config.ini")
    return set(sections)


# --------------------------------------------------------------------------- #
# Scripts
# --------------------------------------------------------------------------- #

@guarded
def check_scripts():
    R.section("Launch scripts")
    posix = os.name != "nt"
    for name in ("start.sh", "run.sh"):
        p = os.path.join(BASE_DIR, name)
        if not os.path.exists(p):
            continue
        with open(p, "rb") as f:
            data = f.read()
        if b"\r\n" in data:
            (R.fail if posix else R.warn)(
                f"{name} has Windows (CRLF) line endings", None,
                "on Linux it fails with `/usr/bin/env: 'bash\\r'`. Fix: `sed -i 's/\\r$//' start.sh run.sh` "
                "(or `git add --renormalize .` + re-checkout; .gitattributes pins *.sh to LF)")
        elif posix and not os.access(p, os.X_OK):
            R.warn(f"{name} is not executable", None, f"chmod +x {name}   (or run `bash {name}`)")
        else:
            R.ok(f"{name} line endings / mode")
    for name in ("start.bat", "run.bat"):
        p = os.path.join(BASE_DIR, name)
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                txt = f.read()
            m = re.search(r'set "LLAMA_SERVER=([^"]+)"', txt)
            if m and os.name == "nt" and not os.path.exists(m.group(1)):
                R.warn(f"{name}: LLAMA_SERVER path does not exist", m.group(1),
                       "edit the path at the top of the script, or set the LLAMA_SERVER env var")


# --------------------------------------------------------------------------- #
# Postgres
# --------------------------------------------------------------------------- #

def pg_error_hint(msg: str, config) -> str:
    m = msg.lower()
    if "password authentication failed" in m:
        return ("DB_PASSWORD in .env does not match this cluster. If several PostgreSQL versions are "
                "installed, each listens on its own port (5432, 5433, ...) - check DB_PORT points at the "
                "cluster that holds hiwin_rag_db")
    if "no password supplied" in m:
        return "set DB_PASSWORD in .env"
    if "does not exist" in m and "database" in m:
        return "run `python setup_db.py` to create the database, then the pipeline or a dump restore"
    if "refused" in m or "could not connect" in m or "timed out" in m or "timeout" in m:
        return (f"PostgreSQL is not listening on {config.DB_HOST}:{config.DB_PORT}. Windows: services.msc -> "
                "postgresql-x64-NN -> Start; Linux: `sudo systemctl start postgresql`; or fix DB_HOST/DB_PORT")
    if "pg_hba.conf" in m:
        return ("the server only trusts local connections: run the backend on the DB host (DB_HOST=localhost) "
                "or add a `host` line for your client in pg_hba.conf and reload")
    if "server closed the connection" in m:
        return "the server dropped the handshake: try DB_SSLMODE=disable (or require) in .env"
    return "check DB_HOST / DB_PORT / DB_NAME / DB_USER / DB_PASSWORD in .env"


@guarded
def check_db(config):
    R.section("PostgreSQL")
    info = {"dim": None, "tables": []}
    try:
        import psycopg2
        conn = psycopg2.connect(dbname=config.DB_NAME, user=config.DB_USER, password=config.DB_PASSWORD,
                                host=config.DB_HOST, port=config.DB_PORT, sslmode=config.DB_SSLMODE,
                                connect_timeout=5)
    except Exception as e:  # noqa: BLE001
        msg = str(e).strip().replace("\n", " ")
        R.fail("cannot connect", msg, pg_error_hint(msg, config))
        return info
    with conn.cursor() as cur:
        cur.execute("SHOW server_version_num")
        vnum = int(cur.fetchone()[0])
        major = vnum // 10000
        cur.execute("SELECT version()")
        vstr = cur.fetchone()[0].split(",")[0]
        if major >= 13:
            R.ok("connected", vstr)
        elif major == 12:
            R.warn("connected to an old server", vstr, "PostgreSQL 12 only works with pgvector <= 0.7; 13+ recommended")
        else:
            R.fail("PostgreSQL too old", vstr, "pgvector needs PostgreSQL 12+ (0.8.x: 13+)")

        cur.execute("SELECT default_version, installed_version FROM pg_available_extensions WHERE name='vector'")
        row = cur.fetchone()
        if not row:
            R.fail("pgvector is not installed on this server", None,
                   "it is a server-side extension: Linux `apt install postgresql-%d-pgvector`, Windows build/copy "
                   "vector.dll (see README 'PostgreSQL and pgvector'); then `python setup_db.py`" % major)
        elif not row[1]:
            R.fail("pgvector available but not enabled in this database", f"available {row[0]}",
                   "run `python setup_db.py` (CREATE EXTENSION vector needs a superuser)")
        else:
            R.ok("pgvector", f"installed {row[1]}")
            try:
                if tuple(int(x) for x in row[1].split(".")[:2]) < (0, 5):
                    R.warn("pgvector older than 0.5", row[1], "HNSW indexes need 0.5+; upgrade the extension")
            except ValueError:
                pass

        cur.execute("SELECT 1 FROM information_schema.schemata WHERE schema_name=%s", (config.DB_SCHEMA,))
        if not cur.fetchone():
            R.fail(f"schema {config.DB_SCHEMA} does not exist", None,
                   "run `python setup_db.py`, then the pipeline (or restore a dump)")
            conn.close()
            return info
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema=%s "
                    "AND table_name LIKE 'data\\_%%' ORDER BY 1", (config.DB_SCHEMA,))
        tables = [r[0] for r in cur.fetchall()]
        info["tables"] = tables
        if not tables:
            R.warn("no data_* tables", f"schema {config.DB_SCHEMA} is empty",
                   "the agent has nothing to search: run the pipeline (pipeline/gui.py / tui.py / Pipeline.py) "
                   "or restore a dump")
        else:
            R.ok("data tables", f"{len(tables)}: {', '.join(t[5:] for t in tables)}")

        known = set(config.LANGUAGE_SKILL_MAP)
        total = 0
        unknown_langs = {}
        null_langs = []
        empty = []
        for t in tables:
            cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name=%s",
                        (config.DB_SCHEMA, t))
            cols = {r[0] for r in cur.fetchall()}
            if "metadata_" not in cols or "embedding" not in cols:
                continue  # plain tables (product URL lists) are fine
            cur.execute(f"SELECT count(*), array_agg(DISTINCT metadata_->>'language_code') FROM {config.DB_SCHEMA}.{t}")
            n, langs = cur.fetchone()
            total += n
            if not n:
                empty.append(t)
            for lang in (langs or []):
                if lang is None:
                    null_langs.append(t)
                elif lang not in known:
                    unknown_langs.setdefault(lang, []).append(t)
            if info["dim"] is None:
                cur.execute("SELECT atttypmod FROM pg_attribute WHERE attrelid=%s::regclass AND attname='embedding'",
                            (f"{config.DB_SCHEMA}.{t}",))
                r = cur.fetchone()
                if r and r[0] and r[0] > 0:
                    info["dim"] = r[0]
        if tables:
            R.info("rows with vectors", f"{total}")
        if empty:
            R.warn("empty tables", ", ".join(empty))
        if null_langs:
            R.warn("rows with NULL language_code (never matched by a search)", ", ".join(null_langs))
        for lang, ts in unknown_langs.items():
            R.warn(f"language_code '{lang}' has no backend skill - those rows are unreachable",
                   ", ".join(ts), "add prompts/skills/<lang>.md + LANGUAGE_SKILL_MAP in config.py, or ignore")
        if info["dim"]:
            R.info("embedding dimension in DB", str(info["dim"]))
            try:
                sys.path.insert(0, PIPELINE_DIR)
                from core import config as pcfg  # noqa: WPS433
                if pcfg.EMBED_DIM != info["dim"]:
                    R.fail("pipeline EMBED_DIM != DB vector width", f"{pcfg.EMBED_DIM} vs {info['dim']}",
                           "pipeline/core/config.py EMBED_DIM must equal the width of the existing tables "
                           "(or rebuild the tables with the new embedding model)")
            except Exception:  # noqa: BLE001
                pass

        cur.execute("SELECT 1 FROM information_schema.tables WHERE table_schema=%s AND table_name=%s",
                    (config.CHAT_LOG_SCHEMA, config.CHAT_LOG_TABLE))
        if cur.fetchone():
            cur.execute(f"SELECT count(*), max(created_at) FROM {config.CHAT_LOG_SCHEMA}.{config.CHAT_LOG_TABLE}")
            n, last = cur.fetchone()
            R.info("chat log", f"{n} rows, last {last}")
        else:
            R.info("chat log table not created yet", "it is created on the first /chat call")
    conn.close()
    return info


# --------------------------------------------------------------------------- #
# Inference server
# --------------------------------------------------------------------------- #

@guarded
def check_inference(config, live: bool, db_dim):
    R.section("Inference server (llama.cpp router)")
    import requests
    url = f"{config.INFERENCE_HOST}/v1/models"
    try:
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:  # noqa: BLE001
        R.fail("router not reachable", f"{url}: {e}",
               "start it: `start.bat` / `./start.sh` (or `llama-server --models-preset config.ini --host 127.0.0.1 "
               "--port 11400`). If it runs on another machine set INFERENCE_HOST in .env")
        return
    models = {m.get("id"): m for m in data.get("data", [])}
    R.ok("router reachable", f"{len(models)} model(s): {', '.join(models)}")
    needed = {config.LANGUAGE_MODEL: "LANGUAGE_MODEL", config.EMBEDDING_MODEL: "EMBEDDING_MODEL",
              config.RERANKER_MODEL: "RERANKER_MODEL", config.COMPLETENESS_MODEL: "COMPLETENESS_MODEL"}
    for name, key in needed.items():
        if name in models:
            status = models[name].get("status")
            st = status.get("value") if isinstance(status, dict) else status
            if st in ("loading", "unloaded"):
                R.warn(f"{key} = {name} is {st}", None,
                       "the router answers before its models finish loading (~1 min for the full set); "
                       "wait, or run `python doctor.py --wait-models`. `unloaded` for a load-on-startup model "
                       "means it was evicted (raise MODELS_MAX / --models-max) or failed to load (see the router log)")
            elif st and st not in ("loaded",):
                R.fail(f"{key} = {name} status: {st}", None, "check the router log for the load error (VRAM, wrong path)")
            else:
                R.ok(f"{key} = {name}", f"status: {st}" if st else None)
        else:
            R.fail(f"{key} = {name} is not served", None,
                   "the name must equal a [section] in the preset the router was started with")
    if not live:
        R.info("live calls skipped", "add --live to test embed / rerank / chat for real")
        return
    import inference
    try:
        vec = inference.embed("search_query: doctor self-test", timeout=120)
        R.ok("embedding call", f"{len(vec)} dims")
        if db_dim and len(vec) != db_dim:
            R.fail("embedding model dimension != DB vector width", f"{len(vec)} vs {db_dim}",
                   "the database was built with a different embedding model. Either serve the model that "
                   "built it (EMBEDDING_MODEL) or re-ingest everything with the new one")
    except Exception as e:  # noqa: BLE001
        R.fail("embedding call failed", str(e)[:300])
    try:
        rr = inference.rerank("load capacity", ["HGW20 basic dynamic load 25 kN", "unrelated text"], 1, timeout=120)
        R.ok("rerank call", f"top index {rr.get('results', [{}])[0].get('index')}")
    except Exception as e:  # noqa: BLE001
        R.fail("rerank call failed", str(e)[:300],
               "if the error mentions batch size, raise ubatch-size/batch-size on the reranker section")
    try:
        r = requests.post(f"{config.INFERENCE_BASE_URL}/chat/completions",
                          json={"model": config.LANGUAGE_MODEL, "max_tokens": 8, "temperature": 0,
                                "messages": [{"role": "user", "content": "Reply with the single word OK."}]},
                          timeout=max(120, config.CHAT_TIMEOUT))
        if r.status_code >= 400:
            R.fail("chat call failed", f"HTTP {r.status_code}: {r.text[:300]}")
        else:
            R.ok("chat call", f"{(r.json()['choices'][0]['message'].get('content') or '').strip()[:40]!r}")
    except Exception as e:  # noqa: BLE001
        R.fail("chat call failed", str(e)[:300])


# --------------------------------------------------------------------------- #
# Pipeline
# --------------------------------------------------------------------------- #

def _norm(p):
    return os.path.normcase(os.path.normpath(p or ""))


@guarded
def check_pipeline(config, ini_sections: set, db_dim):
    R.section("Ingestion pipeline (pipeline/settings.json)")
    sys.path.insert(0, PIPELINE_DIR)
    from core import config as pcfg
    from core import settings as st
    spath = st.SETTINGS_PATH
    if not os.path.exists(spath):
        R.warn("settings.json missing", spath, "the GUI/TUI settings screen creates it; defaults from core/config.py apply")
    else:
        with open(spath, "r", encoding="utf-8", errors="replace") as f:
            raw = f.read()
        for m in re.finditer(r'"([A-Z_]+)":\s*"([^"]*/path/to/[^"]*)"', raw):
            R.warn(f"settings.json {m.group(1)} is a placeholder", m.group(2), "set the real path on the GUI/TUI settings screen or edit the file")
        R.ok("settings.json found", spath)
    st.apply(st.load())

    for label, p in (("ROOT_PATH", pcfg.ROOT_PATH), ("IMAGE_TARGET_ROOT", pcfg.IMAGE_TARGET_ROOT)):
        if os.path.isdir(p):
            R.ok(label, p)
        else:
            (R.fail if label == "ROOT_PATH" else R.warn)(f"{label} folder does not exist", p,
                                                       "create it / fix the path (pipeline creates IMAGE_TARGET_ROOT itself)")
    for label, p, how in (("PDFs folder", os.path.join(pcfg.ROOT_PATH, "PDFs"), "put the source PDFs under <ROOT_PATH>/PDFs/<product>/..."),
                          ("layout model", pcfg.MODEL_PATH_PASS_1, "download PP-DocLayout-PlusL.onnx into ROOT_PATH (README 'Prerequisites')")):
        if os.path.exists(p):
            R.ok(label, p)
        else:
            R.fail(f"{label} missing", p, how)
    if os.path.exists(pcfg.PDF_CONFIG_PATH):
        R.ok("PDF_Config.yaml", pcfg.PDF_CONFIG_PATH)
    else:
        R.info("PDF_Config.yaml not present", "the first pipeline run generates it from PDFs/ and stops for review")
    for label, p in (("Process_Files (per-page output; needed by per-page ingest)", pcfg.PROCESS_ROOT),
                     ("Final_Output (figures/tables; needed by --assets-only)", pcfg.FINAL_OUTPUT_ROOT)):
        R.info(label, ("present" if os.path.isdir(p) else "absent") + f": {p}")

    # parity with the backend
    if _norm(pcfg.IMAGE_TARGET_ROOT) != _norm(config.IMAGE_STATIC_ROOT):
        R.warn("IMAGE_TARGET_ROOT (pipeline) != IMAGE_STATIC_ROOT (backend)",
               f"{pcfg.IMAGE_TARGET_ROOT} vs {config.IMAGE_STATIC_ROOT}",
               "the backend must serve the folder the pipeline copies figures into; run env_from_settings.py")
    else:
        R.ok("image folder shared by pipeline and backend")
    pairs = [("DB_NAME", pcfg.DB_NAME, config.DB_NAME), ("DB_USER", pcfg.DB_USER, config.DB_USER),
             ("DB_HOST", pcfg.DB_HOST, config.DB_HOST), ("DB_PORT", str(pcfg.DB_PORT), str(config.DB_PORT)),
             ("DB_SCHEMA", pcfg.DB_SCHEMA, config.DB_SCHEMA)]
    bad = [k for k, a, b in pairs if a != b]
    if pcfg.DB_PASS != config.DB_PASSWORD:
        bad.append("DB_PASS/DB_PASSWORD")
    if bad:
        R.fail("database settings differ between pipeline and backend", ", ".join(bad),
               "the pipeline WRITES what the backend READS; edit pipeline/settings.json then run "
               "`python env_from_settings.py`")
    else:
        R.ok("database settings identical in pipeline and backend")
    if pcfg.EMBED_MODEL != config.EMBEDDING_MODEL:
        R.fail("EMBED_MODEL (pipeline) != EMBEDDING_MODEL (backend)", f"{pcfg.EMBED_MODEL} vs {config.EMBEDDING_MODEL}",
               "both sides must embed with the SAME model or search results are meaningless")
    else:
        R.ok("embedding model identical in pipeline and backend", pcfg.EMBED_MODEL)
    base = pcfg.LLM_BASE_URL.rstrip("/")
    host = base[:-3].rstrip("/") if base.endswith("/v1") else base
    if host != config.INFERENCE_HOST.rstrip("/"):
        R.warn("LLM_BASE_URL (pipeline) points elsewhere than INFERENCE_HOST (backend)",
               f"{pcfg.LLM_BASE_URL} vs {config.INFERENCE_HOST}",
               "both normally point at the same router; fix settings.json and run env_from_settings.py")
    if db_dim and pcfg.EMBED_DIM != db_dim:
        R.fail("EMBED_DIM != DB vector width", f"{pcfg.EMBED_DIM} vs {db_dim}")
    if ini_sections:
        for key in ("MODEL_PASS_2", "MODEL_PASS_2B", "MODEL_PASS_3", "MODEL_PASS_3B", "MODEL_PASS_4", "EMBED_MODEL"):
            name = getattr(pcfg, key)
            if name in ini_sections:
                R.ok(f"{key} = {name}", "defined in config.ini")
            else:
                R.fail(f"{key} = {name} has no [section] in config.ini", None,
                       "add the section or change the name in pipeline/settings.json")

    # models.json vs config.ini
    mpath = os.path.join(PIPELINE_DIR, "models.json")
    if os.path.exists(mpath):
        with open(mpath, "r", encoding="utf-8") as f:
            man = json.load(f)
        mdir = man.get("model_dir") or ""
        if "/path/to/" in mdir or not os.path.isdir(mdir):
            R.warn("models.json model_dir is not a real folder", mdir,
                   "only matters for the in-app GGUF downloader; set it to the folder config.ini loads from")
        else:
            missing = [m["filename"] for m in man.get("models", []) if not os.path.exists(os.path.join(mdir, m["filename"]))]
            if missing:
                R.warn("GGUF files listed in models.json but absent from model_dir", ", ".join(missing),
                       "download them (GUI 'Download models...' / TUI 'm') or fix the filenames")
            else:
                R.ok("all GGUFs in models.json present", mdir)


# --------------------------------------------------------------------------- #

def wait_models(timeout_s: int) -> int:
    """Block until every load-on-startup model in the preset reports `loaded` (used by the
    launchers so the backend does not start against half-loaded models)."""
    import time
    import requests
    import config
    path = os.environ.get("PRESET") or os.path.join(BASE_DIR, "config.ini")
    wanted = []
    if os.path.exists(path):
        cp = load_preset(path)
        wanted = [s for s in cp.sections() if s not in ("__top__", "*")
                  and (cp.get(s, "load-on-startup", fallback="") or "").strip().lower() in ("true", "1", "on", "yes")]
    url = f"{config.INFERENCE_HOST}/v1/models"
    t0 = time.time()
    last = ""
    unloaded_since: dict = {}   # an evicted model never comes back by itself - stop waiting on it
    while time.time() - t0 < timeout_s:
        try:
            data = requests.get(url, timeout=5).json().get("data", [])
        except Exception as e:  # noqa: BLE001
            print(f"waiting for the router at {url} ({e.__class__.__name__}) ...")
            time.sleep(3)
            continue
        st = {}
        for m in data:
            s = m.get("status")
            st[m.get("id")] = s.get("value") if isinstance(s, dict) else (s or "?")
        now = time.time()
        for n in wanted:
            if st.get(n) == "unloaded":
                unloaded_since.setdefault(n, now)
            else:
                unloaded_since.pop(n, None)
        stuck = [n for n, t in unloaded_since.items() if now - t > 30]
        if stuck:
            print(f"WARN: {', '.join(stuck)} stayed `unloaded` for 30 s - evicted by another model "
                  "(raise MODELS_MAX) or never requested; it will load on first use. Continuing.")
            return 0
        pending = [n for n in wanted if st.get(n) != "loaded"]
        failed = [n for n in wanted if st.get(n) not in (None, "loaded", "loading", "unloaded")]
        line = ", ".join(f"{n}={st.get(n, 'missing')}" for n in wanted) or "(no load-on-startup models in preset)"
        if line != last:
            print(line)
            last = line
        if failed:
            print(f"FAIL: model(s) failed to load: {', '.join(failed)} - see the router log")
            return 1
        if not pending:
            print(f"all load-on-startup models loaded ({int(time.time() - t0)} s)")
            return 0
        time.sleep(3)
    print(f"WARN: still loading after {timeout_s} s: {', '.join(pending)} - continuing")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Preflight checks for the HIWIN Support Agent.")
    ap.add_argument("--wait-models", action="store_true",
                    help="only wait until every load-on-startup model in config.ini reports loaded (max 20 min)")
    ap.add_argument("--pipeline", action="store_true", help="also check the ingestion pipeline setup")
    ap.add_argument("--live", action="store_true", help="also make real embed / rerank / chat calls")
    ap.add_argument("--all", action="store_true", help="--pipeline --live")
    args = ap.parse_args()
    if args.wait_models:
        return wait_models(1200)
    pipeline = args.pipeline or args.all
    live = args.live or args.all

    print(f"HIWIN Support Agent doctor - {BASE_DIR}")
    check_python()
    check_packages(pipeline)
    config = check_env()
    ini_sections = set()
    db_info = {"dim": None}
    if config is not None:
        required = {config.LANGUAGE_MODEL: ".env LANGUAGE_MODEL", config.EMBEDDING_MODEL: ".env EMBEDDING_MODEL",
                    config.RERANKER_MODEL: ".env RERANKER_MODEL", config.COMPLETENESS_MODEL: ".env COMPLETENESS_MODEL"}
        ini_sections = check_preset(required, set(required)) or set()
        check_scripts()
        db_info = check_db(config) or db_info
        check_inference(config, live, db_info.get("dim"))
        if pipeline:
            check_pipeline(config, ini_sections, db_info.get("dim"))

    print()
    if R.fails:
        print(f"RESULT: {R.fails} FAIL, {R.warns} WARN - fix the FAIL lines above (top to bottom), then re-run.")
        return 1
    if R.warns:
        print(f"RESULT: no failures, {R.warns} warning(s).")
    else:
        print("RESULT: all checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
