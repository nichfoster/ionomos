"""
Files and folders dropped from Explorer onto the app window (D80).

    ok = enable(root, lambda paths: ...)     # True when drops are on; callback runs on the Tk thread

Tk has no file drop of its own. On Windows the app's window is told to accept files (DragAcceptFiles) and its
window procedure is wrapped to read WM_DROPFILES; everything else goes to Tk's own procedure untouched. A drop
anywhere in the window reaches it: Windows hands a drop on a child widget to the nearest window that accepts
files. Elsewhere (the Mac the code is written on) it returns False and the Folder… / Table… buttons are the way.

No dependency: ctypes only. Any failure leaves the window as it was and returns False.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Callable

log = logging.getLogger("ionomos.app")

WM_DROPFILES = 0x0233
WM_COPYDATA = 0x004A
WM_COPYGLOBALDATA = 0x0049
MSGFLT_ALLOW = 1
GWLP_WNDPROC = -4


def paths_from(names: list[str]) -> list[str]:
    """What a drop delivered, cleaned: no empty names, no duplicates, in the order dropped."""
    out: list[str] = []
    for n in names:
        n = (n or "").strip().strip('"')
        if n and n not in out:
            out.append(n)
    return out


def enable(widget, callback: Callable[[list[str]], None]) -> bool:
    """Accept files dropped on widget's top-level window. callback(paths) runs on the Tk thread, after the drop."""
    if os.name != "nt":
        return False
    try:
        return _enable_windows(widget, callback)
    except Exception:  # noqa: BLE001 - drag and drop is a convenience; the buttons always work
        log.exception("drag and drop could not be switched on")
        return False


def _enable_windows(widget, callback) -> bool:  # pragma: no cover - Windows only, needs a real window
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
    top.update_idletasks()
    hwnd = wintypes.HWND(top.winfo_id())
    holder = getattr(top, "_ionomos_drop", None)
    if holder is not None:      # already on: only the callback changes
        holder["callback"] = callback
        return True
    state = {"callback": callback}

    def on_drop(hdrop) -> None:
        names = []
        try:
            count = shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
            for i in range(count):
                n = shell32.DragQueryFileW(hdrop, i, None, 0)
                buf = ctypes.create_unicode_buffer(n + 1)
                shell32.DragQueryFileW(hdrop, i, buf, n + 1)
                names.append(buf.value)
        finally:
            shell32.DragFinish(hdrop)
        paths = paths_from(names)
        if paths:   # after the window procedure returns: Tk is not re-entered from inside it
            top.after(0, lambda: state["callback"](paths))

    def proc(h, msg, wparam, lparam):
        if msg == WM_DROPFILES:
            try:
                on_drop(ctypes.c_void_p(wparam))
            except Exception:  # noqa: BLE001 - an exception must never cross into Windows
                log.exception("reading a drop failed")
            return 0
        return call_proc(state["old"], h, msg, wparam, lparam)

    state["proc"] = WNDPROC(proc)   # kept alive as long as the window: Windows calls it
    state["old"] = set_long(hwnd, GWLP_WNDPROC, ctypes.cast(state["proc"], ctypes.c_void_p))
    if not state["old"]:
        raise OSError(ctypes.get_last_error(), "SetWindowLongPtr failed")
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
    log.info("drag and drop on")
    return True
