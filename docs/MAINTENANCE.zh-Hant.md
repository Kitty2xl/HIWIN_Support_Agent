# 維護指南

[English](MAINTENANCE.md) · [繁體中文]

給負責讓 HIWIN Support Agent 持續運作的人的維運手冊。安裝見 [README](../README.zh-Hant.md)；
設計見 [ARCHITECTURE.zh-Hant.md](ARCHITECTURE.zh-Hant.md)。

內容：[可以改什麼](#1-可以自由更改什麼必須保持一致什麼) ·
[調整答覆](#2-調整答覆) · [知識庫](#3-更動知識庫) ·
[還原圖示](#還原圖示) · [備份](#4-備份) ·
[模型與 llama.cpp](#5-更新模型或-llamacpp) · [密碼](#6-更換資料庫密碼) ·
[例行檢查](#7-例行檢查清單) · [變更歷史](#8-過去改了什麼為什麼)

---

## 1. 可以自由更改什麼、必須保持一致什麼

**可自由更改（沒有其他檔案依賴它）：**

| 項目 | 位置 | 生效 |
|---|---|---|
| 用語、語氣、路由規則、超出範圍範本 | `prompts/System_prompt.md`、`prompts/skills/*.md` | 下一個請求（不需重啟——每次請求重新讀檔） |
| 檢索深度／速度旋鈕 | `.env`（`VECTOR_TOP_K`、`RERANK_*`、`COMPLETENESS_*`、`FIGURE_TEXT_MAX_CHARS`…） | 重啟後端 |
| 取樣、脈絡長度、GPU 層數、模型的量化版本 | `config.ini` | 重啟路由 |
| 模型讀到的工具描述 | `tool_schemas.py` | 重啟後端 |
| 展示頁 | `frontend/index.html` | 重新整理頁面 |
| 匯入哪些 PDF／頁面 | `<ROOT_PATH>/PDF_Config.yaml` | 下次管線執行 |

**必須保持一致（一次全改，然後 `python doctor.py --all`）：**

| 改了 | 也要改 |
|---|---|
| 模型**名稱**（`config.ini` 的 `[區段]`） | `.env`（`LANGUAGE_MODEL` / `EMBEDDING_MODEL` / `RERANKER_MODEL` / `COMPLETENESS_MODEL`）及／或 `pipeline/settings.json`（`MODEL_PASS_*`、`EMBED_MODEL`） |
| 資料庫主機／埠／名稱／使用者／密碼／schema | `pipeline/settings.json`，**然後** `python env_from_settings.py`（就地更新 `.env`） |
| 圖示資料夾 | `settings.json` `IMAGE_TARGET_ROOT` → `env_from_settings.py` → `.env` `IMAGE_STATIC_ROOT` |
| **向量化模型**（或其量化版本） | `EMBED_MODEL` + `EMBEDDING_MODEL`、`pipeline/core/config.py` 的 `EMBED_DIM`，並**重新匯入所有文件**——舊向量與新模型不相容。絕不能只改名稱。 |
| 語言代碼 | `config.py` `LANGUAGE_SKILL_MAP` + 一份 skill 檔 + `PDF_Config.yaml` 的 `language` 值 |

**無故不要碰：** `db.py` 的 base64 SQL 常數（溯源）、`rag_tools.py` 的 `[SOURCE: …]` 標記
（引用依賴它）、系統提示與 `db.resolve_tables` 的 `data_` 前綴邏輯。

## 2. 調整答覆

### 行為定義在哪裡

| 層 | 檔案 | 在那裡改什麼 |
|---|---|---|
| 全域行為、狀態機、召回規則、引用與圖片規則 | `prompts/System_prompt.md` | 語氣、窮盡程度、輸出版面（條列 vs 表格）、後備條件 |
| 各語言路由 | `prompts/skills/english_support.md`、`japanese_support.md`、`traditional_chinese_support.md`、`simplified_chinese_support.md` | 12 種問題類別、哪些會回答（1–2）、聯絡表單範本與 URL |
| 完整性列舉＋整合提示 | `agent.py` → `_enumerate_passage`、`_completeness_pass` | 如何抽列與合併；`NONE — 原因` 行為 |
| 檢索圖示的視覺提示 | `rag_tools.py` → `_run_vision_analysis` | 要視覺模型從圖上讀什麼 |
| 相關性評分提示（預設關閉） | `rag_tools.py` → `_grade_passage` | |
| 模型認為每個工具做什麼 | `tool_schemas.py` | 描述與參數提示 |
| 取樣 | `.env` `TEMPERATURE`（對話模型，每請求）；`config.ini` `temp`（其餘） | |

提示詞為純 Markdown／文字；請保留 `System_prompt.md` 的區段名稱（STATE 0–4），skill 會引用。

### 旋鈕，依影響大小排序

1. **`COMPLETENESS_MODE` / `COMPLETENESS_PASS_ENABLED`**——「三列範例」與「每一列」的差別。
   `pre_draft`（正式）較緊湊、主模型一次；`post_draft` 兩次並多一步整合。
2. **`RERANK_TOP_K`**（6）——段落越多召回越高、脈絡越大、越慢。同步調高 `RERANK_CANDIDATE_K`。
3. **`VECTOR_TOP_K`**（每表 15）——只在某產品的該屬性跨很多頁時才重要。
4. **`RERANK_DOC_MAX_CHARS`**（1000）——減半使重排時間減半且排名無可量測損失；訊號在段落開頭。
5. **`FIGURE_TEXT_MAX_CHARS`**（120）與 **`KEEP_RECENT_TOOL_RESULTS`**（−1 = 全保留）——脈絡
   控制。若看到 `n_ctx` 溢位，先降 `RERANK_TOP_K`，再設 `KEEP_RECENT_TOOL_RESULTS=4`
   （只在 `post_draft` 下合理）。
6. **`GRADING_ENABLED`**——保持關閉；它是精確度過濾，會丟掉相關的型號頁。
7. **`reasoning-budget`**（`config.ini`，對話模型）——8192 阻止失控思考。不要移除。

### 如何量測

```bash
python run_prompts.py                                  # 所有 examples/*.json
python run_prompts.py my_questions.jsonl --timeout 900  # 每行一個 {"prompt","language"}
```

每次執行寫出 `results_<timestamp>.json`（完整回應：答覆、來源、追蹤、指標）並印出每種呼叫
類型的延遲（`agent`、`vision`、`completeness_enum`、`completeness_merge`、`embed`、`rerank`）。
改動前後各跑一次比較。正式紀錄在對話記錄：

```sql
SELECT created_at, language, latency_ms, llm_calls, tool_calls, total_tokens, left(prompt, 60)
FROM hiwin_cs_db.chat_logs ORDER BY created_at DESC LIMIT 50;
```

展示頁的 **Debug view** 是看答覆*為何如此*最快的方式：搜了哪些表與語言、每次搜尋回傳
什麼、每段的完整性列舉是找到列還是回 `NONE — <原因>`（原因不對表示過度過濾）。

## 3. 更動知識庫

### 文件如何儲存

`hiwin_rag.data_<product>` 每列一頁 PDF；`metadata_.file_name` 是文件 id
`<Product>_<sub_folder>_<lang>`（如 `Ballscrew_ballscrew-(c)_tc`），`page_number` 頁碼、
`language_code` 語言、`version` 匯入世代。重新匯入某文件會刪除舊列並以 `version + 1` 寫入
新列。文字引用的圖示／表格位於 `IMAGE_TARGET_ROOT/<product>/<sub_folder>/<lang>/Figures|Tables/`。

### 新增一個 PDF

1. 複製到 `<ROOT_PATH>/PDFs/<Product>/[<子資料夾>/]<file>.pdf`。新的 `<Product>` 資料夾 =
   新的 `data_<product>` 資料表（自動建立）。
2. 在 `<ROOT_PATH>/PDF_Config.yaml` 加一筆（語言 `en|jp|tc|sc`、要排除的頁如封面）。或刪掉
   YAML 讓管線重新產生——但要重新檢查每一筆既有項目。
3. 啟動路由並留空間給管線模型（啟動腳本 `MODELS_MAX=6`，或趁後端閒置時跑管線）。
4. 執行管線（`cd pipeline && python gui.py` / `tui.py` / `Pipeline.py`）。已完成的文件由
   `checkpoint.json` 略過；只處理並匯入新文件。
5. `python inspect_metadata.py`——新文件的列出現；問代理一個相關問題。
   `db_get_available_product_tables` 自動看到新資料表。

### 重新匯入（更新）文件

- **先讀 [INGESTION_DEFECTS.md](INGESTION_DEFECTS.md)。** 滾珠螺桿型錄 tc 第 12、13、43 頁
  與 en 第 12 頁曾在資料庫手工修復。重新匯入 `Ballscrew` 會還原有缺陷的轉錄；你必須重新
  套用修復（修復前的快照是 `hiwin_rag.repair_backup_*`；修復後的列就是目前的列——重新匯入
  前先複製保存）。
- 強制重跑：從 `<ROOT_PATH>/checkpoint.json` 移除該文件的 pass（或整筆），再執行管線；
  只重新向量化：`python -m ingestion.Ingest --force`（需要 `Process_Files`）。
- 新版型錄：以相同檔名替換 PDF 後重跑；或以不同檔名保留兩者並移除舊列（見下）。

### 移除文件

```sql
DELETE FROM hiwin_rag.data_ballscrew WHERE metadata_->>'file_name' = 'Ballscrew_ballscrew-(c)_tc';
```

然後刪除其 `checkpoint.json` 項目，以免之後的管線執行視為已完成；需要的話也刪掉它的圖示資料夾。

### 新增語言

新增 `prompts/skills/<lang>_support.md`，在 `config.py` `LANGUAGE_SKILL_MAP` 對應代碼，在
`PDF_Config.yaml` 使用該代碼，匯入。前端的語言 `<select>` 是寫死的清單。

### 還原圖示

後端於 `/static/HIWIN` 提供 `IMAGE_STATIC_ROOT`，視覺步驟也讀同一批檔案。資料夾內必須有
`<product>/<sub_folder>/<lang>/Figures|Tables/*.jpg`，與 Markdown 連結完全對應。若不存在
（如目前機器）：

1. 解壓 `Final_Output.7z`（部署資料夾內；4.5 GB、5.7 萬檔），使
   `<ROOT_PATH>/Final_Output/<Product>/…` 存在。
2. 確認 `settings.json` `IMAGE_TARGET_ROOT` == `.env` `IMAGE_STATIC_ROOT`。
3. `cd pipeline && python -m ingestion.Ingest --assets-only`——把每個 `Figures/` 與 `Tables/`
   複製到位；不向量化、不寫資料庫。
4. 重啟後端；`python doctor.py` 應顯示 `[PASS] IMAGE_STATIC_ROOT`，答覆開始帶圖片與視覺分析。

或者從已有該資料夾的機器複製過來。

## 4. 備份

- **資料庫**（最昂貴的部分）：依 README（[§9](../README.zh-Hant.md#備份與搬移資料庫)）
  `pg_dump`。排程執行；自訂格式的 dump 與 GGUF 放一起。
- **圖示**：`IMAGE_STATIC_ROOT` 資料夾，或 `Final_Output.7z` 封存。
- **輸入**：`PDFs/`、`PDF_Config.yaml`、ONNX 模型——只有重建才需要。
- **設定**：本專案（`.env`、`settings.json`、`config.ini` 已版控）。
- `hiwin_rag` 內的 `bak_*` / `repair_backup_*` / `link_backup_*` 是手工修復（2026-08/09）的
  歷史快照。應用程式不讀它們；在對應修正進入管線前請保留。

## 5. 更新模型或 llama.cpp

- **新版 llama.cpp：** 替換執行檔、重啟、`python doctor.py --live`。路由模式（`--models-preset`）
  與 `/v1/rerank` 端點必須仍受支援；若 preset 格式改變，依發行說明調整。
- **對話／aux／reranker 模型的新量化版本：** 改 `model =` 行、重啟、跑 `run_prompts.py` 比較。
  不影響資料。
- **新的向量化模型或量化版本：** 視為重建——改 `EMBED_MODEL` / `EMBEDDING_MODEL`、設
  `EMBED_DIM`、刪除或改名舊的 `data_*` 資料表、重新匯入全部。`doctor.py --live` 會比較模型
  輸出寬度與資料庫欄位，不符時明確失敗。
- **GPU 變動：** 機器共用時把常駐模型組維持在一張卡內（啟動腳本的 `CUDA_VISIBLE_DEVICES`）。
  驅動更新後看 `nvidia-smi`——CUDA 索引可能改變。

## 6. 更換資料庫密碼

```sql
ALTER USER postgres WITH PASSWORD 'new-password';
```

然後 `pipeline/settings.json` → `DB_PASS`、`python env_from_settings.py`（更新 `.env`）、
重啟後端、提交。兩個檔案刻意納入版控（內部專案）；專案對外分享前務必更換。

## 7. 例行檢查清單

- **每週：** `python doctor.py --live`；看一眼 `hiwin_cs_db.chat_logs` 有無延遲飆高或錯誤；
  資料庫與 dump 的磁碟空間。
- **任何設定變更後：** `python doctor.py --all`，重啟受影響的程序（`config.ini` → 路由，
  `.env`／提示詞 → 後端，提示詞檔案不需重啟）。
- **新增文件後：** `inspect_metadata.py`、一個測試問題、一份新的 `pg_dump`。
- **交接機器給他人前：** 更新 README §3（部署快照）與 AGENTS.md §6。

## 8. 過去改了什麼、為什麼

`config.ini` / `.env` 的註解只說了一部分，故記錄於此。

| 時間 | 變更 | 原因 |
|---|---|---|
| 2026-07 | 路由模式取代 llama-swap；四個服務模型全部常駐 | 對話／向量化／重排序／aux 之間不再切換模型 |
| 2026-07 | `COMPLETENESS_MODE=pre_draft`，列舉改用 aux 模型 `Support_Agent_Aux` | 在可接受的延遲下取得完整的規格表答覆 |
| 2026-07 | `RERANK_DOC_MAX_CHARS` 2000 → 1000 | 重排 prefill 每次約 15 秒；減半後時間減半且排名無損 |
| 2026-08-14 | 對話模型 `reasoning-budget = 8192` | 三個問題產生 112k–145k 推理 token 並回傳空答覆 |
| 2026-08-18 | 資料庫中手工修復滾珠螺桿第 12/13/43 頁 | 轉錄缺陷（見 INGESTION_DEFECTS.md）；管線尚未修正 |
| 2026-08-26 | 路由固定於單一 GPU | 模型一部分落到正在跑別人工作的卡上；延遲 71 秒 → 36 分鐘 |
| 2026-08-28 | 向量化／reranker／aux 的 `parallel = 1` | 相同請求在不同 slot 回傳不同向量；排名飄移 |
| 2026-09-11/13 | 所有資料表的 `bak_data_*` 快照 | 進一步批次修復前 |
| 2026-09-16 | 交接稽核：`doctor.py`、`setup_db.py`、就地更新的 `env_from_settings.py`、`--assets-only`、`EMBED_MODEL` 設定、`.gitattributes`、headless OpenCV、文件重寫 | 讓新人能在 Windows 或 Linux 上重建本專案 |
