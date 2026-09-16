# 系統架構

[English](ARCHITECTURE.md) · [繁體中文]

本文件說明 **HIWIN Support Agent** 的組成方式與設計理由。安裝與使用請見
[README](../README.zh-Hant.md)；日常維運請見 [MAINTENANCE.zh-Hant.md](MAINTENANCE.zh-Hant.md)。

## 1. 兩個半邊與它們的連結

```
            PDF ─▶ pipeline/（版面偵測 → VLM 轉錄 → 驗證 → 向量化）─▶ PostgreSQL + pgvector
                                                                              │  data_* 資料表
                                                                              ▼
用戶端 ─▶ POST /chat ─▶ 後端（代理迴圈 + 4 個檢索工具）◀────────────── 向量化 / 重排序 / 對話
                                                                    llama.cpp 路由（config.ini）
```

兩半以模型「名稱」呼叫同一個 llama.cpp 路由，讀寫同一個資料庫。它們的設定檔
各自獨立（`pipeline/settings.json`、`.env`），但必須一致——`env_from_settings.py`
負責複製共用鍵，`doctor.py` 負責檢查。

## 2. 請求生命週期（後端）

```
用戶端 ──POST /chat {prompt, language}──▶ main.py
  1. 解析語言（請求值，否則 DEFAULT_LANGUAGE）
  2. prompts.build_system(language)         → System_prompt.md + skills/<lang>.md
  3. build_user_message(prompt, language)   → "[Language Code: xx]\n<prompt>"
  4. agent.run(system, user_msg)            → 工具呼叫迴圈（＋完整性檢查）
  5. 將 sources 修剪為答覆實際引用的頁碼
  6. 盡力寫入 hiwin_cs_db.chat_logs
  7. 回傳 ChatResponse {response, language, sources, trace, metrics}
```

迴圈旁有三條側通道：`trace`（每次工具呼叫及其結果預覽）、`sources`（去重後的引用
中繼資料，含 reranker 分數）與 `metrics`（每次呼叫的耗時與 token，見 §7）。

## 3. 狀態機（提示詞）

行為以自然語言定義於 `prompts/System_prompt.md`，模型以狀態機方式執行：

| 狀態 | 用途 | 工具 |
|---|---|---|
| **0 — 路由** | 偵測語言、載入對應 skill、分類問題、選擇路徑。 | — |
| **1 — 資料庫查詢** | 拆解問題（產品範圍、屬性、相鄰元件），逐關鍵字檢索所有相關事實。 | `db_get_available_product_tables`、`db_search_technical_manuals` |
| **2 — URL 檢索** | 回傳下載／CAD 連結。 | `db_search_product_urls` |
| **3 — 格式化回應** | 以使用者語言列出每個數值的條列＋重點＋引用，語氣為顧問式。 | — |
| **4 — 後備** | 對超出範圍或無結果者輸出 skill 的聯絡表單範本。 | — |

**skills**（`prompts/skills/*.md`）承載各語言的路由規則：將問題分為 12 類，僅回答
第 1、2 類（產品／文件問題），其餘回傳當地語言的「請填寫聯絡表單」範本。

模型並非由 Python 逐步編排——它透過 OpenAI 風格的函式呼叫自行決定何時呼叫哪個
工具。Python 端負責執行工具、回饋結果，並加上完整性檢查。

## 4. 模組

| 模組 | 職責 |
|---|---|
| `main.py` | FastAPI 應用；`/chat`、`/health`、`/` 展示頁、`/static/HIWIN` 圖片掛載；引用修剪；對話記錄。 |
| `agent.py` | 工具呼叫迴圈（並行工具呼叫、工具歷史修剪、迭代上限）與**完整性檢查**（`pre_draft` / `post_draft`）。 |
| `tool_schemas.py` | 四個工具的 OpenAI 函式 schema 與名稱→函式的 `DISPATCH` 對應。 |
| `rag_tools.py` | 四個工具：pgvector 搜尋（逐表並行）、列出資料表、證書、URL 列表——以及重排序、選用的相關性評分與視覺輔助。 |
| `inference.py` | `/v1/chat/completions`、`/v1/embeddings`、`/v1/rerank` 的 HTTP 封裝；以 contextvar 記錄每次呼叫的耗時／token。 |
| `db.py` | 執行緒連線池、SQL 常數（原 open-WebUI 查詢保留 base64）、寬容的資料表名稱解析、對話記錄寫入。 |
| `prompts.py` | 將系統提示與當前 skill 串接。 |
| `config.py` | 以環境變數驅動的設定（載入 `.env`）。 |
| `doctor.py`、`setup_db.py`、`env_from_settings.py`、`inspect_metadata.py` | 維運工具（預檢、資料庫初始化、設定同步、資料檢視）。 |

## 5. 代理迴圈（`agent.py`）

```
messages = [system, user]
最多重複 MAX_AGENT_ITERS 次：
    修剪較舊的工具結果（KEEP_RECENT_TOOL_RESULTS）
    resp = inference.chat(messages, tools=TOOL_SCHEMAS)
    msg  = resp.choices[0].message；附加 msg
    若 msg 無 tool_calls：
        answer = msg.content
        若 COMPLETENESS_MODE == post_draft：answer = 完整性檢查(answer, 已檢索段落)
        回傳 answer
    執行所有 tool_calls（PARALLEL_TOOL_CALLS 時並行）
    對每個結果：
        收集帶 [SOURCE:…] 標籤的段落
        若 COMPLETENESS_MODE == pre_draft：立即列舉符合的列並附加到工具結果
        累積 sources（去重、保留最佳 rerank 分數）；附加 {role: tool, content}
# 達到迭代上限 → 再呼叫一次（不帶工具），回傳其內容
```

工具皆為 `async`（阻塞的 DB/HTTP 工作透過 `asyncio.to_thread` 執行）。檢索類工具
回傳 `{"text", "sources"}`——`text` 回饋給模型，`sources` 收集進回應；其餘回傳字串。

### 完整性檢查

長規格表會被起草模型摘要（「HGW15…HGW65」變成三列代表）。完整性檢查對抗這點：

- **`_enumerate_passage`** 單獨重讀一則檢索段落，列出所有符合請求的項目（或
  `NONE — <原因>`）。它在 `COMPLETENESS_MODEL`（常駐的小模型 `Support_Agent_Aux`）
  上執行，受全域 semaphore（`COMPLETENESS_CONCURRENCY`）限制。
- **`pre_draft`**（正式環境設定）：列舉在迴圈「內」、每次搜尋後立即進行，抽出的列
  附加到工具結果，主模型一次就從段落＋列起草。
- **`post_draft`**：先起草，再列舉全部段落，請主模型把草稿與抽取結果整合為最終答覆。

每段的結果（`found` / `none` / `error` ＋原始輸出）放在 `metrics.completeness`，並顯示
於展示頁的除錯面板。

## 6. 檢索管線（`rag_tools.py`）

`db_search_technical_manuals` 為核心路徑：

1. **向量化**——以 `search_query: <query>`（向量化模型預期的指令前綴）經 `/v1/embeddings` 編碼。
2. **解析資料表**——`db.resolve_tables` 將模型猜的 `product_table` 對應到真實的
   `data_*` 名稱（容許大小寫／前綴／標點差異）。
3. **向量搜尋**——各資料表並行（受 `DB_SEARCH_CONCURRENCY` 限制、各用一條池化連線），
   pgvector 餘弦查詢（`embedding <=> %s::vector`），以 `metadata_->>'language_code'`
   過濾，`LIMIT VECTOR_TOP_K`。`data_all_products` 為「bypass」資料表，整表回傳。
4. **英文後備**——目標語言查無結果時改以 `en` 重試。
5. **重排序**——合併後最佳的 `RERANK_CANDIDATE_K` 送往 `/v1/rerank`，保留前
   `RERANK_TOP_K`。每段**僅在評分時**截斷至 `RERANK_DOC_MAX_CHARS`（完整內容保留），
   以免超過 reranker 的物理批次大小。
6. **相關性評分**（預設關閉）——逐段 LLM YES/NO 過濾。
7. **標記**——每段加上 `[SOURCE: file=… · page=… · product=…]` 標頭（使引用可信）、
   圖片路徑 URL 編碼、冗長圖說截斷（`FIGURE_TEXT_MAX_CHARS`）。
8. **視覺**——保留段落中最多 `VISION_MAX_IMAGES` 張 `/static/HIWIN/...` 圖片自
   `IMAGE_STATIC_ROOT` 讀入、Base64 編碼送往視覺模型；描述附加於工具輸出。資料夾
   不存在時靜默略過。
9. **來源**——每個保留段落的 `metadata_` 白名單化為引用字典（頁碼／檔名／`web_path`…），
   附 `rerank_score`。

`db_search_certifications` 結構相同但針對 `data_certificates`（其中繼資料含可直接使用的
`web_path`）。`db_search_product_urls` 回傳兩張扁平 URL 資料表的所有列。

### SQL 備註

原 open-WebUI 工具的 SQL 以 base64 儲存；這些常數在 `db.py` 中逐位元保留（解碼形式見
註解）。為引用功能新增的含中繼資料變體（`SQL_VECTOR_SEARCH_META`、`SQL_VECTOR_BYPASS_META`）
以純文字撰寫。

## 7. 指標、追蹤與對話記錄

`inference.start_recording()` 在 `contextvars` 變數中安裝一個每請求的串列；每次 `chat`
/ `embed` / `rerank` 呼叫附加 `{kind, model, duration_ms, prompt_tokens, completion_tokens[, error]}`。
因為 contextvars 會傳播到 `asyncio.gather` 任務與 `to_thread` 工作，下三層的視覺與
完整性呼叫也被記錄。`agent.run` 將其整理為 `metrics`（`by_kind`、`llm_calls`、
`total_tokens`、`agent_iterations`、`tool_calls`、`completeness_calls`、`completeness`）。
`main.py` 加上 `latency_ms`，並將提示、答覆、來源、追蹤與指標寫入 `hiwin_cs_db.chat_logs`
（盡力而為；絕不影響回應）。

## 8. 資料模型（管線寫入什麼）

每個 `data_<product>` 資料表為 LlamaIndex 的 `PGVectorStore` 資料表：`id`、`text`、
`metadata_ json`、`node_id`、`embedding vector(2560)`。每列為一頁 PDF（`INGEST_BY_PAGE = true`），
文字以 `Page N` 開頭，圖片連結改寫為 `/static/HIWIN/<product>/<sub_folder>/<lang>/Figures|Tables/…`。
`metadata_` 含 `file_name`（文件 id `<Product>_<sub_folder>_<lang>`）、`page_number`、
`language_code`、`product_type`、`sub_folder`、`source_md`、`version`、`date_ingested`。
重新匯入某文件會刪除其舊列（依 `file_name`）並將 `version` 加一。

兩張 `data_product_*_urls` 為扁平表（無向量）。`data_certificates` 的列帶有證書圖片的 `web_path`。

## 9. 引用與圖片

- **引用。** `sources` 由 `metadata_` 建立而非模型文字，因此即使模型打錯頁碼仍可靠。
  系統提示禁止引用不在 `[SOURCE:…]` 標籤內的頁碼，`main.py` 再將 `sources` 修剪為實際
  引用的頁碼（若無法解析則保留全部）。
- **圖片。** 模型依系統提示的圖片規則輸出 `![alt](/static/HIWIN/...)`。`main.py` 將
  `IMAGE_STATIC_ROOT` 掛載於 `/static/HIWIN`，因此這些根相對 URL 解析到本後端——前提是
  前端**同源**（內附於 `/` 的展示頁即是）。獨立前端請用反向代理或改為絕對 URL。

## 10. 與 open-WebUI 的淵源

| open-WebUI 元件 | 由何者取代 |
|---|---|
| 模型系統提示 | `prompts/System_prompt.md` |
| `Filter.inlet`（語言注入） | `main.py` 的 `build_user_message` |
| `Tools` 類別（4 個函式） | `rag_tools.py`（移除 emitter/Valves） |
| Skills／路由規則 | `prompts/skills/*.md` |
| 原生函式呼叫 | `agent.py` 工具呼叫迴圈 |
| 靜態檔案服務（`/static/HIWIN`） | `main.py` 的 FastAPI `StaticFiles` 掛載 |

原始檔保存於 `reference/` 以供溯源。

## 11. 重要決策與陷阱

- **部署於資料庫／模型伺服器上。** Postgres 預設僅信任本機連線；後端置於同一主機可免改
  `pg_hba.conf`。
- **路由模式、模型常駐。** 四個服務模型皆常駐（`load-on-startup`），請求不必等待模型切換。
  管線模型按需載入，若未調高 `--models-max` 會擠掉一個服務模型。
- **確定性的向量。** 向量化與 reranker 區段以 `parallel = 1` 執行：多個 slot 時相同請求
  會回傳略有不同的向量，導致排名飄移。
- **推理預算。** 對話模型的 `reasoning-budget = 8192` 阻止曾吃掉整個 150k 脈絡、回傳空答覆的
  失控思考。
- **Reranker 批次大小。** 重排序／向量化模型將整段輸入放在單一物理批次；過長段落會超過預設
  `--ubatch-size 512` 而回傳 HTTP 500。以 `RERANK_DOC_MAX_CHARS` 與 `ubatch-size = 4096` 緩解。
- **語言代碼。** 資料庫使用 `en` / `jp` / `tc` / `sc`；須與 `LANGUAGE_SKILL_MAP` 及用戶端送出
  的值一致。以 `inspect_metadata.py` 確認。
- **`temperature: 0`。** 確定性解碼降低模型謄寫密集規格表數值時的偏移。
- **Windows 長路徑。** 管線輸出路徑超過 260 字元；所有檔案系統呼叫經 `core/fs_utils.to_long_path`。
