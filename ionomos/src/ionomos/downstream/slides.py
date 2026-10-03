"""
Figures for slides as files, without a browser (D62, D68).

    ionomos export <experiment or results folder>     -> results/figures/*.svg + README.txt
    ionomos export ... --list                         -> the figures this report can draw, and what can be chosen
    ionomos export ... --figures dose,volcano_A*      -> kinds, groups or figure names (* and ? allowed)
    ionomos export ... --features EGFR,BTK --top 9    -> what the curve, profile and site figures show
    ionomos export ... --format png | both            -> PNG too, drawn by a renderer the computer has (raster.py)
    analysis.export.figures: [volcano, pca, dose]     -> the same after every analysis (the watcher; SVG only)

The figures are drawn by charts.figures() / charts.catalog() from the data the report carries (the JSON inside
results/report.html), so they show the numbers the report shows and nothing is analysed again. The style is
the report's export style: charts.STYLE_DEFAULTS, then the lab's analysis.export, a style file saved from
the report (--style), the command line.

PNG needs a program that draws SVG (raster.py: cairosvg, resvg, rsvg-convert or Inkscape); Ionomos adds no
dependency for it. Without one, PNG is refused with what to install, and nothing is written.

Nothing is deleted. A file of the same name is replaced only when Ionomos wrote it (its figures and README
say so inside); anything else in the folder is left alone and the new file gets another name.
"""
from __future__ import annotations

import fnmatch
import json
import re
from datetime import datetime
from pathlib import Path

from ionomos.downstream import charts, raster

FOLDER = "figures"
README = "README.txt"
_DATA = re.compile(r"<script id='ionomos-data' type='application/json'>(.*?)</script>", re.DOTALL)
_OURS = ("Export style:", "Figures from the Ionomos report")  # what an Ionomos figure / README carries inside


class SlidesError(ValueError):
    pass


def find_report(target: Path) -> Path:
    """report.html for an experiment folder, its results folder, or the file itself."""
    target = Path(target)
    for p in (target, target / "report.html", target / "results" / "report.html"):
        if p.is_file() and p.suffix.lower() in (".html", ".htm"):
            return p
    raise SlidesError(f"no report.html in {target} (nor in its results folder): run the analysis first "
                      "(ionomos analyze), or give the folder that holds report.html")


def read_payload(report: Path) -> dict:
    """The data a report carries (report.payload). Raises SlidesError when the page has none."""
    try:
        html = Path(report).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SlidesError(f"cannot read {report}: {exc}") from exc
    m = _DATA.search(html)
    if not m:
        raise SlidesError(f"{report} carries no data: it is the simplified report (the full one could not be made) "
                          "or not an Ionomos report. Re-run the analysis, then export again")
    try:
        d = json.loads(m.group(1))
    except ValueError as exc:
        raise SlidesError(f"the data in {report} cannot be read ({exc})") from exc
    if not isinstance(d, dict) or not isinstance(d.get("f"), dict) or not isinstance(d.get("comps"), list):
        raise SlidesError(f"the data in {report} is not a report's data")
    return d


def _ours(path: Path) -> bool:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            head = fh.read(4096)
    except OSError:
        return False
    return any(mark in head for mark in _OURS)


def _target(folder: Path, name: str) -> Path:
    """Where a file goes: its name, unless a file of that name is there that Ionomos did not write."""
    path = folder / name
    k = 2
    while path.exists() and not _ours(path):
        stem, dot, ext = name.rpartition(".")
        path = folder / (f"{stem}_{k}.{ext}" if dot else f"{name}_{k}")
        k += 1
    return path


def readme(d: dict, style: dict, listed: list[tuple[str, str]], generator: str) -> str:
    lines = ["Figures from the Ionomos report", "", f"Experiment:  {charts.clean_text(d.get('title'))}",
             f"Exported:    {datetime.now():%Y-%m-%d %H:%M}", f"Made by:     {generator}",
             f"Source:      {charts.clean_text(d.get('sourceName'))}" if d.get("sourceName") else "",
             "", "Cut-offs (the ones saved with the report; the report itself can show others)",
             f"  {charts.cut_text(d)}", f"  {charts.analysis_text(d)}", "", "Style", f"  {charts.style_text(style)}",
             "", "Files"]
    wide = min(48, max((len(n) for n, _ in listed), default=0))
    lines += [f"  {n.ljust(wide)}  {charts.clean_text(what)}" for n, what in listed]
    lines += ["", "SVG files keep their text as text and can be ungrouped and edited in PowerPoint, Illustrator or Inkscape.",
              "Each figure carries the cut-offs in its file (SVG: <desc>, PNG: the Description field), so a figure on a",
              "slide can be traced back. For the other charts and for other cut-offs, open report.html and use Export."]
    return "\r\n".join(x for x in lines if x is not None) + "\r\n"


def select(d: dict, spec=None, features=None, top: int | None = None) -> list[charts.Figure]:
    """The figures asked for: `spec` is None (every one), or a list of kinds (volcano, dose_curves ...),
    groups (dose, time, liganded), "all", and figure names as --list shows them, with or without .svg,
    * and ? allowed. A name that matches no figure raises SlidesError saying which there are."""
    if spec is None:
        return charts.catalog(d, None, features, top)
    items = [str(x).strip() for x in ([spec] if isinstance(spec, str) else spec) if str(x).strip()]
    kinds, names = [], []
    for x in items:
        low = x.lower()
        if low in ("all", "none") or low in charts.STATIC_FIGURES or low in charts.FIGURE_GROUPS:
            kinds.append(low)
        else:
            names.append(re.sub(r"\.(svg|png)$", "", x, flags=re.I))
    which = charts.style_layer({"figures": kinds})["figures"] if kinds else []
    every = charts.catalog(d, None, features, top)
    stem = {f.name: f.name.rsplit(".", 1)[0] for f in every}
    keep = {f.name for f in every if f.kind in which}
    for n in names:
        hit = [f.name for f in every if fnmatch.fnmatchcase(stem[f.name].lower(), n.lower())]
        if not hit:
            raise SlidesError(f"no figure {n!r} in this report; its figures: {', '.join(stem.values()) or 'none'} "
                              f"(ionomos export --list says what each is). Kinds: {', '.join(charts.STATIC_FIGURES)}; "
                              f"groups: {', '.join(charts.FIGURE_GROUPS)}")
        keep.update(hit)
    return [f for f in every if f.name in keep]  # in the report's order


def listing(d: dict, figs: list[charts.Figure]) -> str:
    """What --list prints: each figure the report can draw, what it is, and what can be chosen for it."""
    if not figs:
        return ("This report has no figure to export (no comparison, PCA, heatmap, correlation, dose-response, "
                "time course or liganded sites).")
    wide = min(40, max(len(f.name) - 4 for f in figs))
    lines = [f"Figures in {charts.clean_text(d.get('title')) or 'this report'} ({len(figs)}):"]
    for f in figs:
        lines.append(f"  {f.name.rsplit('.', 1)[0].ljust(wide)}  {charts.clean_text(f.what)}")
        if f.choose:
            lines.append(f"  {''.ljust(wide)}    choose: {f.choose}")
        lines += [f"  {''.ljust(wide)}    note: {n}" for n in f.notes]
    lines += ["", "Pick with --figures: kinds (" + ", ".join(charts.STATIC_FIGURES) + "), groups ("
              + ", ".join(charts.FIGURE_GROUPS) + ") or names from this list (* and ? allowed), comma-separated."]
    return "\n".join(lines)


def write(folder: Path, d: dict, style: dict, which=None, generator: str = "", features=None, top: int | None = None,
          formats=("svg",), renderer: raster.Renderer | None = None, figs: list[charts.Figure] | None = None) -> list[Path]:
    """Write the figures asked for (default: every one the report can draw; `figs` from select() instead of
    `which`) and a README into `folder`; returns the paths. `formats`: svg and / or png; png needs `renderer`,
    and every PNG is made before anything is written, so a renderer that fails leaves the folder as it was.
    `generator` is the "Made by ..." line each file carries."""
    folder = Path(folder)
    stamp = f"Made by {generator}, {datetime.now():%Y-%m-%d %H:%M}" if generator else ""
    if figs is None:
        figs = charts.catalog(d, which, features, top)
    made = [(f.name, f.what, svg) for f in figs for svg in [f.draw(style, stamp)] if svg]
    if not made:
        return []
    if "png" in formats and renderer is None:
        raise raster.NoRenderer(raster.HOW)
    files: list[tuple[str, str, str | bytes]] = []
    for name, what, svg in made:
        if "svg" in formats:
            files.append((name, what, svg))
        if "png" in formats:
            files.append((name[:-4] + ".png", what, raster.to_png(svg, style, renderer)))
    folder.mkdir(parents=True, exist_ok=True)
    out, listed = [], []
    for name, what, data in files:
        path = _target(folder, name)
        if isinstance(data, bytes):
            path.write_bytes(data)
        else:
            path.write_text(data, encoding="utf-8", newline="\n")
        out.append(path)
        listed.append((path.name, what))
    path = _target(folder, README)
    path.write_text("﻿" + readme(d, style, listed, generator or "Ionomos"), encoding="utf-8", newline="")
    out.append(path)
    return out
