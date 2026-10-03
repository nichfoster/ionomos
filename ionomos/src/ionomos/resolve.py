"""
GUI resolver — a small tkinter window that shows how a drop was read: when intake
can't interpret a folder (unknown user, no method, unparseable file tails, uneven
fractions), and — as a review before filing — for every drop that did parse
(gui.review_drops). The person sees user, method, date, each file's condition /
replicate / fraction, which condition is the control, and each condition's role
with the comparisons that follow (role_view -> downstream/roles.preview, D65),
and edits anything wrong; the result is written to experiment.yaml in the folder
so it sticks.

Threading: tkinter must run on the main thread. `TkResolver.resolve()` may be
called from the watcher thread; it posts the Draft to a queue and blocks on an
Event. The main thread runs `root.mainloop()` and `pump()` (via root.after)
opens dialogs one at a time.

    root = tk.Tk(); root.withdraw()
    resolver = TkResolver(root, timeout_seconds=0, remember=cfg_remember_alias)
    threading.Thread(target=watcher.run_forever, daemon=True).start()
    resolver.start(); root.mainloop()

Design goals: instant to open, no dependencies, keyboard friendly
(Enter = accept, Esc = skip), every field pre-filled with the best guess,
comboboxes editable so a new user can be typed.
"""
from __future__ import annotations

import logging
import queue
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ionomos import tkutil
from ionomos.intake import Draft, DraftFile, Kind
from ionomos.manifest import FileOverride, Overrides
from ionomos.naming import DEFAULT_METHOD_ALIASES, NamingError, parse_raw_name, tokens_of

log = logging.getLogger("ionomos.resolve")


def gui_available_isolated(timeout: float = 15) -> tuple[bool, str]:
    """gui_available() run in a child process, so a wedged display can't hang the caller (check/diagnose)."""
    import os
    import subprocess

    if os.environ.get("IONOMOS_NO_GUI"):
        return False, "disabled by IONOMOS_NO_GUI"
    from ionomos.service import _creationflags, ionomos_command

    try:
        r = subprocess.run([*ionomos_command(console=True), "probe-gui"], capture_output=True, text=True,
                           timeout=timeout, creationflags=_creationflags(), encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return False, f"the display did not answer within {timeout:.0f}s (window system busy or hung)"
    except OSError as exc:
        return False, f"could not probe: {exc}"
    out = (r.stdout or "").strip().splitlines()
    if r.returncode == 0 and out and out[-1] == "ok":
        return True, ""
    return False, (out[-1] if out else (r.stderr or "probe failed").strip()[-200:])


def gui_available() -> tuple[bool, str]:
    """(ok, reason). False when tkinter is missing, there is no display, or IONOMOS_NO_GUI is set."""
    import os

    if os.environ.get("IONOMOS_NO_GUI"):
        return False, "disabled by IONOMOS_NO_GUI"
    try:
        import tkinter as tk
    except ImportError as exc:
        return False, f"tkinter not installed ({exc}); on Windows re-run the Python installer and tick tcl/tk"
    try:
        r = tk.Tk()
        r.withdraw()
        r.destroy()
    except tk.TclError as exc:
        return False, f"no display: {exc}"
    return True, ""


# --------------------------------------------------------------- pure logic --


def guess_alias_token(d: Draft) -> str:
    """The token in the folder name most likely to be the person's initials."""
    kw = {a.lower() for als in DEFAULT_METHOD_ALIASES.values() for a in als}
    for t in tokens_of(d.folder):
        tl = t.lower()
        if tl.isdigit() or tl in kw or any(k in tl for k in kw) or len(t) < 2:
            continue
        m = re.match(r"^([A-Za-z]{2,5})\d*$", t)
        if m:
            return m.group(1)
    return ""


def reparse(files: list[DraftFile], method: str, codes: dict[str, str] | None = None,
            rules: dict | None = None) -> list[DraftFile]:
    """rules: the config's file rules (Draft.file_rules); None = the built-in ones."""
    out = []
    for f in files:
        df = DraftFile(filename=f.filename, experiment=f.experiment, bioreplicate=f.bioreplicate or "1")
        try:
            r = parse_raw_name(f.filename, method, codes, rules)
            df.experiment, df.bioreplicate = r.sample, str(r.rep)
            df.fraction = "" if r.fraction is None else str(r.fraction)
        except NamingError as exc:
            df.error = str(exc)
        out.append(df)
    return out


def has_control(method: str) -> bool:
    """isoDTB is a ratio vs 0 per sample; TMT conditions live in the channel annotation, not the file names.
    method here and in summarize() is the method's kind (Draft.kind_of), so a `like: TMT` method is TMT."""
    return method not in ("isoDTB", "TMT")


def conditions_of(files: list[DraftFile]) -> list[str]:
    out: list[str] = []
    for f in files:
        e = f.experiment.strip()
        if e and e not in out:
            out.append(e)
    return out


def guess_control(conditions: list[str], keywords: list[str], roles: dict[str, str] | None = None) -> str:
    """The analysis's own rule (downstream.analysis.find_control): a condition analysis.roles calls the control;
    else one with a control keyword as a token, e.g. KC_DIA_DMSO, that the roles don't call something else;
    otherwise the alphabetically first, which the analysis also falls back to."""
    given = {str(k).lower(): str(v) for k, v in (roles or {}).items()}
    for c in conditions:
        if given.get(c.lower()) == "control":
            return c
    for kw in keywords:
        for c in conditions:
            if c.lower() in given:
                continue
            if kw.lower() in re.split(r"[_\-\s.]+", c.lower()) or c.lower() == kw.lower():
                return c
    return sorted(conditions)[0] if conditions else ""


def replicates(files: list[DraftFile]) -> dict[str, int]:
    """{condition: number of replicates}, in the files' order (fractions of one replicate count once)."""
    reps: dict[str, set] = {}
    for f in files:
        c = f.experiment.strip()
        if c:
            reps.setdefault(c, set()).add(f.bioreplicate.strip())
    return {c: len(r) for c, r in reps.items()}


def given_roles(d: Draft) -> dict[str, str]:
    """The experiment's analysis.roles as experiment.yaml has them (normalised; {} when unreadable)."""
    from ionomos.downstream import roles

    try:
        return roles.normalise((d.exp_analysis or {}).get("roles") or {})
    except roles.RoleError:
        return {}


def _lab_roles(d: Draft) -> dict[str, str]:
    from ionomos.downstream import roles

    try:
        return roles.normalise((d.lab_analysis or {}).get("roles") or {})
    except roles.RoleError:
        return {}


def pinned_control(control: str, files: list[DraftFile], d: Draft, chosen: dict[str, str] | None = None) -> str:
    """The control to_overrides writes to analysis.control: the picked one when it isn't what the analysis would
    pick anyway (or experiment.yaml already names one); "" otherwise."""
    if not control:
        return ""
    automatic = guess_control(conditions_of(files), d.control_keywords, {**_lab_roles(d), **(chosen or {})})
    return control if control != automatic or d.control else ""


def role_view(files: list[DraftFile], method: str, control: str, d: Draft, chosen: dict[str, str] | None = None):
    """roles.preview for the review window: each condition's role with its replicates, the comparisons the
    analysis will run, uneven groups. method: the method's kind. chosen: the window's analysis.roles. None for
    isoDTB and TMT, whose conditions are not in the file names."""
    from dataclasses import replace

    from ionomos.downstream import roles
    from ionomos.downstream.analysis import AnalysisError, Settings, settings_from

    if not has_control(method):
        return None
    exp = {k: v for k, v in (d.exp_analysis or {}).items()
           if k not in ("roles", "control", "sample_conditions", "exclude_samples")}
    pin = pinned_control(control, files, d, chosen)
    try:
        s = settings_from(d.lab_analysis, exp, {"control": pin} if pin else None)
    except AnalysisError:
        s = replace(Settings(), control_keywords=tuple(d.control_keywords) or Settings().control_keywords,
                    control=pin or None)
    return roles.preview(replicates(files), s, chosen, "intensity", method)


def _ranges(nums: list[int]) -> str:
    nums = sorted(set(nums))
    parts, i = [], 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        parts.append(str(nums[i]) if i == j else f"{nums[i]}–{nums[j]}")
        i = j + 1
    return ", ".join(parts)


def summarize(files: list[DraftFile], method: str, control: str, roles: dict[str, str] | None = None) -> list[str]:
    """One line per condition: its role, replicates and fractions — what the search and analysis will assume.
    roles: {condition: role in words} (role_view's rows); without them a condition is CONTROL or treated."""
    lines = []
    for c in sorted(conditions_of(files), key=lambda x: x != control):  # the control first
        reps: dict[int, list[int]] = {}
        for f in files:
            if f.experiment.strip() != c:
                continue
            r = int(f.bioreplicate) if f.bioreplicate.strip().isdigit() else 0
            reps.setdefault(r, [])
            if f.fraction.strip().isdigit():
                reps[r].append(int(f.fraction))
        if method == "isoDTB":
            role = "sample (heavy/light ratio vs 0)"
        elif method == "TMT":
            role = "plex (conditions come from the TMT channel annotation)"
        elif c == control:
            role = "CONTROL"
        elif roles and c in roles:
            role = roles[c]
        else:
            role = "treated" if control else "?"
        n = len(reps)
        text = f"{c} — {role} · {n} replicate{'s' if n != 1 else ''} ({_ranges(list(reps))})"
        fr = {r: tuple(sorted(v)) for r, v in reps.items() if v}
        if fr:
            sets = set(fr.values())
            text += (f" · fractions {_ranges(list(next(iter(sets))))}" if len(sets) == 1
                     else " · ⚠ replicates have different fractions")
        if n == 1 and has_control(method):
            text += " · ⚠ one replicate: statistics will be low confidence"
        lines.append(text)
    if has_control(method) and len(lines) == 1:
        lines.append("⚠ only one condition: nothing to compare — check the condition names above")
    return lines


@dataclass
class Answer:
    user: str
    method: str
    date: str
    allow_uneven: bool
    files: list[DraftFile]
    remember_alias: str = ""
    control: str = ""
    roles: dict[str, str] | None = None  # the window's analysis.roles; None = the window has no role list


def validate(a: Answer, known_methods: list[str], kinds: dict[str, str] | None = None) -> str:
    """Return an error message, or '' if the answer is usable. kinds: Draft.kinds."""
    if not a.user.strip():
        return "User is required"
    if not re.match(r"^[A-Za-z0-9._-]+$", a.user.strip()):
        return "User may only contain letters, digits, . _ -"
    if a.method not in known_methods:
        return f"Method must be one of {', '.join(known_methods)}"
    if a.date.strip():
        try:
            date.fromisoformat(a.date.strip())
        except ValueError:
            return "Date must be YYYY-MM-DD (or blank)"
    if not a.files:
        return "At least one raw file is required"
    if a.control and has_control((kinds or {}).get(a.method, a.method)) and a.control not in conditions_of(a.files):
        return f"Control {a.control!r} is not one of the conditions — pick it again"
    seen = set()
    for f in a.files:
        if not f.experiment.strip():
            return f"{f.filename}: experiment is required"
        if not f.bioreplicate.strip().isdigit() or int(f.bioreplicate) < 1:
            return f"{f.filename}: replicate must be a whole number ≥ 1"
        if f.fraction.strip() and (not f.fraction.strip().isdigit() or int(f.fraction) < 1):
            return f"{f.filename}: fraction must be a whole number or blank"
        key = (f.experiment.strip(), int(f.bioreplicate), f.fraction.strip())
        if key in seen:
            return f"{f.filename}: duplicates another file's experiment/replicate/fraction"
        seen.add(key)
    exps = {f.experiment.strip() for f in a.files}
    fr = {e: {} for e in exps}
    for f in a.files:
        fr[f.experiment.strip()].setdefault(int(f.bioreplicate), set())
        if f.fraction.strip():
            fr[f.experiment.strip()][int(f.bioreplicate)].add(int(f.fraction))
    if not a.allow_uneven:
        for e, reps in fr.items():
            sets = {frozenset(s) for s in reps.values()}
            if len(sets) > 1:
                return f"{e}: replicates have different fractions — fix, or tick 'accept uneven fractions'"
    return ""


def to_overrides(a: Answer, d: Draft) -> Overrides:
    """Only what differs from a clean re-parse with the chosen method is written per file."""
    ov = Overrides(user=a.user.strip(), method=a.method, allow_uneven_fractions=a.allow_uneven)
    if a.date.strip():
        ov.date = date.fromisoformat(a.date.strip())
    base = {f.filename: f for f in reparse(a.files, a.method, d.condition_codes or None, d.file_rules or None)}
    for f in a.files:
        b = base[f.filename]
        exp, rep, frac = f.experiment.strip(), int(f.bioreplicate), f.fraction.strip()
        if b.error or exp != b.experiment or str(rep) != b.bioreplicate or frac != b.fraction:
            ov.files[f.filename] = FileOverride(
                experiment=exp, bioreplicate=rep, fraction=int(frac) if frac else -1
            )
    if has_control(d.kind_of(a.method)):
        chosen = a.roles if a.roles is not None else given_roles(d)
        pin = pinned_control(a.control, a.files, d, chosen)  # only when it isn't what the analysis picks anyway
        if pin:
            ov.analysis["control"] = pin
        if a.roles is not None:
            from ionomos.downstream.roles import keep_roles

            conds = conditions_of(a.files)
            given = given_roles(d)
            mine = keep_roles(a.roles, conds)
            if mine != keep_roles(given, conds):
                # the whole mapping (save_overrides replaces analysis.roles): entries for names that aren't
                # conditions here were not shown, so they are kept as written; {} clears the rest
                low = {c.lower() for c in conds}
                ov.analysis["roles"] = {**{c: r for c, r in given.items() if c.lower() not in low}, **mine}
    return ov


# ----------------------------------------------------------------- tk GUI --


class TkResolver:
    def __init__(
        self,
        root,
        timeout_seconds: float = 0,
        remember: Callable[[str, str], None] | None = None,
        refresh: Callable[[Draft], Draft] | None = None,
    ):
        """refresh(d): a fresh reading of the same folder with the current config — the open window uses it to
        pick up a user folder or alias added in the app meanwhile, without closing."""
        self.root = root
        self.timeout = timeout_seconds
        self.remember = remember
        self.refresh = refresh
        self._q: queue.Queue[tuple[Draft, threading.Event, list]] = queue.Queue()
        self._busy = False

    # -- called from the watcher thread
    def review(self, d: Draft) -> Overrides | None:
        """The "check before filing" window for a drop that parsed cleanly (d.review is set)."""
        return self.resolve(d)

    def resolve(self, d: Draft) -> Overrides | None:
        if threading.current_thread() is threading.main_thread():
            return self._dialog(d)
        done = threading.Event()
        box: list = []
        self._q.put((d, done, box))
        done.wait()
        return box[0] if box else None

    # -- main thread
    def start(self, every_ms: int = 200) -> None:
        self.root.after(every_ms, self._pump, every_ms)

    def _pump(self, every_ms: int) -> None:
        if not self._busy:
            try:
                d, done, box = self._q.get_nowait()
            except queue.Empty:
                d = None
            if d is not None:
                self._busy = True
                try:
                    box.append(self._dialog(d))
                except Exception:
                    log.exception("resolver dialog crashed; treating as skip")
                finally:
                    self._busy = False
                    done.set()
        self.root.after(every_ms, self._pump, every_ms)

    def _dialog(self, d: Draft) -> Overrides | None:
        import tkinter as tk
        from tkinter import messagebox, ttk

        source = Path(d.source) if d.source else None
        if source is not None and not source.is_dir():
            return None
        win = tk.Toplevel(self.root)
        win.title("ionomos — check before filing" if d.review else "ionomos — needs a hand")
        win.attributes("-topmost", True)
        win.resizable(True, True)
        result: list[Overrides | None] = [None]

        pad = {"padx": 6, "pady": 3}
        frm = ttk.Frame(win, padding=10)
        frm.grid(sticky="nsew")
        win.columnconfigure(0, weight=1)
        win.rowconfigure(0, weight=1)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text=d.folder, font=("", 11, "bold")).grid(row=0, column=0, columnspan=4, sticky="w", **pad)
        if d.review:
            ttk.Label(frm, text="Nothing is wrong — this is how Ionomos read the drop. Check the user, the conditions, "
                                "replicates and the control below; fix anything wrong, then Accept.",
                      foreground="#1565c0", wraplength=640).grid(row=1, column=0, columnspan=4, sticky="w", **pad)
        else:
            ttk.Label(frm, text="⚠ " + d.problem, foreground="#b00020", wraplength=640).grid(
                row=1, column=0, columnspan=4, sticky="w", **pad)

        # ---- header fields
        user_v = tkutil.StringVar(value=d.user)
        meth_v = tkutil.StringVar(value=d.method or (d.known_methods[0] if d.known_methods else ""))
        date_v = tkutil.StringVar(value=d.date)
        uneven_v = tkutil.BooleanVar(value=d.allow_uneven or d.kind == Kind.LAYOUT and "fractions" in d.problem)
        alias_v = tkutil.StringVar(value=guess_alias_token(d) if (d.kind == Kind.USER or not d.user) else "")
        remember_v = tkutil.BooleanVar(value=bool(alias_v.get()) and self.remember is not None)

        ttk.Label(frm, text="User").grid(row=2, column=0, sticky="e", **pad)
        user_cb = ttk.Combobox(frm, textvariable=user_v, values=d.known_users, width=24)
        user_cb.grid(row=2, column=1, sticky="w", **pad)
        ttk.Label(frm, text="(type a new name to create a folder; users added in the app appear here)",
                  foreground="#666").grid(row=2, column=2, columnspan=2, sticky="w", **pad)

        ttk.Label(frm, text="Method").grid(row=3, column=0, sticky="e", **pad)
        meth_cb = ttk.Combobox(frm, textvariable=meth_v, values=d.known_methods, state="readonly", width=12)
        meth_cb.grid(row=3, column=1, sticky="w", **pad)
        ttk.Label(frm, text="Date").grid(row=3, column=2, sticky="e", **pad)
        ttk.Entry(frm, textvariable=date_v, width=12).grid(row=3, column=3, sticky="w", **pad)

        if self.remember is not None:
            rf = ttk.Frame(frm)
            rf.grid(row=4, column=0, columnspan=4, sticky="w", **pad)
            ttk.Checkbutton(rf, text="Remember that", variable=remember_v).pack(side="left")
            ttk.Entry(rf, textvariable=alias_v, width=10).pack(side="left", padx=4)
            ttk.Label(rf, text="means this user (future drops won't ask)").pack(side="left")

        ttk.Checkbutton(frm, text="Accept uneven fractions between replicates", variable=uneven_v).grid(
            row=5, column=0, columnspan=4, sticky="w", **pad)

        # ---- file grid (scrollable)
        ttk.Label(frm, text="Files — condition / replicate / fraction  (edit anything that looks wrong)",
                  font=("", 9, "bold")).grid(row=6, column=0, columnspan=3, sticky="w", **pad)
        rows: list[tuple[DraftFile, tk.StringVar, tk.StringVar, tk.StringVar]] = []

        canvas = tk.Canvas(frm, height=min(28 * max(len(d.files), 1) + 30, 360), highlightthickness=0)
        vsb = ttk.Scrollbar(frm, orient="vertical", command=canvas.yview)
        grid = ttk.Frame(canvas)
        grid.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=grid, anchor="nw")
        canvas.configure(yscrollcommand=vsb.set)
        canvas.grid(row=7, column=0, columnspan=4, sticky="nsew", **pad)
        vsb.grid(row=7, column=4, sticky="ns")
        frm.rowconfigure(7, weight=1)

        def fill_grid(files: list[DraftFile]) -> None:
            for w in grid.winfo_children():
                w.destroy()
            rows.clear()
            for c, h in enumerate(("file", "condition / sample", "rep", "frac")):
                ttk.Label(grid, text=h, foreground="#666").grid(row=0, column=c, sticky="w", padx=4)
            for i, f in enumerate(files, start=1):
                ev, rv, fv = tkutil.StringVar(value=f.experiment), tkutil.StringVar(value=f.bioreplicate), tkutil.StringVar(value=f.fraction)
                for var in (ev, rv, fv):
                    var.trace_add("write", lambda *_: update_summary())
                ttk.Label(grid, text=f.filename, foreground="#b00020" if f.error else "").grid(
                    row=i, column=0, sticky="w", padx=4)
                ttk.Entry(grid, textvariable=ev, width=34).grid(row=i, column=1, padx=4, pady=1)
                ttk.Entry(grid, textvariable=rv, width=4).grid(row=i, column=2, padx=4)
                ttk.Entry(grid, textvariable=fv, width=4).grid(row=i, column=3, padx=4)
                rows.append((f, ev, rv, fv))
                if source is not None:
                    ttk.Button(grid, text="Delete", command=lambda name=f.filename: delete_file(name)).grid(
                        row=i, column=4, padx=4)

        def delete_file(filename):
            from ionomos.inbox import remove
            from ionomos.intake import raw_paths

            try:
                target = next((p for p in raw_paths(source) if p.name == filename), None)
                if target is None:
                    raise ValueError(f"{filename} is no longer in {source.name}")
                remove(source.parent, target)
                refresh_files()
            except (OSError, ValueError) as exc:
                messagebox.showerror("Could not remove file", str(exc), parent=win)

        def refresh_files():
            from ionomos.intake import _find_raws

            if source is None:
                return
            if not source.is_dir():
                skip()
                return
            _, current, _ = _find_raws(source)
            saved = {f.filename: DraftFile(f.filename, ev.get(), rv.get(), fv.get()) for f, ev, rv, fv in rows}
            if set(current) != set(saved):
                d.files = [saved.get(name, reparse([DraftFile(name)], meth_v.get(), d.condition_codes or None,
                                                   d.file_rules or None)[0])
                           for name in current]
                fill_grid(d.files)
                update_summary()
            if not current:
                skip()

        # ---- what the search and the analysis will assume: conditions, roles, replicates, control
        ctl_v = tkutil.StringVar(value="")
        sumf = ttk.LabelFrame(frm, text="What Ionomos will assume", padding=6)
        sumf.grid(row=11, column=0, columnspan=4, sticky="ew", **pad)
        ctlrow = ttk.Frame(sumf)
        ctlrow.pack(anchor="w", fill="x")
        ctl_lbl = ttk.Label(ctlrow, text="Control (the 'vs' side of every volcano):")
        ctl_cb = ttk.Combobox(ctlrow, textvariable=ctl_v, state="readonly", width=34)
        ctl_note = ttk.Label(ctlrow, foreground="#666")
        summary_v = tkutil.StringVar()
        ttk.Label(sumf, textvariable=summary_v, justify="left", font=("", 9)).pack(anchor="w", pady=(4, 0))
        # each condition's role (D65): a list to change it, a mark on a guess to confirm, the comparisons in words.
        # The rows scroll beyond ~7 conditions so the buttons stay on a small screen.
        rolesbox = ttk.Frame(sumf)
        rolesbox.pack(anchor="w", fill="x", pady=(4, 0))
        rolescv = tk.Canvas(rolesbox, height=1, width=1, highlightthickness=0)
        rolescv.grid(row=0, column=0, sticky="nw")
        rolesvsb = ttk.Scrollbar(rolesbox, orient="vertical", command=rolescv.yview)
        rolescv.configure(yscrollcommand=rolesvsb.set)
        rolesf = ttk.Frame(rolescv)
        rolescv.create_window((0, 0), window=rolesf, anchor="nw")

        def fit_roles(_e=None) -> None:
            h = rolesf.winfo_reqheight()
            rolescv.configure(scrollregion=rolescv.bbox("all"), width=rolesf.winfo_reqwidth(), height=min(h, 200))
            if h > 200:
                rolesvsb.grid(row=0, column=1, sticky="ns")
            else:
                rolesvsb.grid_remove()

        rolesf.bind("<Configure>", fit_roles)
        plan_v = tkutil.StringVar()
        ttk.Label(sumf, textvariable=plan_v, justify="left", foreground="#333", wraplength=640).pack(anchor="w")
        codes_note = ttk.Label(sumf, foreground="#666", wraplength=640, justify="left")
        codes_note.pack(anchor="w")
        guessed_from: list[str] = [""]  # the automatic control for the current conditions
        chosen: list[dict[str, str]] = [given_roles(d)]  # the window's analysis.roles
        role_rows: dict[str, list] = {}  # condition -> [combobox, samples label, note, confirm button, {shown: value}]
        shown: list = [None]             # the conditions the role rows were built for
        last_view: list = [None]

        def chose(cond: str) -> None:
            from ionomos.downstream import roles

            value = role_rows[cond][4].get(role_rows[cond][0].get(), "")
            try:
                chosen[0] = roles.set_role(chosen[0], cond, value)
            except roles.RoleError as exc:
                err_v.set(str(exc))
                return
            if value == "control":
                ctl_v.set(cond)
            win.after_idle(update_summary)

        def confirm(cond: str) -> None:
            from ionomos.downstream import roles

            row = next((r for r in (last_view[0].rows if last_view[0] else []) if r.condition == cond), None)
            if row is not None and row.confirmable:
                chosen[0] = roles.set_role(chosen[0], cond, row.role)
                win.after_idle(update_summary)

        def fill_roles(view) -> None:
            from ionomos.downstream import roles

            conds = [r.condition for r in view.rows] if view is not None else []
            if conds != shown[0]:
                for w in rolesf.winfo_children():
                    w.destroy()
                role_rows.clear()
                if conds:
                    ttk.Label(rolesf, text="Roles (change one if a name misleads; saved in experiment.yaml):",
                              foreground="#666").grid(row=0, column=0, columnspan=5, sticky="w")
                for i, r in enumerate(view.rows if view is not None else [], start=1):
                    ttk.Label(rolesf, text=r.condition).grid(row=i, column=0, sticky="w", padx=4)
                    n_lbl = ttk.Label(rolesf)
                    n_lbl.grid(row=i, column=1, sticky="w", padx=4)
                    cb = ttk.Combobox(rolesf, state="readonly", width=30)
                    cb.grid(row=i, column=2, sticky="w", padx=4, pady=1)
                    cb.bind("<<ComboboxSelected>>", lambda _e, c=r.condition: chose(c))
                    note = ttk.Label(rolesf, wraplength=300, justify="left")
                    note.grid(row=i, column=3, sticky="w", padx=4)
                    btn = ttk.Button(rolesf, text="Confirm", command=lambda c=r.condition: confirm(c))
                    btn.grid(row=i, column=4, sticky="w", padx=4)
                    role_rows[r.condition] = [cb, n_lbl, note, btn, {}]
                shown[0] = conds
            for r in view.rows if view is not None else []:
                cb, n_lbl, note, btn, values = role_rows[r.condition]
                pairs = roles.role_choices(view, r.condition)
                values.clear()
                values.update(pairs)
                cb.configure(values=[s for s, _v in pairs])
                cb.set(next((s for s, v in pairs if r.set_here and v == r.role), pairs[0][0]))
                n_lbl.configure(text=f"{r.n} rep{'s' if r.n != 1 else ''}")
                note.configure(text=f"? {r.confirm}" if r.confirm else f"from {r.source}",
                               foreground="#b26a00" if r.confirm else "#666")
                if r.confirmable:
                    btn.grid()
                else:
                    btn.grid_remove()

        def current_files() -> list[DraftFile]:
            return [DraftFile(filename=f.filename, experiment=ev.get(), bioreplicate=rv.get(), fraction=fv.get())
                    for f, ev, rv, fv in rows]

        def update_summary(*_):
            try:
                files, method = current_files(), d.kind_of(meth_v.get())
                conds = conditions_of(files)
                for w in (ctl_lbl, ctl_cb, ctl_note):
                    w.pack_forget()
                if has_control(method):
                    auto = guess_control(conds, d.control_keywords, {**_lab_roles(d), **chosen[0]})
                    keep = ctl_v.get() in conds and ctl_v.get() != guessed_from[0]  # a person's pick survives edits
                    if not keep:
                        ctl_v.set(d.control if d.control in conds else auto)
                    guessed_from[0] = auto
                    ctl_cb.configure(values=conds)
                    ctl_lbl.pack(side="left")
                    ctl_cb.pack(side="left", padx=4)
                    found = any(kw.lower() in re.split(r"[_\-\s.]+", c.lower()) for c in conds
                                for kw in d.control_keywords)
                    ctl_note.configure(text="" if found or not conds else
                                       "no DMSO / vehicle / control name found — guessed; please check")
                    ctl_note.pack(side="left", padx=6)
                else:
                    ctl_v.set("")
                    ctl_note.configure(text="isoDTB: every sample is its own heavy/light ratio vs 0 — no control"
                                       if method == "isoDTB" else "TMT: conditions come from the channel annotation "
                                       "(their roles can be set on the Analysis tab after the search)")
                    ctl_note.pack(side="left")
                view = role_view(files, method, ctl_v.get(), d, chosen[0])
                last_view[0] = view
                fill_roles(view)
                plan_v.set("\n".join(view.lines()) if view is not None else "")
                labels = {r.condition: r.label for r in view.rows} if view is not None else None
                summary_v.set("\n".join("• " + line for line in summarize(files, method, ctl_v.get(), labels))
                              or "—")
                codes = d.condition_codes or {}
                codes_note.configure(text=("Short codes in DIA file names: " + ", ".join(
                    f"{k}1 = {v} rep 1" for k, v in codes.items()) + " (config: naming.condition_codes)")
                    if method == "DIA" and codes else "")
            except Exception:  # noqa: BLE001 - a summary glitch must never block the answer
                log.exception("review summary failed")

        ctl_cb.bind("<<ComboboxSelected>>", update_summary)
        fill_grid(d.files)
        update_summary()

        def on_method_change(*_):
            fill_grid(reparse(d.files, meth_v.get(), d.condition_codes or None, d.file_rules or None))
            update_summary()

        meth_cb.bind("<<ComboboxSelected>>", on_method_change)
        ttk.Button(frm, text="Re-read from file names", command=on_method_change).grid(
            row=6, column=3, sticky="e", **pad)

        # ---- buttons + error line
        err_v = tkutil.StringVar()
        ttk.Label(frm, textvariable=err_v, foreground="#b00020", wraplength=640, name="error").grid(
            row=12, column=0, columnspan=4, sticky="w", **pad)
        btns = ttk.Frame(frm)
        btns.grid(row=13, column=0, columnspan=4, sticky="e", **pad)

        def answer() -> Answer:
            return Answer(user=user_v.get(), method=meth_v.get(), date=date_v.get(),
                          allow_uneven=uneven_v.get(), files=current_files(),
                          remember_alias=alias_v.get().strip() if remember_v.get() else "", control=ctl_v.get(),
                          roles=dict(chosen[0]) if has_control(d.kind_of(meth_v.get())) else None)

        def accept(*_):
            try:
                refresh_files()
            except (OSError, ValueError) as exc:
                # IntakeError (a ValueError), e.g. a mixed raw layout (#14): say why on
                # the error line and keep the window up instead of a Tk traceback.
                err_v.set(str(exc))
                return
            if not win.winfo_exists():
                return
            a = answer()
            msg = validate(a, d.known_methods, d.kinds)
            if msg:
                err_v.set(msg)
                return
            result[0] = to_overrides(a, d)
            if a.remember_alias and self.remember is not None:
                try:
                    self.remember(a.user.strip(), a.remember_alias)
                except Exception:
                    log.exception("could not save alias")
            win.destroy()

        def skip(*_):
            result[0] = None
            win.destroy()

        def delete_folder():
            from ionomos.inbox import remove

            try:
                remove(source.parent, source)
            except (OSError, ValueError) as exc:
                messagebox.showerror("Could not remove folder", str(exc), parent=win)
                return
            skip()

        if source is not None:
            ttk.Button(btns, text="Delete from inbox", command=delete_folder).pack(side="left", padx=4)
            ttk.Label(frm, text="Deleted items are recoverable in inbox/.removed. Changes restart intake.").grid(
                row=14, column=0, columnspan=4, sticky="w")

            def poll():
                if win.winfo_exists():
                    try:
                        refresh_files()
                    except (OSError, ValueError) as exc:
                        # Same shape as accept: show the rejection, keep the dialog up.
                        err_v.set(str(exc))
                    if win.winfo_exists():
                        win.after(500, poll)

            win.after(500, poll)

        def live():
            """Users / aliases added in the app while this window is open: offer them, fill a blank user."""
            if not win.winfo_exists():
                return
            try:
                fresh = self.refresh(d)
                user_cb.configure(values=fresh.known_users)
                if not user_v.get().strip() and fresh.user:
                    user_v.set(fresh.user)
                    err_v.set(f"User recognised as {fresh.user} (added meanwhile) — check and Accept")
            except Exception:  # noqa: BLE001 - a failed refresh just leaves the window as it was
                log.debug("resolver refresh failed", exc_info=True)
            if win.winfo_exists():
                win.after(2000, live)

        if self.refresh is not None:
            win.after(2000, live)

        ttk.Button(btns, text="Not now (leave in inbox)" if d.review else "Skip (leave in inbox)",
                   command=skip).pack(side="left", padx=4)
        ttk.Button(btns, text="Accept & queue  ⏎", command=accept).pack(side="left", padx=4)
        win.bind("<Return>", accept)
        win.bind("<Escape>", skip)
        win.protocol("WM_DELETE_WINDOW", skip)

        def timed_out():
            # nobody answered: a review files the drop as read (it parsed); a problem stays in the inbox
            if d.review and win.winfo_exists():
                accept()
            if win.winfo_exists():
                skip()

        if self.timeout:
            win.after(int(self.timeout * 1000), timed_out)

        win.update_idletasks()
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        win.geometry(f"+{max((sw - w) // 2, 0)}+{max((sh - h) // 3, 0)}")
        win.deiconify()
        win.lift()
        win.focus_force()
        (user_cb if not d.user else meth_cb).focus_set()
        try:
            win.bell()
        except tk.TclError:
            pass
        self.root.wait_window(win)
        return result[0]
