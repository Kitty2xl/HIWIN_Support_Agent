"""
i18n.py — tiny UI-localisation layer shared by gui.py and tui.py.

Holds an English / Traditional-Chinese string catalog and a single current
language. Both front-ends read their on-screen text through here, so a language
toggle flips the whole UI consistently and the two can never drift.

  from core import i18n
  i18n.t("save_launch")            # current-language UI string
  i18n.section("Paths")            # translated settings section title
  i18n.field("DB_PASS", fallback)  # translated settings field label (by key)
  i18n.toggle()                    # switch en <-> tc

Only UI chrome is translated. Pipeline-emitted log/step text (from Pipeline.py)
is passed through verbatim in whatever language the pipeline produced it.
"""

LANGS = ("tc", "en")
DEFAULT_LANG = "tc"          # HIWIN is a Taiwanese company; default to 繁體中文

_lang = DEFAULT_LANG


def get_lang() -> str:
    return _lang


def set_lang(lang: str) -> None:
    global _lang
    if lang in LANGS:
        _lang = lang


def toggle() -> str:
    set_lang("en" if _lang == "tc" else "tc")
    return _lang


def lang_button_label() -> str:
    """Label for the toggle control — shows the language you'd switch TO."""
    return "EN" if _lang == "tc" else "中"


# ── General UI chrome ────────────────────────────────────────────────────────
STRINGS = {
    "app_title":         {"en": "HIWIN Document Pipeline", "tc": "HIWIN 文件處理管線"},
    "settings_subtitle": {"en": "Settings — configure the pipeline, then launch the monitor",
                          "tc": "設定 — 設定管線後啟動監控"},
    "monitor_subtitle":  {"en": "Document processing monitor", "tc": "文件處理監控"},
    "reset_defaults":    {"en": "Reset to defaults", "tc": "重設為預設值"},
    "save_launch":       {"en": "Save & Launch", "tc": "儲存並啟動"},
    "browse":            {"en": "Browse…", "tc": "瀏覽…"},
    "start_pipeline":    {"en": "Start Pipeline", "tc": "開始管線"},
    "stop":              {"en": "Stop", "tc": "停止"},
    "stopping":          {"en": "Stopping…", "tc": "停止中…"},
    "log":               {"en": "Log", "tc": "記錄"},
    "clear":             {"en": "Clear", "tc": "清除"},
    "col_pdf":           {"en": "PDF", "tc": "PDF"},
    "col_progress":      {"en": "Progress", "tc": "進度"},
    "col_step":          {"en": "Step", "tc": "步驟"},
    "sum_total":         {"en": "PDFs", "tc": "PDF 數"},
    "sum_running":       {"en": "Running", "tc": "執行中"},
    "sum_done":          {"en": "Done", "tc": "完成"},
    "sum_errors":        {"en": "Errors", "tc": "錯誤"},
    "completed":         {"en": "Completed", "tc": "已完成"},
    "status_idle":       {"en": "● Idle", "tc": "● 閒置"},
    "status_running":    {"en": "● Running", "tc": "● 執行中"},
    "status_done":       {"en": "✓ Done", "tc": "✓ 完成"},
    "status_stopped":    {"en": "■ Stopped", "tc": "■ 已停止"},
    "status_error":      {"en": "✗ Error", "tc": "✗ 錯誤"},
    "phase_idle":        {"en": "Idle", "tc": "閒置"},
    "phase_starting":    {"en": "Starting…", "tc": "啟動中…"},
    "phase_a":           {"en": "Phase A — Passes 1–3", "tc": "階段 A — Pass 1–3"},
    "phase_b":           {"en": "Phase B — Passes 3b & 4", "tc": "階段 B — Pass 3b 與 4"},
    "phase_complete":    {"en": "Complete", "tc": "完成"},
    "phase_stopped":     {"en": "Stopped", "tc": "已停止"},
    "phase_dash":        {"en": "—", "tc": "—"},
    "msg_invalid":       {"en": "Invalid value for '{label}'.", "tc": "「{label}」的值無效。"},
    "msg_save_err":      {"en": "Could not save settings: {exc}", "tc": "無法儲存設定：{exc}"},
    "log_phase_started": {"en": "{label} started — {total} PDF(s)",
                          "tc": "{label} 已開始 — {total} 個 PDF"},
    "log_phase_done":    {"en": "Phase {phase} complete", "tc": "階段 {phase} 完成"},
    "log_done":          {"en": "Pipeline finished successfully.", "tc": "管線已成功完成。"},
    "log_stopped":       {"en": "Pipeline stopped by user.", "tc": "管線已由使用者停止。"},
    "log_stop_req":      {"en": "Stop requested — will halt at the next phase boundary.",
                          "tc": "已要求停止 — 將於下一個階段邊界停止。"},
    # TUI-only
    "tier_common":       {"en": "Common", "tc": "常用"},
    "tier_advanced":     {"en": "Advanced", "tc": "進階"},
    "tui_select":        {"en": "Select", "tc": "選擇"},
    "tui_start":         {"en": "Start", "tc": "開始"},
    "tui_menu":          {"en": "Enter a number to edit a field · s save & launch · "
                                "m models · l language · r reset to defaults · q quit",
                          "tc": "輸入數字以編輯欄位 · s 儲存並啟動 · m 模型 · "
                                "l 切換語言 · r 重設為預設值 · q 離開"},
    "tui_settings_title":{"en": "Settings — configure the pipeline, then save & launch",
                          "tc": "設定 — 設定管線後儲存並啟動"},
    "tui_col_num":       {"en": "#", "tc": "#"},
    "tui_col_section":   {"en": "Section", "tc": "區段"},
    "tui_col_setting":   {"en": "Setting", "tc": "設定項"},
    "tui_col_value":     {"en": "Value", "tc": "值"},
    "tui_loaded":        {"en": "{n} PDF(s) loaded. Press Enter to start, or q then Enter to quit.",
                          "tc": "已載入 {n} 個 PDF。按 Enter 開始，或輸入 q 後按 Enter 離開。"},
    "tui_keep_current":  {"en": "blank = keep current", "tc": "留空 = 保留目前值"},
    "tui_comma_sep":     {"en": "comma-separated", "tc": "以逗號分隔"},
    "tui_aborted":       {"en": "Aborted.", "tc": "已中止。"},
    "tui_pipeline_end":  {"en": "Pipeline {status} — elapsed {elapsed}",
                          "tc": "管線{status} — 經過時間 {elapsed}"},
    "tui_press_enter":   {"en": "Press Enter to continue", "tc": "按 Enter 繼續"},
    "tui_bad_value":     {"en": "Invalid value for '{label}'.", "tc": "「{label}」的值無效。"},
    "tui_bad_choice":    {"en": "Unrecognized choice.", "tc": "無法辨識的選項。"},
    "unset":             {"en": "(unset)", "tc": "（未設定）"},
    "empty":             {"en": "(empty)", "tc": "（空）"},
    "yes":               {"en": "yes", "tc": "是"},
    "no":                {"en": "no", "tc": "否"},
    # GGUF model download (Hugging Face)
    "models_button":     {"en": "Download models…", "tc": "下載模型…"},
    "models_title":      {"en": "Model files (GGUF)", "tc": "模型檔案（GGUF）"},
    "models_present":    {"en": "Present: {n}", "tc": "已存在：{n}"},
    "models_all_present":{"en": "All required GGUFs are already present in {dir}.",
                          "tc": "所需的 GGUF 皆已存在於 {dir}。"},
    "models_missing":    {"en": "Missing {n} model file(s):", "tc": "缺少 {n} 個模型檔案："},
    "models_confirm":    {"en": "Download {n} missing model(s) from Hugging Face into {dir}?",
                          "tc": "要從 Hugging Face 下載 {n} 個缺少的模型至 {dir} 嗎？"},
    "models_downloading":{"en": "Downloading {name} ({i}/{n})…",
                          "tc": "正在下載 {name}（{i}/{n}）…"},
    "models_done":       {"en": "Downloaded {n} model(s) to {dir}.",
                          "tc": "已下載 {n} 個模型至 {dir}。"},
    "models_failed":     {"en": "Download failed: {err}", "tc": "下載失敗：{err}"},
}

_STATUS_KEY = {
    "idle": "status_idle", "running": "status_running", "done": "status_done",
    "stopped": "status_stopped", "error": "status_error",
}


def t(key: str, **fmt) -> str:
    s = STRINGS.get(key, {}).get(_lang, key)
    return s.format(**fmt) if fmt else s


def status_text(status: str) -> str:
    return t(_STATUS_KEY.get(status, "status_idle"))


# ── Settings SCHEMA labels (section titles + field labels by key) ─────────────
SECTIONS = {
    "Paths": {"en": "Paths", "tc": "路徑"},
    "LLM Server (local — Pass 3b/4 & ingest)":
        {"en": "LLM Server (local — Pass 3b/4 & ingest)",
         "tc": "LLM 伺服器（本地 — Pass 3b/4 與匯入）"},
    "Pass34 inference nodes (Pass 2/2b/3)":
        {"en": "Pass34 inference nodes (Pass 2/2b/3)",
         "tc": "Pass34 推論節點（Pass 2/2b/3）"},
    "Models": {"en": "Models", "tc": "模型"},
    "Pass 1 — layout detection":
        {"en": "Pass 1 — layout detection", "tc": "Pass 1 — 版面偵測"},
    "Concurrency": {"en": "Concurrency", "tc": "並行數"},
    "Behaviour toggles": {"en": "Behaviour toggles", "tc": "行為開關"},
    "Database & ingestion": {"en": "Database & ingestion", "tc": "資料庫與匯入"},
}

FIELDS = {
    "ROOT_PATH":           {"en": "Project root (HIWIN folder)", "tc": "專案根目錄（HIWIN 資料夾）"},
    "IMAGE_TARGET_ROOT":   {"en": "Web static image target", "tc": "網頁靜態圖片目標"},
    "LLM_BASE_URL":        {"en": "LLM base URL", "tc": "LLM 基礎 URL"},
    "LLAMA_SWAP_URL":      {"en": "llama-swap URL", "tc": "llama-swap URL"},
    "LLM_API_KEY":         {"en": "API key", "tc": "API 金鑰"},
    "LLM_TIMEOUT":         {"en": "Request timeout (ms)", "tc": "請求逾時（毫秒）"},
    "LLM_PREHEAT_TIMEOUT": {"en": "Preheat timeout (s)", "tc": "預熱逾時（秒）"},
    "LLM_MAX_RETRIES":     {"en": "Max retries per call", "tc": "每次呼叫最大重試次數"},
    "PASS34_NODE_URLS":    {"en": "Node URLs (one per line)", "tc": "節點 URL（每行一個）"},
    "PASS34_NODE_SWAP_URLS": {"en": "Node llama-swap URLs (one per line)", "tc": "節點 llama-swap URL（每行一個）"},
    "MODEL_PASS_2":        {"en": "Pass 2 model (page → markdown)", "tc": "Pass 2 模型（頁面 → markdown）"},
    "MODEL_PASS_2B":       {"en": "Pass 2b model (figure caption)", "tc": "Pass 2b 模型（圖片說明）"},
    "MODEL_PASS_3":        {"en": "Pass 3 model (table → markdown)", "tc": "Pass 3 模型（表格 → markdown）"},
    "MODEL_PASS_3B":       {"en": "Pass 3b model (table summary)", "tc": "Pass 3b 模型（表格摘要）"},
    "MODEL_PASS_4":        {"en": "Pass 4 model (validation)", "tc": "Pass 4 模型（驗證）"},
    "SCORE_THRESHOLD":     {"en": "Detection confidence (0–1)", "tc": "偵測信心度（0–1）"},
    "PASS_1_RENDER_DPI":   {"en": "Render DPI", "tc": "渲染 DPI"},
    "BATCH_SIZE_PASS_1":   {"en": "Pages per batch", "tc": "每批頁數"},
    "ORT_INTRA_THREADS":   {"en": "ONNX intra-op threads (blank = auto)", "tc": "ONNX intra-op 執行緒（留空 = 自動）"},
    "MAX_PDF_WORKERS":     {"en": "PDFs processed at once (0 = auto)", "tc": "同時處理的 PDF 數（0 = 自動）"},
    "CONCURRENCY":         {"en": "Pass 2/2b/3 async workers", "tc": "Pass 2/2b/3 非同步工作數"},
    "CONCURRENCY_PASS_4":  {"en": "Pass 3b/4 concurrent calls", "tc": "Pass 3b/4 並行呼叫數"},
    "PASS_2_SKIP_BLANK_PAGES": {"en": "Skip blank pages in Pass 2", "tc": "Pass 2 跳過空白頁"},
    "PASS_2_BLANK_DRY_RUN": {"en": "Blank detection dry-run (log only)", "tc": "空白偵測試跑（僅記錄）"},
    "PASS_4_GATE_VALIDATION": {"en": "Only validate malformed pages", "tc": "僅驗證格式異常的頁面"},
    "TIMEOUT_RETRY_ENABLED": {"en": "Retry timed-out passes after run", "tc": "執行後重試逾時的 pass"},
    "DB_HOST":             {"en": "DB host", "tc": "資料庫主機"},
    "DB_PORT":             {"en": "DB port", "tc": "資料庫連接埠"},
    "DB_NAME":             {"en": "DB name", "tc": "資料庫名稱"},
    "DB_USER":             {"en": "DB user", "tc": "資料庫使用者"},
    "DB_PASS":             {"en": "DB password", "tc": "資料庫密碼"},
    "DB_SCHEMA":           {"en": "DB schema", "tc": "資料庫 schema"},
    "EMBED_BATCH_SIZE":    {"en": "Embedding batch size", "tc": "向量批次大小"},
    "INGEST_NUM_WORKERS":  {"en": "Ingestion workers", "tc": "匯入工作數"},
    "INGEST_BY_PAGE":      {"en": "Ingest one node per page", "tc": "每頁建立一個節點"},
}


def section(name: str) -> str:
    return SECTIONS.get(name, {}).get(_lang, name)


def field(key: str, fallback: str = "") -> str:
    return FIELDS.get(key, {}).get(_lang, fallback or key)
