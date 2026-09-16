# API 串接指南

[English](API.md) · [繁體中文]

給要在 HIWIN Support Agent 之上開發前端或其他系統的人。後端以 API 為核心：一個
JSON 端點完成所有工作；內附於 `/` 的頁面只是呼叫範例。

## 1. 一覽

| | |
|---|---|
| 基礎 URL | `http://<後端主機>:8079`（本機 `http://localhost:8079`；從其他機器用它的 IP） |
| 主要端點 | `POST /chat`——JSON 進、JSON 出 |
| 驗證 | 無（內部網路）。需要驗證或 TLS 時在前面加反向代理 |
| 回應時間 | 每題通常 **30 – 120 秒**（檢索＋多次模型呼叫）。用戶端逾時請設 **10 分鐘以上**，並顯示「思考中」狀態 |
| 串流 | 不支援；整個答覆一次回傳 |
| 並行 | 模型伺服器的對話 slot 只有一個；同時多題會排隊而非並行。每位使用者一次送一題，高負載時預期排隊 |
| 內容類型 | 雙向皆 `application/json; charset=utf-8` |
| CORS | 預設允許所有來源（`.env` 的 `CORS_ALLOW_ORIGINS=*`）；正式環境請限縮為你的前端來源 |

## 2. `POST /chat`

### 請求

```json
{
  "prompt":   "HGW20 的動態負荷是多少？",
  "language": "tc"
}
```

| 欄位 | 型別 | 必填 | 說明 |
|---|---|---|---|
| `prompt` | string | 是 | 使用者的問題，長度不限。含產品／系列名稱有助檢索（如「HGW20」、「EG 系列」）。 |
| `language` | string | 否 | `en` 英文 · `jp` 日文 · `tc` 繁體中文 · `sc` 簡體中文。決定搜尋哪個語言的文件**以及**答覆語言。省略時預設 `tc`。 |

語言是知識庫的硬性過濾條件：`tc` 的問題先搜繁中型錄，查無結果才退回英文。請依 UI
語言或文字偵測來設定；英文使用者不要留空。

### 回應（`200 OK`）

```json
{
  "response": "您好！很高興為您說明 HGW20 ... \n\n* **基本動額定負荷 C：** 17.75 kN [Page 46]\n...\n![HGW20 dimensions](/static/HIWIN/Linear_Guideway/linear_guideway-(c)/tc/Figures/page46_figure_1.jpg)",
  "language": "tc",
  "sources": [
    {
      "product_type": "linear_guideway",
      "page_number": 46,
      "file_name": "Linear_Guideway_linear_guideway-(c)_tc",
      "sub_folder": "linear_guideway-(c)",
      "language_code": "tc",
      "source_md": "page46_filled_page.md",
      "rerank_score": 0.9661
    }
  ],
  "trace": [
    { "tool": "db_get_available_product_tables", "args": {}, "result": "The following product tables are available..." },
    { "tool": "db_search_technical_manuals", "args": { "query": "HGW20 動額定負荷", "language_code": "tc", "product_table": ["data_linear_guideway"] }, "result": "[SOURCE: file=... · page=46 · product=linear_guideway]\n..." }
  ],
  "metrics": {
    "latency_ms": 41230.5,
    "llm_calls": 5, "agent_iterations": 3, "tool_calls": 2, "completeness_calls": 6,
    "prompt_tokens": 35120, "completion_tokens": 2992, "total_tokens": 38112,
    "by_kind": { "agent": { "calls": 3, "ms": 21000.1, "errors": 0 }, "completeness_enum": { "calls": 6, "ms": 49200.0, "errors": 0 }, "rerank": { "calls": 2, "ms": 15900.0, "errors": 0 }, "embed": { "calls": 2, "ms": 2400.0, "errors": 0 } },
    "completeness": [ { "source": "[SOURCE: file=... · page=46 · product=linear_guideway]", "status": "found", "output": "..." } ],
    "generations": [ { "kind": "agent", "model": "Support_Agent_Qwen3.6", "duration_ms": 8100.2, "prompt_tokens": 12000, "completion_tokens": 400 } ]
  }
}
```

| 欄位 | 怎麼用 |
|---|---|
| `response` | **答覆本文，GitHub 風格 Markdown。** 用任何 Markdown 函式庫渲染（展示頁用 `marked`）。含條列、粗體、`[Page N]` 引用與 `![alt](url)` 圖片。公式可能出現 LaTeX（`$…$`）；用 KaTeX 渲染或以文字顯示。 |
| `language` | 實際使用的語言（請求值或預設值）。 |
| `sources` | 答覆引用頁面的來源：`product_type`（型錄系列）、`page_number`、`file_name`（文件 id `<產品>_<子資料夾>_<語言>`）、`language_code`、`rerank_score`（0–1 相關度）。證書類結果另有 `web_path`（證書圖片 URL）。建議顯示為「來源」清單。 |
| `trace` | 執行了哪些檢索工具、參數與結果預覽。供除錯畫面；不要給一般使用者看。 |
| `metrics` | 耗時與 token 數。`latency_ms` 為端到端；`by_kind` 顯示時間花在哪。可用於「耗時 41 秒」標示與監控。 |

**超出範圍的問題**（報價、交期、訂單…）不是錯誤：答覆是一段簡短範本，引導使用者到
聯絡表單（`https://www.hiwinsupport.com/contact_us.aspx`），`sources` 通常為空。查無
文件的問題會得到「目前網站上沒有相關資訊」範本。想套不同樣式時可用 `sources` 是否
為空來判斷。

### 錯誤

| 狀態碼 | 意義 | 處理 |
|---|---|---|
| `422 Unprocessable Entity` | 請求本文無效（缺 `prompt`、型別錯）。本文為 FastAPI 標準的 `{"detail": [...]}`。 | 修正請求。 |
| `500 Internal Server Error` | 推論伺服器或資料庫連不上、或處理途中失敗。本文：`Internal Server Error`。 | 稍後重試；持續發生時請維運人員執行 `python doctor.py`。 |
| 連線被拒／逾時 | 後端未執行、主機或埠錯誤、或你的逾時比答覆時間短。 | 先看 `GET /health`；逾時調到 ≥ 600 秒。 |

後端不會回傳部分答覆；任何失敗都是 5xx 且沒有答覆，請把請求視為不可分割。

## 3. 其他端點

| 端點 | 用途 | 回應 |
|---|---|---|
| `GET /health` | 存活探測。只表示程序在跑，**不**代表模型與資料庫就緒。 | `{"status": "ok"}` |
| `GET /static/HIWIN/{path}` | 答覆引用的圖示與表格（JPEG）。 | 圖片，或 `404` |
| `GET /` | 展示頁（`frontend/index.html`）。 | HTML |
| `GET /docs` | 自動產生的 OpenAPI／Swagger UI。 | HTML |
| `GET /openapi.json` | OpenAPI schema（可用於產生用戶端程式碼）。 | JSON |

要確認整個堆疊能回答問題，可呼叫模型伺服器的 `http://<後端主機>:11400/v1/models`
並檢查每個模型的 `"status": {"value": "loaded"}`；或請維運人員執行 `python doctor.py --live`。

## 4. 圖片與跨來源部署

答覆以**根相對**連結引用圖示：`![…](/static/HIWIN/Linear_Guideway/…/page46_figure_1.jpg)`。
只有當顯示頁面由本後端提供時才能解析。不同來源的前端有三種做法：

1. **請維運人員在 `.env` 設定 `PUBLIC_BASE_URL`**（例如 `PUBLIC_BASE_URL=http://10.0.0.5:8079`）。
   之後 `response` 中每個 `/static/HIWIN/…` 連結與 `sources` 的 `web_path` 都會是絕對 URL。
   最簡單，前端不用改。
2. **在用戶端改寫**——渲染前把 `](/static/HIWIN/` 換成 `](http://<後端主機>:8079/static/HIWIN/`。
3. **反向代理**——讓前端與 `/chat`、`/static/HIWIN` 由同一主機提供（nginx 片段如下），
   相對連結照常運作，也不需要 CORS。

```nginx
location /chat         { proxy_pass http://127.0.0.1:8079; proxy_read_timeout 900s; }
location /static/HIWIN { proxy_pass http://127.0.0.1:8079; }
location /health       { proxy_pass http://127.0.0.1:8079; }
```

**CORS。** 後端會對 `.env` `CORS_ALLOW_ORIGINS` 列出的來源送出 `Access-Control-Allow-Origin`
（預設 `*`）。要鎖定為你的應用：`CORS_ALLOW_ORIGINS=http://10.0.0.7:3000,https://support.example.com`，
然後重啟後端。注意目前伺服器上的圖片資料夾尚未建立（見 README §3）；在還原之前，
圖片連結回 404，答覆單純沒有圖。

## 5. 範例

### curl

```bash
curl -X POST http://localhost:8079/chat \
  -H "Content-Type: application/json" \
  -d '{"prompt": "HGW20 load capacity", "language": "en"}' \
  --max-time 900
```

### JavaScript（瀏覽器或 Node 18+）

```js
async function ask(prompt, language = "tc") {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15 * 60 * 1000);   // 15 分鐘
  try {
    const res = await fetch("http://10.0.0.5:8079/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt, language }),
      signal: controller.signal,
    });
    if (!res.ok) throw new Error(`backend ${res.status}`);
    const data = await res.json();
    // data.response 是 Markdown；例如用 marked：container.innerHTML = marked.parse(data.response)
    // data.sources -> {product_type, page_number, file_name, rerank_score, web_path?} 的陣列
    return data;
  } finally {
    clearTimeout(timer);
  }
}
```

若圖片連結仍是相對路徑（未設 `PUBLIC_BASE_URL`），渲染前先加上前綴：

```js
const html = marked.parse(data.response.replaceAll("](/static/HIWIN/", "](http://10.0.0.5:8079/static/HIWIN/"));
```

### Python

```python
import requests

r = requests.post("http://localhost:8079/chat",
                  json={"prompt": "滾珠螺桿的預壓等級有哪些？", "language": "tc"},
                  timeout=900)
r.raise_for_status()
data = r.json()
print(data["response"])                       # Markdown 答覆
for s in data["sources"]:
    print(s["product_type"], "page", s["page_number"], s.get("rerank_score"))
print("耗時", data["metrics"]["latency_ms"] / 1000, "秒")
```

### PowerShell

```powershell
$body = @{ prompt = "HGW20 load capacity"; language = "en" } | ConvertTo-Json
$r = Invoke-RestMethod -Uri http://localhost:8079/chat -Method Post -ContentType "application/json; charset=utf-8" -Body $body -TimeoutSec 900
$r.response
$r.sources | Format-Table product_type, page_number, rerank_score
```

### 批次／回歸測試

儲存庫內的 `run_prompts.py` 會送出一批問題並保存完整回應與耗時——適合確認前端改動
沒有改變後端收到的內容：

```bash
python run_prompts.py questions.jsonl --url http://10.0.0.5:8079/chat --timeout 900
```

（`questions.jsonl`：每行一個 `{"prompt": "...", "language": "en"}`。）

## 6. 給正式前端的建議

- 送出後立即顯示進度狀態；答覆需 30–120 秒且沒有串流。可考慮在回覆前停用送出按鈕。
- 以 Markdown 渲染 `response` 並開啟 HTML 消毒（內容來自模型讀型錄文字；視為不可信 HTML）。
- 在答覆下方顯示 `sources`；頁碼對應型錄 PDF 的頁面。
- 記錄每次請求的 `metrics.latency_ms` 與 HTTP 狀態；後端也會把每次對話存進
  `hiwin_cs_db.chat_logs` 供維運人員查看。
- 明確送出 `language`。UI 是繁中就送 `tc`，英文 UI 送 `en`。
- 每位使用者同時只有一個進行中的請求；其餘在用戶端排隊。
- 不要把後端埠開放到網際網路：沒有驗證、沒有流量限制。需要時放在你自己的閘道或
  含 TLS 的反向代理之後。
