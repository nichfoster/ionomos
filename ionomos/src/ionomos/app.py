"""
Ionomos app — setup wizard on first run, control panel afterwards.

    ionomos setup [--config PATH]        (or just double-click Ionomos.exe)

Tabs (uinav.TABS, D95):  Today · Inbox · Jobs · Analysis  |  ✓ Setup · Folders · Users · Methods · Advanced ·
       Run & Test · Notifications  |  Help
Bottom bar: config path, Reload, Save, Save & Check, Report a problem, Help (the full help page in the browser),
the busy indicator (run_bg), the status line; under it the build line and the watcher / FragPipe strip.
Today (what needs you, recent jobs), F1 / keyboard shortcuts, the remembered window and last tab: uinav.py and
docs/UI.md. The bottom bar is packed before the tabs, so a small screen shrinks the tabs, never the Save button.
Slow work (disks, FragPipe, the ledger, the network) runs through run_bg or a daemon thread and comes back through
App.post; Save shows what changes first (configio.diff_words) and keeps the version before it (Undo last save);
closing with unsaved changes asks; the Setup tab is one "Check everything" list (checks.py).

Pure tkinter/ttk. Edits a plain dict (ionomos.configio) and writes a
commented config.yaml; validation is done by ionomos.config.load so the app
can never write something the watcher wouldn't accept.
"""
from __future__ import annotations

import copy
import logging
import os
import queue
import sys
import threading
import time
import tkinter as tk
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from ionomos import __version__, configio, names, service, tkutil
from ionomos.config import ConfigError, load

log = logging.getLogger("ionomos.app")

PAD = {"padx": 6, "pady": 3}
IS_WIN = os.name == "nt"

HELP_TEXT = """\
IONOMOS — what it does
  Lab members drag an experiment folder (raw files inside) into the INBOX.
  Ionomos waits for the copy to finish, works out WHO it belongs to and WHICH
  METHOD (isoDTB / TMT / DIA) from the names, moves it to
  <users_root>/<user>/<experiment>/ and queues it for FragPipe.
  If it can't tell, a small window asks you.

HELP FOR THE LAB
  The Help button (bottom right, or the button above) opens the full help in
  the browser: naming folders, the review window, reading the report chart by
  chart, a glossary, what every message and pop-up means, and what Ionomos
  never does to your data. Same page: ionomos help --open. Every report also
  has a Help section and a "?" beside each part. This tab is about setting up.

THE TABS
  Daily:       Today (what needs you, the recent jobs), Inbox (what is waiting
               to be taken in), Jobs (every search), Analysis (re-run an
               analysis, the lab's defaults, figure style, accuracy checks).
  Setting up:  Setup (Check everything), Folders, Users, Methods, Advanced,
               Run & Test (the watcher; Testing: the testbed), Notifications.
  Help:        this tab. Ctrl+1 … Ctrl+9 jump to the tab in that position.

START HERE: the Setup tab
  "Check everything": one list of everything Ionomos needs (folders, people,
  FragPipe, workflows + FASTA with decoys, disk space, watcher, startup task),
  what FragPipe's own files say, newer workflows and FASTAs, crash reports,
  and what needs you. Each open item says what to do and has a button where
  the app can do it. "Auto-setup" does the standard layout in one click.

FIRST-TIME SETUP (the set-up tabs, left to right)
  Folders     Pick or create the inbox (the shared folder people drop into),
              the users folder, and the working folders. "Create all missing"
              makes them in one click. Point fragpipe_exe at FragPipe's
              headless launcher (only needed once searches are enabled).
  Users       One subfolder per person under users_root. Add the initials each
              person uses in folder names so "IJD05_isoDTB" -> Isaac.
  Methods     For each method: which .workflow file (exported from the FragPipe
              GUI via Workflow -> Save to custom folder, AFTER setting the FASTA)
              and which FASTA. Keywords are what Ionomos looks for in names.
  Advanced    Timings, FragPipe threads/RAM, the resolver window, the config
              file (Undo last save, Restore a previous config…).
  Run & Test  Start the watcher and install the startup task so it runs every
              time this account logs in. The watcher also runs FragPipe on
              each filed experiment, one at a time (Advanced -> "Run FragPipe
              automatically" turns that off). Save & Check is in the bottom bar.
  Then drop a test folder into the inbox and watch it move.

SAVING
  Nothing is written until you press Save. Save first shows what will change,
  in words (Cancel goes back). The version before each Save is kept in
  config-backups/: Advanced -> Undo last save brings the newest one back into
  the window, Restore a previous config… lists them all. The title shows a
  dot while the window has unsaved changes; closing, Reload and Change… ask.

NAMING RULES (tell the lab)
  Folder: anything containing your initials and isoDTB / TMT / DIA.
          e.g. 20260902_EJQ_isoDTB_EJQ-2-027_1uM-3h
  Files:  isoDTB  <sample>_<rep>_<fraction>.raw     (X_1_1.raw ... X_3_7.raw)
          TMT     <sample>_TMT_F<fraction>.raw      (all reps are in channels)
          DIA     <condition>_<biorep>.raw          (DMSO_1.raw, Drug_2.raw)
  Spaces are fine — they are removed on the way in.
  Just the .raw files (no folder) is fine too: Ionomos groups them by name into
  a folder. Xcalibur's _YYYYMMDDhhmmss suffix on a re-acquired file is ignored.
  If Ionomos can't work it out, a window pops up. Answers are saved in an
  experiment.yaml inside the folder, so it never asks the same thing twice.

TESTING WITHOUT REAL DATA
  Run & Test -> Testing: "Create testbed" builds a fake lab with sample
  drops and a fake FragPipe. Start the testbed watcher, then Drop samples and
  watch them move (or open the folders and drag them yourself).

UPDATES
  Ionomos checks GitHub when it opens and every 6 hours. A new version shows
  as "Update to x.y.z" in the bottom bar: one click downloads it (checksum
  checked), stops the watcher cleanly, installs, reopens, restarts the watcher.

UPDATING A DEVELOPMENT INSTALL (git checkout)
  If this app was installed with deploy\\dev_install.ps1 it runs straight from
  a copy of the GitHub repository, and Run & Test -> Testing shows a
  "Development install" box. When a fix is pushed from the Mac, press
  "Update from GitHub & restart" — that is the whole upgrade. Settings, the
  ledger and lab data are never touched by an update.

REPORTING A PROBLEM
  "Report a problem…" (bottom right) -> a sentence, the jobs, Save the zip.
  One file on the Desktop with the report, config, logs, crash reports and
  the search logs of the chosen jobs (never raw data); tick the result
  tables when the numbers are to be checked. Names are replaced by
  pseudonyms; the KEY file saved next to the zip stays in the lab. Nothing
  is sent: copy the zip yourself. "Copy diagnostics" (Run & Test tab) is the
  text only, with the names replaced the same way ("Copy with real names"
  keeps them, for use inside the lab).

FRAGPIPE SEARCHES
  Each filed experiment is searched with its method's workflow + FASTA.
  Inside the experiment folder you get:
    ionomos_run/   the manifest, the workflow as used, FragPipe's console log
    fragpipe/       FragPipe's output
    DONE.txt or FAILED.txt   one-line result (FAILED says why)
  A job "waiting: ..." is missing something outside the job (FragPipe
  launcher, workflow, FASTA, free disk space) and starts by itself once
  that's fixed. Re-runs keep old output as fragpipe_previous_<time>/.

RESULTS: FRAGPIPE-ANALYST STATISTICS + INTERACTIVE REPORT (the Analysis tab)
  After each search, results/ inside the experiment folder gets:
    report.html                 open this — interactive: volcano/MA plot with live
                                cut-offs (click a protein for its values; imputed = hollow),
                                results table (CSV), heatmap of hits, enrichment, QC
                                (PCA, correlation, missing values, CV), methods text
    <comparison>_differential.tsv   every protein/site: log2FC, 95% CI, p, adjusted p, up/down
    <level>_results.tsv         all comparisons side by side + the values used
    enrichment.tsv              pathways / GO terms over-represented among the hits
    volcano_<comparison>.svg    the plot on its own (slides, papers)
    fragpipe-analyst/           experiment_annotation.tsv + reproduce_in_R.R (same analysis
                                in FragPipeAnalystR / FragPipe-Analyst, to cross-check)
    <sample>_sites.tsv          isoDTB sites — identical to the lab's R script
    experimental_annotation.tsv TMT — identical to the lab's R script
  The statistics are FragPipe-Analyst's (ported from FragPipeAnalystR): filter
  (measured in >= 50% of one condition), median normalisation, Perseus-type
  imputation for label-free data (none for TMT), limma, BH. Conditions come
  from the file names (DMSO_1.raw -> DMSO); the control is recognised by name
  (DMSO, vehicle, ctrl, WT, ...) and every other condition is compared with it.
  isoDTB ratios are tested against 0.
  Analysis tab -> Analyse an experiment: pick a finished job (or any FragPipe
  output folder), fix a sample's condition, leave a failed run out, choose the
  comparisons (vs control / all pairs / vs others / just these) and cut-offs
  for that experiment, Run. The choices are saved in its experiment.yaml.
  Analysis tab -> Lab defaults: the settings used after every search
  ("Use FragPipe-Analyst's defaults" = exactly theirs).
  Analysis tab -> Figure style: the lab's size, text and colours for exported
  figures, and which figures are written after each analysis (results/figures).
  Analysis tab -> Check accuracy: Compare an analysis with a reference result
  (FragPipe-Analyst, limma, Perseus, another run) and Benchmark it on simulated
  data or a benchmark sample; the page opens when done.
  Jobs tab -> Re-run analysis / Analysis options… for a finished job.
  Enrichment downloads gene-set libraries from Enrichr once; after that it
  runs offline on this PC (gene lists are never sent anywhere).

NOTIFICATIONS (the Notifications tab)
  A message (Teams, Slack, a webhook, email) when a search is done, failed or
  waiting. Off by default. Fill in a channel, press Send test, then Save and
  restart the watcher. Addresses and the password are masked and kept out of
  logs and diagnostics.

WHEN SOMETHING NEEDS YOU (pop-up windows)
  Ionomos opens a window by itself when it can't go on without a person, and
  the bottom bar shows "⚠ N need attention" until it's dealt with:
    • an analysis needs a decision — e.g. every sample landed in one condition,
      no condition looks like a control, a group has one sample, a run looks
      like a failed injection. The window has the experiment editor: fix the
      conditions ("Guess from names" helps), leave a run out, pick the control,
      Run analysis. The answer is saved in the experiment's experiment.yaml.
    • an analysis had a problem — no result table (the likely FragPipe causes
      are listed), a step crashed. Re-run analysis / FragPipe log / Report a problem.
    • a FragPipe search failed — the most likely cause in plain English, the end
      of FragPipe's log, Retry search.
    • a search is waiting (no FASTA, no workflow, low disk) — what's missing.
    • a dropped folder couldn't be taken in — why, and the inbox.
  Windows close by themselves once the cause is fixed. "Remind me in an hour"
  or Dismiss if it can wait. When the app is closed the watcher shows the same
  windows. Turn pop-ups off in Advanced (the list stays).
  Every analysis also checks itself: the report starts with the issues found,
  and every comparison always gets a volcano plot (an empty one says why).

THE JOBS TAB
  Every job, live: status, how long it ran, and what FragPipe is doing now.
  Find narrows the list (a word, a user, a method, a job number). Select one
  to see details and — for a failed job — the MOST LIKELY CAUSE in plain
  English (out of memory, MSFragger not installed, disk full, file open in
  another program, ...). Buttons: Open report, Open folder, FragPipe log,
  Re-run analysis, Analysis options…, Retry, Cancel (stops a running search),
  Copy details, Zip for troubleshooting….
  Pause searches: no new FragPipe runs start (the running one finishes) —
  e.g. while someone needs the PC. Resume to continue.
  Check FragPipe install: finds FragPipe's version, bundled Java, MSFragger,
  IonQuant, diaTracer, DIA-NN and Python, then starts FragPipe itself (no
  window, no search) for its version and a dry run of each method: FragPipe
  checks the workflow, the FASTA (decoys) and its tools and says what it
  would run. Takes up to a few minutes. Same as: ionomos-cli.exe preflight

METHODS TAB: IMPORT WORKFLOW
  Select a method -> Import workflow… -> pick a .workflow file (e.g. the
  fragpipe.workflow inside a run that worked). It is copied in as
  <method>.workflow and its FASTA is copied to the FASTA folder too. The
  readiness line under the form shows ✓/!/✗ for workflow and FASTA.

SAFETY NETS (automatic)
  - Only one watcher can run per setup; a second one refuses to start.
  - If part of the watcher crashes it restarts itself; crash reports go to
    logs/crash-*.txt. The app shows NOT RESPONDING if it hangs.
  - Stopping the watcher stops FragPipe cleanly and re-queues the job.
  - The job list is backed up daily (logs/backups) and rebuilt from the
    experiment folders if it's ever damaged (ionomos repair-ledger).
  - Every Save keeps the previous config in config-backups/
    (Advanced -> Restore a previous config…). An invalid config is never
    written over a good one.

WHERE THINGS ARE
  config.yaml        all settings (path shown at the bottom of this window)
  logs/ionomos.log  what the watcher did
  ionomos.db        job ledger (ionomos status)
  <folder>/ionomos.json    status + provenance next to each experiment
  <inbox>/<name>.REJECTED.txt   why a folder was not taken; fix it or delete the note

COMMAND LINE (same program; ionomos-cli.exe on Windows)
  ionomos check | status | dry-run <folder> | run | retry <id> | cancel <id>
  ionomos help [topic] [--open]   e.g. ionomos help NO_TABLE, ionomos help pca
  ionomos pause | resume | diagnose [--zip] | repair-ledger | testbed ...
  ionomos testbed stress   many messy drops + chaos, then checks nothing was lost
  ionomos doctor           the Setup tab's Check everything list, in a terminal
  ionomos compare | benchmark | export | notify-test   (also buttons on Analysis and Notifications)
"""


# ------------------------------------------------------------- widgets -----


class PathRow:
    """Label + entry + Browse (+ Create for dirs) + a green/red existence dot."""

    def __init__(self, parent, row: int, label: str, var: tk.StringVar, kind: str, hint: str = "",
                 on_change: Callable[[], None] | None = None):
        self.var, self.kind = var, kind
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="e", **PAD)
        self.entry = ttk.Entry(parent, textvariable=var, width=58)
        self.entry.grid(row=row, column=1, sticky="ew", **PAD)
        self.dot = ttk.Label(parent, text="●", width=2)
        self.dot.grid(row=row, column=2, sticky="w")
        ttk.Button(parent, text="Browse…", width=9, command=self.browse).grid(row=row, column=3, **PAD)
        if kind == "dir":
            ttk.Button(parent, text="Create", width=7, command=self.create).grid(row=row, column=4, **PAD)
        if hint:
            ttk.Label(parent, text=hint, foreground="#666").grid(row=row + 1, column=1, columnspan=4, sticky="w", padx=6)
        var.trace_add("write", lambda *_: (self.refresh(), on_change() if on_change else None))
        self.refresh()

    def path(self) -> Path:
        return Path(self.var.get().strip())

    def refresh(self):
        p = self.var.get().strip()
        if not p:
            self.dot.configure(foreground="#999")
            return
        ok = Path(p).is_dir() if self.kind == "dir" else (Path(p).is_file() if self.kind == "file" else Path(p).parent.is_dir())
        self.dot.configure(foreground="#2e7d32" if ok else "#c62828")

    def browse(self):
        cur = self.var.get().strip()
        start = cur if cur and Path(cur).exists() else (str(Path(cur).parent) if cur else str(Path.home()))
        if self.kind == "dir":
            r = filedialog.askdirectory(initialdir=start, mustexist=False)
        elif self.kind == "file":
            r = filedialog.askopenfilename(initialdir=start)
        else:
            r = filedialog.asksaveasfilename(initialdir=start, initialfile=Path(cur).name if cur else "")
        if r:
            self.var.set(r.replace("\\", "/"))

    def create(self):
        p = self.var.get().strip()
        if not p:
            return
        try:
            Path(p).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("Create folder", f"Could not create {p}:\n{exc}\n\n→ Pick a folder this Windows account "
                                                  "can write to (Browse…), or create it in Explorer, then try again.")
        self.refresh()


class OutputPane:
    def __init__(self, parent, height=12):
        self.text = scrolledtext.ScrolledText(parent, height=height, wrap="word", font=("Menlo" if sys.platform == "darwin" else "Consolas", 10))
        self.text.configure(state="disabled")

    def grid(self, **kw):
        self.text.grid(**kw)

    def write(self, s: str, clear: bool = False):
        self.text.configure(state="normal")
        if clear:
            self.text.delete("1.0", "end")
        self.text.insert("end", s if s.endswith("\n") else s + "\n")
        self.text.see("end")
        self.text.configure(state="disabled")


# ----------------------------------------------------------------- app -----


class App:
    def __init__(self, root: tk.Tk, config_path: Path | None = None):
        self.root = root
        self.config_path = Path(config_path) if config_path else service.default_config_path()
        try:
            moved = service.relocate_config_from_program_dir(self.config_path)
        except OSError as exc:
            moved = None
            log.warning("could not move the config out of the program folder: %s", exc)
        if moved:
            log.info("moved the config from %s to %s", self.config_path, moved)
            self.config_path = moved
        self.first_run = not self.config_path.is_file()
        self.data, self.config_error = self._read_config(self.config_path)
        self.proc = None  # child watcher process started from here
        self.proc_config: Path | None = None
        self.vars: dict[str, tk.Variable] = {}
        self._follow_job = None
        self._follow_gen = 0
        self._ui_q: queue.Queue = queue.Queue()  # worker threads post callables here
        self._bg: dict[str, object] = {}  # background work in flight: label -> the button that started it (run_bg)
        self._status_busy = False
        self._saved_text = ""  # config.yaml as last read or written (dirty(): the window differs from the file)

        root.title(f"Ionomos {__version__} — setup & control")
        root.minsize(900, 640)
        try:
            ttk.Style().theme_use("vista" if IS_WIN else "aqua" if sys.platform == "darwin" else "clam")
        except tk.TclError:
            pass

        self.nb = ttk.Notebook(root)
        self.nb.pack(fill="both", expand=True, padx=8, pady=(8, 0))
        self._tab_today()
        self._tab_setup()
        self._tab_folders()
        self._tab_users()
        self._tab_methods()
        self._tab_advanced()
        self._tab_run()
        self._tab_inbox()
        self._tab_jobs()
        self._tab_analysis()
        self._tab_notify()
        self._tab_help()
        self._order_tabs()  # the row's order and labels come from uinav.TABS, not from the build order above
        self._bottom_bar()
        self._load_vars()
        self._saved_text = configio.dump_config(self.data)
        self._refresh_users()
        self._refresh_methods()
        self._refresh_status()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.report_callback_exception = self._on_tk_error
        root.after(100, self._pump_ui)
        self._restore_ui_state()  # a new install opens on the checklist; afterwards the last tab, else Today
        self.nb.bind("<<NotebookTabChanged>>", lambda e: self.refresh_setup() if self.nb.select() == str(self.tab_setup) else None,
                     add="+")
        self._bind_keys()
        self.root.after(300, self.refresh_setup)  # the quick check on start (S3.2); Today shows its one line
        self.root.after(600, self._after_update_restart)
        self.root.after(2500, self.check_downloaded_update)
        self._start_popups()
        self._start_drops()
        if self.config_error:
            self.root.after(400, self._say_config_broken)
        log.info("app started: %s, config %s", __import__("ionomos.buildinfo", fromlist=["x"]).one_line(), self.config_path)

    @staticmethod
    def _read_config(path: Path) -> tuple[dict, str | None]:
        """config.yaml as a dict, plus the parse error when the file does not parse (#136): then the defaults,
        so the app opens, shows the error and the file is left as it is."""
        try:
            return configio.read_config(path), None
        except ConfigError as exc:
            log.warning("%s", exc)
            return configio.defaults(), str(exc)

    def _say_config_broken(self) -> None:
        """At start, on a config.yaml that does not parse: the error in the status bar and a dialog that offers
        Restore a previous config when there are backups, else says how to fix the file."""
        msg = self.config_error or ""
        self.set_status(f"{msg} (the file is untouched; the settings shown are the defaults)", ok=False)
        self.nb.select(self.tab_setup)
        backups = sorted((self.config_path.parent / configio.BACKUP_DIR).glob("config-*.yaml"))
        if backups and messagebox.askyesno(
                "Config problem", f"{msg}\n\nThe file was left as it is, and the settings shown are the defaults.\n"
                f"Restore a previous config now? ({len(backups)} saved in {configio.BACKUP_DIR}/; the newest is "
                f"{backups[-1].name}.) Press Save afterwards to make it the active config."):
            self.restore_config()
        elif not backups:
            messagebox.showerror("Config problem", f"{msg}\n\nThe file was left as it is, and the settings shown "
                                 "are the defaults. Fix that line in a text editor and press Reload, or press Save "
                                 "to write the settings shown over it (a backup of the file is kept).")

    def post(self, fn) -> None:
        """Thread-safe: run fn on the Tk thread soon."""
        self._ui_q.put(fn)

    def run_bg(self, label: str, fn: Callable[[], object], done: Callable[[object, BaseException | None], None] | None = None,
               *, key: str | None = None, button=None) -> bool:
        """Run fn on a worker thread with one busy indicator (docs/UI.md S2.3, D67's rule in one place): the bottom
        bar shows "working: <label>…" with a progress bar while it runs, the button that started it is disabled, and
        a second start of the same work is refused (False). done(result, error) runs on the Tk thread afterwards;
        without it, an error goes to the status line. Nothing here touches Tk off the Tk thread."""
        key = key or label
        if key in self._bg:
            self.set_status(f"still working: {label}… — wait for it to finish", ok=False)
            return False
        self._bg[key] = (label, button)
        if button is not None:
            try:
                button.configure(state="disabled")
            except tk.TclError:
                pass
        self._show_busy()

        def go():
            result, err = None, None
            try:
                result = fn()
            except Exception as exc:  # noqa: BLE001 - reported to done() / the status line, never lost
                err = exc
                log.warning("background work failed: %s: %s", label, exc, exc_info=True)

            def finish():
                self._bg.pop(key, None)
                if button is not None:
                    try:
                        button.configure(state="normal")
                    except tk.TclError:
                        pass
                self._show_busy()
                if done is not None:
                    done(result, err)
                elif err is not None:
                    self.set_status(f"{label} failed: {err}", ok=False)

            self.post(finish)

        threading.Thread(target=go, daemon=True, name=f"app-{key}").start()
        return True

    def busy(self, key: str) -> bool:
        """Whether run_bg work with this key (or label) is still in flight."""
        return key in self._bg

    def _show_busy(self) -> None:
        labels = [label for label, _button in self._bg.values()]
        try:
            if labels:
                self.busy_lbl.configure(text="working: " + ", ".join(labels) + "…")
                if not self.busy_lbl.winfo_manager():  # (winfo_ismapped is False while the window is withdrawn)
                    self.busy_lbl.pack(side="left", padx=(12, 4))
                    self.busy_bar.pack(side="left")
                    self.busy_bar.start(80)
            elif self.busy_lbl.winfo_manager():
                self.busy_bar.stop()
                self.busy_bar.pack_forget()
                self.busy_lbl.pack_forget()
        except (AttributeError, tk.TclError):
            pass  # before the bottom bar exists, or while the window closes

    def fail(self, title: str, what: str, how: str, topic: str | None = None) -> None:
        """An error box that says what failed and what to do next (D46, S2.1); with a help topic it offers the help
        page for it."""
        if topic:
            if messagebox.askyesno(title, f"{what}\n\n→ {how}\n\nOpen the help for this?"):
                self.open_help(topic)
        else:
            messagebox.showerror(title, f"{what}\n\n→ {how}")

    def _pump_ui(self):
        try:
            while True:
                fn = self._ui_q.get_nowait()
                try:
                    fn()
                except Exception:  # noqa: BLE001 - one bad callback must not stop the pump
                    self._on_tk_error(*sys.exc_info())
        except queue.Empty:
            pass
        self.root.after(100, self._pump_ui)

    def _on_tk_error(self, exc_type, exc, tb):
        """Any error in a button/callback: log it, save a crash report, tell the user — never die silently."""
        import traceback

        from ionomos import health

        text = "".join(traceback.format_exception(exc_type, exc, tb))
        log_dir = None
        try:
            log_dir = self._log_dir()
        except Exception:  # noqa: BLE001
            pass
        log.error("app error\n%s", text)
        where = health.write_crash_file(log_dir if log_dir and log_dir.is_dir() else None, "app", text)
        try:
            self.out.write("INTERNAL ERROR (the app keeps running):\n" + text, clear=True)
        except Exception:  # noqa: BLE001
            pass
        messagebox.showerror("Ionomos — something went wrong",
                             f"{exc_type.__name__}: {exc}\n\nThe app keeps running. Details"
                             + (f" were saved to\n{where}" if where else " are in the output pane on Run & Test")
                             + ".\nIf it keeps happening: Run & Test -> Save diagnostics bundle, and send it.")

    # -------------------------------------------------------- helpers ----

    def v(self, key: str, default="") -> tk.StringVar:
        if key not in self.vars:
            self.vars[key] = tkutil.StringVar(value=str(default))
        return self.vars[key]

    def bv(self, key: str, default=False) -> tk.BooleanVar:
        if key not in self.vars:
            self.vars[key] = tkutil.BooleanVar(value=bool(default))
        return self.vars[key]

    def _load_vars(self):
        d = self.data
        for k, val in d["paths"].items():
            self.v(f"paths.{k}").set(val)
        for sec in ("watcher", "fragpipe", "gui"):
            for k, val in d[sec].items():
                if isinstance(val, bool):
                    self.bv(f"{sec}.{k}").set(val)
                else:
                    self.v(f"{sec}.{k}").set("" if val is None else str(val))
        self.analysis.load_vars(d)
        self.notify_tab.load_vars(d)
        self.v("users.default").set(d["users"].get("default", "") or "")
        self.v("users.learned_aliases_file").set(d["users"].get("learned_aliases_file", "") or "")
        self.v("config_path").set(str(self.config_path))

    def _collect(self, base: dict | None = None) -> dict:
        """The settings as the window shows them, over the file on disk (keys the window has no field for are kept;
        a file that does not parse counts as the defaults, #136). base: start from this dict instead of reading the
        disk (dirty() uses the dict last loaded)."""
        if base is not None:
            d = base
        else:
            d = self._read_config(self.config_path)[0] if self.config_path.is_file() else configio.defaults()
        d["paths"] = {k: self.v(f"paths.{k}").get().strip().replace("\\", "/") for k in d["paths"]}
        for sec, keys in (("watcher", ("poll_seconds", "stable_seconds", "min_raw_files")),
                          ("fragpipe", ("threads", "ram_gb", "timeout_minutes", "min_free_gb"))):
            for k in keys:
                raw = self.v(f"{sec}.{k}").get().strip()
                try:
                    d[sec][k] = float(raw) if "." in raw else int(raw)
                except ValueError:
                    raise ConfigError(f"{sec}.{k} must be a number, got {raw!r}") from None
        d["fragpipe"]["auto_run"] = self.bv("fragpipe.auto_run", True).get()
        d["fragpipe"]["config_tools_folder"] = self.v("fragpipe.config_tools_folder").get().strip()
        d["fragpipe"]["config_diann"] = self.v("fragpipe.config_diann").get().strip()
        d["fragpipe"]["config_python"] = self.v("fragpipe.config_python").get().strip()
        d["watcher"]["group_loose_files"] = self.bv("watcher.group_loose_files", True).get()
        d["gui"]["enabled"] = self.bv("gui.enabled").get()
        d["gui"]["popups"] = self.bv("gui.popups", True).get()
        try:
            d["gui"]["timeout_minutes"] = float(self.v("gui.timeout_minutes").get().strip() or 0)
        except ValueError:
            raise ConfigError("gui.timeout_minutes must be a number") from None
        d["users"]["aliases"] = {u: list(a) for u, a in self.data["users"].get("aliases", {}).items() if a}
        d["users"]["default"] = self.v("users.default").get().strip()
        self.analysis.collect(d)
        self.notify_tab.collect(d)
        d["users"]["learned_aliases_file"] = self.v("users.learned_aliases_file").get().strip()
        d["methods"] = self.data["methods"]
        return d

    def set_status(self, msg: str, ok: bool = True):
        self.status_lbl.configure(text=msg, foreground="#2e7d32" if ok else "#c62828")

    # -- unsaved changes (S2.6) and what Save will change (S2.5)

    def changes(self) -> list[str]:
        """What Save would change, in words (configio.diff_words): the file on disk against the window."""
        old = self._read_config(self.config_path)[0] if self.config_path.is_file() else configio.defaults()
        return configio.diff_words(old, self._collect())

    def dirty(self) -> bool:
        """Whether the window shows something other than config.yaml as it was last read or written. Cheap enough
        for the 2 s tick: no disk read (the dict last loaded is the base); a field that doesn't parse counts."""
        try:
            return configio.dump_config(self._collect(base=copy.deepcopy(self.data))) != self._saved_text
        except ConfigError:
            return True
        except Exception:  # noqa: BLE001 - a half-built window; never a crash from a status tick
            return False

    def _update_title(self) -> None:
        try:
            self.root.title(f"Ionomos {__version__} — setup & control" + ("  •" if self.dirty() else ""))
        except tk.TclError:
            pass

    def _guard_unsaved(self, before: str) -> bool:
        """Before quitting, reloading or switching the config: Save, Discard or Cancel. True = go on."""
        if not self.dirty():
            return True
        ans = messagebox.askyesnocancel("Unsaved changes", f"The window has changes that are not saved.\n\n"
                                        f"Save them before {before}?\n\nYes = Save first, No = discard them, "
                                        "Cancel = stay here.")
        if ans is None:
            return False
        if ans:
            return self.save()
        return True

    # -------------------------------------------------------- tab: today ----

    _TCOLS = (("id", 40), ("status", 90), ("user", 80), ("method", 72), ("experiment", 300), ("when", 115),
              ("note", 380))

    def _tab_today(self):
        """What needs a person, and the recent jobs with one-click report / folder (uinav.snapshot, off Tk)."""
        f = ttk.Frame(self.nb, padding=12)
        self.tab_today = f
        self.nb.add(f, text="  Today  ")
        f.columnconfigure(0, weight=1)
        f.rowconfigure(5, weight=1)
        top = ttk.Frame(f)
        top.grid(row=0, column=0, sticky="ew")
        ttk.Label(top, text="Today", font=("", 15, "bold")).pack(side="left")
        ttk.Button(top, text="Refresh", command=self.refresh_today).pack(side="right", padx=4)
        ttk.Button(top, text="Help page", command=lambda: self.open_help("faq.app")).pack(side="right", padx=4)
        self.today_head = ttk.Label(f, text="reading…", font=("", 11), wraplength=900, justify="left")
        self.today_head.grid(row=1, column=0, sticky="w", pady=(4, 2))
        st = ttk.Frame(f)
        st.grid(row=2, column=0, sticky="ew")
        st.columnconfigure(0, weight=1)
        self.today_watcher = ttk.Label(st, text="", wraplength=900, justify="left")
        self.today_watcher.grid(row=0, column=0, sticky="w")
        ttk.Button(st, text="Run & Test", command=lambda: self.nb.select(self.tab_run)).grid(row=0, column=1, padx=4)
        self.today_setup = ttk.Label(st, text="", wraplength=900, justify="left")
        self.today_setup.grid(row=1, column=0, sticky="w")
        ttk.Button(st, text="Check everything", command=lambda: self.nb.select(self.tab_setup)).grid(
            row=1, column=1, padx=4, pady=2)

        a = ttk.LabelFrame(f, text="Needs you", padding=6)
        a.grid(row=3, column=0, sticky="ew", pady=(8, 4))
        a.columnconfigure(0, weight=1)
        from ionomos.popups import KIND_TITLE, SEV_COLOUR

        self._kind_title = KIND_TITLE
        self.today_attn = ttk.Treeview(a, columns=("what", "title", "since"), show="headings", height=4,
                                       selectmode="browse")
        for c, w in (("what", 230), ("title", 520), ("since", 160)):
            self.today_attn.heading(c, text=c)
            self.today_attn.column(c, width=w, anchor="w", stretch=c == "title")
        for sev, colour in SEV_COLOUR.items():
            self.today_attn.tag_configure(sev, foreground=colour)
        self.today_attn.grid(row=0, column=0, sticky="ew")
        self.today_attn.bind("<Double-1>", lambda e: self._today_open_item())
        self.today_attn.bind("<Return>", lambda e: self._today_open_item())
        ab = ttk.Frame(a)
        ab.grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Button(ab, text="Open", command=self._today_open_item).pack(side="left", padx=4)
        ttk.Button(ab, text="The whole list…", command=self.show_attention).pack(side="left", padx=4)
        ttk.Label(ab, text="double-click = open the window with what to do; items close by themselves once fixed",
                  foreground="#666").pack(side="left", padx=12)

        j = ttk.LabelFrame(f, text="Recent jobs", padding=6)
        j.grid(row=5, column=0, sticky="nsew", pady=4)
        j.columnconfigure(0, weight=1)
        j.rowconfigure(0, weight=1)
        self.today_jobs = ttk.Treeview(j, columns=[c for c, _ in self._TCOLS], show="headings", height=8,
                                       selectmode="browse")
        for c, w in self._TCOLS:
            self.today_jobs.heading(c, text=c)
            self.today_jobs.column(c, width=w, anchor="w", stretch=c in ("experiment", "note"))
        for tag, color in (("failed", "#c62828"), ("running", "#1565c0"), ("waiting", "#b26a00"),
                           ("done", "#2e7d32"), ("needs a look", "#b26a00")):
            self.today_jobs.tag_configure(tag, foreground=color)
        self.today_jobs.grid(row=0, column=0, sticky="nsew")
        self.today_jobs.bind("<Double-1>", lambda e: self._today_job("report"))
        self.today_jobs.bind("<Return>", lambda e: self._today_job("report"))
        jb = ttk.Frame(j)
        jb.grid(row=1, column=0, sticky="w", pady=(4, 0))
        for text, action in (("Open report", "report"), ("Open folder", "folder"), ("Re-run analysis", "analyze"),
                             ("Analysis options…", "studio"), ("Show in Jobs", "show")):
            ttk.Button(jb, text=text, command=lambda x=action: self._today_job(x)).pack(side="left", padx=4)
        ttk.Button(jb, text="Open inbox", command=lambda: self._open(self.v("paths.inbox").get())).pack(
            side="left", padx=(16, 4))
        ttk.Label(jb, text="double-click = open the report", foreground="#666").pack(side="left", padx=12)
        self._today_rows: dict[str, object] = {}
        self._today_busy = False
        self._last_setup_text = ""
        self._last_setup_problems = 0
        self.nb.bind("<<NotebookTabChanged>>", lambda e: self.refresh_today() if self._current_tab() == "today"
                     else None, add="+")
        self.root.after(1200, self._today_tick)

    def _today_tick(self):
        try:
            if self._current_tab() == "today":
                self.refresh_today()
        except Exception:  # noqa: BLE001 - never stop the refresh loop
            log.exception("today refresh failed")
        self.root.after(4000, self._today_tick)

    def refresh_today(self):
        """Read the attention list and the ledger on a thread; draw on the Tk thread (App.post)."""
        from ionomos import uinav

        try:
            self.today_watcher.configure(text=f"{self.run_status.cget('text')}   ·   {self.fp_status.cget('text')}",
                                         foreground=self.run_status.cget("foreground"))
        except (AttributeError, tk.TclError):
            pass
        if self._today_busy:
            return
        self._today_busy = True
        log_dir, db = self._active_log_dir(), self._active_paths().get("database")

        def go():
            try:
                snap = uinav.snapshot(log_dir, db)
            except Exception as exc:  # noqa: BLE001 - snapshot never raises; belt and braces
                snap = uinav.Snapshot(problem=str(exc))
            self.post(lambda: self._show_today(snap))

        threading.Thread(target=go, daemon=True).start()

    def _show_today(self, snap):
        self._today_busy = False
        try:
            self.today_head.configure(text=snap.headline() + (f"  ({snap.problem})" if snap.problem else ""),
                                      foreground="#c62828" if any(i.severity == "error" for i in snap.attention)
                                      else "#b26a00" if snap.attention else "")
            self.today_setup.configure(text=f"Checks: {self._last_setup_text}" if self._last_setup_text else "",
                                       foreground="#c62828" if self._last_setup_problems else "")
            sel = self.today_attn.selection()
            self.today_attn.delete(*self.today_attn.get_children())
            for it in snap.attention:
                self.today_attn.insert("", "end", iid=it.id, tags=(it.severity,), values=(
                    self._kind_title.get(it.kind, it.kind), it.title, (it.created or "").replace("T", " ")[:16]))
            if sel and self.today_attn.exists(sel[0]):
                self.today_attn.selection_set(sel[0])
            sel = self.today_jobs.selection()
            self.today_jobs.delete(*self.today_jobs.get_children())
            self._today_rows = {}
            for r in snap.jobs:
                self._today_rows[str(r.id)] = r
                self.today_jobs.insert("", "end", iid=str(r.id), tags=(r.state,), values=(
                    r.id, r.state, r.user, r.method, r.experiment, r.when, r.note))
            if sel and self.today_jobs.exists(sel[0]):
                self.today_jobs.selection_set(sel[0])
        except tk.TclError:
            pass  # the window is closing

    def _today_open_item(self):
        from ionomos import attention

        sel = self.today_attn.selection()
        it = attention.get(self._active_log_dir(), sel[0]) if sel else None
        if it is None:
            messagebox.showinfo("Today", "Select an item first.")
            return
        self.popups.show(it)

    def _today_job(self, action: str):
        sel = self.today_jobs.selection()
        row = self._today_rows.get(sel[0]) if sel else None
        if row is None:
            messagebox.showinfo("Today", "Select a job first.")
            return
        if action == "show":
            self.nb.select(self.tab_jobs)
            self.v("jobs.find").set("")
            self.refresh_jobs()
            if self.jtree.exists(str(row.id)):
                self.jtree.selection_set(str(row.id))
                self.jtree.see(str(row.id))
            return
        self.job_action(action, job=row.job)

    # ------------------------------------------------- navigation and keys ----

    def _tab_frames(self) -> dict:
        """uinav.TABS key -> the tab's frame (the keys ionomos-ui.json remembers)."""
        return {k: getattr(self, f"tab_{a}", None) for k, a in (
            ("today", "today"), ("setup", "setup"), ("folders", "folders"), ("users", "users"), ("methods", "methods"),
            ("advanced", "advanced"), ("run", "run"), ("inbox", "inbox"), ("jobs", "jobs"), ("analysis", "analysis"),
            ("notify", "notify"), ("help", "help"))}

    def _current_tab(self) -> str | None:
        try:
            cur = self.nb.select()
        except tk.TclError:
            return None
        return next((k for k, w in self._tab_frames().items() if w is not None and str(w) == cur), None)

    def select_tab(self, key: str) -> None:
        w = self._tab_frames().get(key)
        if w is not None:
            self.nb.select(w)

    def _order_tabs(self) -> None:
        """The tab row in uinav.TABS' order with uinav's labels, whatever order the tabs were built in (S1.6)."""
        from ionomos import uinav

        frames = self._tab_frames()
        for i, key in enumerate(uinav.TAB_KEYS):
            w = frames.get(key)
            if w is not None:
                self.nb.insert(i, w)
                self.nb.tab(w, text=uinav.tab_label(key))

    def _bind_keys(self) -> None:
        """Keyboard shortcuts (uinav.SHORTCUTS), on the main window only: a pop-up keeps its own keys."""
        from ionomos import uinav

        def key(fn):
            def handler(_e=None):
                fn()
                return "break"
            return handler

        self.nb.enable_traversal()  # Ctrl+Tab / Ctrl+Shift+Tab
        self._keys = {"<F1>": key(lambda: self.open_help(uinav.tab_help(self._current_tab()))),
                      "<F5>": key(self.refresh_current_tab), "<Control-s>": key(lambda: self.save(confirm=True)),
                      "<Control-f>": key(self.find_job)}
        for digit, tab in uinav.DIGIT_TABS.items():
            self._keys[f"<Control-Key-{digit}>"] = key(lambda t=tab: self.select_tab(t))
        for event, handler in self._keys.items():
            self.root.bind(event, handler)

    def find_job(self) -> None:
        self.select_tab("jobs")
        self.jobs_find.focus_set()
        self.jobs_find.select_range(0, "end")

    def refresh_current_tab(self) -> None:
        tab = self._current_tab()
        if tab == "today":
            self.refresh_today()
        elif tab == "setup":
            self.refresh_setup()
        elif tab == "jobs":
            self.refresh_jobs()
        elif tab == "inbox":
            self._refresh_inbox()
        elif tab == "users":
            self._refresh_users()
        elif tab == "methods":
            self._refresh_methods()
        elif tab == "analysis":
            self.analysis.refresh_jobs()

    def _restore_ui_state(self) -> None:
        """The window's size and the last tab from <config folder>/ionomos-ui.json, made to fit this screen.
        Without one, a window bigger than the screen is shrunk to fit (the bottom bar stays in view)."""
        from ionomos import uinav

        state = uinav.load_state(uinav.state_path(self.config_path))
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        geom = uinav.clamp_geometry(state["geometry"], sw, sh) if state.get("geometry") else None
        if geom:
            self.root.geometry(geom)
        else:
            self.root.update_idletasks()
            rw, rh = self.root.winfo_reqwidth(), self.root.winfo_reqheight()
            w, h = uinav.fit_size(rw, rh, sw, sh)
            if (w, h) != (rw, rh):
                self.root.geometry(f"{w}x{h}+0+0")
        if state.get("zoomed") and IS_WIN:
            try:
                self.root.state("zoomed")
            except tk.TclError:
                pass
        self.select_tab(uinav.start_tab(self.first_run, state.get("tab")))

    def _save_ui_state(self) -> None:
        from ionomos import uinav

        try:
            zoomed = self.root.state() == "zoomed"
            if zoomed:
                self.root.state("normal")  # remember the size it goes back to, not the full screen
            state = {"geometry": self.root.geometry(), "zoomed": zoomed, "tab": self._current_tab() or "today"}
        except tk.TclError:
            return
        uinav.save_state(uinav.state_path(self.config_path), state)

    # ------------------------------------------------------ tab: folders ----

    def _tab_folders(self):
        f = ttk.Frame(self.nb, padding=10)
        self.tab_folders = f
        self.nb.add(f, text="  1  Folders  ")
        f.columnconfigure(1, weight=1)
        r = 0
        if self.first_run:
            ttk.Label(f, text="Welcome — let's set up Ionomos. Pick where things live, then go tab by tab. "
                              "Nothing is written until you press Save.", wraplength=820,
                      font=("", 11, "bold")).grid(row=r, column=0, columnspan=5, sticky="w", **PAD)
            r += 1
        q = ttk.LabelFrame(f, text="Quick fill: standard layout under one folder", padding=6)
        q.grid(row=r, column=0, columnspan=5, sticky="ew", **PAD)
        r += 1
        self.v("quick.root").set(configio.defaults()["paths"]["inbox"].rsplit("/", 1)[0])
        self.v("quick.users").set(configio.defaults()["paths"]["users_root"])
        ttk.Label(q, text="App folder").grid(row=0, column=0, sticky="e", **PAD)
        ttk.Entry(q, textvariable=self.v("quick.root"), width=40).grid(row=0, column=1, **PAD)
        ttk.Label(q, text="Users folder").grid(row=0, column=2, sticky="e", **PAD)
        ttk.Entry(q, textvariable=self.v("quick.users"), width=32).grid(row=0, column=3, **PAD)
        ttk.Button(q, text="Apply layout", command=self.apply_quick).grid(row=0, column=4, **PAD)

        rows = [
            ("paths.inbox", "Inbox (drop folder)", "dir", "The SHARED folder lab members drag experiment folders into. No spaces in the path."),
            ("paths.users_root", "Users folder", "dir", "Experiments are moved to <users folder>/<user>/<experiment>. One subfolder per person (the Users tab)."),
            ("paths.workflow_dir", "Workflows folder", "dir", "Pinned FragPipe .workflow files, one per method (the Methods tab)."),
            ("paths.fasta_dir", "FASTA folder", "dir", "Protein databases (with decoys) the workflows use."),
            ("paths.log_dir", "Logs folder", "dir", ""),
            ("paths.database", "Job ledger (SQLite file)", "save", "Created automatically; its folder must exist."),
            ("paths.fragpipe_exe", "FragPipe launcher", "file", "fragpipe.bat in FragPipe's bin folder, e.g. C:/FragPipe/FragPipe-24.0/bin/fragpipe.bat — 'Find FragPipe' looks for it."),
        ]
        self.path_rows: dict[str, PathRow] = {}
        for key, label, kind, hint in rows:
            self.path_rows[key] = PathRow(f, r, label, self.v(key), kind, hint)
            r += 2
        b = ttk.Frame(f)
        b.grid(row=r, column=0, columnspan=5, sticky="w", **PAD)
        ttk.Button(b, text="Create all missing folders", command=self.create_all).pack(side="left", padx=4)
        ttk.Button(b, text="Find FragPipe", command=self.find_fragpipe).pack(side="left", padx=4)
        ttk.Button(b, text="Open inbox", command=lambda: self._open(self.v("paths.inbox").get())).pack(side="left", padx=4)
        ttk.Button(b, text="Open users folder", command=lambda: self._open(self.v("paths.users_root").get())).pack(side="left", padx=4)

    def find_fragpipe(self):
        from ionomos.fragpipe import detect_launcher

        found = detect_launcher()
        if found:
            self.v("paths.fragpipe_exe").set(str(found).replace("\\", "/"))
            self.set_status(f"found FragPipe: {found} (press Save)")
        else:
            messagebox.showinfo("Find FragPipe", "No fragpipe.bat found under C:/FragPipe or your Downloads.\n"
                                "Use Browse… and pick <FragPipe folder>/bin/fragpipe.bat.")

    def apply_quick(self):
        d = configio.defaults(self.v("quick.root").get().strip(), self.v("quick.users").get().strip())
        for k in ("inbox", "users_root", "workflow_dir", "fasta_dir", "database", "log_dir"):
            self.v(f"paths.{k}").set(d["paths"][k])
        self.v("users.learned_aliases_file").set(d["users"]["learned_aliases_file"])
        self.set_status("layout applied — press Create all missing folders, then Save")

    def create_all(self):
        made = []
        for row in self.path_rows.values():
            p = row.path()
            if not str(p):
                continue
            target = p if row.kind == "dir" else (p.parent if row.kind == "save" else None)
            if target and not target.is_dir():
                try:
                    target.mkdir(parents=True)
                    made.append(str(target))
                except OSError as exc:
                    messagebox.showerror("Create folders", f"Could not create {target}:\n{exc}\n\n→ Pick a folder this "
                                                           "Windows account can write to (Browse…), then press "
                                                           "Create all missing folders again.")
            row.refresh()
        self._refresh_users()
        self.set_status(f"created {len(made)} folder(s)" if made else "all folders already exist")

    def _open(self, p: str):
        if p and service.is_url(str(p)):
            service.open_url(str(p))
        elif p and Path(p).exists():
            service.open_path(Path(p))
        else:
            messagebox.showinfo("Open", f"Does not exist yet:\n{p}")

    # -------------------------------------------------------- tab: users ----

    def _tab_users(self):
        f = ttk.Frame(self.nb, padding=10)
        self.tab_users = f
        self.nb.add(f, text="  2  Users  ")
        f.columnconfigure(1, weight=1)
        f.rowconfigure(1, weight=1)
        ttk.Label(f, text="A user is a subfolder of the users folder. Add the initials each person writes in "
                          "folder names so they are recognised (e.g. IJ, IJD → Isaac).", wraplength=820).grid(
            row=0, column=0, columnspan=2, sticky="w", **PAD)

        left = ttk.LabelFrame(f, text="User folders", padding=6)
        left.grid(row=1, column=0, sticky="nsw", **PAD)
        self.user_list = tk.Listbox(left, height=14, width=26, exportselection=False)
        self.user_list.pack(fill="both", expand=True)
        self.user_list.bind("<<ListboxSelect>>", lambda e: self._show_user())
        add = ttk.Frame(left)
        add.pack(fill="x", pady=4)
        ttk.Entry(add, textvariable=self.v("users.new"), width=16).pack(side="left")
        ttk.Button(add, text="Add", command=self.add_user).pack(side="left", padx=2)
        ttk.Button(left, text="Refresh", command=self._refresh_users).pack(fill="x")
        ttk.Button(left, text="Open users folder", command=lambda: self._open(self.v("paths.users_root").get())).pack(fill="x")

        right = ttk.LabelFrame(f, text="Selected user", padding=6)
        right.grid(row=1, column=1, sticky="nsew", **PAD)
        right.columnconfigure(1, weight=1)
        ttk.Label(right, text="Folder").grid(row=0, column=0, sticky="e", **PAD)
        ttk.Label(right, textvariable=self.v("users.sel"), font=("", 11, "bold")).grid(row=0, column=1, sticky="w", **PAD)
        ttk.Label(right, text="Initials / aliases").grid(row=1, column=0, sticky="e", **PAD)
        ttk.Entry(right, textvariable=self.v("users.sel_aliases"), width=40).grid(row=1, column=1, sticky="ew", **PAD)
        ttk.Label(right, text="comma-separated, e.g.  IJ, IJD, Isaac-J", foreground="#666").grid(row=2, column=1, sticky="w", padx=6)
        ttk.Button(right, text="Apply aliases", command=self.apply_aliases).grid(row=3, column=1, sticky="w", **PAD)
        ttk.Label(right, text="Learned from the resolver window (read-only):").grid(row=4, column=0, columnspan=2, sticky="w", **PAD)
        self.learned_lbl = ttk.Label(right, text="", foreground="#666", wraplength=500, justify="left")
        self.learned_lbl.grid(row=5, column=0, columnspan=2, sticky="w", padx=6)

        df = ttk.LabelFrame(f, text="Unknown users", padding=6)
        df.grid(row=2, column=0, columnspan=2, sticky="ew", **PAD)
        ttk.Label(df, text="If no user is recognised:").pack(side="left")
        self.default_cb = ttk.Combobox(df, textvariable=self.v("users.default"), width=20)
        self.default_cb.pack(side="left", padx=6)
        ttk.Label(df, text="(blank = ask in the resolver window / reject; or pick a catch-all folder like _unsorted)",
                  foreground="#666").pack(side="left")

    def _users_root(self) -> Path:
        return Path(self.v("paths.users_root").get().strip() or ".")

    def _refresh_users(self):
        root = self._users_root()
        from ionomos.config import DEFAULT_USER_IGNORE, _subfolders, not_a_user

        ignore = tuple(self.data["users"].get("ignore") if self.data["users"].get("ignore") is not None
                       else DEFAULT_USER_IGNORE)
        users = [u for u in _subfolders(root) if not_a_user(u, ignore) is None]
        self.user_list.delete(0, "end")
        for u in users:
            self.user_list.insert("end", u)
        self.default_cb.configure(values=["", *users])
        stale = [u for u in self.data["users"].get("aliases", {}) if u not in users]
        if stale:
            self.set_status(f"aliases refer to users without a folder: {', '.join(stale)}", ok=False)
        self._show_learned()

    def _show_learned(self):
        p = Path(self.v("users.learned_aliases_file").get().strip() or "")
        txt = ""
        if p.is_file():
            try:
                import yaml

                from ionomos.config import read_yaml_text

                d = yaml.safe_load(read_yaml_text(p)) or {}
                txt = "   ".join(f"{u}: {', '.join(a)}" for u, a in d.items())
            except Exception:
                txt = "(unreadable)"
        self.learned_lbl.configure(text=txt or "nothing yet")

    def _show_user(self):
        sel = self.user_list.curselection()
        if not sel:
            return
        u = self.user_list.get(sel[0])
        self.v("users.sel").set(u)
        self.v("users.sel_aliases").set(", ".join(self.data["users"].get("aliases", {}).get(u, [])))

    def add_user(self):
        name = self.v("users.new").get().strip()
        if not name:
            return
        if " " in name:
            messagebox.showerror("Add user", "No spaces in user folder names: FragPipe can't use a path with a space.\n\n"
                                             "→ Type the name without spaces (Isaac_J, not Isaac J), then Add.")
            return
        root = self._users_root()
        try:
            (root / name).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("Add user", f"Could not create {root / name}:\n{exc}\n\n→ Check the users folder on the "
                                             "Folders tab exists and this Windows account can write to it, then Add again.")
            return
        self.v("users.new").set("")
        self._refresh_users()
        idx = list(self.user_list.get(0, "end")).index(name)
        self.user_list.selection_clear(0, "end")
        self.user_list.selection_set(idx)
        self._show_user()
        self.set_status(f"created {root / name}")

    def apply_aliases(self):
        u = self.v("users.sel").get()
        if not u:
            return
        als = [a.strip() for a in self.v("users.sel_aliases").get().replace(";", ",").split(",") if a.strip()]
        self.data["users"].setdefault("aliases", {})
        if als:
            self.data["users"]["aliases"][u] = als
        else:
            self.data["users"]["aliases"].pop(u, None)
        self.set_status(f"aliases for {u}: {', '.join(als) or '(none)'} — remember to Save")

    # ------------------------------------------------------ tab: methods ----

    _MCOLS = ("aliases", "workflow", "fasta", "data_type", "postprocess")

    def _tab_methods(self):
        f = ttk.Frame(self.nb, padding=10)
        self.tab_methods = f
        self.nb.add(f, text="  3  Methods  ")
        f.columnconfigure(0, weight=1)
        f.rowconfigure(1, weight=1)
        ttk.Label(f, text="Each method = keywords Ionomos looks for in names + the FragPipe workflow and FASTA "
                          "to run. Export a workflow from the FragPipe GUI: load it, set the FASTA (Database tab), "
                          "then Workflow → Save to custom folder → your Workflows folder.", wraplength=840).grid(
            row=0, column=0, sticky="w", **PAD)
        self.mtree = ttk.Treeview(f, columns=("key", *self._MCOLS), show="headings", height=6)
        for c, w in (("key", 90), ("aliases", 160), ("workflow", 170), ("fasta", 190), ("data_type", 70), ("postprocess", 140)):
            self.mtree.heading(c, text=c)
            self.mtree.column(c, width=w, anchor="w")
        self.mtree.grid(row=1, column=0, sticky="nsew", **PAD)
        self.mtree.bind("<<TreeviewSelect>>", lambda e: self._show_method())

        e = ttk.LabelFrame(f, text="Edit method", padding=6)
        e.grid(row=2, column=0, sticky="ew", **PAD)
        e.columnconfigure(1, weight=1)
        e.columnconfigure(3, weight=1)
        ttk.Label(e, text="Name").grid(row=0, column=0, sticky="e", **PAD)
        ttk.Entry(e, textvariable=self.v("m.key"), width=14).grid(row=0, column=1, sticky="w", **PAD)
        ttk.Label(e, text="Keywords").grid(row=0, column=2, sticky="e", **PAD)
        ttk.Entry(e, textvariable=self.v("m.aliases"), width=30).grid(row=0, column=3, sticky="ew", **PAD)
        ttk.Label(e, text="Workflow file").grid(row=1, column=0, sticky="e", **PAD)
        wf_box, fa_box = ttk.Frame(e), ttk.Frame(e)  # each box + its Choose… button (library_tab.LibraryPanel)
        wf_box.grid(row=1, column=1, sticky="ew")
        wf_box.columnconfigure(0, weight=1)
        self.m_wf = ttk.Combobox(wf_box, textvariable=self.v("m.workflow"), width=30)
        self.m_wf.grid(row=0, column=0, sticky="ew", **PAD)
        ttk.Label(e, text="FASTA file").grid(row=1, column=2, sticky="e", **PAD)
        fa_box.grid(row=1, column=3, sticky="ew")
        fa_box.columnconfigure(0, weight=1)
        self.m_fa = ttk.Combobox(fa_box, textvariable=self.v("m.fasta"), width=30)
        self.m_fa.grid(row=0, column=0, sticky="ew", **PAD)
        ttk.Label(e, text="Data type").grid(row=2, column=0, sticky="e", **PAD)
        ttk.Combobox(e, textvariable=self.v("m.data_type"), values=["DDA", "DIA"], state="readonly", width=8).grid(row=2, column=1, sticky="w", **PAD)
        ttk.Label(e, text="Post-processing").grid(row=2, column=2, sticky="e", **PAD)
        ttk.Entry(e, textvariable=self.v("m.postprocess"), width=30).grid(row=2, column=3, sticky="ew", **PAD)
        ttk.Label(e, text="Extra options (key: value per line)").grid(row=3, column=0, sticky="ne", **PAD)
        self.m_extra = tk.Text(e, height=3, width=40)
        self.m_extra.grid(row=3, column=1, columnspan=3, sticky="ew", **PAD)
        ttk.Label(e, text="steps: isodtb_sites, tmt_annotation (comma-separated; run after FragPipe)",
                  foreground="#666").grid(row=4, column=3, sticky="w", padx=6)
        self.m_info = ttk.Label(e, text="Select a method to see whether its workflow and FASTA are ready.",
                                foreground="#444", wraplength=820, justify="left")
        self.m_info.grid(row=6, column=0, columnspan=4, sticky="w", **PAD)
        b = ttk.Frame(e)
        b.grid(row=5, column=0, columnspan=4, sticky="w", **PAD)
        ttk.Button(b, text="Apply changes", command=self.apply_method).pack(side="left", padx=4)
        ttk.Button(b, text="New method", command=self.new_method).pack(side="left", padx=4)
        ttk.Button(b, text="Remove method", command=self.remove_method).pack(side="left", padx=4)
        ttk.Button(b, text="Test names…", command=self.test_names).pack(side="left", padx=4)
        ttk.Button(b, text="Import workflow…", command=self.import_workflow).pack(side="left", padx=12)
        ttk.Button(b, text="Open workflows folder", command=lambda: self._open(self.v("paths.workflow_dir").get())).pack(side="left", padx=4)
        ttk.Button(b, text="Open FASTA folder", command=lambda: self._open(self.v("paths.fasta_dir").get())).pack(side="left", padx=4)
        from ionomos.library_tab import LibraryPanel

        # Find on this PC…, Choose…, the FASTA line, Download / decoys / updates, first-setup banner (D89, D90)
        self.library = LibraryPanel(self, f, row=3)

    def _refresh_methods(self):
        self.mtree.delete(*self.mtree.get_children())
        wf_dir = Path(self.v("paths.workflow_dir").get().strip() or ".")
        fa_dir = Path(self.v("paths.fasta_dir").get().strip() or ".")
        for key, m in self.data["methods"].items():
            wf_ok = (wf_dir / m.get("workflow", "")).is_file()
            self.mtree.insert("", "end", iid=key, values=(
                key, ", ".join(m.get("aliases", [])), m.get("workflow", "") + ("" if wf_ok else "  ✗ missing"),
                m.get("fasta", ""), m.get("data_type", ""), ", ".join(m.get("postprocess", []))))
        self.m_wf.configure(values=sorted(p.name for p in wf_dir.glob("*.workflow")) if wf_dir.is_dir() else [])
        self.m_fa.configure(values=sorted(p.name for p in fa_dir.iterdir() if p.suffix.lower() in (".fas", ".fasta", ".fa")) if fa_dir.is_dir() else [])

    def _show_method(self):
        sel = self.mtree.selection()
        if not sel:
            return
        key = sel[0]
        m = self.data["methods"][key]
        self.v("m.key").set(key)
        self.v("m.aliases").set(", ".join(m.get("aliases", [])))
        self.v("m.workflow").set(m.get("workflow", ""))
        self.v("m.fasta").set(m.get("fasta", ""))
        self.v("m.data_type").set(m.get("data_type", "DDA"))
        self.v("m.postprocess").set(", ".join(m.get("postprocess", [])))
        self.m_extra.delete("1.0", "end")
        self.m_extra.insert("1.0", "\n".join(f"{k}: {v}" for k, v in m.items() if k not in ("aliases", "workflow", "fasta", "data_type", "postprocess")))
        self._describe_method(m.get("workflow", ""), m.get("fasta", ""))

    def _describe_method(self, workflow: str, fasta: str):
        """Readiness of a workflow + FASTA pair, computed off the Tk thread (FASTAs can be big)."""
        from ionomos import fragpipe

        wf_dir = Path(self.v("paths.workflow_dir").get().strip() or ".")
        fa_dir = Path(self.v("paths.fasta_dir").get().strip() or ".")
        self.m_info.configure(text="checking workflow and FASTA…", foreground="#444")

        def go():
            try:
                lines = fragpipe.describe_files(wf_dir, fa_dir, workflow, fasta)
            except Exception as exc:  # noqa: BLE001
                lines = [(False, f"could not inspect: {exc}")]
            mark = {True: "✓", None: "!", False: "✗"}
            text = "\n".join(f"{mark[ok]} {t}" for ok, t in lines)
            color = "#c62828" if any(ok is False for ok, _ in lines) else ("#b26a00" if any(ok is None for ok, _ in lines) else "#2e7d32")
            self.post(lambda: self.m_info.configure(text=text, foreground=color))

        threading.Thread(target=go, daemon=True).start()

    def import_workflow(self):
        from ionomos import fragpipe

        key = self.v("m.key").get().strip()
        if not key:
            messagebox.showinfo("Import workflow", "Select (or create) the method first, then import its workflow.")
            return
        src = filedialog.askopenfilename(title=f"Workflow for {key} (a .workflow file, e.g. from a good run's fragpipe folder)",
                                         filetypes=[("FragPipe workflow", "*.workflow"), ("all", "*.*")])
        if not src:
            return
        wf_dir = Path(self.v("paths.workflow_dir").get().strip())
        fa_dir = Path(self.v("paths.fasta_dir").get().strip())

        def done(res, err):
            if err is not None:
                messagebox.showerror("Import workflow", f"Could not import {Path(src).name}:\n{err}\n\n→ Check the "
                                                        "workflows and FASTA folders on the Folders tab exist and can "
                                                        "be written to, and that the FASTA the workflow names is still "
                                                        "where it was; then Import workflow… again.")
                return
            self.v("m.key").set(key)
            self.v("m.workflow").set(res["workflow"])
            if res["fasta"]:
                self.v("m.fasta").set(res["fasta"])
            self._refresh_methods()
            self.apply_method()
            self._describe_method(res["workflow"], self.v("m.fasta").get())
            messagebox.showinfo("Import workflow", f"{key} now uses {res['workflow']}"
                                + (f" and FASTA {res['fasta']}" if res["fasta"] else "") + ".\n\n"
                                + "\n".join(res["notes"]) + "\n\nPress Save to keep it.")

        # the FASTA copy can be gigabytes: off the Tk thread, with the busy indicator (S2.2, S2.3)
        self.run_bg("importing the workflow", lambda: fragpipe.import_workflow(Path(src), key, wf_dir, fa_dir), done,
                    key="import_workflow")

    def apply_method(self):
        key = self.v("m.key").get().strip()
        if not key or " " in key:
            messagebox.showerror("Method", "A method needs a name without spaces (isoDTB, TMT, DIA …).\n\n"
                                           "→ Type one in the Name box, then press Apply changes.")
            return
        sel = self.mtree.selection()
        old = sel[0] if sel else None
        m = {
            "aliases": [a.strip() for a in self.v("m.aliases").get().replace(";", ",").split(",") if a.strip()] or [key.lower()],
            "workflow": self.v("m.workflow").get().strip(),
            "fasta": self.v("m.fasta").get().strip(),
            "data_type": self.v("m.data_type").get().strip() or "DDA",
            "postprocess": [a.strip() for a in self.v("m.postprocess").get().replace(";", ",").split(",") if a.strip()],
        }
        for line in self.m_extra.get("1.0", "end").splitlines():
            if ":" in line:
                k, _, val = line.partition(":")
                if k.strip():
                    m[k.strip()] = val.strip()
        if not m["workflow"] or not m["fasta"]:
            messagebox.showerror("Method", "A method needs a workflow file and a FASTA file.\n\n→ Pick them in the two "
                                           "boxes (Choose… lists what is on this PC; Import workflow… takes a run that "
                                           "worked), then press Apply changes.")
            return
        if old and old != key:
            self.data["methods"].pop(old, None)
        self.data["methods"][key] = m
        self._refresh_methods()
        self.mtree.selection_set(key)
        self.set_status(f"method {key} updated — remember to Save")

    def new_method(self):
        self.mtree.selection_remove(*self.mtree.selection())
        for k in ("m.key", "m.aliases", "m.workflow", "m.fasta", "m.postprocess"):
            self.v(k).set("")
        self.v("m.data_type").set("DDA")
        self.m_extra.delete("1.0", "end")
        self.set_status("fill in the fields and press Apply changes")

    def remove_method(self):
        sel = self.mtree.selection()
        if not sel:
            return
        if len(self.data["methods"]) == 1:
            messagebox.showerror("Method", "This is the only method; Ionomos needs at least one.\n\n→ Add the method you "
                                           "want first (New method → Apply changes), then remove this one.")
            return
        if messagebox.askyesno("Remove method", f"Remove {sel[0]}?"):
            self.data["methods"].pop(sel[0], None)
            self._refresh_methods()

    def test_names(self):
        """A small window: type names, see how the settings in this app read them (namecheck, D37)."""
        from ionomos.namecheck import report_from_data

        win = tk.Toplevel(self.root)
        win.title("Test your names")
        win.transient(self.root)
        tkutil.fit_window(win, 100, 32)
        f = ttk.Frame(win, padding=10)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="One name per line: a folder name, then its .raw file names (or a folder's path). "
                          "Uses the settings in this window, saved or not; nothing is moved.",
                  wraplength=tkutil.text_size(92, 1)[0], justify="left").pack(anchor="w")
        inp = tk.Text(f, width=90, height=6)
        inp.pack(fill="x", pady=4)
        inp.insert("1.0", "20260902_EJQ_isoDTB_EJQ-2-027\nEJQ_PK_EJQ-2-027_isoDTB_1uM_3h_1_1.raw\n")
        out = tk.Text(f, width=90, height=18, wrap="none", state="disabled")

        def run():
            try:
                text = report_from_data(self._collect(), inp.get("1.0", "end"), self.config_path)
            except ConfigError as exc:
                text = f"config problem: {exc}\n→ fix that setting in this window, then Test again\n"
            out.configure(state="normal")
            out.delete("1.0", "end")
            out.insert("1.0", text)
            out.configure(state="disabled")

        ttk.Button(f, text="Test", command=run).pack(anchor="w")
        out.pack(fill="both", expand=True, pady=(6, 0))
        win.bind("<Escape>", lambda e: win.destroy())
        self.names_win, self.names_in, self.names_out, self.names_run = win, inp, out, run  # tests
        return win

    # ----------------------------------------------------- tab: advanced ----

    def _tab_advanced(self):
        f = ttk.Frame(self.nb, padding=10)
        self.tab_advanced = f
        self.nb.add(f, text="  4  Advanced  ")
        f.columnconfigure(0, weight=1)
        f.columnconfigure(1, weight=1)

        def group(title, col, row):
            g = ttk.LabelFrame(f, text=title, padding=6)
            g.grid(row=row, column=col, sticky="nsew", **PAD)
            g.columnconfigure(1, weight=1)
            return g

        def num(g, r, key, label, hint):
            """A number field with the Folders tab's ● dot: red with the problem in the hint while the value is not
            one the loader accepts (forms.check_setting, S3.3), grey/green otherwise."""
            from ionomos import forms

            ttk.Label(g, text=label).grid(row=r, column=0, sticky="e", **PAD)
            box = ttk.Frame(g)
            box.grid(row=r, column=1, sticky="w")
            ttk.Entry(box, textvariable=self.v(key), width=10).grid(row=0, column=0, sticky="w", **PAD)
            dot = ttk.Label(box, text="●", width=2, foreground="#999")
            dot.grid(row=0, column=1, sticky="w")
            hint_lbl = ttk.Label(g, text=hint, foreground="#666", wraplength=300)
            hint_lbl.grid(row=r, column=2, sticky="w", **PAD)

            def check(*_):
                text = self.v(key).get().strip()
                problem = forms.check_setting(key, text)
                if problem:
                    dot.configure(foreground="#c62828")
                    hint_lbl.configure(text=problem, foreground="#c62828")
                else:
                    dot.configure(foreground="#2e7d32" if text else "#999")
                    hint_lbl.configure(text=hint, foreground="#666")

            self.v(key).trace_add("write", check)
            self.num_fields[key] = (dot, hint_lbl)
            check()

        self.num_fields: dict[str, tuple] = {}

        w = group("Watcher", 0, 0)
        num(w, 0, "watcher.poll_seconds", "Poll every (s)", "how often the inbox is scanned")
        num(w, 1, "watcher.stable_seconds", "Stable for (s)", "folder must be unchanged this long before it is taken — be generous for USB/network copies")
        num(w, 2, "watcher.min_raw_files", "Min .raw files", "folders with fewer are ignored (left in the inbox)")
        ttk.Checkbutton(w, text="Put .raw files dropped without a folder into a folder (grouped by name)",
                        variable=self.bv("watcher.group_loose_files", True)).grid(row=3, column=0, columnspan=3, sticky="w", **PAD)

        fp = group("FragPipe searches", 1, 0)
        ttk.Checkbutton(fp, text="Run FragPipe automatically on every queued experiment",
                        variable=self.bv("fragpipe.auto_run", True)).grid(row=0, column=0, columnspan=3, sticky="w", **PAD)
        num(fp, 1, "fragpipe.threads", "Threads", "PC has 32 logical CPUs; leave a few for the OS")
        num(fp, 2, "fragpipe.ram_gb", "RAM (GB)", "of 64 GB")
        num(fp, 3, "fragpipe.timeout_minutes", "Timeout (min)", "a search running longer is stopped and failed; 0 = no limit")
        num(fp, 4, "fragpipe.min_free_gb", "Min free disk (GB)", "a search waits while the data drive has less than this + its raw files free")
        ttk.Label(fp, text="Tools folder").grid(row=5, column=0, sticky="e", **PAD)
        ttk.Entry(fp, textvariable=self.v("fragpipe.config_tools_folder"), width=34).grid(row=5, column=1, columnspan=2, sticky="ew", **PAD)
        ttk.Label(fp, text="DIA-NN exe").grid(row=6, column=0, sticky="e", **PAD)
        ttk.Entry(fp, textvariable=self.v("fragpipe.config_diann"), width=34).grid(row=6, column=1, columnspan=2, sticky="ew", **PAD)
        ttk.Label(fp, text="Python folder").grid(row=7, column=0, sticky="e", **PAD)
        ttk.Entry(fp, textvariable=self.v("fragpipe.config_python"), width=34).grid(row=7, column=1, columnspan=2, sticky="ew", **PAD)
        ttk.Label(fp, text="Off = experiments are only filed and queued. Tools folder / DIA-NN exe / Python folder are "
                           "optional: only if a search can't find MSFragger / DIA-NN / Python (Jobs → Check "
                           "FragPipe install tells; FragPipe 24 on Windows always uses its own Python). Restart "
                           "the watcher after changes.",
                  foreground="#666", wraplength=380).grid(row=8, column=0, columnspan=3, sticky="w", padx=6)

        g = group("Resolver window", 0, 1)
        ttk.Checkbutton(g, text="Open a window when a folder can't be interpreted", variable=self.bv("gui.enabled")).grid(row=0, column=0, columnspan=3, sticky="w", **PAD)
        num(g, 1, "gui.timeout_minutes", "Auto-skip after (min)", "0 = wait for a person; otherwise reject with a note after N minutes")
        ttk.Checkbutton(g, text="Pop up a window when something needs you (an analysis decision, a failed search, "
                               "a rejected folder)", variable=self.bv("gui.popups", True)).grid(
            row=2, column=0, columnspan=3, sticky="w", **PAD)
        ttk.Label(g, text="Needs the watcher to run in the logged-in session (the startup task does this).",
                  foreground="#666", wraplength=380).grid(row=3, column=0, columnspan=3, sticky="w", padx=6)

        u = group("Users (advanced)", 1, 1)
        ttk.Label(u, text="Learned aliases file").grid(row=0, column=0, sticky="e", **PAD)
        ttk.Entry(u, textvariable=self.v("users.learned_aliases_file"), width=34).grid(row=0, column=1, columnspan=2, sticky="ew", **PAD)
        ttk.Label(u, text="where 'remember that XYZ means …' answers are stored", foreground="#666").grid(row=1, column=1, columnspan=2, sticky="w", padx=6)

        m = group("Config file", 0, 2)
        ttk.Button(m, text="Open config.yaml in editor", command=lambda: self._open(str(self.config_path))).grid(row=0, column=0, **PAD)
        ttk.Button(m, text="Open logs folder", command=lambda: self._open(self.v("paths.log_dir").get())).grid(row=0, column=1, **PAD)
        ttk.Button(m, text="Reset all to defaults", command=self.reset_defaults).grid(row=0, column=2, **PAD)
        ttk.Button(m, text="Undo last save", command=self.undo_last_save).grid(row=0, column=3, **PAD)
        ttk.Button(m, text="Restore a previous config…", command=self.restore_config).grid(row=0, column=4, **PAD)
        ttk.Label(m, text="Every setting here is also a plain key in config.yaml (see comments in the file). "
                          "Each Save keeps the previous version in config-backups/ next to it: Undo last save brings "
                          "the newest one back into this window, Restore… lists them all. Nothing is written until "
                          "you press Save.", foreground="#666", wraplength=380).grid(
            row=1, column=0, columnspan=5, sticky="w", padx=6)

    def undo_last_save(self) -> bool:
        """The newest config-backups/ copy back into the window (S2.4); Save makes it the active config again."""
        latest = configio.latest_backup(self.config_path)
        if latest is None:
            messagebox.showinfo("Undo last save", "There is no earlier config to go back to: nothing has been saved "
                                                  "from this window yet.\n\n→ Change the settings and press Save; every "
                                                  "Save keeps the version before it.")
            return False
        return self.restore_config(latest)

    def restore_config(self, path: Path | str | None = None) -> bool:
        """Load a saved config into the window (nothing is written until Save). Without a path: a list of the backups
        with their time and what differs from the active config, read off the Tk thread."""
        if path is None:
            self._pick_backup()
            return False
        try:
            data = configio.read_config(Path(path))
        except Exception as exc:  # noqa: BLE001 - show any parse problem
            messagebox.showerror("Restore", f"Could not read {path}:\n{exc}\n\n→ Pick another backup from the list, or "
                                            "open the file in an editor and fix the line it names.")
            return False
        self.data = data
        self._load_vars()
        self._refresh_users()
        self._refresh_methods()
        when = configio.backup_time(Path(path))
        self.set_status(f"loaded the config saved at {when:%Y-%m-%d %H:%M} — press Save to make it the active config"
                        if when else f"loaded {Path(path).name} — press Save to make it the active config")
        return True

    def _pick_backup(self):
        """Restore a previous config…: every backup, newest first, with its time and how it differs (S2.4)."""
        win = tk.Toplevel(self.root)
        win.title("Restore a previous config")
        win.transient(self.root)
        tkutil.fit_window(win, 120, 26)
        f = ttk.Frame(win, padding=10)
        f.pack(fill="both", expand=True)
        f.columnconfigure(0, weight=1)
        f.rowconfigure(1, weight=1)
        ttk.Label(f, text="Each Save keeps the version before it. Pick one: it is loaded into the window, and Save makes "
                          "it the active config. Nothing is written here.", wraplength=800, justify="left").grid(
            row=0, column=0, sticky="w")
        cw = tkutil.char_width()
        tree = ttk.Treeview(f, columns=("when", "differs"), show="headings", selectmode="browse")
        tree.heading("when", text="saved at")
        tree.heading("differs", text="what differs from the active config")
        tree.column("when", width=20 * cw, anchor="w", stretch=False)
        tree.column("differs", width=90 * cw, anchor="w")
        tree.grid(row=1, column=0, sticky="nsew", pady=6)
        state = ttk.Label(f, text="reading the backups…", foreground="#666")
        state.grid(row=2, column=0, sticky="w")
        b = ttk.Frame(f)
        b.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        paths: dict[str, Path] = {}

        def restore():
            sel = tree.selection()
            if not sel:
                messagebox.showinfo("Restore", "Select a backup in the list first, then press Restore into the window.")
                return
            if self.restore_config(paths[sel[0]]):
                win.destroy()

        ttk.Button(b, text="Restore into the window", command=restore).pack(side="left", padx=2)
        ttk.Button(b, text="Open the backups folder", command=lambda: self._open(
            str(self.config_path.parent / configio.BACKUP_DIR))).pack(side="left", padx=2)
        ttk.Button(b, text="Cancel", command=win.destroy).pack(side="right", padx=2)
        tree.bind("<Double-1>", lambda e: restore())
        win.bind("<Escape>", lambda e: win.destroy())
        cfg_path = self.config_path

        def read():
            cur = configio.read_config(cfg_path) if cfg_path.is_file() else configio.defaults()
            rows = []
            for p in configio.backups(cfg_path):
                when = configio.backup_time(p)
                try:
                    diff = configio.diff_words(cur, configio.read_config(p))
                    text = "; ".join(diff[:4]) + (f" … ({len(diff)})" if len(diff) > 4 else "") if diff else "the same"
                except Exception as exc:  # noqa: BLE001 - a damaged backup is still listed
                    text = f"could not read: {exc}"
                rows.append((p, f"{when:%Y-%m-%d %H:%M:%S}" if when else p.name, text))
            return rows

        def show(rows, err):
            if not win.winfo_exists():
                return
            if err is not None:
                state.configure(text=f"could not read the backups: {err}", foreground="#c62828")
                return
            for p, when, text in rows:
                paths[str(p)] = p
                tree.insert("", "end", iid=str(p), values=(when, text))
            state.configure(text=f"{len(rows)} backup(s), newest first" if rows else
                            "no backups yet: every Save keeps the version before it", foreground="#666")

        self.run_bg("reading the config backups", read, show, key="backups")
        self.restore_win = win  # tests
        return win

    def reset_defaults(self):
        if messagebox.askyesno("Reset", "Reset all settings to defaults? (Not saved until you press Save.)"):
            self.data = configio.defaults()
            self._load_vars()
            self._refresh_users()
            self._refresh_methods()

    # ---------------------------------------------------------- tab: run ----

    def _tab_run(self):
        """Run & Test: a Watcher page (watcher, startup task, updates) and a Testing page (the testbed, the
        development-install box), with the output pane under both (S1.9). The job-number prompts are gone: the Jobs
        tab does Retry and Open folder on a selected row; Save & Check lives in the bottom bar only."""
        f = ttk.Frame(self.nb, padding=10)
        self.tab_run = f
        self.nb.add(f, text="  5  Run & Test  ")
        f.columnconfigure(0, weight=1)
        f.rowconfigure(1, weight=1)
        self.repo = service.source_checkout()  # a git checkout (deploy/dev_install.ps1): the Testing page's box
        self.run_nb = ttk.Notebook(f)
        self.run_nb.grid(row=0, column=0, sticky="ew")
        page = ttk.Frame(self.run_nb, padding=6)
        self.run_nb.add(page, text="Watcher")
        page.columnconfigure(0, weight=1)
        page.columnconfigure(1, weight=1)

        w = ttk.LabelFrame(page, text="Watcher", padding=6)
        w.grid(row=0, column=0, sticky="nsew", **PAD)
        self.run_status = ttk.Label(w, text="…", font=("", 10, "bold"))
        self.run_status.grid(row=0, column=0, columnspan=4, sticky="w", **PAD)
        self.start_btn = ttk.Button(w, text="Start watcher", command=self.start_watcher)
        self.start_btn.grid(row=1, column=0, **PAD)
        self.stop_btn = ttk.Button(w, text="Stop watcher", command=self.stop_watcher)
        self.stop_btn.grid(row=1, column=1, **PAD)
        ttk.Button(w, text="Show queue (status)", command=lambda: self._cli(["status", "--all"])).grid(row=1, column=2, **PAD)
        ttk.Button(w, text="Copy diagnostics", command=self.copy_diagnostics).grid(row=2, column=0, **PAD)
        ttk.Button(w, text="Copy with real names", command=lambda: self.copy_diagnostics(anonymise=False)).grid(
            row=2, column=1, **PAD)
        ttk.Button(w, text="Save diagnostics bundle (.zip)", command=self.save_bundle).grid(row=2, column=2, **PAD)
        self.fp_status = ttk.Label(w, text="", wraplength=440)
        self.fp_status.grid(row=4, column=0, columnspan=4, sticky="w", **PAD)
        ttk.Label(w, text="Start = runs in the background until you Stop or log out. Use the startup task to make it "
                          "permanent. Copy diagnostics = one text block (check, status, config, log tail) on the "
                          "clipboard, names replaced by pseudonyms as in a bundle — paste it to whoever is fixing "
                          "things. Copy with real names = the same text as it is, for use inside the lab. A job's "
                          "Retry and Open folder are on the Jobs tab.",
                  foreground="#666", wraplength=420).grid(row=3, column=0, columnspan=4, sticky="w", padx=6)

        s = ttk.LabelFrame(page, text="Start automatically at logon" + ("" if IS_WIN else " (Windows only)"), padding=6)
        s.grid(row=0, column=1, sticky="nsew", **PAD)
        self.task_status = ttk.Label(s, text="…")
        self.task_status.grid(row=0, column=0, columnspan=3, sticky="w", **PAD)
        ttk.Button(s, text="Install startup task", command=self.install_task).grid(row=1, column=0, **PAD)
        ttk.Button(s, text="Remove", command=self.remove_task).grid(row=1, column=1, **PAD)
        ttk.Button(s, text="Desktop shortcut", command=self.shortcut).grid(row=1, column=2, **PAD)
        ttk.Label(s, text="Registers a Task Scheduler task for this Windows account that runs the watcher "
                          "interactively at every logon (so the resolver window can appear).",
                  foreground="#666", wraplength=420).grid(row=2, column=0, columnspan=3, sticky="w", padx=6)
        self._updates_section(page)

        testing = ttk.Frame(self.run_nb, padding=6)
        self.run_nb.add(testing, text="Testing")
        self.tab_testing = testing
        testing.columnconfigure(0, weight=1)
        testing.columnconfigure(1, weight=1)
        self._dev_section(testing, row=1)

        t = ttk.LabelFrame(testing, text="Testbed — try everything with fake data", padding=6)
        t.grid(row=0, column=0, columnspan=2, sticky="ew", **PAD)
        t.columnconfigure(1, weight=1)
        ttk.Label(t, text="Testbed folder").grid(row=0, column=0, sticky="e", **PAD)
        from ionomos.testbed import default_root as _testbed_default_root

        self.v("tb.dir").set(str(_testbed_default_root().resolve()).replace("\\", "/"))
        ttk.Entry(t, textvariable=self.v("tb.dir"), width=50).grid(row=0, column=1, sticky="ew", **PAD)
        ttk.Button(t, text="Browse…", command=lambda: self._browse_into("tb.dir")).grid(row=0, column=2, **PAD)
        ttk.Button(t, text="Create testbed", command=self.tb_init).grid(row=0, column=3, **PAD)
        ttk.Button(t, text="Open folder", command=lambda: self._open(self.v("tb.dir").get())).grid(row=0, column=4, **PAD)
        from ionomos.testbed import SAMPLES

        ttk.Label(t, text="Sample drop").grid(row=1, column=0, sticky="e", **PAD)
        self.tb_sample = ttk.Combobox(t, values=list(SAMPLES), state="readonly", width=24)
        self.tb_sample.set("iso_good")
        self.tb_sample.grid(row=1, column=1, sticky="w", **PAD)
        self.tb_sample.bind("<<ComboboxSelected>>", lambda e: self.tb_desc.configure(text=SAMPLES[self.tb_sample.get()]["shows"]))
        ttk.Button(t, text="Drop", command=lambda: self.tb_drop(False)).grid(row=1, column=2, **PAD)
        ttk.Button(t, text="Drop slowly", command=lambda: self.tb_drop(True)).grid(row=1, column=3, **PAD)
        ttk.Button(t, text="Reset testbed", command=self.tb_reset).grid(row=1, column=4, **PAD)
        self.tb_desc = ttk.Label(t, text=SAMPLES["iso_good"]["shows"], foreground="#666", wraplength=700)
        self.tb_desc.grid(row=2, column=1, columnspan=4, sticky="w", padx=6)
        bb = ttk.Frame(t)
        bb.grid(row=3, column=0, columnspan=5, sticky="w", **PAD)
        ttk.Button(bb, text="Start TESTBED watcher", command=lambda: self.start_watcher(testbed=True)).pack(side="left", padx=4)
        ttk.Button(bb, text="Testbed status", command=lambda: self._cli(["status", "--all"], config=self._tb_cfg())).pack(side="left", padx=4)
        ttk.Button(bb, text="Resolver window demo", command=self.tb_gui_demo).pack(side="left", padx=12)
        ttk.Label(bb, text="1 Create → 2 Start TESTBED watcher → 3 Drop samples (or drag from samples/ into the inbox) → "
                           "watch the log. Stop watcher is on the Watcher page.",
                  foreground="#666", wraplength=700, justify="left").pack(side="left", padx=8)

        o = ttk.Frame(f)
        o.grid(row=1, column=0, sticky="nsew", **PAD)
        o.columnconfigure(0, weight=1)
        o.rowconfigure(1, weight=1)
        top = ttk.Frame(o)
        top.grid(row=0, column=0, sticky="ew")
        ttk.Label(top, text="Output").pack(side="left")
        ttk.Checkbutton(top, text="follow the watcher log", variable=self.bv("follow"), command=self._toggle_follow).pack(side="left", padx=12)
        ttk.Button(top, text="Clear", command=lambda: self.out.write("", clear=True)).pack(side="right")
        self.out = OutputPane(o, height=12)
        self.out.grid(row=1, column=0, sticky="nsew")

    def _browse_into(self, key):
        r = filedialog.askdirectory(mustexist=False)
        if r:
            self.v(key).set(r.replace("\\", "/"))

    # -- watcher process

    def _log_dir(self) -> Path:
        return Path(self.v("paths.log_dir").get().strip() or ".")

    def _refresh_status(self):
        """Every 2 s: the watcher's state, the FragPipe line, the bottom strip, the title's unsaved dot and (every
        30 s) the startup task. The lock file, heartbeat, ledger and schtasks are read on a worker thread
        (uinav.watcher_status, S2.2); only the drawing is here. Re-armed even when a pass fails."""
        try:
            self._refresh_status_once()
        except Exception:  # noqa: BLE001 - never stop the tick
            log.exception("status refresh failed")
        finally:
            self.root.after(2000, self._refresh_status)

    def _refresh_status_once(self):
        from ionomos import uinav

        self._update_title()
        if self._status_busy:
            return
        here_pid, here_label = None, ""
        if self.proc is not None:
            if self.proc.poll() is None:
                here_pid = self.proc.pid
                here_label = "TESTBED" if self.proc_config != self.config_path else "lab config"
            else:
                code = self.proc.returncode
                self.proc = None
                if code not in (0, None, -15, 1 if IS_WIN else -15):
                    self.out.write(f"the watcher exited with code {code} — see the log (tick 'follow the watcher log')")
        now = time.monotonic()
        ask_task = now - getattr(self, "_task_checked", -1e9) > 30  # schtasks is slow; ask every 30 s
        if ask_task:
            self._task_checked = now
        cfg = self._active_cfg()
        fallback_log = self._log_dir()
        self._status_busy = True

        def go():
            try:
                paths = self._paths_of(cfg)
                st = uinav.watcher_status(Path(paths.get("log_dir") or fallback_log), paths.get("database"),
                                          here_pid, here_label, ask_task)
            except Exception as exc:  # noqa: BLE001
                st = uinav.StatusLine(f"Watcher: could not read its state ({exc})", False, "")
            self.post(lambda: self._show_status(st))

        threading.Thread(target=go, daemon=True, name="app-status").start()

    def _show_status(self, st) -> None:
        self._status_busy = False
        try:
            colour = "#2e7d32" if st.ok else "#c62828"
            self.run_status.configure(text=st.watcher, foreground=colour)
            self.fp_status.configure(text=st.fragpipe)
            self.strip_lbl.configure(text=st.strip(), foreground=colour)  # the strip on every tab (S1.7)
            if st.task:
                self.task_status.configure(text=st.task)
        except tk.TclError:
            pass  # the window is closing

    def _active_cfg(self) -> Path:
        """The config of the watcher started here (e.g. the testbed), else the lab config."""
        return self.proc_config if (self.proc is not None and self.proc.poll() is None) else self.config_path

    @staticmethod
    def _paths_of(cfg: Path | None) -> dict:
        """The paths block of a config file, {} when it is missing or half-edited. Reads the disk: fine on a worker
        thread, and on the Tk thread only for a click."""
        try:
            return configio.read_config(cfg)["paths"] if cfg and cfg.is_file() else {}
        except Exception:  # noqa: BLE001 - half-edited config
            return {}

    def _active_paths(self) -> dict:
        return self._paths_of(self._active_cfg())

    def _active_log_dir(self) -> Path:
        return Path(self._active_paths().get("log_dir") or self._log_dir())

    def _ledger(self):
        from ionomos.ledger import Ledger, integrity

        db = self._active_paths().get("database")
        if not db or integrity(db) != "ok":
            return None
        return Ledger(db)

    def _jobs_summary(self) -> str:
        """The FragPipe line, read now (a click's path; the 2 s tick reads it on a thread, uinav.watcher_status)."""
        from ionomos import uinav

        return uinav.jobs_summary(self._active_paths().get("database"))

    def _tb_cfg(self) -> Path:
        return Path(self.v("tb.dir").get().strip()) / "Fragpipe_Auto" / "config.yaml"

    def start_watcher(self, testbed: bool = False):
        if self.proc is not None and self.proc.poll() is None:
            messagebox.showinfo("Watcher", "A watcher started from this app is already running. Stop it first.")
            return
        cfg = self._tb_cfg() if testbed else self.config_path
        if not cfg.is_file():
            messagebox.showerror("Watcher", f"There is no config at {cfg}.\n\n→ "
                                 + ("Press Create testbed first, then Start TESTBED watcher." if testbed
                                    else "Press Save first (bottom right), then Start watcher."))
            return
        from ionomos import health

        try:
            target_log = Path(configio.read_config(cfg)["paths"]["log_dir"])
        except ConfigError as exc:
            messagebox.showerror("Watcher", f"{exc}\nFix the file first; the watcher would not start on it.")
            return
        if health.is_locked(target_log):
            messagebox.showinfo("Watcher", "A watcher is already running for this config (probably the startup task).")
            return
        log.info("start watcher with %s", cfg)
        self.proc = service.start_watcher(cfg)
        self.proc_config = cfg
        self.out.write(f"started watcher pid {self.proc.pid} with {cfg}")
        self.bv("follow").set(True)
        self._toggle_follow()

    def stop_watcher(self):
        from ionomos import health

        if self.proc is not None and self.proc.poll() is None:
            proc, log_dir = self.proc, self._active_log_dir()
        else:
            proc, log_dir = None, self._active_log_dir()
            if not (health.is_locked(log_dir) or service.running_pid(log_dir)):
                self.out.write("no watcher running")
                return
            pid = health.holder_pid(log_dir) or service.running_pid(log_dir)
            if not messagebox.askyesno("Stop", f"Stop the watcher running as pid {pid} (started elsewhere)?"):
                return
        if "RUNNING job" in self._jobs_summary() and not messagebox.askyesno(
                "Stop", "A FragPipe search is running. Stopping ends it; the job re-runs from the start next time. Stop?"):
            return
        self.out.write("stopping the watcher (it finishes its current step and re-queues a running search)…")

        def go():
            how = service.request_stop(log_dir, proc=proc)
            self.post(lambda: self.out.write(f"watcher {how}"))

        self.proc = None if proc is not None else self.proc
        threading.Thread(target=go, daemon=True).start()

    def _toggle_follow(self):
        self._follow_gen += 1  # a tick in flight for the old setting draws nothing
        if self._follow_job:
            self.root.after_cancel(self._follow_job)
            self._follow_job = None
        if self.bv("follow").get():
            self._last_log_size = -1
            self._follow_tick()

    def _follow_tick(self):
        """The watcher log's last lines in the output pane every 1.5 s: the file (up to 5 MB) is read on a worker
        thread (S2.2); this draws and re-arms."""
        self._follow_job = None
        gen = self._follow_gen
        cfg = self._active_cfg()
        last = self._last_log_size

        def read():
            paths = self._paths_of(cfg)
            log_path = names.log_file(Path(paths["log_dir"])) if paths.get("log_dir") else None
            if not (log_path and log_path.is_file()):
                return None
            size = log_path.stat().st_size
            if size == last:
                return None
            try:
                with open(log_path, "rb") as fh:
                    fh.seek(max(0, size - 200_000))
                    lines = fh.read().decode("utf-8", errors="replace").splitlines()[-40:]
            except OSError:
                return None
            return size, "── " + str(log_path) + "\n" + "\n".join(lines)

        def show(res):
            if gen != self._follow_gen or not self.bv("follow").get():
                return
            if res is not None:
                self._last_log_size, text = res
                self.out.write(text, clear=True)
            self._follow_job = self.root.after(1500, self._follow_tick)

        def go():  # a plain thread, not run_bg: a 1.5 s tick is not "work" the busy indicator should show
            try:
                res = read()
            except Exception:  # noqa: BLE001
                res = None
            self.post(lambda: show(res))

        threading.Thread(target=go, daemon=True, name="app-follow").start()

    def _cli(self, args: list[str], config: Path | None = None):
        cfg = config or self.config_path
        self.bv("follow").set(False)
        self._toggle_follow()
        self.out.write(f"$ ionomos --config {cfg} {' '.join(args)}", clear=True)

        def go():
            code, text = service.run_cli(args, cfg)
            self.post(lambda: self.out.write(text + f"\n(exit {code})"))

        threading.Thread(target=go, daemon=True).start()

    # -- task scheduler / shortcut

    def install_task(self):
        if not self.config_path.is_file():
            messagebox.showerror("Startup task", "The startup task needs a saved config to point at.\n\n→ Press Save "
                                                 "(bottom right), then Install startup task.")
            return
        ok, msg = service.install_task(self.config_path)
        self.out.write(msg or ("installed" if ok else "failed"))
        if ok:
            messagebox.showinfo("Startup task", "Installed. The watcher will start at every logon.\nStart it now with 'Start watcher'.")

    def remove_task(self):
        ok, msg = service.remove_task()
        self.out.write(msg or ("removed" if ok else "failed"))

    def shortcut(self):
        ok, msg = service.create_desktop_shortcut()
        self.out.write(msg)
        if ok:
            messagebox.showinfo("Shortcut", f"Created {msg}")

    def _updates_section(self, f):
        """Installed builds: new versions come as Ionomos-Setup-<version>.exe from the Releases page."""
        if getattr(self, "repo", None) is not None:
            return  # the git checkout box above handles updates
        u = ttk.LabelFrame(f, text="Updates", padding=6)
        u.grid(row=1, column=0, columnspan=2, sticky="ew", **PAD)
        from ionomos import names
        from ionomos.buildinfo import one_line

        ttk.Label(u, text=f"This is {one_line()}.").grid(row=0, column=0, columnspan=3, sticky="w", **PAD)
        ttk.Button(u, text="Check for updates", command=lambda: self.check_downloaded_update(auto=False)).grid(row=1, column=0, **PAD)
        ttk.Button(u, text="Update now", command=self.install_update).grid(row=1, column=1, **PAD)
        ttk.Button(u, text="Release notes (GitHub)", command=lambda: self._open(names.RELEASES_URL)).grid(row=1, column=2, **PAD)
        self.upd_lbl = ttk.Label(u, text="", foreground="#666")
        self.upd_lbl.grid(row=2, column=0, columnspan=3, sticky="w", padx=6)
        ttk.Label(u, text="Ionomos checks GitHub when it opens and every 6 hours; a new version shows as "
                          "'Update to …' in the bottom bar. One click downloads it (checksum-verified), stops the "
                          "watcher cleanly, installs, reopens and restarts the watcher. Settings and data are kept.",
                  foreground="#666", wraplength=820).grid(row=3, column=0, columnspan=3, sticky="w", padx=6)

    # -- dev install: update from GitHub / diagnostics

    def _dev_section(self, f, row: int):
        """Shown only when ionomos is imported from a git checkout (deploy/dev_install.ps1)."""
        if self.repo is None:
            return
        d = ttk.LabelFrame(f, text="Development install — this app runs from a git checkout", padding=6)
        d.grid(row=row, column=0, columnspan=2, sticky="ew", **PAD)
        d.columnconfigure(0, weight=1)
        self.dev_lbl = ttk.Label(d, text=f"{self.repo}   {service.git_describe(self.repo)}   (checking for updates…)")
        self.dev_lbl.grid(row=0, column=0, columnspan=3, sticky="w", **PAD)
        ttk.Button(d, text="Update from GitHub & restart", command=self.update_from_git).grid(row=1, column=0, sticky="w", **PAD)
        ttk.Button(d, text="Check again", command=self._check_updates).grid(row=1, column=1, sticky="w", **PAD)
        ttk.Button(d, text="Open repo folder", command=lambda: self._open(str(self.repo))).grid(row=1, column=2, sticky="w", **PAD)
        ttk.Label(d, text="Update = stop the watcher, git pull, reinstall, reopen this app. Settings and data are untouched.",
                  foreground="#666", wraplength=700).grid(row=2, column=0, columnspan=3, sticky="w", padx=6)
        if not os.environ.get("IONOMOS_OFFLINE"):
            self._check_updates()

    def _check_updates(self):
        repo = self.repo

        def go():
            n, msg = service.updates_available(repo)
            if n is None:
                txt = f"could not check: {msg}"
            elif n == 0:
                txt = "up to date"
            else:
                txt = f"UPDATE AVAILABLE: {n} new commit{'s' if n != 1 else ''} on GitHub"
            self.post(lambda: self.dev_lbl.configure(text=f"{repo}   {service.git_describe(repo)}   — {txt}",
                                                     foreground="#c62828" if (n or 0) > 0 else ""))

        threading.Thread(target=go, daemon=True).start()

    def update_from_git(self):
        if "RUNNING job" in self._jobs_summary() and not messagebox.askyesno(
                "Update", "A FragPipe search is running. Updating stops it; the job re-runs from the start "
                          "when the watcher starts again. Update anyway?"):
            return
        from ionomos import health

        proc = self.proc if (self.proc is not None and self.proc.poll() is None) else None
        here_log = self._active_log_dir() if proc is not None else None
        lab_log = self._log_dir()
        if health.is_locked(lab_log) and proc is None \
                and not messagebox.askyesno("Update", "The watcher is running. Stop it and update?"):
            return
        self.proc = None
        self.bv("follow").set(False)
        self._toggle_follow()
        self.out.write("updating…", clear=True)
        repo = self.repo

        def go():  # stopping the watcher can take 15 s: off the Tk thread (S2.2)
            if proc is not None:
                service.request_stop(here_log, proc=proc)
            if health.is_locked(lab_log):
                service.request_stop(lab_log)
            ok, msg = service.update_source(repo, log=lambda t: self.post(lambda: self.out.write(t)))
            return ok, msg

        self.run_bg("updating from GitHub", go, lambda res, err: self._after_update(*res) if err is None
                    else self._after_update(False, f"could not update: {err}"), key="update")

    def _after_update(self, ok: bool, msg: str):
        self.out.write(msg)
        if not ok:
            messagebox.showerror("Update", f"{msg}\n\n→ Check the PC is online and the checkout has no local changes "
                                           "(Open repo folder), then press Update from GitHub & restart again; the "
                                           "installed version keeps running meanwhile.")
            return
        if messagebox.askyesno("Update", msg + "\n\nRestart Ionomos now to load the new code?\n"
                                          "(Press Start watcher again afterwards.)"):
            service.restart_app()

    # -- report a problem / updates

    # ------------------------------------------------------ needs attention ----

    def _start_popups(self):
        from ionomos.popups import PopupHost, Popups

        host = PopupHost(
            root=self.root, log_dir=self._active_log_dir, config_path=lambda: self.config_path,
            open_path=lambda p: self._open(p),
            lab_settings=lambda: dict((self.data or {}).get("analysis") or {}),
            popups_enabled=lambda: bool((self.data.get("gui") or {}).get("popups", True)),
            report_problem=lambda note, job_id=None: self.report_problem(note, job_id), open_setup=lambda: self.nb.select(self.tab_setup),
            on_change=self._attention_badge, database=lambda: self._active_paths().get("database"))
        self.popups = Popups(self.root, host, is_app=True)
        self.popups.start()

    def _attention_badge(self, items) -> None:
        n = len(items)
        try:
            if n:
                errs = sum(1 for i in items if i.severity == "error")
                self.attn_btn.configure(text=f"⚠ {n} need{'s' if n == 1 else ''} attention",
                                        foreground="#c62828" if errs else "#b26a00")
                if not self.attn_btn.winfo_manager():
                    self.attn_btn.pack(side="right", padx=4, before=self.report_btn)
            elif self.attn_btn.winfo_manager():
                self.attn_btn.pack_forget()
        except tk.TclError:
            pass

    def show_attention(self):
        self.popups.center()

    def refresh_attention(self):
        try:
            self.popups.check()
        except Exception:  # noqa: BLE001
            log.exception("attention refresh failed")

    def report_problem(self, note: str = "", job_id: int | None = None):
        """One window -> one zip (bundle.py, D63) on the Desktop, selected in Explorer, and its key file."""
        from ionomos.bundle_dialog import BundleDialog

        d = BundleDialog(self.root, self.config_path, self.post, note=note, job_id=job_id,
                         log_dir=self._active_log_dir, on_status=self.set_status)
        self.report_dialog = d
        self.report_win, self.report_text, self.report_debug, self.report_create = d.win, d.text, d.debug, d.save  # tests

    def check_downloaded_update(self, auto: bool = True):
        """Ask GitHub for a newer release (and look in Downloads). auto=False: also say "you're up to date"."""
        from ionomos import updates

        def go():
            rel, why = updates.check_latest()
            online = rel if updates.is_newer(rel) else None
            local = updates.find_downloaded_installer()
            self.post(lambda: self._show_update(online, local, why, auto, rel))

        threading.Thread(target=go, daemon=True).start()
        if auto:
            self.root.after(6 * 3600 * 1000, self.check_downloaded_update)  # and again every 6 hours

    def _show_update(self, online, local, why: str = "", auto: bool = True, latest=None):
        self._release, self._update_found = online, local
        version = online.version if online else (local[1] if local else None)
        if version:
            self.update_btn.configure(text=f"Update to {version}")
            if not self.update_btn.winfo_ismapped():
                self.update_btn.pack(side="right", padx=4, after=self.report_btn)
            log.info("update available: %s (%s)", version, "GitHub" if online else local[0])
        if hasattr(self, "upd_lbl"):
            if online:
                text = f"Ionomos {online.version} is available — press Update to {online.version}."
            elif local:
                text = f"Downloaded and ready: {local[0].name}"
            elif latest is not None:
                text = f"Up to date (latest release: {latest.version})."
            else:
                text = f"Couldn't check GitHub just now ({why})." if why else ""
            self.upd_lbl.configure(text=text)
        if not auto and not version:
            messagebox.showinfo("Updates", f"You have the latest version ({__version__})." if latest is not None
                                else f"Couldn't check for updates:\n{why}")

    def install_update(self):
        """Download (if needed, verified) and install the newer version; the watcher restarts afterwards."""
        from ionomos import health, updates

        online = getattr(self, "_release", None)
        local = getattr(self, "_update_found", None)
        if not online and not local:
            self.check_downloaded_update(auto=False)
            return
        version = online.version if online else local[1]
        if not updates.can_self_update():
            messagebox.showinfo("Update", f"Ionomos {version} is available: {updates.RELEASES_PAGE}\n"
                                          "(this copy isn't an installed Windows build, so it can't update itself)")
            return
        running = health.is_locked(self._active_log_dir())
        if not messagebox.askyesno("Update", f"Update to Ionomos {version} now?\n\n"
                                   + ("The watcher will be stopped (a running search re-runs afterwards) and "
                                      "started again when the new version opens.\n" if running else "")
                                   + "Your settings and data are not touched."):
            return

        def finish(installer: Path):
            """Stop the watcher (up to 15 s, on a worker thread: S2.2), then install and close."""
            log.info("installing update %s from %s", version, installer)
            log_dir = self._active_log_dir()
            proc = self.proc if self.proc and self.proc.poll() is None else None
            if running:
                self.set_status("stopping the watcher for the update…")

            def stop_then_install():
                if running:
                    service.request_stop(log_dir, proc=proc)
                updates.install(installer, log_dir, running)

            def done(_res, err):
                if err is not None:
                    messagebox.showerror("Update", f"The update could not be started: {err}\n\n→ Try again; if it repeats, "
                                                   f"run {installer.name} yourself from\n{updates.RELEASES_PAGE}")
                    return
                self.root.destroy()

            self.run_bg(f"installing Ionomos {version}", stop_then_install, done, key="install_update")

        if local and (not online or parse_v(local[1]) >= parse_v(online.version)):
            release = online if online and parse_v(local[1]) == parse_v(online.version) else None
            if release is None or not release.sha256:
                messagebox.showerror("Update", f"Ionomos {version} has no published SHA-256 to verify {local[0].name} "
                                               f"against, so it won't be installed automatically.\n\n"
                                               f"→ Download it from\n{updates.RELEASES_PAGE}\nand run it yourself.")
                return
            if not updates._verified(local[0], release):
                messagebox.showerror("Update", f"{local[0].name} failed its checksum.\n\n→ Delete it and download it "
                                               f"again from\n{updates.RELEASES_PAGE}")
                return
            finish(local[0])
            return

        if not online.sha256:
            messagebox.showerror("Update", f"Ionomos {online.version} has no published SHA-256, so its installer can't be "
                                           f"verified before installing.\n\n→ Download it from\n{updates.RELEASES_PAGE}"
                                           "\nand run it yourself.")
            return

        def progress(got, total):
            pct = f"{100 * got / total:.0f}%" if total else f"{got / 1e6:.0f} MB"
            self.post(lambda: self.set_status(f"downloading Ionomos {version}… {pct}"))

        def downloaded(path, err):
            if err is not None:
                messagebox.showerror("Update", f"{err}\n\n→ Try again, or download it from\n{online.page}")
                return
            finish(path)

        self.set_status(f"downloading Ionomos {version}…")
        self.run_bg(f"downloading Ionomos {version}", lambda: updates.download(online, progress=progress), downloaded,
                    key="download_update")

    def _after_update_restart(self):
        """After an update that stopped the watcher: start it again (via the startup task if there is one)."""
        from ionomos import health, updates

        log_dir = self._active_log_dir()
        if not updates.take_restart_flag(log_dir) or health.is_locked(log_dir):
            return
        log.info("restarting the watcher after an update")
        if service.task_status() == "installed":
            service.run_task()
        else:
            self.start_watcher()
        self.set_status("updated — the watcher was started again")

    def save_bundle(self):
        cfg = self.config_path
        self.out.write("building diagnostics bundle…", clear=True)

        def show(z, err):
            if err is not None:
                self.out.write(f"could not build the bundle: {err}")
                messagebox.showerror("Diagnostics bundle", f"Could not build the bundle: {err}\n\n→ Check the log folder "
                                                           "exists and has free space, then try again; Copy diagnostics "
                                                           "gives the text alone meanwhile.")
                return
            self.out.write(f"saved {z}")
            messagebox.showinfo("Diagnostics bundle", f"Saved:\n{z}\n\nIt contains the report, config, logs and the "
                                "FragPipe logs of failed/running jobs — no raw data, names replaced by "
                                "pseudonyms. Send this file; the KEY file next to it stays in the lab.")
            self._open(str(Path(z).parent))

        self.run_bg("building the diagnostics bundle", lambda: service.save_diagnostics_zip(cfg), show, key="bundle")

    def copy_diagnostics(self, anonymise: bool = True):
        """The diagnostics text on the clipboard: anonymised as a bundle is (D74), or with the real names."""
        cfg = self.config_path
        self.out.write("collecting diagnostics…", clear=True)

        def show(res, err):
            if err is not None:  # BundleLeak: a name was left, so nothing is copied
                self.out.write(str(err), clear=True)
                messagebox.showerror("Diagnostics", f"Nothing was copied.\n\n{err}\n\n→ Press Copy with real names for "
                                                    "the text as it is (for use inside the lab), or Report a problem… "
                                                    "for a zip; the detail above says which name was left.")
                return
            text, where = res
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.out.write(text + ("\n(saved to " + str(where) + ")" if where else ""), clear=True)
            messagebox.showinfo("Diagnostics", service.diagnostics_copied_message(where, anonymise))

        self.run_bg("collecting diagnostics", lambda: service.save_diagnostics(cfg, anonymise=anonymise), show,
                    key="diagnostics")

    # -- testbed

    def tb_init(self):
        from ionomos import testbed

        d = Path(self.v("tb.dir").get().strip())
        try:
            cfg = testbed.init(d)
        except OSError as exc:
            messagebox.showerror("Testbed", f"Could not create the testbed in {d}:\n{exc}\n\n→ Pick a folder without "
                                            "spaces that this account can write to (Browse…), then Create testbed again.")
            return
        self.out.write((d / "README.txt").read_text(encoding="utf-8"), clear=True)
        self.out.write(f"testbed ready: {cfg}")

    def tb_drop(self, slow: bool):
        from ionomos import testbed

        d = Path(self.v("tb.dir").get().strip())
        name = self.tb_sample.get()
        if not (d / "Fragpipe_Auto").is_dir():
            messagebox.showerror("Testbed", f"There is no testbed in {d} yet.\n\n→ Press Create testbed first, then Drop.")
            return

        def go():
            try:
                dst = testbed.drop(d, name, slow=slow, delay=0.6)
                self.post(lambda: self.out.write(f"dropped {dst}"))
            except SystemExit as exc:
                msg = str(exc)
                self.post(lambda: self.out.write(msg))

        self.out.write(f"dropping {name}{' slowly' if slow else ''}…")
        threading.Thread(target=go, daemon=True).start()

    def tb_reset(self):
        from ionomos import testbed

        d = Path(self.v("tb.dir").get().strip())
        if (d / "Fragpipe_Auto").is_dir() and messagebox.askyesno("Reset testbed", f"Empty inbox/users/ledger/logs in {d}?"):
            # deleting folders can take a while: off the Tk thread (S2.2)
            self.run_bg("resetting the testbed", lambda: testbed.reset(d),
                        lambda _r, err: self.out.write("testbed reset" if err is None else f"could not reset: {err}"),
                        key="tb_reset")

    def tb_gui_demo(self):
        from ionomos.intake import Draft, DraftFile, Kind
        from ionomos.resolve import TkResolver

        files = [DraftFile(f"EJQ_PK_EJQ-2-027_isoDTB_1uM_3h_{r}_{fr}.raw", "EJQ_PK_EJQ-2-027_isoDTB_1uM_3h", str(r), str(fr))
                 for r in (1, 2, 3) for fr in range(1, 4)]
        files.append(DraftFile("EJQ_PK_EJQ-2-027_isoDTB_1uM_3h_extra.raw", "EJQ_PK_EJQ-2-027_isoDTB_1uM_3h_extra", "1", "",
                               error="must end in _rep_fraction"))
        d = Draft(folder="20260902-isoDTB_XYZ-2-027 (1uM 3h)", problem="no known user in the folder name (demo)",
                  kind=Kind.USER, user="", method="isoDTB", date="2026-09-02",
                  known_users=list(self.user_list.get(0, "end")) or ["EJQ", "Isaac"], known_methods=list(self.data["methods"]),
                  files=files)
        ov = TkResolver(self.root, remember=lambda u, a: self.out.write(f"(demo) would remember {a!r} -> {u}")).resolve(d)
        self.out.write("demo answer: " + ("skipped" if ov is None else str(ov.to_dict())))

    # --------------------------------------------------------- tab: setup ----

    _MARK = {"ok": ("✓", "#2e7d32"), "todo": ("○", "#1565c0"), "warn": ("!", "#b26a00"), "fail": ("✗", "#c62828"),
             "info": ("·", "#666")}

    def _tab_setup(self):
        """Check everything (S3.1): the setup checklist, FragPipe read from disk, the workflow library, the PC's
        health and what needs you, in one list with a Fix button where the app can do it (checks.everything)."""
        f = ttk.Frame(self.nb, padding=12)
        self.tab_setup = f
        self.nb.add(f, text="  ✓ Setup  ")
        f.columnconfigure(0, weight=1)
        f.rowconfigure(2, weight=1)
        top = ttk.Frame(f)
        top.grid(row=0, column=0, sticky="ew")
        ttk.Label(top, text="Check everything", font=("", 15, "bold")).pack(side="left")
        self.check_btn = ttk.Button(top, text="Check again", command=self.refresh_setup)
        self.check_btn.pack(side="right", padx=4)
        ttk.Button(top, text="Auto-setup", command=self.auto_setup).pack(side="right", padx=4)
        self.setup_summary = ttk.Label(f, text="checking…", font=("", 11), wraplength=860, justify="left")
        self.setup_summary.grid(row=1, column=0, sticky="w", pady=(4, 10))
        self.setup_scroll = tkutil.ScrolledFrame(f)  # 13+ rows: scrolls on a laptop instead of going below the fold (S1.8)
        self.setup_scroll.grid(row=2, column=0, sticky="nsew")
        self.setup_list = self.setup_scroll.inner
        self.setup_list.columnconfigure(2, weight=1)
        ttk.Label(f, text="Auto-setup: standard folders (C:/Fragpipe_Auto + C:/Fragpipe_General), creates them, "
                          "finds FragPipe, saves. Then add people (the Users tab) and import one workflow per method "
                          "(the Methods tab). "
                          "Everything else on this list has a button. The full FragPipe check (it starts FragPipe for "
                          "a dry run; minutes) is Check FragPipe install on the Jobs tab.", foreground="#666",
                  wraplength=860, justify="left").grid(row=3, column=0, sticky="w", pady=(10, 0))

    def refresh_setup(self):
        """Run every check off the Tk thread (disks, the lock file, schtasks, the library catalog, the ledger)."""
        from ionomos import checks

        try:
            data = self._collect()
        except ConfigError as exc:
            self.setup_summary.configure(text=f"A setting isn't valid yet: {exc}", foreground="#c62828")
            return
        cfg_path = self.config_path

        def done(rows, err):
            if err is not None:
                rows, text = [], f"could not run the checks: {err}"
            else:
                text = checks.summary(rows)
            self._show_setup(rows, text)

        self.run_bg("checking everything", lambda: checks.everything(cfg_path, data), done, key="checks",
                    button=self.check_btn)

    def _show_setup(self, items, text):
        from ionomos import checks

        for w in self.setup_list.winfo_children():
            w.destroy()
        self.setup_items = items
        r, last_source = 0, None
        for it in items:
            source = getattr(it, "source", None)
            if source and source != last_source and len({getattr(x, "source", None) for x in items}) > 1:
                ttk.Label(self.setup_list, text=checks.SOURCES.get(source, source), font=("", 10, "bold"),
                          foreground="#666").grid(row=r, column=0, columnspan=4, sticky="w", pady=(8 if r else 0, 2))
                r += 1
            last_source = source
            sym, color = self._MARK.get(it.status, self._MARK["info"])
            ttk.Label(self.setup_list, text=sym, foreground=color, font=("", 13, "bold"), width=2).grid(
                row=r, column=0, sticky="n", padx=(0, 4), pady=3)
            ttk.Label(self.setup_list, text=it.title, font=("", 11, "bold"), width=30, wraplength=240,
                      justify="left").grid(row=r, column=1, sticky="nw", pady=3)
            detail = it.detail + (f"\n→ {it.fix}" if it.status not in ("ok", "info") and it.fix else "")
            ttk.Label(self.setup_list, text=detail, foreground="#555", wraplength=520, justify="left").grid(
                row=r, column=2, sticky="w", pady=3)
            if it.action and it.status not in ("ok", "info"):
                label = {"create_folders": "Create folders", "find_fragpipe": "Find FragPipe", "save": "Save",
                         "install_task": "Install", "start_watcher": "Start", "resume_searches": "Resume",
                         "show_attention": "Show", "report_problem": "Report…"}.get(it.action.split(":", 1)[0],
                                                                                   "Open" if ":" in it.action else "Fix")
                ttk.Button(self.setup_list, text=label, command=lambda a=it.action: self._setup_action(a)).grid(
                    row=r, column=3, sticky="ne", pady=3)
            r += 1
        problems = [i for i in items if i.status in ("fail", "todo")]
        ok = not problems and items
        self.setup_summary.configure(text=text, foreground="#2e7d32" if ok else "#c62828" if problems else "")
        self._last_setup_text, self._last_setup_problems = text, len(problems)  # the Today tab's Checks line (S3.2)

    _DIGIT_TABS = {"1": "folders", "2": "users", "3": "methods", "4": "advanced", "5": "run"}  # setupcheck's old actions

    def _setup_action(self, action: str):
        """A Fix button on the checks list (checks.Row.action)."""
        kind, _, arg = action.partition(":")
        if kind == "open_tab":
            self.select_tab(self._DIGIT_TABS.get(arg, arg))
            return
        if kind == "open_path":
            self._open(arg)
            return
        if kind == "show_attention":
            self.show_attention()
            return
        if kind == "report_problem":
            self.report_problem()
            return
        if kind == "create_folders":
            self.create_all()
        elif kind == "find_fragpipe":
            self.find_fragpipe()
            self.save()
        elif kind == "save":
            self.save()
        elif kind == "install_task":
            self.install_task()
        elif kind == "start_watcher":
            self.start_watcher()
        elif kind == "resume_searches":
            from ionomos.worker import paused

            if paused(self._active_log_dir()):
                self.toggle_pause()
        self.root.after(800, self.refresh_setup)

    def auto_setup(self):
        """Sensible defaults for everything that has one, then save. Never overwrites a path that already works."""
        from ionomos.fragpipe import detect_launcher

        inbox = Path(self.v("paths.inbox").get().strip() or "")
        if not str(inbox) or not inbox.is_dir():
            if not self.v("quick.root").get().strip():
                d = configio.defaults()
                self.v("quick.root").set(d["paths"]["inbox"].rsplit("/", 1)[0])
                self.v("quick.users").set(d["paths"]["users_root"])
            self.apply_quick()
        self.create_all()
        if not Path(self.v("paths.fragpipe_exe").get().strip() or "").is_file():
            found = detect_launcher()
            if found:
                self.v("paths.fragpipe_exe").set(str(found).replace("\\", "/"))
        self.save()
        self.refresh_setup()

    # ------------------------------------------------------- tab: analysis ----

    def _tab_analysis(self):
        from ionomos.analysis_tab import AnalysisTab

        self.analysis = AnalysisTab(self)
        self.tab_analysis = self.analysis.frame

    def _tab_notify(self):
        from ionomos.notify_tab import NotifyTab

        self.notify_tab = NotifyTab(self)
        self.tab_notify = self.notify_tab.frame

    def analyze_folder(self):
        self.analysis._analyse_folder()

    def _start_drops(self) -> None:
        """Folders and tables dropped from Explorer anywhere on the window open in the Analysis tab, checked first
        (dragdrop.py, folder_check.py, D80). Windows only; elsewhere the Folder… button is the way."""
        from ionomos import dragdrop

        if dragdrop.enable(self.root, self.analysis.dropped):
            self.analysis.drop_enabled(True)

    def _tab_inbox(self):
        frame = ttk.Frame(self.nb, padding=10)
        self.tab_inbox = frame
        self.nb.add(frame, text="Inbox")
        ttk.Label(frame, text="What lab members have dropped and Ionomos has not taken in yet (it waits for the copy to "
                              "settle). Delete removes an item from intake; recover it from the removed-items folder.",
                  wraplength=900, justify="left").pack(anchor="w")
        self.inbox_lbl = ttk.Label(frame, text="", foreground="#666", wraplength=900, justify="left")  # the path, "empty"
        self.inbox_lbl.pack(anchor="w", pady=(4, 0))
        self.inbox_tree = ttk.Treeview(frame, show="tree", selectmode="browse")
        self.inbox_tree.pack(fill="both", expand=True, pady=8)
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Refresh", command=self._refresh_inbox).pack(side="left")
        ttk.Button(buttons, text="Delete selected", command=self._delete_inbox).pack(side="left", padx=6)
        ttk.Button(buttons, text="Open inbox", command=lambda: self._open(self.v("paths.inbox").get())).pack(side="left")
        ttk.Button(buttons, text="Open removed items", command=lambda: self._open(
            str(Path(self.v("paths.inbox").get()) / names.REMOVED_DIR))).pack(side="left", padx=6)
        self._inbox_busy = False
        self._inbox_tick()

    def _refresh_inbox(self):
        """Read the inbox now and draw it (the Refresh button, F5; the tick does the read on a thread)."""
        from ionomos import uinav

        inbox = self.v("paths.inbox").get().strip()
        self._show_inbox(inbox, uinav.inbox_listing(Path(inbox) if inbox else None))

    def _show_inbox(self, inbox: str, entries) -> None:
        selected = self.inbox_tree.selection()
        expanded = {key for key in self.inbox_tree.get_children() if self.inbox_tree.item(key, "open")}
        self.inbox_tree.delete(*self.inbox_tree.get_children())
        if entries is None:
            self.inbox_lbl.configure(text=f"{inbox or 'no inbox folder set'} — the folder is not there (the Folders tab "
                                          "sets it; Create all missing folders makes it)", foreground="#c62828")
            return
        for e in entries:
            self.inbox_tree.insert("", "end", iid=e.key, text=e.name, open=e.key in expanded)
            for iid, text in e.children:
                self.inbox_tree.insert(e.key, "end", iid=iid, text=text)
        if selected and self.inbox_tree.exists(selected[0]):
            self.inbox_tree.selection_set(selected[0])
        n = len(entries)
        self.inbox_lbl.configure(text=f"{inbox} — " + ("empty: nothing is waiting" if not n else
                                                        f"{n} item{'s' if n != 1 else ''} waiting"), foreground="#666")

    def _inbox_tick(self):
        """Every 2 s while the Inbox tab is shown: read the shared folder on a worker thread (S2.2), then draw."""
        from ionomos import uinav

        try:
            if self._current_tab() == "inbox" and not self._inbox_busy:
                self._inbox_busy = True
                inbox = self.v("paths.inbox").get().strip()

                def go():
                    try:
                        entries = uinav.inbox_listing(Path(inbox) if inbox else None)
                    except Exception:  # noqa: BLE001 - never a Tk callback traceback from the tick (issue #14)
                        entries = None

                    def show():
                        self._inbox_busy = False
                        self._show_inbox(inbox, entries)

                    self.post(show)

                threading.Thread(target=go, daemon=True, name="app-inbox").start()
        except Exception:  # noqa: BLE001 - never stop the tick
            self._inbox_busy = False
        self.root.after(2000, self._inbox_tick)

    def _delete_inbox(self):
        from ionomos.inbox import remove

        selected = self.inbox_tree.selection()
        if not selected:
            return
        try:
            destination = remove(Path(self.v("paths.inbox").get()), Path(selected[0]))
            self.set_status(f"Removed from inbox; recover from {destination}")
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not remove item", f"{exc}\n\n→ Close any program that has the folder open "
                                                          "(Explorer, the acquisition PC's copy), then Delete selected again.")
        self._refresh_inbox()

    # ---------------------------------------------------------- tab: jobs ----

    _JCOLS = (("id", 40), ("status", 70), ("user", 80), ("method", 72), ("experiment", 260), ("queued", 110),
              ("ran", 70), ("now", 330))

    def _tab_jobs(self):
        f = ttk.Frame(self.nb, padding=10)
        self.tab_jobs = f
        self.nb.add(f, text="  6  Jobs  ")
        f.columnconfigure(0, weight=1)
        f.rowconfigure(1, weight=3)
        f.rowconfigure(3, weight=2)
        top = ttk.Frame(f)
        top.grid(row=0, column=0, sticky="ew")
        self.jobs_state = ttk.Label(top, text="", font=("", 10, "bold"))
        self.jobs_state.pack(side="left", padx=4)
        ttk.Label(top, text="Find").pack(side="left", padx=(16, 2))
        self.jobs_find = ttk.Entry(top, textvariable=self.v("jobs.find"), width=26)  # uinav.job_matches; Ctrl+F
        self.jobs_find.pack(side="left")
        self.jobs_find.bind("<Escape>", lambda e: self.v("jobs.find").set(""))
        self.v("jobs.find").trace_add("write", lambda *_: self.refresh_jobs())
        self.check_fp_btn = ttk.Button(top, text="Check FragPipe install", command=self.check_fragpipe)
        self.check_fp_btn.pack(side="right", padx=4)
        self.qc_btn = ttk.Button(top, text="Instrument QC", command=self.open_qc_trend)
        self.qc_btn.pack(side="right", padx=4)
        self.pause_btn = ttk.Button(top, text="Pause searches", command=self.toggle_pause)
        self.pause_btn.pack(side="right", padx=4)
        ttk.Button(top, text="Refresh", command=self.refresh_jobs).pack(side="right", padx=4)

        self.jtree = ttk.Treeview(f, columns=[c for c, _ in self._JCOLS], show="headings", height=10, selectmode="browse")
        for c, w in self._JCOLS:
            self.jtree.heading(c, text=c)
            self.jtree.column(c, width=w, anchor="w", stretch=c in ("experiment", "now"))
        for tag, color in (("failed", "#c62828"), ("running", "#1565c0"), ("waiting", "#b26a00"), ("done", "#2e7d32"),
                           ("attention", "#b26a00")):
            self.jtree.tag_configure(tag, foreground=color)
        self.jtree.grid(row=1, column=0, sticky="nsew", pady=4)
        self.jtree.bind("<<TreeviewSelect>>", lambda e: self.show_job())
        self.jtree.bind("<Double-1>", lambda e: self.job_action("report"))
        self.jtree.bind("<Return>", lambda e: self.job_action("report"))

        b = ttk.Frame(f)
        b.grid(row=2, column=0, sticky="w")
        for text, action in (("Open report", "report"), ("Open folder", "folder"), ("FragPipe log", "log"),
                             ("Re-run analysis", "analyze"), ("Analysis options…", "studio"), ("Retry", "retry"),
                             ("Cancel", "cancel"),
                             ("Copy details", "copy"), ("Zip for troubleshooting…", "bundle")):
            ttk.Button(b, text=text, command=lambda a=action: self.job_action(a)).pack(side="left", padx=4)
        ttk.Label(b, text="double-click = open the report", foreground="#666").pack(side="left", padx=12)
        self.jdetail = OutputPane(f, height=10)
        self.jdetail.grid(row=3, column=0, sticky="nsew", pady=4)
        self._jobs_cache: dict[int, object] = {}
        self._jobs_tick()

    def _jobs_tick(self):
        try:
            if self.nb.index("current") == self.nb.index(self.jtree.master):
                self.refresh_jobs()
        except Exception:  # noqa: BLE001 - never stop the refresh loop
            pass
        self.root.after(3000, self._jobs_tick)

    def refresh_jobs(self):
        from ionomos import fragpipe
        from ionomos.worker import paused

        log_dir = self._active_log_dir()
        is_paused = paused(log_dir)
        self.pause_btn.configure(text="Resume searches" if is_paused else "Pause searches")
        led = self._ledger()
        if led is None:
            self.jobs_state.configure(text="No jobs yet" + ("  ·  searches PAUSED" if is_paused else ""))
            self.jtree.delete(*self.jtree.get_children())
            return
        try:
            jobs = led.list()
        finally:
            led.close()
        sel = self.jtree.selection()
        self.jtree.delete(*self.jtree.get_children())
        self._jobs_cache = {j.id: j for j in jobs}
        counts: dict[str, int] = {}
        from ionomos.uinav import job_matches

        query, shown = self.v("jobs.find").get(), 0
        for j in reversed(jobs):  # newest first
            counts[j.status] = counts.get(j.status, 0) + 1
            if not job_matches(j, query):
                continue
            shown += 1
            waiting = j.status == "queued" and (j.reason or "").startswith("waiting:")
            if j.status == "running":
                now = "FragPipe: " + fragpipe.progress(names.console_log(Path(j.dest_dir)))
            else:
                now = (j.reason or "").replace("\n", " ")[:160]
            needs = j.status == "done" and (j.reason or "").startswith(("analysis needs", "analysis had a problem"))
            tag = "waiting" if waiting else "attention" if needs else j.status
            self.jtree.insert("", "end", iid=str(j.id), tags=(tag,), values=(
                j.id, "waiting" if waiting else j.status, j.user, j.method, j.inbox_name, _local(j.created_at),
                _duration(j.started_at, j.finished_at if j.status != "running" else None) if j.started_at else "", now))
        if sel and self.jtree.exists(sel[0]):
            self.jtree.selection_set(sel[0])
        summary = ", ".join(f"{n} {k}" for k, n in sorted(counts.items()))
        if query.strip():
            summary += f"  ·  {shown} shown"
        self.jobs_state.configure(text=(summary or "No jobs") + ("  ·  searches PAUSED" if is_paused else ""),
                                  foreground="#b26a00" if is_paused else "")

    def _selected_job(self):
        sel = self.jtree.selection()
        return self._jobs_cache.get(int(sel[0])) if sel else None

    def show_job(self):
        from ionomos import fragpipe

        j = self._selected_job()
        if j is None:
            return
        self.jdetail.write(self._job_details(j, fragpipe), clear=True)

    def _job_details(self, j, fragpipe) -> str:
        import json

        dest = Path(j.dest_dir)
        lines = [f"job {j.id}: {j.user}/{j.inbox_name}   [{j.status}]   method {j.method}   attempts {j.attempts}",
                 f"folder: {dest}"]
        if j.reason:
            lines.append(f"reason: {j.reason}")
        try:
            run = json.loads(names.status_path(dest).read_text(encoding="utf-8")).get("run") or {}
        except (OSError, ValueError):
            run = {}
        for w in run.get("warnings") or []:
            lines.append(f"note: {w}")
        console = names.console_log(dest)
        text = fragpipe.read_tail_text(console, 60_000)
        hints = fragpipe.explain(text) if j.status == "failed" else []
        if hints:
            lines.append("")
            lines.append("MOST LIKELY CAUSE:")
            lines += [f"  • {h}" for h in hints]
        if j.status == "running":
            lines.append(f"progress: {fragpipe.progress(console)}")
        if text:
            lines.append("")
            lines.append(f"--- last lines of {console}")
            lines += text.splitlines()[-25:]
        return "\n".join(lines)

    def job_action(self, action: str, job=None):
        from ionomos import fragpipe
        from ionomos.worker import request_cancel, request_retry

        j = job if job is not None else self._selected_job()  # job: from the Today tab's list
        if j is None:
            messagebox.showinfo("Jobs", "Select a job first.")
            return
        if action == "report":
            rep = Path(j.dest_dir) / "results" / "report.html"
            if rep.is_file():
                self._open(str(rep))
            elif j.status == "done":
                if messagebox.askyesno("Report", "This job has no report yet (it ran before reports existed, or "
                                                 "analysis is off). Make one now?"):
                    self._rerun_analysis(j)
            else:
                messagebox.showinfo("Report", f"Job {j.id} is {j.status}; the report is made when FragPipe finishes.")
        elif action == "studio":
            if j.status != "done":
                messagebox.showinfo("Analysis", "Only finished (done) jobs have FragPipe output to analyse.")
                return
            self.analysis.select_job(j)
        elif action == "analyze":
            if j.status != "done":
                messagebox.showinfo("Re-run analysis", "Only finished (done) jobs have FragPipe output to analyse.")
                return
            self._rerun_analysis(j)
        elif action == "bundle":
            self.report_problem(job_id=j.id)
        elif action == "folder":
            self._open(j.dest_dir)
        elif action == "log":
            log = names.console_log(Path(j.dest_dir))
            if log.is_file():
                self._open(str(log))
            else:
                messagebox.showinfo("FragPipe log", "FragPipe hasn't run for this job yet.")
        elif action == "copy":
            self.root.clipboard_clear()
            self.root.clipboard_append(self._job_details(j, fragpipe))
            self.set_status(f"job {j.id} details copied")
        elif action in ("retry", "cancel"):
            led = self._ledger()
            if led is None:
                return
            try:
                if action == "retry":
                    if j.status != "failed":
                        messagebox.showinfo("Retry", f"Job {j.id} is {j.status}; only failed jobs can be retried.")
                        return
                    request_retry(led, j.id, self._active_log_dir())  # every Retry is this one (D75)
                    msg = f"job {j.id} re-queued; it starts when the watcher is free"
                else:
                    if j.status not in ("running", "queued"):
                        messagebox.showinfo("Cancel", f"Job {j.id} is {j.status}; nothing to cancel.")
                        return
                    if not messagebox.askyesno("Cancel", f"Cancel job {j.id} ({j.inbox_name})?"
                                               + ("\nThe running FragPipe search is stopped." if j.status == "running" else "")):
                        return
                    msg = request_cancel(led, j.id)
            finally:
                led.close()
            self.set_status(msg)
            self.refresh_jobs()

    def _rerun_analysis(self, j):
        """Statistics + plots + report again with the Analysis tab's lab defaults, off the Tk thread."""
        from ionomos import postprocess

        try:
            cfg = load(self.config_path, check_paths=False)
        except ConfigError:
            cfg = None
        self.jdetail.write(f"analysing job {j.id} ({j.inbox_name})…", clear=True)

        def done(out, err):
            if err is not None:
                self.jdetail.write(f"analysis failed: {err}\n→ Analysis options… shows the samples and conditions; "
                                   "Report a problem… sends the details.", clear=True)
                return
            lines = [f"method {out.method}"] + [f"{c['name']}: {c['up']} up, {c['down']} down of {c['tested']}"
                                                for c in out.summary.get("comparisons", [])]
            lines += [f"note: {w}" for w in out.warnings]
            rep = out.report
            self.jdetail.write("\n".join(lines) + (f"\nreport: {rep}" if rep else ""), clear=True)
            if rep and rep.is_file():
                self._open(str(rep))

        if not self.run_bg(f"analysing job {j.id}", lambda: postprocess.run_for_folder(Path(j.dest_dir), cfg, j.method),
                           done, key=f"analyse:{j.id}"):
            self.jdetail.write(f"job {j.id} is being analysed already — wait for it to finish", clear=True)

    def toggle_pause(self):
        from ionomos.worker import pause, paused, resume

        log_dir = self._active_log_dir()
        if paused(log_dir):
            resume(log_dir)
            self.set_status("searches resumed")
        else:
            pause(log_dir, "from the app")
            self.set_status("searches paused — queued jobs wait; a running search finishes")
        self.refresh_jobs()

    def open_qc_trend(self):
        """The instrument QC trend page (qctrend.page_for: rebuilt from the store when stale) in the browser."""
        from ionomos import qctrend

        cfg_path = self.config_path

        def done(page, err):
            if err is not None:
                messagebox.showerror("Instrument QC", f"Could not build the QC trend page: {err}\n\n→ Press Save first if "
                                                      "the config is new; otherwise Report a problem… with this text.")
                return
            self._open(str(page))

        # building the page reads every QC run's metrics: off the Tk thread (S2.2)
        self.run_bg("building the QC trend page", lambda: qctrend.page_for(cfg_path), done, key="qc_trend",
                    button=self.qc_btn)

    def check_fragpipe(self):
        """The full preflight (D59): FragPipe is started for its version and a dry run per method; minutes. Once at a
        time (run_bg refuses a second start while it runs)."""
        from ionomos import preflight

        cfg_path = self.config_path

        def go():
            cfg = load(cfg_path, check_paths=False)
            return preflight.text(preflight.run(cfg))  # same as `ionomos preflight`

        def done(text, err):
            self.jdetail.write(text if err is None else f"could not check: {err}\n→ Press Save first if the config is "
                               "new; the Setup tab's Check everything says what else is missing.", clear=True)

        if self.run_bg("checking the FragPipe install", go, done, key="preflight", button=self.check_fp_btn):
            self.jdetail.write("checking FragPipe: the installation, then FragPipe itself is started for its version "
                               "and a dry run of each method (no search; up to a few minutes)…", clear=True)

    def _tab_help(self):
        f = ttk.Frame(self.nb, padding=10)
        self.tab_help = f
        self.nb.add(f, text="  Help  ")
        top = ttk.Frame(f)
        top.pack(fill="x", pady=(0, 8))
        ttk.Button(top, text="Open the full help in the browser", command=self.open_help).pack(side="left")
        ttk.Label(top, text="  For everyone in the lab: naming folders, reading the report, a glossary, what every "
                            "message means. Below: setting Ionomos up.", foreground="#555").pack(side="left")
        t = scrolledtext.ScrolledText(f, wrap="word", font=("Menlo" if sys.platform == "darwin" else "Consolas", 10))
        t.pack(fill="both", expand=True)
        from ionomos import uinav

        t.insert("1.0", uinav.shortcuts_text() + "\n" + HELP_TEXT)
        t.configure(state="disabled")

    def open_help(self, topic: str | None = None):
        """The full help page (ionomos/help) in the browser; the same page `ionomos help --open` writes."""
        from ionomos import help as helpdoc

        try:
            helpdoc.open_help(topic, log_dir=self.v("paths.log_dir").get().strip() or None,
                              opener=lambda p: self._open(p))
        except OSError as exc:
            messagebox.showerror("Help", f"Could not write the help page:\n{exc}\n\n→ Check the logs folder on the Folders "
                                         "tab exists and can be written to, then press Help again; the same page is at "
                                         f"{names.REPO_URL}/blob/master/docs/HELP.md.")

    # -------------------------------------------------------- bottom bar ----

    def _bottom_bar(self):
        b = ttk.Frame(self.root, padding=(8, 6))
        b.pack(side="bottom", fill="x", before=self.nb)  # packed first: a small screen shrinks the tabs, not Save
        left = (ttk.Label(b, text="Config:"), ttk.Label(b, textvariable=self.v("config_path"), foreground="#444"),
                ttk.Button(b, text="Change…", width=9, command=self.change_config))
        self.status_lbl = ttk.Label(b, text="", foreground="#2e7d32")
        ttk.Button(b, text="Save & Check", command=self.save_and_check).pack(side="right", padx=4)
        self.save_btn = ttk.Button(b, text="Save", command=lambda: self.save(confirm=True))
        self.save_btn.pack(side="right", padx=4)
        ttk.Button(b, text="Reload", command=self.reload).pack(side="right", padx=4)
        self.report_btn = ttk.Button(b, text="Report a problem…", command=self.report_problem)
        self.report_btn.pack(side="right", padx=(4, 16))
        self.help_btn = ttk.Button(b, text="Help", command=self.open_help)
        self.help_btn.pack(side="right", padx=4)
        self.attn_btn = tk.Button(b, text="", command=self.show_attention, foreground="#b26a00", relief="groove")
        self.update_btn = ttk.Button(b, text="", command=self.install_update)  # shown when an update is downloaded
        # The left side is packed after the buttons: a long config path or status line is cut, never Save, Help
        # or the "needs attention" badge (seen on a 1366x768 screen, docs/UI.md).
        for w, pad in zip(left, (0, 4, 0), strict=True):
            w.pack(side="left", padx=pad)
        self.status_lbl.pack(side="left", padx=16)
        # the one busy indicator (run_bg, S2.3): "working: <label>…" and the stock progress bar, only while working
        self.busy_lbl = ttk.Label(b, text="", foreground="#1565c0")
        self.busy_bar = ttk.Progressbar(b, mode="indeterminate", length=90)
        b2 = ttk.Frame(self.root, padding=(8, 0, 8, 6))
        b2.pack(side="bottom", fill="x", before=b)
        from ionomos.buildinfo import one_line

        ttk.Label(b2, text=one_line(), foreground="#888").pack(side="left")
        # the watcher and FragPipe state on every tab (S1.7); the same text as Run & Test, in its green/red
        self.strip_lbl = ttk.Label(b2, text="", foreground="#888")
        self.strip_lbl.pack(side="left", padx=(24, 0))
        self._bottom = b

    def change_config(self, path: Path | str | None = None):
        """Switch to another config.yaml (unsaved changes are asked about first, S2.6). path: for tests."""
        if not self._guard_unsaved("switching config"):
            return
        r = path or filedialog.asksaveasfilename(title="Config file to use", defaultextension=".yaml",
                                                 initialfile="config.yaml", confirmoverwrite=False)
        if not r:
            return
        self.config_path = Path(r)
        self.first_run = not self.config_path.is_file()
        self.data, self.config_error = self._read_config(self.config_path)
        self._load_vars()
        self._saved_text = configio.dump_config(self.data)
        self._refresh_users()
        self._refresh_methods()
        self._update_title()
        if self.config_error:
            self.set_status(f"{self.config_error} (the settings shown are the defaults)", ok=False)
            return
        self.set_status("switched config" + (" (new file — press Save to create it)" if self.first_run else ""))

    def reload(self, ask: bool = True):
        if ask and not self._guard_unsaved("reloading from disk"):
            return
        self.data, self.config_error = self._read_config(self.config_path)
        self._load_vars()
        self._saved_text = configio.dump_config(self.data)
        self._refresh_users()
        self._refresh_methods()
        self._update_title()
        if self.config_error:
            self.set_status(f"{self.config_error} (the settings shown are the defaults)", ok=False)
            return
        self.set_status("reloaded from disk")

    def save(self, confirm: bool = False) -> bool:
        """Validate a candidate file first; the real config.yaml is only replaced if it passes. confirm=True (the
        Save button, Ctrl+S): first show what changes, in words, and let Cancel go back (S2.5). Nothing to change:
        nothing is written."""
        probe = self.config_path.with_name(f".{self.config_path.stem}.checking.yaml")
        try:
            d = self._collect()
            if self.config_path.is_file():  # a file that does not parse (#136) counts as the defaults: Save replaces it
                changes = configio.diff_words(self._read_config(self.config_path)[0], d)
                if not changes:
                    self.set_status("nothing to save — config.yaml already has what this window shows")
                    self._update_title()
                    return True
            else:
                changes = configio.diff_words(configio.defaults(), d) or ["a new config.yaml with the defaults"]
            if confirm and (len(changes) > 1 or self._confirm_single()) and not self._confirm_changes(changes):
                self.set_status("not saved — config.yaml unchanged", ok=False)
                return False
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            probe.write_text(configio.dump_config(d), encoding="utf-8")
            cfg = load(probe, check_paths=True)
            configio.write_config(self.config_path, d)
        except ConfigError as exc:
            messagebox.showerror("Config problem", f"{exc}\n\n→ Fix that setting (a red dot marks a field the loader "
                                                   "refuses), then Save again. config.yaml is unchanged.")
            self.set_status("not saved — fix the problem (config.yaml unchanged)", ok=False)
            return False
        except OSError as exc:
            messagebox.showerror("Save", f"Could not write {self.config_path}:\n{exc}\n\n→ Check the folder exists and "
                                         "this Windows account can write to it (or close a program that has the file "
                                         "open), then Save again.")
            return False
        finally:
            try:
                probe.unlink()
            except OSError:
                pass
        self.data = d
        self._saved_text = configio.dump_config(d)
        service.remember_config_path(self.config_path)
        self.first_run = False
        self.v("config_path").set(str(self.config_path))
        self._refresh_methods()
        self._update_title()
        warn = f"  ({len(cfg.warnings)} warning(s) — run Check)" if cfg.warnings else ""
        n = len(changes)
        self.set_status(f"saved {self.config_path.name}: {n} setting{'s' if n != 1 else ''} changed{warn}  "
                        "(Undo last save: Advanced → Config file)")
        log.info("saved %s (%d changes)", self.config_path, n)
        self.root.after(50, self.refresh_setup)  # the checklist must never show a problem that's already fixed
        return True

    def _confirm_single(self) -> bool:
        """Whether Save shows the "what changes" window for a single changed setting (a tick in that window)."""
        from ionomos import uinav

        return uinav.load_state(uinav.state_path(self.config_path)).get("confirm_single", True)

    def _confirm_changes(self, changes: list[str]) -> bool:
        """"What will change": the changes in words, OK saves, Cancel goes back (S2.5). Waits for the answer."""
        from ionomos import uinav

        win = tk.Toplevel(self.root)
        win.title("Save: what will change")
        win.transient(self.root)
        tkutil.fit_window(win, 96, 8 + min(len(changes), 14))
        f = ttk.Frame(win, padding=10)
        f.pack(fill="both", expand=True)
        f.columnconfigure(0, weight=1)
        f.rowconfigure(1, weight=1)
        n = len(changes)
        ttk.Label(f, text=f"{n} setting{'s' if n != 1 else ''} will change in {self.config_path.name}. The version "
                          "before it is kept in config-backups/ (Undo last save).", wraplength=700,
                  justify="left").grid(row=0, column=0, sticky="w")
        box = scrolledtext.ScrolledText(f, height=min(len(changes), 14) + 1, wrap="word",
                                        font=("Menlo" if sys.platform == "darwin" else "Consolas", 10))
        box.insert("1.0", "\n".join(changes))
        box.configure(state="disabled")
        box.grid(row=1, column=0, sticky="nsew", pady=6)
        single = tkutil.BooleanVar(master=win, value=not self._confirm_single())
        ttk.Checkbutton(f, text="Don't ask when only one setting changes", variable=single).grid(row=2, column=0, sticky="w")
        answer = {"ok": False}
        b = ttk.Frame(f)
        b.grid(row=3, column=0, sticky="ew", pady=(8, 0))

        def ok(_e=None):
            answer["ok"] = True
            win.destroy()

        ttk.Button(b, text="Save", command=ok).pack(side="right", padx=2)
        ttk.Button(b, text="Cancel", command=win.destroy).pack(side="right", padx=2)
        win.bind("<Return>", ok)
        win.bind("<Escape>", lambda e: win.destroy())
        win.protocol("WM_DELETE_WINDOW", win.destroy)
        self.confirm_win = win  # tests
        try:
            win.grab_set()
            win.focus_force()
        except tk.TclError:
            pass
        self.root.wait_window(win)
        p = uinav.state_path(self.config_path)
        state = uinav.load_state(p)
        if state.get("confirm_single", True) == single.get():  # the tick changed: remember it (a UI preference, D93)
            uinav.save_state(p, {**state, "confirm_single": not single.get()})
        return answer["ok"]

    def save_and_check(self):
        if self.save(confirm=True):
            self.nb.select(self.tab_run)
            self._cli(["check"])

    def on_close(self):
        """Quit: unsaved changes are asked about (S2.6); a watcher started here is stopped on a worker thread while
        the window is already hidden (up to 15 s, S2.2); then the window closes."""
        if not self._guard_unsaved("quitting"):
            return
        self._save_ui_state()  # window size and the tab in use, next to config.yaml (uinav.py)
        try:
            from ionomos import attention

            self.popups.stop()
            attention.clear_app_alive(self._active_log_dir())
        except Exception:  # noqa: BLE001
            pass
        if self.proc is not None and self.proc.poll() is None and messagebox.askyesno(
                "Quit", "A watcher started from this app is running. Stop it and quit?\n(No = quit and leave it running)"):
            proc, log_dir = self.proc, self._active_log_dir()
            try:
                self.root.withdraw()
            except tk.TclError:
                pass

            def stop():
                try:
                    service.request_stop(log_dir, proc=proc, timeout=15)
                finally:
                    self.post(self.root.destroy)

            threading.Thread(target=stop, daemon=True, name="app-quit").start()
            return
        self.root.destroy()


def _setup_app_log() -> None:
    """The app's own log (%APPDATA%/Ionomos/app.log): what was clicked, what failed. Goes into every report."""
    from ionomos import health

    try:
        fh = health.rotating_log_handler(service.appdata_dir() / "app.log", max_bytes=1_000_000, backups=2)
    except OSError:
        return
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    lg = logging.getLogger("ionomos")
    lg.addHandler(fh)
    if lg.level == logging.NOTSET or lg.level > logging.INFO:
        lg.setLevel(logging.INFO)


def main(config_path: Path | None = None) -> int:
    _setup_app_log()
    root = tk.Tk()
    try:
        App(root, config_path)
    except Exception as exc:  # noqa: BLE001 - the windowed exe has no console: say it before dying
        log.exception("the app could not start")
        try:
            messagebox.showerror("Ionomos could not start", f"{type(exc).__name__}: {exc}\n\nThe details are in "
                                 f"{service.appdata_dir() / 'app.log'}.\n\n→ Start Ionomos again; if it happens "
                                 "again, send that file with Report a problem (ionomos-cli.exe diagnose --zip).")
        except tk.TclError:
            pass
        raise
    root.mainloop()
    return 0


def _local(iso: str | None) -> str:
    """UTC ISO timestamp -> local 'MM-DD HH:MM'."""
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%m-%d %H:%M")
    except (TypeError, ValueError):
        return ""


def _duration(start: str | None, end: str | None) -> str:
    from datetime import UTC

    try:
        a = datetime.fromisoformat(start)
        b = datetime.fromisoformat(end) if end else datetime.now(UTC)
    except (TypeError, ValueError):
        return ""
    mins = max(0, int((b - a).total_seconds() // 60))
    return f"{mins // 60}h{mins % 60:02d}" if mins >= 60 else f"{mins} min"


def parse_v(v: str) -> tuple[int, ...]:
    from ionomos.updates import parse_version

    return parse_version(v)
