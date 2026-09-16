# HIWIN Support Agent（HIWIN 支援代理）

[English](README.md) · **繁體中文**

**作者：** ACPIE_Lab

一套自架的 HIWIN 工業產品**支援代理**：用戶端把問題與語言代碼送到 `POST /chat`；
服務會對本地的 **PostgreSQL + pgvector** 知識庫與本地的 **llama.cpp** 推論伺服器執行
代理式（agentic）檢索流程，回傳含結構化引用與內嵌技術圖示的 Markdown 答覆。本專案
同時內含由 HIWIN PDF 型錄**建立**該知識庫的資料匯入管線，因此一個專案涵蓋
「建立」與「服務」兩端。

> **接手這個專案？從這裡開始。**
> 1. 先讀 [§2 各部件如何串接](#2-各部件如何串接)——三個設定檔必須一致，多數問題都是其中之一寫錯。
> 2. 執行 `python doctor.py --all`——它檢查整個堆疊並明確指出該修什麼。
> 3. 日常工作（調整答覆、新增 PDF、備份）請看 [docs/MAINTENANCE.zh-Hant.md](docs/MAINTENANCE.zh-Hant.md)；
>    只想照步驟操作的人請看 [docs/操作手冊.md](docs/操作手冊.md)。
> 4. [AGENTS.md](AGENTS.md) 是給 AI 程式代理與新開發者的精簡簡報；[docs/ARCHITECTURE.zh-Hant.md](docs/ARCHITECTURE.zh-Hant.md) 說明設計。

---

## 目錄

1. [功能](#1-功能)
2. [各部件如何串接](#2-各部件如何串接)
3. [目前部署快照](#3-目前部署快照)
4. [環境需求](#4-環境需求)
5. [安裝](#5-安裝)
6. [路徑 A——服務既有資料庫](#6-路徑-a服務既有資料庫)
7. [路徑 B——搬到另一台機器](#7-路徑-b搬到另一台機器)
8. [路徑 C——由 PDF 重建資料庫](#8-路徑-c由-pdf-重建資料庫)
9. [PostgreSQL 與 pgvector](#9-postgresql-與-pgvector)
10. [設定參考](#10-設定參考)
11. [使用 API](#11-使用-api)
12. [資料匯入管線](#12-資料匯入管線)
13. [維護](#13-維護)
14. [疑難排解](#14-疑難排解)
15. [已知問題](#15-已知問題)
16. [專案結構](#16-專案結構)
17. [作者與第三方聲明](#17-作者與第三方聲明)

---

## 1. 功能

- **單一 `POST /chat` API**——輸入 `{prompt, language}`；輸出 JSON，含 Markdown
  `response`、結構化 `sources`、工具呼叫紀錄 `trace` 與計時 `metrics`。
- **代理式狀態機**——LLM 工具呼叫迴圈驅動「路由 → 檢索 → 格式化 → 後備」，以自然語言
  定義於 `prompts/System_prompt.md`，另有每種語言一份 skill 檔。
- **多語言**——英文（`en`）、日文（`jp`）、繁體中文（`tc`）、簡體中文（`sc`）；答覆與
  使用者的字集一致。
- **完整檢索管線**——向量化 → pgvector 相似度搜尋（逐產品表並行）→ 重排序 → 選用的
  圖示視覺分析 → **完整性檢查**（逐段重讀，使長規格表完整重現而非被摘要）。
- **結構化引用**——由每個片段的 `metadata_` 建立，並修剪為答覆實際引用的頁碼。
- **圖片服務**——圖示於 `/static/HIWIN` 提供，Markdown 圖片連結可同源解析。
- **對話記錄**——每次對話（提示、答覆、來源、追蹤、耗時、token）存入
  `hiwin_cs_db.chat_logs`，供分析與除錯。
- **附除錯面板的展示前端**於 `/`——僅供示範；產品是 API。

```
POST /chat {prompt, language}
  └─ 將「[Language Code: xx]」注入提示詞
  └─ 系統提示 = System_prompt.md + prompts/skills/<language>.md
  └─ 代理迴圈（LLM + 工具 schema）：
        ├─ db_get_available_product_tables   （探索 data_* 資料表）
        ├─ db_search_technical_manuals       （向量化 → pgvector → 重排序 → 視覺 → 完整性）
        ├─ db_search_certifications          （證書 + web_path）
        └─ db_search_product_urls            （下載／CAD 連結）
  └─ 回傳 { response（Markdown）, sources, trace, metrics }
```

## 2. 各部件如何串接

兩個應用共用一個資料庫與一個推論伺服器：

```
            PDF ─▶ pipeline/（版面偵測 → VLM 轉錄 → 驗證 → 向量化）─▶ PostgreSQL + pgvector
                                                                              │  hiwin_rag.data_* 資料表
                                                                              ▼
用戶端 ─▶ POST /chat ─▶ 後端（代理迴圈 + 4 個檢索工具）◀────────────── 向量化 / 重排序 / 對話
                                                                    llama.cpp 路由（config.ini）
```

**三個檔案設定整個系統，且它們必須一致。** 這是「怎麼跑不起來」最常見的原因：

| 檔案 | 設定對象 | 主要內容 |
|---|---|---|
| [`config.ini`](config.ini) | **llama.cpp 路由**（哪些 GGUF、脈絡長度、取樣、GPU 層數） | 每個模型**名稱**一個 `[區段]`；GGUF 檔的絕對路徑 |
| [`.env`](.env) | **後端** | `INFERENCE_HOST`、它請求的模型**名稱**、`DB_*`、`IMAGE_STATIC_ROOT`、答覆品質調校 |
| [`pipeline/settings.json`](pipeline/settings.json) | **管線** | `ROOT_PATH`、`IMAGE_TARGET_ROOT`、`DB_*`、各 pass 的模型**名稱**、`EMBED_MODEL` |

必須成立的對應：

```
config.ini [Support_Agent_Qwen3.6]  ══ .env LANGUAGE_MODEL
config.ini [Embedding_Qwen3.6]      ══ .env EMBEDDING_MODEL      ══ settings.json EMBED_MODEL   （必須是建立資料庫時所用的模型！）
config.ini [Reranker_Qwen3.6]       ══ .env RERANKER_MODEL
config.ini [Support_Agent_Aux]      ══ .env COMPLETENESS_MODEL
config.ini [RAG_Pipeline_Pass34]    ══ settings.json MODEL_PASS_2 / 2B / 3
config.ini [RAG_Pipeline_Pass5Ingest] ══ settings.json MODEL_PASS_3B / 4
settings.json DB_HOST/PORT/NAME/USER/PASS/SCHEMA ══ .env DB_HOST/PORT/NAME/USER/PASSWORD/SCHEMA
settings.json IMAGE_TARGET_ROOT     ══ .env IMAGE_STATIC_ROOT
```

兩個工具維持這些對應：

- `python env_from_settings.py`——把 `settings.json` 的共用值（資料庫／圖片／主機／
  向量化模型）**就地**寫入 `.env`（`.env` 中的調校鍵與註解一律保留）。共用值改在
  `settings.json`，執行它，完成。
- `python doctor.py --all`——驗證上述每個對應、`config.ini` 內每個檔案路徑、資料庫
  （版本、pgvector、資料表、語言代碼、向量寬度）、路由的模型清單等。**永遠先執行它。**

其餘（提示詞、檢索上限、埠號）皆有合理的預設值並已納入版控。

## 3. 目前部署快照

本專案稽核時（2026-09-16，Windows Server 2022）所在機器的事實。換到新機器時這些就是
要改的路徑；`doctor.py` 會逐一指出。

| 項目 | 值 |
|---|---|
| 專案 | `C:\Users\User_11\Desktop\HIWIN\HIWIN_Dem\HIWIN_Support_Agent`（venv `.venv`）。Python 3.12.7 是 Anaconda 的 `C:\ProgramData\anaconda3\python.exe`，**不在 PATH 上**——啟動腳本會自行找到；手動下指令請用完整路徑。 |
| 後端 | `http://localhost:8079`（uvicorn） |
| 推論 | llama.cpp 路由 `http://localhost:11400`；執行檔 `C:\Users\User_11\Desktop\llama\llama-server.exe`；GGUF 位於 `C:\Users\User_11\Desktop\HIWIN\Models` |
| PostgreSQL | **18.1，埠 5432**——資料庫 `hiwin_rag_db`、schema `hiwin_rag`（28 張 `data_*` 資料表、約 29.7k 個向量化頁面、1.4 GB）、對話記錄 `hiwin_cs_db.chat_logs`。pgvector 0.8.1。**另一個叢集（PostgreSQL 17）在埠 5433，屬於其他使用者**——不是我們的。 |
| 資料庫密碼 | 在 `.env`（`DB_PASSWORD`）與 `pipeline/settings.json`（`DB_PASS`），刻意納入版控（內部專案） |
| 資料根目錄（`ROOT_PATH`） | `C:\Users\User_11\Desktop\HIWIN`——`PDFs/`、`PDF_Config.yaml`、`PP-DocLayout-PlusL.onnx`、`checkpoint.json`。管線的 `Process_Files/` 與 `Final_Output/` 以 `.7z` **封存**於此，未解壓。 |
| 圖片 | **不存在**——`IMAGE_STATIC_ROOT`（`…\HIWIN\web_static\HIWIN`）尚未建立，因此答覆沒有圖示、視覺步驟被略過。還原方式：[MAINTENANCE → 還原圖示](docs/MAINTENANCE.zh-Hant.md#還原圖示)。 |
| GPU | 2× NVIDIA RTX A6000 48 GB，**與其他使用者共用**；啟動腳本把路由固定在 GPU 0（`CUDA_VISIBLE_DEVICES=0`）。服務模型組約需 47 GB。 |
| 資料庫語言 | `en`、`jp`、`tc`、`sc`（有服務），另有少數 `kr` 與 NULL 列，代理永遠搜不到——無害。 |

## 4. 環境需求

> **我的機器跑得動嗎？** 服務模型組為四個常駐的 GGUF：35B-A3B 對話／視覺模型（150k
> 脈絡）、4B 向量化、4B 重排序、4B「aux」模型——依現行設定約 **47 GB VRAM**。純 CPU 或
> 較少 GPU 層數*也能*跑，但每題要以分鐘計。GGUF 需數十 GB 磁碟，資料庫與圖示約 5 GB。

**作業系統。** Windows 10/11/Server 或 Linux（Ubuntu/Debian/RHEL 系）；macOS 可跑後端。
兩種啟動腳本都有（`start.bat` / `start.sh`）。換行字元由 `.gitattributes` 固定，在
Windows 複製的資料夾到 Linux 一樣能跑。

**Python 3.12.x**（測試於 3.12.7，`.python-version` 鎖定）。
- Windows：python.org 安裝程式（勾選「Add to PATH」），或 `py -3.12`。
- Ubuntu/Debian：`sudo apt install python3.12 python3.12-venv`（想用管線 GUI 再加
  `python3-tk`）。RHEL：`dnf install python3.12`。

**PostgreSQL 13 – 18 並安裝 pgvector 擴充。** 這些版本皆可；預裝／較舊／多個安裝的
情況與如何裝上 pgvector 見 [§9](#9-postgresql-與-pgvector)。

**llama.cpp `llama-server`**，需支援**路由模式**（`--models-preset`）的較新版本。自
https://github.com/ggml-org/llama.cpp/releases 下載對應 OS/GPU 的版本（NVIDIA 用 CUDA
版）或自行建置。一個程序服務所有模型。

**模型檔（GGUF）**——`config.ini` / `pipeline/models.json` 中的名稱：

| 角色 | 檔案（現行部署） |
|---|---|
| 對話＋視覺（後端 `Support_Agent_Qwen3.6`、管線 `RAG_Pipeline_Pass34`） | `Qwen3.6-35B-A3B-UD-Q4_K_XL_MTP.gguf` + `mmproj-35BA3B.gguf` |
| 向量化（`Embedding_Qwen3.6`，**必須是建立資料庫的那個模型**） | `Qwen3-Embedding-4B.i1-Q4_K_S.gguf`（2560 維） |
| 重排序（`Reranker_Qwen3.6`） | `Qwen3-Reranker-4B.i1-Q4_K_S.gguf` |
| 小型文字模型（後端 `Support_Agent_Aux`、管線 `RAG_Pipeline_Pass5Ingest`） | `gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf`（＋選用的草稿模型 `mtp-gemma-4-E4B-it.gguf`） |
| Qwen 模型的對話範本 | `templates/qwen.jinja`（在本專案內） |

**僅管線需要：** 版面偵測 ONNX 模型 `PP-DocLayout-PlusL.onnx`（PaddleOCR
PP-DocLayout_plus-L，https://huggingface.co/PaddlePaddle/PP-DocLayout_plus-L），直接放在
`ROOT_PATH`；來源 PDF 放在 `ROOT_PATH/PDFs/`。

## 5. 安裝

```bash
git clone https://github.com/Kitty2xl/HIWIN_Support_Agent
cd HIWIN_Support_Agent

# Windows
python -m venv .venv && .venv\Scripts\activate
# Linux / macOS
python3 -m venv .venv && source .venv/bin/activate

pip install -r requirements.txt          # 後端＋管線同一份
# 若全新安裝抓到壞掉的新版套件：  pip install -r requirements.lock.txt
```

`requirements.lock.txt` 是本專案最後驗證通過的精確版本。啟動腳本（`start.*`、`run.*`）
第一次執行時會自動建立 venv 並安裝，因此上面的手動步驟可省略。

> **`python` 不是可辨識的命令？** Windows 常常裝了 Python 卻沒加進 PATH（本機是
> `C:\ProgramData\anaconda3\python.exe`）。`-m venv` 那一步請用完整路徑，或有 py 啟動器時用
> `py -3.12`。啟動腳本會自行嘗試 `python`、`py -3.12` 與常見安裝位置；
> `set PYTHON=C:\path\to\python.exe`（Linux：`export PYTHON=...`）可覆蓋。

Linux 注意：OpenCV 使用 *headless* 版本（不需 `libGL`）。管線 GUI 需要 `python3-tk`；
TUI 與後端不需顯示器。

## 6. 路徑 A——服務既有資料庫

你已有資料庫（本機，或還原自 dump）與 GGUF 檔。

1. **讓啟動腳本指向 llama-server。** 開啟 `start.bat`（Windows）或 `start.sh`（Linux），
   在頂端設定 `LLAMA_SERVER`（或以環境變數匯出）。選用：`MODELS_MAX`、`CUDA_VISIBLE_DEVICES`。
2. **檢查 `config.ini` 路徑**——每個 `model =`、`mmproj =`、`model-draft =`、
   `chat-template-file =` 都必須指向本機實際存在的檔案。
3. **檢查 `.env`**——`DB_*` 對應你的 Postgres，`IMAGE_STATIC_ROOT` 為圖示資料夾
   （尚未存在時見 §3）。
4. **預檢：** `python doctor.py`（由上而下修正 FAIL 行）。
5. **一鍵啟動：**
   ```bash
   start.bat        # Windows（可雙擊）
   ./start.sh       # Linux / macOS
   ```
   腳本會建立 venv、安裝相依套件、以所有服務模型常駐啟動路由、等待就緒、執行
   `doctor.py`，再於 8079 埠啟動後端。Postgres 必須已在執行。
6. **冒煙測試：**
   ```bash
   curl -X POST http://localhost:8079/chat -H "Content-Type: application/json" \
     -d '{"prompt": "HGW20 load capacity", "language": "en"}'
   ```
   或開啟 `http://localhost:8079/` 使用附除錯面板的展示頁。

`run.bat` / `run.sh` 只啟動後端（路由已在執行時）。手動啟動路由：

```bash
llama-server --models-preset config.ini --host 127.0.0.1 --port 11400 --models-max 4
```

### 逐層驗證（由下而上）

```bash
curl http://localhost:8079/health                 # 1. 後端已啟動 -> {"status":"ok"}
curl http://localhost:11400/v1/models             # 2. 路由已啟動 -> 列出模型名稱
python inspect_metadata.py                        # 3. 資料庫可連、資料表與語言代碼
python doctor.py --live                           # 4. 真實的向量化／重排序／對話呼叫
curl -X POST http://localhost:8079/chat -H "Content-Type: application/json" \
  -d '{"prompt": "HGW20 load capacity", "language": "en"}'   # 5. 端到端
```

第一個失敗的步驟就是該修的那層；對照 [§14](#14-疑難排解)。

## 7. 路徑 B——搬到另一台機器

不重新處理 PDF，把運行中的系統搬家（Windows → Linux 或反向）：

1. **複製專案**（clone 或複製資料夾）。`.gitattributes` 讓 shell 腳本維持 LF；若手動複製後
   Linux 抱怨 `bash\r`，執行 `sed -i 's/\r$//' start.sh run.sh`。
2. **複製 GGUF 檔**與 `templates/qwen.jinja`；安裝 `llama-server`。修改 `config.ini` 的路徑
   （Linux 路徑亦可：`/srv/models/x.gguf`）。
3. **在目標機安裝 PostgreSQL + pgvector**（[§9](#9-postgresql-與-pgvector)），在
   `pipeline/settings.json` 設定 `DB_*`，執行 `python env_from_settings.py`，再執行
   `python setup_db.py`（建立資料庫、`vector` 擴充與兩個 schema；可重複執行）。
4. **舊機 dump、新機 restore**——指令見 [§9 備份與搬移資料庫](#備份與搬移資料庫)。
5. **還原圖示**（`IMAGE_STATIC_ROOT`）：從舊機複製資料夾，或由封存的 `Final_Output` 以
   `python -m ingestion.Ingest --assets-only` 重建（[MAINTENANCE](docs/MAINTENANCE.zh-Hant.md#還原圖示)）。
6. `python doctor.py --all`，然後走路徑 A。

ONNX 模型、PDF 與 `PDF_Config.yaml` 只有打算在新機跑管線時才需要一起搬。

## 8. 路徑 C——由 PDF 重建資料庫

**安裝 → 準備 Postgres → 啟動路由 → 以管線建立資料庫 → 提供服務。**

1. 安裝（[§5](#5-安裝)）。
2. 在 `pipeline/settings.json` 設定資料庫，執行 `python env_from_settings.py`，再
   `python setup_db.py`。
3. 啟動路由（`start.bat` / `start.sh` 也會啟動後端，無妨；或執行
   `llama-server --models-preset config.ini … --models-max 6`，讓兩個管線模型能與服務模型
   同時常駐）。
4. 把 ONNX 模型與 PDF 放進 `ROOT_PATH`，執行管線（[§12](#12-資料匯入管線)）。第一次執行會
   產生 `PDF_Config.yaml` 並停下供你檢視；第二次執行才處理。
5. `python doctor.py --all`，然後提供服務（路徑 A）。

> ⚠️ 重新匯入**已在**資料庫內的文件前，請先讀
> [docs/INGESTION_DEFECTS.md](docs/INGESTION_DEFECTS.md)：有幾頁是直接*在資料庫中*手工修復的，
> 重新匯入會把缺陷帶回來。

## 9. PostgreSQL 與 pgvector

**本專案對 Postgres 的需求：** `vector` 型別與 `<=>` 餘弦運算子（pgvector）、`json` 欄位，
以及能 `CREATE SCHEMA` / `CREATE TABLE` 的使用者。資料表**沒有向量索引**（僅 `id` 與
`ref_doc_id` 的 btree），因此不依賴 HNSW/IVFFlat。`CREATE EXTENSION vector` 需要超級使用者
（每個資料庫一次）。

**版本相容性。**

| PostgreSQL | pgvector | 狀態 |
|---|---|---|
| 18、17、16、15、14、13 | 0.8.x（現行 0.8.1） | 支援；目前運行的是 18.1 |
| 12 | ≤ 0.7.4 | 可用；pgvector 0.8 已不支援 PG 12 |
| ≤ 11 | — | 現行 pgvector 不支援；請升級 Postgres |

`setup_db.py` 與 `doctor.py` 會印出伺服器版本，並拒絕 12 以下。

**機器已預裝 PostgreSQL（可能較舊）。** 只要是 13+（12 需搭配舊版 pgvector）都沒問題。步驟：

1. 找出它的埠：Windows 在 `services.msc` 找 `postgresql-x64-NN` 服務，並看
   `<資料目錄>\postgresql.conf` 的 `port = …`；Linux 用 `pg_lsclusters`（Debian）或
   `ss -ltnp | grep postgres`。**多個版本各自監聽不同埠**（5432、5433…）——本機是 18 在
   5432、別人的 17 在 5433。把正確的埠填入 `DB_PORT`。
2. 確認*該版本*有 pgvector（見下）。
3. 在 `pipeline/settings.json` 設定 `DB_*` → `python env_from_settings.py` →
   `python setup_db.py --admin-user postgres --admin-password -`。

**安裝 pgvector**（伺服器端擴充；`pip` 裝不了）：

- Debian/Ubuntu（PGDG 套件庫）：`sudo apt install postgresql-16-pgvector`（對應主版本）。
  RHEL 系：`sudo dnf install pgvector_16`。
- 原始碼（任何 Linux/macOS）：`git clone --branch v0.8.1 https://github.com/pgvector/pgvector && cd pgvector && make && sudo make install`
  （需 `postgresql-server-dev-NN`）。
- Docker：映像 `pgvector/pgvector:pg16`（或 `pg17`、`pg18`）已內建。
- **Windows：** 沒有官方二進位檔。依 pgvector README 以 Visual Studio Build Tools + `nmake`
  建置；或者——當另一個相同主版本的安裝已有它時最簡單——從該安裝複製 `lib\vector.dll`
  與 `share\extension\vector.control` + `share\extension\vector--*.sql` 到
  `C:\Program Files\PostgreSQL\NN\`，重啟服務。本機兩個叢集都已有 0.8.1。
- 然後：`python setup_db.py`（執行 `CREATE EXTENSION IF NOT EXISTS vector`）。

隨時可用 `python doctor.py` → PostgreSQL 區段確認。

### 備份與搬移資料庫

使用 Postgres 附帶的 `pg_dump` / `pg_restore`（Windows：`C:\Program Files\PostgreSQL\18\bin\`）。
只需要兩個應用 schema；`hiwin_rag` 內的 `bak_*` / `repair_backup_*` / `link_backup_*`
快照表是手工修復的歷史，可排除以縮小 dump。

```bash
# Dump（來源機）。自訂格式、兩個 schema、不含修復快照：
pg_dump -h localhost -p 5432 -U postgres -d hiwin_rag_db -Fc \
  -n hiwin_rag -n hiwin_cs_db \
  -T 'hiwin_rag.bak_*' -T 'hiwin_rag.repair_backup_*' -T 'hiwin_rag.link_backup_*' \
  -f hiwin_rag_db.dump

# Restore（目標機）——先以 `python setup_db.py` 建好資料庫與擴充：
pg_restore -h localhost -p 5432 -U postgres -d hiwin_rag_db --no-owner --no-privileges hiwin_rag_db.dump
```

- **目標為相同或更新版的 PostgreSQL：** 上面的指令即可。
- **目標為較舊版的 PostgreSQL**（例如 18 → 16）：比寫出封存檔的 `pg_dump` 更舊的 `pg_restore`
  可能拒絕它（「unsupported version in file header」）。改用純 SQL dump 並以 `psql` 載入；
  刪除舊伺服器不認得的 `SET transaction_timeout = 0;` 等 `SET …` 行：
  ```bash
  pg_dump -h localhost -p 5432 -U postgres -d hiwin_rag_db -Fp -n hiwin_rag -n hiwin_cs_db \
    -T 'hiwin_rag.bak_*' -T 'hiwin_rag.repair_backup_*' -T 'hiwin_rag.link_backup_*' -f hiwin_rag_db.sql
  psql -h localhost -p 5432 -U postgres -d hiwin_rag_db -v ON_ERROR_STOP=0 -f hiwin_rag_db.sql
  ```
  `pg_dump` 一律用**較新**版本 `bin` 目錄裡的（新版 `pg_dump` 可讀舊伺服器；反之會被拒絕）。
- 還原後：`python doctor.py` 必須顯示同樣的 28 張 `data_*` 資料表與
  `embedding dimension in DB - 2560`。

dump 也是正確的每日備份方式。管線雖能從 PDF 重建資料庫，但需要許多 GPU 小時，且會
抹掉手工修復。

## 10. 設定參考

### `.env`（後端）

自專案根目錄自動載入（隱藏檔——`dir /a` / `ls -a`）。共用值由 `settings.json` 經
`env_from_settings.py` 而來；其餘為後端專屬調校。已納入版控的檔案即正式環境設定。

| 變數 | 預設 | 說明 |
|---|---|---|
| `INFERENCE_HOST` | `http://localhost:11400` | 路由基礎 URL。後端在別台機器時填伺服器 IP。 |
| `LANGUAGE_MODEL` | `Support_Agent_Qwen3.6` | 對話＋視覺模型名稱（`config.ini` 區段）。 |
| `EMBEDDING_MODEL` | `Embedding_Qwen3.6` | 向量化模型——**必須是建立資料庫的模型。** |
| `RERANKER_MODEL` | `Reranker_Qwen3.6` | 重排序模型名稱。 |
| `COMPLETENESS_MODEL` | `Support_Agent_Aux`（版控的 `.env`；程式預設 = `LANGUAGE_MODEL`） | 逐段列舉用的小型常駐模型。 |
| `DB_HOST` / `DB_PORT` | `localhost` / `5432` | Postgres 主機／埠（**選對叢集的埠**）。 |
| `DB_NAME` / `DB_USER` / `DB_PASSWORD` | `hiwin_rag_db` / `postgres` / *（必填）* | 認證資訊。 |
| `DB_SCHEMA` | `hiwin_rag` | 存放 `data_*` 的 schema。 |
| `DB_SSLMODE` | `prefer` | libpq SSL 模式（除錯連線時可試 `disable` / `require`）。 |
| `DB_POOL_MAX` | `12` | 連線池最大連線數。 |
| `IMAGE_STATIC_ROOT` | （無） | 於 `/static/HIWIN` 提供並供視覺步驟讀取的資料夾。 |
| `DEFAULT_LANGUAGE` | `tc` | 請求未指定 `language` 時使用。 |
| `TEMPERATURE` | `0` | 對話模型的每請求溫度（覆蓋 preset）。 |
| `CHAT_TIMEOUT` | `120`（版控：`30000`） | 每次呼叫逾時（秒）。刻意設很大：冷載入與長列舉不可被中斷。 |
| `MAX_AGENT_ITERS` | `8` | 每請求的工具呼叫回合上限。 |
| `CHAT_LOG_ENABLED` / `CHAT_LOG_SCHEMA` / `CHAT_LOG_TABLE` | `true` / `hiwin_cs_db` / `chat_logs` | 對話記錄（見 §11）。 |
| `CORS_ALLOW_ORIGINS` | `*` | 允許從瀏覽器呼叫 API 的來源（逗號分隔）；空白則不送 CORS 標頭。見 [docs/API.zh-Hant.md](docs/API.zh-Hant.md)。 |
| `PUBLIC_BASE_URL` | *（空）* | 設定後（如 `http://10.0.0.5:8079`），答覆中的圖片連結變為絕對 URL，供不同來源的前端使用。 |

檢索品質與速度：

| 變數 | 預設 | 說明 |
|---|---|---|
| `VECTOR_TOP_K` | `15` | 每張資料表取回的 pgvector 列數。 |
| `RERANK_CANDIDATE_K` | `20` | 送入 reranker 的段落數。 |
| `RERANK_TOP_K` | `6` | 重排序後保留的段落數（手冊搜尋）。 |
| `CERT_RERANK_TOP_K` | `10` | 證書搜尋保留的段落數。 |
| `RERANK_DOC_MAX_CHARS` | `2000`（版控：`1000`） | 每段送入 reranker 評分的字元數。越低重排越快。 |
| `FIGURE_TEXT_MAX_CHARS` | `120` | 冗長圖說在模型看到前截斷。`-1` 停用。 |
| `KEEP_RECENT_TOOL_RESULTS` | `4`（版控：`-1`） | 脈絡中只完整保留最近 N 筆工具結果。`-1` = 全保留（`pre_draft` 必需）。 |
| `PARALLEL_TOOL_CALLS` | `true` | 一回合的多個工具呼叫並行執行。 |
| `DB_SEARCH_CONCURRENCY` | `8` | 單次工具呼叫內逐表向量搜尋的並行數。 |
| `GRADING_ENABLED` | `false` | 逐段 LLM YES/NO 相關性過濾。關閉：它與窮盡召回相衝突。 |
| `COMPLETENESS_PASS_ENABLED` | `true` | 逐段重讀以救回草稿摘掉的列。 |
| `COMPLETENESS_MODE` | `post_draft`（版控：`pre_draft`） | `pre_draft`：每次搜尋後立即抽列、起草一次。`post_draft`：先起草，再列舉並整合。 |
| `COMPLETENESS_CONCURRENCY` | `1`（版控：`4`） | 同時進行的列舉呼叫。aux 模型 `parallel = 1` 時只是排隊。 |
| `COMPLETENESS_TIMEOUT` | `120` | 每次列舉逾時（秒）。 |
| `COMPLETENESS_EXPLAIN_NONE` | `true` | 不符合的段落說明原因（除錯輔助，顯示於除錯面板）。 |
| `VISION_MAX_IMAGES` | `4` | 每次搜尋送往視覺模型的圖示上限。 |
| `TRACE_RESULT_MAX_CHARS` | `600` | `trace` 保留每筆工具結果的字元數。`0` = 全部。 |

### `pipeline/settings.json`（管線）

由 GUI/TUI 設定畫面或手動編輯。常用：`ROOT_PATH`、`IMAGE_TARGET_ROOT`、`DB_*`、
`EMBED_MODEL`；進階：`LLM_BASE_URL`、`MODEL_PASS_*`、Pass 1 偵測設定、並行數、行為開關。
每個鍵的預設值與說明在 `pipeline/core/config.py`。`EMBED_DIM`（2560）刻意**不可**在此編輯
——它與向量化模型及既有資料表綁定。

### `config.ini`（路由）

每個模型一個 `[區段]`；鍵為去掉 `--` 的 `llama-server` 參數。改檔後重啟路由。註解說明
每個非預設值的由來（記錄了真實事故——請保留）。兩個常碰的：

- **Temperature**（`temp`）：`0` = 確定性。後端對對話模型每請求送出自己的 `TEMPERATURE`，
  因此 preset 的值作用於管線各 pass 與 aux 模型。
- **Context size**（`ctx-size`）：每請求的提示＋輸出 token；屬 VRAM 設定。`ctx-size` 由
  `parallel` 個 slot 平分。

`--models-max`（啟動腳本變數 `MODELS_MAX`，預設 4）是可同時常駐的模型數；四個服務模型
剛好占滿，管線模型會擠掉其一，除非調到 6。

## 11. 使用 API

### `POST /chat`

```bash
curl -X POST http://localhost:8079/chat -H "Content-Type: application/json" \
  -d '{"prompt": "HGW20 load capacity", "language": "en"}'
```

```json
{
  "response": "### HGW20 Load Capacity …（Markdown，可含 ![](/static/HIWIN/…) 圖片）",
  "language": "en",
  "sources": [{"product_type": "linear_guideway", "page_number": 6, "file_name": "…", "language_code": "en", "rerank_score": 0.93}],
  "trace":   [{"tool": "db_get_available_product_tables", "args": {}, "result": "…"}, {"tool": "db_search_technical_manuals", "args": {"…"}, "result": "…"}],
  "metrics": {"latency_ms": 41230, "llm_calls": 5, "tool_calls": 3, "completeness_calls": 6, "total_tokens": 38112, "by_kind": {"agent": {"calls": 3, "ms": 21000}, "completeness_enum": {…}}, "completeness": [{"source": "[SOURCE: …]", "status": "found", "output": "…"}]}
}
```

- `language` 可省略（預設 `DEFAULT_LANGUAGE`）；值為 `en` / `jp` / `tc` / `sc`。
- `response` 為 GitHub 風格 Markdown；圖片連結對 `/static/HIWIN` 解析。
- `sources` 已修剪為答覆引用的頁碼；`trace` 顯示每次工具呼叫；`metrics` 顯示時間花在哪。

| 端點 | 用途 |
|---|---|
| `GET /` | 展示前端（`frontend/index.html`），附除錯面板。 |
| `GET /health` | 存活檢查 → `{"status": "ok"}`。 |
| `GET /static/HIWIN/...` | `IMAGE_STATIC_ROOT` 的圖示。 |

**串接自己的前端或系統：** 完整的 API 規格、curl / JavaScript / Python / PowerShell 範例、
CORS 與圖片網址處理，見 [docs/API.zh-Hant.md](docs/API.zh-Hant.md)。

### 批次測試與檢視

```bash
python run_prompts.py                          # 所有 examples/*.json -> results_<timestamp>.json
python run_prompts.py examples/tc_load_capacity.json --url http://host:8079/chat --timeout 600
python inspect_metadata.py --sample            # 資料表、列數、語言代碼、metadata_ 樣本
```

`run_prompts.py` 印出每請求依呼叫類型的延遲與整批摘要——調校前後比較就靠它。

### 對話記錄

每次 `/chat` 寫入 `hiwin_cs_db.chat_logs`（首次使用時建立）：`created_at`、`language`、
`prompt`、`response`、`sources`、`trace`（jsonb）、`latency_ms`、`llm_calls`、
`agent_iterations`、`tool_calls`、`prompt_tokens`、`completion_tokens`、`total_tokens`、
`generations`（每次呼叫的計時）。盡力而為：資料庫故障只印出並忽略。以
`CHAT_LOG_ENABLED=false` 關閉。

### 展示前端與除錯面板

`frontend/index.html` 於 `/` 提供：輸入提示、選語言、看渲染後的答覆與來源，開啟
**Debug view** 則多了指標列、含參數與結果的工具呼叫時間軸、逐段的完整性檢查
（`✓ found` / `NONE` 附原因 / `⚠ error`）與原始 JSON。Markdown 以 CDN 的 `marked` 渲染
（離線請自行放置）；不渲染 LaTeX。

## 12. 資料匯入管線

`pipeline/` 把 PDF 變成資料庫：

```
PDF ─▶ Pass 1 版面偵測（ONNX，CPU）─▶ Pass 2 頁面 → Markdown（VLM）─▶ Pass 2b 圖示說明
    ─▶ Pass 3 表格 → Markdown（VLM）─▶ Pass 3b 表格摘要 ─▶ Pass 4 驗證／合併
    ─▶ Phase 5 整理 Final_Output ─▶ Phase 7 向量化＋寫入（逐頁）＋複製圖示到 IMAGE_TARGET_ROOT
```

每份文件、每個 pass 都有檢查點（`<ROOT_PATH>/checkpoint.json`），中斷可續跑；逾時的 pass
會在最後重試一次。

### `ROOT_PATH` 配置

```
<ROOT_PATH>/
├── PP-DocLayout-PlusL.onnx   # 你提供（檔名固定，直接放這裡）
├── PDFs/                     # 你提供：<product>/<子資料夾…>/<file>.pdf
├── PDF_Config.yaml           # 處理哪些 PDF／頁面（首次執行自動產生，供檢視）
├── checkpoint.json           # 進度（自動）
├── Process_Files/            # 各 pass 的中間輸出（自動）——逐頁 Markdown 在此
└── Final_Output/             # <product>/<sub_folder>/<lang>/document.md + Figures/ + Tables/（自動）
```

`<product>` 資料夾名稱成為資料表 `data_<product>`（小寫、非英數字元 → `_`）。

### `PDF_Config.yaml`

不存在時，首次執行會掃描 `PDFs/`，為每個 PDF 寫一筆（包含所有頁、語言由檔名猜測），然後
**停下**讓你檢視。格式：

```yaml
Ballscrew:                          # 產品 -> PDFs/Ballscrew/，資料表 data_ballscrew
  Ballscrew-(C).pdf:                # 葉節點 = PDF 檔名
    language: tc                    # en | jp | tc | sc（= 資料庫 language_code）
    pages_to_exclude: [0, 1, 2]     # 要略過的頁（0 起算）
  Ballscrew-(E).pdf:
    language: en
    pages_to_include: [5, 6, 7]     # 非空時只處理這些頁
```

產品與葉節點之間可有任意層的分組資料夾；會合併為 `sub_folder`。自動偵測把所有中文檔名
對應到 `tc`——簡體版請手動改為 `sc`（現行設定即如此）。

### 執行

```bash
# 先啟用 venv
cd pipeline
python gui.py                 # Tk GUI：設定畫面＋逐 PDF 即時監控（EN / 繁中切換）
python tui.py                 # 終端機版（rich）
python Pipeline.py            # 無介面，使用 settings.json
python -m ingestion.Ingest                # 僅（重新）向量化＋寫入 Final_Output
python -m ingestion.Ingest --db-only      # …但不複製圖示
python -m ingestion.Ingest --assets-only  # 只把圖示／表格複製到 IMAGE_TARGET_ROOT（不碰資料庫）
python -m ingestion.Ingest --force        # 已標記完成的文件也重新匯入
```

`gui.py`、`tui.py`、`Pipeline.py` 與 `ingestion/Ingest.py` 可從任何工作目錄啟動；`-m` 形式
仍需 `cd pipeline`。設定畫面存回 `settings.json`；資料庫密碼欄位遮罩顯示。逐頁匯入
（`INGEST_BY_PAGE = true`，正式模式）從 `Process_Files/…/Pass_3b` 讀逐頁 Markdown，因此
重新匯入需要該資料夾，不只 `Final_Output`。

**管線模型與服務模型。** 管線使用與後端相同的路由。它的兩個模型不是 `load-on-startup`；
送出第一頁時路由才載入它們，而在預設 `--models-max 4` 下會擠掉最久未用的服務模型
（測試時先是向量化、再是 reranker；48 GB 卡峰值 48.3 GB，可以運作）。之後服務模型組不完整，
直到下一個 `/chat` 請求把需要的模型重新載回（第一題多花約 30 秒），因此請在離峰時段匯入，
或只有在 GPU 真的放得下六個模型時才在啟動腳本設 `MODELS_MAX=6`。

2026-09-16 已從全新 clone 端到端測試：一份 2 頁 PDF 含模型載入約 4 分鐘（Pass 1 3 秒、
Pass 2–4 在路由上、然後匯入）；列出現在 `data_controller_drive`，後端能據此回答。

### 模型檔

`pipeline/models.json` 列出 GGUF 檔名與 `model_dir`。GUI（**Download models…**）／TUI（`m`）
會檢查哪些已存在，填好 `repo_id` 後可自 Hugging Face 下載缺少的檔案。`doctor.py --pipeline`
驗證同一份清單。

## 13. 維護

[docs/MAINTENANCE.zh-Hant.md](docs/MAINTENANCE.zh-Hant.md) 是維運手冊，涵蓋：

- **可自由更改的 vs 必須保持一致的**（三個設定檔、向量化模型、`EMBED_DIM`）。
- **調整答覆**：每個提示詞在哪裡（系統提示、語言 skill、完整性與視覺提示）、檢索旋鈕、
  如何用 `run_prompts.py` 與對話記錄量測、過去改了什麼與為什麼。
- **新增、更新或移除知識庫文件**，含手工修復頁面的警告（[INGESTION_DEFECTS.md](docs/INGESTION_DEFECTS.md)）。
- **還原圖示**、備份、更換資料庫密碼、更新模型或 llama.cpp，以及例行檢查清單。

## 14. 疑難排解

先執行 `python doctor.py --all`；然後：

| 症狀 | 可能原因／處理 |
|---|---|
| `[FAIL] router not reachable` / `:11400` 連線被拒 | 路由未執行或 `INFERENCE_HOST` 錯誤。`start.bat` / `start.sh`，再 `curl http://localhost:11400/v1/models`。 |
| 路由啟動即結束 | `config.ini` 某路徑錯誤（`doctor.py` 逐一列出），或 VRAM 不足——降低 `n-gpu-layers` / `ctx-size`，或固定單一 GPU（`CUDA_VISIBLE_DEVICES`）。 |
| `password authentication failed` | `DB_PASSWORD` 錯，**或 `DB_PORT` 指到另一個 Postgres 叢集**（裝了多個版本）。 |
| `fe_sendauth: no password supplied` | `DB_PASSWORD` 空白；`.env` 不存在或未載入（`pip install -r requirements.txt`）。 |
| `extension "vector" is not available` | *該版本* Postgres 未安裝 pgvector（[§9](#9-postgresql-與-pgvector)）。 |
| `python setup_db.py` → `permission denied to create extension` | 以超級使用者執行：`--admin-user postgres --admin-password -`。 |
| 還原時 `unsupported version in file header` | 目標 Postgres 比所用 `pg_dump` 舊；改用純 SQL dump（[§9](#備份與搬移資料庫)）。 |
| `/usr/bin/env: 'bash\r': No such file`（Linux） | CRLF 腳本：`sed -i 's/\r$//' start.sh run.sh`。 |
| `ImportError: libGL.so.1`（Linux） | 舊 venv 裝的是 `opencv-python`；`pip install -r requirements.txt`（現為 headless）或 `apt install libgl1`。 |
| `python3 -m venv` 失敗（Debian/Ubuntu） | `sudo apt install python3.12-venv`。 |
| 代理反覆呼叫 `db_search_*` 後回傳亂答 | 每次檢索都出錯或無結果（通常是資料庫認證）。看除錯面板／`trace`。 |
| `No results for language …` | 送出的 `language_code` 與資料庫不符（`inspect_metadata.py`）。 |
| `Reranking failed: HTTP 500 … increase the physical batch size` | 調高 reranker 區段的 `ubatch-size` / `batch-size`（已設 4096）或降低 `RERANK_DOC_MAX_CHARS`。 |
| 等很久後回傳空答覆 | 推理失控；`config.ini` 的 `reasoning-budget = 8192` 可防止——確認路由用的是*這份* preset。 |
| 相同問題答案不一致 | 向量化／reranker 的 `parallel` > 1 會產生不同向量；preset 已固定 `parallel = 1`。 |
| 延遲從約 1 分鐘暴增到 30 分鐘以上 | 模型被拆到多張 GPU，其一正忙：固定單一 GPU（`CUDA_VISIBLE_DEVICES=0` 或 `device = CUDA0`）。 |
| 答覆只列部分產品／漏列 | 保持 `COMPLETENESS_PASS_ENABLED=true`、`GRADING_ENABLED=false`；看除錯面板完整性區段是否有原因錯誤的 `NONE`。 |
| 回應非常慢 | 主因是完整性檢查。`COMPLETENESS_MODEL` 指向小型 aux 模型（預設）、降低 `RERANK_TOP_K`，或提高 aux 區段的 `parallel` 與 `COMPLETENESS_CONCURRENCY`。 |
| 圖片 404／沒有視覺分析 | `IMAGE_STATIC_ROOT` 未設或資料夾不存在（[MAINTENANCE → 還原圖示](docs/MAINTENANCE.zh-Hant.md#還原圖示)）。 |
| 獨立前端看不到圖片 | 非同源：兩者放在同一反向代理後，或改用絕對圖片 URL。 |
| 管線：`Final document not found`／明明存在卻說找不到（Windows） | 路徑超過 260 字元；程式使用 `\\?\` 長路徑——請確認執行的是本專案現行的管線，而非舊複本。 |
| 管線 GUI：`No module named tkinter` | `sudo apt install python3-tk`，或改用 `tui.py`。 |
| 管線在 `Successfully ingested` 之後以 `UnicodeEncodeError: 'cp950' codec can't encode…` 結束 | 舊版管線在傳統 Windows 主控台印 emoji；資料*已經*匯入。現行程式強制 UTF-8 輸出——請更新你執行的那份。 |
| `doctor.py`：剛跑完管線就出現 `EMBEDDING_MODEL … is unloaded` | 被管線模型擠掉了。下一個 `/chat` 會自動重新載入；或重啟路由。 |
| `doctor.py --wait-models` 說某模型 `stayed unloaded` | 同樣是被擠掉，或該區段缺 `load-on-startup = true`。無害：第一次使用時會載入。 |

## 15. 已知問題

- **各語言型錄版次不同。** 各語言匯入的版本不同，部分數值在語言間本來就有差。屬資料
  問題，非後端錯誤。
- **手工修復的頁面。** 滾珠螺桿型錄三頁直接在資料庫修正；管線會把缺陷帶回來
  （[INGESTION_DEFECTS.md](docs/INGESTION_DEFECTS.md)）。
- **目前機器沒有圖示**（§3）。
- 少數資料表有 **`kr` 與 NULL 語言列**，永遠不會被檢索。
- **展示前端的 LaTeX** 以純文字顯示。

## 16. 專案結構

```
HIWIN_Support_Agent/
├── main.py                 # FastAPI 應用：/chat、/health、/、/static/HIWIN
├── agent.py                # 工具呼叫迴圈＋完整性檢查
├── rag_tools.py            # 4 個檢索工具（pgvector → 重排序 → 視覺）
├── tool_schemas.py         # 函式呼叫 schema＋分派
├── inference.py            # chat / embeddings / rerank HTTP 封裝（＋逐呼叫計時）
├── db.py                   # Postgres 連線池＋SQL＋對話記錄寫入
├── prompts.py              # 系統提示 = System_prompt.md + skill
├── config.py               # 環境變數驅動的設定（載入 .env）
├── doctor.py               # 預檢／診斷（先跑這個）
├── setup_db.py             # 建立資料庫＋pgvector 擴充＋schema（可重複執行）
├── env_from_settings.py    # 同步共用值 settings.json -> .env（就地）
├── inspect_metadata.py     # 資料庫內容（資料表、列數、語言代碼）
├── run_prompts.py          # 批次測試，含延遲拆解
├── config.ini              # llama.cpp 路由 preset（改模型路徑）
├── templates/qwen.jinja    # config.ini 引用的對話範本
├── start.bat / start.sh    # 一鍵啟動路由＋後端
├── run.bat / run.sh        # 只啟動後端
├── .env                    # 後端設定（已版控；內部部署）
├── requirements.txt / requirements.lock.txt
├── prompts/                # System_prompt.md + skills/<lang>.md
├── frontend/index.html     # 附除錯面板的展示頁
├── examples/               # /chat 範例請求（回歸測試集）
├── docs/                   # ARCHITECTURE、MAINTENANCE、INGESTION_DEFECTS（en + 繁中）
├── reference/              # 原 open-WebUI filter 與 tool（僅供溯源）
├── AGENTS.md               # 給 AI 代理／新開發者的簡報
└── pipeline/               # PDF -> pgvector 匯入
    ├── Pipeline.py         #   編排器（pass 1-4、整理、匯入）
    ├── gui.py / tui.py     #   設定畫面＋即時監控（Tk / 終端機）
    ├── pdf_passes/         #   Pass_1（ONNX 版面）… Pass_4（驗證）——提示詞在此
    ├── ingestion/Ingest.py #   向量化＋寫入；--db-only / --assets-only / --force
    ├── core/               #   預設設定、設定 schema、i18n、檢查點、工具
    ├── settings.json       #   已版控的管線設定（本機路徑）
    ├── models.json         #   下載器用的 GGUF 清單
    └── PDF_Config.example.yaml
```

## 17. 作者與第三方聲明

由 **ACPIE_Lab** 開發與維護，供 HIWIN 內部使用。第三方元件與模型列於
[THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)；對外散布前請注意 **PyMuPDF**
（AGPL-3.0，僅管線使用）與 **Gemma** 模型條款。
