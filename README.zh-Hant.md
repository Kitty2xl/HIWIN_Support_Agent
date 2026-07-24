# HIWIN Support Agent Backend（HIWIN 支援代理後端）

[English](README.md) · **繁體中文**

一套精簡、可獨立運行的 Python/FastAPI 後端服務，用於 HIWIN 工業產品的
**支援代理（support agent，即知識檢索助手）**。使用者透過 HTTP 傳送提示詞
（prompt）與語言代碼；服務會對本地的 Postgres + pgvector 資料庫與本地的
llama.cpp 推論伺服器執行代理式（agentic）檢索流程，最後回傳含結構化引用來源與
內嵌技術圖示的 Markdown 答覆。

本服務**以 API 為核心**——任何用戶端（網頁應用、其他服務、`curl`）皆可呼叫。
專案內附的 HTML 頁面僅作為**範例／展示（demo）**前端。本專案以一個小巧、透明的
服務，取代先前以 [open-WebUI](https://github.com/open-webui/open-webui) 為基礎的
部署。

本專案同時內附**資料匯入管線**（`pipeline/`），可將來源 PDF 轉換為後端讀取的
Postgres + pgvector 資料庫——因此單一專案即涵蓋知識庫的**建立**與**服務**。
若您是從自己的空資料庫開始，請參考[快速開始](#快速開始從零開始)。

---

## 目錄

- [功能特色](#功能特色)
- [運作原理](#運作原理)
- [最快上手（服務既有資料庫）](#最快上手服務既有資料庫)
- [快速開始（從零開始）](#快速開始從零開始)
- [環境需求](#環境需求)
- [安裝步驟](#安裝步驟)
- [設定](#設定)
- [模型參數](#模型參數)
- [啟動服務](#啟動服務)
- [使用方式](#使用方式)
- [對話記錄](#對話記錄)
- [範例展示前端](#範例展示前端)
- [資料匯入管線](#資料匯入管線)
- [專案結構](#專案結構)
- [疑難排解](#疑難排解)
- [已知問題](#已知問題)
- [授權](#授權)

---

## 功能特色

- **單一 `POST /chat` API** — 輸入 `{prompt, language}`，輸出 JSON（含 Markdown
  格式的 `response`、結構化的 `sources`、以及工具呼叫紀錄 `trace`）。
- **代理式狀態機** — 由 LLM 工具呼叫迴圈驅動「路由 → 檢索 → 格式化 → 後備」流程，
  流程定義於 `prompts/System_prompt.md`。
- **多語言支援** — 英文（`en`）、日文（`jp`）、繁體中文（`tc`）、簡體中文（`sc`），且答覆會與使用者
  輸入的字集一致（字集鏡像）。
- **完整檢索管線** — 向量化 → pgvector 相似度搜尋 → 重新排序（rerank）→ 對檢索到
  的圖示進行視覺分析。
- **結構化引用來源** — 由每筆檢索片段的 `metadata_` 建立去重後的 `sources` 陣列
  （頁碼／檔名／`web_path`）。
- **圖片服務** — 後端於 `/static/HIWIN` 提供 HIWIN 圖示，使 Markdown 中的圖片連結
  能在同源（same-origin）下正確解析。
- **範例展示前端** — `/` 提供一個可選的單頁 HTML 展示頁（`frontend/index.html`），
  可輸入提示詞並預覽渲染後的答覆。此頁僅供示範；本服務以 API 為核心，您可換成自己
  的前端。

## 運作原理

```
POST /chat {prompt, language}
  └─ 將「[Language Code: xx]」注入提示詞
  └─ 組成系統提示 = System_prompt.md + 對應語言的 skill
  └─ 代理迴圈（LLM 搭配工具 schema）：
        ├─ db_get_available_product_tables   （探索資料表）
        ├─ db_search_technical_manuals       （向量化 → pgvector → rerank → 視覺）
        ├─ db_search_certifications          （證書 + web_path）
        └─ db_search_product_urls            （下載／CAD 連結）
  └─ 回傳 { response（Markdown）, sources, trace }
```

LLM 會依系統提示自行決定要呼叫哪些工具、以及何時呼叫——與 open-WebUI 的原生
函式呼叫（function calling）行為一致。完整設計請見
[docs/ARCHITECTURE.zh-Hant.md](docs/ARCHITECTURE.zh-Hant.md)。

當請求要求列舉規格表（「列出所有…」）時，草稿之後會執行一次**完整性檢查
（completeness pass）**：逐一重新閱讀每則檢索到的段落，抽取所有符合的型號列，再
重建答覆以涵蓋全部。此步驟準確但會多花模型呼叫（調整或關閉請見[設定](#設定)的
`COMPLETENESS_*`）。

## 最快上手（服務既有資料庫）

已備妥 `hiwin_rag` 資料庫與磁碟上的 GGUF 模型？這是取得可用 `/chat` API 的最短路徑
——不需管線、不需重建：

1. **複製並進入專案：**
   ```bash
   git clone https://github.com/Kitty2xl/HIWIN_Support_Agent
   cd HIWIN_Support_Agent
   ```
2. **讓啟動器指向你的推論伺服器。** 開啟 `start.bat`（Windows）或 `start.sh`
   （Linux/macOS），於檔案頂端設定兩個值：`LLAMA_SERVER`（你的 `llama-server`
   執行檔）與 `PRESET`（一份路由 preset——複製
   [`router.example.ini`](router.example.ini) 並把其中的 `model` / `mmproj` 路徑
   改為你的 GGUF）。
3. **填好 `.env`**——至少 `IMAGE_STATIC_ROOT` 與 `DB_PASSWORD`（見[設定](#設定)）。
4. **執行**（會建立 venv、以所有模型常駐啟動路由、等待就緒，再啟動後端）：
   ```bash
   start.bat        # Windows
   ./start.sh       # Linux/macOS
   ```
   服務即在 `http://localhost:8079` 上運作。冒煙測試：
   ```bash
   curl -X POST http://localhost:8079/chat -H "Content-Type: application/json" \
     -d '{"prompt": "HGW20 load capacity", "language": "en"}'
   ```

> Postgres 必須已在執行——啟動器僅在無法連線時**警告**。**要從 PDF 建立資料庫？**
> 請改用下方的[快速開始（從零開始）](#快速開始從零開始)。
> （`run.bat` / `run.sh` 為僅啟動後端的啟動器，適用於路由已在執行時。）

## 快速開始（從零開始）

從您自己的空資料庫開始，端到端流程為：
**安裝 → 準備 Postgres → 啟動推論伺服器 → 以管線建立資料庫 → 以後端提供服務。**

1. **安裝** — 取得專案、建立虛擬環境，並執行 `pip install -r requirements.txt`
   （此單一檔案同時涵蓋後端與管線）。見[安裝步驟](#安裝步驟)。

2. **準備您的 Postgres 資料庫。** 可使用任何資料庫（新建或現有）；於其中啟用
   pgvector 並建立 schema：
   ```sql
   \c YOUR_DATABASE_NAME_HERE
   CREATE EXTENSION IF NOT EXISTS vector;
   CREATE SCHEMA IF NOT EXISTS hiwin_rag;
   ```
   名稱／使用者／密碼／schema 皆可自訂——只要使用者具備 `CREATE` 權限，管線也會於
   首次執行時自動建立缺少的 schema（`hiwin_rag`、`hiwin_cs_db`）與 `data_*` 資料表。
   （`\c` 是 `psql` 專用指令；在 GUI 用戶端如 pgAdmin／DBeaver，只需連到該資料庫並
   執行其餘兩行即可。）

3. **啟動您的推論伺服器**（llama.cpp `llama-server` 路由模式），載入[環境需求](#環境需求)所列的模型。
   GUI／TUI 可代您下載 GGUF——見[下載模型檔案](#下載模型檔案選用)。

4. **建立資料庫** — 將管線指向您的 PDF 與資料庫後執行。見[資料匯入管線](#資料匯入管線)。

5. **提供服務** — 在 `.env` 填入**相同**的資料庫認證後啟動後端。見[設定](#設定)與[啟動服務](#啟動服務)。

> ⚠️ 步驟 4 與 5 使用**各自獨立**的設定檔——`pipeline/settings.json` 與 `.env`。
> 兩者的**資料庫名稱、使用者、密碼、schema 與向量化模型必須一致**，因為管線*寫入*
> 的正是後端*讀取*的內容。

## 環境需求

> **我的機器跑得動嗎？** 三個推論模型（對話＋視覺、向量化、重排序——加上管線的
> 視覺／文字 pass）皆為數十億參數的 GGUF，且多個以大脈絡（`--ctx-size 100000`）
> 啟動。實務上您需要一張 **CUDA 或 Metal GPU，且 VRAM 足以同時容納您並行載入的
> 模型**（路由模式會讓它們一起常駐），以及 **數十 GB 的可用磁碟空間**存放
> GGUF。純 CPU *能*執行，但答覆會以分鐘計、而非秒計——除了初步測試之外，強烈
> 建議使用 GPU。

- **Python 3.12.x**（[`.python-version`](.python-version) 檔為 `pyenv` 使用者鎖定
  **3.12.7**——執行 `pyenv install 3.12.7` 後會自動選用；未使用 `pyenv` 時，PATH 上
  任一 3.12.x 皆可）。可於 **Windows、Linux
  與 macOS** 執行。管線的 GUI 另需 Tk（Windows／macOS 的 Python 已內建；Linux 請
  執行 `sudo apt install python3-tk`）。管線的 TUI 與後端皆不需顯示器。
- **PostgreSQL** 並安裝 **pgvector** 擴充。可使用您自己的資料庫——管線會在其中
  建立 schema（預設 `hiwin_rag`）、`data_*` 資料表與 `metadata_` jsonb 欄位。
  **pgvector 必須先安裝進 Postgres**——它是伺服器端擴充，並非 pip 套件：請安裝
  pgvector 的作業系統套件（例如 `postgresql-16-pgvector`）、使用內建 pgvector 的
  Docker 映像（`pgvector/pgvector:pg16`），或自
  https://github.com/pgvector/pgvector 建置。執行 `CREATE EXTENSION vector` 通常
  需要 **超級使用者（superuser）**（或具備該資料庫 `CREATE` 權限的角色）。
- 一個本地的 **OpenAI 相容推論伺服器**——即 **llama.cpp `llama-server` 的路由模式
  （router mode）**。以 `--models-preset` 啟動（且**不帶** `-m`），單一
  `llama-server` 程序即可在單一端點託管**所有**模型，並依請求的 `model` 欄位路由。
  標記 `load-on-startup = true` 的模型會一起**常駐**（chat／embedding／reranker／
  小模型之間不再換入換出）。請將用戶端指向該端點：後端的 `INFERENCE_HOST` 與管線的
  `LLM_BASE_URL`。

> **本專案內附一份可直接編輯的路由 preset——[`router.example.ini`](router.example.ini)。**
> 每個 `[區段]` 即一個可路由的模型名稱（須與 `.env` / `pipeline/settings.json` 中的
> 名稱一致，見[模型名稱如何串接](#模型名稱如何串接)）；鍵即 `llama-server` 的長參數
> 去掉開頭的 `--`（`--n-gpu-layers 99` → `n-gpu-layers = 99`）。請將其中的 `model` /
> `mmproj` 路徑改為你的 GGUF，然後：
>
> ```sh
> llama-server --models-preset router.example.ini --host 127.0.0.1 --port 11400
> ```
>
> `start.bat` / `start.sh` 啟動器會為你執行以上命令。完整 preset schema 與
> `--models-max`（同時常駐幾個模型，預設 4）請見 llama.cpp server 文件的
> **「Using multiple models」**。

  兩半會以不同的模型角色使用此伺服器：

**提供服務（後端）** — 需提供 `/v1/chat/completions`、`/v1/embeddings` 與
`/v1/rerank`，並載入：
  - 對話／視覺模型（預設 `Support_Agent_Qwen3.6`）、
  - 向量化模型（預設 `Embedding_Qwen3.6`）、
  - 重排序模型（預設 `Reranker_Qwen3.6`）；
  另需磁碟上的 HIWIN **靜態圖片資料夾**（於 `/static/HIWIN` 提供）。

**建立資料庫（管線）** — 另需於您的 `ROOT_PATH` 下：
  - 一個放置來源 PDF 的 **`PDFs/`** 資料夾，以及描述它們的 **`PDF_Config.yaml`**、
  - 版面偵測 ONNX 模型 **`PP-DocLayout-PlusL.onnx`**（PaddleOCR PP-DocLayout_plus-L，
    RT-DETR-L，800×800，20 個版面類別——https://huggingface.co/PaddlePaddle/PP-DocLayout_plus-L）；
  並於推論伺服器載入視覺與文字模型（預設 `RAG_Pipeline_Pass34` 與
  `RAG_Pipeline_Pass5Ingest`），以及與後端**相同的向量化模型**（預設
  `Embedding_Qwen3.6`），使儲存的向量在查詢時可相互比對。

> **提示：** 最簡單的部署方式是將所有元件（後端、Postgres、推論伺服器）放在
> **同一台機器**，如此所有主機位址皆為 `localhost`。

## 安裝步驟

```bash
# 1. 取得並進入專案
git clone https://github.com/Kitty2xl/HIWIN_Support_Agent
cd HIWIN_Support_Agent

# 2. 建立並啟用虛擬環境
python -m venv .venv     # Linux/macOS 若找不到 `python`，請改用 python3
# Windows (cmd):        .venv\Scripts\activate.bat
# Windows (PowerShell): .venv\Scripts\Activate.ps1
# Windows (Git Bash):   source .venv/Scripts/activate
# Linux / macOS:        source .venv/bin/activate

# 3. 安裝相依套件
pip install -r requirements.txt

# 4. 編輯已納入版控的 .env——設定 IMAGE_STATIC_ROOT（見「設定」一節）。
#    .env 已為內部部署預先填妥，無需複製。
```

亦可使用便捷啟動腳本，自動完成步驟 2–3 並啟動服務：
`run.bat`（Windows）／ `./run.sh`（Linux/macOS）。

## 設定

所有設定皆由環境變數讀取；已納入版控的 `.env` 檔會自動載入。它已為內部部署預先
填妥——請編輯 `IMAGE_STATIC_ROOT`（以及任何與您環境不同的項目）。

> **`.env` 在哪裡？** 它位於**專案根目錄**——與 `main.py`、`config.py`、`run.bat`
> 同一個資料夾。其檔名以點開頭，因此是**隱藏檔**：Windows 檔案總管與部分編輯器／
> 檔案選擇器預設不會顯示。可用 `dir /a`（Windows）或 `ls -a`（Linux/macOS）顯示，
> 或以 VS Code 等編輯器開啟該資料夾。

若 `.env` 不存在（例如您的 clone 未包含它），請於專案根目錄建立一個名稱正好為
`.env` 的檔案，至少包含下列鍵。`DB_PASSWORD` 空白或缺少，正是查詢時出現
`fe_sendauth: no password supplied` 的原因——請將它設為與 `pipeline/settings.json`
的 `DB_PASS` **相同**的值：

```dotenv
INFERENCE_HOST=http://localhost:11400
LANGUAGE_MODEL=Support_Agent_Qwen3.6
EMBEDDING_MODEL=Embedding_Qwen3.6
RERANKER_MODEL=Reranker_Qwen3.6
DB_NAME=hiwin_rag_db
DB_USER=postgres
DB_PASSWORD=your_db_password          # 必填；須與 pipeline/settings.json 的 DB_PASS 一致
DB_HOST=localhost
DB_PORT=5432
DB_SCHEMA=hiwin_rag
IMAGE_STATIC_ROOT=/path/to/open_webui/static/HIWIN
CHAT_LOG_ENABLED=true
```

**捷徑——由管線設定產生 `.env`：** 與其重複填寫資料庫／伺服器／圖片等值，可執行
`python env_from_settings.py`。它會讀取您匯入時已設定好的 `pipeline/settings.json`，
並產生對應的 `.env`，同時處理鍵名差異（`DB_PASS` → `DB_PASSWORD`、
`IMAGE_TARGET_ROOT` → `IMAGE_STATIC_ROOT`、基礎 URL 去除 `/v1`）。這些共用值變更時
重新執行即可。（既有的 `.env` 會備份為 `.env.bak`。）

主要設定如下：

| 變數 | 預設值 | 說明 |
|---|---|---|
| `INFERENCE_HOST` | `http://localhost:11400` | 推論伺服器的基礎 URL。遠端執行時請填伺服器 IP。 |
| `LANGUAGE_MODEL` | `Support_Agent_Qwen3.6` | 對話＋視覺模型名稱。 |
| `EMBEDDING_MODEL` | `Embedding_Qwen3.6` | 向量化模型——**必須**與建立資料庫時所用的模型一致。 |
| `RERANKER_MODEL` | `Reranker_Qwen3.6` | 重排序模型名稱。 |
| `DB_HOST` / `DB_PORT` | `localhost` / `5432` | Postgres 主機／連接埠。 |
| `DB_NAME` / `DB_USER` / `DB_PASSWORD` | `hiwin_rag_db` / `postgres` /（無） | Postgres 認證資訊。**`DB_PASSWORD` 為必填。** |
| `DB_SCHEMA` | `hiwin_rag` | 存放 `data_*` 資料表的 schema。 |
| `DB_SSLMODE` | `prefer` | libpq SSL 模式（`prefer` / `disable` / `require`）。 |
| `IMAGE_STATIC_ROOT` | （無） | HIWIN 圖片資料夾的檔案系統路徑。**圖片顯示為必填。** |
| `DEFAULT_LANGUAGE` | `tc` | 請求未指定語言時所使用的語言。 |
| `TEMPERATURE` | `0` | 解碼溫度（0 表示完全確定性）。 |
| `RERANK_DOC_MAX_CHARS` | `2000` | 送往重排序模型的每段字元上限。 |
| `MAX_AGENT_ITERS` | `8` | 每次請求的工具呼叫迴圈上限。 |
| `CHAT_LOG_ENABLED` | `true` | 將每次 `/chat` 對話記錄至 `hiwin_cs_db` schema（見[對話記錄](#對話記錄)）。 |
| `CHAT_TIMEOUT` | `120` | 對推論伺服器每次呼叫的逾時（秒）。若冷啟動載入模型會超過此值（500／read timeout），請調高。 |

檢索的品質與成本亦可調整——皆已附合理預設值，僅在需要於召回率與速度之間取捨時才調整：

| 變數 | 預設值 | 說明 |
|---|---|---|
| `VECTOR_TOP_K` | `15` | 每個資料表取回的 pgvector 列數。 |
| `RERANK_CANDIDATE_K` | `20` | 送入 reranker 的段落數。 |
| `RERANK_TOP_K` | `6` | reranker 後保留的段落數（每次手冊檢索）。 |
| `CERT_RERANK_TOP_K` | `10` | 證書檢索保留的段落數。 |
| `GRADING_ENABLED` | `false` | 對每則段落做 LLM YES/NO 相關性過濾。預設關閉——它是*精確度*過濾，與窮盡召回相衝突。 |
| `COMPLETENESS_PASS_ENABLED` | `true` | 逐段重讀以救回草稿被摘略掉的列。 |
| `COMPLETENESS_MODEL` | `LANGUAGE_MODEL` | 逐段列舉所用的模型——指向一個常駐的小模型（如 `Support_Agent_Aux`）可加速。 |
| `COMPLETENESS_BASE_URL` | `INFERENCE_BASE_URL` | 該模型的端點（僅在另行提供服務時才需設定）。 |
| `COMPLETENESS_CONCURRENCY` | `1` | 同時進行的列舉呼叫數。伺服器有餘裕的並行槽時可調高。 |
| `COMPLETENESS_TIMEOUT` | `120` | 每次列舉的逾時（秒）。 |
| `COMPLETENESS_EXPLAIN_NONE` | `true` | 讓不符合的段落說明*原因*（除錯輔助）。 |
| `FIGURE_TEXT_MAX_CHARS` | `120` | 在送入模型前截斷冗長的圖說（節省脈絡）。 |
| `KEEP_RECENT_TOOL_RESULTS` | `4` | 執行中的脈絡只完整保留最近 N 筆工具結果；較舊的以摘要取代。`-1` 停用。 |
| `TRACE_RESULT_MAX_CHARS` | `600` | 除錯 trace 保留每筆工具結果的字元數。`0` = 不截斷。 |

> **語言代碼**（`en` / `jp` / `tc` / `sc`）必須與資料庫中
> `metadata_->>'language_code'` 儲存的值一致。可用
> `python inspect_metadata.py` 確認。

> **也要建立資料庫嗎？** 請在 `pipeline/settings.json` 設定**相同**的 `DB_*` 值
> 與向量化模型——後端讀取的正是管線寫入的內容。見[資料匯入管線](#資料匯入管線)。

> **內部資料庫密碼。** HIWIN 內部部署使用資料庫密碼 **`hiwinpassword`**——請填入
> `.env`（`DB_PASSWORD`），管線則填入 `pipeline/settings.json`（`DB_PASS`）。它刻意
> 保留於 `reference/tools/database_query.py`（保存的 open-WebUI 出處）而未清除。
> 本專案為專有／僅供內部使用（見 [LICENSE](LICENSE)）；若日後公開或對外分享，
> 請**更改／輪換此密碼**。

## 模型參數

大多數生成設定位於**推論伺服器**——即 `--models-preset`（`router.example.ini`）中每個
模型的區段，而非本專案內。最重要的兩項：

- **Temperature（溫度，`--temp`，例如 `--temp 0.7`）。** 輸出的隨機程度：`0` 為
  確定性（相同輸入→相同答案），較高值（`0.7`–`1.0`）用詞較多變。**對精確度要求高的
  工作請設為低／零**——例如從規格表轉錄確切數字——僅在需要自然語句時才調高。
  - **後端**會額外於每次請求送出 `temperature`（環境變數 `TEMPERATURE`，預設 `0`；
    見 `inference.py`），此值會**覆蓋**伺服器對支援代理所設的 `--temp`。
  - **管線**各 pass 不覆蓋此值，因此會使用您在 preset 中為各模型設定的
    `temp`。
- **Context size（脈絡長度，`--ctx-size`，例如 `--ctx-size 100000`）。** 模型在單次
  請求中可處理的最大 token 數（提示**＋**生成輸出）。這是啟動時／VRAM 設定：脈絡越大
  所需 GPU 記憶體越多。請設為足以容納您最長的頁面或表格再加上模型的答覆；若請求超過
  此長度，伺服器會截斷輸入或報錯。應用程式不會更動此值——它由您啟動模型的方式決定。

preset 範例（於 `router.example.ini`）：`ctx-size = 100000`、`temp = 0.7`……請於
preset 中修改後重啟路由；本專案無需改動。

## 啟動服務

請在**專案根目錄**（含 `main.py` 的資料夾）執行後端，並**啟用虛擬環境**——
`uvicorn` 需匯入 `main`，因此工作目錄必須是專案根目錄：

```bash
cd /path/to/HIWIN_Support_Agent      # 專案根目錄（main.py 所在處）
# 啟用 venv： Windows： .venv\Scripts\activate  |  Linux/macOS： source .venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8079
```

（或直接使用啟動腳本：Windows 的 `run.bat` / Linux/macOS 的 `./run.sh`——它會自動
啟用 venv 並啟動 uvicorn。）

- `--host 0.0.0.0` 可讓其他機器連線（請留意防火牆）。
- 省略此參數（或使用 `--host 127.0.0.1`）則僅限本機存取。
- 接著開啟 `http://localhost:8079/` 看展示頁，或對 `/chat` 發送 POST（見[使用方式](#使用方式)）。

### 逐層驗證（由下而上的煙霧測試）

若 `/chat` 表現異常，請由下而上逐層測試——下列每個指令只有在前一個成功時才會成功，
因此**第一個失敗的指令即指向需要修正的層級**：

```bash
# 1. 後端程序已啟動：
curl http://localhost:8079/health                 # -> {"status":"ok"}

# 2. 推論伺服器可連線（主機／埠請對應 INFERENCE_HOST）：
curl http://localhost:11400/v1/models             # -> 列出您的模型別名

# 3. Postgres 可連線且已匯入具正確語言代碼的資料：
python inspect_metadata.py                         # -> 印出樣本 metadata_ 列

# 4. 完整的端到端流程：
curl -X POST http://localhost:8079/chat \
  -H "Content-Type: application/json" \
  -d '{"prompt": "HGW20 load capacity", "language": "en"}'
```

接著至[疑難排解](#疑難排解)對照失敗的層級。

## 使用方式

### `POST /chat`

```bash
curl -X POST http://localhost:8079/chat \
  -H "Content-Type: application/json" \
  -d '{"prompt": "HGW20 線性滑軌的額定負荷是多少？", "language": "tc"}'
```

回應：

```json
{
  "response": "### HGW20 額定負荷 …（Markdown，可能含 ![](/static/HIWIN/…) 圖片）",
  "language": "tc",
  "sources": [
    {"product_type": "linear_guideway", "page_number": 6, "file_name": "…", "language_code": "tc"}
  ],
  "trace": [
    {"tool": "db_get_available_product_tables", "args": {}, "result": "…"},
    {"tool": "db_search_technical_manuals", "args": {"…"}, "result": "…"}
  ]
}
```

- **`response`** — 以 GitHub 風格 Markdown 呈現的答覆，可用任一 Markdown 函式庫
  渲染；內嵌的 `![](...)` 圖片會對應到 `/static/HIWIN`。
- **`language`** — 請求中可省略，省略時採用 `DEFAULT_LANGUAGE`。
- **`sources`** — 檢索片段去重後的引用來源中繼資料。
- **`trace`** — 代理依序呼叫的工具（便於除錯）。

### 其他端點

| 端點 | 用途 |
|---|---|
| `GET /` | 範例展示前端（由 `frontend/index.html` 提供）。 |
| `GET /health` | 健康檢查 → `{"status": "ok"}`。 |
| `GET /static/HIWIN/...` | 由 `IMAGE_STATIC_ROOT` 提供 HIWIN 圖示。 |

### 批次測試工具

`run_prompts.py` 會送出一組請求並輸出 JSON 報告（請求、每筆的耗時，以及完整回應——答覆、來源、trace、metrics）：

```bash
python run_prompts.py                          # 執行所有 examples/*.json
python run_prompts.py examples/tc_load_capacity.json
export CHAT_URL=http://localhost:8079/chat     # 覆寫端點位址（Linux/macOS）
set CHAT_URL=http://localhost:8079/chat        # 覆寫端點位址（Windows cmd）
```

### 檢視資料庫中繼資料

`python inspect_metadata.py` 會印出數筆樣本列的 `metadata_`，可用來確認實際的
`language_code` 值與引用來源欄位。

## 對話記錄

每次 `POST /chat` 對話都會記錄到 Postgres 資料表，供分析與除錯。此功能**預設開啟**
（`CHAT_LOG_ENABLED=true`），並存放於同一資料庫中的**獨立 schema——`hiwin_cs_db`**，
與 RAG 資料隔離。記錄為**盡力而為**：資料庫發生問題時只會印出並略過，絕不影響對話
回應。schema 與資料表會於首次使用時自動建立（資料庫使用者首次需具備 `CREATE` 權限）。

每一列記錄：

| 欄位 | 意義 |
|---|---|
| `created_at` | 時間戳記 |
| `language`、`prompt`、`response` | 請求語言、使用者提示、最終答覆 |
| `sources`、`trace` | 引用來源清單與工具呼叫紀錄（jsonb） |
| `latency_ms` | 該請求的總耗時 |
| `llm_calls`、`agent_iterations`、`tool_calls` | 使用的模型生成數／代理回合數／工具呼叫數 |
| `prompt_tokens`、`completion_tokens`、`total_tokens` | token 用量（各次生成加總） |
| `generations` | jsonb 陣列，每次生成的 `{duration_ms, prompt_tokens, completion_tokens}` |

設定（於 `.env`）：`CHAT_LOG_ENABLED`（預設 `true`）、`CHAT_LOG_SCHEMA`（預設
`hiwin_cs_db`）、`CHAT_LOG_TABLE`（預設 `chat_logs`）。設為 `CHAT_LOG_ENABLED=false`
即可關閉。

## 範例展示前端

`frontend/index.html` 是一個小巧、自包含的 HTML 頁面，於 `/` 提供，純粹用來
**示範** API——輸入提示詞、選擇語言，即可看到渲染後的 Markdown 答覆、內嵌圖片與
來源。請將它視為起點或參考，而非正式 UI；可隨時換成您自己的前端。

由於它與 `/chat`、`/static/HIWIN` 由**同源**提供，答覆中的根相對圖片連結無需額外
設定即可解析到本後端。（若使用不同源的前端，則需反向代理或將圖片 URL 改為絕對
路徑。）此頁透過 CDN 載入 `marked` 來渲染 Markdown——若瀏覽器無法連網，請改為
在本地保存該檔；LaTeX（`$M_R$`）需 KaTeX，本頁未內含。

### 除錯面板

展示頁有一個 **Debug view（除錯檢視）** 開關（預設開啟）——這是了解答覆*為何如此*
最快的方式，也是排除問題的第一站。它會在每則答覆下方顯示：

- **指標列**——語言、代理迭代次數、工具／LLM／完整性呼叫次數、token 用量、以及
  端到端延遲。
- **Trace 時間軸**——依序列出每次工具呼叫，各含其**參數**（模型所搜尋的
  `language_code` / `product_table` / 查詢字串）與其**結果**（內嵌顯示）；空結果會被
  標示，因此錯誤的語言過濾或漏掉的資料表一目了然。（`TRACE_RESULT_MAX_CHARS` 控制保留
  多少結果——設為 `0` 可讀取完整段落。）
- **完整性檢查**——逐則檢索段落顯示列舉是否 `✓ found`、回傳 `NONE`（若
  `COMPLETENESS_EXPLAIN_NONE` 則含原因），或發生 `⚠ error`（例如逾時）。
- **來源**——各自附上 reranker `score`，以及一份 **Raw response JSON** 傾印。

此處顯示的一切也都在原始 `/chat` 回應（`trace`、`sources`、`metrics`）與
[對話記錄](#對話記錄)中，因此不透過展示頁也能從 API 取得相同洞察。

## 資料匯入管線

本後端僅**讀取** Postgres + pgvector 資料庫，並不負責建立它。
[`pipeline/`](pipeline/) 資料夾即為其**上游**——負責建立資料的另一半：

```
PDF ──▶ pipeline/（版面偵測 → VLM 轉錄 → 驗證 → 向量化）──▶ Postgres/pgvector ──▶ 本後端
```

它以 ONNX 模型偵測頁面版面，透過本地視覺模型轉錄頁面／圖片／表格，驗證 markdown，
再將每一頁／段落向量化並寫入後端檢索工具所查詢的同一個 `hiwin_rag` schema
（`data_*` 資料表、`metadata_` jsonb）。其向量化模型**必須**與後端的
`EMBEDDING_MODEL` 一致；它複製到網頁靜態根目錄的圖片，即為後端在
`/static/HIWIN` 所提供的圖片。

### 執行前必須修改的項目

幾乎所有設定都已預先填妥。使用您自己的資料庫時，只需設定少數**機器相關的路徑**
（已納入版控的設定檔以 `/path/to/...` 佔位）：

| 檔案 | 欄位 | 設為 |
|---|---|---|
| `pipeline/settings.json` | `ROOT_PATH` | 您的 HIWIN 資料夾（內含 `PDFs/`、`PDF_Config.yaml`、ONNX 模型） |
| `pipeline/settings.json` | `IMAGE_TARGET_ROOT` | 後端在 `/static/HIWIN` 提供的網頁靜態資料夾 |
| `pipeline/models.json` | `model_dir` | llama.cpp 載入 GGUF 的資料夾（路由 preset 中的 `model` 路徑） |
| `pipeline/models.json` | 各 `repo_id` | 每個 GGUF 的 Hugging Face repo（僅在使用下載器時需要） |
| `<ROOT_PATH>/PDF_Config.yaml` | — | 選用——首次執行會由 `PDFs/` **產生後停止**供您檢視（語言由檔名自動偵測；每頁皆處理）。檢查後再執行一次。可從 `pipeline/PDF_Config.example.yaml` 預先建立以略過此步。 |
| `.env`（後端） | `IMAGE_STATIC_ROOT` | 與管線 `IMAGE_TARGET_ROOT` 相同的資料夾（資料庫密碼與模型名稱已填妥） |

資料庫認證、模型名稱、連接埠與調校參數皆已為內部部署設定妥當——僅在您的環境不同時
才需更改。（Windows 上 `C:\...` 或 `C:/...` 皆可；正斜線於所有作業系統皆適用。）

### 專案根目錄（`ROOT_PATH`）應包含什麼

`ROOT_PATH` 是管線的資料夾。您建立它並放入**兩樣**東西（其餘自動產生）：

```
<ROOT_PATH>/
├── PP-DocLayout-PlusL.onnx   # 您提供：版面偵測模型（Pass 1）。
│                             #   必須直接放在 ROOT_PATH 並使用此確切檔名。
├── PDFs/                     # 您提供：來源 PDF，依 產品/子資料夾/檔案 巢狀
│   └── <product>/<sub-folder>/<file>.pdf
├── PDF_Config.yaml           # 要處理哪些 PDF／頁（首次執行自動建立，檢視後再執行一次）
├── Process_Files/            # 各 pass 的中間產物（自動建立）
└── Final_Output/             # 最終 markdown + Figures/Tables（自動建立）
```

因此您**唯一需要手動放置的檔案**是 `PP-DocLayout-PlusL.onnx`（直接放在
`ROOT_PATH`）與 `PDFs/` 下的 PDF。ONNX 模型即 PaddleOCR PP-DocLayout_plus-L 檔
（見[環境需求](#環境需求)）。

### 設定要處理哪些 PDF（`PDF_Config.yaml`）

管線由位於 `<ROOT_PATH>/PDF_Config.yaml` 的 YAML 檔驅動，列出您的 PDF。

**若該檔不存在，管線會先自動產生它，然後停止**——讓您在處理前先檢視。當執行時找不到
`PDF_Config.yaml`，它會掃描 `<ROOT_PATH>/PDFs/`，每個 PDF 一筆、**處理每一頁**，並
**由檔名自動偵測各 PDF 的語言**：

- 檔名含日文假名 → `jp`；含中文字 → `tc`；
- 否則看檔名中的語言代碼（例如 `_en`、`_jp`、`zh-tw`、`zh-cn`）；
- 再退回 `DEFAULT_LANGUAGE`（預設 `tc`；可用 `PIPELINE_DEFAULT_LANGUAGE` 環境變數覆蓋）。

接著它會**停止並提示您檢視與編輯所產生的檔案**（該檔開頭也有提示）——請確認各
`language` 並加入需要的 `pages_to_exclude`——然後**再次執行**才會真正處理。自動偵測
為盡力而為（僅含漢字的日文標題可能被誤判為中文），值得花點時間檢查。

您也可以自行撰寫：將 [`pipeline/PDF_Config.example.yaml`](pipeline/PDF_Config.example.yaml)
複製為 `<ROOT_PATH>/PDF_Config.yaml` 後編輯。它是一棵巢狀資料夾樹，最末端為**每個
PDF 一個葉節點**：

```yaml
ballspline:                  # 產品——最上層資料夾與資料表（data_ballspline）
  user_manual:               # 任意層數的分組子資料夾
    HG_Series.pdf:           # 葉節點鍵 = 實際的 PDF 檔名
      language: en           # en | jp | tc | sc（須與資料庫 language_code 一致）
      pages_to_exclude: [0, 1]      # 要略過的頁（0 起算；封面、空白頁…）
    LM_Guide.pdf:
      language: tc
      pages_to_include: [5, 6, 7]   # 若有且非空，則「僅」處理這些頁
                                    # （此時忽略 pages_to_exclude）
linear_guideway:
  catalog.pdf:
    language: en
    pages_to_exclude: []            # [] = 處理每一頁
```

判讀方式：

- 當節點同時具有 `language` 鍵，以及 `pages_to_include` **或** `pages_to_exclude`
  時，即視為一個 **PDF 任務**；其上的層級僅為資料夾巢狀結構。
- PDF 檔須實際存在於
  `<ROOT_PATH>/PDFs/<產品>/<…子資料夾…>/<葉節點檔名>`。
- 頁碼為 **0 起算**。`pages_to_include` 非空時優先於 `pages_to_exclude`。兩者皆可
  為清單（`[0, 2, 5]`）或 JSON 字串（`"[0, 2, 5]"`）。
- 輸出寫入 `Final_Output/<產品>/<以 _ 連接的子資料夾>/<語言>/`，並匯入
  `data_<產品>` 資料表。`Process_Files/` 與 `Final_Output/` 會自動建立。

### 執行管線

`pipeline/settings.json` 與 `pipeline/models.json` 皆已**納入版控**（本部署已填妥）。
首次執行前，您只需為自己的機器／作業系統**修改其中的檔案路徑**（見
[執行前必須修改的項目](#執行前必須修改的項目)）。接著：

所有管線指令都請在 **`pipeline/` 資料夾內**執行，並**啟用 venv**（它們會匯入
`core` / `Pipeline`，因此工作目錄必須是 `pipeline/`）：

```bash
# 1. 啟用 venv（於專案根目錄）：
#    Windows：      .venv\Scripts\activate
#    Linux/macOS：  source .venv/bin/activate
# 2. 進入 pipeline 資料夾：
cd pipeline
# 3. 擇一執行：
python gui.py                 # 圖形介面：設定畫面 + 即時逐 PDF 監控
python tui.py                 # 終端介面：相同功能（建議用 Windows Terminal，勿用 cmd.exe）
python Pipeline.py            # 無介面：完整管線（無 UI；讀取 settings.json／環境變數）
python -m ingestion.Ingest    # 僅將 Final_Output 的 markdown（重新）匯入資料庫
python -m ingestion.Ingest --db-only   # 匯入但不複製圖片至網頁靜態根目錄
```

兩種前端**擇一使用**即可——圖形版 `gui.py` 或終端版 `tui.py`，功能相同。兩者啟動時
都會先進入**設定畫面**（可編輯所有欄位，含遮蔽顯示的**資料庫密碼**），接著顯示每個
PDF 與每個 pass 的即時監控。已納入版控的 `settings.json` 已含內部資料庫密碼；於設定
畫面修改（或設定 `DB_PASS` 環境變數）即可覆蓋。您在該畫面所做的變更會存回
`settings.json`。

兩種介面預設皆為**繁體中文**，並內建**英文 ⇄ 繁體中文切換**：於 GUI 標題列點擊
`中`/`EN` 按鈕，或在 TUI 設定畫面按 `l`。設定欄位、狀態、階段與摘要等標籤會即時切換
語言。若需完全無人值守的自動化，請改用 `python Pipeline.py`（無介面；讀取
`settings.json` 或環境變數）。

### 下載模型檔案（選用）

管線與後端都是透過 HTTP 呼叫您的推論伺服器，本身並不載入 GGUF。為方便起見，
GUI／TUI 可檢查您伺服器所需的 GGUF 檔案是否存在於某個資料夾，並在您確認後自
**Hugging Face** 下載缺少的檔案：

1. `pipeline/models.json` 已納入版控，且已列出 GGUF **檔名**。請編輯以設定：
   - `model_dir`——您的 llama.cpp 載入 GGUF 的資料夾（須與路由 preset 中的
     `model` 路徑一致）；
   - 每個模型的 Hugging Face `repo_id`（託管該 `.gguf` 的 repo）——`hf_token` 為選用，
     供受限／私有 repo 使用（亦可用 `HF_TOKEN` 環境變數）。
2. 觸發檢查／下載：
   - **GUI**——於設定畫面點擊 **下載模型…**；
   - **TUI**——於設定畫面按 `m`。

   它會回報哪些檔案已存在、列出缺少的，並**僅在您確認後**將其下載至 `model_dir`
   （可續傳，透過 `huggingface_hub`）。

這只會備妥**檔案**；您仍需自行設定並執行推論伺服器。設定中的模型**名稱**即路由
preset 的區段名稱，因此由此 manifest 將每個名稱對應到實際的 Hugging Face repo／檔案。

管線的相依套件已併入根目錄的 [`requirements.txt`](requirements.txt)（較重的
ML／視覺區塊），因此單一 `pip install -r requirements.txt` 即可備妥兩半。其設定
**不與**後端的 `.env` 共用：管線讀取 `pipeline/settings.json`（可由圖形介面編輯；
覆蓋 `pipeline/core/config.py` 的預設值）。請讓兩者的資料庫名稱／schema 與
向量化模型保持一致。

### 模型名稱如何串接

除了**下載** GGUF（GUI/TUI）與**託管**（llama.cpp 路由模式）之外，無需其他接線
——名稱會自動串接：

```
models.json ──(檔名)──> model_dir 內的 .gguf
                              │  路由 preset 的區段以其名稱載入它
                              ▼
路由 preset 的 [區段] 名稱 ──被使用於──> 管線 MODEL_PASS_*（settings.json）
                                        後端 LANGUAGE/EMBEDDING/RERANKER_MODEL（.env）
```

有兩組名稱必須一致——且目前出廠即已一致：

1. **模型名稱**——路由 preset 的 `[區段]` 名稱等於應用程式請求的名稱：管線
   `MODEL_PASS_2/2B/3` = `RAG_Pipeline_Pass34`、`MODEL_PASS_3B/4` =
   `RAG_Pipeline_Pass5Ingest`；後端 `LANGUAGE_MODEL` / `EMBEDDING_MODEL` /
   `RERANKER_MODEL` = `Support_Agent_Qwen3.6` / `Embedding_Qwen3.6` /
   `Reranker_Qwen3.6`。
2. **檔名**——每個 `models.json` 的 `filename` 等於該 preset 區段載入的 `model`
   檔案，兩者都在 `model_dir` 內。

因此 `MODEL_PASS_3`（及其餘）開箱即正確解析：應用程式請求模型名稱，路由即載入該區段
的 GGUF。若您日後重新命名模型，請於**兩處**同步修改（路由 preset 與 settings／
`.env`），並讓 `model_dir` 指向 preset 載入的資料夾。

## 專案結構

```
HIWIN_Support_Agent/
├── main.py             # FastAPI 應用：/chat, /health, /, /static/HIWIN
├── agent.py            # LLM 工具呼叫迴圈（狀態機驅動器）
├── rag_tools.py        # 四個檢索工具（pgvector → rerank → 視覺）
├── tool_schemas.py     # OpenAI 函式呼叫 schema 與分派
├── inference.py        # chat / embeddings / rerank 的 HTTP 封裝
├── db.py               # Postgres 存取與 SQL
├── prompts.py          # 組成系統提示 = System_prompt.md + skill
├── config.py           # 以環境變數驅動的設定（載入 .env）
├── router.example.ini  # llama.cpp 路由 preset（改好模型路徑後即可啟動）
├── start.bat / start.sh# 一鍵啟動：路由（所有模型）+ 後端
├── run.bat / run.sh    # 僅啟動後端（路由已在執行時）
├── run_prompts.py      # 批次測試工具
├── inspect_metadata.py # 資料庫中繼資料檢視工具
├── frontend/index.html # 範例展示前端
├── prompts/
│   ├── System_prompt.md
│   └── skills/         # english / japanese / traditional_chinese / simplified_chinese
├── examples/           # 範例請求內容
├── docs/               # ARCHITECTURE（英文 + 繁中）
├── reference/          # 原始 open-WebUI filter 與 tool，作為出處保存
└── pipeline/           # 資料匯入管線（PDF → pgvector 資料庫）
    ├── Pipeline.py     #   流程協調器（Phase A 各 pass → 匯入）
    ├── gui.py          #   Tkinter 圖形介面：設定 + 即時監控
    ├── tui.py          #   終端介面（rich）：相同的設定 + 即時監控
    ├── pdf_passes/     #   Pass 1（ONNX 版面）→ 2/2b/3/3b（VLM）→ 4（驗證）
    ├── ingestion/      #   將 markdown 向量化 → Postgres/pgvector（Ingest.py）
    ├── core/           #   設定、i18n（英文／繁中）、檢查點、工具函式
    ├── settings.json          # 已納入版控的設定——請依機器修改檔案路徑
    ├── models.json            # 已納入版控的 GGUF 清單——請修改 model_dir 與 repo_id
    └── PDF_Config.example.yaml # 複製為 <ROOT_PATH>/PDF_Config.yaml；列出 PDF
```

## 疑難排解

| 症狀 | 可能原因／解法 |
|---|---|
| 連線被拒／呼叫推論伺服器（`:11400`）時發生錯誤 | 路由（`llama-server --models-preset`）未啟動，或 `INFERENCE_HOST` 指向錯誤的主機／埠。請以 `start.bat`／`start.sh` 啟動，並以 `curl http://localhost:11400/v1/models` 確認已列出模型。 |
| `CREATE EXTENSION vector` 失敗：*「extension "vector" is not available」* | pgvector 尚未安裝進 Postgres（它是伺服器端擴充，而非 pip 套件）。請安裝 pgvector 的作業系統套件或使用 `pgvector/pgvector` Docker 映像後再重試。建立擴充需要**超級使用者**。 |
| `fe_sendauth: no password supplied` | `DB_PASSWORD` 空白或未設定。請於 `.env`（專案根目錄的隱藏檔；見[設定](#設定)）將它設為與 `pipeline/settings.json` 的 `DB_PASS` 相同的值後重啟。若確實已設定，代表 `.env` 未載入——請確認已安裝 `python-dotenv`（`pip install -r requirements.txt`）。 |
| 代理不斷呼叫 `db_search_*` 後回傳無意義的答覆 | 每次檢索都出錯（常是上述資料庫認證問題）或無結果，模型便一直重試直到 `MAX_AGENT_ITERS`。請開啟 **除錯面板**（或原始 `trace`）與後端主控台，檢視每次搜尋回傳了什麼。 |
| `connection ... server closed the connection unexpectedly` | Postgres 僅信任本機連線。請將後端部署在**資料庫伺服器上**（使用 `localhost`），或於 `pg_hba.conf` / `postgresql.conf` 開放遠端連線。 |
| `db_search_*` 回傳「No results for language…」 | 送出的 `language_code` 與資料庫不符。請以 `inspect_metadata.py` 確認。 |
| `Reranking failed: HTTP 500 … input is too large … increase the physical batch size` | 重排序模型的 `ubatch-size` 太小。請於路由 preset 中該 reranker 區段設定 `ubatch-size = 4096` / `batch-size = 4096`，或調低 `RERANK_DOC_MAX_CHARS`。 |
| 有回覆但代理從未呼叫工具 | 推論伺服器未回傳 OpenAI 格式的 `tool_calls`。請確認其支援工具呼叫；若格式不同，請調整 `agent.py`。 |
| 答覆不完整——只列出部分產品／漏掉表格列 | 請查看 **除錯面板**：若搜尋已取回資料，即為列舉不足——請保持 `COMPLETENESS_PASS_ENABLED=true` 與 `GRADING_ENABLED=false`（grading 可能丟棄相關頁面）。若某段落顯示 `NONE` 且原因有誤，代表列舉過度過濾。 |
| 回覆很慢（數分鐘） | 多半是完整性檢查（每則檢索段落一次模型呼叫）加上冷啟動載入模型。請將 `COMPLETENESS_MODEL` 指向常駐的小模型並調高 `COMPLETENESS_CONCURRENCY`；調高 `CHAT_TIMEOUT`；或調低 `RERANK_TOP_K`。除錯面板的 `completeness_calls` 與延遲可看出時間花在哪。 |
| 多次搜尋的查詢出現 `read timeout` / 脈絡（n_ctx）溢位 | 冷啟動請調高 `CHAT_TIMEOUT`；脈絡溢位請調低 `RERANK_TOP_K`、`FIGURE_TEXT_MAX_CHARS` 或 `KEEP_RECENT_TOOL_RESULTS`（會從脈絡中修剪較舊的工具結果）。 |
| 展示頁中圖片出現 404 | `IMAGE_STATIC_ROOT` 未設定或指向錯誤資料夾。可直接開啟某個 `/static/HIWIN/...` URL 驗證。 |
| 在另一個前端中圖片無法顯示 | 前端非同源。請改由本後端提供前端、將兩者置於同一反向代理之後，或將圖片 URL 改為絕對路徑。 |

## 已知問題

- **各語言間的型錄版本不一致。** 目前資料庫各語言所收錄的型錄版本不同（例如
  英文／中文來自較新版本，日文來自較舊版本），因此部分規格數值在不同語言間會
  合理地不一致。這屬於**資料**層面的問題（應於匯入管線修正），並非後端錯誤。
- **展示前端中的 LaTeX。** `$...$` 數學式（例如 `$M_R$`）會以純文字呈現；如有需要
  可於展示頁加入 KaTeX。

## 授權

專有／僅供內部使用。詳見 [LICENSE](LICENSE)。

本專案使用第三方軟體與模型，其授權列於
[THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)。其中兩項在對外散布前須特別留意：
**PyMuPDF**（AGPL-3.0，僅用於管線）與 **Gemma** 模型（Google 的 Gemma 使用條款）。
