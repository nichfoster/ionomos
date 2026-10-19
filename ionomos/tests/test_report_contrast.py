"""WCAG AA contrast of the report's text inks (#177).

Every text colour the report, help.html and the static charts use reaches 4.5:1 on the backgrounds it is
drawn on, in light and dark mode. The ratios are computed from the CSS itself (report.css, help's PAGE_CSS,
charts.py's tokens), so a palette edit that drops a word below AA fails here. The chart marks (points, bars,
lines) keep the palette colours: only words take the darker text-only inks (--upink, --okink, --c3ink, --link,
--vz-tick).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from ionomos.downstream import charts
from ionomos.help import PAGE_CSS

REPORT_CSS = (Path(__file__).resolve().parent.parent / "src" / "ionomos" / "downstream" / "assets"
              / "report.css").read_text(encoding="utf-8")
AA = 4.5


def _rgb(c: str) -> tuple[float, float, float]:
    h = c.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(x * 2 for x in h)
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _lum(c: tuple[float, float, float]) -> float:
    lin = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(fg, bg) -> float:
    a, b = _lum(fg if isinstance(fg, tuple) else _rgb(fg)), _lum(bg if isinstance(bg, tuple) else _rgb(bg))
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def _tokens(block: str) -> dict[str, str]:
    return dict(re.findall(r"(--[\w-]+):\s*([^;}\s]+)", block))


def _report_themes() -> dict[str, dict[str, str]]:
    light = _tokens(re.search(r":root\{color-scheme:light;(.*?)\}", REPORT_CSS, re.S).group(1))
    media = _tokens(re.search(r'prefers-color-scheme: dark\)\{:root:where\(:not\(\[data-theme="light"\]\)\)\{(.*?)\}\}',
                              REPORT_CSS, re.S).group(1))
    forced = _tokens(re.search(r':root\[data-theme="dark"\]\{(.*?)\}', REPORT_CSS, re.S).group(1))
    assert media == forced, "the dark theme by the system and by the switch must be the same tokens"
    return {"light": light, "dark": forced}


THEMES = _report_themes()


def _rule(selector: str) -> str:
    m = re.search(r"(?:^|[}\s])" + re.escape(selector) + r"\{([^}]*)\}", REPORT_CSS, re.M)
    assert m, f"no rule for {selector} in report.css"
    return m.group(1)


def _ink(selector: str, theme: dict[str, str]) -> tuple[float, float, float]:
    """The colour a rule gives its words, resolved through the theme's tokens."""
    m = re.search(r"(?<![-\w])color:\s*var\((--[\w-]+)\)", _rule(selector))
    assert m, f"{selector} sets no color: var(--...)"
    return _rgb(theme[m.group(1)])


def _over(rule_bg: str, base: str) -> tuple[float, float, float]:
    """A translucent rgba() background as it shows over `base`."""
    r, g, b, a = (float(x) for x in re.fullmatch(r"rgba\(([\d.]+),([\d.]+),([\d.]+),([\d.]+)\)", rule_bg).groups())
    return tuple(f / 255 * a + bb * (1 - a) for f, bb in zip((r, g, b), _rgb(base), strict=True))


def _bg(selector: str, theme: dict[str, str]):
    m = re.search(r"background:\s*([^;]+)", _rule(selector))
    v = m.group(1).strip()
    if v.startswith("var("):
        return _rgb(theme[v[4:-1]])
    return _over(v, theme["--surface"])


# (what, selector whose color: is the ink, backgrounds the words sit on: tokens, or "own" for the rule's own)
REPORT_TEXT = [
    ("not found: … in the search line", ".warnink", ["--surface", "--page"]),
    ("a flagged QC cell", "td.zbad", ["--surface", "--sunk"]),
    ("a QC cell to look at", "td.zwarn", ["--surface", "--sunk"]),
    ("the fail pill", ".pill.fail", ["own"]),
    ("the ok pill", ".pill.ok", ["own"]),
    ("the warn pill", ".pill.warn", ["own"]),
    ("a 1-peptide badge", ".badge.pep", ["--sunk", "--surface"]),
    ("an only-in-one-condition badge", ".badge.oo", ["--sunk", "--surface"]),
    ("a gene-set term badge", ".badge.term", ["--sunk", "--surface"]),
    ("an imputed badge", ".badge.imp", ["own"]),
    ("muted words", ".muted", ["--surface", "--page", "--sunk"]),
    ("a link", "a", ["--surface", "--page"]),
    ("the notes box", ".notes", ["own"]),
    ("a table description", "td.desc", ["--surface", "--sunk"]),
]


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize(("what", "selector", "bgs"), REPORT_TEXT, ids=[r[1] for r in REPORT_TEXT])
def test_report_text_reaches_aa(theme, what, selector, bgs):
    T = THEMES[theme]
    ink = _ink(selector, T)
    for bg in bgs:
        back = _bg(selector, T) if bg == "own" else _rgb(T[bg])
        ratio = contrast(ink, back)
        assert ratio >= AA, f"{what} ({selector}, {theme}) on {bg}: {ratio:.2f}:1 is below {AA}:1"


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_marks_keep_the_palette(theme):
    """Only the words changed: points, the up/down dots and the pill tints keep the palette colours."""
    T = THEMES[theme]
    want = {"light": {"--up": "#e34948", "--c2": "#1baf7a", "--c3": "#eda100", "--accent": "#2a78d6"},
            "dark": {"--up": "#e66767", "--c2": "#199e70", "--c3": "#c98500", "--accent": "#3987e5"}}[theme]
    assert {k: T[k] for k in want} == want
    assert "rgba(227,73,72,.15)" in _rule(".pill.fail") and "rgba(27,175,122,.16)" in _rule(".pill.ok")
    assert ".dot.up{background:var(--up)}" in REPORT_CSS
    assert "border-color:var(--up)" in _rule(".badge.pep")


def test_the_old_numbers_were_below_aa():
    """The ratios the issue measured, so the helper is known to compute WCAG's formula."""
    assert round(contrast("#e34948", "#fcfcfb"), 2) == 3.85
    assert contrast("#898781", "#fcfcfb") < AA <= contrast("#6f6d68", "#fcfcfb")


@pytest.mark.parametrize("dark", [False, True])
def test_help_page_text_reaches_aa(dark):
    blocks = re.findall(r":root\{color-scheme:\w+;(.*?)\}", PAGE_CSS, re.S)
    T = _tokens(blocks[1 if dark else 0])
    for ink in ("--text", "--text2", "--muted", "--link"):
        for bg in ("--page", "--surface"):
            ratio = contrast(T[ink], T[bg])
            assert ratio >= AA, f"help.html {ink} on {bg} ({'dark' if dark else 'light'}): {ratio:.2f}:1"
    assert re.search(r"(?<![-\w])a\{color:var\(--link\)\}", PAGE_CSS)
    assert ".meta{color:var(--muted)" in PAGE_CSS


@pytest.mark.parametrize("dark", [False, True])
def test_static_chart_text_reaches_aa(dark):
    T = _tokens(charts._TOKENS_DARK if dark else charts._TOKENS_LIGHT)
    for ink in ("--vz-text", "--vz-text2", "--vz-tick", "--vz-warn"):
        ratio = contrast(T[ink], T["--vz-surface"])
        assert ratio >= AA, f"charts.py {ink} ({'dark' if dark else 'light'}): {ratio:.2f}:1"
    assert ".vz .tick{fill:var(--vz-tick)" in charts.STYLE
    # the threshold lines and whiskers are marks: they keep the old grey
    assert T["--vz-muted"] == "#898781"
    assert ".vz .thr{stroke:var(--vz-muted)" in charts.STYLE
