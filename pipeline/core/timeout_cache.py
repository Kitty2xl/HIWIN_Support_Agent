"""
timeout_cache.py — deferred-retry registry for model timeouts.

When an LLM call in a pass times out (even after that pass's own per-request
retries), the item is not abandoned: the pass records "(document, pass) timed
out" here.  After every pass for every document has finished, Pipeline.py runs a
single retry phase that re-runs the affected passes (from the earliest timed-out
pass downward, since later passes consume earlier output) — all still within the
same pipeline run.

The registry is shared across the Phase A worker threads and the Phase B event
loop, so all mutating methods are guarded by a lock.  It is also persisted to
disk so the information survives a crash mid-run.
"""

import os
import json
import asyncio
import threading


# Canonical order of the LLM passes.  Used to decide the earliest pass that needs
# re-running for a document (everything from there on is cleared and redone).
LLM_PASS_ORDER = ["pass_2", "pass_2b", "pass_3", "pass_3b", "pass_4"]


def is_timeout_error(exc: BaseException | None) -> bool:
    """
    Best-effort check for whether *exc* (or anything in its cause/context chain)
    represents a timeout — covers asyncio.TimeoutError, the builtin TimeoutError,
    openai.APITimeoutError, httpx.*Timeout, and any error whose type name or
    message mentions a timeout.  Name/string based so it needs no imports of the
    networking libraries.
    """
    seen: set[int] = set()
    e = exc
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        if isinstance(e, (asyncio.TimeoutError, TimeoutError)):
            return True
        name = type(e).__name__.lower()
        if "timeout" in name or "timedout" in name:
            return True
        msg = str(e).lower()
        if "timed out" in msg or "timeout" in msg:
            return True
        e = e.__cause__ or e.__context__
    return False


class TimeoutRegistry:
    """Thread-safe record of which (document, pass) pairs hit a model timeout."""

    def __init__(self, path: str | None = None):
        self._lock = threading.Lock()
        self._data: dict[str, set[str]] = {}   # checkpoint_filename -> {pass_name}
        self.path = path
        self._load()

    # ── persistence ──────────────────────────────────────────────────────────
    def _load(self):
        if self.path and os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                self._data = {k: set(v) for k, v in raw.items()}
            except (json.JSONDecodeError, IOError, TypeError):
                self._data = {}

    def _save(self):
        """Persist current state.  Caller must hold self._lock."""
        if not self.path:
            return
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({k: sorted(v) for k, v in self._data.items()}, f, indent=2)
        except IOError:
            pass

    # ── mutation ─────────────────────────────────────────────────────────────
    def record(self, checkpoint_filename: str, pass_name: str):
        """Note that *pass_name* timed out at least once for this document."""
        if not checkpoint_filename or not pass_name:
            return
        with self._lock:
            self._data.setdefault(checkpoint_filename, set()).add(pass_name)
            self._save()

    def clear_doc(self, checkpoint_filename: str):
        """Forget all recorded timeouts for one document."""
        with self._lock:
            self._data.pop(checkpoint_filename, None)
            self._save()

    def clear_all(self):
        with self._lock:
            self._data = {}
            self._save()

    # ── queries ──────────────────────────────────────────────────────────────
    def any(self) -> bool:
        with self._lock:
            return any(self._data.values())

    def docs(self) -> dict[str, set[str]]:
        """Snapshot copy of {checkpoint_filename: {pass_names}} with timeouts."""
        with self._lock:
            return {k: set(v) for k, v in self._data.items() if v}

    def earliest_pass(self, checkpoint_filename: str) -> str | None:
        """The earliest LLM pass (in pipeline order) that timed out for a doc."""
        passes = self._data.get(checkpoint_filename, set())
        return next((p for p in LLM_PASS_ORDER if p in passes), None)


def make_timeout_noter(registry: "TimeoutRegistry | None",
                       checkpoint_filename: str | None, pass_name: str):
    """
    Build a zero-arg callable that records a timeout for (document, pass), or
    None when there is nothing to record to.  Pass functions call the returned
    noter from their final exception handler when is_timeout_error() is true.
    """
    if registry is None or not checkpoint_filename:
        return None

    def _note():
        registry.record(checkpoint_filename, pass_name)

    return _note
