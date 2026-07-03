# 系統架構

[English](ARCHITECTURE.md) · [繁體中文]

本文件說明 **HIWIN Support Agent Backend** 的組成方式與設計理由。安裝與使用方式
請見 [README](../README.zh-Hant.md)。

## 1. 請求生命週期

```
用戶端 ──POST /chat {prompt, language}──▶ main.py
  1. 解析語言（請求值，否則用 DEFAULT_LANGUAGE）
  2. prompts.build_system(language)         → System_prompt.md + skills/<lang>.md
  3. build_user_message(prompt, language)   → "[Language Code: xx]\n<prompt>"
  4. agent.run(system, user_msg)            → 工具呼叫迴圈
  5. 回傳 ChatResponse {response, language, sources, trace}
```

迴圈旁有兩條側通道：`trace` 串列（每次工具呼叫及其結果預覽）與 `sources` 串列
（去重後的引用來源中繼資料）。

## 2. 狀態機

行為以自然語言定義於 `prompts/System_prompt.md`，由模型以狀態機方式執行：

| 狀態 | 用途 | 工具 |
|---|---|---|
| **0 — 路由** | 偵測語言、載入對應 skill、分類問題、選擇路徑。 | — |
| **1 — 資料庫查詢** | 檢索某產品系列在所問維度上的所有事實。 | `db_get_available_product_tables`、`db_search_technical_manuals` |
| **2 — URL 檢索** | 回傳下載／CAD 連結。 | `db_search_product_urls` |
| **3 — 格式化回應** | 以使用者語言建立完整數值表 + 重點 + 引用。 | — |
| **4 — 後備** | 對超出範圍或無結果者輸出 skill 的聯絡表單範本。 | — |

**skills**（`prompts/skills/*.md`）承載各語言的路由規則：將問題分類為 12 種類別，
僅回答第 1、2 類，其餘則回傳當地語言的「請填寫聯絡表單」範本。

關鍵在於：模型並非由 Python 逐步編排——它透過 OpenAI 風格的函式呼叫自行決定何時
呼叫哪個工具。Python 端僅負責執行工具並回饋結果。

## 3. 模組

| 模組 | 職責 |
|---|---|
| `main.py` | FastAPI 應用；`/chat`、`/health`、`/` 展示前端、`/static/HIWIN` 圖片掛載。 |
| `agent.py` | 工具呼叫迴圈：送出訊息 + schema、執行 `tool_calls`、回饋結果，最多重複 `MAX_AGENT_ITERS` 次。 |
| `tool_schemas.py` | 四個工具的 OpenAI 函式 schema，以及名稱→可呼叫物件的 `DISPATCH` 對應。 |
| `rag_tools.py` | 四個工具：pgvector 搜尋、列出資料表、證書、URL 列表——以及重排序與視覺輔助函式。 |
| `inference.py` | `/v1/chat/completions`、`/v1/embeddings`、`/v1/rerank` 的 HTTP 封裝。 |
| `db.py` | Postgres 連線、SQL 常數、寬容的資料表名稱解析。 |
| `prompts.py` | 將系統提示與當前 skill 串接。 |
| `config.py` | 以環境變數驅動的設定（載入 `.env`）。 |

## 4. 代理迴圈（`agent.py`）

```
messages = [system, user]
最多重複 MAX_AGENT_ITERS 次：
    resp = inference.chat(messages, tools=TOOL_SCHEMAS)
    msg  = resp.choices[0].message
    附加 msg
    若 msg 無 tool_calls：     → 回傳 msg.content     # 最終答覆
    對每個 tool_call：
        text, sources = 執行該工具
        累積 sources（去重）
        附加 {role: "tool", tool_call_id, content: text}
# 達到迴圈上限 → 再呼叫一次（不帶工具），回傳其內容
```

工具皆為 `async`（透過 `asyncio.to_thread` 將阻塞的 DB/HTTP 工作卸載）。檢索類工具
回傳 `{"text", "sources"}` 字典——`text` 回饋給模型，`sources` 則收集進回應；其餘
工具回傳純字串。

## 5. 檢索管線（`rag_tools.py`）

`db_search_technical_manuals` 為核心路徑：

1. **向量化** — 透過 `/v1/embeddings` 將查詢以 `search_query: <query>`（向量化模型
   預期的指令前綴）編碼。
2. **解析資料表** — 由 `db.resolve_tables` 將模型猜測的 `product_table` 對應到真實的
   `data_*` 資料表名稱（容許大小寫／前綴／標點差異）。
3. **向量搜尋** — 對每個資料表執行 pgvector 餘弦查詢
   （`embedding <=> %s::vector`），以 `metadata_->>'language_code'` 過濾，`LIMIT 15`。
   特殊的 `data_all_products`「bypass」會回傳列而不做距離排序。
4. **英文後備** — 若目標語言查無結果，改以 `en` 重試。
5. **重排序** — 將前段候選送往 `/v1/rerank`，保留前 5 筆。每段**僅在評分時**截斷至
   `RERANK_DOC_MAX_CHARS`（完整內容保留），以避免超過重排序模型的物理批次大小。
6. **視覺** — 保留片段中的 `/static/HIWIN/...` 圖片會被載入、Base64 編碼，送往視覺
   模型取得聚焦描述，並附加於工具輸出。
7. **來源** — 每個保留片段的 `metadata_` 會被白名單篩選為引用字典（頁碼／檔名／
   `web_path` 等）。

`db_search_certifications` 為相同結構，但針對 `data_certificates`（其中繼資料已含
可直接使用的 `web_path`）。`db_search_product_urls` 則直接回傳兩張 URL 資料表的
所有列。

### SQL 備註

原始 open-WebUI 工具將其 SQL 以 base64 編碼儲存；這些常數在 `db.py` 中逐位元保留
（解碼後形式見註解）。為引用功能新增的含中繼資料變體（`SQL_VECTOR_SEARCH_META`、
`SQL_VECTOR_BYPASS_META`）則以純文字撰寫。

## 6. 引用與圖片

- **引用。** `sources` 由 `metadata_` 建立，而非取自模型的文字敘述，因此即使模型把
  頁碼打錯仍然可靠。它會在代理迴圈中跨所有工具呼叫去重。
- **圖片。** 模型依系統提示的圖片規則輸出 Markdown `![alt](/static/HIWIN/...)`。
  `main.py` 將 `IMAGE_STATIC_ROOT` 掛載於 `/static/HIWIN`，因此這些根相對 URL 會
  解析到本後端——前提是前端由**同源**提供（內附於 `/` 的範例展示前端即符合）。若
  使用獨立前端，請改用反向代理或將圖片 URL 改為絕對路徑。

## 7. 與 open-WebUI 的淵源

| open-WebUI 元件 | 由何者取代 |
|---|---|
| 模型系統提示 | `prompts/System_prompt.md` |
| `Filter.inlet`（語言注入） | `main.py` 中的 `build_user_message` |
| `Tools` 類別（4 個函式） | `rag_tools.py`（移除 emitter/Valves） |
| Skills／路由規則 | `prompts/skills/*.md` |
| 原生函式呼叫 | `agent.py` 工具呼叫迴圈 |
| 靜態檔案服務（`/static/HIWIN`） | `main.py` 的 FastAPI `StaticFiles` 掛載 |

原始檔保存於 `reference/` 以供溯源。

## 8. 重要決策與陷阱

- **部署於資料庫／模型伺服器上。** Postgres 僅信任本機連線且關閉 SSL；將後端置於
  同一主機可免去修改 `pg_hba.conf`。
- **重排序批次大小。** 重排序／向量化模型會在單一物理批次內對整段輸入做 pooling，
  因此過長片段會超出預設的 `--ubatch-size 512` 而回傳 HTTP 500。此處以截斷緩解，並
  可於伺服器端調高 `--ubatch-size`。
- **語言代碼。** 資料庫使用 `en` / `jp` / `tc`；這些值必須與 `LANGUAGE_SKILL_MAP`
  及用戶端送出的值一致。可用 `inspect_metadata.py` 確認。
- **`temperature: 0`。** 確定性解碼能降低模型在謄寫密集規格表數值時的偏移。
