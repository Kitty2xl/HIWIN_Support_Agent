"""Thin wrappers around the local OpenAI-compatible inference server (:11400).

Lifted from the open-WebUI tool's `_call_chat_completion` / `_get_embedding` /
`_rerank_passages`, with the `Valves`/event-emitter coupling removed.
"""

import requests

import config


def _raise_with_body(resp):
    """Like resp.raise_for_status(), but include the server's response body —
    llama.cpp puts the real error (e.g. 'unsupported', context overflow) there."""
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code} from {resp.url}: {resp.text}")


def chat(messages, tools=None, model=None, timeout=None):
    """POST /v1/chat/completions and return the full JSON response.

    When `tools` is supplied the model may return `tool_calls` in the message —
    this is what drives the agentic STATE machine.
    """
    model = model or config.LANGUAGE_MODEL
    timeout = timeout or config.CHAT_TIMEOUT
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "temperature": config.TEMPERATURE,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    resp = requests.post(
        f"{config.INFERENCE_BASE_URL}/chat/completions", json=payload, timeout=timeout
    )
    resp.raise_for_status()
    return resp.json()


def chat_content(messages, model=None, timeout=None) -> str:
    """Convenience: return just the assistant text. Used for the vision pass."""
    data = chat(messages, model=model, timeout=timeout)
    return data["choices"][0]["message"]["content"]


def embed(text, model=None, timeout=None):
    """POST /v1/embeddings and return the embedding vector."""
    model = model or config.EMBEDDING_MODEL
    timeout = timeout or config.EMBED_TIMEOUT
    resp = requests.post(
        f"{config.INFERENCE_BASE_URL}/embeddings",
        json={"model": model, "input": text},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()["data"][0]["embedding"]


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
    resp = requests.post(config.RERANK_URL, json=payload, timeout=timeout)
    _raise_with_body(resp)
    return resp.json()
