# API integration guide

[English] · [繁體中文](API.zh-Hant.md)

For anyone building a frontend or another service on top of the HIWIN Support
Agent. The backend is API-first: one JSON endpoint does all the work, and the
bundled page at `/` is only a demo of how to call it.

## 1. At a glance

| | |
|---|---|
| Base URL | `http://<backend-host>:8079` (this machine: `http://localhost:8079`; from elsewhere use its IP) |
| Main endpoint | `POST /chat` — JSON in, JSON out |
| Authentication | none (internal network). Put a reverse proxy in front if you need auth or TLS |
| Response time | typically **30 – 120 s** per question (retrieval + several model calls). Use a client timeout of at least **10 minutes** and show a "thinking" state |
| Streaming | not supported; the whole answer arrives at once |
| Concurrency | the model server has one slot for chat; concurrent questions are queued, not parallelised. Send one at a time per user and expect queueing under load |
| Content type | `application/json; charset=utf-8` both ways |
| CORS | enabled for every origin by default (`CORS_ALLOW_ORIGINS=*` in `.env`); restrict it to your frontend's origin in production |

## 2. `POST /chat`

### Request

```json
{
  "prompt":   "HGW20 的動態負荷是多少？",
  "language": "tc"
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `prompt` | string | yes | The user's question, any length. Product/series names help retrieval (e.g. "HGW20", "EG series"). |
| `language` | string | no | `en` English · `jp` Japanese · `tc` Traditional Chinese · `sc` Simplified Chinese. Controls which language's documents are searched **and** the language of the answer. Defaults to `tc` when omitted. |

The language is a hard filter on the knowledge base: a `tc` question searches
the Traditional-Chinese catalogues first and falls back to English only when
nothing is found. Pick it from the UI language or detect it from the text; do
not leave it blank for English users.

### Response (`200 OK`)

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

| Field | What to do with it |
|---|---|
| `response` | **The answer, as GitHub-flavoured Markdown.** Render it with any Markdown library (the demo uses `marked`). It contains bullet lists, bold, `[Page N]` citations and `![alt](url)` images. LaTeX (`$…$`) may appear in formulas; render with KaTeX or show as text. |
| `language` | The language actually used (the request value or the default). |
| `sources` | Citations for the pages the answer cites: `product_type` (catalogue family), `page_number`, `file_name` (document id `<Product>_<sub_folder>_<lang>`), `language_code`, `rerank_score` (0–1 relevance). Certificate results also carry `web_path` (URL of the certificate image). Show them as a "Sources" list. |
| `trace` | Which retrieval tools ran, with arguments and a preview of each result. For a debug view; hide from end users. |
| `metrics` | Timings and token counts. `latency_ms` is end-to-end; `by_kind` shows where the time went. Useful for a "took 41 s" label and for monitoring. |

**Out-of-scope questions** (pricing, delivery, orders, …) are not errors: the
answer is a short template pointing the user to the contact form
(`https://www.hiwinsupport.com/contact_us.aspx`), and `sources` is usually empty.
Questions with no matching documents get the "information is not currently
available" template. Detect these by the empty `sources` array if you want to
style them differently.

### Errors

| Status | Meaning | What to do |
|---|---|---|
| `422 Unprocessable Entity` | Request body invalid (missing `prompt`, wrong type). Body is FastAPI's standard `{"detail": [...]}`. | Fix the request. |
| `500 Internal Server Error` | The inference server or database was unreachable or failed mid-request. Body: `Internal Server Error`. | Retry after a delay; if persistent, the operator runs `python doctor.py`. |
| Connection refused / timeout | Backend not running, wrong host/port, or your timeout is shorter than the answer takes. | Check `GET /health`; raise the timeout to ≥ 600 s. |

The backend never returns partial answers; on any failure you get a 5xx and no
answer, so treat the request as atomic.

## 3. Other endpoints

| Endpoint | Purpose | Response |
|---|---|---|
| `GET /health` | Liveness probe. Says the process is up, **not** that the models or database are ready. | `{"status": "ok"}` |
| `GET /static/HIWIN/{path}` | The figures and tables referenced by answers (JPEG). | image, or `404` |
| `GET /` | The demo page (`frontend/index.html`). | HTML |
| `GET /docs` | Auto-generated OpenAPI/Swagger UI for the schemas above. | HTML |
| `GET /openapi.json` | The OpenAPI schema (for code generation). | JSON |

For a readiness check that the whole stack can answer, call
`http://<backend-host>:11400/v1/models` (the model server) and check every model
has `"status": {"value": "loaded"}`; or ask the operator to run `python doctor.py --live`.

## 4. Images and cross-origin setups

Answers reference figures as **root-relative** links: `![…](/static/HIWIN/Linear_Guideway/…/page46_figure_1.jpg)`.
Those resolve only when the page that displays them is served by this backend.
A frontend on another origin has three options:

1. **Ask the operator to set `PUBLIC_BASE_URL`** in `.env` (e.g.
   `PUBLIC_BASE_URL=http://10.0.0.5:8079`). Every `/static/HIWIN/…` link in
   `response` and every `web_path` in `sources` then arrives as an absolute URL.
   Simplest; no frontend work.
2. **Rewrite on the client** — before rendering, replace `](/static/HIWIN/` with
   `](http://<backend-host>:8079/static/HIWIN/`.
3. **Reverse proxy** — serve your frontend and proxy `/chat` and `/static/HIWIN`
   from the same host (nginx snippet below), so relative links keep working and
   CORS is not needed at all.

```nginx
location /chat         { proxy_pass http://127.0.0.1:8079; proxy_read_timeout 900s; }
location /static/HIWIN { proxy_pass http://127.0.0.1:8079; }
location /health       { proxy_pass http://127.0.0.1:8079; }
```

**CORS.** The backend sends `Access-Control-Allow-Origin` for the origins listed
in `.env` `CORS_ALLOW_ORIGINS` (default `*`). To lock it to your app:
`CORS_ALLOW_ORIGINS=http://10.0.0.7:3000,https://support.example.com`, then
restart the backend. Note the figure folder does not exist on the current
server yet (see README §3); until it is restored, image links return 404 and
answers simply contain no figures.

## 5. Examples

### curl

```bash
curl -X POST http://localhost:8079/chat \
  -H "Content-Type: application/json" \
  -d '{"prompt": "HGW20 load capacity", "language": "en"}' \
  --max-time 900
```

### JavaScript (browser or Node 18+)

```js
async function ask(prompt, language = "tc") {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15 * 60 * 1000);   // 15 min
  try {
    const res = await fetch("http://10.0.0.5:8079/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt, language }),
      signal: controller.signal,
    });
    if (!res.ok) throw new Error(`backend ${res.status}`);
    const data = await res.json();
    // data.response is Markdown; e.g. with marked:  container.innerHTML = marked.parse(data.response)
    // data.sources -> list of {product_type, page_number, file_name, rerank_score, web_path?}
    return data;
  } finally {
    clearTimeout(timer);
  }
}
```

If image links stay relative (`PUBLIC_BASE_URL` not set), prefix them before rendering:

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
print(data["response"])                       # Markdown answer
for s in data["sources"]:
    print(s["product_type"], "page", s["page_number"], s.get("rerank_score"))
print("took", data["metrics"]["latency_ms"] / 1000, "s")
```

### PowerShell

```powershell
$body = @{ prompt = "HGW20 load capacity"; language = "en" } | ConvertTo-Json
$r = Invoke-RestMethod -Uri http://localhost:8079/chat -Method Post -ContentType "application/json; charset=utf-8" -Body $body -TimeoutSec 900
$r.response
$r.sources | Format-Table product_type, page_number, rerank_score
```

### Batch / regression testing

`run_prompts.py` in the repo sends a list of questions and saves the full
responses with timings — handy for checking a frontend change did not alter
what the backend receives:

```bash
python run_prompts.py questions.jsonl --url http://10.0.0.5:8079/chat --timeout 900
```

(`questions.jsonl`: one `{"prompt": "...", "language": "en"}` per line.)

## 6. Recommendations for a production frontend

- Show a progress state immediately; answers take 30–120 s and there is no
  streaming. Consider disabling the send button until the reply arrives.
- Render `response` as Markdown with HTML sanitisation on (the content comes
  from a model reading catalogue text; treat it as untrusted HTML).
- Display `sources` under the answer; page numbers refer to the catalogue PDF pages.
- Log `metrics.latency_ms` and the HTTP status per request; the backend also
  records every exchange in `hiwin_cs_db.chat_logs` for the operator.
- Send `language` explicitly. If your UI is Traditional Chinese, `tc`; English UI, `en`.
- One in-flight request per user; queue additional ones client-side.
- Do not expose the backend port to the internet: no auth, no rate limiting.
  Put it behind your own gateway or a reverse proxy with TLS if needed.
