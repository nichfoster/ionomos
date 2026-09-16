"""`python -m labwatch` and the PyInstaller entry point.

Wraps the CLI so a frozen exe can never die silently: any uncaught error is
written to LabWatch-crash.txt next to the exe, shown in a dialog when there
is no console, and a console launched by double-click waits for a key press.
"""
import multiprocessing
import os
import sys
import traceback
from pathlib import Path


def _crash_file() -> Path:
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path.cwd()
    return base / "LabWatch-crash.txt"


def _launched_by_double_click() -> bool:
    """Windows: true when this process is the only one attached to its console (Explorer started it)."""
    if os.name != "nt" or sys.stdout is None:
        return False
    try:
        import ctypes

        buf = (ctypes.c_uint * 2)()
        n = ctypes.windll.kernel32.GetConsoleProcessList(buf, 2)
        return n == 1
    except Exception:  # noqa: BLE001
        return False


def _report(exc: BaseException) -> None:
    text = "LabWatch crashed.\n\n" + "".join(traceback.format_exception(exc))
    text += f"\npython {sys.version.split()[0]}  exe={sys.executable}  argv={sys.argv[1:]}\n"
    where = None
    try:
        where = _crash_file()
        where.write_text(text, encoding="utf-8")
    except OSError:
        where = None
    if sys.stderr is not None:
        print(text, file=sys.stderr)
        if where:
            print(f"(saved to {where})", file=sys.stderr)
    else:  # windowed exe: the only way to be seen is a dialog
        try:
            import tkinter as tk
            from tkinter import messagebox

            r = tk.Tk()
            r.withdraw()
            messagebox.showerror("LabWatch crashed", text[-1500:] + (f"\n\nSaved to {where}" if where else ""))
            r.destroy()
        except Exception:  # noqa: BLE001 - tkinter itself may be what is broken
            pass


def run() -> int:
    from labwatch.cli import main

    try:
        return main(sys.argv[1:])
    except SystemExit as exc:  # argparse / config errors already printed their message
        c = exc.code
        return c if isinstance(c, int) else (0 if c is None else 1)
    except KeyboardInterrupt:
        return 130
    except BaseException as exc:  # noqa: BLE001
        _report(exc)
        return 70


if __name__ == "__main__":
    multiprocessing.freeze_support()
    code = run()
    if code != 0 and _launched_by_double_click():
        try:
            input("\nPress Enter to close this window...")
        except EOFError:
            pass
    raise SystemExit(code)
