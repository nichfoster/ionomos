"""
Small Tk helpers shared by the app's windows.

    StringVar / BooleanVar      Tk variables that are safe to garbage-collect from any thread (below)
    char_width / line_height    the default font's size in pixels: windows are sized in text units (docs/UI.md S1.10)
    text_size / fit_window      a box of cols x rows characters, clamped to the screen
    ScrolledFrame               a frame that scrolls when its content is taller than the space (S1.8)
    after / after_idle          a timer of a window that may close first: never calls another window's callback (#185)

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
from collections.abc import Callable
from tkinter import ttk


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


# ------------------------------------------------------------- timers of a window that may close first (#185) --
#
# widget.after(ms, f) makes f a Tcl command of the widget, named id(tkinter's wrapper) + f.__name__
# ("2155…<lambda>", "2155…close"), and sets a Tcl timer that calls that name. widget.destroy() deletes the command
# but not the timer. Python soon reuses the freed wrapper's address, so the next callback registered with the same
# __name__ gets the same command name, and the old timer calls it: a pop-up closed within 1.5 s of opening pressed
# the Setup checklist's Find FragPipe button (its command=lambda a=…: …) that way. So a timer of a window that can
# close is set on the root, which lives as long as the app, and does nothing once that window is gone.


def after(widget, ms: int | str, func: Callable, *args):
    """widget.after(ms, func, *args) for a widget that may be destroyed before the timer fires: the timer is the
    root's, and func runs only if widget still exists then. Returns the root's after id."""
    def run():
        try:
            alive = bool(widget.winfo_exists())
        except tk.TclError:  # the app is closing
            alive = False
        if alive:
            func(*args)

    return widget._root().after(ms, run)


def after_idle(widget, func: Callable, *args):
    """widget.after_idle(func, *args) for a widget that may be destroyed first (as after)."""
    return after(widget, "idle", func, *args)


# ------------------------------------------------------------- sizes in text units (docs/UI.md S1.10) --
#
# Windows are sized from the font, never in pixels: a column 40 characters wide is 40 characters at 100 % and
# at 150 % scaling. Everything here runs on the Tk thread (it asks Tk for the font).


def char_width() -> int:
    """The width of one '0' of the default font, in pixels (7 at 100 %, about 10 at 150 %)."""
    from tkinter import font as tkfont

    try:
        return max(tkfont.nametofont("TkDefaultFont").measure("0"), 1)
    except (tk.TclError, RuntimeError):
        return 7


def line_height() -> int:
    """One line of the default font, in pixels."""
    from tkinter import font as tkfont

    try:
        return max(tkfont.nametofont("TkDefaultFont").metrics("linespace"), 1)
    except (tk.TclError, RuntimeError):
        return 16


def text_size(cols: int, rows: int) -> tuple[int, int]:
    """(width, height) in pixels of a box cols characters wide and rows lines tall, in the default font."""
    return cols * char_width(), rows * line_height()


def fit_window(win, cols: int, rows: int, min_cols: int = 60, min_rows: int = 18, place: bool = True) -> tuple[int, int]:
    """Size a Toplevel to cols x rows of text, no bigger than the screen (uinav.fit_size), and put it near the
    top centre of the screen. Returns the (width, height) in pixels that was used."""
    from ionomos import uinav

    w, h = text_size(cols, rows)
    mw, mh = text_size(min_cols, min_rows)
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    w, h = uinav.fit_size(w, h, sw, sh, min_w=mw, min_h=mh)
    if place:
        win.geometry(f"{w}x{h}+{max((sw - w) // 2, 0)}+{max((sh - h) // 4, 0)}")
    else:
        win.geometry(f"{w}x{h}")
    return w, h


class ScrolledFrame(ttk.Frame):
    """A frame that scrolls vertically when its content is taller than the space it gets (docs/UI.md S1.8).

    Put widgets in `.inner`. The scrollbar is shown only when needed; the mouse wheel scrolls while the pointer
    is over the frame. Plain ttk, nothing restyled."""

    def __init__(self, parent, **kw):
        super().__init__(parent, **kw)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vbar.set)
        self.inner = ttk.Frame(self.canvas)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._fit)
        self.canvas.bind("<Configure>", self._fit)
        for w in (self.canvas, self.inner):
            w.bind("<Enter>", self._bind_wheel)
            w.bind("<Leave>", self._unbind_wheel)
        self._bar_shown = False
        try:  # the frame's own background, so the canvas is not a white box
            bg = ttk.Style().lookup("TFrame", "background")
            if bg:
                self.canvas.configure(background=bg)
        except tk.TclError:
            pass

    def _fit(self, _event=None) -> None:
        try:
            self.canvas.itemconfigure(self._win, width=self.canvas.winfo_width())
            need = self.inner.winfo_reqheight()
            self.canvas.configure(scrollregion=(0, 0, self.inner.winfo_reqwidth(), need))
            show = need > self.canvas.winfo_height() + 2
            if show and not self._bar_shown:
                self.vbar.grid(row=0, column=1, sticky="ns")
            elif not show and self._bar_shown:
                self.vbar.grid_remove()
                self.canvas.yview_moveto(0)
            self._bar_shown = show
        except tk.TclError:
            pass

    def _bind_wheel(self, _event=None) -> None:
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.canvas.bind_all(seq, self._wheel)

    def _unbind_wheel(self, _event=None) -> None:
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.canvas.unbind_all(seq)

    def _wheel(self, event) -> None:
        if not self._bar_shown:
            return
        if getattr(event, "num", None) == 4 or getattr(event, "delta", 0) > 0:
            self.canvas.yview_scroll(-1, "units")
        elif getattr(event, "num", None) == 5 or getattr(event, "delta", 0) < 0:
            self.canvas.yview_scroll(1, "units")

    def scrolls(self) -> bool:
        """Whether the content is taller than the frame right now (the scrollbar is shown)."""
        return self._bar_shown
