"""The tool-calling agent loop.

This is the standalone replacement for open-WebUI's native function calling: it
feeds the system prompt + tool schemas to the model, executes whatever tools the
model decides to call (driving the System_prompt.md STATE machine), and returns
the final assistant message.
"""

import asyncio
import json
import time

import config
import inference
from tool_schemas import TOOL_SCHEMAS, DISPATCH


def _assistant_msg(msg: dict) -> dict:
    """Normalize the server's assistant message for appending back to history."""
    out = {"role": "assistant", "content": msg.get("content")}
    if msg.get("tool_calls"):
        out["tool_calls"] = msg["tool_calls"]
    return out


async def _run_tool(name: str, args: dict):
    """Run a tool and return (text_for_llm, sources_list).

    Retrieval tools return a {"text", "sources"} dict; the others return a plain
    string (no sources)."""
    fn = DISPATCH.get(name)
    if fn is None:
        return f"Error: unknown tool '{name}'.", []
    try:
        result = await fn(**args)
    except TypeError as e:
        return f"Error calling {name} (bad arguments): {e}", []
    except Exception as e:
        return f"Error in {name}: {e}", []
    if isinstance(result, dict):
        return result.get("text", ""), result.get("sources") or []
    return (result if isinstance(result, str) else str(result)), []


async def run(system: str, user_msg: str, trace: list = None,
              sources: list = None, metrics: dict = None) -> str:
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_msg},
    ]

    generations: list = []   # one entry per LLM generation call (timing + tokens)
    iterations = 0
    tool_calls_count = 0

    def _record(data: dict, t0: float):
        usage = data.get("usage") or {}
        generations.append({
            "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
        })

    def _finalize():
        if metrics is None:
            return
        p = sum(g["prompt_tokens"] or 0 for g in generations)
        c = sum(g["completion_tokens"] or 0 for g in generations)
        metrics.update({
            "generations": generations,
            "llm_calls": len(generations),
            "agent_iterations": iterations,
            "tool_calls": tool_calls_count,
            "prompt_tokens": p,
            "completion_tokens": c,
            "total_tokens": p + c,
        })

    for _ in range(config.MAX_AGENT_ITERS):
        iterations += 1
        t0 = time.perf_counter()
        data = await asyncio.to_thread(inference.chat, messages, TOOL_SCHEMAS)
        _record(data, t0)
        msg = data["choices"][0]["message"]
        messages.append(_assistant_msg(msg))

        tool_calls = msg.get("tool_calls")
        if not tool_calls:
            _finalize()
            return msg.get("content") or ""

        for tc in tool_calls:
            tool_calls_count += 1
            name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except Exception:
                args = {}
            text, srcs = await _run_tool(name, args)

            # Accumulate citations across all tool calls, de-duplicated.
            if sources is not None:
                for s in srcs:
                    if s not in sources:
                        sources.append(s)

            if trace is not None:
                preview = (
                    text
                    if len(text) <= 600
                    else text[:600] + f"... [truncated, {len(text)} chars total]"
                )
                trace.append({"tool": name, "args": args, "result": preview})
            messages.append(
                {"role": "tool", "tool_call_id": tc.get("id"), "content": text}
            )

    # Iteration cap hit — force a final answer with tools disabled.
    t0 = time.perf_counter()
    data = await asyncio.to_thread(inference.chat, messages)
    _record(data, t0)
    _finalize()
    return data["choices"][0]["message"].get("content") or ""
