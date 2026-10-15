"""PNG screenshots of every tab, Analysis sub-tab and window of the app, on a testbed (docs/UI.md S4.4, D93).

    python scripts/screenshots.py                 # -> C:/ionomos-screenshots/<timestamp>/
    python scripts/screenshots.py --out shots     # PNGs in ./shots (the testbed still goes under C:/ionomos-screenshots)
    python scripts/screenshots.py --no-small      # only the natural size, not 1366x768 as well

Windows only: the capture is GDI through ctypes (PrintWindow of the window's frame, then GetDIBits) and the PNG is
written with zlib, so nothing new is installed. Elsewhere it says so and exits 0.

The app opens on a new testbed in C:/ionomos-screenshots/<timestamp>/bed (no space in the path: FragPipe's rule,
and usernames can contain one, so never under the profile folder; docs/TESTING.md), with HOME / APPDATA there too,
so the lab's own app data is not touched. The windows are shown on screen (GDI needs them mapped) at +0+0 for a
few seconds, then closed. Run it before a UI pull request and look at the pictures; CI on Windows uploads them as
the `ui-screenshots-<python>` artifact of the tests workflow.
"""
from __future__ import annotations

import argparse
import os
import re
import struct
import sys
import time
import zlib
from datetime import datetime
from pathlib import Path

SETTLE = 0.4  # seconds between showing something and capturing it: Tk draws in idle time


# ------------------------------------------------------------------ PNG and GDI --


def png_bytes(width: int, height: int, rgb: bytes) -> bytes:
    """An 8-bit RGB PNG of rgb (rows top to bottom, 3 bytes a pixel)."""
    stride = width * 3
    raw = b"".join(b"\0" + rgb[y * stride:(y + 1) * stride] for y in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def capture_hwnd(hwnd: int) -> tuple[int, int, bytes]:
    """(width, height, RGB bytes) of a window, frame included, through PrintWindow (BitBlt of the screen if
    PrintWindow fails)."""
    import ctypes
    from ctypes import wintypes

    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    user32.GetWindowDC.restype = gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HDC,
                             ctypes.c_int, ctypes.c_int, wintypes.DWORD]
    user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    user32.GetWindowDC.argtypes = [wintypes.HWND]
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
                                ctypes.POINTER(BITMAPINFOHEADER), wintypes.UINT]

    rect = wintypes.RECT()
    if not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
        raise OSError("GetWindowRect failed")
    w, h = rect.right - rect.left, rect.bottom - rect.top
    if w <= 0 or h <= 0:
        raise OSError(f"the window has no size ({w}x{h})")
    wdc = user32.GetWindowDC(hwnd)
    mdc = gdi32.CreateCompatibleDC(wdc)
    bmp = gdi32.CreateCompatibleBitmap(wdc, w, h)
    old = gdi32.SelectObject(mdc, bmp)
    try:
        if not user32.PrintWindow(hwnd, mdc, 2):  # PW_RENDERFULLCONTENT (Windows 8.1+)
            sdc = user32.GetWindowDC(None)
            try:
                gdi32.BitBlt(mdc, 0, 0, w, h, sdc, rect.left, rect.top, 0x00CC0020)  # SRCCOPY
            finally:
                user32.ReleaseDC(None, sdc)
        bih = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=w, biHeight=-h, biPlanes=1,
                               biBitCount=32, biCompression=0)
        buf = ctypes.create_string_buffer(w * h * 4)
        gdi32.SelectObject(mdc, old)  # GetDIBits wants the bitmap not selected into a DC
        if gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bih), 0) != h:
            raise OSError("GetDIBits failed")
    finally:
        gdi32.SelectObject(mdc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mdc)
        user32.ReleaseDC(hwnd, wdc)
    bgra = buf.raw
    # Windows 10/11 draw an invisible resize border around the frame: keep what DWM shows (extended frame bounds,
    # in physical pixels; GetWindowRect is in this process's pixels, which differ when it is not DPI-aware)
    vis = wintypes.RECT()
    x0, y0, cw, ch = 0, 0, w, h
    try:
        if ctypes.windll.dwmapi.DwmGetWindowAttribute(wintypes.HWND(hwnd), 9, ctypes.byref(vis),
                                                      ctypes.sizeof(vis)) == 0:  # DWMWA_EXTENDED_FRAME_BOUNDS
            tl, br = wintypes.POINT(vis.left, vis.top), wintypes.POINT(vis.right, vis.bottom)
            for pt in (tl, br):
                user32.PhysicalToLogicalPointForPerMonitorDPI(wintypes.HWND(hwnd), ctypes.byref(pt))
            x0, y0 = tl.x - rect.left, tl.y - rect.top
            cw, ch = br.x - tl.x, br.y - tl.y
            if not (0 <= x0 and 0 <= y0 and 0 < cw <= w - x0 and 0 < ch <= h - y0):  # another DPI scale: keep all
                x0, y0, cw, ch = 0, 0, w, h
    except (AttributeError, OSError):
        pass
    if (x0, y0, cw, ch) != (0, 0, w, h):
        bgra = b"".join(bgra[(y * w + x0) * 4:(y * w + x0 + cw) * 4] for y in range(y0, y0 + ch))
        w, h = cw, ch
    rgb = bytearray(w * h * 3)
    rgb[0::3], rgb[1::3], rgb[2::3] = bgra[2::4], bgra[1::4], bgra[0::4]
    return w, h, bytes(rgb)


def frame_hwnd(win) -> int:
    """The HWND of a Tk window's frame (title bar and border included)."""
    try:
        return int(win.wm_frame(), 16)
    except Exception:  # noqa: BLE001 - older Tk: the client window's parent
        import ctypes

        return ctypes.windll.user32.GetParent(win.winfo_id())


# --------------------------------------------------------------------- the run --


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower()[:40] or "x"


class Shooter:
    def __init__(self, root, out: Path):
        self.root, self.out, self.n = root, out, 0
        self.saved: list[Path] = []

    def settle(self, win=None) -> None:
        for _ in range(3):
            self.root.update()
            time.sleep(SETTLE / 3)
        if win is not None:
            win.update()

    def shot(self, win, name: str) -> None:
        self.settle(win)
        self.n += 1
        path = self.out / f"{self.n:02d}_{name}.png"
        try:
            w, h, rgb = capture_hwnd(frame_hwnd(win))
        except OSError as exc:
            print(f"  {path.name}: not captured ({exc})")
            return
        path.write_bytes(png_bytes(w, h, rgb))
        self.saved.append(path)
        print(f"  {path.name}  {w}x{h}")


def shoot_tabs(app, s: Shooter, size: str) -> None:
    from ionomos import uinav

    for key, _label, _help in uinav.TABS:
        app.select_tab(key)
        nb = getattr(getattr(app, "analysis", None), "nb", None) if key == "analysis" else None
        if nb is None:
            s.shot(app.root, f"{size}_tab_{key}")
            continue
        for i, t in enumerate(nb.tabs()):  # the first sub-tab is what the Analysis tab opens on
            nb.select(t)
            s.shot(app.root, f"{size}_tab_analysis_{i}_{slug(nb.tab(t, 'text'))}")
        nb.select(0)


def shoot_windows(app, s: Shooter, size: str) -> None:
    import tkinter as tk

    def new_window(before: set) -> tk.Toplevel | None:
        app.root.update()
        fresh = [w for w in _toplevels(app.root) if str(w) not in before]
        return fresh[-1] if fresh else None

    openers = (("attention_list", lambda: app.popups.center()), ("test_names", app.test_names))
    for name, open_ in openers:
        before = {str(w) for w in _toplevels(app.root)}
        try:
            win = open_() or new_window(before)
        except Exception as exc:  # noqa: BLE001 - one window failing must not lose the others
            print(f"  {name}: could not open ({type(exc).__name__}: {exc})")
            continue
        if not isinstance(win, tk.Toplevel):
            win = new_window(before)
        if win is None:
            print(f"  {name}: no window appeared")
            continue
        win.geometry("+0+0")
        win.lift()
        s.shot(win, f"{size}_window_{name}")
        win.destroy()


def _toplevels(root) -> list:
    import tkinter as tk

    out = []

    def walk(w):
        for c in list(w.children.values()):
            if isinstance(c, tk.Toplevel) and c.winfo_exists():
                out.append(c)
            walk(c)

    walk(root)
    return out


def _no_message_boxes() -> None:
    """A message box would wait for a click (and in CI for ever): print it and answer as if dismissed."""
    from tkinter import messagebox

    for name in ("showinfo", "showwarning", "showerror", "askyesno", "askyesnocancel", "askokcancel",
                 "askquestion", "askretrycancel"):
        def box(title=None, message=None, _name=name, **_kw):
            print(f"  [{_name}] {title}: {message}")
            return None if _name.startswith("show") or _name == "askyesnocancel" else False
        setattr(messagebox, name, box)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, help="folder for the PNGs (default: next to the testbed)")
    ap.add_argument("--no-small", action="store_true", help="skip the pass at 1366x768")
    args = ap.parse_args(argv)
    if os.name != "nt":
        print("screenshots need Windows (GDI capture); nothing done")
        return 0

    base = Path("C:/ionomos-screenshots") / datetime.now().strftime("%Y%m%d-%H%M%S")
    bed = base / "bed"
    out = (args.out or base).resolve()
    out.mkdir(parents=True, exist_ok=True)
    home = base / "home"
    home.mkdir(parents=True, exist_ok=True)
    os.environ.update({"HOME": str(home), "APPDATA": str(home), "IONOMOS_OFFLINE": "1"})

    import tkinter as tk

    from ionomos import testbed
    from ionomos.app import App

    cfg = testbed.init(bed)
    print(f"testbed {bed}\nPNGs to {out}")
    _no_message_boxes()
    root = tk.Tk()
    s = Shooter(root, out)
    try:
        app = App(root, cfg)
        root.report_callback_exception = lambda t, e, tb: print(f"  Tk error: {t.__name__}: {e}")
        root.geometry("+0+0")
        root.deiconify()
        root.lift()
        s.settle()
        sizes = [("natural", None)] + ([] if args.no_small else [("1366x768", "1366x768+0+0")])
        for size, geom in sizes:
            if geom:
                root.state("normal")
                root.geometry(geom)
            print(f"{size}:")
            shoot_tabs(app, s, size)
            if size == "natural":  # a window's size does not follow the main window's
                shoot_windows(app, s, size)
        try:
            app.popups.stop()
        except Exception:  # noqa: BLE001
            pass
    finally:
        try:
            tk.Tk.destroy(root)
        except tk.TclError:
            pass
    print(f"{len(s.saved)} PNGs in {out}")
    return 0 if s.saved else 1


if __name__ == "__main__":
    sys.exit(main())
