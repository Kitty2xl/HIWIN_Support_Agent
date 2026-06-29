"""
tui.py — HIWIN Document Pipeline TUI (terminal interface)

A text-mode counterpart to gui.py for headless / terminal-only environments
(SSH sessions, servers with no display). It replicates the GUI's two screens:

  1. Settings — an interactive editor over the SAME `core.settings` SCHEMA the
     GUI uses, so the two can never drift. Edit any field, reset to defaults,
     then save & launch. Values persist to settings.json exactly as the GUI does.
  2. Monitor  — a live dashboard (built with `rich`) showing per-PDF × per-pass
     status, the phase progress bar, a running summary, and a log tail. It is
     driven by the very same `run_pipeline(progress_queue, stop_event)` event
     stream the GUI consumes, so behaviour matches the GUI exactly.

Run:  python tui.py
During a run, press Ctrl-C once to request a graceful stop at the next phase
boundary (same semantics as the GUI's Stop button).
"""

import os
import sys
import time
import queue
import threading
from collections import deque
from datetime import datetime

try:
    from rich.console import Console, Group
    from rich.table import Table
    from rich.live import Live
    from rich.panel import Panel
    from rich.text import Text
    from rich.prompt import Prompt, Confirm
    from rich.rule import Rule
except ImportError:
    sys.stderr.write(
        "The TUI needs the 'rich' package. Install dependencies with:\n"
        "    pip install -r requirements.txt\n"
        "(or: pip install rich)\n"
    )
    raise

from core.config import (
    PASS_KEYS, PASS_LABELS,
    I_PENDING, I_RUNNING, I_DONE, I_SKIP, I_ERROR,
)
from core import settings as settings_store
from core import i18n
from core import model_fetch

console = Console()

# Per-pass state → (glyph, rich style). Mirrors the GUI's _PASS_ICON/_PASS_COLOR.
_PASS_STYLE = {
    "pending": (I_PENDING, "grey50"),
    "running": (I_RUNNING, "yellow"),
    "done":    (I_DONE,    "green"),
    "skip":    (I_SKIP,    "grey50"),
    "error":   (I_ERROR,   "red"),
}
_STATUS_STYLE = {
    "idle":    ("● Idle",    "grey50"),
    "running": ("● Running", "yellow"),
    "done":    ("✓ Done",    "green"),
    "stopped": ("■ Stopped", "dark_orange"),
    "error":   ("✗ Error",   "red"),
}

_TERMINAL_EVENTS = {"pipeline_done", "pipeline_stopped", "pipeline_error"}


def _truncate(text: str, maxlen: int = 34) -> str:
    return text if len(text) <= maxlen else "…" + text[-(maxlen - 1):]


# ─────────────────────────────────────────────────────────────────────────────
# Settings screen
# ─────────────────────────────────────────────────────────────────────────────

def _flat_fields():
    """SCHEMA flattened to an ordered list of (section, key, label, kind)."""
    out = []
    for section, fields in settings_store.SCHEMA:
        for key, label, kind in fields:
            out.append((section, key, label, kind))
    return out


def _display_value(kind, value) -> Text:
    """Render a field's current value for the settings table (password masked)."""
    if kind == "password":
        return Text("••••••" if value else i18n.t("unset"),
                    style="green" if value else "red")
    if kind == "list":
        return Text(", ".join(map(str, value)) if value else i18n.t("empty"))
    if kind == "bool":
        return Text(i18n.t("yes") if value else i18n.t("no"))
    if value is None or value == "":
        return Text(i18n.t("unset"), style="red")
    return Text(str(value))


def _render_settings_table(values: dict):
    """A numbered table of every editable field and its current value."""
    tbl = Table(title=i18n.t("tui_settings_title"),
                title_style="bold", expand=True, header_style="bold grey62")
    tbl.add_column(i18n.t("tui_col_num"), justify="right", width=3, style="cyan")
    tbl.add_column(i18n.t("tui_col_section"), style="grey50", no_wrap=True)
    tbl.add_column(i18n.t("tui_col_setting"), no_wrap=True)
    tbl.add_column(i18n.t("tui_col_value"), overflow="fold")

    n = 0
    last_section = None
    last_tier = None
    for section, key, label, kind in _flat_fields():
        tier = settings_store.section_tier(section)
        if tier != last_tier:                       # Common / Advanced divider row
            label_key = "tier_common" if tier == "common" else "tier_advanced"
            tbl.add_row("", Text(i18n.t(label_key).upper(), style="bold cyan"), "", "")
            last_tier = tier
            last_section = None                      # reprint section label after divider
        n += 1
        sect = i18n.section(section) if section != last_section else ""
        last_section = section
        tbl.add_row(str(n), sect, i18n.field(key, label),
                    _display_value(kind, values.get(key)))
    return tbl


def _prompt_field(label, kind, current):
    """Prompt for one field, returning the coerced value. Blank keeps current."""
    if kind == "bool":
        return Confirm.ask(f"{label}", default=bool(current))

    if kind == "password":
        raw = Prompt.ask(f"{label} ({i18n.t('tui_keep_current')})",
                         password=True, default="")
        return current if raw == "" else raw

    if kind == "list":
        cur = ", ".join(map(str, current)) if current else ""
        raw = Prompt.ask(f"{label} ({i18n.t('tui_comma_sep')})", default=cur)
        return settings_store.coerce("list", raw)

    # str / dir / int / int_opt / float — coerce via the shared settings logic.
    cur = "" if current is None else str(current)
    raw = Prompt.ask(f"{label}", default=cur)
    return settings_store.coerce(kind, raw)


def _models_flow():
    """Check the model folder for the manifest's GGUFs and download the missing
    ones from Hugging Face, with the user's confirmation."""
    try:
        manifest = model_fetch.load_manifest()
        model_fetch.ensure_hf_available()
    except model_fetch.ManifestError as exc:
        console.print(f"[red]{exc}[/]")
        return

    present, missing = model_fetch.check_models(manifest)
    console.print(f"[bold]{i18n.t('models_title')}[/]  "
                  f"({manifest['model_dir']})")
    console.print(f"[green]{i18n.t('models_present', n=len(present))}[/]")
    if not missing:
        console.print(f"[green]{i18n.t('models_all_present', dir=manifest['model_dir'])}[/]")
        return

    console.print(f"[yellow]{i18n.t('models_missing', n=len(missing))}[/]")
    for m in missing:
        console.print(f"  • {m['name']}  [grey62]{m['repo_id']} / {m['filename']}[/]")

    if not Confirm.ask(i18n.t("models_confirm", n=len(missing),
                              dir=manifest["model_dir"]), default=True):
        return

    for i, m in enumerate(missing, 1):
        console.print(i18n.t("models_downloading", name=m["name"], i=i, n=len(missing)))
        try:
            model_fetch.download_model(m, manifest["model_dir"], manifest["hf_token"])
        except Exception as exc:                       # network / auth / repo errors
            console.print(f"[red]{i18n.t('models_failed', err=exc)}[/]")
            return
    console.print(f"[green]{i18n.t('models_done', n=len(missing), dir=manifest['model_dir'])}[/]")


def run_settings_editor() -> bool:
    """Interactive settings editor. Returns True to launch, False to quit.

    Mirrors the GUI's settings screen: edit fields, reset to defaults, then
    Save & Launch — persisting to settings.json and applying onto core.config.
    """
    fields = _flat_fields()
    # Start from whatever is already saved; edits accumulate as deltas.
    overrides = dict(settings_store.load())

    while True:
        base = settings_store.effective()           # saved-or-default per key
        values = {**base, **overrides}
        console.clear()
        console.print(_render_settings_table(values))
        console.print(i18n.t("tui_menu"))
        choice = Prompt.ask(i18n.t("tui_select"), default="s").strip().lower()

        if choice == "q":
            return False

        if choice == "l":            # toggle UI language, then redraw
            i18n.toggle()
            continue

        if choice == "m":            # check / download GGUF model files
            _models_flow()
            Prompt.ask(i18n.t("tui_press_enter"))
            continue

        if choice == "s":
            try:
                settings_store.save(overrides)
                settings_store.apply(overrides)
            except Exception as exc:
                console.print(f"[red]{i18n.t('msg_save_err', exc=exc)}[/]")
                Prompt.ask(i18n.t("tui_press_enter"))
                continue
            return True

        if choice == "r":
            import importlib
            import core.config as _cfg
            importlib.reload(_cfg)
            settings_store.save({})
            overrides = {}
            continue

        if choice.isdigit() and 1 <= int(choice) <= len(fields):
            _section, key, label, kind = fields[int(choice) - 1]
            tlabel = i18n.field(key, label)
            try:
                overrides[key] = _prompt_field(tlabel, kind, values.get(key))
            except (ValueError, TypeError):
                console.print(f"[red]{i18n.t('tui_bad_value', label=tlabel)}[/]")
                Prompt.ask(i18n.t("tui_press_enter"))
            continue

        console.print(f"[red]{i18n.t('tui_bad_choice')}[/]")
        Prompt.ask(i18n.t("tui_press_enter"))


# ─────────────────────────────────────────────────────────────────────────────
# Monitor screen
# ─────────────────────────────────────────────────────────────────────────────

class Monitor:
    """Holds live pipeline state and renders the dashboard.

    Consumes the same event dicts the GUI handles in PipelineGUI._handle, so the
    two stay behaviourally identical.
    """

    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.phase_key = "phase_idle"      # i18n key; rendered in current language
        self.phase_cur = 0
        self.phase_total = 0
        self.status = "idle"
        self.start_time: float | None = None
        self.finished = False
        self.logs: deque[str] = deque(maxlen=8)

    # -- state mutation ------------------------------------------------------

    def add_row(self, label: str):
        if label not in self.rows:
            self.rows[label] = {
                "states": {k: "pending" for k in PASS_KEYS},
                "completed": False, "cur": 0, "total": 0, "step_desc": "",
            }

    def _is_completed(self, row: dict) -> bool:
        states = row["states"].values()
        if "error" in states or "running" in states:
            return False
        return row["states"].get("pass_4") in ("done", "skip")

    def _set_pass(self, label: str, pass_key: str, state: str):
        row = self.rows.get(label)
        if not row:
            return
        row["states"][pass_key] = state
        row["completed"] = self._is_completed(row)

    def _mark_running_as(self, state: str):
        for row in self.rows.values():
            for key, st in list(row["states"].items()):
                if st == "running":
                    row["states"][key] = state

    def log(self, level: str, message: str):
        stamp = datetime.now().strftime("%H:%M:%S")
        self.logs.append(f"[{stamp}] {message}")

    def handle(self, event: dict):
        t = event.get("type", "")
        label = event.get("pdf_label", "")

        if t == "pipeline_start":
            for lbl in event.get("pdf_labels", []):
                self.add_row(lbl)

        elif t == "phase_start":
            phase = event["phase"]
            self.phase_total = event.get("total", 1)
            self.phase_cur = 0
            self.phase_key = "phase_a" if phase == "A" else "phase_b"
            self.log("info", i18n.t("log_phase_started",
                                    label=i18n.t(self.phase_key),
                                    total=self.phase_total))

        elif t == "phase_progress":
            self.phase_cur = event.get("current", 0)
            self.phase_total = event.get("total", self.phase_total)

        elif t == "phase_done":
            self.log("info", i18n.t("log_phase_done", phase=event.get("phase", "")))

        elif t == "icon_start":
            self._set_pass(label, event["pass"], "running")
        elif t == "icon_done":
            self._set_pass(label, event["pass"], "done")
            if event["pass"] == "pass_4":
                row = self.rows.get(label)
                if row:
                    row["cur"], row["total"] = 1, 1
        elif t == "icon_skip":
            self._set_pass(label, event["pass"], "skip")
        elif t == "icon_error":
            self._set_pass(label, event["pass"], "error")

        elif t == "step_start":
            row = self.rows.get(label)
            if row:
                row["total"] = max(event.get("total", 1), 1)
                row["cur"] = 0
                row["step_desc"] = event.get("step", "")
        elif t == "step_progress":
            row = self.rows.get(label)
            if row:
                row["cur"] = event.get("current", 0)
                row["total"] = event.get("total", row["total"])

        elif t == "log":
            self.log(event.get("level", "info"), event.get("message", ""))

        elif t == "pipeline_done":
            self.status = "done"
            self.finished = True
            self.log("info", i18n.t("log_done"))
        elif t == "pipeline_stopped":
            self._mark_running_as("pending")
            self.status = "stopped"
            self.finished = True
            self.log("warning", i18n.t("log_stopped"))
        elif t == "pipeline_error":
            self._mark_running_as("error")
            self.status = "error"
            self.finished = True
            self.log("error", event.get("message", "Unknown error"))

    # -- rendering -----------------------------------------------------------

    def _elapsed(self) -> str:
        if self.start_time is None:
            return "0:00:00"
        elapsed = int(time.monotonic() - self.start_time)
        h, rem = divmod(elapsed, 3600)
        m, s = divmod(rem, 60)
        return f"{h}:{m:02d}:{s:02d}"

    def _summary(self):
        total = running = done = error = 0
        for row in self.rows.values():
            total += 1
            states = row["states"].values()
            if "error" in states:
                error += 1
            elif "running" in states:
                running += 1
            elif row["states"].get("pass_4") in ("done", "skip"):
                done += 1
        return total, running, done, error

    @staticmethod
    def _bar(cur: int, total: int, width: int = 28) -> str:
        frac = (cur / total) if total else 0.0
        filled = int(round(frac * width))
        return "█" * filled + "░" * (width - filled)

    def _table(self) -> Table:
        tbl = Table(expand=True, header_style="bold grey62", pad_edge=False)
        tbl.add_column(i18n.t("col_pdf"), overflow="ellipsis", no_wrap=True, ratio=3)
        for lbl in PASS_LABELS:
            tbl.add_column(lbl, justify="center", width=4)
        tbl.add_column(i18n.t("col_progress"), justify="left", width=12)
        tbl.add_column(i18n.t("col_step"), overflow="ellipsis", no_wrap=True, ratio=2)

        active = [(l, r) for l, r in self.rows.items() if not r["completed"]]
        completed = sum(1 for r in self.rows.values() if r["completed"])

        # Cap visible active rows so a few-thousand-PDF run can't overflow the
        # terminal; the summary line still reflects the true totals.
        shown = active[:30]
        for label, row in shown:
            cells = [_truncate(label)]
            for k in PASS_KEYS:
                glyph, style = _PASS_STYLE[row["states"][k]]
                cells.append(Text(glyph, style=style))
            if row["total"]:
                cells.append(f"{row['cur']}/{row['total']}")
            else:
                cells.append("")
            cells.append(row["step_desc"] or "")
            tbl.add_row(*cells)

        if len(active) > len(shown):
            tbl.add_row(Text(f"… +{len(active) - len(shown)} more active",
                             style="grey50"),
                        *([""] * (len(PASS_LABELS) + 2)))
        if completed:
            tbl.add_row(Text(f"✓ {completed} {i18n.t('completed')}", style="green"),
                        *([""] * (len(PASS_LABELS) + 2)))
        return tbl

    def render(self) -> Group:
        status_txt = i18n.status_text(self.status)
        status_style = _STATUS_STYLE[self.status][1]

        header = Text()
        header.append(i18n.t("app_title") + "   ", style="bold")
        header.append(status_txt, style=status_style)
        header.append(f"    {self._elapsed()}", style="cyan")

        phase_label = i18n.t(self.phase_key)
        if self.phase_total:
            pct = int(100 * self.phase_cur / self.phase_total)
            phase = Text(
                f"{phase_label}  {self._bar(self.phase_cur, self.phase_total)}  "
                f"{self.phase_cur}/{self.phase_total} ({pct}%)"
            )
        else:
            phase = Text(phase_label, style="grey50")

        total, running, done, error = self._summary()
        summary = Text()
        summary.append(f"{i18n.t('sum_total')} {total}    ")
        summary.append(f"{i18n.t('sum_running')} {running}    ", style="yellow")
        summary.append(f"{i18n.t('sum_done')} {done}    ", style="green")
        summary.append(f"{i18n.t('sum_errors')} {error}", style="red")

        parts = [header, phase, summary, Rule(style="grey37"), self._table()]
        if self.logs:
            parts.append(Panel(Text("\n".join(self.logs), style="grey62"),
                               title=i18n.t("log"), title_align="left",
                               border_style="grey37"))
        return Group(*parts)


def _preload_rows(mon: Monitor):
    """Populate the PDF list before the run starts (like the GUI does), so the
    user sees what will be processed. Best-effort — the pipeline_start event
    repopulates authoritatively anyway."""
    try:
        import core.config as cfg
        from Pipeline import get_pdf_tasks
        import yaml
        if not os.path.exists(cfg.PDF_CONFIG_PATH):
            return
        with open(cfg.PDF_CONFIG_PATH, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        for path_list, _filename, language, _excl, _incl in get_pdf_tasks(config):
            product = path_list[0]
            sub = "_".join(os.path.splitext(p)[0] for p in path_list[1:]).lower()
            mon.add_row(f"{product}/{sub}/{language}")
    except Exception:
        pass


def run_monitor():
    """Run the pipeline and render the live dashboard until it finishes."""
    q: queue.Queue = queue.Queue()
    stop_event = threading.Event()
    mon = Monitor()

    _preload_rows(mon)
    console.clear()
    console.print(mon.render())
    console.print("\n" + i18n.t("tui_loaded", n=len(mon.rows)))
    if Prompt.ask(i18n.t("tui_start"), default="").strip().lower() == "q":
        return

    def worker():
        try:
            from Pipeline import run_pipeline
            run_pipeline(progress_queue=q, stop_event=stop_event)
        except Exception as exc:  # surface as an in-band event
            q.put({"type": "pipeline_error", "message": str(exc)})

    mon.status = "running"
    mon.start_time = time.monotonic()
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    try:
        with Live(mon.render(), console=console, refresh_per_second=8,
                  screen=False) as live:
            while True:
                try:
                    while True:
                        mon.handle(q.get_nowait())
                except queue.Empty:
                    pass
                live.update(mon.render())
                if mon.finished and q.empty():
                    break
                if not thread.is_alive() and q.empty():
                    break
                time.sleep(0.1)
            live.update(mon.render())
    except KeyboardInterrupt:
        stop_event.set()
        console.print(f"\n[dark_orange]{i18n.t('log_stop_req')}[/]")
        thread.join(timeout=600)
        # Drain whatever the pipeline emitted while shutting down.
        try:
            while True:
                mon.handle(q.get_nowait())
        except queue.Empty:
            pass
        console.print(mon.render())

    status_style = _STATUS_STYLE[mon.status][1]
    console.print(i18n.t("tui_pipeline_end", status=i18n.status_text(mon.status),
                         elapsed=mon._elapsed()),
                  style=status_style)


def main():
    # Apply any saved settings up front so the editor shows effective values and
    # a later Pipeline import picks them up (mirrors PipelineGUI.__init__).
    try:
        settings_store.apply(settings_store.load())
    except Exception:
        pass

    if not run_settings_editor():
        console.print(i18n.t("tui_aborted"))
        return
    run_monitor()


if __name__ == "__main__":
    main()
