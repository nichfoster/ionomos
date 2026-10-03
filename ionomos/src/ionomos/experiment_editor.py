"""
One experiment's analysis choices, as a reusable Tk panel: used on the app's Analysis tab and inside
the pop-up window that opens when an analysis needs a person.

    host = EditorHost(post=..., open_path=..., config_path=..., log_dir=..., lab_settings=..., status=...)
    ed = ExperimentEditor(parent_frame, host, on_done=callback)
    ed.load(dest, method)                 # reads the samples on a thread
    ed.set_condition(["Drug_3"], "DMSO"); ed.toggle_used(["DMSO_1"]); ed.guess_conditions()
    ed.set_role("Probe_pre", "compound"); ed.confirm_role("Probe_10x")    # analysis.roles (D65)
    ed.run()                              # saves experiment.yaml analysis:, re-runs the analysis

Shows: what the data is, the doctor's current issues (from the last analysis), every sample with
its condition / replicate / used, the comparisons, each condition's role with its samples (and a list
to change it; a guess from a weak keyword is marked to confirm), the comparisons that will be run in
words and what uneven groups mean (downstream/roles.preview, which holds the logic), per-experiment
cut-offs. After a run the issues are refreshed and the attention item for the experiment is closed
when nothing is left to decide.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk

from ionomos import tkutil

log = logging.getLogger("ionomos.app")

NORMALIZATION_CHOICES = ("auto", "median", "gn", "ratio", "none")  # downstream/fpa.py, D64; as the Analysis tab

PAD = {"padx": 6, "pady": 3}
LAB_DEFAULT = "(lab default)"
SEV_COLOUR = {"error": "#c62828", "input": "#b26a00", "warning": "#555"}
SEV_WORD = {"error": "PROBLEM", "input": "DECIDE", "warning": "note"}


@dataclass
class EditorHost:
    post: Callable[[Callable], None]           # run on the Tk thread
    open_path: Callable[[str], None]
    config_path: Callable[[], Path | None]
    log_dir: Callable[[], Path | None]
    lab_settings: Callable[[], dict]
    status: Callable[[str], None] = lambda msg: None


class ExperimentEditor:
    def __init__(self, parent, host: EditorHost, on_done: Callable | None = None, compact: bool = False):
        self.host = host
        self.on_done = on_done
        self.vars: dict[str, tkutil.StringVar] = {}
        self.target: dict | None = None
        self._samples: list[dict] = []
        self._cond: dict[str, str] = {}
        self._excluded: set[str] = set()
        self._other: dict = {}
        self._roles: dict = {}          # this experiment's analysis.roles (D65)
        self._view = None               # roles.RolePreview of the current choices
        self._role_values: dict[str, str] = {}   # what the role list shows -> analysis.roles value
        self.running = False
        self.last_outcome = None
        self.frame = f = ttk.Frame(parent)
        f.columnconfigure(0, weight=1)
        f.rowconfigure(2, weight=1)

        self.info = ttk.Label(f, text="", foreground="#555", wraplength=900, justify="left")
        self.info.grid(row=0, column=0, sticky="w", **PAD)
        self.issues = scrolledtext.ScrolledText(f, height=5 if not compact else 6, wrap="word",
                                                font=("Segoe UI" if _win() else "Helvetica", 10))
        self.issues.grid(row=1, column=0, sticky="ew", **PAD)
        for sev, colour in SEV_COLOUR.items():
            self.issues.tag_configure(sev, foreground=colour)
        self.issues.tag_configure("b", font=("Segoe UI" if _win() else "Helvetica", 10, "bold"))
        self.issues.configure(state="disabled")

        body = ttk.Frame(f)
        body.grid(row=2, column=0, sticky="nsew")
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(0, weight=1)
        sf = ttk.LabelFrame(body, text="Samples", padding=6)
        sf.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        sf.columnconfigure(0, weight=1)
        sf.rowconfigure(0, weight=1)
        cols = (("sample", 200), ("condition", 150), ("rep", 45), ("used", 70), ("run file", 220))
        self.tree = ttk.Treeview(sf, columns=[c for c, _ in cols], show="headings", height=9, selectmode="extended")
        for c, w in cols:
            self.tree.heading(c, text=c)
            self.tree.column(c, width=w, anchor="w", stretch=c in ("sample", "condition", "run file"))
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
        ttk.Button(sbtn, text="Guess from names", command=self.guess_conditions).pack(side="left", padx=2)
        ttk.Button(sbtn, text="Undo", command=self.reset_samples).pack(side="left", padx=2)
        ttk.Label(sf, text="Double-click a condition to change it (select several to change them together); "
                           "double-click 'used' to leave a failed run out.", foreground="#666", wraplength=460,
                  justify="left").grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))

        right = ttk.Frame(body)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        cf = ttk.LabelFrame(right, text="Comparisons", padding=6)
        cf.grid(row=0, column=0, sticky="ew")
        cf.columnconfigure(1, weight=1)
        self.var("de_type").set("control")
        ttk.Radiobutton(cf, text="each condition vs control:", value="control",
                        variable=self.var("de_type")).grid(row=0, column=0, sticky="w")
        self.ctrl = ttk.Combobox(cf, textvariable=self.var("control"), width=18)
        self.ctrl.grid(row=0, column=1, sticky="w", **PAD)
        ttk.Radiobutton(cf, text="all pairs", value="all", variable=self.var("de_type")).grid(row=1, column=0, sticky="w")
        ttk.Radiobutton(cf, text="each condition vs all others", value="others",
                        variable=self.var("de_type")).grid(row=2, column=0, columnspan=2, sticky="w")
        ttk.Radiobutton(cf, text="just these:", value="custom", variable=self.var("de_type")).grid(row=3, column=0, sticky="w")
        ttk.Entry(cf, textvariable=self.var("comparisons"), width=30).grid(row=3, column=1, sticky="ew", **PAD)
        ttk.Label(cf, text="e.g.  Drug vs DMSO; Drug2 vs DMSO", foreground="#666").grid(row=4, column=1, sticky="w", padx=6)

        rf = ttk.LabelFrame(right, text="Roles (what each condition is)", padding=6)
        rf.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        rf.columnconfigure(1, weight=1)
        rcols = (("condition", 120), ("samples", 60), ("role", 150), ("from", 190))
        self.roles_tree = ttk.Treeview(rf, columns=[c for c, _ in rcols], show="headings", height=4,
                                       selectmode="browse")
        for c, w in rcols:
            self.roles_tree.heading(c, text=c)
            self.roles_tree.column(c, width=w, anchor="w", stretch=c in ("role", "from"))
        self.roles_tree.tag_configure("ask", foreground=SEV_COLOUR["input"])
        self.roles_tree.tag_configure("set", foreground="#1565c0")
        self.roles_tree.grid(row=0, column=0, columnspan=3, sticky="ew")
        self.roles_tree.bind("<<TreeviewSelect>>", self._role_selected)
        ttk.Label(rf, text="Role of the selected condition:").grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.role_cb = ttk.Combobox(rf, textvariable=self.var("role"), state="readonly", width=30)
        self.role_cb.grid(row=1, column=1, sticky="w", padx=4, pady=(4, 0))
        self.role_cb.bind("<<ComboboxSelected>>", self._role_chosen)
        self.confirm_btn = ttk.Button(rf, text="Confirm", command=self.confirm_role)
        self.confirm_btn.grid(row=1, column=2, sticky="w", pady=(4, 0))
        self.role_ask = ttk.Label(rf, text="", foreground=SEV_COLOUR["input"], wraplength=440, justify="left")
        self.role_ask.grid(row=2, column=0, columnspan=3, sticky="w")
        self.role_plan = ttk.Label(rf, text="", foreground="#333", wraplength=440, justify="left")
        self.role_plan.grid(row=3, column=0, columnspan=3, sticky="w", pady=(4, 0))
        for k in ("de_type", "control", "comparisons"):
            self.var(k).trace_add("write", lambda *_: self.refresh_roles())

        of = ttk.LabelFrame(right, text="For this experiment only (blank = lab default)", padding=6)
        of.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        ttk.Label(of, text="|log2FC| ≥").grid(row=0, column=0, sticky="e", **PAD)
        ttk.Entry(of, textvariable=self.var("log2fc"), width=8).grid(row=0, column=1, sticky="w", **PAD)
        ttk.Label(of, text="p ≤").grid(row=0, column=2, sticky="e", **PAD)
        ttk.Entry(of, textvariable=self.var("alpha"), width=8).grid(row=0, column=3, sticky="w", **PAD)
        ttk.Label(of, text="Imputation").grid(row=1, column=0, sticky="e", **PAD)
        self.var("imputation").set(LAB_DEFAULT)
        ttk.Combobox(of, textvariable=self.var("imputation"), state="readonly", width=12,
                     values=[LAB_DEFAULT, "auto", "none", "perseus", "min", "zero", "mindet", "minprob", "knn"]
                     ).grid(row=1, column=1, columnspan=3, sticky="w", **PAD)
        ttk.Label(of, text="Normalisation").grid(row=2, column=0, sticky="e", **PAD)
        self.var("normalize").set(LAB_DEFAULT)
        ttk.Combobox(of, textvariable=self.var("normalize"), state="readonly", width=12,
                     values=[LAB_DEFAULT, *NORMALIZATION_CHOICES]).grid(row=2, column=1, columnspan=3, sticky="w", **PAD)

        act = ttk.Frame(right)
        act.grid(row=3, column=0, sticky="w", pady=(10, 0))
        self.run_btn = ttk.Button(act, text="Run analysis", command=self.run)
        self.run_btn.pack(side="left", padx=2)
        ttk.Button(act, text="Save choices", command=self.save_choices).pack(side="left", padx=2)
        ttk.Button(act, text="Open report", command=self.open_report).pack(side="left", padx=2)
        ttk.Button(act, text="Results folder", command=self.open_results).pack(side="left", padx=2)
        self.state = ttk.Label(right, text="", foreground="#2e7d32", wraplength=380, justify="left")
        self.state.grid(row=4, column=0, sticky="w", pady=(6, 0))
        self.log = scrolledtext.ScrolledText(f, height=5 if not compact else 4, wrap="word",
                                             font=("Consolas" if _win() else "Menlo", 9))
        self.log.grid(row=3, column=0, sticky="nsew", pady=(8, 0))
        self.log.configure(state="disabled")

    # ------------------------------------------------------------- helpers --

    def var(self, key: str) -> tkutil.StringVar:
        if key not in self.vars:
            self.vars[key] = tkutil.StringVar(value="")
        return self.vars[key]

    def write(self, text: str, clear: bool = False) -> None:
        self.log.configure(state="normal")
        if clear:
            self.log.delete("1.0", "end")
        self.log.insert("end", text if text.endswith("\n") else text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def show_issues(self, issues: list[dict]) -> None:
        t = self.issues
        t.configure(state="normal")
        t.delete("1.0", "end")
        order = {"error": 0, "input": 1, "warning": 2}
        issues = sorted(issues or [], key=lambda i: order.get(i.get("severity"), 3))
        if not issues:
            t.insert("end", "No problems found in the last analysis." if self.target else "")
        for i in issues:
            sev = i.get("severity", "warning")
            t.insert("end", f"{SEV_WORD.get(sev, sev)}  ", (sev, "b"))
            t.insert("end", i.get("title", "") + "\n", ("b",))
            t.insert("end", "   " + i.get("message", "") + "\n")
            if i.get("fixes"):
                t.insert("end", "   → " + i["fixes"][0] + "\n", (sev,))
        t.configure(state="disabled")

    def _cfg(self):
        from ionomos.config import ConfigError, load

        p = self.host.config_path()
        if p is None:
            return None
        try:
            return load(p, check_paths=False)
        except (ConfigError, OSError):
            return None

    # ----------------------------------------------------------------- load --

    def load(self, dest: Path, method: str | None, job_id: int | None = None, table: Path | None = None) -> None:
        from ionomos import postprocess

        cfg = self._cfg()
        dest = Path(dest)
        self.info.configure(text=f"reading {dest} …", foreground="#555")
        self.state.configure(text="")

        def go():
            try:
                info, err = postprocess.inspect_folder(dest, cfg, method, table), None
            except Exception as exc:  # noqa: BLE001
                info, err = None, f"{type(exc).__name__}: {exc}"
            self.host.post(lambda: self._show(dest, method, job_id, info, err, table))

        threading.Thread(target=go, daemon=True).start()

    def _show(self, dest: Path, method: str | None, job_id, info: dict | None, err: str | None,
              table: Path | None = None) -> None:
        if err or info is None:
            self.info.configure(text=f"Could not read {dest}: {err}", foreground="#c62828")
            return
        self.target = {"dest": dest, "method": info.get("method") or method, "info": info, "job_id": job_id,
                       "table": table}
        ov = dict(info.get("overrides") or {})
        self._samples = info["samples"]
        self._cond = dict(ov.pop("sample_conditions", None) or {})
        self._excluded = set(ov.pop("exclude_samples", None) or [])
        given = ov.pop("roles", None)
        self._roles = dict(given) if isinstance(given, dict) else {}
        if given is not None and not isinstance(given, dict):
            ov["roles"] = given  # not a mapping: kept as written, and the preview says what is wrong
        de = ov.pop("de_type", None)
        comps = ov.pop("comparisons", None)
        control = ov.pop("control", None)
        if comps:
            self.var("de_type").set("custom")
            items = comps if isinstance(comps, list) else [comps]
            self.var("comparisons").set("; ".join(" vs ".join(c) if isinstance(c, (list, tuple)) else str(c) for c in items))
        else:
            self.var("de_type").set(de or self.host.lab_settings().get("de_type") or "control")
            self.var("comparisons").set("")
        for k in ("log2fc", "alpha"):
            val = ov.pop(k, None)
            self.var(k).set("" if val is None else str(val))
        for k in ("imputation", "normalize"):
            val = ov.pop(k, None)
            self.var(k).set(LAB_DEFAULT if val in (None, "") else str(val))
        self._other = ov
        conds = self.conditions()
        self.ctrl.configure(values=conds)
        self.var("control").set(control or "")
        if not control and conds:
            from ionomos.downstream.analysis import AnalysisError, find_control, settings_from

            try:
                guess = find_control(conds, settings_from(self._lab()))
            except AnalysisError:
                guess = None
            self.var("control").set(guess or "")
        n = len(self._samples)
        src = Path(info["source"]).name if info.get("source") else "no result table"
        text = (f"{dest.name} — {self.target['method'] or 'unknown method'} · {src} · {info['features']:,} "
                f"{'sites' if info.get('kind') == 'ratio' else 'features'} · {n} samples in {len(conds)} condition(s)")
        self.info.configure(text=text, foreground="#333")
        self.show_issues(info.get("issues") or [])
        self._fill()

    def conditions(self) -> list[str]:
        return list(dict.fromkeys(self._cond.get(s["sample"], s["condition"]) for s in self._samples
                                  if s["sample"] not in self._excluded))

    def _fill(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for s in self._samples:
            name = s["sample"]
            used = name not in self._excluded
            tags = ("off",) if not used else (("changed",) if name in self._cond else ())
            self.tree.insert("", "end", iid=name, tags=tags, values=(
                name, self._cond.get(name, s["condition"]), s.get("replicate") or "", "yes" if used else "left out",
                s.get("run") or ""))
        self.ctrl.configure(values=self.conditions())
        self.refresh_roles()

    # --------------------------------------------------------------- roles --

    def sizes(self) -> dict[str, int]:
        """{condition: used samples}, in the samples' order."""
        out: dict[str, int] = {}
        for s in self._samples:
            if s["sample"] not in self._excluded:
                c = self._cond.get(s["sample"], s["condition"])
                out[c] = out.get(c, 0) + 1
        return out

    def role_view(self):
        """roles.preview of the current choices: the rows, the comparisons that will run, uneven groups."""
        from ionomos.downstream import roles
        from ionomos.downstream.analysis import AnalysisError, settings_from

        if not self.target:
            return None
        info = self.target["info"]
        try:
            an = self.choices()
        except ValueError:  # a cut-off being typed: the roles don't depend on it
            an = {k: v for k, v in self._other.items()}
        an.pop("roles", None)
        err = ""
        try:
            s = settings_from(self._lab(), an)
        except AnalysisError as exc:  # the rows can still be shown from the lab's settings
            err = str(exc)
            try:
                s = settings_from(self._lab())
            except AnalysisError as exc2:
                return roles.RolePreview(error=str(exc2))
        view = roles.preview(self.sizes(), s, self._roles, info.get("kind") or "intensity", info.get("exp") or "",
                             info.get("sdrf_roles"), info.get("sdrf_file") or "")
        if err:
            view.error, view.comparisons, view.uneven, view.notes = err, [], [], []
        return view

    def refresh_roles(self) -> None:
        try:
            view = self.role_view()
        except Exception:  # noqa: BLE001 - the preview must never break the editor
            log.exception("roles preview failed")
            view = None
        self._view = view
        sel = self.roles_tree.selection()
        self.roles_tree.delete(*self.roles_tree.get_children())
        if view is None:
            self.role_plan.configure(text="")
            self.role_ask.configure(text="")
            return
        for r in view.rows:
            mark = "  ?" if r.confirm else ""
            self.roles_tree.insert("", "end", iid=r.condition, values=(r.condition, r.n, r.label + mark, r.source),
                                   tags=("ask",) if r.confirm else ("set",) if r.set_here else ())
        keep = [x for x in sel if self.roles_tree.exists(x)] or [r.condition for r in view.rows if r.confirm][:1]
        if keep:  # the selection survives a refresh; with none, the first role to confirm is selected
            self.roles_tree.selection_set(keep)
        self.role_plan.configure(text="\n".join(view.lines()))
        self._role_selected()

    def _row(self, condition: str | None = None):
        if self._view is None:
            return None
        if condition is None:
            sel = self.roles_tree.selection()
            condition = sel[0] if sel else None
        return next((r for r in self._view.rows if r.condition == condition), None)

    def _role_selected(self, _event=None) -> None:
        from ionomos.downstream import roles

        row = self._row()
        fixed = self._view is None or self._view.by_construction or row is None
        self.role_cb.state(["disabled"] if fixed else ["!disabled"])
        self.confirm_btn.state(["!disabled"] if not fixed and row.confirmable else ["disabled"])
        if fixed:
            self.role_cb.configure(values=[])
            self.var("role").set("isoDTB: every condition competes with the probe" if self._view is not None
                                 and self._view.by_construction else "")
            hint = "Select a condition above to change its role." if row is None and self._view is not None \
                and self._view.rows and not self._view.by_construction else ""
            self.role_ask.configure(text=hint if row is None or not row.confirm else f"{row.condition}: {row.confirm}")
            return
        pairs = roles.role_choices(self._view, row.condition)
        self._role_values = dict(pairs)
        self.role_cb.configure(values=[s for s, _v in pairs])
        self.var("role").set(next((s for s, v in pairs if row.set_here and v == row.role), pairs[0][0]))
        self.role_ask.configure(text=f"{row.condition}: {row.confirm}" if row.confirm else "")

    def _role_chosen(self, _event=None) -> None:
        row = self._row()
        if row is not None:
            self.set_role(row.condition, self._role_values.get(self.var("role").get(), ""))

    def set_role(self, condition: str, value: str) -> None:
        """value: control | compound | competition of X | competition | reference | qc; "" = automatic. A
        condition made the control also becomes the Control choice."""
        from ionomos.downstream import roles

        try:
            self._roles = roles.set_role(self._roles, condition, value)
        except roles.RoleError as exc:
            messagebox.showerror("Role", str(exc), parent=self.frame)
            return
        if value == "control" and self.var("de_type").get() in ("control", "all"):
            self.var("control").set(condition)  # refreshes through the variable's trace
        self.refresh_roles()

    def confirm_role(self, condition: str | None = None) -> None:
        """Keep the role read from the name ('Probe_pre': competition of Probe): it is written to analysis.roles,
        so the analysis stops asking."""
        row = self._row(condition)
        if row is not None and row.confirmable:
            self.set_role(row.condition, row.role)

    # --------------------------------------------------------------- edits --

    def _double_click(self, event):
        row = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)
        if not row:
            return
        if row not in self.tree.selection():
            self.tree.selection_set(row)
        if col == "#4":
            self.toggle_used()
        else:
            self.change_condition()

    def change_condition(self):
        sel = list(self.tree.selection())
        if not sel:
            messagebox.showinfo("Samples", "Select one or more samples first.", parent=self.frame)
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
        self._fill()

    def toggle_used(self, samples: list[str] | None = None):
        for name in samples if samples is not None else list(self.tree.selection()):
            if name in self._excluded:
                self._excluded.discard(name)
            else:
                self._excluded.add(name)
        self._fill()

    def guess_conditions(self):
        """Conditions from the run file names (the part that differs, without replicate numbers)."""
        from ionomos.downstream.doctor import suggest_conditions

        runs = {s["sample"]: s.get("run") or s["sample"] for s in self._samples}
        guess = suggest_conditions(list(runs.values()))
        if len(set(guess.values())) < 2:
            self.state.configure(text="Couldn't tell conditions apart from the file names — type them in.",
                                 foreground="#b26a00")
            return
        for name, run in runs.items():
            self.set_condition([name], guess[run])
        self.state.configure(text="Conditions filled in from the file names — check them, then Run analysis.",
                             foreground="#1565c0")

    def reset_samples(self):
        self._cond.clear()
        self._excluded.clear()
        self._fill()

    # -------------------------------------------------------------- choices --

    def _lab(self) -> dict:
        an = dict(self.host.lab_settings() or {})
        an.pop("enabled", None)
        return an

    def choices(self) -> dict:
        an = dict(self._other)
        if self._cond:
            an["sample_conditions"] = dict(self._cond)
        if self._excluded:
            an["exclude_samples"] = sorted(self._excluded)
        if self._roles:  # all of them: an entry for a name that is no longer a condition is the user's, and kept
            an["roles"] = dict(self._roles)
        de = self.var("de_type").get()
        if de == "custom":
            items = [x.strip() for x in self.var("comparisons").get().replace("\n", ";").split(";") if x.strip()]
            if items:
                an["comparisons"] = items
        else:
            an["de_type"] = de
            ctrl = self.var("control").get().strip()
            if de in ("control", "all") and ctrl:
                an["control"] = ctrl
        for k, conv in (("log2fc", float), ("alpha", float)):
            raw = self.var(k).get().strip()
            if raw:
                try:
                    an[k] = conv(raw)
                except ValueError:
                    raise ValueError(f"{k} must be a number, got {raw!r}") from None
        for k in ("imputation", "normalize"):
            val = self.var(k).get().strip()
            if val and val != LAB_DEFAULT:
                an[k] = val
        return an

    def problems(self) -> list[str]:
        """Things that would make the analysis pointless, checked before running."""
        out = []
        used = [s for s in self._samples if s["sample"] not in self._excluded]
        conds = self.conditions()
        if self.target and (self.target["info"].get("kind") == "intensity"):
            if len(conds) < 2:
                out.append("All used samples are in one condition — give them their conditions first "
                           "(Guess from names can help).")
            ctrl = self.var("control").get().strip()
            if self.var("de_type").get() == "control" and ctrl and ctrl not in conds:
                out.append(f"The control '{ctrl}' isn't one of the conditions ({', '.join(conds)}).")
        if self._samples and not used:
            out.append("Every sample is left out.")
        for r in (self._view.rows if self._view is not None else []):
            if r.confirm:
                out.append(f"{r.condition}: {r.confirm} (Roles, on the right).")
        return out

    def save_choices(self, quiet: bool = False) -> bool:
        from ionomos.downstream.analysis import AnalysisError, settings_from
        from ionomos.manifest import OverridesError, load_overrides, save_overrides

        if not self.target:
            if not quiet:
                messagebox.showinfo("Analysis", "Pick an experiment first.", parent=self.frame)
            return False
        dest = self.target["dest"]
        try:
            an = self.choices()
            settings_from(self._lab(), an)
            ov = load_overrides(dest)
        except (ValueError, AnalysisError, OverridesError) as exc:
            messagebox.showerror("Analysis choices", str(exc), parent=self.frame)
            return False
        if ov.analysis == an:
            return True
        ov.analysis = an
        try:
            path = save_overrides(dest, ov, replace_analysis=True)  # the editor shows the whole analysis: block
        except OSError as exc:
            messagebox.showerror("Analysis choices", f"Could not write experiment.yaml:\n{exc}", parent=self.frame)
            return False
        self.state.configure(text=f"saved in {path.name}", foreground="#2e7d32")
        log.info("analysis choices saved in %s", path)
        return True

    # ----------------------------------------------------------------- run --

    def run(self, confirm: bool = True):
        from ionomos import postprocess

        if self.running:
            return
        if not self.target:
            messagebox.showinfo("Analysis", "Pick an experiment first.", parent=self.frame)
            return
        probs = self.problems()
        if probs and confirm and not messagebox.askyesno(
                "Analysis", "\n\n".join(probs) + "\n\nRun anyway?", parent=self.frame):
            return
        if not self.save_choices(quiet=True):
            return
        dest, method, job_id = self.target["dest"], self.target["method"], self.target.get("job_id")
        cfg = self._cfg()
        self.running = True
        self.run_btn.state(["disabled"])
        self.write(f"analysing {dest} …", clear=True)

        def progress(msg):
            self.host.post(lambda: (self.state.configure(text=msg + " …", foreground="#1565c0"), self.write(msg)))

        def go():
            try:
                out, err = postprocess.run_for_folder(dest, cfg, method, progress=progress,
                                                      table=self.target.get("table")), None
                postprocess.record_issues(self.host.log_dir(), dest, out, job_id)
            except Exception as exc:  # noqa: BLE001 - analyze() never raises; belt and braces
                out, err = None, f"{type(exc).__name__}: {exc}"
            self.host.post(lambda: self._done(out, err))

        threading.Thread(target=go, daemon=True).start()

    def _done(self, out, err):
        from ionomos.downstream import doctor

        self.running = False
        self.last_outcome = out
        try:
            self.run_btn.state(["!disabled"])
        except Exception:  # noqa: BLE001 - window closed meanwhile
            return
        if err or out is None:
            self.state.configure(text=f"analysis failed: {err}", foreground="#c62828")
            self.write(f"analysis failed: {err}")
            return
        lines = [f"{c['name']}: {c['up']} up, {c['down']} down of {c['tested']} tested"
                 for c in out.summary.get("comparisons", [])]
        for st in out.summary.get("processing", []):
            if "removed" in st:
                lines.append(f"  {st['step']}: −{st['removed']}")
            elif st["step"] == "imputation":
                lines.append(f"  imputed {st['values']} values ({st['percent']}%) — {st['method']}")
        lines += [f"note: {w}" for w in out.warnings]
        self.write("\n".join(lines) or "done")
        issues = [i.as_dict() for i in out.issues]
        self.show_issues(issues)
        left = doctor.popups(out.issues)
        if self.target:
            self.target["info"]["issues"] = issues
        if left:
            self.state.configure(text=f"done — {len(left)} thing(s) still need a look (above)", foreground="#b26a00")
        else:
            self.state.configure(text="done — the report is open in your browser", foreground="#2e7d32")
        if out.report and out.report.is_file():
            self.host.open_path(str(out.report))
        if self.on_done:
            self.on_done(out)

    def open_report(self):
        if self.target and self.target["info"]["report"].is_file():
            self.host.open_path(str(self.target["info"]["report"]))
        else:
            messagebox.showinfo("Report", "No report yet: press Run analysis.", parent=self.frame)

    def open_results(self):
        if self.target:
            from ionomos import downstream

            d = self.target["dest"] / downstream.RESULTS
            self.host.open_path(str(d if d.is_dir() else self.target["dest"]))

    def choose_folder(self) -> Path | None:
        d = filedialog.askdirectory(title="Experiment or FragPipe output folder", parent=self.frame)
        return Path(d) if d else None

    def choose_table(self) -> Path | None:
        f = filedialog.askopenfilename(
            title="A protein or results table (MaxQuant, Spectronaut, Perseus, limma, Excel, CSV …)", parent=self.frame,
            filetypes=[("Tables", "*.tsv *.csv *.txt *.xlsx *.tab"), ("All files", "*.*")])
        return Path(f) if f else None


def _win() -> bool:
    import os

    return os.name == "nt"
