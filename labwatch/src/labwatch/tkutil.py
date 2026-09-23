"""
Tk variables that are safe to garbage-collect from any thread.

tkinter.Variable.__del__ calls into Tcl. If Python's garbage collector happens
to run on another thread (the FragPipe worker, the watcher, an app helper
thread) while a closed dialog's variables are garbage, Tcl aborts the whole
process on Windows ("fatal exception 0x80000003"). These subclasses only
unset the Tcl variable on the Tk (main) thread; elsewhere they skip it and
leave a few bytes for Tcl to free when the interpreter goes away.
"""
from __future__ import annotations

import threading
import tkinter as tk


class _ThreadSafeDel:
    def __del__(self):
        if threading.current_thread() is threading.main_thread():
            try:
                super().__del__()  # type: ignore[misc]
            except Exception:  # noqa: BLE001 - interpreter already gone at exit
                pass


class StringVar(_ThreadSafeDel, tk.StringVar):
    pass


class BooleanVar(_ThreadSafeDel, tk.BooleanVar):
    pass
