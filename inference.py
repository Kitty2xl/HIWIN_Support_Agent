"""Thin wrappers around the local OpenAI-compatible inference server (:11400).

Lifted from the open-WebUI tool's `_call_chat_completion` / `_get_embedding` /
`_rerank_passages`, with the `Valves`/event-emitter coupling removed.

Every call is timed and recorded (see `start_recording`), so a request's metrics
cover the auxiliary passes — vision, completeness, embed, rerank — and not just
the agent loop. Without that, most of a slow request is invisible.
"""

import contextvars
import time

import requests

import config


# --- Per-request call log ---------------------------------------------------
# `start_recording` installs a fresh list for the current request. ContextVars
# propagate into tasks spawned by asyncio.gather AND into asyncio.to_thread
# workers, so anything running inside that request appends to the same list
# without the call site having to thread a metrics object down through the tools
# (the vision pass, for one, is three layers below the agent).
_generations: contextvars.ContextVar = contextvars.ContextVar(
    "inference_generations", default=None
)

# Call kinds that are not text generations — counted and timed, but they carry no
# token usage, so metrics that mean "LLM calls" exclude them.
NON_GENERATION_KINDS = ("embed", "rerank")


def start_recording() -> list:
    """Begin a fresh per-request call log and return it (see `_record`)."""
    log: list = []
    _generations.set(log)
    return log


def _record(kind, model, t0, data=None, error=None):
    """Append one call's timing/usage to the current request's log, if any.

    Failures are recorded too: a call that timed out or was rejected is exactly
    what you want to see when working out where a slow request went.
    """
    log = _generations.get()
    if log is None:
        return
    usage = (data or {}).get("usage") or {}
    entry = {
        "kind": kind,
        "model": model,
        "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
    }
    if error is not None:
        entry["error"] = str(error)[:300]
    log.append(entry)


def _raise_with_body(resp):
    """Like resp.raise_for_status(), but include the server's response body —
    llama.cpp puts the real error (e.g. 'unsupported', context overflow) there."""
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code} from {resp.url}: {resp.text}")


def chat(messages, tools=None, model=None, timeout=None, base_url=None, kind="agent"):
    """POST /v1/chat/completions and return the full JSON response.

    When `tools` is supplied the model may return `tool_calls` in the message —
    this is what drives the agentic STATE machine. `base_url` targets a specific
    inference endpoint (only needed if a model is served separately from the main
    router); it defaults to the main server. `kind` labels the call in the
    per-request log so latency can be attributed to the agent loop vs. the
    vision / completeness passes.
    """
    model = model or config.LANGUAGE_MODEL
    timeout = timeout or config.CHAT_TIMEOUT
    base_url = base_url or config.INFERENCE_BASE_URL
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "temperature": config.TEMPERATURE,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    t0 = time.perf_counter()
    try:
        resp = requests.post(
            f"{base_url}/chat/completions", json=payload, timeout=timeout
        )
        _raise_with_body(resp)
        data = resp.json()
    except Exception as e:
        _record(kind, model, t0, error=e)
        raise
    _record(kind, model, t0, data)
    return data


def chat_content(messages, model=None, timeout=None, base_url=None, kind="agent") -> str:
    """Convenience: return just the assistant text. Used for the vision and
    completeness passes (the latter may target a smaller model via base_url)."""
    data = chat(messages, model=model, timeout=timeout, base_url=base_url, kind=kind)
    return data["choices"][0]["message"]["content"]


def embed(text, model=None, timeout=None):
    """POST /v1/embeddings and return the embedding vector."""
    model = model or config.EMBEDDING_MODEL
    timeout = timeout or config.EMBED_TIMEOUT
    t0 = time.perf_counter()
    try:
        resp = requests.post(
            f"{config.INFERENCE_BASE_URL}/embeddings",
            json={"model": model, "input": text},
            timeout=timeout,
        )
        _raise_with_body(resp)
        data = resp.json()
    except Exception as e:
        _record("embed", model, t0, error=e)
        raise
    _record("embed", model, t0, data)
    return data["data"][0]["embedding"]


def rerank(query, documents, top_n, model=None, timeout=None):
    """POST /v1/rerank and return the parsed JSON (expects a `results` list)."""
    model = model or config.RERANKER_MODEL
    timeout = timeout or config.EMBED_TIMEOUT
    payload = {
        "model": model,
        "query": query,
        "documents": documents,
        "top_n": top_n,
    }
    t0 = time.perf_counter()
    try:
        resp = requests.post(config.RERANK_URL, json=payload, timeout=timeout)
        _raise_with_body(resp)
        data = resp.json()
    except Exception as e:
        _record("rerank", model, t0, error=e)
        raise
    _record("rerank", model, t0, data)
    return data
