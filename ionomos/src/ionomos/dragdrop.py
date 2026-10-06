"""
Files and folders dropped from Explorer onto the app window (D80, D82).

    ok = enable(root, lambda paths: ...)     # True when drops are on; callback runs on the Tk thread

Tk has no file drop of its own. On Windows the app's window is told to accept files (DragAcceptFiles) and its
window procedure is wrapped to read WM_DROPFILES; every other message goes to Tk's own procedure untouched. A drop
anywhere in the window reaches it: Windows hands a drop on a child widget to the nearest window that accepts files.
Elsewhere (the Mac the code is written on) it returns False and the Folder… / Table… buttons are the way.

The window procedure runs inside Tk's event loop, while _tkinter holds its Tcl lock. Calling Tk from there (even
`after`) waits for that lock forever: the window froze on the first drop (0.18.0). So the procedure only reads the
dropped names into a queue (no Tcl, no Python object of Tk) and returns; a timer on the Tk side takes them from the
queue and calls the callback (D82). The procedure is removed again when the window is destroyed, before Python
or Tk go away, and nothing it does can raise into Windows.

No dependency: ctypes only. Any failure leaves the window as it was and returns False.
"""
from __future__ import annotations

import logging
import os
import queue
from collections.abc import Callable

log = logging.getLogger("ionomos.app")

WM_DROPFILES = 0x0233
WM_NCDESTROY = 0x0082
WM_COPYDATA = 0x004A
WM_COPYGLOBALDATA = 0x0049
MSGFLT_ALLOW = 1
GWLP_WNDPROC = -4
POLL_MS = 150


def paths_from(names: list[str]) -> list[str]:
    """What a drop delivered, cleaned: no empty names, no duplicates, in the order dropped."""
    out: list[str] = []
    for n in names:
        n = (n or "").strip().strip('"')
        if n and n not in out:
            out.append(n)
    return out


class Drops:
    """The Tk side: dropped paths waiting in a queue, handed to the callback by a timer on the Tk thread."""

    def __init__(self, widget, callback: Callable[[list[str]], None]):
        self.widget = widget
        self.callback = callback
        self.waiting: queue.SimpleQueue = queue.SimpleQueue()
        self.stopped = False

    def put(self, names: list[str]) -> None:
        """Any thread, and inside the window procedure: no Tk here."""
        paths = paths_from(names)
        if paths:
            self.waiting.put(paths)

    def drain(self) -> None:
        while True:
            try:
                paths = self.waiting.get_nowait()
            except queue.Empty:
                break
            try:
                self.callback(paths)
            except Exception:  # noqa: BLE001 - a bad drop must never stop the timer or the app
                log.exception("handling dropped %s failed", paths)

    def start(self) -> None:
        def tick():
            if self.stopped:
                return
            self.drain()
            try:
                self.widget.after(POLL_MS, tick)
            except Exception:  # noqa: BLE001 - the window is gone
                self.stopped = True

        self.widget.after(POLL_MS, tick)


def enable(widget, callback: Callable[[list[str]], None]) -> bool:
    """Accept files dropped on widget's top-level window. callback(paths) runs on the Tk thread, after the drop."""
    if os.name != "nt":
        return False
    try:
        return _enable_windows(widget, callback)
    except Exception:  # noqa: BLE001 - drag and drop is a convenience; the buttons always work
        log.exception("drag and drop could not be switched on")
        return False


def _enable_windows(widget, callback) -> bool:  # pragma: no cover - Windows only (tests/test_app.py drops on CI)
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32")
    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    set_long = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
    set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    set_long.restype = ctypes.c_void_p
    call_proc = user32.CallWindowProcW
    call_proc.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    call_proc.restype = LRESULT
    shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
    shell32.DragAcceptFiles.restype = None
    shell32.DragQueryFileW.argtypes = [ctypes.c_void_p, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
    shell32.DragQueryFileW.restype = wintypes.UINT
    shell32.DragFinish.argtypes = [ctypes.c_void_p]
    shell32.DragFinish.restype = None

    top = widget.winfo_toplevel()
    old_state = getattr(top, "_ionomos_drop", None)
    if old_state is not None:      # already on: only the callback changes
        old_state["drops"].callback = callback
        return True
    top.update_idletasks()
    hwnd = top.winfo_id()
    drops = Drops(top, callback)
    state: dict = {"drops": drops, "old": None}

    def read(hdrop) -> list[str]:
        names = []
        try:
            count = shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
            for i in range(min(count, 1000)):
                n = shell32.DragQueryFileW(hdrop, i, None, 0)
                buf = ctypes.create_unicode_buffer(n + 1)
                if shell32.DragQueryFileW(hdrop, i, buf, n + 1):
                    names.append(buf.value)
        finally:
            shell32.DragFinish(hdrop)
        return names

    def proc(h, msg, wparam, lparam):
        old = state["old"]
        try:
            if msg == WM_DROPFILES:
                drops.put(read(ctypes.c_void_p(wparam)))   # names into a queue; Tk is not touched here
                return 0
            if msg == WM_NCDESTROY and old:   # the window goes: Tk's own procedure back, before Python may go
                set_long(h, GWLP_WNDPROC, old)
                drops.stopped = True
        except Exception:  # noqa: BLE001 - an exception must never cross into Windows
            pass
        return call_proc(old, h, msg, wparam, lparam) if old else 0

    state["proc"] = WNDPROC(proc)   # kept alive as long as the window: Windows calls it
    ctypes.set_last_error(0)
    old = set_long(hwnd, GWLP_WNDPROC, ctypes.cast(state["proc"], ctypes.c_void_p))
    if not old:
        raise OSError(ctypes.get_last_error(), "SetWindowLongPtr failed")
    state["old"] = old
    shell32.DragAcceptFiles(hwnd, True)
    try:   # an app started 'as administrator' still takes drops from a normal Explorer
        f = user32.ChangeWindowMessageFilterEx
        f.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.DWORD, ctypes.c_void_p]
        f.restype = wintypes.BOOL
        for m in (WM_DROPFILES, WM_COPYDATA, WM_COPYGLOBALDATA):
            f(hwnd, m, MSGFLT_ALLOW, None)
    except (AttributeError, OSError):
        pass
    top._ionomos_drop = state
    drops.start()
    log.info("drag and drop on")
    return True
