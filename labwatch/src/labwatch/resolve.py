"""
GUI resolver — a small tkinter window that appears only when intake can't
interpret a folder (unknown user, no method, unparseable file tails, uneven
fractions). The person picks/edits the values; the result is written to
experiment.yaml in the folder so the decision sticks.

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

from labwatch.intake import Draft, DraftFile, Kind
from labwatch.manifest import FileOverride, Overrides
from labwatch.naming import DEFAULT_METHOD_ALIASES, NamingError, parse_raw_name, tokens_of

log = logging.getLogger("labwatch.resolve")


def gui_available() -> tuple[bool, str]:
    """(ok, reason). False when tkinter is missing or there is no display."""
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


def reparse(files: list[DraftFile], method: str) -> list[DraftFile]:
    out = []
    for f in files:
        df = DraftFile(filename=f.filename, experiment=f.experiment, bioreplicate=f.bioreplicate or "1")
        try:
            r = parse_raw_name(f.filename, method)
            df.experiment, df.bioreplicate = r.sample, str(r.rep)
            df.fraction = "" if r.fraction is None else str(r.fraction)
        except NamingError as exc:
            df.error = str(exc)
        out.append(df)
    return out


@dataclass
class Answer:
    user: str
    method: str
    date: str
    allow_uneven: bool
    files: list[DraftFile]
    remember_alias: str = ""


def validate(a: Answer, known_methods: list[str]) -> str:
    """Return an error message, or '' if the answer is usable."""
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
    base = {f.filename: f for f in reparse(a.files, a.method)}
    for f in a.files:
        b = base[f.filename]
        exp, rep, frac = f.experiment.strip(), int(f.bioreplicate), f.fraction.strip()
        if b.error or exp != b.experiment or str(rep) != b.bioreplicate or frac != b.fraction:
            ov.files[f.filename] = FileOverride(
                experiment=exp, bioreplicate=rep, fraction=int(frac) if frac else -1
            )
    return ov


# ----------------------------------------------------------------- tk GUI --


class TkResolver:
    def __init__(
        self,
        root,
        timeout_seconds: float = 0,
        remember: Callable[[str, str], None] | None = None,
    ):
        self.root = root
        self.timeout = timeout_seconds
        self.remember = remember
        self._q: queue.Queue[tuple[Draft, threading.Event, list]] = queue.Queue()
        self._busy = False

    # -- called from the watcher thread
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
        from tkinter import ttk

        win = tk.Toplevel(self.root)
        win.title("labwatch — needs a hand")
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
        ttk.Label(frm, text="⚠ " + d.problem, foreground="#b00020", wraplength=640).grid(
            row=1, column=0, columnspan=4, sticky="w", **pad)

        # ---- header fields
        user_v = tk.StringVar(value=d.user)
        meth_v = tk.StringVar(value=d.method or (d.known_methods[0] if d.known_methods else ""))
        date_v = tk.StringVar(value=d.date)
        uneven_v = tk.BooleanVar(value=d.allow_uneven or d.kind == Kind.LAYOUT and "fractions" in d.problem)
        alias_v = tk.StringVar(value=guess_alias_token(d) if d.kind == Kind.USER else "")
        remember_v = tk.BooleanVar(value=bool(alias_v.get()) and self.remember is not None)

        ttk.Label(frm, text="User").grid(row=2, column=0, sticky="e", **pad)
        user_cb = ttk.Combobox(frm, textvariable=user_v, values=d.known_users, width=24)
        user_cb.grid(row=2, column=1, sticky="w", **pad)
        ttk.Label(frm, text="(type a new name to create a folder)", foreground="#666").grid(
            row=2, column=2, columnspan=2, sticky="w", **pad)

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
        ttk.Label(frm, text="Files — experiment / replicate / fraction  (edit anything that looks wrong)",
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
            for c, h in enumerate(("file", "experiment", "rep", "frac")):
                ttk.Label(grid, text=h, foreground="#666").grid(row=0, column=c, sticky="w", padx=4)
            for i, f in enumerate(files, start=1):
                ev, rv, fv = tk.StringVar(value=f.experiment), tk.StringVar(value=f.bioreplicate), tk.StringVar(value=f.fraction)
                ttk.Label(grid, text=f.filename, foreground="#b00020" if f.error else "").grid(
                    row=i, column=0, sticky="w", padx=4)
                ttk.Entry(grid, textvariable=ev, width=34).grid(row=i, column=1, padx=4, pady=1)
                ttk.Entry(grid, textvariable=rv, width=4).grid(row=i, column=2, padx=4)
                ttk.Entry(grid, textvariable=fv, width=4).grid(row=i, column=3, padx=4)
                rows.append((f, ev, rv, fv))

        fill_grid(d.files)

        def on_method_change(*_):
            fill_grid(reparse(d.files, meth_v.get()))

        meth_cb.bind("<<ComboboxSelected>>", on_method_change)
        ttk.Button(frm, text="Re-read from file names", command=on_method_change).grid(
            row=6, column=3, sticky="e", **pad)

        # ---- buttons + error line
        err_v = tk.StringVar()
        ttk.Label(frm, textvariable=err_v, foreground="#b00020", wraplength=640).grid(
            row=8, column=0, columnspan=4, sticky="w", **pad)
        btns = ttk.Frame(frm)
        btns.grid(row=9, column=0, columnspan=4, sticky="e", **pad)

        def answer() -> Answer:
            files = [DraftFile(filename=f.filename, experiment=ev.get(), bioreplicate=rv.get(), fraction=fv.get())
                     for f, ev, rv, fv in rows]
            return Answer(user=user_v.get(), method=meth_v.get(), date=date_v.get(),
                          allow_uneven=uneven_v.get(), files=files,
                          remember_alias=alias_v.get().strip() if remember_v.get() else "")

        def accept(*_):
            a = answer()
            msg = validate(a, d.known_methods)
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

        ttk.Button(btns, text="Skip (leave in inbox)", command=skip).pack(side="left", padx=4)
        ttk.Button(btns, text="Accept & queue  ⏎", command=accept).pack(side="left", padx=4)
        win.bind("<Return>", accept)
        win.bind("<Escape>", skip)
        win.protocol("WM_DELETE_WINDOW", skip)

        if self.timeout:
            win.after(int(self.timeout * 1000), skip)

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
