"""
LabWatch app — setup wizard on first run, control panel afterwards.

    labwatch setup [--config PATH]        (or just double-click LabWatch.exe)

Tabs:  1 Folders · 2 Users · 3 Methods · 4 Advanced · 5 Run & Test · Help
Bottom bar: config path, Reload, Save, Save & Check.

Pure tkinter/ttk. Edits a plain dict (labwatch.configio) and writes a
commented config.yaml; validation is done by labwatch.config.load so the app
can never write something the watcher wouldn't accept.
"""
from __future__ import annotations

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

from labwatch import __version__, configio, service
from labwatch.config import ConfigError, load

PAD = {"padx": 6, "pady": 3}
IS_WIN = os.name == "nt"

HELP_TEXT = """\
LABWATCH — what it does
  Lab members drag an experiment folder (raw files inside) into the INBOX.
  LabWatch waits for the copy to finish, works out WHO it belongs to and WHICH
  METHOD (isoDTB / TMT / DIA) from the names, moves it to
  <users_root>/<user>/<experiment>/ and queues it for FragPipe.
  If it can't tell, a small window asks you.

START HERE: the ✓ Setup tab
  A live checklist of everything LabWatch needs (folders, people, FragPipe,
  workflows + FASTA with decoys, disk space, watcher, startup task). Each open
  item has a button. "Auto-setup" does the standard layout in one click.

FIRST-TIME SETUP (this app, tabs left to right)
  1 Folders   Pick or create the inbox (the shared folder people drop into),
              the users folder, and the working folders. "Create all missing"
              makes them in one click. Point fragpipe_exe at FragPipe's
              headless launcher (only needed once searches are enabled).
  2 Users     One subfolder per person under users_root. Add the initials each
              person uses in folder names so "IJD05_isoDTB" -> Isaac.
  3 Methods   For each method: which .workflow file (exported from the FragPipe
              GUI via Workflow -> Save to custom folder, AFTER setting the FASTA)
              and which FASTA. Keywords are what LabWatch looks for in names.
  4 Advanced  Timings, FragPipe threads/RAM, the resolver window, etc.
  5 Run       Save, run Check (all green?), Start the watcher, and install the
              startup task so it runs every time this account logs in.
              The watcher also runs FragPipe on each filed experiment, one at
              a time (Advanced -> "Run FragPipe automatically" turns that off).
  Then drop a test folder into the inbox and watch it move.

NAMING RULES (tell the lab)
  Folder: anything containing your initials and isoDTB / TMT / DIA.
          e.g. 20260902_EJQ_isoDTB_EJQ-2-027_1uM-3h
  Files:  isoDTB  <sample>_<rep>_<fraction>.raw     (X_1_1.raw ... X_3_7.raw)
          TMT     <sample>_TMT_F<fraction>.raw      (all reps are in channels)
          DIA     <condition>_<biorep>.raw          (DMSO_1.raw, Drug_2.raw)
  Spaces are fine — they are removed on the way in.
  If LabWatch can't work it out, a window pops up. Answers are saved in an
  experiment.yaml inside the folder, so it never asks the same thing twice.

TESTING WITHOUT REAL DATA
  Run & Test tab -> Testbed: "Create testbed" builds a fake lab with sample
  drops and a fake FragPipe. Start the testbed watcher, then Drop samples and
  watch them move (or open the folders and drag them yourself).

UPDATING (while the tool is being developed)
  If this app was installed with deploy\\dev_install.ps1 it runs straight from
  a copy of the GitHub repository, and the Run & Test tab shows a
  "Development install" box. When a fix is pushed from the Mac, press
  "Update from GitHub & restart" — that is the whole upgrade. Settings, the
  ledger and lab data are never touched by an update.

REPORTING A PROBLEM
  Run & Test tab -> "Save diagnostics bundle (.zip)" — one file with the
  report, config, logs, crash reports and the FragPipe logs of failed or
  running jobs (never raw data). Or "Copy diagnostics" for the text only.
  Send it with a sentence about what you expected.

FRAGPIPE SEARCHES
  Each filed experiment is searched with its method's workflow + FASTA.
  Inside the experiment folder you get:
    labwatch_run/   the manifest, the workflow as used, FragPipe's console log
    fragpipe/       FragPipe's output
    DONE.txt or FAILED.txt   one-line result (FAILED says why)
  A job "waiting: ..." is missing something outside the job (FragPipe
  launcher, workflow, FASTA, free disk space) and starts by itself once
  that's fixed. Re-runs keep old output as fragpipe_previous_<time>/.

RESULTS: STATISTICS, VOLCANO PLOTS, REPORT (tab 7 sets the defaults)
  After each search, results/ inside the experiment folder gets:
    report.html                 open this — volcano plot(s), hit tables, QC, methods text
    <comparison>_differential.tsv   every protein/site: log2FC, p, q, up/down
    volcano_<comparison>.svg    the plot on its own (slides, papers)
    <sample>_sites.tsv          isoDTB sites — identical to the lab's R script
    experimental_annotation.tsv TMT — identical to the lab's R script
  Conditions come from the file names (DMSO_1.raw -> DMSO). The control is
  recognised by name (DMSO, vehicle, ctrl, WT, ...) and every other condition
  is compared with it. isoDTB ratios are tested against 0. Choose comparisons
  for one experiment in its experiment.yaml:
      analysis:
        comparisons: ["Drug vs DMSO", "Drug2 vs DMSO"]
  Jobs tab -> Re-run analysis applies new settings to a finished job.
  Analysis tab -> Analyse a folder… works on any FragPipe output folder,
  including runs from before LabWatch.

JOBS TAB (6)
  Every job, live: status, how long it ran, and what FragPipe is doing now.
  Select one to see details and — for a failed job — the MOST LIKELY CAUSE
  in plain English (out of memory, MSFragger not installed, disk full, file
  open in another program, ...). Buttons: Open folder, FragPipe log, Retry,
  Cancel (stops a running search), Copy details.
  Pause searches: no new FragPipe runs start (the running one finishes) —
  e.g. while someone needs the PC. Resume to continue.
  Check FragPipe install: finds FragPipe's version, bundled Java, MSFragger,
  IonQuant, diaTracer, DIA-NN, and checks each method's workflow + FASTA
  (including whether the FASTA has decoys).

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
    experiment folders if it's ever damaged (labwatch repair-ledger).
  - Every Save keeps the previous config in config-backups/
    (Advanced -> Restore a previous config…). An invalid config is never
    written over a good one.

WHERE THINGS ARE
  config.yaml        all settings (path shown at the bottom of this window)
  logs/labwatch.log  what the watcher did
  labwatch.db        job ledger (labwatch status)
  <folder>/labwatch.json    status + provenance next to each experiment
  <inbox>/<name>.REJECTED.txt   why a folder was not taken; fix it or delete the note

COMMAND LINE (same program; labwatch-cli.exe on Windows)
  labwatch check | status | dry-run <folder> | run | retry <id> | cancel <id>
  labwatch pause | resume | diagnose [--zip] | repair-ledger | testbed ...
  labwatch testbed stress   many messy drops + chaos, then checks nothing was lost
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
            messagebox.showerror("Create folder", str(exc))
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
        self.first_run = not self.config_path.is_file()
        self.data = configio.read_config(self.config_path)
        self.proc = None  # child watcher process started from here
        self.proc_config: Path | None = None
        self.vars: dict[str, tk.Variable] = {}
        self._follow_job = None
        self._ui_q: queue.Queue = queue.Queue()  # worker threads post callables here

        root.title(f"LabWatch {__version__} — setup & control")
        root.minsize(900, 640)
        try:
            ttk.Style().theme_use("vista" if IS_WIN else "aqua" if sys.platform == "darwin" else "clam")
        except tk.TclError:
            pass

        self.nb = ttk.Notebook(root)
        self.nb.pack(fill="both", expand=True, padx=8, pady=(8, 0))
        self._tab_setup()
        self._tab_folders()
        self._tab_users()
        self._tab_methods()
        self._tab_advanced()
        self._tab_run()
        self._tab_jobs()
        self._tab_analysis()
        self._tab_help()
        self._bottom_bar()
        self._load_vars()
        self._refresh_users()
        self._refresh_methods()
        self._refresh_status()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.report_callback_exception = self._on_tk_error
        root.after(100, self._pump_ui)
        self.nb.select(0)  # the checklist: always the first thing you see
        self.root.after(300, self.refresh_setup)

    def post(self, fn) -> None:
        """Thread-safe: run fn on the Tk thread soon."""
        self._ui_q.put(fn)

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

        from labwatch import health

        text = "".join(traceback.format_exception(exc_type, exc, tb))
        log_dir = None
        try:
            log_dir = self._log_dir()
        except Exception:  # noqa: BLE001
            pass
        where = health.write_crash_file(log_dir if log_dir and log_dir.is_dir() else None, "app", text)
        try:
            self.out.write("INTERNAL ERROR (the app keeps running):\n" + text, clear=True)
        except Exception:  # noqa: BLE001
            pass
        messagebox.showerror("LabWatch — something went wrong",
                             f"{exc_type.__name__}: {exc}\n\nThe app keeps running. Details"
                             + (f" were saved to\n{where}" if where else " are in the output pane on Run & Test")
                             + ".\nIf it keeps happening: Run & Test -> Save diagnostics bundle, and send it.")

    # -------------------------------------------------------- helpers ----

    def v(self, key: str, default="") -> tk.StringVar:
        if key not in self.vars:
            self.vars[key] = tk.StringVar(value=str(default))
        return self.vars[key]

    def bv(self, key: str, default=False) -> tk.BooleanVar:
        if key not in self.vars:
            self.vars[key] = tk.BooleanVar(value=bool(default))
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
        an = d.get("analysis") or {}
        for k in ("test", "log2fc", "alpha", "min_valid", "normalize", "top_labels"):
            self.v(f"analysis.{k}").set("" if an.get(k) is None else str(an.get(k)))
        self.bv("analysis.enabled", True).set(bool(an.get("enabled", True)))
        self.bv("analysis.use_adjusted", True).set(bool(an.get("use_adjusted", True)))
        self.v("analysis.control_keywords").set(", ".join(an.get("control_keywords") or []))
        self.v("users.default").set(d["users"].get("default", "") or "")
        self.v("users.learned_aliases_file").set(d["users"].get("learned_aliases_file", "") or "")
        self.v("config_path").set(str(self.config_path))

    def _collect(self) -> dict:
        d = configio.read_config(self.config_path) if self.config_path.is_file() else configio.defaults()
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
        d["gui"]["enabled"] = self.bv("gui.enabled").get()
        try:
            d["gui"]["timeout_minutes"] = float(self.v("gui.timeout_minutes").get().strip() or 0)
        except ValueError:
            raise ConfigError("gui.timeout_minutes must be a number") from None
        d["users"]["aliases"] = {u: list(a) for u, a in self.data["users"].get("aliases", {}).items() if a}
        d["users"]["default"] = self.v("users.default").get().strip()
        an = dict(d.get("analysis") or {})
        an["enabled"] = self.bv("analysis.enabled", True).get()
        an["use_adjusted"] = self.bv("analysis.use_adjusted", True).get()
        for k, conv in (("log2fc", float), ("alpha", float), ("min_valid", int), ("top_labels", int)):
            raw = self.v(f"analysis.{k}").get().strip()
            try:
                an[k] = conv(raw)
            except ValueError:
                raise ConfigError(f"analysis.{k} must be a number, got {raw!r}") from None
        an["test"] = self.v("analysis.test").get().strip() or "moderated"
        an["normalize"] = self.v("analysis.normalize").get().strip() or "median"
        an["control_keywords"] = [x.strip() for x in self.v("analysis.control_keywords").get().split(",") if x.strip()]
        d["analysis"] = an
        d["users"]["learned_aliases_file"] = self.v("users.learned_aliases_file").get().strip()
        d["methods"] = self.data["methods"]
        return d

    def set_status(self, msg: str, ok: bool = True):
        self.status_lbl.configure(text=msg, foreground="#2e7d32" if ok else "#c62828")

    # ------------------------------------------------------ tab: folders ----

    def _tab_folders(self):
        f = ttk.Frame(self.nb, padding=10)
        self.tab_folders = f
        self.nb.add(f, text="  1  Folders  ")
        f.columnconfigure(1, weight=1)
        r = 0
        if self.first_run:
            ttk.Label(f, text="Welcome — let's set up LabWatch. Pick where things live, then go tab by tab. "
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
            ("paths.users_root", "Users folder", "dir", "Experiments are moved to <users folder>/<user>/<experiment>. One subfolder per person (tab 2)."),
            ("paths.workflow_dir", "Workflows folder", "dir", "Pinned FragPipe .workflow files, one per method (tab 3)."),
            ("paths.fasta_dir", "FASTA folder", "dir", "Protein databases (with decoys) the workflows use."),
            ("paths.log_dir", "Logs folder", "dir", ""),
            ("paths.database", "Job ledger (SQLite file)", "save", "Created automatically; its folder must exist."),
            ("paths.fragpipe_exe", "FragPipe launcher", "file", "fragpipe.bat in FragPipe's bin folder, e.g. C:/FragPipe/FragPipe-24.0/fragpipe/bin/fragpipe.bat — 'Find FragPipe' looks for it."),
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
        from labwatch.fragpipe import detect_launcher

        found = detect_launcher()
        if found:
            self.v("paths.fragpipe_exe").set(str(found).replace("\\", "/"))
            self.set_status(f"found FragPipe: {found} (press Save)")
        else:
            messagebox.showinfo("Find FragPipe", "No fragpipe.bat found under C:/FragPipe or your Downloads.\n"
                                "Use Browse… and pick <FragPipe folder>/fragpipe/bin/fragpipe.bat.")

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
                    messagebox.showerror("Create folders", f"{target}: {exc}")
            row.refresh()
        self._refresh_users()
        self.set_status(f"created {len(made)} folder(s)" if made else "all folders already exist")

    def _open(self, p: str):
        if p and Path(p).exists():
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
        users = sorted(p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")) if root.is_dir() else []
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

                d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
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
            messagebox.showerror("Add user", "No spaces in user folder names (FragPipe paths).")
            return
        root = self._users_root()
        try:
            (root / name).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("Add user", str(exc))
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
        ttk.Label(f, text="Each method = keywords LabWatch looks for in names + the FragPipe workflow and FASTA "
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
        self.m_wf = ttk.Combobox(e, textvariable=self.v("m.workflow"), width=30)
        self.m_wf.grid(row=1, column=1, sticky="ew", **PAD)
        ttk.Label(e, text="FASTA file").grid(row=1, column=2, sticky="e", **PAD)
        self.m_fa = ttk.Combobox(e, textvariable=self.v("m.fasta"), width=30)
        self.m_fa.grid(row=1, column=3, sticky="ew", **PAD)
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
        ttk.Button(b, text="Import workflow…", command=self.import_workflow).pack(side="left", padx=12)
        ttk.Button(b, text="Open workflows folder", command=lambda: self._open(self.v("paths.workflow_dir").get())).pack(side="left", padx=4)
        ttk.Button(b, text="Open FASTA folder", command=lambda: self._open(self.v("paths.fasta_dir").get())).pack(side="left", padx=4)

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
        from labwatch import fragpipe

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
        from labwatch import fragpipe

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
        try:
            res = fragpipe.import_workflow(Path(src), key, wf_dir, fa_dir)
        except OSError as exc:
            messagebox.showerror("Import workflow", f"Could not import:\n{exc}")
            return
        self.v("m.workflow").set(res["workflow"])
        if res["fasta"]:
            self.v("m.fasta").set(res["fasta"])
        self._refresh_methods()
        self.apply_method()
        self._describe_method(res["workflow"], self.v("m.fasta").get())
        messagebox.showinfo("Import workflow", f"{key} now uses {res['workflow']}"
                            + (f" and FASTA {res['fasta']}" if res["fasta"] else "") + ".\n\n"
                            + "\n".join(res["notes"]) + "\n\nPress Save to keep it.")

    def apply_method(self):
        key = self.v("m.key").get().strip()
        if not key or " " in key:
            messagebox.showerror("Method", "Name is required and may not contain spaces.")
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
            messagebox.showerror("Method", "Workflow file and FASTA file are required.")
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
            messagebox.showerror("Method", "At least one method is required.")
            return
        if messagebox.askyesno("Remove method", f"Remove {sel[0]}?"):
            self.data["methods"].pop(sel[0], None)
            self._refresh_methods()

    # ----------------------------------------------------- tab: advanced ----

    def _tab_advanced(self):
        f = ttk.Frame(self.nb, padding=10)
        self.nb.add(f, text="  4  Advanced  ")
        f.columnconfigure(0, weight=1)
        f.columnconfigure(1, weight=1)

        def group(title, col, row):
            g = ttk.LabelFrame(f, text=title, padding=6)
            g.grid(row=row, column=col, sticky="nsew", **PAD)
            g.columnconfigure(1, weight=1)
            return g

        def num(g, r, key, label, hint):
            ttk.Label(g, text=label).grid(row=r, column=0, sticky="e", **PAD)
            ttk.Entry(g, textvariable=self.v(key), width=10).grid(row=r, column=1, sticky="w", **PAD)
            ttk.Label(g, text=hint, foreground="#666", wraplength=300).grid(row=r, column=2, sticky="w", **PAD)

        w = group("Watcher", 0, 0)
        num(w, 0, "watcher.poll_seconds", "Poll every (s)", "how often the inbox is scanned")
        num(w, 1, "watcher.stable_seconds", "Stable for (s)", "folder must be unchanged this long before it is taken — be generous for USB/network copies")
        num(w, 2, "watcher.min_raw_files", "Min .raw files", "folders with fewer are ignored (left in the inbox)")

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
        ttk.Label(fp, text="Off = experiments are only filed and queued. Tools folder / DIA-NN exe are optional: "
                           "only if the first headless run can't find MSFragger / DIA-NN. Restart the watcher after changes.",
                  foreground="#666", wraplength=380).grid(row=7, column=0, columnspan=3, sticky="w", padx=6)

        g = group("Resolver window", 0, 1)
        ttk.Checkbutton(g, text="Open a window when a folder can't be interpreted", variable=self.bv("gui.enabled")).grid(row=0, column=0, columnspan=3, sticky="w", **PAD)
        num(g, 1, "gui.timeout_minutes", "Auto-skip after (min)", "0 = wait for a person; otherwise reject with a note after N minutes")
        ttk.Label(g, text="Needs the watcher to run in the logged-in session (the startup task does this).",
                  foreground="#666", wraplength=380).grid(row=2, column=0, columnspan=3, sticky="w", padx=6)

        u = group("Users (advanced)", 1, 1)
        ttk.Label(u, text="Learned aliases file").grid(row=0, column=0, sticky="e", **PAD)
        ttk.Entry(u, textvariable=self.v("users.learned_aliases_file"), width=34).grid(row=0, column=1, columnspan=2, sticky="ew", **PAD)
        ttk.Label(u, text="where 'remember that XYZ means …' answers are stored", foreground="#666").grid(row=1, column=1, columnspan=2, sticky="w", padx=6)

        m = group("Config file", 0, 2)
        ttk.Button(m, text="Open config.yaml in editor", command=lambda: self._open(str(self.config_path))).grid(row=0, column=0, **PAD)
        ttk.Button(m, text="Open logs folder", command=lambda: self._open(self.v("paths.log_dir").get())).grid(row=0, column=1, **PAD)
        ttk.Button(m, text="Reset all to defaults", command=self.reset_defaults).grid(row=0, column=2, **PAD)
        ttk.Button(m, text="Restore a previous config…", command=self.restore_config).grid(row=0, column=3, **PAD)
        ttk.Label(m, text="Every setting here is also a plain key in config.yaml (see comments in the file). "
                          "Each Save keeps the previous version in config-backups/ next to it.",
                  foreground="#666", wraplength=380).grid(row=1, column=0, columnspan=3, sticky="w", padx=6)

    def restore_config(self):
        d = self.config_path.parent / configio.BACKUP_DIR
        r = filedialog.askopenfilename(title="Pick a saved config to restore", initialdir=str(d if d.is_dir() else self.config_path.parent),
                                       filetypes=[("config", "*.yaml"), ("all", "*.*")])
        if not r:
            return
        try:
            self.data = configio.read_config(Path(r))
        except Exception as exc:  # noqa: BLE001 - show any parse problem
            messagebox.showerror("Restore", f"Could not read {r}:\n{exc}")
            return
        self._load_vars()
        self._refresh_users()
        self._refresh_methods()
        self.set_status(f"loaded {Path(r).name} — press Save to make it the active config")

    def reset_defaults(self):
        if messagebox.askyesno("Reset", "Reset all settings to defaults? (Not saved until you press Save.)"):
            self.data = configio.defaults()
            self._load_vars()
            self._refresh_users()
            self._refresh_methods()

    # ---------------------------------------------------------- tab: run ----

    def _tab_run(self):
        f = ttk.Frame(self.nb, padding=10)
        self.tab_run = f
        self.nb.add(f, text="  5  Run & Test  ")
        f.columnconfigure(0, weight=1)
        f.columnconfigure(1, weight=1)
        f.rowconfigure(3, weight=1)

        w = ttk.LabelFrame(f, text="Watcher", padding=6)
        w.grid(row=0, column=0, sticky="nsew", **PAD)
        self.run_status = ttk.Label(w, text="…", font=("", 10, "bold"))
        self.run_status.grid(row=0, column=0, columnspan=4, sticky="w", **PAD)
        ttk.Button(w, text="Save & Check", command=self.save_and_check).grid(row=1, column=0, **PAD)
        ttk.Button(w, text="Start watcher", command=self.start_watcher).grid(row=1, column=1, **PAD)
        ttk.Button(w, text="Stop watcher", command=self.stop_watcher).grid(row=1, column=2, **PAD)
        ttk.Button(w, text="Show queue (status)", command=lambda: self._cli(["status", "--all"])).grid(row=1, column=3, **PAD)
        ttk.Button(w, text="Copy diagnostics", command=self.copy_diagnostics).grid(row=2, column=0, **PAD)
        ttk.Button(w, text="Save diagnostics bundle (.zip)", command=self.save_bundle).grid(row=2, column=3, **PAD)
        ttk.Button(w, text="Retry a failed job…", command=self.retry_job).grid(row=2, column=1, **PAD)
        ttk.Button(w, text="Open job folder…", command=self.open_job).grid(row=2, column=2, **PAD)
        self.fp_status = ttk.Label(w, text="", wraplength=440)
        self.fp_status.grid(row=4, column=0, columnspan=4, sticky="w", **PAD)
        ttk.Label(w, text="Start = runs in the background until you Stop or log out. Use the startup task to make it "
                          "permanent. Copy diagnostics = one text block (check, status, config, log tail) on the "
                          "clipboard — paste it to whoever is fixing things.",
                  foreground="#666", wraplength=420).grid(row=3, column=0, columnspan=4, sticky="w", padx=6)

        s = ttk.LabelFrame(f, text="Start automatically at logon" + ("" if IS_WIN else " (Windows only)"), padding=6)
        s.grid(row=0, column=1, sticky="nsew", **PAD)
        self.task_status = ttk.Label(s, text="…")
        self.task_status.grid(row=0, column=0, columnspan=3, sticky="w", **PAD)
        ttk.Button(s, text="Install startup task", command=self.install_task).grid(row=1, column=0, **PAD)
        ttk.Button(s, text="Remove", command=self.remove_task).grid(row=1, column=1, **PAD)
        ttk.Button(s, text="Desktop shortcut", command=self.shortcut).grid(row=1, column=2, **PAD)
        ttk.Label(s, text="Registers a Task Scheduler task for this Windows account that runs the watcher "
                          "interactively at every logon (so the resolver window can appear).",
                  foreground="#666", wraplength=420).grid(row=2, column=0, columnspan=3, sticky="w", padx=6)

        self._dev_section(f, row=1)

        t = ttk.LabelFrame(f, text="Testbed — try everything with fake data", padding=6)
        t.grid(row=2, column=0, columnspan=2, sticky="ew", **PAD)
        t.columnconfigure(1, weight=1)
        ttk.Label(t, text="Testbed folder").grid(row=0, column=0, sticky="e", **PAD)
        from labwatch.testbed import default_root as _testbed_default_root

        self.v("tb.dir").set(str(_testbed_default_root().resolve()).replace("\\", "/"))
        ttk.Entry(t, textvariable=self.v("tb.dir"), width=50).grid(row=0, column=1, sticky="ew", **PAD)
        ttk.Button(t, text="Browse…", command=lambda: self._browse_into("tb.dir")).grid(row=0, column=2, **PAD)
        ttk.Button(t, text="Create testbed", command=self.tb_init).grid(row=0, column=3, **PAD)
        ttk.Button(t, text="Open folder", command=lambda: self._open(self.v("tb.dir").get())).grid(row=0, column=4, **PAD)
        from labwatch.testbed import SAMPLES

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
        ttk.Button(bb, text="Stop watcher", command=self.stop_watcher).pack(side="left", padx=4)
        ttk.Button(bb, text="Testbed status", command=lambda: self._cli(["status", "--all"], config=self._tb_cfg())).pack(side="left", padx=4)
        ttk.Button(bb, text="Resolver window demo", command=self.tb_gui_demo).pack(side="left", padx=12)
        ttk.Label(bb, text="1 Create → 2 Start TESTBED watcher → 3 Drop samples (or drag from samples/ into the inbox) → watch the log",
                  foreground="#666").pack(side="left", padx=8)

        o = ttk.Frame(f)
        o.grid(row=3, column=0, columnspan=2, sticky="nsew", **PAD)
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
        try:
            self._refresh_status_once()
        finally:
            self.root.after(2000, self._refresh_status)  # keep refreshing even if one pass failed

    def _refresh_status_once(self):
        from labwatch import health

        txt, ok = "Watcher: stopped", False
        active_log = self._active_log_dir()
        if self.proc is not None and self.proc.poll() is None:
            which = "TESTBED" if self.proc_config != self.config_path else "lab config"
            txt, ok = f"Watcher: RUNNING (started here, {which}, pid {self.proc.pid})", True
        else:
            if self.proc is not None:
                code = self.proc.returncode
                self.proc = None
                if code not in (0, None, -15, 1 if IS_WIN else -15):
                    self.out.write(f"the watcher exited with code {code} — see the log (tick 'follow the watcher log')")
            if health.is_locked(active_log) or service.running_pid(active_log):
                pid = health.holder_pid(active_log) or service.running_pid(active_log)
                txt, ok = f"Watcher: RUNNING (pid {pid}, started elsewhere — startup task?)", True
        if ok:
            hb, healthy = health.heartbeat_summary(active_log)
            if not healthy and "no heartbeat" not in hb:
                txt += f" — NOT RESPONDING ({hb}). Stop and start it again."
                ok = False
        self.run_status.configure(text=txt, foreground="#2e7d32" if ok else "#c62828")
        self.fp_status.configure(text=self._jobs_summary())
        now = time.monotonic()
        if now - getattr(self, "_task_checked", -1e9) > 30:  # schtasks is slow; ask every 30 s
            self._task_checked = now
            st = service.task_status()
            self.task_status.configure(text={"installed": "Startup task: installed", "missing": "Startup task: not installed",
                                             "n/a": "Startup task: n/a on this OS"}[st])

    def _active_cfg(self) -> Path:
        """The config of the watcher started here (e.g. the testbed), else the lab config."""
        return self.proc_config if (self.proc is not None and self.proc.poll() is None) else self.config_path

    def _active_paths(self) -> dict:
        cfg = self._active_cfg()
        try:
            return configio.read_config(cfg)["paths"] if cfg and cfg.is_file() else {}
        except Exception:  # noqa: BLE001 - half-edited config
            return {}

    def _active_log_dir(self) -> Path:
        return Path(self._active_paths().get("log_dir") or self._log_dir())

    def _ledger(self):
        from labwatch.ledger import Ledger, integrity

        db = self._active_paths().get("database")
        if not db or integrity(db) != "ok":
            return None
        return Ledger(db)

    def _jobs_summary(self) -> str:
        led = self._ledger()
        if led is None:
            return "FragPipe: no jobs yet"
        try:
            jobs = led.list()
        finally:
            led.close()
        running = [j for j in jobs if j.status == "running"]
        counts = {s: sum(1 for j in jobs if j.status == s) for s in ("queued", "failed", "done")}
        parts = []
        if running:
            j = running[0]
            try:
                since = datetime.fromisoformat(j.started_at).astimezone().strftime("%H:%M")
            except (TypeError, ValueError):
                since = "?"
            parts.append(f"FragPipe: RUNNING job {j.id} ({j.user}/{j.inbox_name}, {j.method}) since {since}")
        else:
            parts.append("FragPipe: idle")
        parts.append(f"{counts['queued']} queued, {counts['failed']} failed, {counts['done']} done")
        waiting = next((j.reason for j in jobs if j.status == "queued" and (j.reason or "").startswith("waiting:")), None)
        if waiting:
            parts.append(waiting)
        return " · ".join(parts)

    def _pick_job(self, title: str, status: str | None):
        from tkinter import simpledialog

        led = self._ledger()
        if led is None:
            messagebox.showinfo(title, "No jobs yet.")
            return None
        try:
            jobs = led.list(status)
        finally:
            led.close()
        if not jobs:
            messagebox.showinfo(title, f"No {status or ''} jobs.".replace("  ", " "))
            return None
        listing = "\n".join(f"{j.id}: {j.user}/{j.inbox_name}  [{j.status}]" + (f" — {j.reason[:80]}" if j.reason else "")
                            for j in jobs[-12:])
        jid = simpledialog.askinteger(title, f"{listing}\n\nJob number:", initialvalue=jobs[-1].id, parent=self.root)
        return next((j for j in jobs if j.id == jid), None) if jid else None

    def retry_job(self):
        job = self._pick_job("Retry a failed job", "failed")
        if job:
            cfg = self.proc_config if (self.proc is not None and self.proc.poll() is None) else self.config_path
            self._cli(["retry", str(job.id)], config=cfg)

    def open_job(self):
        job = self._pick_job("Open job folder", None)
        if job:
            self._open(job.dest_dir)

    def _tb_cfg(self) -> Path:
        return Path(self.v("tb.dir").get().strip()) / "Fragpipe_Auto" / "config.yaml"

    def start_watcher(self, testbed: bool = False):
        if self.proc is not None and self.proc.poll() is None:
            messagebox.showinfo("Watcher", "A watcher started from this app is already running. Stop it first.")
            return
        cfg = self._tb_cfg() if testbed else self.config_path
        if not cfg.is_file():
            messagebox.showerror("Watcher", f"No config at {cfg}. " + ("Create the testbed first." if testbed else "Save first."))
            return
        from labwatch import health

        target_log = Path(configio.read_config(cfg)["paths"]["log_dir"])
        if health.is_locked(target_log):
            messagebox.showinfo("Watcher", "A watcher is already running for this config (probably the startup task).")
            return
        self.proc = service.start_watcher(cfg)
        self.proc_config = cfg
        self.out.write(f"started watcher pid {self.proc.pid} with {cfg}")
        self.bv("follow").set(True)
        self._toggle_follow()

    def stop_watcher(self):
        from labwatch import health

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
        if self._follow_job:
            self.root.after_cancel(self._follow_job)
            self._follow_job = None
        if self.bv("follow").get():
            self._last_log_size = -1
            self._follow_tick()

    def _follow_tick(self):
        cfg = self.proc_config if (self.proc is not None and self.proc.poll() is None) else self.config_path
        log = (Path(configio.read_config(cfg)["paths"]["log_dir"]) / "labwatch.log") if cfg and cfg.is_file() else None
        if log and log.is_file():
            size = log.stat().st_size
            if size != self._last_log_size:
                try:
                    lines = log.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]
                    self.out.write("── " + str(log) + "\n" + "\n".join(lines), clear=True)
                except OSError:
                    pass
                self._last_log_size = size
        self._follow_job = self.root.after(1500, self._follow_tick)

    def _cli(self, args: list[str], config: Path | None = None):
        cfg = config or self.config_path
        self.bv("follow").set(False)
        self._toggle_follow()
        self.out.write(f"$ labwatch --config {cfg} {' '.join(args)}", clear=True)

        def go():
            code, text = service.run_cli(args, cfg)
            self.post(lambda: self.out.write(text + f"\n(exit {code})"))

        threading.Thread(target=go, daemon=True).start()

    # -- task scheduler / shortcut

    def install_task(self):
        if not self.config_path.is_file():
            messagebox.showerror("Startup task", "Save the config first.")
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

    # -- dev install: update from GitHub / diagnostics

    def _dev_section(self, f, row: int):
        """Shown only when labwatch is imported from a git checkout (deploy/dev_install.ps1)."""
        self.repo = service.source_checkout()
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
        if not os.environ.get("LABWATCH_OFFLINE"):
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
        from labwatch import health

        if self.proc is not None and self.proc.poll() is None:
            service.request_stop(self._active_log_dir(), proc=self.proc)
            self.proc = None
        if health.is_locked(self._log_dir()):
            if not messagebox.askyesno("Update", "The watcher is running. Stop it and update?"):
                return
            service.request_stop(self._log_dir())
        self.bv("follow").set(False)
        self._toggle_follow()
        self.out.write("updating…", clear=True)
        repo = self.repo

        def go():
            ok, msg = service.update_source(repo, log=lambda t: self.post(lambda: self.out.write(t)))
            self.post(lambda: self._after_update(ok, msg))

        threading.Thread(target=go, daemon=True).start()

    def _after_update(self, ok: bool, msg: str):
        self.out.write(msg)
        if not ok:
            messagebox.showerror("Update", msg)
            return
        if messagebox.askyesno("Update", msg + "\n\nRestart LabWatch now to load the new code?\n"
                                          "(Press Start watcher again afterwards.)"):
            service.restart_app()

    def save_bundle(self):
        cfg = self.config_path
        self.out.write("building diagnostics bundle…", clear=True)

        def go():
            try:
                z = service.save_diagnostics_zip(cfg)
                msg = f"saved {z}"
            except Exception as exc:  # noqa: BLE001
                z, msg = None, f"could not build the bundle: {exc}"

            def show():
                self.out.write(msg)
                if z:
                    messagebox.showinfo("Diagnostics bundle", f"Saved:\n{z}\n\nIt contains the report, config, logs and the "
                                        "FragPipe logs of failed/running jobs — no raw data. Send this file.")
                    self._open(str(Path(z).parent))

            self.post(show)

        threading.Thread(target=go, daemon=True).start()

    def copy_diagnostics(self):
        cfg = self.config_path
        self.out.write("collecting diagnostics…", clear=True)

        def go():
            text, where = service.save_diagnostics(cfg)

            def show():
                self.root.clipboard_clear()
                self.root.clipboard_append(text)
                self.out.write(text + ("\n(saved to " + str(where) + ")" if where else ""), clear=True)
                messagebox.showinfo("Diagnostics", "Copied to the clipboard" + (f" and saved to\n{where}" if where else "") +
                                    ".\nPaste it into the chat / issue describing the problem.")

            self.post(show)

        threading.Thread(target=go, daemon=True).start()

    # -- testbed

    def tb_init(self):
        from labwatch import testbed

        d = Path(self.v("tb.dir").get().strip())
        try:
            cfg = testbed.init(d)
        except OSError as exc:
            messagebox.showerror("Testbed", str(exc))
            return
        self.out.write((d / "README.txt").read_text(encoding="utf-8"), clear=True)
        self.out.write(f"testbed ready: {cfg}")

    def tb_drop(self, slow: bool):
        from labwatch import testbed

        d = Path(self.v("tb.dir").get().strip())
        name = self.tb_sample.get()
        if not (d / "Fragpipe_Auto").is_dir():
            messagebox.showerror("Testbed", "Create the testbed first.")
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
        from labwatch import testbed

        d = Path(self.v("tb.dir").get().strip())
        if (d / "Fragpipe_Auto").is_dir() and messagebox.askyesno("Reset testbed", f"Empty inbox/users/ledger/logs in {d}?"):
            testbed.reset(d)
            self.out.write("testbed reset")

    def tb_gui_demo(self):
        from labwatch.intake import Draft, DraftFile, Kind
        from labwatch.resolve import TkResolver

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

    # --------------------------------------------------------- tab: help ----

    # --------------------------------------------------------- tab: setup ----

    _MARK = {"ok": ("✓", "#2e7d32"), "todo": ("○", "#1565c0"), "warn": ("!", "#b26a00"), "fail": ("✗", "#c62828")}

    def _tab_setup(self):
        f = ttk.Frame(self.nb, padding=12)
        self.tab_setup = f
        self.nb.add(f, text="  ✓ Setup  ")
        f.columnconfigure(0, weight=1)
        f.rowconfigure(2, weight=1)
        top = ttk.Frame(f)
        top.grid(row=0, column=0, sticky="ew")
        ttk.Label(top, text="Setup checklist", font=("", 15, "bold")).pack(side="left")
        ttk.Button(top, text="Check again", command=self.refresh_setup).pack(side="right", padx=4)
        ttk.Button(top, text="Auto-setup", command=self.auto_setup).pack(side="right", padx=4)
        self.setup_summary = ttk.Label(f, text="checking…", font=("", 11), wraplength=860, justify="left")
        self.setup_summary.grid(row=1, column=0, sticky="w", pady=(4, 10))
        self.setup_list = ttk.Frame(f)
        self.setup_list.grid(row=2, column=0, sticky="nsew")
        self.setup_list.columnconfigure(2, weight=1)
        ttk.Label(f, text="Auto-setup: standard folders (C:/Fragpipe_Auto + C:/Fragpipe_General), creates them, "
                          "finds FragPipe, saves. Then add people (tab 2) and import one workflow per method (tab 3). "
                          "Everything else on this list has a button.", foreground="#666", wraplength=860,
                  justify="left").grid(row=3, column=0, sticky="w", pady=(10, 0))

    def refresh_setup(self):
        """Run the checklist off the Tk thread (it touches disks, the lock file, schtasks)."""
        from labwatch import setupcheck

        try:
            data = self._collect()
        except ConfigError as exc:
            self.setup_summary.configure(text=f"A setting isn't valid yet: {exc}", foreground="#c62828")
            return
        cfg_path = self.config_path

        def go():
            try:
                items = setupcheck.run(cfg_path, data)
                text = setupcheck.summary(items)
            except Exception as exc:  # noqa: BLE001
                items, text = [], f"could not run the checklist: {exc}"
            self.post(lambda: self._show_setup(items, text))

        threading.Thread(target=go, daemon=True).start()

    def _show_setup(self, items, text):
        for w in self.setup_list.winfo_children():
            w.destroy()
        self.setup_items = items
        for r, it in enumerate(items):
            sym, color = self._MARK[it.status]
            ttk.Label(self.setup_list, text=sym, foreground=color, font=("", 13, "bold"), width=2).grid(
                row=r, column=0, sticky="n", padx=(0, 4), pady=3)
            ttk.Label(self.setup_list, text=it.title, font=("", 11, "bold"), width=30).grid(row=r, column=1, sticky="nw", pady=3)
            detail = it.detail + (f"\n→ {it.fix}" if it.status != "ok" and it.fix else "")
            ttk.Label(self.setup_list, text=detail, foreground="#555", wraplength=520, justify="left").grid(
                row=r, column=2, sticky="w", pady=3)
            if it.action and it.status != "ok":
                label = {"create_folders": "Create folders", "find_fragpipe": "Find FragPipe", "save": "Save",
                         "install_task": "Install", "start_watcher": "Start"}.get(it.action, "Go")
                ttk.Button(self.setup_list, text=label, command=lambda a=it.action: self._setup_action(a)).grid(
                    row=r, column=3, sticky="ne", pady=3)
        ok = all(i.status in ("ok", "warn") for i in items) and items
        self.setup_summary.configure(text=text, foreground="#2e7d32" if ok else "")

    def _setup_action(self, action: str):
        if action.startswith("open_tab:"):
            tab = {"1": self.tab_folders, "2": self.tab_users, "3": self.tab_methods, "5": self.tab_run}.get(action[-1])
            if tab is not None:
                self.nb.select(tab)
            return
        if action == "create_folders":
            self.create_all()
        elif action == "find_fragpipe":
            self.find_fragpipe()
            self.save()
        elif action == "save":
            self.save()
        elif action == "install_task":
            self.install_task()
        elif action == "start_watcher":
            self.start_watcher()
        self.root.after(800, self.refresh_setup)

    def auto_setup(self):
        """Sensible defaults for everything that has one, then save. Never overwrites a path that already works."""
        from labwatch.fragpipe import detect_launcher

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
        f = ttk.Frame(self.nb, padding=12)
        self.nb.add(f, text="  7  Analysis  ")
        f.columnconfigure(1, weight=1)
        ttk.Label(f, text="What happens after FragPipe: statistics, volcano plots and results/report.html in "
                          "every experiment folder. These are the lab defaults; one experiment can override them "
                          "in its experiment.yaml (analysis: comparisons: [\"Drug vs DMSO\"], control: DMSO).",
                  wraplength=860, justify="left").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
        ttk.Checkbutton(f, text="Make statistics, volcano plots and a report after each search",
                        variable=self.bv("analysis.enabled", True)).grid(row=1, column=0, columnspan=3, sticky="w", **PAD)

        def row(r, key, label, hint, widget=None):
            ttk.Label(f, text=label).grid(row=r, column=0, sticky="e", **PAD)
            (widget or ttk.Entry(f, textvariable=self.v(key), width=12)).grid(row=r, column=1, sticky="w", **PAD)
            ttk.Label(f, text=hint, foreground="#666", wraplength=520).grid(row=r, column=2, sticky="w", **PAD)

        row(2, "analysis.test", "Test", "moderated = limma-style empirical Bayes (recommended with 3 replicates: "
            "borrows strength across all proteins); welch / student = classic t-tests",
            ttk.Combobox(f, textvariable=self.v("analysis.test"), values=["moderated", "welch", "student"],
                         state="readonly", width=12))
        row(3, "analysis.log2fc", "log2 fold change ≥", "1 = 2-fold. A hit needs this and the significance cut-off")
        row(4, "analysis.alpha", "Significance <", "0.05 is usual")
        ttk.Checkbutton(f, text="…on Benjamini-Hochberg adjusted q-values (recommended; untick = raw p-values)",
                        variable=self.bv("analysis.use_adjusted", True)).grid(row=5, column=1, columnspan=2, sticky="w", **PAD)
        row(6, "analysis.min_valid", "Values per group ≥", "proteins/sites with fewer measured values are not tested")
        row(7, "analysis.normalize", "Normalisation", "median = align sample medians (intensities only; ratios are never normalised)",
            ttk.Combobox(f, textvariable=self.v("analysis.normalize"), values=["median", "none"], state="readonly", width=12))
        row(8, "analysis.top_labels", "Names on volcano", "how many top hits are labelled")
        ttk.Label(f, text="Control keywords").grid(row=9, column=0, sticky="e", **PAD)
        ttk.Entry(f, textvariable=self.v("analysis.control_keywords"), width=70).grid(row=9, column=1, columnspan=2, sticky="ew", **PAD)
        ttk.Label(f, text="the first condition matching one of these is the control; every other condition is "
                          "compared against it", foreground="#666").grid(row=10, column=1, columnspan=2, sticky="w", padx=6)
        b = ttk.Frame(f)
        b.grid(row=11, column=0, columnspan=3, sticky="w", pady=(14, 0))
        ttk.Button(b, text="Analyse a folder…", command=self.analyze_folder).pack(side="left", padx=4)
        ttk.Label(b, text="any experiment or FragPipe output folder, including runs from before LabWatch "
                          "(Save first so the settings above are used)", foreground="#666").pack(side="left", padx=8)

    def analyze_folder(self):
        from labwatch import postprocess

        d = filedialog.askdirectory(title="Experiment or FragPipe output folder")
        if not d:
            return
        try:
            cfg = load(self.config_path, check_paths=False)
        except ConfigError:
            cfg = None
        self.set_status(f"analysing {d}…")

        def go():
            out = postprocess.run_for_folder(Path(d), cfg)
            msg = (f"{out.method or 'unknown method'}: " + "; ".join(
                f"{c['name']} {c['up']} up / {c['down']} down" for c in out.summary.get("comparisons", []))
                   + ("" if not out.warnings else "  (" + "; ".join(out.warnings) + ")"))

            def done():
                self.set_status(msg[:200])
                if out.report and out.report.is_file():
                    self._open(str(out.report))
                else:
                    messagebox.showinfo("Analyse", msg)

            self.post(done)

        threading.Thread(target=go, daemon=True).start()

    # ---------------------------------------------------------- tab: jobs ----

    _JCOLS = (("id", 40), ("status", 70), ("user", 80), ("method", 60), ("experiment", 260), ("queued", 110),
              ("ran", 70), ("now", 330))

    def _tab_jobs(self):
        f = ttk.Frame(self.nb, padding=10)
        self.nb.add(f, text="  6  Jobs  ")
        f.columnconfigure(0, weight=1)
        f.rowconfigure(1, weight=3)
        f.rowconfigure(3, weight=2)
        top = ttk.Frame(f)
        top.grid(row=0, column=0, sticky="ew")
        self.jobs_state = ttk.Label(top, text="", font=("", 10, "bold"))
        self.jobs_state.pack(side="left", padx=4)
        ttk.Button(top, text="Check FragPipe install", command=self.check_fragpipe).pack(side="right", padx=4)
        self.pause_btn = ttk.Button(top, text="Pause searches", command=self.toggle_pause)
        self.pause_btn.pack(side="right", padx=4)
        ttk.Button(top, text="Refresh", command=self.refresh_jobs).pack(side="right", padx=4)

        self.jtree = ttk.Treeview(f, columns=[c for c, _ in self._JCOLS], show="headings", height=10, selectmode="browse")
        for c, w in self._JCOLS:
            self.jtree.heading(c, text=c)
            self.jtree.column(c, width=w, anchor="w", stretch=c in ("experiment", "now"))
        for tag, color in (("failed", "#c62828"), ("running", "#1565c0"), ("waiting", "#b26a00"), ("done", "#2e7d32")):
            self.jtree.tag_configure(tag, foreground=color)
        self.jtree.grid(row=1, column=0, sticky="nsew", pady=4)
        self.jtree.bind("<<TreeviewSelect>>", lambda e: self.show_job())
        self.jtree.bind("<Double-1>", lambda e: self.job_action("report"))

        b = ttk.Frame(f)
        b.grid(row=2, column=0, sticky="w")
        for text, action in (("Open report", "report"), ("Open folder", "folder"), ("FragPipe log", "log"),
                             ("Re-run analysis", "analyze"), ("Retry", "retry"), ("Cancel", "cancel"),
                             ("Copy details", "copy")):
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
        from labwatch import fragpipe
        from labwatch.worker import paused

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
        for j in reversed(jobs):  # newest first
            counts[j.status] = counts.get(j.status, 0) + 1
            waiting = j.status == "queued" and (j.reason or "").startswith("waiting:")
            if j.status == "running":
                now = "FragPipe: " + fragpipe.progress(Path(j.dest_dir) / fragpipe.RUN_DIR / fragpipe.CONSOLE_LOG)
            else:
                now = (j.reason or "").replace("\n", " ")[:160]
            self.jtree.insert("", "end", iid=str(j.id), tags=("waiting" if waiting else j.status,), values=(
                j.id, "waiting" if waiting else j.status, j.user, j.method, j.inbox_name, _local(j.created_at),
                _duration(j.started_at, j.finished_at if j.status != "running" else None) if j.started_at else "", now))
        if sel and self.jtree.exists(sel[0]):
            self.jtree.selection_set(sel[0])
        summary = ", ".join(f"{n} {k}" for k, n in sorted(counts.items()))
        self.jobs_state.configure(text=(summary or "No jobs") + ("  ·  searches PAUSED" if is_paused else ""),
                                  foreground="#b26a00" if is_paused else "")

    def _selected_job(self):
        sel = self.jtree.selection()
        return self._jobs_cache.get(int(sel[0])) if sel else None

    def show_job(self):
        from labwatch import fragpipe

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
            run = json.loads((dest / "labwatch.json").read_text(encoding="utf-8")).get("run") or {}
        except (OSError, ValueError):
            run = {}
        for w in run.get("warnings") or []:
            lines.append(f"note: {w}")
        console = dest / fragpipe.RUN_DIR / fragpipe.CONSOLE_LOG
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

    def job_action(self, action: str):
        from labwatch import fragpipe
        from labwatch.worker import request_cancel

        j = self._selected_job()
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
        elif action == "analyze":
            if j.status != "done":
                messagebox.showinfo("Re-run analysis", "Only finished (done) jobs have FragPipe output to analyse.")
                return
            self._rerun_analysis(j)
        elif action == "folder":
            self._open(j.dest_dir)
        elif action == "log":
            log = Path(j.dest_dir) / fragpipe.RUN_DIR / fragpipe.CONSOLE_LOG
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
                    led.requeue(j.id, "retry requested", reset_attempts=True)
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
        """Statistics + plots + report again with the current Analysis settings (tab 7), off the Tk thread."""
        from labwatch import postprocess

        try:
            cfg = load(self.config_path, check_paths=False)
        except ConfigError:
            cfg = None
        self.jdetail.write(f"analysing job {j.id} ({j.inbox_name})…", clear=True)

        def go():
            try:
                out = postprocess.run_for_folder(Path(j.dest_dir), cfg, j.method)
                lines = [f"method {out.method}"] + [f"{c['name']}: {c['up']} up, {c['down']} down of {c['tested']}"
                                                    for c in out.summary.get("comparisons", [])]
                lines += [f"note: {w}" for w in out.warnings]
                text, rep = "\n".join(lines), out.report
            except Exception as exc:  # noqa: BLE001
                text, rep = f"analysis failed: {exc}", None

            def done():
                self.jdetail.write(text + (f"\nreport: {rep}" if rep else ""), clear=True)
                if rep and rep.is_file():
                    self._open(str(rep))

            self.post(done)

        threading.Thread(target=go, daemon=True).start()

    def toggle_pause(self):
        from labwatch.worker import pause, paused, resume

        log_dir = self._active_log_dir()
        if paused(log_dir):
            resume(log_dir)
            self.set_status("searches resumed")
        else:
            pause(log_dir, "from the app")
            self.set_status("searches paused — queued jobs wait; a running search finishes")
        self.refresh_jobs()

    def check_fragpipe(self):
        cfg_path = self.config_path
        self.jdetail.write("checking the FragPipe installation and each method's workflow + FASTA…", clear=True)

        def go():
            from labwatch import fragpipe

            try:
                cfg = load(cfg_path, check_paths=False)
                mark = {True: "✓", None: "!", False: "✗"}
                lines = [f"{mark[ok]} {label:<16} {detail}" for ok, label, detail in fragpipe.install_report(cfg)]
                for k in cfg.methods:
                    lines.append("")
                    lines.append(f"{k}:")
                    lines += [f"  {mark[ok]} {t}" for ok, t in fragpipe.describe_method(cfg, k)]
                text = "\n".join(lines)
            except Exception as exc:  # noqa: BLE001
                text = f"could not check: {exc} (save the config first?)"
            self.post(lambda: self.jdetail.write(text, clear=True))

        threading.Thread(target=go, daemon=True).start()

    def _tab_help(self):
        f = ttk.Frame(self.nb, padding=10)
        self.nb.add(f, text="  Help  ")
        t = scrolledtext.ScrolledText(f, wrap="word", font=("Menlo" if sys.platform == "darwin" else "Consolas", 10))
        t.pack(fill="both", expand=True)
        t.insert("1.0", HELP_TEXT)
        t.configure(state="disabled")

    # -------------------------------------------------------- bottom bar ----

    def _bottom_bar(self):
        b = ttk.Frame(self.root, padding=(8, 6))
        b.pack(fill="x")
        ttk.Label(b, text="Config:").pack(side="left")
        ttk.Label(b, textvariable=self.v("config_path"), foreground="#444").pack(side="left", padx=4)
        ttk.Button(b, text="Change…", width=9, command=self.change_config).pack(side="left")
        self.status_lbl = ttk.Label(b, text="", foreground="#2e7d32")
        self.status_lbl.pack(side="left", padx=16)
        ttk.Button(b, text="Save & Check", command=self.save_and_check).pack(side="right", padx=4)
        ttk.Button(b, text="Save", command=self.save).pack(side="right", padx=4)
        ttk.Button(b, text="Reload", command=self.reload).pack(side="right", padx=4)

    def change_config(self):
        r = filedialog.asksaveasfilename(title="Config file to use", defaultextension=".yaml",
                                         initialfile="config.yaml", confirmoverwrite=False)
        if not r:
            return
        self.config_path = Path(r)
        self.first_run = not self.config_path.is_file()
        self.data = configio.read_config(self.config_path)
        self._load_vars()
        self._refresh_users()
        self._refresh_methods()
        self.set_status("switched config" + (" (new file — press Save to create it)" if self.first_run else ""))

    def reload(self):
        self.data = configio.read_config(self.config_path)
        self._load_vars()
        self._refresh_users()
        self._refresh_methods()
        self.set_status("reloaded from disk")

    def save(self) -> bool:
        """Validate a candidate file first; the real config.yaml is only replaced if it passes."""
        probe = self.config_path.with_name(f".{self.config_path.stem}.checking.yaml")
        try:
            d = self._collect()
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            probe.write_text(configio.dump_config(d), encoding="utf-8")
            cfg = load(probe, check_paths=True)
            configio.write_config(self.config_path, d)
        except ConfigError as exc:
            messagebox.showerror("Config problem", str(exc))
            self.set_status("not saved — fix the problem (config.yaml unchanged)", ok=False)
            return False
        except OSError as exc:
            messagebox.showerror("Save", f"Could not write {self.config_path}:\n{exc}")
            return False
        finally:
            try:
                probe.unlink()
            except OSError:
                pass
        self.data = d
        service.remember_config_path(self.config_path)
        self.first_run = False
        self.v("config_path").set(str(self.config_path))
        self._refresh_methods()
        warn = f"  ({len(cfg.warnings)} warning(s) — run Check)" if cfg.warnings else ""
        self.set_status(f"saved {self.config_path.name}{warn}")
        return True

    def save_and_check(self):
        if self.save():
            self.nb.select(self.tab_run)
            self._cli(["check"])

    def on_close(self):
        if self.proc is not None and self.proc.poll() is None:
            if messagebox.askyesno("Quit", "A watcher started from this app is running. Stop it and quit?\n"
                                           "(No = quit and leave it running)"):
                service.request_stop(self._active_log_dir(), proc=self.proc, timeout=15)
        self.root.destroy()


def main(config_path: Path | None = None) -> int:
    root = tk.Tk()
    App(root, config_path)
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
