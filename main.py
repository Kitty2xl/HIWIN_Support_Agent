"""HIWIN Support Agent Backend.

A single POST /chat endpoint: takes a prompt + language, runs the agentic
retrieval flow against the local inference server + Postgres, and returns the
answer. Also serves an example demo frontend at / and the HIWIN images at
/static/HIWIN.

Run:  uvicorn main:app --host 0.0.0.0 --port 8079
"""

import asyncio
import os
import time
from typing import List, Optional

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import agent
import config
import db
import prompts

app = FastAPI(title="HIWIN Support Agent Backend")

# Serve the HIWIN image assets so the markdown `![](/static/HIWIN/...)` links in
# responses (and the cert `web_path`s in sources) actually resolve — this is the
# job open-WebUI's static server used to do.
if config.IMAGE_STATIC_ROOT and os.path.isdir(config.IMAGE_STATIC_ROOT):
    app.mount(
        "/static/HIWIN",
        StaticFiles(directory=config.IMAGE_STATIC_ROOT),
        name="hiwin-static",
    )
else:
    print(
        f"WARNING: IMAGE_STATIC_ROOT not set/found ({config.IMAGE_STATIC_ROOT!r}); "
        "images will not be served. Set IMAGE_STATIC_ROOT in .env to the HIWIN static folder."
    )


class ChatRequest(BaseModel):
    prompt: str
    language: Optional[str] = None


class ChatResponse(BaseModel):
    response: str
    language: str
    sources: List[dict] = []
    trace: List[dict] = []


def build_user_message(prompt: str, language: str) -> str:
    """Port of the open-WebUI language_extract filter inlet: inject the language
    code into the user's prompt so the model's STATE 0 routing sees it."""
    return f"[Language Code: {language}]\n{prompt}"


# The bundled HTML page is an EXAMPLE/DEMO frontend; the service is API-first.
_FRONTEND_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "frontend", "index.html"
)


@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the example demo frontend from the same origin as /chat and
    /static/HIWIN, so its markdown image links resolve with no extra config."""
    with open(_FRONTEND_PATH, "r", encoding="utf-8") as f:
        return f.read()


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    language = req.language or config.DEFAULT_LANGUAGE
    system = prompts.build_system(language)
    user_msg = build_user_message(req.prompt, language)

    trace: List[dict] = []
    sources: List[dict] = []
    metrics: dict = {}
    t0 = time.perf_counter()
    answer = await agent.run(system, user_msg, trace=trace, sources=sources, metrics=metrics)
    latency_ms = round((time.perf_counter() - t0) * 1000, 1)

    # Persist the exchange (best-effort; never blocks or breaks the response).
    if config.CHAT_LOG_ENABLED:
        try:
            await asyncio.to_thread(
                db.log_chat,
                prompt=req.prompt, language=language, response=answer,
                sources=sources, trace=trace, metrics=metrics, latency_ms=latency_ms,
            )
        except Exception as e:  # noqa: BLE001
            print(f"WARNING: chat log dispatch failed: {e}")

    return ChatResponse(response=answer, language=language, sources=sources, trace=trace)
