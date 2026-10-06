"""
The window that checks a folder before it is analysed (D80): opened by the Analysis tab's Folder… button, by a
folder dropped on the app, and by a finished job whose folder needs a look.

    host = CheckHost(post=..., open_path=..., check=..., choose_folder=..., review=..., run=...)
    win = FolderCheckWindow(parent, host, Path("D:/Ana/HeLa_DIA"))

Shows what was found (FragPipe's output folder, workflow, manifest, tables, log), what the folder will be analysed
as and why, and every problem in plain words with what to do, most serious first. The buttons beside a problem
fix what can be fixed here (analyse as another kind, use another of several outputs, take the conditions from the
file names, open the log or the folder, choose another folder); the check runs again after each. Then:
    Review samples   the samples load into the Analysis tab, to check conditions before running
    Analyse now      the same, and the analysis starts (the editor still asks about anything left to decide)
The work is fpfolder.scan (through postprocess.check_folder), on a thread.
"""
from __future__ import annotations

import logging
import threading
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from tkinter import scrolledtext, ttk

from ionomos import fpfolder, tkutil

log = logging.getLogger("ionomos.app")

COLOUR = {"error": "#c62828", "input": "#b26a00", "warning": "#555", "info": "#1565c0"}
WORD = {"error": "PROBLEM", "input": "CHECK", "warning": "note", "info": "info"}
OK = "#2e7d32"


@dataclass
class CheckHost:
    post: Callable[[Callable], None]                                   # run on the Tk thread
    open_path: Callable[[str], None]
    check: Callable[..., fpfolder.Scan]                               # (picked, method=, root=, filed=) -> Scan
    choose_folder: Callable[[], Path | None]
    review: Callable[[fpfolder.Scan, bool], None]                     # (scan, use the guessed conditions)
    run: Callable[[fpfolder.Scan, bool], None]


def kind_label(kind: str) -> str:
    return f"{kind} — {fpfolder.KIND_WORDS[kind]}" if kind in fpfolder.KIND_WORDS else kind


def kind_of(label: str) -> str:
    return label.split(" — ", 1)[0].strip()


class FolderCheckWindow:
    def __init__(self, parent, host: CheckHost, picked: Path, filed: str | None = None, job_id: int | None = None,
                 scan: fpfolder.Scan | None = None):
        self.host, self.picked, self.filed, self.job_id = host, Path(picked), filed, job_id
        self.scan: fpfolder.Scan | None = None
        self.method: str | None = None      # chosen in the window; None = the recommendation
        self.root: Path | None = None       # the output chosen when there are several
        self.busy = False
        self._token = 0
        win = self.win = tk.Toplevel(parent)
        win.title("Check the folder before analysing")
        win.geometry("860x640")
        win.minsize(640, 460)
        f = ttk.Frame(win, padding=10)
        f.pack(fill="both", expand=True)
        f.columnconfigure(0, weight=1)
        f.rowconfigure(5, weight=1)
        self.title = ttk.Label(f, text="", font=("", 12, "bold"), wraplength=820, justify="left")
        self.title.grid(row=0, column=0, sticky="w")
        self.path = ttk.Label(f, text="", foreground="#666", wraplength=820, justify="left")
        self.path.grid(row=1, column=0, sticky="w")

        self.found = ttk.LabelFrame(f, text="What Ionomos found", padding=6)
        self.found.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        self.found.columnconfigure(1, weight=1)

        choose = ttk.Frame(f)
        choose.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        choose.columnconfigure(1, weight=1)
        ttk.Label(choose, text="Analyse as").grid(row=0, column=0, sticky="e", padx=(0, 6))
        self.kind = ttk.Combobox(choose, state="readonly", width=46)
        self.kind.grid(row=0, column=1, sticky="w")
        self.kind.bind("<<ComboboxSelected>>", lambda e: self._kind_chosen())
        self.why = ttk.Label(choose, text="", foreground="#555", wraplength=780, justify="left")
        self.why.grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 0))
        self.out_label = ttk.Label(choose, text="FragPipe output")
        self.out_box = ttk.Combobox(choose, state="readonly", width=70)
        self.out_box.bind("<<ComboboxSelected>>", lambda e: self._output_chosen())
        self.dest = ttk.Label(choose, text="", foreground="#555", wraplength=780, justify="left")
        self.dest.grid(row=3, column=0, columnspan=2, sticky="w", pady=(2, 0))
        self.guess = tkutil.BooleanVar(master=win, value=False)
        self.guess_box = ttk.Checkbutton(choose, text="Use the conditions guessed from the file names",
                                         variable=self.guess)

        self.verdict = ttk.Label(f, text="", font=("", 10, "bold"), wraplength=820, justify="left")
        self.verdict.grid(row=4, column=0, sticky="w", pady=(8, 2))
        self.issues = scrolledtext.ScrolledText(f, height=12, wrap="word", font=("Segoe UI" if _win() else
                                                                                 "Helvetica", 10))
        self.issues.grid(row=5, column=0, sticky="nsew")
        for sev, colour in COLOUR.items():
            self.issues.tag_configure(sev, foreground=colour)
        bold = ("Segoe UI" if _win() else "Helvetica", 10, "bold")
        self.issues.tag_configure("b", font=bold)
        self.issues.tag_configure("fix", lmargin1=18, lmargin2=18)
        self.issues.tag_configure("msg", lmargin1=18, lmargin2=18)
        self.issues.configure(state="disabled")

        b = ttk.Frame(f)
        b.grid(row=6, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(b, text="Choose another folder…", command=self.choose).pack(side="left", padx=2)
        self.recheck_btn = ttk.Button(b, text="Check again", command=self.recheck)
        self.recheck_btn.pack(side="left", padx=2)
        ttk.Button(b, text="Open folder", command=lambda: self.host.open_path(str(self.picked))).pack(side="left",
                                                                                                    padx=2)
        self.run_btn = ttk.Button(b, text="Analyse now", command=self.analyse)
        self.run_btn.pack(side="right", padx=2)
        self.review_btn = ttk.Button(b, text="Review samples", command=self.review)
        self.review_btn.pack(side="right", padx=2)
        ttk.Button(b, text="Cancel", command=self.close).pack(side="right", padx=2)
        win.protocol("WM_DELETE_WINDOW", self.close)
        win.bind("<Escape>", lambda e: self.close())
        win.transient(parent)
        win.lift()
        try:
            win.focus_force()
        except tk.TclError:
            pass
        if scan is not None:
            self.show(scan)
        else:
            self.recheck()

    # ---------------------------------------------------------------- checking --

    def alive(self) -> bool:
        try:
            return bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def recheck(self) -> None:
        self._token += 1
        token, picked, method, root, filed = self._token, self.picked, self.method, self.root, self.filed
        self.busy = True
        self._buttons()
        self.title.configure(text=f"Checking {picked.name or picked} …")
        self.path.configure(text=str(picked))
        host = self.host

        def go():
            try:
                sc, err = host.check(picked, method=method, root=root, filed=filed), None
            except Exception as exc:  # noqa: BLE001 - the window must say what happened, never hang
                log.exception("folder check failed for %s", picked)
                sc, err = None, f"{type(exc).__name__}: {exc}"
            host.post(lambda: self._checked(token, sc, err))

        threading.Thread(target=go, daemon=True).start()

    def _checked(self, token: int, sc, err) -> None:
        if token != self._token or not self.alive():
            return
        self.busy = False
        if sc is None:
            sc = fpfolder.Scan(picked=self.picked, findings=[fpfolder.Finding(
                "CHECK_FAILED", "error", "The folder could not be checked", err or "unknown error",
                "Choose the folder again; if it keeps failing, use Help → Report a problem.",
                [("Choose another folder…", "choose")])])
        self.show(sc)

    def show(self, sc: fpfolder.Scan) -> None:
        self.scan = sc
        self.picked = sc.picked
        name = sc.picked.name or str(sc.picked)
        self.title.configure(text=f"{name}: " + ("ready to analyse" if not sc.blocking and not sc.needs_a_look else
                                                  "ready — please check the points below" if not sc.blocking else
                                                  "can't be analysed yet"))
        self.path.configure(text=str(sc.picked))
        for w in self.found.winfo_children():
            w.destroy()
        rows = sc.found or [("Nothing", "no FragPipe output, engine results or table was found")]
        for r, (label, text) in enumerate(rows):
            bad = "can't be read" in text or "failed" in text
            ttk.Label(self.found, text=("✗ " if bad else "✓ ") + label, foreground=COLOUR["error"] if bad else OK
                      ).grid(row=r, column=0, sticky="nw", padx=(0, 8))
            ttk.Label(self.found, text=text, wraplength=620, justify="left").grid(row=r, column=1, sticky="w")
        self.kind.configure(values=[kind_label(k) for k in sc.choices])
        self.kind.set(kind_label(sc.method) if sc.method else "")
        self.kind.state(["!disabled"] if len(sc.choices) > 1 else ["disabled"])
        why = ""
        if sc.recommended:
            why = f"Recommended: {sc.recommended}" + (f" — {sc.why}." if sc.why else ".")
        elif not sc.choices:
            why = "Nothing here can be analysed yet: see the problems below."
        self.why.configure(text=why)
        pool = [o for o in sc.outputs if o.tables] or sc.outputs
        if len(pool) > 1:
            self._outputs = {self._out_text(o, sc): o.root for o in pool}
            self.out_label.grid(row=2, column=0, sticky="e", padx=(0, 6), pady=(4, 0))
            self.out_box.grid(row=2, column=1, sticky="w", pady=(4, 0))
            self.out_box.configure(values=list(self._outputs))
            if sc.chosen is not None:
                self.out_box.set(self._out_text(sc.chosen, sc))
        else:
            self._outputs = {}
            self.out_label.grid_remove()
            self.out_box.grid_remove()
        self.dest.configure(text=f"Results go to {sc.dest / 'results'}" if sc.dest and sc.method else "")
        if sc.guess:
            self.guess_box.grid(row=4, column=0, columnspan=2, sticky="w", pady=(4, 0))
            self.guess.set(True)
        else:
            self.guess_box.grid_remove()
            self.guess.set(False)
        self._issues(sc)
        self._buttons()

    def _out_text(self, o: fpfolder.Output, sc: fpfolder.Scan) -> str:
        m = fpfolder.decide(o, sc.filed)[0]
        rel = fpfolder._rel(o.root, sc.picked)
        return f"{rel}  ({m or 'no readable table'})"

    def _issues(self, sc: fpfolder.Scan) -> None:
        t = self.issues
        t.configure(state="normal")
        t.delete("1.0", "end")
        shown = [f for f in sc.sorted_findings() if f.severity != "info" or f.actions]
        errors = sum(f.severity == "error" for f in shown)
        checks = sum(f.severity == "input" for f in shown)
        if sc.blocking:
            self.verdict.configure(text=f"✗ {errors or 1} problem{'s' if errors > 1 else ''} to fix before this can "
                                        "be analysed", foreground=COLOUR["error"])
        elif checks:
            self.verdict.configure(text=f"! {checks} thing{'s' if checks > 1 else ''} to check — Ionomos has picked "
                                        "the recommended choice for each", foreground=COLOUR["input"])
        else:
            self.verdict.configure(text="✓ Everything needed is here. Analyse now runs it; Review samples lets you "
                                        "check the conditions first.", foreground=OK)
        if not shown:
            t.insert("end", "No problems found.\n")
        for f in shown:
            t.insert("end", f"{WORD.get(f.severity, f.severity)}  ", (f.severity, "b"))
            t.insert("end", f.title + "\n", ("b",))
            t.insert("end", f.message + "\n", ("msg",))
            if f.fix:
                t.insert("end", "→ " + f.fix + "\n", ("fix", f.severity))
            if f.actions:
                t.insert("end", "  ", ("fix",))
                for text, action in f.actions:
                    btn = ttk.Button(t, text=text, command=lambda a=action: self.act(a))
                    t.window_create("end", window=btn, padx=3, pady=2)
                t.insert("end", "\n")
            t.insert("end", "\n")
        t.configure(state="disabled")

    def _buttons(self) -> None:
        sc = self.scan
        ready = sc is not None and not self.busy
        self.run_btn.state(["!disabled"] if ready and not sc.blocking else ["disabled"])
        self.review_btn.state(["!disabled"] if ready and sc.method and sc.dest else ["disabled"])
        self.recheck_btn.state(["disabled"] if self.busy else ["!disabled"])

    # ----------------------------------------------------------------- actions --

    def act(self, action: str) -> None:
        """A problem's button: method:<kind>, root:<folder>, guess, review, choose, open:<path>."""
        what, _, arg = action.partition(":")
        if what == "method":
            self.method = arg
            self.recheck()
        elif what == "root":
            self.root = Path(arg)
            self.recheck()
        elif what == "guess":
            self.guess.set(True)
            self.verdict.configure(text="The conditions from the file names will be used: check them in the "
                                        "samples list.", foreground=COLOUR["info"])
        elif what == "review":
            self.review()
        elif what == "choose":
            self.choose()
        elif what == "open" and arg:
            self.host.open_path(arg)

    def _kind_chosen(self) -> None:
        kind = kind_of(self.kind.get())
        if kind and (self.scan is None or kind != self.scan.method):
            self.method = kind
            self.recheck()

    def _output_chosen(self) -> None:
        root = self._outputs.get(self.out_box.get())
        if root is not None:
            self.root = root
            self.method = None   # a new output: recommend again
            self.recheck()

    def choose(self) -> None:
        d = self.host.choose_folder()
        if d:
            self.picked, self.method, self.root, self.filed = Path(d), None, None, None
            self.recheck()

    def review(self) -> None:
        if self.scan is not None and self.scan.method and self.scan.dest:
            sc, guess = self.scan, bool(self.guess.get())
            self.close()
            self.host.review(sc, guess)

    def analyse(self) -> None:
        if self.scan is not None and not self.scan.blocking:
            sc, guess = self.scan, bool(self.guess.get())
            self.close()
            self.host.run(sc, guess)

    def close(self) -> None:
        self._token += 1   # a check still running is ignored when it ends
        try:
            self.win.destroy()
        except tk.TclError:
            pass


def _win() -> bool:
    import os

    return os.name == "nt"
