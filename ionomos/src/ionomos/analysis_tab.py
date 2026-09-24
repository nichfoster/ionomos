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
from pathlib import Path
from tkinter import filedialog, ttk

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
        self._jobs: list[tuple[str, Path, str | None, int]] = []
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
        from ionomos.experiment_editor import EditorHost, ExperimentEditor

        p = ttk.Frame(self.nb, padding=10)
        self.nb.add(p, text="Analyse an experiment")
        p.columnconfigure(1, weight=1)
        p.rowconfigure(1, weight=1)
        ttk.Label(p, text="Experiment").grid(row=0, column=0, sticky="e", **PAD)
        self.pick = ttk.Combobox(p, state="readonly", width=80)
        self.pick.grid(row=0, column=1, sticky="ew", **PAD)
        self.pick.bind("<<ComboboxSelected>>", lambda e: self._picked())
        bb = ttk.Frame(p)
        bb.grid(row=0, column=2, sticky="w")
        ttk.Button(bb, text="Folder…", command=self.choose_folder).pack(side="left", padx=2)
        ttk.Button(bb, text="Reload", command=self.reload_target).pack(side="left", padx=2)
        host = EditorHost(post=self.app.post, open_path=lambda x: self.app._open(x),
                          config_path=lambda: self.app.config_path, log_dir=self._log_dir,
                          lab_settings=lambda: dict((self.app.data or {}).get("analysis") or {}),
                          status=self.app.set_status)
        self.editor = ExperimentEditor(p, host, on_done=lambda out: self.app.refresh_attention())
        self.editor.frame.grid(row=1, column=0, columnspan=3, sticky="nsew")
        self.editor.info.configure(text="Pick a finished job, or a folder with FragPipe output.")

    def _log_dir(self):
        try:
            return self.app._active_log_dir()
        except Exception:  # noqa: BLE001
            return None

    # jobs list
    def refresh_jobs(self):
        led = self.app._ledger()
        jobs = []
        if led is not None:
            try:
                jobs = [j for j in led.list() if j.status == "done"]
            finally:
                led.close()
        self._jobs = [(f"job {j.id} · {j.user}/{j.inbox_name} · {j.method}", Path(j.dest_dir), j.method, j.id)
                      for j in reversed(jobs)]
        labels = [x[0] for x in self._jobs]
        t = self.editor.target
        if t and not any(x[1] == t["dest"] for x in self._jobs):
            labels.append(str(t["dest"]))
        self.pick.configure(values=labels)

    def _picked(self):
        label = self.pick.get()
        for lab, dest, method, jid in self._jobs:
            if lab == label:
                self.editor.load(dest, method, jid)
                return

    def choose_folder(self):
        d = self.editor.choose_folder()
        if d:
            self.pick.set(str(d))
            self.editor.load(d, None)

    def select_job(self, job) -> None:
        """From the Jobs tab: open this job here."""
        self.app.nb.select(self.frame)
        self.nb.select(0)
        self.refresh_jobs()
        for lab, dest, _m, _j in self._jobs:
            if dest == Path(job.dest_dir):
                self.pick.set(lab)
        self.editor.load(Path(job.dest_dir), job.method, job.id)

    def open_folder(self, dest: Path, method: str | None = None, job_id: int | None = None) -> None:
        self.app.nb.select(self.frame)
        self.nb.select(0)
        self.pick.set(str(dest))
        self.editor.load(Path(dest), method, job_id)

    def reload_target(self):
        t = self.editor.target
        if t:
            self.editor.load(t["dest"], t["method"], t.get("job_id"))

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

    def _cfg(self):
        from ionomos.config import ConfigError, load

        try:
            return load(self.app.config_path, check_paths=False)
        except (ConfigError, OSError):
            return None

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
