"""
progress.py — Shared progress-reporting infrastructure for the HIWIN pipeline.

Each pass function accepts an optional `progress_callback` (or `callback`).
When None  → falls back to tqdm (CLI behaviour, unchanged).
When set   → posts structured event dicts to the GUI queue instead.

Event types emitted by this module:
  step_start    {'type', 'pass', 'step', 'total'}
  step_progress {'type', 'pass', 'current', 'total'}

Pipeline.py is responsible for the higher-level icon events:
  icon_start  {'type', 'pass'}
  icon_done   {'type', 'pass'}
  icon_skip   {'type', 'pass'}
"""

import asyncio
from tqdm import tqdm as _tqdm
from tqdm.asyncio import tqdm as _atqdm

BAR_FMT = "{l_bar}{bar}| {elapsed} {remaining:} [{rate_fmt}{postfix}]"


def make_log_fn(callback, level: str = "warning"):
    """Return a log function that routes to tqdm.write or the progress callback."""
    if callback is None:
        return _tqdm.write
    return lambda msg: callback({"type": "log", "level": level, "message": msg})


class ProgressTracker:
    """
    Drop-in replacement for a tqdm context manager that can also send progress
    events to a GUI callback.  Falls back to tqdm when callback is None.

    Usage:
        with ProgressTracker(total=n, desc="Detecting Layouts",
                             pass_name="pass_1", callback=cb, unit="page") as p:
            p.update(1)
            p.log("some warning")
    """

    def __init__(self, total: int, desc: str, pass_name: str = "",
                 callback=None, unit: str = "it",
                 mininterval: float = 0.5, bar_format: str | None = None):
        self._cb        = callback
        self._pass_name = pass_name
        self._total     = total
        self._current   = 0

        if callback is None:
            kw = dict(total=total, desc=desc, unit=unit, mininterval=mininterval,
                      bar_format=bar_format or BAR_FMT)
            self._bar = _tqdm(**kw)
        else:
            self._bar = None
            callback({"type": "step_start", "pass": pass_name,
                      "step": desc, "total": total})

    def update(self, n: int = 1) -> None:
        self._current = min(self._current + n, self._total)
        if self._bar is not None:
            self._bar.update(n)
        elif self._cb:
            self._cb({"type": "step_progress", "pass": self._pass_name,
                      "current": self._current, "total": self._total})

    def log(self, msg: str, level: str = "warning") -> None:
        if self._bar is not None:
            _tqdm.write(msg)
        elif self._cb:
            self._cb({"type": "log", "level": level, "message": msg})

    def __enter__(self):
        return self

    def __exit__(self, *_):
        if self._bar is not None:
            self._bar.close()


def iter_with_progress(iterable, desc: str, pass_name: str = "",
                       callback=None, unit: str = "it",
                       mininterval: float = 0.3):
    """
    Yield each item from *iterable* while reporting progress via tqdm or callback.

    Usage:
        for item in iter_with_progress(items, "Injecting Tables",
                                       pass_name="pass_3", callback=cb):
            process(item)
    """
    items = list(iterable)
    with ProgressTracker(len(items), desc, pass_name=pass_name,
                         callback=callback, unit=unit,
                         mininterval=mininterval) as p:
        for item in items:
            yield item
            p.update(1)


async def async_gather_with_progress(coros: list, total: int,
                                     desc: str, pass_name: str = "",
                                     callback=None, unit: str = "it",
                                     mininterval: float = 0.5):
    """
    Await all coroutines while reporting progress via tqdm or callback.
    Preserves result order (same contract as asyncio.gather).
    """
    if callback is None:
        return await _atqdm.gather(
            *coros, desc=desc, unit=unit, mininterval=mininterval,
            bar_format=BAR_FMT,
        )

    # GUI mode — manual gather with per-completion progress events
    callback({"type": "step_start", "pass": pass_name, "step": desc, "total": total})

    results = [None] * len(coros)
    count   = [0]
    lock    = asyncio.Lock()

    async def _wrap(i, coro):
        result = await coro
        async with lock:
            count[0] += 1
            callback({"type": "step_progress", "pass": pass_name,
                      "current": count[0], "total": total})
        results[i] = result
        return result

    await asyncio.gather(*[_wrap(i, c) for i, c in enumerate(coros)])
    return results
