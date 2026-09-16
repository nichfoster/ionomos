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
import tkinter as tk
from collections.abc import Callable
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
  Run & Test tab -> "Copy diagnostics". It puts check + status + config + the
  last 150 log lines on the clipboard (and saves a copy in the logs folder).
  Paste that into the chat with a sentence about what you expected.

WHERE THINGS ARE
  config.yaml        all settings (path shown at the bottom of this window)
  logs/labwatch.log  what the watcher did
  labwatch.db        job ledger (labwatch status)
  <folder>/labwatch.json    status + provenance next to each experiment
  <inbox>/<name>.REJECTED.txt   why a folder was not taken; fix it or delete the note

COMMAND LINE (same program)
  labwatch check | status | dry-run <folder> | run | retry <id> | testbed ...
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
        self._tab_folders()
        self._tab_users()
        self._tab_methods()
        self._tab_advanced()
        self._tab_run()
        self._tab_help()
        self._bottom_bar()
        self._load_vars()
        self._refresh_users()
        self._refresh_methods()
        self._refresh_status()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(100, self._pump_ui)
        if self.first_run:
            self.nb.select(0)

    def post(self, fn) -> None:
        """Thread-safe: run fn on the Tk thread soon."""
        self._ui_q.put(fn)

    def _pump_ui(self):
        try:
            while True:
                self._ui_q.get_nowait()()
        except queue.Empty:
            pass
        self.root.after(100, self._pump_ui)

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
        self.v("users.default").set(d["users"].get("default", "") or "")
        self.v("users.learned_aliases_file").set(d["users"].get("learned_aliases_file", "") or "")
        self.v("config_path").set(str(self.config_path))

    def _collect(self) -> dict:
        d = configio.read_config(self.config_path) if self.config_path.is_file() else configio.defaults()
        d["paths"] = {k: self.v(f"paths.{k}").get().strip().replace("\\", "/") for k in d["paths"]}
        for sec, keys in (("watcher", ("poll_seconds", "stable_seconds", "min_raw_files")),
                          ("fragpipe", ("threads", "ram_gb", "timeout_minutes"))):
            for k in keys:
                raw = self.v(f"{sec}.{k}").get().strip()
                try:
                    d[sec][k] = float(raw) if "." in raw else int(raw)
                except ValueError:
                    raise ConfigError(f"{sec}.{k} must be a number, got {raw!r}") from None
        d["fragpipe"]["config_tools_folder"] = self.v("fragpipe.config_tools_folder").get().strip()
        d["fragpipe"]["config_diann"] = self.v("fragpipe.config_diann").get().strip()
        d["gui"]["enabled"] = self.bv("gui.enabled").get()
        try:
            d["gui"]["timeout_minutes"] = float(self.v("gui.timeout_minutes").get().strip() or 0)
        except ValueError:
            raise ConfigError("gui.timeout_minutes must be a number") from None
        d["users"]["aliases"] = {u: list(a) for u, a in self.data["users"].get("aliases", {}).items() if a}
        d["users"]["default"] = self.v("users.default").get().strip()
        d["users"]["learned_aliases_file"] = self.v("users.learned_aliases_file").get().strip()
        d["methods"] = self.data["methods"]
        return d

    def set_status(self, msg: str, ok: bool = True):
        self.status_lbl.configure(text=msg, foreground="#2e7d32" if ok else "#c62828")

    # ------------------------------------------------------ tab: folders ----

    def _tab_folders(self):
        f = ttk.Frame(self.nb, padding=10)
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
            ("paths.fragpipe_exe", "FragPipe launcher (exe)", "file", "e.g. C:/FragPipe/FragPipe-24.0/fragpipe/bin/fragpipe.exe — needed only when searches are enabled."),
        ]
        self.path_rows: dict[str, PathRow] = {}
        for key, label, kind, hint in rows:
            self.path_rows[key] = PathRow(f, r, label, self.v(key), kind, hint)
            r += 2
        b = ttk.Frame(f)
        b.grid(row=r, column=0, columnspan=5, sticky="w", **PAD)
        ttk.Button(b, text="Create all missing folders", command=self.create_all).pack(side="left", padx=4)
        ttk.Button(b, text="Open inbox", command=lambda: self._open(self.v("paths.inbox").get())).pack(side="left", padx=4)
        ttk.Button(b, text="Open users folder", command=lambda: self._open(self.v("paths.users_root").get())).pack(side="left", padx=4)

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
        b = ttk.Frame(e)
        b.grid(row=5, column=0, columnspan=4, sticky="w", **PAD)
        ttk.Button(b, text="Apply changes", command=self.apply_method).pack(side="left", padx=4)
        ttk.Button(b, text="New method", command=self.new_method).pack(side="left", padx=4)
        ttk.Button(b, text="Remove method", command=self.remove_method).pack(side="left", padx=4)
        ttk.Button(b, text="Open workflows folder", command=lambda: self._open(self.v("paths.workflow_dir").get())).pack(side="left", padx=12)
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

        fp = group("FragPipe (searches — Phase 2)", 1, 0)
        num(fp, 0, "fragpipe.threads", "Threads", "PC has 32 logical CPUs; leave a few for the OS")
        num(fp, 1, "fragpipe.ram_gb", "RAM (GB)", "of 64 GB")
        num(fp, 2, "fragpipe.timeout_minutes", "Timeout (min)", "a run longer than this is failed")
        ttk.Label(fp, text="Tools folder").grid(row=3, column=0, sticky="e", **PAD)
        ttk.Entry(fp, textvariable=self.v("fragpipe.config_tools_folder"), width=34).grid(row=3, column=1, columnspan=2, sticky="ew", **PAD)
        ttk.Label(fp, text="DIA-NN exe").grid(row=4, column=0, sticky="e", **PAD)
        ttk.Entry(fp, textvariable=self.v("fragpipe.config_diann"), width=34).grid(row=4, column=1, columnspan=2, sticky="ew", **PAD)
        ttk.Label(fp, text="both optional: only if the first headless run can't find MSFragger/DIA-NN",
                  foreground="#666", wraplength=380).grid(row=5, column=0, columnspan=3, sticky="w", padx=6)

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
        ttk.Label(m, text="Every setting here is also a plain key in config.yaml (see comments in the file).",
                  foreground="#666", wraplength=380).grid(row=1, column=0, columnspan=3, sticky="w", padx=6)

    def reset_defaults(self):
        if messagebox.askyesno("Reset", "Reset all settings to defaults? (Not saved until you press Save.)"):
            self.data = configio.defaults()
            self._load_vars()
            self._refresh_users()
            self._refresh_methods()

    # ---------------------------------------------------------- tab: run ----

    def _tab_run(self):
        f = ttk.Frame(self.nb, padding=10)
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
        txt, ok = "Watcher: stopped", False
        if self.proc is not None and self.proc.poll() is None:
            which = "TESTBED" if self.proc_config != self.config_path else "lab config"
            txt, ok = f"Watcher: RUNNING (started here, {which}, pid {self.proc.pid})", True
        else:
            if self.proc is not None:
                self.proc = None
            pid = service.running_pid(self._log_dir())
            if pid:
                txt, ok = f"Watcher: RUNNING (pid {pid}, started elsewhere — startup task?)", True
        self.run_status.configure(text=txt, foreground="#2e7d32" if ok else "#c62828")
        st = service.task_status()
        self.task_status.configure(text={"installed": "Startup task: installed", "missing": "Startup task: not installed",
                                         "n/a": "Startup task: n/a on this OS"}[st])
        self.root.after(2000, self._refresh_status)

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
        if not testbed and service.running_pid(self._log_dir()):
            messagebox.showinfo("Watcher", "The watcher is already running (probably from the startup task).")
            return
        self.proc = service.start_watcher(cfg)
        self.proc_config = cfg
        self.out.write(f"started watcher pid {self.proc.pid} with {cfg}")
        self.bv("follow").set(True)
        self._toggle_follow()

    def stop_watcher(self):
        if self.proc is not None and self.proc.poll() is None:
            service.stop_process(self.proc)
            self.out.write("watcher stopped")
            self.proc = None
            return
        pid = service.running_pid(self._log_dir())
        if pid and messagebox.askyesno("Stop", f"Stop the watcher running as pid {pid} (started elsewhere)?"):
            service.kill_pid(pid)
            self.out.write(f"sent stop to pid {pid}")
        elif not pid:
            self.out.write("no watcher running")

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
        if self.proc is not None and self.proc.poll() is None:
            service.stop_process(self.proc)
            self.proc = None
        pid = service.running_pid(self._log_dir())
        if pid:
            if not messagebox.askyesno("Update", f"The watcher (pid {pid}) is running. Stop it and update?"):
                return
            service.kill_pid(pid)
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
        try:
            d = self._collect()
            configio.write_config(self.config_path, d)
            cfg = load(self.config_path, check_paths=True)
        except ConfigError as exc:
            messagebox.showerror("Config problem", str(exc))
            self.set_status("not saved — fix the problem", ok=False)
            return False
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
            self.nb.select(4)
            self._cli(["check"])

    def on_close(self):
        if self.proc is not None and self.proc.poll() is None:
            if messagebox.askyesno("Quit", "A watcher started from this app is running. Stop it and quit?\n"
                                           "(No = quit and leave it running)"):
                service.stop_process(self.proc)
        self.root.destroy()


def main(config_path: Path | None = None) -> int:
    root = tk.Tk()
    App(root, config_path)
    root.mainloop()
    return 0
