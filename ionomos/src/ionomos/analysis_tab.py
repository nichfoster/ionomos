"""
The app's Analysis tab (7): analyse one experiment, and the lab's default analysis settings.

  Analyse an experiment
    pick a finished job (or any FragPipe output folder) -> its samples are listed with the
    condition each was given; change a condition, leave samples out, choose the comparisons
    and, for this experiment only, the cut-offs / imputation -> Run analysis -> the report opens.
    Choices are saved in the experiment's experiment.yaml (analysis:), so re-runs and later
    automatic analyses use them too.

  Lab defaults
    FragPipe-Analyst's settings (filtering, normalisation, imputation, limma, enrichment) as
    used after every search; saved in config.yaml with the Save button.

All data work runs on a thread; results come back through App.post().
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

log = logging.getLogger("ionomos.app")

PAD = {"padx": 6, "pady": 3}
LAB_DEFAULT = "(lab default)"
NUM_KEYS = (("log2fc", float), ("alpha", float), ("min_valid", int), ("top_labels", int),
            ("filter_global_pct", float), ("filter_condition_pct", float))
FRAGPIPE_ANALYST_DEFAULTS = {"test": "limma", "de_type": "all", "log2fc": "1", "alpha": "0.05", "use_adjusted": True,
                             "remove_contaminants": True, "filter_global_pct": "0", "filter_condition_pct": "0",
                             "normalize": "none", "imputation": "auto"}
IONOMOS_DEFAULTS = {"test": "limma", "de_type": "control", "log2fc": "1", "alpha": "0.05", "use_adjusted": True,
                    "remove_contaminants": True, "filter_global_pct": "0", "filter_condition_pct": "50",
                    "normalize": "median", "imputation": "auto"}
DE_LABELS = {"control": "each condition vs the control", "all": "all pairs", "others": "each condition vs all others"}


class AnalysisTab:
    def __init__(self, app):
        self.app = app
        self._jobs: list[tuple[str, Path, str | None]] = []
        self._target: dict | None = None  # {"dest", "method", "info"}
        self._samples: list[dict] = []
        self._cond: dict[str, str] = {}
        self._excluded: set[str] = set()
        self._other_overrides: dict = {}
        self._running = False
        f = ttk.Frame(app.nb, padding=8)
        app.nb.add(f, text="  7  Analysis  ")
        self.frame = f
        f.columnconfigure(0, weight=1)
        f.rowconfigure(0, weight=1)
        self.nb = ttk.Notebook(f)
        self.nb.grid(row=0, column=0, sticky="nsew")
        self._page_experiment()
        self._page_defaults()
        app.nb.bind("<<NotebookTabChanged>>", self._on_tab, add="+")

    # --------------------------------------------------------------- helpers --

    def v(self, key, default=""):
        return self.app.v(key, default)

    def bv(self, key, default=False):
        return self.app.bv(key, default)

    def _on_tab(self, _e=None):
        try:
            if self.app.nb.select() == str(self.frame):
                self.refresh_jobs()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------ page 1: one experiment --

    def _page_experiment(self):
        p = ttk.Frame(self.nb, padding=10)
        self.nb.add(p, text="Analyse an experiment")
        p.columnconfigure(1, weight=1)
        p.rowconfigure(2, weight=1)
        ttk.Label(p, text="Experiment").grid(row=0, column=0, sticky="e", **PAD)
        self.pick = ttk.Combobox(p, state="readonly", width=80)
        self.pick.grid(row=0, column=1, sticky="ew", **PAD)
        self.pick.bind("<<ComboboxSelected>>", lambda e: self._picked())
        bb = ttk.Frame(p)
        bb.grid(row=0, column=2, sticky="w")
        ttk.Button(bb, text="Folder…", command=self.choose_folder).pack(side="left", padx=2)
        ttk.Button(bb, text="Reload", command=self.reload_target).pack(side="left", padx=2)
        self.info = ttk.Label(p, text="Pick a finished job, or a folder with FragPipe output.", foreground="#555",
                              wraplength=900, justify="left")
        self.info.grid(row=1, column=0, columnspan=3, sticky="w", **PAD)

        body = ttk.Frame(p)
        body.grid(row=2, column=0, columnspan=3, sticky="nsew")
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(0, weight=1)
        sf = ttk.LabelFrame(body, text="Samples", padding=6)
        sf.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        sf.columnconfigure(0, weight=1)
        sf.rowconfigure(0, weight=1)
        cols = (("sample", 220), ("condition", 160), ("rep", 50), ("used", 70))
        self.tree = ttk.Treeview(sf, columns=[c for c, _ in cols], show="headings", height=10, selectmode="extended")
        for c, w in cols:
            self.tree.heading(c, text=c)
            self.tree.column(c, width=w, anchor="w", stretch=c in ("sample", "condition"))
        self.tree.tag_configure("off", foreground="#999")
        self.tree.tag_configure("changed", foreground="#1565c0")
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(sf, orient="vertical", command=self.tree.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.bind("<Double-1>", self._double_click)
        sbtn = ttk.Frame(sf)
        sbtn.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Button(sbtn, text="Change condition…", command=self.change_condition).pack(side="left", padx=2)
        ttk.Button(sbtn, text="Leave out / use", command=self.toggle_used).pack(side="left", padx=2)
        ttk.Button(sbtn, text="Undo sample changes", command=self.reset_samples).pack(side="left", padx=2)
        ttk.Label(sf, text="Double-click a condition to rename it (e.g. put a mislabelled sample in the right group), "
                           "or 'used' to leave a failed run out.", foreground="#666", wraplength=460,
                  justify="left").grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))

        right = ttk.Frame(body)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        cf = ttk.LabelFrame(right, text="Comparisons", padding=6)
        cf.grid(row=0, column=0, sticky="ew")
        cf.columnconfigure(1, weight=1)
        self.v("exp.de_type").set("control")
        ttk.Radiobutton(cf, text="each condition vs control:", value="control",
                        variable=self.v("exp.de_type")).grid(row=0, column=0, sticky="w")
        self.ctrl = ttk.Combobox(cf, textvariable=self.v("exp.control"), width=18)
        self.ctrl.grid(row=0, column=1, sticky="w", **PAD)
        ttk.Radiobutton(cf, text="all pairs", value="all", variable=self.v("exp.de_type")).grid(row=1, column=0, sticky="w")
        ttk.Radiobutton(cf, text="each condition vs all others", value="others",
                        variable=self.v("exp.de_type")).grid(row=2, column=0, columnspan=2, sticky="w")
        ttk.Radiobutton(cf, text="just these:", value="custom", variable=self.v("exp.de_type")).grid(row=3, column=0, sticky="w")
        ttk.Entry(cf, textvariable=self.v("exp.comparisons"), width=30).grid(row=3, column=1, sticky="ew", **PAD)
        ttk.Label(cf, text="e.g.  Drug vs DMSO; Drug2 vs DMSO", foreground="#666").grid(row=4, column=1, sticky="w", padx=6)

        of = ttk.LabelFrame(right, text="For this experiment only (blank = lab default)", padding=6)
        of.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        ttk.Label(of, text="|log2FC| ≥").grid(row=0, column=0, sticky="e", **PAD)
        ttk.Entry(of, textvariable=self.v("exp.log2fc"), width=8).grid(row=0, column=1, sticky="w", **PAD)
        ttk.Label(of, text="p ≤").grid(row=0, column=2, sticky="e", **PAD)
        ttk.Entry(of, textvariable=self.v("exp.alpha"), width=8).grid(row=0, column=3, sticky="w", **PAD)
        ttk.Label(of, text="Imputation").grid(row=1, column=0, sticky="e", **PAD)
        self.v("exp.imputation").set(LAB_DEFAULT)
        ttk.Combobox(of, textvariable=self.v("exp.imputation"), state="readonly", width=12,
                     values=[LAB_DEFAULT, "auto", "none", "perseus", "min", "zero", "mindet", "minprob", "knn"]
                     ).grid(row=1, column=1, columnspan=3, sticky="w", **PAD)
        ttk.Label(of, text="Normalisation").grid(row=2, column=0, sticky="e", **PAD)
        self.v("exp.normalize").set(LAB_DEFAULT)
        ttk.Combobox(of, textvariable=self.v("exp.normalize"), state="readonly", width=12,
                     values=[LAB_DEFAULT, "median", "gn", "none"]).grid(row=2, column=1, columnspan=3, sticky="w", **PAD)

        act = ttk.Frame(right)
        act.grid(row=2, column=0, sticky="w", pady=(10, 0))
        self.run_btn = ttk.Button(act, text="Run analysis", command=self.run)
        self.run_btn.pack(side="left", padx=2)
        ttk.Button(act, text="Save choices", command=self.save_choices).pack(side="left", padx=2)
        ttk.Button(act, text="Open report", command=self.open_report).pack(side="left", padx=2)
        ttk.Button(act, text="Results folder", command=self.open_results).pack(side="left", padx=2)
        self.state = ttk.Label(right, text="", foreground="#2e7d32", wraplength=380, justify="left")
        self.state.grid(row=3, column=0, sticky="w", pady=(6, 0))

        from ionomos.app import OutputPane

        self.out = OutputPane(p, height=7)
        self.out.grid(row=3, column=0, columnspan=3, sticky="nsew", pady=(8, 0))

    # jobs list
    def refresh_jobs(self):
        led = self.app._ledger()
        jobs = []
        if led is not None:
            try:
                jobs = [j for j in led.list() if j.status == "done"]
            finally:
                led.close()
        self._jobs = [(f"job {j.id} · {j.user}/{j.inbox_name} · {j.method}", Path(j.dest_dir), j.method)
                      for j in reversed(jobs)]
        labels = [x[0] for x in self._jobs]
        if self._target and not any(x[1] == self._target["dest"] for x in self._jobs):
            labels.append(str(self._target["dest"]))
        self.pick.configure(values=labels)

    def _picked(self):
        label = self.pick.get()
        for lab, dest, method in self._jobs:
            if lab == label:
                self.load(dest, method)
                return

    def choose_folder(self):
        d = filedialog.askdirectory(title="Experiment or FragPipe output folder")
        if d:
            self.load(Path(d), None)

    def select_job(self, job) -> None:
        """From the Jobs tab: open this job here."""
        self.app.nb.select(self.frame)
        self.nb.select(0)
        self.refresh_jobs()
        for lab, dest, _m in self._jobs:
            if dest == Path(job.dest_dir):
                self.pick.set(lab)
        self.load(Path(job.dest_dir), job.method)

    def reload_target(self):
        if self._target:
            self.load(self._target["dest"], self._target["method"])

    def load(self, dest: Path, method: str | None) -> None:
        """Read the experiment's samples (thread) and fill the page."""
        from ionomos import postprocess

        cfg = self._cfg()
        self.info.configure(text=f"reading {dest} …")
        self.state.configure(text="")

        def go():
            try:
                info = postprocess.inspect_folder(dest, cfg, method)
                err = None
            except Exception as exc:  # noqa: BLE001
                info, err = None, f"{type(exc).__name__}: {exc}"
            self.app.post(lambda: self._show(dest, method, info, err))

        threading.Thread(target=go, daemon=True).start()

    def _show(self, dest: Path, method: str | None, info: dict | None, err: str | None) -> None:
        if err or info is None:
            self.info.configure(text=f"Could not read {dest}: {err}", foreground="#c62828")
            return
        self._target = {"dest": dest, "method": info.get("method") or method, "info": info}
        if not any(x[1] == dest for x in self._jobs):
            self.pick.set(str(dest))
        ov = dict(info.get("overrides") or {})
        self._samples = info["samples"]
        self._cond = {k: v for k, v in (ov.pop("sample_conditions", None) or {}).items()}
        self._excluded = set(ov.pop("exclude_samples", None) or [])
        de = ov.pop("de_type", None)
        comps = ov.pop("comparisons", None)
        control = ov.pop("control", None)
        if comps:
            self.v("exp.de_type").set("custom")
            items = comps if isinstance(comps, list) else [comps]
            self.v("exp.comparisons").set("; ".join(" vs ".join(c) if isinstance(c, (list, tuple)) else str(c) for c in items))
        else:
            self.v("exp.de_type").set(de or (self.app.data.get("analysis") or {}).get("de_type") or "control")
            self.v("exp.comparisons").set("")
        for k in ("log2fc", "alpha"):
            val = ov.pop(k, None)
            self.v(f"exp.{k}").set("" if val is None else str(val))
        for k in ("imputation", "normalize"):
            val = ov.pop(k, None)
            self.v(f"exp.{k}").set(LAB_DEFAULT if val in (None, "") else str(val))
        self._other_overrides = ov  # anything else in experiment.yaml's analysis: is kept as it is
        conds = []
        for s in self._samples:
            c = self._cond.get(s["sample"], s["condition"])
            if c not in conds:
                conds.append(c)
        self.ctrl.configure(values=conds)
        self.v("exp.control").set(control or "")
        if not control and conds:
            from ionomos.downstream.analysis import AnalysisError, find_control, settings_from

            try:
                guess = find_control(conds, settings_from(self._lab_settings()))
            except AnalysisError:
                guess = None
            self.v("exp.control").set(guess or "")
        n = len(self._samples)
        src = Path(info["source"]).name if info.get("source") else "no result table"
        text = (f"{self._target['method'] or 'unknown method'} · {src} · {info['features']:,} "
                f"{'sites' if info.get('kind') == 'ratio' else 'features'} · {n} samples in {len(conds)} condition(s)")
        if info.get("notes"):
            text += "\n" + "\n".join(f"note: {x}" for x in info["notes"][:4])
        if info["report"].is_file():
            text += "\nThis experiment already has a report (Open report)."
        self.info.configure(text=text, foreground="#333")
        self._fill_tree()

    def _fill_tree(self):
        self.tree.delete(*self.tree.get_children())
        for s in self._samples:
            name = s["sample"]
            cond = self._cond.get(name, s["condition"])
            used = name not in self._excluded
            tags = ("off",) if not used else (("changed",) if name in self._cond else ())
            self.tree.insert("", "end", iid=name, tags=tags,
                             values=(name, cond, s.get("replicate") or "", "yes" if used else "left out"))

    def _selected(self) -> list[str]:
        return list(self.tree.selection())

    def _double_click(self, event):
        row = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)
        if not row:
            return
        self.tree.selection_set(row)
        if col == "#4":
            self.toggle_used()
        else:
            self.change_condition()

    def change_condition(self):
        sel = self._selected()
        if not sel:
            messagebox.showinfo("Samples", "Select one or more samples first.")
            return
        current = self._cond.get(sel[0]) or next((s["condition"] for s in self._samples if s["sample"] == sel[0]), "")
        new = simpledialog.askstring("Condition", f"Condition for {', '.join(sel[:4])}{'…' if len(sel) > 4 else ''}:",
                                     initialvalue=current, parent=self.frame)
        if new is not None:
            self.set_condition(sel, new)

    def set_condition(self, samples: list[str], condition: str) -> None:
        condition = condition.strip()
        for name in samples:
            base = next((s["condition"] for s in self._samples if s["sample"] == name), None)
            if not condition or condition == base:
                self._cond.pop(name, None)
            else:
                self._cond[name] = condition
        self._fill_tree()
        conds = list(dict.fromkeys(self._cond.get(s["sample"], s["condition"]) for s in self._samples))
        self.ctrl.configure(values=conds)

    def toggle_used(self):
        for name in self._selected():
            if name in self._excluded:
                self._excluded.discard(name)
            else:
                self._excluded.add(name)
        self._fill_tree()

    def reset_samples(self):
        self._cond.clear()
        self._excluded.clear()
        self._fill_tree()

    def choices(self) -> dict:
        """The experiment.yaml analysis: block these controls describe."""
        an = dict(self._other_overrides)
        if self._cond:
            an["sample_conditions"] = dict(self._cond)
        if self._excluded:
            an["exclude_samples"] = sorted(self._excluded)
        de = self.v("exp.de_type").get()
        if de == "custom":
            items = [x.strip() for x in self.v("exp.comparisons").get().replace("\n", ";").split(";") if x.strip()]
            if items:
                an["comparisons"] = items
        else:
            an["de_type"] = de
            ctrl = self.v("exp.control").get().strip()
            if de in ("control", "all") and ctrl:
                an["control"] = ctrl
        for k, conv in (("log2fc", float), ("alpha", float)):
            raw = self.v(f"exp.{k}").get().strip()
            if raw:
                try:
                    an[k] = conv(raw)
                except ValueError:
                    raise ValueError(f"{k} must be a number, got {raw!r}") from None
        for k in ("imputation", "normalize"):
            val = self.v(f"exp.{k}").get().strip()
            if val and val != LAB_DEFAULT:
                an[k] = val
        return an

    def save_choices(self, quiet: bool = False) -> bool:
        from ionomos.downstream.analysis import AnalysisError, settings_from
        from ionomos.manifest import OverridesError, load_overrides, save_overrides

        if not self._target:
            if not quiet:
                messagebox.showinfo("Analysis", "Pick an experiment first.")
            return False
        dest = self._target["dest"]
        try:
            an = self.choices()
            settings_from(self._lab_settings(), an)
            ov = load_overrides(dest)
        except (ValueError, AnalysisError, OverridesError) as exc:
            messagebox.showerror("Analysis choices", str(exc))
            return False
        if ov.analysis == an:
            return True
        ov.analysis = an
        try:
            path = save_overrides(dest, ov)
        except OSError as exc:
            messagebox.showerror("Analysis choices", f"Could not write experiment.yaml:\n{exc}")
            return False
        self.state.configure(text=f"saved in {path.name}", foreground="#2e7d32")
        log.info("analysis choices saved in %s", path)
        return True

    def run(self):
        from ionomos import postprocess

        if self._running:
            return
        if not self._target:
            messagebox.showinfo("Analysis", "Pick an experiment first.")
            return
        if not self.save_choices(quiet=True):
            return
        dest, method = self._target["dest"], self._target["method"]
        cfg = self._cfg()
        self._running = True
        self.run_btn.state(["disabled"])
        self.out.write(f"analysing {dest} …", clear=True)

        def progress(msg):
            self.app.post(lambda: (self.state.configure(text=msg + " …", foreground="#1565c0"), self.out.write(msg)))

        def go():
            try:
                out = postprocess.run_for_folder(dest, cfg, method, progress=progress)
                err = None
            except Exception as exc:  # noqa: BLE001 - analyze() never raises; belt and braces
                out, err = None, f"{type(exc).__name__}: {exc}"
            self.app.post(lambda: self._done(out, err))

        threading.Thread(target=go, daemon=True).start()

    def _done(self, out, err):
        self._running = False
        self.run_btn.state(["!disabled"])
        if err or out is None:
            self.state.configure(text=f"analysis failed: {err}", foreground="#c62828")
            self.out.write(f"analysis failed: {err}")
            return
        lines = [f"{c['name']}: {c['up']} up, {c['down']} down of {c['tested']} tested"
                 for c in out.summary.get("comparisons", [])]
        for st in out.summary.get("processing", []):
            if "removed" in st:
                lines.append(f"  {st['step']}: −{st['removed']}")
            elif st["step"] == "imputation":
                lines.append(f"  imputed {st['values']} values ({st['percent']}%) — {st['method']}")
        lines += [f"note: {w}" for w in out.warnings]
        self.out.write("\n".join(lines) or "done")
        self.state.configure(text=("done" if not out.warnings else f"done with {len(out.warnings)} note(s)") +
                             " — the report is open in your browser", foreground="#2e7d32")
        if out.report and out.report.is_file():
            self.app._open(str(out.report))

    def open_report(self):
        if self._target and self._target["info"]["report"].is_file():
            self.app._open(str(self._target["info"]["report"]))
        else:
            messagebox.showinfo("Report", "No report yet: press Run analysis.")

    def open_results(self):
        if self._target:
            from ionomos import downstream

            d = self._target["dest"] / downstream.RESULTS
            self.app._open(str(d if d.is_dir() else self._target["dest"]))

    def _cfg(self):
        from ionomos.config import ConfigError, load

        try:
            return load(self.app.config_path, check_paths=False)
        except (ConfigError, OSError):
            return None

    def _lab_settings(self) -> dict:
        an = dict((self.app.data or {}).get("analysis") or {})
        an.pop("enabled", None)
        return an

    # ----------------------------------------------------- page 2: defaults --

    def _page_defaults(self):
        p = ttk.Frame(self.nb, padding=10)
        self.nb.add(p, text="Lab defaults")
        p.columnconfigure(0, weight=1)
        p.columnconfigure(1, weight=1)
        ttk.Label(p, text="Used after every FragPipe search and whenever an experiment doesn't choose otherwise. "
                          "The statistics are FragPipe-Analyst's (FragPipeAnalystR, Nesvilab): filter → normalise → "
                          "impute → limma. Press Save (bottom right) to keep changes.",
                  wraplength=900, justify="left").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        ttk.Checkbutton(p, text="Make statistics, volcano plots and a report after each search",
                        variable=self.bv("analysis.enabled", True)).grid(row=1, column=0, columnspan=2, sticky="w", **PAD)

        st = ttk.LabelFrame(p, text="Statistics", padding=6)
        st.grid(row=2, column=0, sticky="nsew", padx=(0, 6), pady=4)
        pr = ttk.LabelFrame(p, text="Processing (label-free intensities; TMT and isoDTB ratios skip imputation)", padding=6)
        pr.grid(row=2, column=1, sticky="nsew", pady=4)

        def row(frame, r, label, widget, hint=""):
            ttk.Label(frame, text=label).grid(row=r, column=0, sticky="e", **PAD)
            widget.grid(row=r, column=1, sticky="w", **PAD)
            if hint:
                ttk.Label(frame, text=hint, foreground="#666", wraplength=260, justify="left").grid(row=r, column=2, sticky="w", **PAD)

        row(st, 0, "Test", ttk.Combobox(st, textvariable=self.v("analysis.test"), state="readonly", width=12,
                                        values=["limma", "welch", "student"]), "limma = moderated t (FragPipe-Analyst)")
        row(st, 1, "Comparisons", ttk.Combobox(st, textvariable=self.v("analysis.de_type"), state="readonly", width=12,
                                               values=["control", "all", "others"]),
            "control: each vs the control · all: every pair · others: each vs the rest")
        row(st, 2, "|log2FC| ≥", ttk.Entry(st, textvariable=self.v("analysis.log2fc"), width=8), "1 = 2-fold")
        row(st, 3, "p ≤", ttk.Entry(st, textvariable=self.v("analysis.alpha"), width=8), "0.05 is usual")
        ttk.Checkbutton(st, text="…on Benjamini-Hochberg adjusted p (recommended)",
                        variable=self.bv("analysis.use_adjusted", True)).grid(row=4, column=1, columnspan=2, sticky="w", **PAD)
        row(st, 5, "Control keywords", ttk.Entry(st, textvariable=self.v("analysis.control_keywords"), width=30),
            "the first condition matching one of these is the control")
        row(st, 6, "Names on volcano", ttk.Entry(st, textvariable=self.v("analysis.top_labels"), width=8))

        ttk.Checkbutton(pr, text="Remove contaminants (contam_ proteins)",
                        variable=self.bv("analysis.remove_contaminants", True)).grid(row=0, column=0, columnspan=3, sticky="w", **PAD)
        row(pr, 1, "Measured in ≥ % of all samples", ttk.Entry(pr, textvariable=self.v("analysis.filter_global_pct"), width=6), "0 = off")
        row(pr, 2, "… and ≥ % of one condition", ttk.Entry(pr, textvariable=self.v("analysis.filter_condition_pct"), width=6),
            "50 = 2 of 3 replicates (FragPipe-Analyst: 0)")
        row(pr, 3, "Normalisation", ttk.Combobox(pr, textvariable=self.v("analysis.normalize"), state="readonly", width=10,
                                                 values=["median", "gn", "none"]), "median centring; gn adds MAD scaling")
        row(pr, 4, "Imputation", ttk.Combobox(pr, textvariable=self.v("analysis.imputation"), state="readonly", width=10,
                                              values=["auto", "perseus", "none", "min", "zero", "mindet", "minprob", "knn"]),
            "auto = Perseus-type for LFQ/DIA, none for TMT")
        row(pr, 5, "Values per group ≥", ttk.Entry(pr, textvariable=self.v("analysis.min_valid"), width=6),
            "only when nothing is imputed")

        en = ttk.LabelFrame(p, text="Enrichment (gene sets downloaded once from Enrichr, tested on this PC)", padding=6)
        en.grid(row=3, column=0, columnspan=2, sticky="ew", pady=4)
        ttk.Checkbutton(en, text="Test the hits for over-represented pathways / GO terms",
                        variable=self.bv("analysis.enrichment", True)).grid(row=0, column=0, columnspan=4, sticky="w", **PAD)
        from ionomos.downstream.enrich import LIBRARIES

        for k, name in enumerate(LIBRARIES):
            ttk.Checkbutton(en, text=name, variable=self.bv(f"analysis.lib.{name}")).grid(row=1 + k // 4, column=k % 4, sticky="w", **PAD)
        g = ttk.Frame(en)
        g.grid(row=3, column=0, columnspan=4, sticky="w")
        ttk.Label(g, text="Own gene sets (.gmt)").pack(side="left", padx=6)
        ttk.Entry(g, textvariable=self.v("analysis.enrichment_gmt"), width=50).pack(side="left")
        ttk.Button(g, text="Browse…", command=self._browse_gmt).pack(side="left", padx=4)

        b = ttk.Frame(p)
        b.grid(row=4, column=0, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Button(b, text="Use FragPipe-Analyst's defaults", command=lambda: self._preset(FRAGPIPE_ANALYST_DEFAULTS)).pack(side="left", padx=2)
        ttk.Button(b, text="Use Ionomos defaults", command=lambda: self._preset(IONOMOS_DEFAULTS)).pack(side="left", padx=2)
        ttk.Button(b, text="Analyse a folder…", command=self._analyse_folder).pack(side="left", padx=12)

    def _browse_gmt(self):
        f = filedialog.askopenfilename(title="Gene sets (.gmt)", filetypes=[("GMT gene sets", "*.gmt"), ("All files", "*.*")])
        if f:
            self.v("analysis.enrichment_gmt").set(f.replace("\\", "/"))

    def _preset(self, values: dict):
        for k, val in values.items():
            if isinstance(val, bool):
                self.bv(f"analysis.{k}").set(val)
            else:
                self.v(f"analysis.{k}").set(val)
        self.app.set_status("defaults filled in — press Save to keep them")

    def _analyse_folder(self):
        self.nb.select(0)
        self.choose_folder()

    # ------------------------------------------------ config <-> variables --

    def load_vars(self, d: dict) -> None:
        from ionomos.downstream.enrich import DEFAULT_LIBRARIES, LIBRARIES

        an = d.get("analysis") or {}
        for k in ("log2fc", "alpha", "min_valid", "top_labels", "filter_global_pct", "filter_condition_pct",
                  "enrichment_gmt"):
            self.v(f"analysis.{k}").set("" if an.get(k) is None else str(an.get(k)))
        test = str(an.get("test") or "limma")
        self.v("analysis.test").set("limma" if test == "moderated" else test)
        self.v("analysis.de_type").set(str(an.get("de_type") or "control"))
        self.v("analysis.normalize").set(str(an.get("normalize") or "median"))
        self.v("analysis.imputation").set(str(an.get("imputation") or "auto"))
        for k, dflt in (("enabled", True), ("use_adjusted", True), ("remove_contaminants", True), ("enrichment", True)):
            self.bv(f"analysis.{k}", dflt).set(bool(an.get(k, dflt)))
        libs = an.get("enrichment_libraries")
        libs = list(DEFAULT_LIBRARIES) if libs is None else list(libs)
        for name in LIBRARIES:
            self.bv(f"analysis.lib.{name}").set(name in libs)
        self.v("analysis.control_keywords").set(", ".join(an.get("control_keywords") or []))

    def collect(self, d: dict) -> None:
        from ionomos.config import ConfigError
        from ionomos.downstream.enrich import LIBRARIES

        an = dict(d.get("analysis") or {})
        for k in ("enabled", "use_adjusted", "remove_contaminants", "enrichment"):
            an[k] = self.bv(f"analysis.{k}", True).get()
        for k, conv in NUM_KEYS:
            raw = self.v(f"analysis.{k}").get().strip()
            try:
                an[k] = conv(raw)
            except ValueError:
                raise ConfigError(f"analysis.{k} must be a number, got {raw!r}") from None
        an["test"] = self.v("analysis.test").get().strip() or "limma"
        an["de_type"] = self.v("analysis.de_type").get().strip() or "control"
        an["normalize"] = self.v("analysis.normalize").get().strip() or "median"
        an["imputation"] = self.v("analysis.imputation").get().strip() or "auto"
        an["enrichment_libraries"] = [n for n in LIBRARIES if self.bv(f"analysis.lib.{n}").get()]
        an["enrichment_gmt"] = self.v("analysis.enrichment_gmt").get().strip()
        an["control_keywords"] = [x.strip() for x in self.v("analysis.control_keywords").get().split(",") if x.strip()]
        d["analysis"] = an
