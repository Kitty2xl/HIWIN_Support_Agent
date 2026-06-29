"""
gui.py — HIWIN Document Pipeline GUI

Run this file directly to launch the pipeline with a graphical interface:
    python gui.py

Each PDF gets its own row showing per-pass status icons and a live progress
bar.  A resizable log panel sits at the bottom.

Theming: two palettes (light + indigo, dark slate + violet) live in config.py.
Every widget is registered with a small styler closure, so the ☾/☀ button in
the header re-colours the entire UI live — including in-flight rows — without a
restart or losing any state.
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import queue
import threading
import os
import time
import yaml
from datetime import datetime

from core.config import (
    THEMES, DEFAULT_THEME,
    I_PENDING, I_RUNNING, I_DONE, I_SKIP, I_ERROR,
    PASS_KEYS, COLUMNS, ROW_H, HDR_H, SCROLLBAR_W, MAX_ROWS_H,
)
from core import settings as settings_store
from core import i18n
from core import model_fetch

# Per-pass state → glyph and theme colour key.
_PASS_ICON = {
    "pending": I_PENDING, "running": I_RUNNING, "done": I_DONE,
    "skip": I_SKIP, "error": I_ERROR,
}
_PASS_COLOR_KEY = {
    "pending": "c_pending", "running": "c_running", "done": "c_done",
    "skip": "c_skip", "error": "c_error",
}
# Status display text is provided per-language by core.i18n.status_text().

# Settings column headings that have translations (others, e.g. P1..P4, are
# language-neutral and shown verbatim).
_COL_I18N = {"PDF": "col_pdf", "Progress": "col_progress", "Step": "col_step"}


def _ts():
    return datetime.now().strftime("%H:%M:%S")


def _truncate(text: str, maxlen: int = 36) -> str:
    return text if len(text) <= maxlen else "…" + text[-(maxlen - 1):]


class PipelineGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title(i18n.t("app_title"))

        self.theme_name = DEFAULT_THEME if DEFAULT_THEME in THEMES else "light"
        self.theme      = THEMES[self.theme_name]
        self._themed: list[tuple[tk.Widget, callable]] = []
        # Live re-text registry (parallel to _themed): each entry re-applies a
        # widget's text in the current language when the ⇄ button is pressed.
        self._texts: list[tuple[tk.Widget, callable]] = []
        self._status    = "idle"
        self._phase_key = "phase_idle"   # i18n key for the current phase label
        self._stopping  = False          # Stop button shows "Stopping…" while True

        self._style = ttk.Style()
        try:
            self._style.theme_use("clam")
        except tk.TclError:
            pass

        self._queue: queue.Queue = queue.Queue()
        self._running = False
        self._thread: threading.Thread | None = None
        self._start_time: float | None = None
        self._stop_event = threading.Event()

        self._rows: dict[str, dict] = {}
        # Finished PDFs are tucked under a collapsible "Completed" header so they
        # don't clutter the active list.  Start collapsed for a clean view.
        self._completed_collapsed = True
        # Coalesce expensive whole-grid work to once per poll cycle, so a burst of
        # hundreds of events doesn't relayout/recount per event (O(events × rows)).
        self._pending_relayout = False
        self._pending_summary  = False

        # Apply any saved settings up front so the form shows effective values and
        # the later Pipeline import picks them up.
        try:
            settings_store.apply(settings_store.load())
        except Exception:
            pass

        # The app opens on the Settings screen; the monitor is built when the user
        # clicks "Save & Launch" (so edits are applied before the pipeline loads).
        self.root.configure(bg=self.theme["bg"])
        self._build_settings_screen()

    # ── Startup settings screen ─────────────────────────────────────────────────

    def _reset_root_grid(self):
        for i in range(8):
            self.root.rowconfigure(i, weight=0)
        self.root.columnconfigure(0, weight=1)

    def _build_settings_screen(self):
        t = self.theme
        self.root.unbind_all("<MouseWheel>")
        self._reset_root_grid()
        self.root.rowconfigure(0, weight=1)

        self._settings_frame = tk.Frame(self.root, bg=t["bg"])
        self._settings_frame.grid(row=0, column=0, sticky="nsew")
        sf = self._settings_frame
        sf.columnconfigure(0, weight=1)
        sf.rowconfigure(1, weight=1)

        hdr = tk.Frame(sf, bg=t["header_bg"])
        hdr.grid(row=0, column=0, sticky="ew")
        tk.Label(hdr, text=i18n.t("app_title"), font=("Segoe UI", 15, "bold"),
                 bg=t["header_bg"], fg=t["header_fg"]).pack(anchor="w", padx=18, pady=(10, 0))
        tk.Label(hdr, text=i18n.t("settings_subtitle"),
                 font=("Segoe UI", 9), bg=t["header_bg"], fg=t["muted"]
                 ).pack(anchor="w", padx=18, pady=(0, 10))

        body = tk.Frame(sf, bg=t["bg"])
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        canvas = tk.Canvas(body, bg=t["bg"], highlightthickness=0)
        canvas.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        sb.grid(row=0, column=1, sticky="ns")
        canvas.configure(yscrollcommand=sb.set)
        inner = tk.Frame(canvas, bg=t["bg"])
        inner.columnconfigure(0, weight=1)
        win = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>",
                   lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(win, width=e.width))
        canvas.bind("<Enter>", lambda _e: canvas.bind_all(
            "<MouseWheel>", lambda ev: canvas.yview_scroll(int(-ev.delta / 120), "units")))
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))

        self._setting_widgets: dict[str, tuple[str, callable]] = {}
        values = settings_store.effective()
        row = 0
        last_tier = None
        for section, fields in settings_store.SCHEMA:
            tier = settings_store.section_tier(section)
            if tier != last_tier:                      # Common / Advanced divider
                row = self._settings_tier_header(inner, row, tier)
                last_tier = tier
            row = self._settings_section(inner, row, section, fields, values)
        tk.Frame(inner, bg=t["bg"], height=8).grid(row=row, column=0)

        footer = tk.Frame(sf, bg=t["surface"], highlightthickness=1,
                          highlightbackground=t["border"])
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        self._settings_msg = tk.Label(footer, text="", font=("Segoe UI", 9),
                                      bg=t["surface"], fg=t["c_error"], anchor="w")
        self._settings_msg.grid(row=0, column=0, sticky="w", padx=16, pady=10)
        self._dl_btn = tk.Button(footer, text=i18n.t("models_button"), relief="flat",
                                 bd=0, cursor="hand2", font=("Segoe UI", 9),
                                 bg=t["surface"], fg=t["subtext"],
                                 activebackground=t["border"], activeforeground=t["accent"],
                                 command=self._download_models)
        self._dl_btn.grid(row=0, column=1, padx=(0, 8), pady=10)
        tk.Button(footer, text=i18n.lang_button_label(), font=("Segoe UI", 10, "bold"),
                  relief="flat", bd=0, padx=10, pady=6, cursor="hand2",
                  bg=t["surface"], fg=t["subtext"],
                  activebackground=t["border"], activeforeground=t["accent"],
                  command=self._toggle_lang
                  ).grid(row=0, column=2, padx=(0, 8), pady=10)
        tk.Button(footer, text=i18n.t("reset_defaults"), relief="flat", bd=0, cursor="hand2",
                  font=("Segoe UI", 9), bg=t["surface"], fg=t["subtext"],
                  activebackground=t["border"], activeforeground=t["text"],
                  command=self._reset_settings_defaults
                  ).grid(row=0, column=3, padx=(0, 8), pady=10)
        tk.Button(footer, text=i18n.t("save_launch") + "  ▶", font=("Segoe UI", 10, "bold"),
                  relief="flat", bd=0, padx=22, pady=7, cursor="hand2",
                  bg=t["accent"], fg="#ffffff",
                  activebackground=t["accent_active"], activeforeground="#ffffff",
                  command=self._save_and_launch
                  ).grid(row=0, column=4, padx=(0, 16), pady=10)

    def _settings_tier_header(self, parent, row, tier):
        """A prominent 'Common' / 'Advanced' divider above its sections."""
        t = self.theme
        label = i18n.t("tier_common" if tier == "common" else "tier_advanced")
        tk.Label(parent, text=label.upper(), font=("Segoe UI", 11, "bold"),
                 bg=t["bg"], fg=t["accent"], anchor="w"
                 ).grid(row=row, column=0, sticky="ew", padx=12, pady=(18, 2))
        return row + 1

    def _settings_section(self, parent, row, title, fields, values):
        t = self.theme
        tk.Label(parent, text=i18n.section(title).upper(), font=("Segoe UI", 8, "bold"),
                 bg=t["bg"], fg=t["col_hdr_fg"], anchor="w"
                 ).grid(row=row, column=0, sticky="ew", padx=16, pady=(14, 4))
        row += 1
        card = tk.Frame(parent, bg=t["surface"], highlightthickness=1,
                        highlightbackground=t["border"], highlightcolor=t["border"])
        card.grid(row=row, column=0, sticky="ew", padx=16)
        card.columnconfigure(1, weight=1)
        for i, (key, label, kind) in enumerate(fields):
            self._settings_field(card, i, key, label, kind, values.get(key))
        return row + 1

    def _settings_field(self, parent, row, key, label, kind, value):
        t = self.theme
        tk.Label(parent, text=i18n.field(key, label), font=("Segoe UI", 9), bg=t["surface"],
                 fg=t["text"], anchor="w"
                 ).grid(row=row, column=0, sticky="w", padx=(12, 10), pady=6)

        if kind == "bool":
            var = tk.BooleanVar(value=bool(value))
            tk.Checkbutton(parent, variable=var, bg=t["surface"], bd=0,
                           activebackground=t["surface"], highlightthickness=0,
                           selectcolor=t["surface_alt"]
                           ).grid(row=row, column=1, sticky="w", padx=(0, 12), pady=6)
            self._setting_widgets[key] = (kind, var.get)

        elif kind == "list":
            items = value or []
            txt = tk.Text(parent, height=max(2, len(items) + 1), font=("Consolas", 9),
                          bg=t["surface_alt"], fg=t["text"], insertbackground=t["text"],
                          relief="flat", highlightthickness=1,
                          highlightbackground=t["border"], wrap="none")
            txt.grid(row=row, column=1, columnspan=2, sticky="ew", padx=(0, 12), pady=6)
            for line in items:
                txt.insert("end", str(line) + "\n")
            self._setting_widgets[key] = (kind, lambda tw=txt: tw.get("1.0", "end"))

        else:
            var = tk.StringVar(value="" if value is None else str(value))
            ent = tk.Entry(parent, textvariable=var, font=("Consolas", 9),
                           show="•" if kind == "password" else "",
                           bg=t["surface_alt"], fg=t["text"], insertbackground=t["text"],
                           relief="flat", highlightthickness=1,
                           highlightbackground=t["border"])
            ent.grid(row=row, column=1, sticky="ew",
                     padx=(0, 4 if kind == "dir" else 12), pady=6)
            self._setting_widgets[key] = (kind, var.get)
            if kind == "dir":
                tk.Button(parent, text=i18n.t("browse"), relief="flat", bd=0, cursor="hand2",
                          font=("Segoe UI", 8), bg=t["border"], fg=t["text"],
                          command=lambda v=var: self._pick_dir(v)
                          ).grid(row=row, column=2, padx=(0, 12), pady=6)

    def _pick_dir(self, var):
        chosen = filedialog.askdirectory(initialdir=var.get() or os.getcwd())
        if chosen:
            var.set(os.path.normpath(chosen))

    def _reset_settings_defaults(self):
        import importlib
        import core.config as _cfg
        importlib.reload(_cfg)
        settings_store.save({})
        self._settings_frame.destroy()
        self._build_settings_screen()

    def _save_and_launch(self):
        overrides = {}
        for key, (kind, getter) in self._setting_widgets.items():
            try:
                overrides[key] = settings_store.coerce(kind, getter())
            except (ValueError, TypeError):
                label = next((i18n.field(k, l) for _s, fs in settings_store.SCHEMA
                              for k, l, _k in fs if k == key), key)
                self._settings_msg.config(text=i18n.t("msg_invalid", label=label))
                return
        try:
            settings_store.save(overrides)
            settings_store.apply(overrides)
        except Exception as exc:
            self._settings_msg.config(text=i18n.t("msg_save_err", exc=exc))
            return
        self._launch_monitor()

    # ── GGUF model download (Hugging Face) ───────────────────────────────────────

    def _download_models(self):
        """Check the model folder for the manifest's GGUFs and download missing
        ones from Hugging Face, with confirmation. Runs in a worker thread so the
        UI stays responsive; progress is shown in the settings message line."""
        try:
            manifest = model_fetch.load_manifest()
            model_fetch.ensure_hf_available()
        except model_fetch.ManifestError as exc:
            messagebox.showwarning(i18n.t("models_button"), str(exc))
            return

        _present, missing = model_fetch.check_models(manifest)
        if not missing:
            messagebox.showinfo(i18n.t("models_button"),
                                i18n.t("models_all_present", dir=manifest["model_dir"]))
            return

        listing = "\n".join(f"  • {m['name']}  ({m['repo_id']} / {m['filename']})"
                            for m in missing)
        if not messagebox.askyesno(
                i18n.t("models_button"),
                i18n.t("models_confirm", n=len(missing), dir=manifest["model_dir"])
                + "\n\n" + listing):
            return

        self._dl_queue = queue.Queue()
        self._dl_btn.config(state="disabled")
        threading.Thread(target=self._download_worker,
                         args=(manifest, missing), daemon=True).start()
        self.root.after(150, self._poll_download)

    def _download_worker(self, manifest, missing):
        try:
            for i, m in enumerate(missing, 1):
                self._dl_queue.put(("status", i18n.t("models_downloading",
                                                     name=m["name"], i=i, n=len(missing))))
                model_fetch.download_model(m, manifest["model_dir"], manifest["hf_token"])
            self._dl_queue.put(("done", i18n.t("models_done", n=len(missing),
                                               dir=manifest["model_dir"])))
        except Exception as exc:        # network / auth / repo errors
            self._dl_queue.put(("error", i18n.t("models_failed", err=exc)))

    def _poll_download(self):
        finished = False
        try:
            while True:
                kind, msg = self._dl_queue.get_nowait()
                colour = {"status": self.theme["subtext"],
                          "done":   self.theme["c_done"],
                          "error":  self.theme["c_error"]}[kind]
                try:
                    self._settings_msg.config(text=msg, fg=colour)
                except tk.TclError:
                    return              # settings screen was rebuilt/closed
                finished = finished or kind in ("done", "error")
        except queue.Empty:
            pass
        if finished:
            try:
                self._dl_btn.config(state="normal")
            except tk.TclError:
                pass
            return
        self.root.after(150, self._poll_download)

    def _launch_monitor(self):
        self.root.unbind_all("<MouseWheel>")
        if getattr(self, "_settings_frame", None) is not None:
            self._settings_frame.destroy()
            self._settings_frame = None
        self._themed.clear()
        self._texts.clear()
        self._rows.clear()
        self._reset_root_grid()
        self._build_ui()
        self._load_pdf_list()
        self._apply_theme()
        self._refresh_summary()
        self.root.after(80, self._poll)

    def _open_settings(self):
        """Return to the settings screen from the monitor (disabled while running)."""
        if self._running:
            return
        self.root.unbind_all("<MouseWheel>")
        for child in self.root.winfo_children():
            child.destroy()
        self._themed.clear()
        self._texts.clear()
        self._rows.clear()
        self._build_settings_screen()

    # ── Theming core ────────────────────────────────────────────────────────────

    def _th(self, widget, styler):
        """Register a widget with a styler(theme)->options dict and apply it now."""
        self._themed.append((widget, styler))
        try:
            widget.configure(**styler(self.theme))
        except tk.TclError:
            pass
        return widget

    # ── Localisation core ───────────────────────────────────────────────────────

    def _tx(self, widget, text_fn):
        """Register a widget whose text is text_fn() in the current language, and
        apply it now. _apply_lang() re-runs every text_fn after a toggle."""
        self._texts.append((widget, text_fn))
        try:
            widget.configure(text=text_fn())
        except tk.TclError:
            pass
        return widget

    def _toggle_lang(self):
        i18n.toggle()
        self.root.title(i18n.t("app_title"))
        # On the settings screen there is no live monitor state, so just rebuild
        # it in the new language; on the monitor, re-text in place (keeping state).
        if getattr(self, "_settings_frame", None) is not None:
            self._settings_frame.destroy()
            self._settings_frame = None
            self._build_settings_screen()
        else:
            self._apply_lang()

    def _apply_lang(self):
        for widget, text_fn in self._texts:
            try:
                widget.configure(text=text_fn())
            except tk.TclError:
                pass
        self._lang_btn.configure(text=i18n.lang_button_label())
        self._set_status(self._status)        # status pill text
        self._set_phase_text()                # phase label
        self._refresh_summary()               # summary chip labels
        completed = sum(1 for r in self._rows.values() if r["completed"])
        if completed:
            self._update_completed_header(completed)

    def _set_phase_text(self):
        self._phase_var.set(i18n.t(self._phase_key))

    def _start_btn_text(self):
        return "▶  " + i18n.t("start_pipeline")

    def _stop_btn_text(self):
        return "■  " + i18n.t("stopping" if self._stopping else "stop")

    def _toggle_theme(self):
        self.theme_name = "dark" if self.theme_name == "light" else "light"
        self.theme = THEMES[self.theme_name]
        self._apply_theme()

    def _apply_theme(self):
        t = self.theme
        self.root.configure(bg=t["bg"])
        self._configure_styles(t)
        for widget, styler in self._themed:
            try:
                widget.configure(**styler(t))
            except tk.TclError:
                pass
        self._toggle_btn.configure(text=t["toggle_icon"])
        self._set_status(self._status)          # repaint pill + timer
        for row in self._rows.values():          # repaint dynamic rows
            self._restyle_row(row)

    def _configure_styles(self, t):
        s = self._style
        for name, colour in (
            ("Phase.Horizontal.TProgressbar", t["accent"]),
            ("Row.Horizontal.TProgressbar",   t["accent"]),
            ("Done.Horizontal.TProgressbar",  t["c_done"]),
        ):
            s.configure(name, troughcolor=t["track"], background=colour,
                        bordercolor=t["track"], lightcolor=colour,
                        darkcolor=colour, thickness=10)
        s.configure("Vertical.TScrollbar", troughcolor=t["bg"],
                    background=t["scrollbar"], bordercolor=t["bg"],
                    arrowcolor=t["subtext"])
        s.configure("TPanedwindow", background=t["border"])
        s.configure("Sash", sashthickness=6, gripcount=0, background=t["border"])

    def _status_colour(self, t):
        return {
            "done":    t["c_done"],
            "error":   t["c_error"],
            "stopped": t["pills"]["stopped"][0],
        }.get(self._status, t["muted"])

    def _set_status(self, status: str):
        self._status = status
        t = self.theme
        bg, fg = t["pills"][status]
        self._status_pill.configure(text=i18n.status_text(status), bg=bg, fg=fg)
        self._elapsed_lbl.configure(bg=t["header_bg"], fg=self._status_colour(t))

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(5, weight=1)   # body (paned) expands

        self._build_header()
        self._sep(1)
        self._build_phase_strip()
        self._sep(3)
        self._build_summary()
        self._build_body()
        self._build_buttons()

    def _sep(self, row):
        bar = tk.Frame(self.root, height=1)
        bar.grid(row=row, column=0, sticky="ew")
        self._th(bar, lambda t: {"bg": t["border"]})

    def _build_header(self):
        hdr = tk.Frame(self.root)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.columnconfigure(1, weight=1)
        self._th(hdr, lambda t: {"bg": t["header_bg"]})

        title_box = tk.Frame(hdr)
        title_box.grid(row=0, column=0, padx=18, pady=10, sticky="w")
        self._th(title_box, lambda t: {"bg": t["header_bg"]})

        title_lbl = self._th(tk.Label(title_box, font=("Segoe UI", 15, "bold")),
                             lambda t: {"bg": t["header_bg"], "fg": t["header_fg"]})
        self._tx(title_lbl, lambda: i18n.t("app_title"))
        title_lbl.pack(anchor="w")
        sub_lbl = self._th(tk.Label(title_box, font=("Segoe UI", 9)),
                           lambda t: {"bg": t["header_bg"], "fg": t["muted"]})
        self._tx(sub_lbl, lambda: i18n.t("monitor_subtitle"))
        sub_lbl.pack(anchor="w")

        self._elapsed_lbl = tk.Label(hdr, text="0:00:00", font=("Consolas", 12))
        self._elapsed_lbl.grid(row=0, column=2, padx=(0, 14), sticky="e")

        self._status_pill = tk.Label(hdr, font=("Segoe UI", 9, "bold"),
                                     padx=12, pady=3)
        self._status_pill.grid(row=0, column=3, padx=(0, 10), sticky="e")

        self._lang_btn = tk.Button(hdr, text=i18n.lang_button_label(),
                                   font=("Segoe UI", 10, "bold"),
                                   relief="flat", bd=0, cursor="hand2",
                                   command=self._toggle_lang)
        self._lang_btn.grid(row=0, column=4, padx=(0, 6), sticky="e")
        self._th(self._lang_btn,
                 lambda t: {"bg": t["header_bg"], "fg": t["subtext"],
                            "activebackground": t["header_bg"],
                            "activeforeground": t["accent"]})

        self._settings_btn = tk.Button(hdr, text="⚙", font=("Segoe UI", 13),
                                       relief="flat", bd=0, cursor="hand2",
                                       command=self._open_settings)
        self._settings_btn.grid(row=0, column=5, padx=(0, 4), sticky="e")
        self._th(self._settings_btn,
                 lambda t: {"bg": t["header_bg"], "fg": t["subtext"],
                            "activebackground": t["header_bg"],
                            "activeforeground": t["accent"],
                            "disabledforeground": t["muted"]})

        self._toggle_btn = tk.Button(hdr, text="☾", font=("Segoe UI", 13),
                                     relief="flat", bd=0, cursor="hand2",
                                     command=self._toggle_theme)
        self._toggle_btn.grid(row=0, column=6, padx=(0, 16), sticky="e")
        self._th(self._toggle_btn,
                 lambda t: {"bg": t["header_bg"], "fg": t["subtext"],
                            "activebackground": t["header_bg"],
                            "activeforeground": t["accent"]})

    def _build_phase_strip(self):
        strip = tk.Frame(self.root)
        strip.grid(row=2, column=0, sticky="ew")
        strip.columnconfigure(1, weight=1)
        self._th(strip, lambda t: {"bg": t["phase_bg"]})

        self._phase_var = tk.StringVar(value=i18n.t("phase_idle"))
        self._th(tk.Label(strip, textvariable=self._phase_var,
                          font=("Segoe UI", 9, "bold"), width=22, anchor="w"),
                 lambda t: {"bg": t["phase_bg"], "fg": t["phase_fg"]}
                 ).grid(row=0, column=0, padx=(18, 10), pady=9)

        self._phase_bar = ttk.Progressbar(strip, orient="horizontal",
                                          mode="determinate",
                                          style="Phase.Horizontal.TProgressbar")
        self._phase_bar.grid(row=0, column=1, sticky="ew", padx=(0, 12))

        self._phase_count_lbl = tk.Label(strip, text="0 / 0",
                                         font=("Consolas", 9), width=14, anchor="e")
        self._th(self._phase_count_lbl,
                 lambda t: {"bg": t["phase_bg"], "fg": t["phase_fg"]}
                 ).grid(row=0, column=2, padx=(0, 18))

    def _build_summary(self):
        bar = tk.Frame(self.root)
        bar.grid(row=4, column=0, sticky="ew", padx=16, pady=(8, 4))
        self._th(bar, lambda t: {"bg": t["bg"]})

        self._summary_lbl: dict[str, tuple] = {}
        for key, i18n_key, colour_key in (
            ("total",   "sum_total",   "subtext"),
            ("running", "sum_running", "c_running"),
            ("done",    "sum_done",    "c_done"),
            ("error",   "sum_errors",  "c_error"),
        ):
            chip = tk.Label(bar, text=f"{i18n.t(i18n_key)} 0", font=("Segoe UI", 9, "bold"))
            chip.pack(side="left", padx=(0, 18))
            self._th(chip, lambda t, ck=colour_key: {"bg": t["bg"], "fg": t[ck]})
            self._summary_lbl[key] = (chip, i18n_key)

    def _build_body(self):
        paned = ttk.PanedWindow(self.root, orient="vertical")
        paned.grid(row=5, column=0, sticky="nsew", padx=16, pady=(2, 0))
        self._build_pdf_grid(paned)
        self._build_log(paned)

    def _build_pdf_grid(self, parent):
        card = tk.Frame(parent, highlightthickness=1)
        self._th(card, lambda t: {"bg": t["surface"],
                                  "highlightbackground": t["border"],
                                  "highlightcolor": t["border"]})
        card.columnconfigure(0, weight=1)
        card.columnconfigure(1, minsize=SCROLLBAR_W, weight=0)
        card.rowconfigure(1, weight=1)
        parent.add(card, weight=3)

        self._build_column_header(card)

        self._canvas = tk.Canvas(card, highlightthickness=0, height=MAX_ROWS_H)
        self._canvas.grid(row=1, column=0, sticky="nsew")
        self._th(self._canvas, lambda t: {"bg": t["border"]})  # row separators

        sb = ttk.Scrollbar(card, orient="vertical", command=self._canvas.yview)
        sb.grid(row=1, column=1, sticky="ns")
        self._canvas.configure(yscrollcommand=sb.set)

        self._rows_frame = tk.Frame(self._canvas)
        self._rows_frame.columnconfigure(0, weight=1)
        self._th(self._rows_frame, lambda t: {"bg": t["border"]})

        # Collapsible "Completed" section header — clicking it shows/hides every
        # finished PDF row.  Hidden until at least one PDF completes.
        self._completed_header = tk.Frame(self._rows_frame, cursor="hand2")
        self._completed_header.columnconfigure(0, weight=1)
        self._th(self._completed_header, lambda t: {"bg": t["col_hdr_bg"]})
        self._completed_header_lbl = tk.Label(
            self._completed_header, text=f"▸  {i18n.t('completed')}  (0)",
            font=("Segoe UI", 9, "bold"), anchor="w", cursor="hand2")
        self._completed_header_lbl.grid(row=0, column=0, sticky="ew",
                                        padx=(14, 4), pady=7)
        self._th(self._completed_header_lbl,
                 lambda t: {"bg": t["col_hdr_bg"], "fg": t["col_hdr_fg"]})
        for w in (self._completed_header, self._completed_header_lbl):
            w.bind("<Button-1>", self._toggle_completed)
        self._completed_header.bind("<Enter>", lambda _e: self._completed_hover(True))
        self._completed_header.bind("<Leave>", lambda _e: self._completed_hover(False))
        self._completed_header_lbl.bind("<Enter>", lambda _e: self._completed_hover(True))
        self._completed_header_lbl.bind("<Leave>", lambda _e: self._completed_hover(False))
        self._completed_header.grid_remove()

        self._canvas_window = self._canvas.create_window(
            (0, 0), window=self._rows_frame, anchor="nw")
        self._rows_frame.bind("<Configure>", self._on_rows_configure)
        self._canvas.bind("<Configure>", self._on_canvas_configure)

        self._canvas.bind("<Enter>",
                          lambda _e: self._canvas.bind_all("<MouseWheel>", self._on_wheel))
        self._canvas.bind("<Leave>",
                          lambda _e: self._canvas.unbind_all("<MouseWheel>"))

    def _build_column_header(self, parent):
        hdr = tk.Frame(parent)
        hdr.grid(row=0, column=0, columnspan=2, sticky="ew")
        self._th(hdr, lambda t: {"bg": t["col_hdr_bg"]})
        self._apply_columns(hdr, gutter=True)

        for i, (key, heading, w, weight) in enumerate(COLUMNS):
            anchor = "w" if key in ("name", "progress", "step") else "center"
            pad = (14, 4) if key == "name" else (4, 4)
            heading_text = (i18n.t(_COL_I18N[heading]) if heading in _COL_I18N
                            else heading)
            if weight == 0:
                cell = tk.Frame(hdr, width=w, height=HDR_H)
                cell.grid_propagate(False)
                cell.grid(row=0, column=i, sticky="nsew")
                self._th(cell, lambda t: {"bg": t["col_hdr_bg"]})
                lbl = tk.Label(cell, text=heading_text, font=("Segoe UI", 8, "bold"),
                               anchor=anchor)
                lbl.pack(fill="both", expand=True, padx=pad)
                self._th(lbl, lambda t: {"bg": t["col_hdr_bg"], "fg": t["col_hdr_fg"]})
            else:
                lbl = tk.Label(hdr, text=heading_text, font=("Segoe UI", 8, "bold"),
                               anchor=anchor)
                lbl.grid(row=0, column=i, sticky="nsew", padx=pad, pady=7)
                self._th(lbl, lambda t: {"bg": t["col_hdr_bg"], "fg": t["col_hdr_fg"]})
            # Translatable headings re-text on a language toggle; P1..P4 are neutral.
            if heading in _COL_I18N:
                self._tx(lbl, lambda h=heading: i18n.t(_COL_I18N[h]))

        gutter = tk.Frame(hdr, width=SCROLLBAR_W, height=HDR_H)
        gutter.grid(row=0, column=len(COLUMNS), sticky="nsew")
        self._th(gutter, lambda t: {"bg": t["col_hdr_bg"]})

    def _apply_columns(self, frame, gutter=False):
        for i, (_key, _heading, w, weight) in enumerate(COLUMNS):
            frame.columnconfigure(i, minsize=w, weight=weight)
        if gutter:
            frame.columnconfigure(len(COLUMNS), minsize=SCROLLBAR_W, weight=0)

    def _on_rows_configure(self, _event=None):
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self._canvas.itemconfig(self._canvas_window, width=event.width)

    def _on_wheel(self, event):
        self._canvas.yview_scroll(int(-event.delta / 120), "units")

    def _build_log(self, parent):
        frm = tk.Frame(parent)
        frm.columnconfigure(0, weight=1)
        frm.rowconfigure(1, weight=1)
        self._th(frm, lambda t: {"bg": t["bg"]})
        parent.add(frm, weight=1)

        head = tk.Frame(frm)
        head.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(8, 3))
        head.columnconfigure(0, weight=1)
        self._th(head, lambda t: {"bg": t["bg"]})

        log_lbl = self._th(tk.Label(head, font=("Segoe UI", 8, "bold"), anchor="w"),
                           lambda t: {"bg": t["bg"], "fg": t["subtext"]})
        self._tx(log_lbl, lambda: i18n.t("log"))
        log_lbl.grid(row=0, column=0, sticky="w")
        clr = tk.Button(head, relief="flat", bd=0, cursor="hand2",
                        font=("Segoe UI", 8), command=self._clear_log)
        self._tx(clr, lambda: i18n.t("clear"))
        clr.grid(row=0, column=1, sticky="e")
        self._th(clr, lambda t: {"bg": t["bg"], "fg": t["subtext"],
                                 "activebackground": t["bg"],
                                 "activeforeground": t["accent"]})

        self._log_text = tk.Text(frm, font=("Consolas", 8), height=7,
                                 state="disabled", wrap="word", relief="flat",
                                 padx=10, pady=6, bd=0)
        self._log_text.grid(row=1, column=0, sticky="nsew")
        self._th(self._log_text, lambda t: {"bg": t["log_bg"], "fg": t["log_fg"],
                                            "insertbackground": t["log_fg"]})
        self._log_text.tag_config("info",    foreground="#60a5fa")
        self._log_text.tag_config("warning", foreground="#fbbf24")
        self._log_text.tag_config("error",   foreground="#f87171")
        self._log_text.tag_config("ts",      foreground="#64748b")

        log_sb = ttk.Scrollbar(frm, orient="vertical", command=self._log_text.yview)
        log_sb.grid(row=1, column=1, sticky="ns")
        self._log_text.configure(yscrollcommand=log_sb.set)

    def _build_buttons(self):
        btn_frm = tk.Frame(self.root)
        btn_frm.grid(row=6, column=0, sticky="ew", padx=16, pady=10)
        self._th(btn_frm, lambda t: {"bg": t["bg"]})

        self._start_btn = tk.Button(
            btn_frm, font=("Segoe UI", 10, "bold"),
            relief="flat", bd=0, padx=22, pady=7, cursor="hand2",
            command=self._start_pipeline,
        )
        self._tx(self._start_btn, self._start_btn_text)
        self._start_btn.pack(side="left", padx=(0, 8))
        self._th(self._start_btn,
                 lambda t: {"bg": t["accent"], "fg": "#ffffff",
                            "activebackground": t["accent_active"],
                            "activeforeground": "#ffffff",
                            "disabledforeground": "#e5e7eb"})

        self._stop_btn = tk.Button(
            btn_frm, font=("Segoe UI", 10),
            relief="flat", bd=0, padx=18, pady=7, cursor="hand2",
            command=self._stop_pipeline, state="disabled",
        )
        self._tx(self._stop_btn, self._stop_btn_text)
        self._stop_btn.pack(side="left")
        self._th(self._stop_btn,
                 lambda t: {"bg": t["surface"], "fg": t["subtext"],
                            "activebackground": t["border"],
                            "activeforeground": t["text"],
                            "disabledforeground": t["muted"]})

    # ── PDF row management ────────────────────────────────────────────────────

    def _add_pdf_row(self, label: str, defer_relayout: bool = False):
        if label in self._rows:
            return
        idx = len(self._rows)
        frm = tk.Frame(self._rows_frame)
        frm.grid(row=idx, column=0, sticky="ew", pady=(0, 1))
        self._apply_columns(frm)

        cells, icon_labels = [frm], {}
        name_lbl = step_lbl = bar = bar_var = None

        for i, (key, _heading, w, weight) in enumerate(COLUMNS):
            if key == "name":
                cell = self._cell(frm, i, w)
                cells.append(cell)
                name_lbl = tk.Label(cell, text=_truncate(label),
                                    font=("Segoe UI", 9), anchor="w")
                name_lbl.pack(fill="both", expand=True, padx=(14, 4))
            elif key in PASS_KEYS:
                cell = self._cell(frm, i, w)
                cells.append(cell)
                lbl = tk.Label(cell, text=I_PENDING, font=("Segoe UI", 12, "bold"))
                lbl.pack(fill="both", expand=True)
                icon_labels[key] = lbl
            elif key == "progress":
                bar_var = tk.DoubleVar(value=0.0)
                bar = ttk.Progressbar(frm, orient="horizontal", maximum=1,
                                      mode="determinate", variable=bar_var,
                                      style="Row.Horizontal.TProgressbar")
                bar.grid(row=0, column=i, sticky="ew", padx=8, pady=11)
            elif key == "step":
                cell = self._cell(frm, i, w)
                cells.append(cell)
                step_lbl = tk.Label(cell, text="", font=("Segoe UI", 8), anchor="w")
                step_lbl.pack(fill="both", expand=True, padx=(4, 4))

        row = {
            "index": idx, "frame": frm, "cells": cells, "name_lbl": name_lbl,
            "icon_labels": icon_labels, "bar": bar, "bar_var": bar_var,
            "bar_max": 1, "step_lbl": step_lbl, "step_desc": "",
            "states": {k: "pending" for k in PASS_KEYS},
            "completed": False,
        }
        self._rows[label] = row
        self._restyle_row(row)
        # When bulk-loading, the caller relayouts once at the end (O(N) instead of
        # O(N²) from relaying the whole grid on every single add).
        if defer_relayout:
            self._pending_relayout = True
        else:
            self._relayout_rows()

    def _cell(self, parent, col, width):
        cell = tk.Frame(parent, width=width, height=ROW_H)
        cell.grid_propagate(False)
        cell.grid(row=0, column=col, sticky="nsew")
        return cell

    def _restyle_row(self, row: dict):
        """Recolour an entire row for the current theme + its live state."""
        t = self.theme
        base = t["surface_alt"] if row["index"] % 2 else t["surface"]
        states = row["states"].values()
        if "error" in states:
            bg = t["row_error"]
        elif "running" in states:
            bg = t["row_running"]
        else:
            bg = base
        for cell in row["cells"]:
            cell.configure(bg=bg)
        row["name_lbl"].configure(bg=bg, fg=t["text"])
        row["step_lbl"].configure(bg=bg, fg=t["subtext"])
        for key, lbl in row["icon_labels"].items():
            st = row["states"][key]
            lbl.configure(bg=bg, text=_PASS_ICON[st], fg=t[_PASS_COLOR_KEY[st]])

    def _set_pass_state(self, pdf_label: str, pass_key: str, state: str):
        row = self._rows.get(pdf_label)
        if not row:
            return
        row["states"][pass_key] = state
        self._restyle_row(row)
        # If this flips the PDF in or out of "completed", restructure the list so
        # finished rows move under the collapsible Completed header.  Defer the
        # whole-grid relayout/recount to the end of the poll cycle (see _poll).
        now_completed = self._is_row_completed(row)
        if now_completed != row["completed"]:
            row["completed"] = now_completed
            self._pending_relayout = True
        self._pending_summary = True

    # ── Completed-section (collapsible) ─────────────────────────────────────────

    def _is_row_completed(self, row: dict) -> bool:
        """A PDF is 'completed' once Pass 4 is done/skipped with nothing left
        running and no errors — mirrors the 'Done' tally in _refresh_summary."""
        states = row["states"].values()
        if "error" in states or "running" in states:
            return False
        return row["states"].get("pass_4") in ("done", "skip")

    def _relayout_rows(self):
        """Re-grid every PDF row: active rows first, then the Completed header
        (only if any are finished) followed by the finished rows when expanded."""
        active    = [r for r in self._rows.values() if not r["completed"]]
        completed = [r for r in self._rows.values() if r["completed"]]

        vis = grid_row = 0
        for row in active:
            row["index"] = vis; vis += 1
            row["frame"].grid(row=grid_row, column=0, sticky="ew", pady=(0, 1))
            self._restyle_row(row)
            grid_row += 1

        if completed:
            self._completed_header.grid(row=grid_row, column=0, sticky="ew",
                                        pady=(0, 1))
            grid_row += 1
            self._update_completed_header(len(completed))
            for row in completed:
                if self._completed_collapsed:
                    row["frame"].grid_remove()
                else:
                    row["index"] = vis; vis += 1
                    row["frame"].grid(row=grid_row, column=0, sticky="ew",
                                      pady=(0, 1))
                    self._restyle_row(row)
                    grid_row += 1
        else:
            self._completed_header.grid_remove()

        self._on_rows_configure()

    def _update_completed_header(self, n: int):
        arrow = "▸" if self._completed_collapsed else "▾"
        self._completed_header_lbl.config(text=f"{arrow}  {i18n.t('completed')}  ({n})")

    def _toggle_completed(self, _event=None):
        self._completed_collapsed = not self._completed_collapsed
        self._relayout_rows()

    def _completed_hover(self, entering: bool):
        t = self.theme
        self._completed_header_lbl.config(
            fg=t["accent"] if entering else t["col_hdr_fg"])

    def _refresh_summary(self):
        total = running = done = error = 0
        for row in self._rows.values():
            states = row["states"]
            total += 1
            if "error" in states.values():
                error += 1
            elif "running" in states.values():
                running += 1
            elif states.get("pass_4") in ("done", "skip"):
                done += 1
        for key, value in (("total", total), ("running", running),
                           ("done", done), ("error", error)):
            chip, i18n_key = self._summary_lbl[key]
            chip.config(text=f"{i18n.t(i18n_key)} {value}")

    # ── Config loading ────────────────────────────────────────────────────────

    def _load_pdf_list(self):
        try:
            import core.config as cfg
            from Pipeline import get_pdf_tasks
            config_path = cfg.PDF_CONFIG_PATH        # reflects current settings
            if not os.path.exists(config_path):
                return
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)
            labels = []
            for path_list, _filename, language, _excl, _incl in get_pdf_tasks(config):
                product = path_list[0]
                sub     = "_".join(os.path.splitext(p)[0] for p in path_list[1:]).lower()
                labels.append(f"{product}/{sub}/{language}")
        except Exception:
            return
        self._build_rows_chunked(labels)

    def _build_rows_chunked(self, labels, chunk=25):
        """Create rows a chunk at a time, yielding to the event loop between
        chunks, so a large list streams in instead of freezing the UI while a few
        thousand widgets are built at once."""
        def build(i=0):
            for lbl in labels[i:i + chunk]:
                self._add_pdf_row(lbl, defer_relayout=True)
            self._relayout_rows()
            self._refresh_summary()
            if i + chunk < len(labels):
                self.root.after(1, lambda: build(i + chunk))
        build()

    # ── Pipeline execution ────────────────────────────────────────────────────

    def _start_pipeline(self):
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._start_time = time.monotonic()
        self._stopping = False
        self._start_btn.config(state="disabled")
        self._stop_btn.config(state="normal", text=self._stop_btn_text())
        self._settings_btn.config(state="disabled")   # lock settings while running
        self._set_status("running")
        self._elapsed_lbl.config(text="0:00:00")
        self._phase_key = "phase_starting"
        self._set_phase_text()
        self._tick()

        self._thread = threading.Thread(target=self._run_pipeline_thread, daemon=True)
        self._thread.start()

    def _stop_pipeline(self):
        self._stop_event.set()
        self._stopping = True
        self._stop_btn.config(state="disabled", text=self._stop_btn_text())
        self._log("warning", i18n.t("log_stop_req"))

    def _tick(self):
        if not self._running or self._start_time is None:
            return
        elapsed = int(time.monotonic() - self._start_time)
        h, rem  = divmod(elapsed, 3600)
        m, s    = divmod(rem, 60)
        self._elapsed_lbl.config(text=f"{h}:{m:02d}:{s:02d}")
        self.root.after(1000, self._tick)

    def _run_pipeline_thread(self):
        try:
            from Pipeline import run_pipeline
            run_pipeline(progress_queue=self._queue, stop_event=self._stop_event)
        except Exception as exc:
            self._queue.put({"type": "pipeline_error", "message": str(exc)})

    # ── Queue polling ─────────────────────────────────────────────────────────

    def _poll(self):
        try:
            while True:
                self._handle(self._queue.get_nowait())
        except queue.Empty:
            pass
        # Run the expensive whole-grid operations at most once per cycle, after the
        # whole event batch is drained, instead of once per event.
        if self._pending_relayout:
            self._pending_relayout = False
            self._relayout_rows()
        if self._pending_summary:
            self._pending_summary = False
            self._refresh_summary()
        self.root.after(80, self._poll)

    def _handle(self, event: dict):
        t = event.get("type", "")

        if t == "pipeline_start":
            for lbl in event.get("pdf_labels", []):
                self._add_pdf_row(lbl, defer_relayout=True)
            self._pending_relayout = True
            self._pending_summary = True

        elif t == "phase_start":
            phase = event["phase"]
            total = event.get("total", 1)
            self._phase_key = "phase_a" if phase == "A" else "phase_b"
            self._set_phase_text()
            self._phase_bar.configure(maximum=max(total, 1), value=0)
            self._phase_count_lbl.config(text=f"0 / {total}")
            self._log("info", i18n.t("log_phase_started",
                                     label=i18n.t(self._phase_key), total=total))

        elif t == "phase_progress":
            cur   = event.get("current", 0)
            total = event.get("total",   1)
            self._phase_bar.configure(value=cur)
            pct = int(100 * cur / total) if total else 0
            self._phase_count_lbl.config(text=f"{cur} / {total}  ({pct}%)")

        elif t == "phase_done":
            self._log("info", i18n.t("log_phase_done", phase=event['phase']))

        elif t == "icon_start":
            self._set_pass_state(event.get("pdf_label", ""), event["pass"], "running")

        elif t == "icon_done":
            self._set_pass_state(event.get("pdf_label", ""), event["pass"], "done")
            self._mark_bar_complete(event.get("pdf_label", ""), event["pass"])

        elif t == "icon_skip":
            self._set_pass_state(event.get("pdf_label", ""), event["pass"], "skip")

        elif t == "icon_error":
            self._set_pass_state(event.get("pdf_label", ""), event["pass"], "error")

        elif t == "step_start":
            row = self._rows.get(event.get("pdf_label", ""))
            if row:
                total = max(event.get("total", 1), 1)
                row["bar_max"] = total
                row["step_desc"] = event.get("step", "")
                row["bar"].configure(maximum=total,
                                     style="Row.Horizontal.TProgressbar")
                row["bar_var"].set(0)
                self._update_step_label(row, 0, total)

        elif t == "step_progress":
            row = self._rows.get(event.get("pdf_label", ""))
            if row:
                cur   = event.get("current", 0)
                total = event.get("total", row["bar_max"])
                row["bar_var"].set(cur)
                self._update_step_label(row, cur, total)

        elif t == "log":
            self._log(event.get("level", "info"), event.get("message", ""))

        elif t == "pipeline_done":
            self._finish("done", i18n.t("log_done"), "info")

        elif t == "pipeline_stopped":
            self._mark_running_as("pending")
            self._finish("stopped", i18n.t("log_stopped"), "warning")

        elif t == "pipeline_error":
            self._mark_running_as("error")
            self._finish("error", event.get("message", "Unknown error"), "error",
                         keep_phase=True)

    def _update_step_label(self, row: dict, cur: int, total: int):
        text = f"{row['step_desc']}  {cur}/{total}".strip()
        row["step_lbl"].config(text=_truncate(text, 30))

    def _mark_bar_complete(self, pdf_label: str, pass_key: str):
        row = self._rows.get(pdf_label)
        if row and pass_key == "pass_4":
            row["bar"].configure(maximum=1, style="Done.Horizontal.TProgressbar")
            row["bar_var"].set(1)

    def _mark_running_as(self, state: str):
        for label, row in self._rows.items():
            for key, st in list(row["states"].items()):
                if st == "running":
                    self._set_pass_state(label, key, state)

    def _finish(self, status: str, message: str, level: str, keep_phase: bool = False):
        self._running = False
        self._stopping = False
        self._start_btn.config(state="normal")
        self._stop_btn.config(state="disabled", text=self._stop_btn_text())
        self._settings_btn.config(state="normal")     # settings editable again
        self._set_status(status)
        if not keep_phase:
            self._phase_key = {"done": "phase_complete",
                               "stopped": "phase_stopped"}.get(status, "phase_dash")
            self._set_phase_text()
        self._log(level, message)

    # ── Log helpers ───────────────────────────────────────────────────────────

    def _log(self, level: str, message: str):
        self._log_text.config(state="normal")
        self._log_text.insert("end", f"[{_ts()}] ", "ts")
        self._log_text.insert("end", message + "\n", level)
        self._log_text.see("end")
        self._log_text.config(state="disabled")

    def _clear_log(self):
        self._log_text.config(state="normal")
        self._log_text.delete("1.0", "end")
        self._log_text.config(state="disabled")

    # ── Entry point ───────────────────────────────────────────────────────────

    def run(self):
        # Polling starts in _launch_monitor; the app opens on the settings screen.
        self.root.minsize(900, 580)
        self.root.mainloop()


if __name__ == "__main__":
    app = PipelineGUI()
    app.run()
