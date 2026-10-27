"""Hidden-window UI tests: the app on a real Tk root that nobody sees (docs/UI.md S4.1, D93).

What a test gets from here (the fixtures in tests/conftest.py wrap them: ``hidden_app``, ``no_dialogs``,
``tk_errors``):

- ``hide_all_windows()``: every ``tk.Toplevel`` is withdrawn right after it is made, and ``deiconify``, ``lift``,
  ``tkraise``, ``focus_force`` and ``state("normal"/"zoomed"/"iconic")`` do nothing on a Toplevel or the root, so a
  window the app shows later stays hidden. A withdrawn window still has its widgets, its requested size
  (``winfo_reqwidth``), ``geometry``, ``winfo_exists``, ``wait_window`` and ``update_idletasks``. Returns the undo.
- ``make_app(tmp_path, monkeypatch, config_path=None, hide=True)`` / ``destroy_app(app)``: the app on a withdrawn
  root, HOME and APPDATA in ``tmp_path`` (as tests/test_app.py's fixture).
- ``pump_until(app_or_root, cond, secs=15)``: run the Tk loop until ``cond()`` is true.
- ``NoDialogs``: messagebox, simpledialog, filedialog and colorchooser replaced by recorders. A native dialog
  (``messagebox.showerror``, the file pickers) is not a Toplevel: hiding cannot hide it, and it would block the
  test until someone clicks. ``.expect(name, answer)`` queues an answer; ``strict=True`` fails the test at the
  call, naming the dialog's title and text, when nothing was expected.
- ``toplevels(root)``: the live Toplevels under a root, newest last.
- ``TkErrors``: records every exception from a Tk callback and from ``App.post`` callbacks (both reach
  ``App._on_tk_error``: ``root.report_callback_exception`` is bound to it and ``App._pump_ui`` calls
  ``self._on_tk_error``), still writing the app's crash file, without the app's error box.
- ``fits_screen(win, width=1366, height=768)``: the window's size fits that screen.
- ``Timers``: records every widget destroyed while a timer of its own (``widget.after`` / ``after_idle``) is still
  pending: that timer outlives its callback and can call one registered later under the same name (#185; the app
  uses ``tkutil.after``). ``make_app`` installs one (``app._tkharness_timers``); ``hidden_app`` fails at teardown
  on any.

Patching dialogs: every Ionomos module that asks does ``from tkinter import messagebox`` (or filedialog,
simpledialog, colorchooser): it holds the module object, so replacing ``messagebox.showerror`` on the module is
what ``app.messagebox.showerror`` calls (checked for ``ionomos.app``, ``popups``, ``experiment_editor``,
``analysis_tab``, ``library_tab``, ``folder_check``, ``notify_tab``, ``accuracy_page``, ``bundle_dialog``,
``resolve``, 2026-10-15). In case one later does ``from tkinter.messagebox import showerror``, ``NoDialogs`` also
replaces any attribute of a loaded ``ionomos.*`` module that *is* one of the original functions.

These open a real Tk interpreter: tests using them run where ``tests.conftest.gui_tests()`` allows (CI always,
locally only with IONOMOS_GUI_TESTS=1). Even hidden, the rule stays: a bug here would put windows on the screen.
"""
from __future__ import annotations

import collections
import itertools
import sys
import time
import tkinter as tk
from collections.abc import Callable
from tkinter import colorchooser, filedialog, messagebox, simpledialog
from typing import Any, NamedTuple

# --------------------------------------------------------------------------- hiding --

_seq = itertools.count(1)
_SHOWING_STATES = ("normal", "zoomed", "iconic")


def hide_all_windows() -> Callable[[], None]:
    """Withdraw every new Toplevel and make showing a window a no-op; returns the function that undoes it."""
    saved: list[tuple[type, str, Any]] = []
    missing = object()

    def patch(cls: type, name: str, fn: Any) -> None:
        saved.append((cls, name, cls.__dict__.get(name, missing)))
        setattr(cls, name, fn)

    orig_init = tk.Toplevel.__init__
    orig_state = tk.Wm.wm_state

    def init(self, *a, **kw):
        orig_init(self, *a, **kw)
        self._tkharness_seq = next(_seq)
        try:
            self.withdraw()
        except tk.TclError:
            pass

    def state(self, newstate=None):
        if newstate in _SHOWING_STATES:
            return None  # the window stays withdrawn
        return orig_state(self, newstate)

    def nothing(self, *a, **kw):
        return None

    patch(tk.Toplevel, "__init__", init)
    for cls in (tk.Toplevel, tk.Tk):
        for name in ("deiconify", "wm_deiconify", "lift", "tkraise", "focus_force"):
            patch(cls, name, nothing)
        for name in ("state", "wm_state"):
            patch(cls, name, state)

    def undo() -> None:
        for cls, name, old in reversed(saved):
            if old is missing:
                try:
                    delattr(cls, name)
                except AttributeError:
                    pass
            else:
                setattr(cls, name, old)
        saved.clear()

    return undo


# ------------------------------------------------------------------------ the app --


def make_app(tmp_path, monkeypatch, config_path=None, hide: bool = True):
    """The app on a withdrawn root (HOME / APPDATA in tmp_path). End it with destroy_app(app)."""
    from ionomos.app import App
    from tests.conftest import make_tk_root

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    undo = hide_all_windows() if hide else None
    timers = Timers().install()
    try:
        root = make_tk_root()
        root.withdraw()
        try:
            app = App(root, config_path or tmp_path / "Auto" / "config.yaml")
        except BaseException:
            tk.Tk.destroy(root)
            raise
    except BaseException:
        timers.uninstall()
        if undo:
            undo()
        raise
    app._tkharness_undo = undo
    app._tkharness_timers = timers
    return app


def destroy_app(app) -> None:
    """Stop the pop-up timer, destroy the root, undo the hiding. Safe to call twice."""
    try:
        app.popups.stop()
    except Exception:  # noqa: BLE001 - a half-built app has no popups
        pass
    try:
        # Unbound call: a test may have monkeypatched app.root.destroy to a no-op (see tests/test_app.py's fixture);
        # the real root must still go, or its interpreter leaks into the rest of the session.
        tk.Tk.destroy(app.root)
    except tk.TclError:
        pass
    timers = getattr(app, "_tkharness_timers", None)
    if timers is not None:
        timers.uninstall()
    undo = getattr(app, "_tkharness_undo", None)
    app._tkharness_undo = None
    if undo:
        undo()


def _root_of(app_or_root) -> tk.Misc:
    return getattr(app_or_root, "root", app_or_root)


def pump_until(app_or_root, cond: Callable[[], Any], secs: float = 15) -> bool:
    """Run the Tk loop until cond() is true (True) or secs pass (False)."""
    root = _root_of(app_or_root)
    end = time.monotonic() + secs
    while time.monotonic() < end:
        root.update()
        if cond():
            return True
        time.sleep(0.05)
    return False


def toplevels(root) -> list[tk.Toplevel]:
    """Every live Toplevel under root (at any depth), newest last."""
    found: list[tk.Toplevel] = []

    def walk(w) -> None:
        for child in list(getattr(w, "children", {}).values()):
            if isinstance(child, tk.Toplevel):
                try:
                    if child.winfo_exists():
                        found.append(child)
                except tk.TclError:
                    pass
            walk(child)

    walk(_root_of(root))
    # creation order: hide_all_windows numbers each one; otherwise the order Python made them in, per parent
    order = {id(w): i for i, w in enumerate(found)}
    return sorted(found, key=lambda w: (getattr(w, "_tkharness_seq", 0), order[id(w)]))


def fits_screen(win, width: int = 1366, height: int = 768) -> bool:
    """True when the window fits a width x height screen: the larger of what its contents ask for
    (winfo_reqwidth / reqheight, computed for a withdrawn window too), its set geometry and its minsize."""
    win.update_idletasks()
    w, h = win.winfo_reqwidth(), win.winfo_reqheight()
    try:
        size = win.wm_geometry().split("+", 1)[0]
        gw, gh = (int(x) for x in size.split("x"))
        if gw > 1 and gh > 1:
            w, h = max(w, gw), max(h, gh)
        mw, mh = win.wm_minsize()
        w, h = max(w, mw), max(h, mh)
    except (tk.TclError, ValueError):
        pass
    return w <= width and h <= height


# ------------------------------------------------------------------------ dialogs --


class DialogCall(NamedTuple):
    name: str  # "showerror", "askyesno", "askopenfilename", ...
    title: str | None
    detail: Any  # the message / prompt, or the keyword arguments (file dialogs, askcolor)


_MESSAGEBOX = ("showinfo", "showwarning", "showerror", "askyesno", "askyesnocancel", "askokcancel", "askquestion",
               "askretrycancel")
_SIMPLEDIALOG = ("askstring", "askinteger", "askfloat")
_FILEDIALOG = ("askopenfilename", "askopenfilenames", "asksaveasfilename", "askdirectory")
_DEFAULTS = {"showinfo": None, "showwarning": None, "showerror": None, "askyesno": False, "askyesnocancel": None,
             "askokcancel": False, "askquestion": "no", "askretrycancel": False, "askstring": None,
             "askinteger": None, "askfloat": None, "askopenfilename": "", "askopenfilenames": "",
             "asksaveasfilename": "", "askdirectory": "", "askcolor": (None, None)}
_MODULES = [(messagebox, n) for n in _MESSAGEBOX] + [(simpledialog, n) for n in _SIMPLEDIALOG] + \
    [(filedialog, n) for n in _FILEDIALOG] + [(colorchooser, "askcolor")]


def _where(frame) -> str:
    """Where a dialog was asked from (frame: the caller of the recorder): when a Tk callback made the call, the
    widget whose command / binding / after() it was (tkinter.CallWrapper.widget), whether that widget still exists
    and which Tcl interpreter it is in (a previous test's app is another one); then the innermost Python frames.
    Issue #185: a "Find FragPipe" box once came from nothing the smoke test pressed, and the log could not say how
    (it was a closed pop-up's timer calling the Fix button's command under its old name: Timers)."""
    import traceback

    lines = []
    f = frame
    while f is not None:
        if f.f_code is tk.CallWrapper.__call__.__code__:
            w = getattr(f.f_locals.get("self"), "widget", None)
            try:
                exists = bool(w.winfo_exists())
                cls = w.winfo_class()
                text = w.cget("text") if cls in ("TButton", "Button") else ""
            except (tk.TclError, AttributeError):
                exists, cls, text = False, "?", ""
            lines.append(f"    from a Tk callback of {w} ({cls} {text!r}; exists={exists}; "
                         f"interpreter {id(getattr(w, 'tk', None)):#x})")
            break
        f = f.f_back
    if not lines:
        lines.append("    called from Python, not from a Tk callback")
    stack = traceback.extract_stack(frame, limit=10)
    lines += [f"    {fs.filename.replace(chr(92), '/').rsplit('/', 1)[-1]}:{fs.lineno} in {fs.name}" for fs in stack]
    return "\n".join(lines)


class NoDialogs:
    """Every dialog replaced by a recorder; a context manager, or ``NoDialogs(monkeypatch=mp).start()``.

    ``.calls``: DialogCall(name, title, detail) in order. ``.expect(name, answer)``: the next call of that name
    returns answer (several queue up). Without one it returns the default (show* None, askyesno / askokcancel /
    askretrycancel False, askyesnocancel None, askquestion "no", simpledialog None, file dialogs "", askcolor
    (None, None)) and counts as unexpected; with ``strict=True`` it raises AssertionError there instead, naming the
    dialog (inside a Tk callback that goes to report_callback_exception: use TkErrors to see it). ``.where``: per
    call, the Tk callback's widget (if one asked) and the stack; ``.report()`` lists the unexpected ones with it.
    """

    def __init__(self, strict: bool = False, monkeypatch=None):
        self.strict = strict
        self.calls: list[DialogCall] = []
        self.where: list[str] = []  # per call: what asked (_where), so a dialog nobody expected says how it came
        self._expected_flags: list[bool] = []
        self._queue: dict[str, collections.deque] = collections.defaultdict(collections.deque)
        self._mp = monkeypatch
        self._undo: list[tuple[Any, str, Any]] = []

    # -- answers --
    def expect(self, name: str, answer: Any) -> NoDialogs:
        if name not in _DEFAULTS:
            raise ValueError(f"no dialog called {name!r}; one of {', '.join(_DEFAULTS)}")
        self._queue[name].append(answer)
        return self

    def unexpected(self) -> list[DialogCall]:
        return [c for c, ok in zip(self.calls, self._expected_flags, strict=True) if not ok]

    def pending(self) -> dict[str, list]:
        """Answers queued with expect() that no dialog has taken yet."""
        return {k: list(v) for k, v in self._queue.items() if v}

    def report(self) -> str:
        """Each dialog nobody expected, with where it was asked from (the Tk callback's widget and the stack)."""
        return "\n".join(f"  {c.name}: {c.title!r}: {c.detail!r}\n{w}"
                         for c, w, ok in zip(self.calls, self.where, self._expected_flags, strict=True) if not ok)

    def assert_none(self) -> None:
        """Fail when a dialog was shown that had no answer queued, naming each and where it came from."""
        if self.unexpected():
            raise AssertionError("dialogs nobody expected:\n" + self.report())

    def _answer(self, name: str, title, detail):
        self.calls.append(DialogCall(name, title, detail))
        self.where.append(_where(sys._getframe(2)))
        q = self._queue.get(name)
        if q:
            self._expected_flags.append(True)
            return q.popleft()
        self._expected_flags.append(False)
        if self.strict:
            raise AssertionError(f"unexpected dialog {name}: {title!r}: {detail!r}")
        return _DEFAULTS[name]

    def _recorder(self, name: str):
        if name in _MESSAGEBOX:
            def fn(title=None, message=None, **options):
                return self._answer(name, title, message)
        elif name in _SIMPLEDIALOG:
            def fn(title=None, prompt=None, **kw):
                return self._answer(name, title, prompt)
        elif name == "askcolor":
            def fn(color=None, **options):
                return self._answer(name, options.get("title"), {"color": color, **options})
        else:
            def fn(**options):
                return self._answer(name, options.get("title"), options)
        fn.__name__ = f"nodialogs_{name}"
        fn._tkharness_nodialogs = True
        return fn

    # -- patching --
    def _set(self, obj, attr: str, value) -> None:
        if self._mp is not None:
            self._mp.setattr(obj, attr, value)
        else:
            self._undo.append((obj, attr, getattr(obj, attr)))
            setattr(obj, attr, value)

    def start(self) -> NoDialogs:
        originals = {}
        for mod, name in _MODULES:
            orig = getattr(mod, name)
            originals[id(orig)] = name
            self._set(mod, name, self._recorder(name))
        # a name imported straight into an ionomos module (from tkinter.messagebox import showerror)
        for modname, mod in list(sys.modules.items()):
            if mod is None or not (modname == "ionomos" or modname.startswith("ionomos.")):
                continue
            for attr, val in list(vars(mod).items()):
                name = originals.get(id(val)) if callable(val) else None
                if name:
                    self._set(mod, attr, self._recorder(name))
        return self

    def stop(self) -> None:
        for obj, attr, old in reversed(self._undo):
            setattr(obj, attr, old)
        self._undo.clear()  # with monkeypatch, its own undo restores at the end of the test

    def __enter__(self) -> NoDialogs:
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()


# ------------------------------------------------------------------------- errors --


class TkErrors:
    """Record every exception from a Tk callback or an App.post callback, as (exc_type, exc, tb).

    Installs itself as ``root.report_callback_exception`` and, for an App, as ``app._on_tk_error`` (what
    ``App._pump_ui`` calls). The app's own handler still runs, so its crash file is written, but with
    ``messagebox.showerror`` silenced for that call (a native box would block a hidden test).
    """

    def __init__(self, app_or_root):
        self.target = app_or_root
        self.root = _root_of(app_or_root)
        self.errors: list[tuple[type, BaseException, Any]] = []
        self._orig_handler = getattr(app_or_root, "_on_tk_error", None)
        self._orig_report = self.root.report_callback_exception
        self._installed = False

    def _record(self, exc_type, exc, tb) -> None:
        self.errors.append((exc_type, exc, tb))
        handler = self._orig_handler
        if handler is None:
            return
        saved = messagebox.showerror
        messagebox.showerror = lambda *a, **k: None
        try:
            handler(exc_type, exc, tb)
        except Exception:  # noqa: BLE001 - the app's handler failing is not the test's error
            pass
        finally:
            messagebox.showerror = saved

    def install(self) -> TkErrors:
        if not self._installed:
            self.root.report_callback_exception = self._record
            if self._orig_handler is not None:
                self.target._on_tk_error = self._record
            self._installed = True
        return self

    def uninstall(self) -> None:
        if not self._installed:
            return
        self.root.report_callback_exception = self._orig_report
        if self._orig_handler is not None:
            try:
                del self.target._on_tk_error  # back to the class's method
            except AttributeError:
                pass
        self._installed = False

    def clear(self) -> None:
        self.errors.clear()

    def text(self) -> str:
        import traceback

        return "\n".join("".join(traceback.format_exception(t, e, tb)) for t, e, tb in self.errors)

    def assert_none(self) -> None:
        if self.errors:
            raise AssertionError(f"{len(self.errors)} error(s) in Tk callbacks:\n{self.text()}")

    def __enter__(self) -> TkErrors:
        return self.install()

    def __exit__(self, *exc) -> None:
        self.uninstall()


# ------------------------------------------------------------------------- timers --


class DanglingTimer(NamedTuple):
    widget: str   # the Tk path of the widget destroyed
    command: str  # the Tcl command its pending timer calls: tkinter names it id(its wrapper) + the callback's __name__
    kind: str     # "timer" or "idle"


def _pending(w) -> list[tuple[str, str]]:
    """(script, kind) of every pending after / after_idle event of w's Tcl interpreter."""
    out = []
    try:
        ids = w.tk.splitlist(w.tk.call("after", "info"))
    except tk.TclError:
        return out
    for tid in ids:
        try:
            script, kind = w.tk.splitlist(w.tk.call("after", "info", tid))
        except (tk.TclError, ValueError):
            continue
        out.append((str(script), str(kind)))
    return out


class Timers:
    """Record each widget destroyed while a timer of its own (``widget.after`` / ``after_idle``) is still pending,
    as DanglingTimer(widget, command, kind); a context manager, or ``Timers().install()`` / ``.uninstall()``.

    ``destroy()`` deletes a widget's Tcl commands but not its timers. Python soon reuses the freed callback wrapper's
    address, so a callback registered later under the same ``__name__`` (any lambda) gets the same command name, and
    the old timer calls it: a pop-up closed within 1.5 s of opening pressed the Setup checklist's Find FragPipe
    button that way (#185). Whether the name is taken over is chance; the pending timer is not, so this looks at that,
    when the widget is destroyed. The root is left out (destroying it ends the app): ``root.after`` and
    ``tkutil.after`` are the root's. Patches ``tkinter.Misc.destroy``, which every widget's destroy() ends in."""

    def __init__(self):
        self.dangling: list[DanglingTimer] = []
        self._orig = None

    def install(self) -> Timers:
        if self._orig is not None:
            return self
        orig = self._orig = tk.Misc.destroy
        dangling = self.dangling

        def destroy(widget):
            mine = widget._tclCommands
            if mine and not isinstance(widget, tk.Tk):
                dangling.extend(DanglingTimer(str(widget), script, kind)
                                for script, kind in _pending(widget) if script in mine)
            orig(widget)

        tk.Misc.destroy = destroy
        return self

    def uninstall(self) -> None:
        if self._orig is not None:
            tk.Misc.destroy = self._orig
            self._orig = None

    def report(self) -> str:
        return "\n".join(f"  {d.widget}: its pending {d.kind} calls {d.command}" for d in self.dangling)

    def assert_none(self) -> None:
        if self.dangling:
            raise AssertionError("widgets destroyed with a timer of their own still pending (set it with "
                                 "tkutil.after; #185):\n" + self.report())

    def __enter__(self) -> Timers:
        return self.install()

    def __exit__(self, *exc) -> None:
        self.uninstall()
