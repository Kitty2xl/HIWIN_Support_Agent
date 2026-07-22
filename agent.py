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


async def _enumerate_passage(question: str, passage: str) -> str:
    """Re-read ONE passage in isolation and exhaustively list every entry in it
    that matches the user's request. The narrow scope is the point: a focused
    one-table task doesn't get summarized down the way the broad synthesis does.
    Returns 'NONE' when the passage has nothing matching (or on failure)."""
    messages = [{
        "role": "user",
        "content": (
            "A user made the request below. From the SINGLE source passage that "
            "follows, list EVERY entry (e.g. every model / part-number row) that "
            "satisfies the request. Be exhaustive: do not omit, merge, or collapse "
            "any matching row, including near-duplicates that differ only in a spec "
            "value. Keep each entry's identifying spec values. Cite each entry with "
            "the page from the passage's [SOURCE: …] tag. If nothing in the passage "
            "matches the request, reply with exactly: NONE.\n\n"
            f"User request:\n{question}\n\n"
            f"Source passage:\n{passage}"
        ),
    }]
    try:
        out = await asyncio.to_thread(
            inference.chat_content, messages,
            config.LANGUAGE_MODEL, config.COMPLETENESS_TIMEOUT,
        )
        return (out or "NONE").strip()
    except Exception as e:
        print(f"Completeness enumeration failed: {e}")
        return "NONE"


async def _completeness_pass(question: str, draft: str, passages: list) -> tuple[str, int]:
    """Fight under-enumeration: extract every matching entry from each passage
    (focused, concurrent), then rewrite the draft to include them all.

    Returns (answer, llm_call_count). Falls back to the draft on any failure."""
    seen, uniq = set(), []
    for p in passages:                       # same page can be retrieved twice
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    if not uniq:
        return draft, 0

    enums = await asyncio.gather(*(_enumerate_passage(question, p) for p in uniq))
    calls = len(uniq)
    findings = [e for e in enums if e and e.upper() != "NONE"]
    if not findings:
        return draft, calls

    messages = [{
        "role": "user",
        "content": (
            "Below is a DRAFT answer, then exhaustive entry lists extracted "
            "individually from each source table. Rewrite the draft so that EVERY "
            "entry from the extracted lists appears in the final answer. Preserve "
            "the draft's language, structure, grouping, formatting and citations. "
            "Do NOT drop, merge, or summarize away any extracted entry, and do NOT "
            "invent entries absent from both the draft and the extracted lists.\n\n"
            f"=== USER REQUEST ===\n{question}\n\n"
            f"=== DRAFT ANSWER ===\n{draft}\n\n"
            "=== EXHAUSTIVE EXTRACTED ENTRIES ===\n" + "\n\n".join(findings)
        ),
    }]
    try:
        merged = await asyncio.to_thread(
            inference.chat_content, messages, config.LANGUAGE_MODEL, config.CHAT_TIMEOUT
        )
        return (merged or draft), calls + 1
    except Exception as e:
        print(f"Completeness reconciliation failed: {e}")
        return draft, calls + 1


async def run(system: str, user_msg: str, trace: list = None,
              sources: list = None, metrics: dict = None) -> str:
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_msg},
    ]

    generations: list = []   # one entry per LLM generation call (timing + tokens)
    iterations = 0
    tool_calls_count = 0
    completeness_calls = 0
    retrieved_passages: list = []   # individual source passages, for the completeness pass

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
            "completeness_calls": completeness_calls,
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
            answer = msg.get("content") or ""
            # Focused per-table enumeration pass to catch rows the broad answer
            # summarized away (see config.COMPLETENESS_PASS_ENABLED).
            if config.COMPLETENESS_PASS_ENABLED and retrieved_passages and answer.strip():
                answer, ncalls = await _completeness_pass(
                    user_msg, answer, retrieved_passages
                )
                completeness_calls += ncalls
            _finalize()
            return answer

        for tc in tool_calls:
            tool_calls_count += 1
            name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except Exception:
                args = {}
            text, srcs = await _run_tool(name, args)

            # Keep the individual source passages (retrieval tools return srcs)
            # for the post-answer completeness pass.
            if srcs:
                retrieved_passages.extend(
                    p for p in text.split("\n---\n") if p.strip().startswith("[SOURCE:")
                )

            # Accumulate citations across all tool calls, de-duplicated on
            # identity EXCLUDING rerank_score (the same chunk can come back from
            # two searches with different scores — keep one, at its best score).
            if sources is not None:
                for s in srcs:
                    ident = {k: v for k, v in s.items() if k != "rerank_score"}
                    match = next(
                        (x for x in sources
                         if {k: v for k, v in x.items() if k != "rerank_score"} == ident),
                        None,
                    )
                    if match is None:
                        sources.append(s)
                    elif (s.get("rerank_score") or 0) > (match.get("rerank_score") or 0):
                        match["rerank_score"] = s["rerank_score"]

            if trace is not None:
                cap = config.TRACE_RESULT_MAX_CHARS
                preview = (
                    text
                    if (cap <= 0 or len(text) <= cap)
                    else text[:cap] + f"... [truncated, {len(text)} chars total]"
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
