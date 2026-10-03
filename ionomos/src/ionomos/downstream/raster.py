"""
PNG from the figures' SVG, with a renderer the computer already has (D68). Ionomos itself stays pure Python.

    r = find()                         # the first renderer found, or None
    png = to_png(svg, style, r)        # bytes: the pixel size of the style, with its print size and description

The renderers tried, in order (each draws text with the computer's own fonts):
    cairosvg       the Python package, when it can be imported (pip install cairosvg; it needs the Cairo library)
    resvg          the resvg program (one file, Windows / macOS / Linux: github.com/linebender/resvg)
    rsvg-convert   librsvg's program (Linux, Homebrew)
    inkscape       Inkscape 1.x (also found in its usual install folder on Windows and macOS)
None found: NoRenderer, which says what to install. Nothing is downloaded or installed here.

The PNG gets the size the report's export gives it (report.js pngScale: png_dpi / 96 or png_scale times the
figure's size in px, at most 16,000 px a side), and the two chunks the report writes (pngMeta): pHYs, so 300 dpi
is 300 dpi in a layout program, and an iTXt "Description" with the figure's <desc> (experiment, cut-offs).
"""
from __future__ import annotations

import importlib
import os
import re
import shutil
import struct
import subprocess
import tempfile
import zlib
from dataclasses import dataclass
from html import unescape
from pathlib import Path

RENDERERS = ("cairosvg", "resvg", "rsvg-convert", "inkscape")
MAX_SIDE = 16000
TIMEOUT = 120  # seconds per figure
_SIG = b"\x89PNG\r\n\x1a\n"
HOW = ("PNG needs a program that draws SVG, and none was found. Install one of: resvg (one file, put it on "
       "PATH: https://github.com/linebender/resvg/releases), Inkscape (https://inkscape.org), or the Python package "
       "cairosvg (pip install cairosvg; it needs the Cairo library). Or open report.html and use Export: the browser "
       "makes the PNG. The SVG files open in PowerPoint and Inkscape as they are.")


class NoRenderer(RuntimeError):
    pass


class RasterError(RuntimeError):
    pass


@dataclass
class Renderer:
    name: str
    path: str = ""  # the program; "" for cairosvg (a module)

    def __str__(self) -> str:
        return self.name + (f" ({self.path})" if self.path else "")


def _inkscape_places() -> list[Path]:
    out = []
    for var in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(var)
        if base:
            out.append(Path(base) / "Inkscape" / "bin" / "inkscape.exe")
            out.append(Path(base) / "Programs" / "Inkscape" / "bin" / "inkscape.exe")
    out.append(Path("/Applications/Inkscape.app/Contents/MacOS/inkscape"))
    return out


def _cairosvg():
    try:
        return importlib.import_module("cairosvg")
    except (ImportError, OSError):  # cairosvg raises OSError when the Cairo library itself is missing
        return None


def find(prefer: str = "auto") -> Renderer | None:
    """The renderer to use: `prefer` if it is there (None if not), or the first of RENDERERS found."""
    for name in (RENDERERS if prefer in ("", "auto", None) else (prefer,)):
        if name == "cairosvg":
            if _cairosvg() is not None:
                return Renderer("cairosvg")
            continue
        path = shutil.which(name)
        if not path and name == "inkscape":
            path = next((str(p) for p in _inkscape_places() if p.is_file()), None)
        if path:
            return Renderer(name, path)
    return None


def svg_size(svg: str) -> tuple[float, float]:
    """The figure's size in px at 96 per inch, from its width / height (px or mm)."""
    head = svg[: svg.find(">", svg.find("<svg")) + 1]

    def one(attr: str) -> float:
        m = re.search(rf'\s{attr}="([0-9.]+)(mm|px)?"', head)
        if not m:
            raise RasterError(f"the figure has no {attr}")
        v = float(m.group(1))
        return v * 96 / 25.4 if m.group(2) == "mm" else v

    return one("width"), one("height")


def png_scale(style: dict, w: float, h: float) -> tuple[float, float]:
    """(scale, dots per inch) of the PNG for a figure of w x h px (report.js pngScale)."""
    k = min(style["png_dpi"] / 96 if style.get("png_dpi") else style.get("png_scale", 2), MAX_SIDE / max(w, h))
    return k, 96 * k


def description(svg: str) -> str:
    m = re.search(r"<desc>(.*?)</desc>", svg, re.S)
    return unescape(m.group(1)) if m else ""


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def png_meta(png: bytes, dpi: float, desc: str) -> bytes:
    """The PNG with pHYs (its print size) and an iTXt Description after the header, any pHYs it had dropped
    (report.js pngMeta)."""
    if len(png) < 45 or not png.startswith(_SIG):
        raise RasterError("the renderer did not write a PNG")
    ppm = round(dpi / 0.0254)
    parts = [png[:33], _chunk(b"pHYs", struct.pack(">IIB", ppm, ppm, 1)),
             _chunk(b"iTXt", b"Description\x00\x00\x00\x00\x00" + desc.encode("utf-8"))]
    p = 33
    while p + 12 <= len(png):
        n = struct.unpack(">I", png[p:p + 4])[0]
        if png[p + 4:p + 8] != b"pHYs":
            parts.append(png[p:p + 12 + n])
        p += 12 + n
    return b"".join(parts)


def png_dimensions(png: bytes) -> tuple[int, int]:
    if not png.startswith(_SIG) or png[12:16] != b"IHDR":
        raise RasterError("not a PNG")
    return struct.unpack(">II", png[16:24])


def _command(r: Renderer, src: Path, dst: Path, w: int, h: int) -> list[str]:
    if r.name == "resvg":
        return [r.path, "--width", str(w), "--height", str(h), str(src), str(dst)]
    if r.name == "rsvg-convert":
        return [r.path, "--width", str(w), "--height", str(h), "--format", "png", "--output", str(dst), str(src)]
    if r.name == "inkscape":
        return [r.path, str(src), "--export-type=png", f"--export-filename={dst}", f"--export-width={w}",
                f"--export-height={h}"]
    raise RasterError(f"unknown renderer {r.name}")


def render(r: Renderer, svg: str, w: int, h: int) -> bytes:
    """The SVG as a w x h PNG, drawn by r. Raises RasterError with what the renderer said."""
    if r.name == "cairosvg":
        mod = _cairosvg()
        if mod is None:
            raise RasterError("cairosvg cannot be imported")
        try:
            return mod.svg2png(bytestring=svg.encode("utf-8"), output_width=w, output_height=h)
        except Exception as exc:  # noqa: BLE001 - a third-party renderer: any failure is reported, never raised raw
            raise RasterError(f"cairosvg could not draw the figure: {exc}") from exc
    with tempfile.TemporaryDirectory(prefix="ionomos-png-") as td:
        src, dst = Path(td) / "figure.svg", Path(td) / "figure.png"
        src.write_text(svg, encoding="utf-8")
        try:
            done = subprocess.run(_command(r, src, dst, w, h), capture_output=True, timeout=TIMEOUT, check=False,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RasterError(f"{r} could not be run: {exc}") from exc
        if done.returncode != 0 or not dst.is_file():
            said = (done.stderr or done.stdout or b"").decode("utf-8", "replace").strip().splitlines()[-3:]
            raise RasterError(f"{r} failed (exit {done.returncode}): {' '.join(said) or 'no message'}")
        return dst.read_bytes()


def to_png(svg: str, style: dict, r: Renderer) -> bytes:
    """The figure as a PNG at the style's resolution, carrying its print size and its description."""
    w, h = svg_size(svg)
    k, dpi = png_scale(style, w, h)
    png = render(r, svg, max(1, round(w * k)), max(1, round(h * k)))
    return png_meta(png, dpi, description(svg))
