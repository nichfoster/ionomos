"""
The Analysis tab's "Check accuracy" page (D67): `ionomos compare` and `ionomos benchmark` with buttons.

Thin Tk only: what is valid, what runs and what the command line would be are accuracy.py (no Tk), the
same code the command line runs. A run happens on a worker thread; every line and the verdict come back to
the Tk thread through App.post (Tk is not thread-safe). Both read results and change neither; the page they
write opens when done.
"""
from __future__ import annotations

import logging
import subprocess
import threading
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from ionomos import accuracy

log = logging.getLogger("ionomos.app")

PAD = {"padx": 6, "pady": 3}
COLOURS = {0: "#2e7d32", 1: "#b26a00", 2: "#c62828"}


class AccuracyPage:
    def __init__(self, tab):
        self.tab, self.app = tab, tab.app
        self.busy = False
        self.last_page: Path | None = None
        self.last_args: list[str] = []
        p = ttk.Frame(tab.nb, padding=10)
        tab.nb.add(p, text="Check accuracy")
        self.frame = p
        p.columnconfigure(1, weight=1)
        p.rowconfigure(5, weight=1)
        ttk.Label(p, text="Does Ionomos agree with your other analysis, and how accurate is it? Both read the results "
                          "and change neither; the page opens when done. Same as `ionomos compare` and "
                          "`ionomos benchmark` on the command line.", wraplength=900, justify="left").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
        self._compare_box(p)
        self._benchmark_box(p)
        self.verdict = ttk.Label(p, text="", font=("", 11, "bold"), wraplength=900, justify="left")
        self.verdict.grid(row=3, column=0, columnspan=3, sticky="w", pady=(8, 2))
        b = ttk.Frame(p)
        b.grid(row=4, column=0, columnspan=3, sticky="w")
        self.open_btn = ttk.Button(b, text="Open the page", command=self.open_page, state="disabled")
        self.open_btn.pack(side="left", padx=2)
        self.folder_btn = ttk.Button(b, text="Open its folder", command=self.open_folder, state="disabled")
        self.folder_btn.pack(side="left", padx=2)
        ttk.Button(b, text="Copy the command line", command=self.copy_command).pack(side="left", padx=2)
        ttk.Button(b, text="Help", command=lambda: self.app.open_help("faq.check-accuracy")).pack(side="left", padx=12)
        from ionomos.app import OutputPane

        self.out = OutputPane(p, height=10)
        self.out.grid(row=5, column=0, columnspan=3, sticky="nsew", pady=4)
        self.frame.bind("<Map>", lambda e: self.prefill(), add="+")
        self._kind_changed()

    def v(self, key, default=""):
        return self.app.v(key, default)

    def bv(self, key, default=False):
        return self.app.bv(key, default)

    # ------------------------------------------------------------------ layout --

    def _row(self, frame, r, label, key, buttons, hint=""):
        ttk.Label(frame, text=label).grid(row=r, column=0, sticky="e", **PAD)
        e = ttk.Entry(frame, textvariable=self.v(key), width=70)
        e.grid(row=r, column=1, sticky="ew", **PAD)
        bb = ttk.Frame(frame)
        bb.grid(row=r, column=2, sticky="w")
        made = [e]
        for text, cmd in buttons:
            btn = ttk.Button(bb, text=text, command=cmd)
            btn.pack(side="left", padx=2)
            made.append(btn)
        if hint:
            ttk.Label(frame, text=hint, foreground="#666", wraplength=640, justify="left").grid(
                row=r + 1, column=1, columnspan=2, sticky="w", padx=6)
        return made

    def _compare_box(self, p):
        c = ttk.LabelFrame(p, text="Compare with a reference result for the same experiment", padding=6)
        c.grid(row=1, column=0, columnspan=3, sticky="ew", pady=4)
        c.columnconfigure(1, weight=1)
        self._row(c, 0, "Ionomos analysis", "acc.analysis", [("Folder…", lambda: self._folder("acc.analysis"))],
                  "an analysed experiment folder (or its results folder, or a <table>_ionomos folder)")
        self._row(c, 2, "Reference", "acc.reference", [("Table…", self._reference_table),
                                                       ("Folder…", lambda: self._folder("acc.reference"))],
                  "a FragPipe-Analyst export, a limma / MSstats table, a Perseus matrix, the lab's R output "
                  "(.tsv .csv .txt .xlsx), or another analysed folder")
        o = ttk.Frame(c)
        o.grid(row=4, column=1, columnspan=2, sticky="w", **PAD)
        ttk.Label(o, text="Match proteins by").pack(side="left")
        self.v("acc.by").set("auto")
        ttk.Combobox(o, textvariable=self.v("acc.by"), state="readonly", width=6, values=list(accuracy.BY)).pack(
            side="left", padx=4)
        ttk.Checkbutton(o, text="the reference is the comparison the other way round (DMSO vs Drug)",
                        variable=self.bv("acc.flip")).pack(side="left", padx=12)
        self.compare_btn = ttk.Button(c, text="Compare", command=self.run_compare)
        self.compare_btn.grid(row=5, column=1, sticky="w", **PAD)

    def _benchmark_box(self, p):
        b = ttk.LabelFrame(p, text="Benchmark: accuracy against known truth", padding=6)
        b.grid(row=2, column=0, columnspan=3, sticky="ew", pady=4)
        b.columnconfigure(1, weight=1)
        self.v("acc.kind").set("simulated")
        ttk.Radiobutton(b, text="On simulated data with planted changes (no data needed)", value="simulated",
                        variable=self.v("acc.kind"), command=self._kind_changed).grid(
            row=0, column=0, columnspan=3, sticky="w", **PAD)
        g = ttk.Frame(b)
        g.grid(row=1, column=1, columnspan=2, sticky="w", **PAD)
        ttk.Label(g, text="Grid").pack(side="left")
        self.v("acc.grid").set("quick")
        self.grid_cb = ttk.Combobox(g, textvariable=self.v("acc.grid"), state="readonly", width=9,
                                    values=list(accuracy.GRIDS))
        self.grid_cb.pack(side="left", padx=4)
        ttk.Label(g, text="quick: seconds · standard: about a minute", foreground="#666").pack(side="left", padx=6)
        self.sim_widgets = [self.grid_cb, *self._row(
            b, 2, "With the settings of", "acc.like", [("Folder…", lambda: self._folder("acc.like"))],
            "optional: an analysed experiment; its settings and group sizes are added, and the page goes into its "
            "results folder")]
        ttk.Radiobutton(b, text="On a benchmark sample run on the instrument (human / yeast / E. coli, or a spike-in)",
                        value="real", variable=self.v("acc.kind"), command=self._kind_changed).grid(
            row=4, column=0, columnspan=3, sticky="w", **PAD)
        self.real_widgets = self._row(b, 5, "Analysed experiment", "acc.folder",
                                      [("Folder…", lambda: self._folder("acc.folder"))])
        self.real_widgets += self._row(b, 6, "Expected ratios", "acc.expected", [("File…", self._expected_file)],
                                       "a .yaml file, e.g.  expected: {HUMAN: 1, YEAST: 0.5, ECOLI: 4}  (the ratio "
                                       "treated / control per species; Help says how)")
        self.where = ttk.Label(b, text="", foreground="#666", wraplength=640, justify="left")
        self.where.grid(row=8, column=1, columnspan=2, sticky="w", padx=6)
        self.v("acc.like").trace_add("write", lambda *_: self._kind_changed())
        self.bench_btn = ttk.Button(b, text="Run benchmark", command=self.run_benchmark)
        self.bench_btn.grid(row=9, column=1, sticky="w", **PAD)

    def _kind_changed(self):
        real = self.v("acc.kind").get() == "real"
        for w in self.sim_widgets:
            w.configure(state="disabled" if real else ("readonly" if isinstance(w, ttk.Combobox) else "normal"))
        for w in self.real_widgets:
            w.configure(state="normal" if real else "disabled")
        if real:
            text = "writes benchmark.html into the experiment's results folder"
        elif self.v("acc.like").get().strip():
            text = "writes benchmark_simulated.html into that experiment's results folder"
        else:
            text = f"writes benchmark_simulated.html into {self._bench_dir()}"
        self.where.configure(text=text)

    def _bench_dir(self) -> Path:
        return accuracy.default_benchmark_dir(self.app.v("paths.log_dir").get().strip() or None)

    def prefill(self):
        """The experiment open on the first page is the likely one to compare."""
        t = getattr(self.tab.editor, "target", None)
        if t and not self.v("acc.analysis").get().strip():
            self.v("acc.analysis").set(str(t["dest"]).replace("\\", "/"))

    # ----------------------------------------------------------------- pickers --

    def _folder(self, key):
        cur = self.v(key).get().strip()
        r = filedialog.askdirectory(initialdir=cur if cur and Path(cur).is_dir() else None, mustexist=True)
        if r:
            self.v(key).set(r.replace("\\", "/"))

    def _reference_table(self):
        r = filedialog.askopenfilename(title="Reference result table", filetypes=[
            ("Result tables", "*.tsv *.csv *.txt *.xlsx"), ("All files", "*.*")])
        if r:
            self.v("acc.reference").set(r.replace("\\", "/"))

    def _expected_file(self):
        r = filedialog.askopenfilename(title="Expected ratios", filetypes=[("YAML", "*.yaml *.yml"),
                                                                            ("All files", "*.*")])
        if r:
            self.v("acc.expected").set(r.replace("\\", "/"))

    # ------------------------------------------------------------------ running --

    def _start(self, title: str, args: list[str], work) -> None:
        """Run work(say) -> Outcome on a thread; lines and the verdict come back through App.post."""
        self.busy = True
        self.last_args = args
        for btn in (self.compare_btn, self.bench_btn):
            btn.configure(state="disabled")
        self.verdict.configure(text=f"{title}: running…", foreground="#1565c0")
        self.out.write("$ ionomos " + subprocess.list2cmdline(args), clear=True)
        log.info("%s from the app", args[0])

        def say(line: str) -> None:
            self.app.post(lambda: self.out.write(line))

        def go():
            try:
                outcome = work(say)
            except Exception as exc:  # noqa: BLE001 - shown, never a dead thread
                log.exception("%s from the app failed", args[0])
                outcome = accuracy.Outcome(2, error=f"{type(exc).__name__}: {exc}")
            self.app.post(lambda: self._done(title, outcome))

        threading.Thread(target=go, name=f"app-{args[0]}", daemon=True).start()

    def _done(self, title: str, out: accuracy.Outcome) -> None:
        self.busy = False
        for btn in (self.compare_btn, self.bench_btn):
            btn.configure(state="normal")
        if out.error:
            self.out.write(out.error)
            text = f"{title}: not run — {out.error}"
        else:
            head = {0: "done", 1: "differs or could not be judged"}.get(out.code, "")
            first = "\n".join(out.verdicts[:4]) + (f"\n… {len(out.verdicts) - 4} more below" if len(out.verdicts) > 4 else "")
            text = f"{title}: {head}" + (f"\n{first}" if first else "")
        self.verdict.configure(text=text, foreground=COLOURS.get(out.code, "#c62828"))
        self.last_page = out.page if out.page and Path(out.page).is_file() else None
        state = "normal" if self.last_page else "disabled"
        self.open_btn.configure(state=state)
        self.folder_btn.configure(state=state)
        if self.last_page:
            self.app._open(str(self.last_page))
        self.app.set_status(f"{title}: finished" if not out.error else f"{title}: not run", ok=not out.error)

    def _refuse(self, title: str, problems: list[str]) -> bool:
        if self.busy:
            messagebox.showinfo(title, "A check is still running; wait for it to finish.")
            return True
        if problems:
            messagebox.showerror(title, "\n".join(f"• {p}" for p in problems))
            return True
        return False

    def run_compare(self) -> bool:
        a, ref = self.v("acc.analysis").get().strip(), self.v("acc.reference").get().strip()
        if self._refuse("Compare", accuracy.compare_problems(a, ref)):
            return False
        by, flip = self.v("acc.by").get() or "auto", self.bv("acc.flip").get()
        self._start("Compare", accuracy.compare_args(a, ref, by=by, flip=flip),
                    lambda say: accuracy.run_compare(a, ref, say, by=by, flip=flip))
        return True

    def run_benchmark(self) -> bool:
        kind = self.v("acc.kind").get()
        folder, expected = self.v("acc.folder").get().strip(), self.v("acc.expected").get().strip()
        like, grid = self.v("acc.like").get().strip(), self.v("acc.grid").get() or "quick"
        if self._refuse("Benchmark", accuracy.benchmark_problems(kind, folder, expected, like, grid)):
            return False
        if kind == "real":
            self._start("Benchmark", accuracy.benchmark_args("real", folder, expected),
                        lambda say: accuracy.run_benchmark_real(folder, expected, say))
        else:
            out = None if like else self._bench_dir()
            self._start("Benchmark", accuracy.benchmark_args("simulated", like=like, grid=grid, out=out),
                        lambda say: accuracy.run_benchmark_simulated(
                            grid, say, like=like or None, out=out, progress=lambda m: say(f"  … {m}")))
        return True

    # -------------------------------------------------------------------- after --

    def open_page(self):
        if self.last_page:
            self.app._open(str(self.last_page))

    def open_folder(self):
        if self.last_page:
            self.app._open(str(self.last_page.parent))

    def copy_command(self):
        if not self.last_args:
            self.app.set_status("run a check first; its command line is copied here", ok=False)
            return
        self.app.root.clipboard_clear()
        self.app.root.clipboard_append("ionomos " + subprocess.list2cmdline(self.last_args))
        self.app.set_status("command line copied")
