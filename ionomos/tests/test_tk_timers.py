"""Timers of a window that may close first (#185, tkutil.after). In a Tcl interpreter without Tk, so no window is
made and these run everywhere; and the source scan that keeps every window's timer on the root.

A closed pop-up's own timer (win.after(1500, lambda: ...)) once called the Setup checklist's Find FragPipe button
on Windows CI: destroy() deleted the pop-up's Tcl command but not its timer, and the button's command, registered
a moment later at the freed address, got the same name."""
import ast
import time
import tkinter as tk
from pathlib import Path

import pytest

from ionomos import tkutil
from tests.tkharness import DanglingTimer, Timers


@pytest.fixture
def tcl():
    try:
        return tk.Tcl()
    except tk.TclError as exc:
        pytest.skip(f"no Tcl here: {exc}")


class Window(tk.Misc):
    """A Toplevel as far as timers go, in an interpreter without Tk: after() and destroy() are tkinter's own."""

    def __init__(self, root):
        self.master, self.tk, self._w = root, root.tk, ".window"
        self.alive = True

    def __str__(self):
        return self._w

    def winfo_exists(self):
        return self.alive

    def destroy(self):  # BaseWidget.destroy without Tk's part: the window's Tcl commands are deleted
        self.alive = False
        tk.Misc.destroy(self)


def _pending(interp) -> tuple:
    return interp.tk.splitlist(interp.tk.call("after", "info"))


def _pump(interp, secs: float = 0.15) -> None:
    end = time.monotonic() + secs
    while time.monotonic() < end:
        interp.update()
        time.sleep(0.005)


def test_a_windows_own_timer_outlives_its_callback(tcl):
    """Why tkutil.after exists: destroy() deletes the window's commands, its timer stays pending and calls a name
    that is gone (or, once Python has reused the address, another callback's)."""
    w = Window(tcl)
    with Timers() as timers:
        tid = w.after(5000, lambda: None)
        name = str(tcl.tk.splitlist(tcl.tk.call("after", "info", tid))[0])
        w.destroy()
    assert tid in _pending(tcl) and not tcl.tk.call("info", "commands", name)
    assert timers.dangling == [DanglingTimer(".window", name, "timer")]
    assert ".window: its pending timer calls" in timers.report()
    with pytest.raises(AssertionError, match="tkutil.after"):
        timers.assert_none()
    tcl.tk.call("after", "cancel", tid)


def test_tkutil_after_runs_while_the_window_is_open(tcl):
    w = Window(tcl)
    ran = []
    with Timers() as timers:
        tkutil.after(w, 10, ran.append, "timer")
        tkutil.after_idle(w, ran.append, "idle")
        _pump(tcl)
    assert sorted(ran) == ["idle", "timer"] and not _pending(tcl) and not timers.dangling


def test_tkutil_after_is_the_roots_and_does_nothing_once_the_window_is_gone(tcl):
    w = Window(tcl)
    ran = []
    with Timers() as timers:
        tkutil.after(w, 10, ran.append, "timer")
        tkutil.after_idle(w, ran.append, "idle")
        w.destroy()
        assert not w._tclCommands  # nothing of the window's own for a timer to outlive
        _pump(tcl)
    assert ran == [] and not _pending(tcl) and not timers.dangling


def test_every_timer_of_a_window_is_set_with_tkutil_after():
    """widget.after / after_idle only on a root (the app's, the resolver's), which lives as long as the app; any
    other widget's timer goes through tkutil.after. AST scan, so comments and strings can't produce false hits."""
    src = Path(tkutil.__file__).parent
    offenders = []
    for path in sorted(src.rglob("*.py")):
        if path.name == "tkutil.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("after", "after_idle")):
                continue
            owner = node.func.value
            if isinstance(owner, ast.Name) and owner.id in ("tkutil", "root"):
                continue
            if isinstance(owner, ast.Attribute) and owner.attr == "root":
                continue
            if path.name == "dragdrop.py" and ast.unparse(owner) == "self.widget":
                continue  # enable(root, ...): the root window, which lives as long as the app
            offenders.append(f"{path.relative_to(src)}:{node.lineno} {ast.unparse(node.func)}")
    assert not offenders, "a window's own timer can outlive it and call another callback (#185); use " \
                          "tkutil.after(win, ms, func): " + ", ".join(offenders)
