"""Resolver: pure logic always; the real tkinter dialog only when a display exists."""
import queue
import threading

import pytest

from ionomos.intake import Draft, DraftFile, IntakeError, Kind, draft, plan
from ionomos.manifest import FileOverride, Overrides
from ionomos.resolve import (
    Answer,
    guess_alias_token,
    gui_available,
    reparse,
    to_overrides,
    validate,
)
from tests.conftest import iso_raws, make_drop, make_tk_root


def _draft(folder="XYZ99_isoDTB_run", files=None, kind=Kind.USER, method="isoDTB", user=""):
    files = files or [DraftFile("S_1_1.raw", "S", "1", "1"), DraftFile("S_1_2.raw", "S", "1", "2")]
    return Draft(folder=folder, problem="p", kind=kind, user=user, method=method, date="",
                 known_users=["EJQ", "Isaac"], known_methods=["isoDTB", "TMT", "DIA"], files=files)


def test_guess_alias_token():
    assert guess_alias_token(_draft("XYZ99_isoDTB_run")) == "XYZ"
    assert guess_alias_token(_draft("20260902-isoDTB_IJD05")) == "IJD"
    assert guess_alias_token(_draft("THB10ISODTB")) == ""       # method glued in; can't tell
    assert guess_alias_token(_draft("isoDTB_20260902")) == ""


def test_reparse_switches_method():
    files = [DraftFile("DMSO_1.raw"), DraftFile("Drug_2.raw")]
    out = reparse(files, "DIA")
    assert [(f.experiment, f.bioreplicate, f.fraction) for f in out] == [("DMSO", "1", ""), ("Drug", "2", "")]
    out = reparse([DraftFile("Sample.raw")], "isoDTB")
    assert out[0].error


@pytest.mark.parametrize(
    "patch, msg",
    [
        ({"user": ""}, "User is required"),
        ({"user": "a b"}, "letters, digits"),
        ({"method": "XXX"}, "Method must be"),
        ({"date": "2/9/26"}, "YYYY-MM-DD"),
        ({"files": [DraftFile("a.raw", "", "1", "")]}, "experiment is required"),
        ({"files": [DraftFile("a.raw", "A", "0", "")]}, "replicate must be"),
        ({"files": [DraftFile("a.raw", "A", "1", "x")]}, "fraction must be"),
        ({"files": [DraftFile("a.raw", "A", "1", "1"), DraftFile("b.raw", "A", "1", "1")]}, "duplicates"),
        ({"files": [DraftFile("a.raw", "A", "1", "1"), DraftFile("b.raw", "A", "2", "2")]}, "different fractions"),
    ],
)
def test_validate_errors(patch, msg):
    a = Answer(user="EJQ", method="isoDTB", date="", allow_uneven=False,
               files=[DraftFile("a.raw", "A", "1", "1"), DraftFile("b.raw", "A", "2", "1")])
    for k, v in patch.items():
        setattr(a, k, v)
    assert msg in validate(a, ["isoDTB", "TMT", "DIA"])


def test_validate_ok_and_uneven_flag():
    files = [DraftFile("a.raw", "A", "1", "1"), DraftFile("b.raw", "A", "2", "2")]
    assert validate(Answer("EJQ", "isoDTB", "2026-09-02", True, files), ["isoDTB"]) == ""


def test_to_overrides_only_records_differences():
    d = _draft(files=[DraftFile("S_1_1.raw", "S", "1", "1"), DraftFile("S_1_2.raw", "S", "1", "2")])
    a = Answer("Isaac", "isoDTB", "", False, files=[
        DraftFile("S_1_1.raw", "S", "1", "1"),          # same as parse -> not written
        DraftFile("S_1_2.raw", "S", "2", ""),           # changed -> written, fraction cleared (-1)
    ])
    ov = to_overrides(a, d)
    assert ov.user == "Isaac" and ov.method == "isoDTB"
    assert set(ov.files) == {"S_1_2.raw"}
    assert ov.files["S_1_2.raw"] == FileOverride(experiment="S", bioreplicate=2, fraction=-1)


# ----------------------------- deterministic queue-contract: no Tcl, no display --


class _StubRoot:
    """No Tcl, no display: records what the resolver schedules instead of running a mainloop."""

    def __init__(self):
        self.after_callbacks = []  # (ms, fn, args) in schedule order

    def after(self, ms, fn, *args):
        self.after_callbacks.append((ms, fn, args))
        return len(self.after_callbacks)  # token, like tk.Tk.after

    def wait_window(self, win):  # only the main-thread dialog path uses this
        pass


class _SpyQueue(queue.Queue):
    """A queue.Queue that signals when a resolve() request has landed.

    resolve() still does its genuine put/done.wait() handoff through it; the event
    only lets the test drive the pump at a deterministic moment — after the worker's
    request is queued, before any pump has run.
    """

    def __init__(self):
        super().__init__()
        self.request_queued = threading.Event()

    def put(self, item, *args, **kwargs):
        super().put(item, *args, **kwargs)
        self.request_queued.set()


def _pump_driven_resolver(monkeypatch, dialog):
    """A TkResolver on a _StubRoot, pump armed, with `dialog` stubbed in place of the
    real Tk dialog (the real one imports tkinter and needs a display)."""
    from ionomos.resolve import TkResolver

    root = _StubRoot()
    res = TkResolver(root)
    res._q = _SpyQueue()  # observe the handoff without changing it
    res.start(every_ms=50)
    monkeypatch.setattr(TkResolver, "_dialog", dialog)
    return res, root


def test_tk_resolver_from_worker_thread(monkeypatch):
    """The resolver's worker-thread contract, with no Tk across threads.

    Replaces the former real-Tk cross-thread variant of the same name, which drove a
    live dialog from root.mainloop() while a worker blocked on resolve() — and flaked
    on windows-latest with 'Windows fatal exception: code 0x80000003', a Tcl abort
    during off-main-thread GC after a failed dialog teardown (three master runs on
    2026-09-25). PR #26's _drive_dialog watchdog (hangs -> fast failures) and PR #30's
    make_tk_root init backoff were necessary but not sufficient. The contract is
    unchanged — resolve() called off the main thread never touches Tcl: it enqueues
    the draft and blocks; the main-thread pump produces the result — and this variant
    needs no display, so it runs on both CI OSes.
    """
    res, root = _pump_driven_resolver(monkeypatch, lambda self, d: Overrides(user="EJQ", method="isoDTB"))
    out = []

    def worker():
        out.append(res.resolve(_draft()))

    t = threading.Thread(target=worker, daemon=True)
    t.start()

    # the worker reached the real queue handoff and is parked on done.wait():
    # nothing can finish until the main-thread pump runs
    assert res._q.request_queued.wait(5.0), "worker never reached the queue handoff"
    assert t.is_alive() and out == []

    ms, pump_fn, args = root.after_callbacks[0]  # deterministic drive, no mainloop
    assert ms == 50  # start() scheduled the pump at the requested cadence
    pump_fn(*args)  # one main-thread pump: stub _dialog -> box.append -> done.set()

    t.join(5.0)
    assert not t.is_alive()
    assert out and out[0].user == "EJQ"


def test_worker_thread_unblocks_when_dialog_crashes(monkeypatch):
    """Pin _pump's swallow contract: a dialog that raises must resolve to None
    ("treating as skip") and unblock the watcher thread, never hang it."""

    def boom(self, d):
        raise RuntimeError("dialog exploded")

    res, root = _pump_driven_resolver(monkeypatch, boom)
    out = []

    def worker():
        out.append(res.resolve(_draft()))

    t = threading.Thread(target=worker, daemon=True)
    t.start()

    assert res._q.request_queued.wait(5.0), "worker never reached the queue handoff"
    assert t.is_alive() and out == []

    _ms, pump_fn, args = root.after_callbacks[0]
    pump_fn(*args)  # _pump swallows the crash, leaves the box empty, sets done

    t.join(5.0)
    assert not t.is_alive()
    assert out == [None]  # resolve() returns box[0] if box else None: the skip outcome


def test_tk_variables_can_be_garbage_collected_on_a_worker_thread():
    """Formerly built 50 real tkutil.StringVar/BooleanVar pairs and gc-collected them
    on a worker thread — the site of the Windows 0x80000003 process aborts (Tcl
    reached from the wrong thread once a dialog teardown had dirtied process-global
    Tcl state). The guarantee the codebase actually relies on is tkutil's
    _ThreadSafeDel guard: __del__ that would call into Tcl is skipped off the main
    thread. Probe the guard directly — pure Python, no Tcl, runs on both CI OSes.
    """
    import gc

    from ionomos import tkutil

    worker_dels, main_dels = [], []

    class _Recording:
        def __init__(self, log):
            self.log = log

        def __del__(self):
            self.log.append(threading.get_ident())

    class Probe(tkutil._ThreadSafeDel, _Recording):
        """MRO: Probe -> _ThreadSafeDel -> _Recording. The guard's super().__del__()
        therefore lands in _Recording.__del__ and records the thread it finally ran on."""

    holder = [Probe(worker_dels) for _ in range(50)]
    for probe in holder:
        probe.ref = probe  # a reference cycle, like a closed dialog's callbacks

    done = threading.Event()

    def drop():
        holder.clear()
        gc.collect()
        done.set()

    t = threading.Thread(target=drop, daemon=True)
    t.start()
    assert done.wait(5.0), "worker never finished the collection"
    t.join(5.0)
    assert worker_dels == []  # the guard deferred every __del__ off the worker thread

    for p in [Probe(main_dels) for _ in range(5)]:
        del p
    gc.collect()
    assert len(main_dels) == 5  # on the main thread the guard runs the real __del__
    assert set(main_dels) == {threading.get_ident()}


def test_src_never_instantiates_plain_tk_variables():
    """tkutil's off-thread safety only holds if every Tk variable is a tkutil wrapper:
    tkinter.Variable.__del__ calls into Tcl and aborts the process when gc runs on a
    worker thread (the Windows 0x80000003 crash). AST scan, so comments and string
    literals can't produce false hits."""
    import ast
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "src" / "ionomos"
    offenders = []
    for path in sorted(src.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if isinstance(fn, ast.Attribute) and fn.attr in {"StringVar", "BooleanVar"}:
                if not (isinstance(fn.value, ast.Name) and fn.value.id == "tkutil"):
                    offenders.append(f"{path.name}:{node.lineno}")
            elif isinstance(fn, ast.Name) and fn.id in {"StringVar", "BooleanVar"}:
                offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, (
        "plain tkinter variables abort on worker-thread GC; use tkutil.StringVar/BooleanVar: " + ", ".join(offenders)
    )


# ------------------------------------------------------------- real tkinter --

_ok, _why = gui_available()


def _drive_dialog(root, act, watchdog_ms=20000):
    """Drive a TkResolver dialog from the Tk event loop, deterministically.

    Waits for the Toplevel to exist AND be mapped before acting, pumping the
    event loop with root.update(). A fixed-delay one-shot driver loses a
    display-timing race on CI: the synthetic key event is fired before the
    freshly deiconified dialog is mapped/focused, no binding ever runs, and
    wait_window (timeout_seconds=0) blocks until the job timeout. A watchdog
    destroys the dialog and flags the test instead of ever hanging CI.
    See issue #24. Returns the watchdog firings (must stay empty).
    """
    import tkinter as tk

    hung = []

    def watchdog():
        hung.append(True)
        for w in root.winfo_children():
            if isinstance(w, tk.Toplevel):
                w.destroy()

    watchdog_id = root.after(watchdog_ms, watchdog)
    acted = False

    def step():
        nonlocal acted
        wins = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]
        if hung or (acted and not wins):
            root.after_cancel(watchdog_id)
            return
        if wins and wins[0].winfo_ismapped() and not acted:
            acted = True
            act(wins[0])
        root.update()
        root.after(10, step)

    root.after(10, step)
    return hung


@pytest.mark.skipif(not _ok, reason=f"no GUI: {_why}")
def test_tk_dialog_accept_and_skip(lab):
    from ionomos.resolve import TkResolver

    d = make_drop(lab["inbox"], "XYZ99_isoDTB_run", iso_raws([1, 2], [1, 2]))
    with pytest.raises(IntakeError) as e:
        plan(d, lab["cfg"])
    dr = draft(d, lab["cfg"], e.value)

    root = make_tk_root()
    root.withdraw()
    remembered = []
    res = TkResolver(root, remember=lambda u, a: remembered.append((u, a)))

    # drive the dialog from the Tk event loop once it is actually interactive:
    # set the user, press Return (a fixed-delay one-shot event loses a display
    # race on CI and hangs wait_window forever — see issue #24)
    def accept_dialog(win):
        combos = [w for w in _all(win) if w.winfo_class() == "TCombobox"]
        combos[0].set("Isaac")
        win.event_generate("<Return>")

    assert not _drive_dialog(root, accept_dialog)
    ov = res.resolve(dr)
    assert ov is not None and ov.user == "Isaac" and ov.method == "isoDTB"
    assert remembered == [("Isaac", "XYZ")]

    # second dialog: Escape -> None
    assert not _drive_dialog(root, lambda win: win.event_generate("<Escape>"))
    assert res.resolve(dr) is None
    root.destroy()


def _all(w):
    yield w
    for c in w.winfo_children():
        yield from _all(c)


@pytest.mark.skipif(not _ok, reason=f"no GUI: {_why}")
def test_dialog_closes_when_folder_removed(lab):
    from ionomos.inbox import remove
    from ionomos.resolve import TkResolver

    folder = make_drop(lab['inbox'], 'bad', ['bad.raw'])
    root = make_tk_root()
    root.withdraw()
    root.after(100, lambda: remove(lab['inbox'], folder))
    assert TkResolver(root).resolve(draft(folder, lab['cfg'])) is None
    root.destroy()


def _err_text(win) -> str:
    """Text of the naming dialog's error line (the only Label bound to a StringVar)."""
    import tkinter.ttk as ttk

    for w in _all(win):
        if isinstance(w, ttk.Label):
            name = str(w.cget("textvariable"))
            if name:
                return str(win.getvar(name))
    return ""


def _mixed_drop(inbox, name):
    folder = make_drop(inbox, name, ["stray.raw"])
    (folder / "raw").mkdir()
    (folder / "raw" / "EJQ_PK_EJQ-2-027_isoDTB_1uM_3h_1_1.raw").write_bytes(b"\0" * 64)
    return folder


def _record_tk_escapes(root):
    """Record what would reach Tk's callback-exception hook on the lab PC."""
    escaped = []
    root.report_callback_exception = lambda *a: escaped.append(a)
    return escaped


@pytest.mark.skipif(not _ok, reason=f"no GUI: {_why}")
def test_mixed_drop_poll_shows_rejection_note(lab):
    """poll()'s 500 ms re-scan of a drop turned mixed must surface the IntakeError on
    the dialog's error line, not escape as a Tk callback traceback (PR #36 fallout)."""
    from ionomos.resolve import TkResolver

    folder = _mixed_drop(lab["inbox"], "MIX01_isoDTB_run")
    root = make_tk_root()
    root.withdraw()
    escaped = _record_tk_escapes(root)
    res = TkResolver(root)
    seen = []

    def arm_read(win):
        def read_and_escape():
            seen.append(_err_text(win))
            win.event_generate("<Escape>")

        win.after(700, read_and_escape)  # one poll cycle (500 ms) has run by then

    _drive_dialog(root, arm_read)
    assert res.resolve(draft(folder, lab["cfg"])) is None  # dialog stayed up, then Escape
    root.destroy()
    assert not escaped
    assert seen and "top level" in seen[0] and "raw/" in seen[0]


@pytest.mark.skipif(not _ok, reason=f"no GUI: {_why}")
def test_mixed_drop_accept_shows_rejection_note(lab):
    """accept() (Return) re-reads the drop before queueing: a mixed layout must surface
    the IntakeError on the dialog's error line, not escape as a Tk callback traceback."""
    from ionomos.resolve import TkResolver

    folder = _mixed_drop(lab["inbox"], "MIX02_isoDTB_run")
    root = make_tk_root()
    root.withdraw()
    escaped = _record_tk_escapes(root)
    res = TkResolver(root)
    seen = []

    def press_return(win):
        win.event_generate("<Return>")  # accept -> refresh_files() hits the mixed layout

        def read_and_escape():
            seen.append(_err_text(win))
            win.event_generate("<Escape>")

        win.after(100, read_and_escape)

    _drive_dialog(root, press_return)
    assert res.resolve(draft(folder, lab["cfg"])) is None  # accept must NOT close the window
    root.destroy()
    assert not escaped
    assert seen and "top level" in seen[0] and "raw/" in seen[0]
