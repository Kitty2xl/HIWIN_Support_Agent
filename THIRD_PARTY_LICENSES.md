# Third-Party Licenses

HIWIN_Support_Agent itself is proprietary / internal-use-only (see [LICENSE](LICENSE)).
It **depends on** the third-party components below but does **not redistribute**
them: the Python packages are installed from PyPI via `pip`, and the inference
server, database, and model weights are installed/run separately. This file is for
attribution and compliance.

> Licenses are recorded per the components' published terms. **Verify each against
> the exact version you install** — and especially confirm the model licenses on
> each model's own page, since the GGUF files you use are your own choice.

## Python packages — backend (`requirements.txt`)

| Package | License |
|---|---|
| fastapi | MIT |
| uvicorn[standard] | BSD-3-Clause |
| psycopg2-binary | LGPL-3.0-or-later (with OpenSSL/libpq exceptions) — **copyleft** |
| requests | Apache-2.0 |
| python-dotenv | BSD-3-Clause |

## Python packages — ingestion pipeline (`requirements.txt`)

| Package | License |
|---|---|
| numpy | BSD-3-Clause (bundles 0BSD / MIT / Zlib / CC0-1.0) |
| opencv-python-headless | Apache-2.0 (OpenCV); MIT (packaging) |
| PyMuPDF | **AGPL-3.0-or-later** OR Artifex commercial license — **strong copyleft** |
| onnxruntime | MIT |
| openai | Apache-2.0 |
| httpx | BSD-3-Clause |
| aiofiles | Apache-2.0 |
| tqdm | MPL-2.0 AND MIT |
| PyYAML | MIT |
| rich | MIT |
| huggingface_hub | Apache-2.0 |
| llama-index-core | MIT |
| llama-index-embeddings-openai-like | MIT |
| llama-index-vector-stores-postgres | MIT |

(Installing these pulls transitive dependencies — e.g. SQLAlchemy [MIT], pgvector
Python client [PostgreSQL License], pydantic [MIT], tiktoken [MIT], starlette
[BSD-3-Clause]. Run `pip-licenses` for the full resolved tree.)

## External tools & services (run separately, not bundled in this repo)

| Component | License |
|---|---|
| CPython (Python 3.12.7) | PSF License Agreement |
| PostgreSQL | PostgreSQL License (permissive, BSD-style) |
| pgvector (Postgres extension) | PostgreSQL License |
| llama.cpp (`llama-server`) | MIT |

## Model weights (provided / downloaded separately — verify on each model's page)

| Model (role) | Typical license | Notes |
|---|---|---|
| PP-DocLayout_plus-L — Pass 1 layout ONNX (PaddlePaddle) | Apache-2.0 | https://huggingface.co/PaddlePaddle/PP-DocLayout_plus-L |
| Qwen3-Embedding-4B — embeddings | Apache-2.0 | Qwen3 series |
| Qwen3-Reranker-4B — reranker | Apache-2.0 | Qwen3 series |
| Qwen3.6-35B-A3B chat/vision (`Support_Agent_Qwen3.6`, `RAG_Pipeline_Pass34`, + `mmproj`) | Apache-2.0 | Alibaba, released 2026-04-15; multimodal MoE w/ MTP — https://huggingface.co/Qwen/Qwen3.6-35B-A3B |
| Gemma-based text model (`RAG_Pipeline_Pass5Ingest`) | **Gemma Terms of Use** (Google) — **not OSI; use restrictions apply** | confirm the exact repo |

The GGUF quantizations you download (per `pipeline/models.json`) inherit the base
model's license; some quantizers add their own notice — check the Hugging Face
repo you pull each file from.

## Notable obligations to be aware of

- **PyMuPDF (AGPL-3.0).** Strong copyleft with a network-use clause (AGPL §13).
  Internal-only use is generally fine, but if this service is ever conveyed or
  offered to users **outside your organization**, AGPL terms may apply — Artifex
  sells a commercial license to avoid that. (PyMuPDF is used only in the *pipeline*
  for PDF rendering, not in the serving backend.)
- **Gemma model (Gemma Terms of Use).** Google's license is **not** a standard
  open-source license and carries a Prohibited Use Policy; review it before any
  external distribution or commercial offering.
- **psycopg2 (LGPL-3.0).** Fine when used as an unmodified library (dynamic use);
  obligations arise mainly if you modify and distribute psycopg2 itself.
- **tqdm (MPL-2.0).** File-level copyleft — only matters if you modify tqdm's own
  source files and distribute them.
- Permissive licenses (MIT / BSD / Apache-2.0 / PSF / PostgreSQL) require
  preserving the original copyright/permission notices **if you redistribute**
  those components. This repository does not redistribute them.
