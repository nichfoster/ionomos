"""
Figures for slides as files, without a browser (D62).

    ionomos export <experiment or results folder>     -> results/figures/*.svg + README.txt
    analysis.export.figures: [volcano, pca]           -> the same after every analysis (the watcher)

The figures are drawn by charts.figures() from the data the report carries (the JSON inside
results/report.html), so they show the numbers the report shows and nothing is analysed again. The style is
the report's export style: charts.STYLE_DEFAULTS, then the lab's analysis.export, a style file saved from
the report (--style), the command line.

SVG only: turning SVG into PNG needs a renderer, and Ionomos adds no dependency for it. For PNG, open the
report and use Export (the browser draws it), or open the SVG in PowerPoint / Inkscape.

Nothing is deleted. A file of the same name is replaced only when Ionomos wrote it (its figures and README
say so inside); anything else in the folder is left alone and the new file gets another name.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from ionomos.downstream import charts

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
              "Each figure carries the cut-offs in its file (<desc>), so a figure on a slide can be traced back.",
              "For PNG, for the other charts and for other cut-offs, open report.html and use Export."]
    return "\r\n".join(x for x in lines if x is not None) + "\r\n"


def write(folder: Path, d: dict, style: dict, which=None, generator: str = "") -> list[Path]:
    """Write the figures asked for (default: every static one) and a README into `folder`; returns the paths.
    `generator` is the "Made by ..." line each file carries."""
    folder = Path(folder)
    made = charts.figures(d, style, which, f"Made by {generator}, {datetime.now():%Y-%m-%d %H:%M}" if generator else "")
    if not made:
        return []
    folder.mkdir(parents=True, exist_ok=True)
    out, listed = [], []
    for name, what, svg in made:
        path = _target(folder, name)
        path.write_text(svg, encoding="utf-8", newline="\n")
        out.append(path)
        listed.append((path.name, what))
    path = _target(folder, README)
    path.write_text("﻿" + readme(d, style, listed, generator or "Ionomos"), encoding="utf-8", newline="")
    out.append(path)
    return out
