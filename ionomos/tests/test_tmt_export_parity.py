"""#178: sectionfigs.py's TMT QC figures carry the words tests/js/tmt_parity.json expects.

The report's own Export of the same figures (report.js composeFigure) is checked against the same file by
tests/js/test/tmtexport.test.mjs, so `ionomos export --figures tmt` and the report's Export button agree: the
experiment and the chart's own line in the footer (no hits, no statistics), no "<what>: <what>" title, a channel
chart named by its plex.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from ionomos.downstream import charts

HERE = Path(__file__).resolve().parent
PARITY = json.loads((HERE / "js" / "tmt_parity.json").read_text(encoding="utf-8"))
NS = "{http://www.w3.org/2000/svg}"


def _payload() -> dict:
    html = (HERE / "js" / "fixture.html").read_text(encoding="utf-8")
    m = re.search(r"<script id='ionomos-data' type='application/json'>(.*?)</script>", html, re.S)
    d = json.loads(m.group(1).replace("<\\u0021--", "<!--"))
    d["title"] = PARITY["title"]
    d["tmtqc"] = PARITY["tmtqc"]
    return d


def words(svg: str) -> dict:
    """What a composed figure says around its chart: <title>, the title and subtitle lines, the footer, <desc>."""
    root = ET.fromstring(svg.split("?>", 1)[1] if svg.startswith("<?xml") else svg)
    kids = list(root)
    at = next(k for k, e in enumerate(kids) if e.tag == NS + "g")
    before = [e.text or "" for e in kids[:at] if e.tag == NS + "text"]
    after = [e.text or "" for e in kids[at + 1:] if e.tag == NS + "text"]
    return {"svg_title": root.find(NS + "title").text, "title": before[0], "subtitle": before[1],
            "note": after[-1] if after else "", "desc": (root.find(NS + "desc").text or "").split("\n")}


FIGS = {name.removesuffix(".svg"): svg for name, _what, svg in charts.figures(_payload(), charts.style_from(), ["tmt"])}


@pytest.mark.parametrize("name", sorted(PARITY["expect"]))
def test_sectionfigs_tmt_words_match_the_report(name):
    want = PARITY["expect"][name]
    got = words(FIGS[name])
    assert {k: got[k] for k in ("svg_title", "title", "subtitle", "note")} == {k: want[k] for k in ("svg_title", "title", "subtitle", "note")}
    assert got["desc"][: len(want["desc"])] == want["desc"]
    for bad in PARITY["never"]:
        assert bad not in FIGS[name].split("<g", 1)[0] + FIGS[name].rsplit("</g>", 1)[-1], (name, bad)


def test_every_tmt_figure_is_compared():
    assert sorted(FIGS) == sorted(PARITY["expect"])
