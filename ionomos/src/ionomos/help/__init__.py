"""
Help for the people who use Ionomos: one source, shown in the report, a help page and the terminal.

    help/*.md                    the content, in plain Markdown: "# Section {#id}", then "## Entry {#id}" per topic
    entries(), sections()        the parsed content, in page order
    page(start=None)             help.html: all of it, self-contained and offline (the app's Help button,
                                 `ionomos help --open`, a pop-up's "More help")
    report_payload(issues)       the part report.html embeds: its sections, QC tabs, the glossary, the issues
                                 found in that report, and every entry those link to
    topic(query)                 "NO_TABLE" / "glossary" / "pca" / "search_failed" -> an entry or section id
    topic_for_item(item)         an attention item (a pop-up) -> the entry that explains it
    text(entry_id)               one entry as plain text for the terminal
    write_page(dir, start) / open_help(start)

Why Markdown files: the content is prose that lab members and the maintainer edit, it reads as-is on
GitHub, and diffs stay readable. The subset below is all the help needs, rendered here with the standard
library (no Markdown package), and everything is HTML-escaped before the markup is applied, so the
content can't inject tags into a page:

    paragraphs, "- " and "1. " lists (continuation lines indented), **bold**, *italic*, `code`,
    [text](#entry.id) to another entry, [text](https://...) to the web

Ids are namespaced by where they are used: start. report. qc. glossary. trouble. attention. intake.
search. issue. safety. faq. The report embeds report.* / qc.* / glossary.*; `issue.<CODE>` explains a
doctor issue code (downstream/doctor.py), `attention.<kind>` an attention kind, `intake.<kind>` an intake
rejection (tests/test_help.py fails when a code, kind or hold reason has no entry). docs/HELP.md says how
to edit it.
"""
from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass, field
from functools import lru_cache
from html import escape
from importlib import resources
from pathlib import Path

FILES = ("getting_started", "reading", "glossary", "troubleshooting", "safety", "faq")  # page order
REPORT_PREFIXES = ("report.", "qc.", "glossary.")
PAGE_NAME = "help.html"

_HEAD = re.compile(r"^(#{1,2}) (.+?)\s*\{#([A-Za-z0-9_.\-]+)\}\s*$")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_CODE = re.compile(r"(`[^`]+`)")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITAL = re.compile(r"(?<![\w*])\*(?![\s*])(.+?)(?<![\s*])\*(?![\w*])")
_ITEM = re.compile(r"^( ?)(-|\d+\.) (.*)$")

# a held search's reason (Hold in fragpipe.py, diann.py, maxquant.py, sage.py) -> the entry that explains it
HOLD_TOPICS = (
    (r"launcher not found", "search.hold-launcher"),
    (r"workflow file for .* missing", "search.hold-workflow"),
    (r"FASTA for .* missing", "search.hold-fasta"),
    (r"low disk space", "search.hold-disk"),
    (r"DIA-NN not found", "search.hold-diann"),
    (r"spectral library", "search.hold-library"),
    (r"MaxQuant not found", "search.hold-maxquant"),
    (r"mqpar .* not in", "search.hold-mqpar"),
    (r"Sage not found", "search.hold-sage"),
    (r"raw file converter not found", "search.hold-converter"),
    (r"Sage settings", "search.hold-sage-config"),
    (r"is not in config\.yaml any more", "search.hold-method"),
)


class HelpError(ValueError):
    pass


@dataclass(frozen=True)
class Entry:
    id: str
    title: str
    body: str          # Markdown
    section: str


@dataclass(frozen=True)
class Section:
    id: str
    title: str
    intro: str         # Markdown
    entries: tuple[str, ...] = field(default_factory=tuple)


# ------------------------------------------------------------------ content --


def _source(name: str) -> str:
    return resources.files("ionomos.help").joinpath(f"{name}.md").read_text(encoding="utf-8")


def parse(text: str, where: str = "help") -> tuple[Section, list[Entry]]:
    """One file: a "# Title {#id}" section, its intro, then "## Title {#id}" entries."""
    sec: tuple[str, str] | None = None
    intro: list[str] = []
    out: list[Entry] = []
    cur: list | None = None  # [id, title, lines]
    for n, line in enumerate(text.splitlines(), 1):
        if line.startswith("#"):
            m = _HEAD.match(line)
            if not m:
                raise HelpError(f"{where}:{n}: a heading needs an id, like '## Title {{#section.name}}'")
            level, title, hid = len(m.group(1)), m.group(2).strip(), m.group(3)
            if level == 1:
                if sec is not None:
                    raise HelpError(f"{where}:{n}: one '# Section' per file")
                sec = (hid, title)
                continue
            if sec is None:
                raise HelpError(f"{where}:{n}: '## {title}' comes before the file's '# Section' heading")
            if cur:
                out.append(Entry(cur[0], cur[1], "\n".join(cur[2]).strip(), sec[0]))
            cur = [hid, title, []]
            continue
        (cur[2] if cur else intro).append(line)
    if sec is None:
        raise HelpError(f"{where}: no '# Section {{#id}}' heading")
    if cur:
        out.append(Entry(cur[0], cur[1], "\n".join(cur[2]).strip(), sec[0]))
    return Section(sec[0], sec[1], "\n".join(intro).strip(), tuple(e.id for e in out)), out


@lru_cache(maxsize=1)
def _content() -> tuple[tuple[Section, ...], dict[str, Entry]]:
    secs, ents = [], {}
    for name in FILES:
        sec, found = parse(_source(name), f"{name}.md")
        secs.append(sec)
        for e in found:
            if e.id in ents:
                raise HelpError(f"{name}.md: the id {e.id!r} is used twice")
            ents[e.id] = e
    return tuple(secs), ents


def sections() -> tuple[Section, ...]:
    return _content()[0]


def entries() -> dict[str, Entry]:
    return _content()[1]


def links(md: str) -> list[str]:
    """The entry / section ids a piece of Markdown links to."""
    return [t[1:] for _txt, t in _LINK.findall(md) if t.startswith("#")]


def check() -> list[str]:
    """Problems a writer should fix: links to ids that don't exist, a section id reused as an entry id."""
    secs = {s.id for s in sections()}
    ents = entries()
    out = [f"{i}: also a section id" for i in ents if i in secs]
    for s in sections():
        out += [f"{s.id} (intro): link to unknown #{t}" for t in links(s.intro) if t not in ents and t not in secs]
    for e in ents.values():
        out += [f"{e.id}: link to unknown #{t}" for t in links(e.body) if t not in ents and t not in secs]
    return out


# ---------------------------------------------------------------- rendering --


def _anchor(hid: str, inner: str) -> str:
    return f'<a href="#{escape(hid)}" data-help="{escape(hid)}">{inner}</a>'


def _inline(s: str, link=None) -> str:
    """Escape, then apply `code`, links, **bold** and *italic*. link(id, inner_html) renders #id links."""
    link = link or _anchor
    codes: list[str] = []

    def keep(m) -> str:  # code spans are literal: set aside, so **`x`** and [`x`](#id) still work
        codes.append(f"<code>{escape(m.group(1)[1:-1])}</code>")
        return f"\x00{len(codes) - 1}\x00"

    s = _CODE.sub(keep, s.replace("\x00", ""))
    out, pos = [], 0
    for m in _LINK.finditer(s):
        out.append(_emph(escape(s[pos:m.start()])))
        txt, target = _emph(escape(m.group(1))), m.group(2)
        if target.startswith("#"):
            out.append(link(target[1:], txt))
        elif re.match(r"https?://", target):
            out.append(f'<a href="{escape(target)}" target="_blank" rel="noopener">{txt}</a>')
        else:  # nothing else is a link (no javascript:, no local files)
            out.append(txt)
        pos = m.end()
    out.append(_emph(escape(s[pos:])))
    return re.sub("\x00(\\d+)\x00", lambda m: codes[int(m.group(1))], "".join(out))


def _emph(s: str) -> str:
    return _ITAL.sub(r"<i>\1</i>", _BOLD.sub(r"<b>\1</b>", s))


def _blocks(md: str) -> list[tuple[str, list]]:
    """[("p", [lines]) | ("ul"/"ol", [[item lines], ...])]."""
    blocks: list[tuple[str, list]] = []
    cur: tuple[str, list] | None = None
    for line in md.splitlines():
        if not line.strip():
            cur = None
            continue
        m = _ITEM.match(line)
        if m:
            kind = "ul" if m.group(2) == "-" else "ol"
            if cur is None or cur[0] != kind:
                cur = (kind, [])
                blocks.append(cur)
            cur[1].append([m.group(3).strip()])
        elif cur is not None and cur[0] != "p" and line.startswith("  "):
            cur[1][-1].append(line.strip())
        else:
            if cur is None or cur[0] != "p":
                cur = ("p", [])
                blocks.append(cur)
            cur[1].append(line.strip())
    return blocks


def to_html(md: str, link=None) -> str:
    out = []
    for kind, body in _blocks(md):
        if kind == "p":
            out.append(f"<p>{_inline(' '.join(body), link)}</p>")
        else:
            out.append(f"<{kind}>" + "".join(f"<li>{_inline(' '.join(it), link)}</li>" for it in body) + f"</{kind}>")
    return "".join(out)


def _plain(s: str) -> str:
    s = _LINK.sub(lambda m: m.group(1) if m.group(2).startswith("#") else f"{m.group(1)} ({m.group(2)})", s)
    s = _BOLD.sub(r"\1", s)
    s = _ITAL.sub(r"\1", s)
    return s.replace("`", "")


def to_text(md: str, width: int = 88) -> str:
    out = []
    for kind, body in _blocks(md):
        if kind == "p":
            out.append(textwrap.fill(_plain(" ".join(body)), width))
        else:
            for k, it in enumerate(body, 1):
                lead = "  - " if kind == "ul" else f"  {k}. "
                out.append(textwrap.fill(_plain(" ".join(it)), width, initial_indent=lead,
                                         subsequent_indent=" " * len(lead)))
    return "\n\n".join(out)


def text(entry_id: str) -> str:
    e = entries().get(entry_id)
    if e is not None:
        return f"{e.title}\n{'=' * len(e.title)}\n\n{to_text(e.body)}"
    s = next((x for x in sections() if x.id == entry_id), None)
    if s is None:
        return ""
    lines = [s.title, "=" * len(s.title), ""] + ([to_text(s.intro), ""] if s.intro else [])
    return "\n".join(lines + [f"  {entries()[i].title}   (ionomos help {i})" for i in s.entries])


# ------------------------------------------------------------------- topics --


def issue_entry(code: str) -> str | None:
    """The entry for a doctor issue code (CRASH_<STAGE> share one)."""
    code = (code or "").upper()
    hid = "issue.CRASH" if code.startswith("CRASH_") else f"issue.{code}"
    return hid if hid in entries() else None


def topic(query: str | None) -> str | None:
    """An entry or section id for what someone typed: an id, an issue code, a kind, a word from a title."""
    q = (query or "").strip().strip("#")
    if not q:
        return None
    ents, secs = entries(), {s.id for s in sections()}
    if q in ents or q in secs:
        return q
    low = q.lower()
    code = issue_entry(q)
    if code:
        return code
    for ns in ("attention", "intake", "glossary", "qc", "report", "faq", "start", "search", "safety", "trouble"):
        if f"{ns}.{low}" in ents:
            return f"{ns}.{low}"
    tail = [i for i in ents if i.split(".", 1)[-1].lower() == low]
    if tail:
        return tail[0]
    for s in sections():
        if low == s.title.lower() or low == s.id.lower():
            return s.id
    for i, e in ents.items():
        if low in e.title.lower():
            return i
    return None


def hold_topic(reason: str) -> str | None:
    for rx, hid in HOLD_TOPICS:
        if re.search(rx, reason or "", re.IGNORECASE):
            return hid
    return None


def topic_for_item(item) -> str:
    """The entry that explains an attention item (attention.Item or a dict with kind / message / data)."""
    get = (lambda k, d=None: item.get(k, d)) if isinstance(item, dict) else (lambda k, d=None: getattr(item, k, d))
    kind = get("kind") or ""
    if kind.startswith("analysis"):
        issues = (get("data") or {}).get("issues") or []
        order = {"error": 0, "input": 1, "warning": 2}
        for i in sorted(issues, key=lambda x: order.get(x.get("severity"), 3)):
            hid = issue_entry(i.get("code", ""))
            if hid:
                return hid
    if kind == "search_waiting":
        hid = hold_topic(get("message") or "")
        if hid:
            return hid
    hid = f"attention.{kind}"
    return hid if hid in entries() else "trouble"


# ------------------------------------------------------------------- report --


def report_payload(issues: list[dict] | None = None) -> dict:
    """What report.html embeds: every report / QC / glossary entry, the entries for this report's issues,
    and every entry those link to (so each help link in the page resolves), rendered to safe HTML."""
    ents = entries()
    found = []
    for i in issues or []:
        hid = issue_entry(i.get("code", ""))
        found.append({"code": str(i.get("code", "")), "sev": str(i.get("severity", "")),
                      "title": str(i.get("title", "")), "id": hid})
    want = [i for i in ents if i.startswith(REPORT_PREFIXES)]
    want += [x["id"] for x in found if x["id"] and x["id"] not in want]
    seen, todo = set(want), list(want)
    while todo:
        for t in links(ents[todo.pop()].body):
            if t in ents and t not in seen:
                seen.add(t)
                want.append(t)
                todo.append(t)

    def link(hid, inner):  # in the report an entry lives at #help-<id>; a section of the full help is plain text
        return f'<a href="#help-{escape(hid)}" data-help="{escape(hid)}">{inner}</a>' if hid in seen else inner

    return {"entries": {i: {"t": ents[i].title, "h": to_html(ents[i].body, link), "s": ents[i].section} for i in want},
            "report": [i for i in want if i.startswith(("report.", "qc."))],
            "glossary": [i for i in want if i.startswith("glossary.")],
            "more": [i for i in want if not i.startswith(REPORT_PREFIXES) and i not in {x["id"] for x in found}],
            "issues": found}


# --------------------------------------------------------------------- page --


def _generated(entry_id: str) -> str:
    """Lists made from the code, so the page can't fall behind it."""
    if entry_id == "search.failed":
        try:
            from ionomos.fragpipe import EXPLANATIONS
        except Exception:  # noqa: BLE001 - the page must render even if this import breaks
            return ""
        return ("<p>What FragPipe's log said, and what to do (the same words as <code>FAILED.txt</code>):</p><ul>"
                + "".join(f"<li>{escape(msg)}</li>" for _rx, msg in EXPLANATIONS) + "</ul>")
    return ""


PAGE_CSS = """
:root{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--sunk:#f1f0ec;--text:#0b0b0b;--text2:#52514e;--muted:#898781;
--line:#e1e0d9;--accent:#2a78d6}
@media (prefers-color-scheme: dark){:root{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--sunk:#141413;--text:#fff;
--text2:#c3c2b7;--muted:#898781;--line:#2c2c2a;--accent:#3987e5}}
*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:70px}
body{margin:0;background:var(--page);color:var(--text);font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
header{position:sticky;top:0;background:var(--page);border-bottom:1px solid var(--line);padding:10px 20px;z-index:2;
display:flex;gap:14px;align-items:center;flex-wrap:wrap}
header h1{font-size:19px;margin:0;font-weight:650}
header input{font:inherit;color:var(--text);background:var(--surface);border:1px solid var(--line);border-radius:8px;
padding:5px 10px;min-width:240px}
.wrap{display:grid;grid-template-columns:250px minmax(0,1fr);gap:28px;max-width:1180px;margin:0 auto;padding:10px 20px 80px}
nav{position:sticky;top:64px;align-self:start;max-height:calc(100vh - 80px);overflow:auto;font-size:13.5px}
nav b{display:block;margin:12px 0 2px}nav a{display:block;color:var(--text2);text-decoration:none;padding:1px 0}
nav a:hover{color:var(--accent)}
main{max-width:80ch}
h2{font-size:21px;margin:36px 0 6px;font-weight:640;border-bottom:1px solid var(--line);padding-bottom:4px}
article{margin:18px 0 22px}article:target{outline:2px solid var(--accent);outline-offset:6px;border-radius:4px}
h3{font-size:16px;margin:0 0 4px;font-weight:620}h3 a.self{color:var(--muted);text-decoration:none;font-weight:400;margin-left:6px}
p{margin:6px 0}ul,ol{margin:6px 0;padding-left:22px}li{margin:3px 0}a{color:var(--accent)}
code{font:13px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:var(--sunk);border:1px solid var(--line);
border-radius:4px;padding:0 4px}
.meta{color:var(--muted);font-size:13px}.hidden{display:none}
@media (max-width:800px){.wrap{grid-template-columns:1fr}nav{position:static;max-height:none}}
@media print{header input,nav{display:none}.wrap{display:block}}
"""

PAGE_JS = """
(function(){
  var box=document.getElementById('q'),arts=[].slice.call(document.querySelectorAll('article'));
  box.addEventListener('input',function(){
    var q=box.value.trim().toLowerCase();
    arts.forEach(function(a){a.classList.toggle('hidden',!!q&&a.textContent.toLowerCase().indexOf(q)<0);});
    [].slice.call(document.querySelectorAll('main section')).forEach(function(s){
      s.classList.toggle('hidden',!!q&&!s.querySelector('article:not(.hidden)'));});
  });
  var start=document.body.getAttribute('data-start');
  if(start&&!location.hash){var el=document.getElementById(start);if(el){try{history.replaceState(null,'','#'+start);}
    catch(e){}el.scrollIntoView();}}
})();
"""


def page(start: str | None = None) -> str:
    """The whole help as one self-contained HTML page (no scripts, styles or fonts from anywhere else)."""
    from ionomos import __version__

    secs, ents = sections(), entries()
    nav, body = [], []
    for s in secs:
        nav.append(f"<b><a href='#{escape(s.id)}'>{escape(s.title)}</a></b>")
        nav += [f"<a href='#{escape(i)}'>{escape(ents[i].title)}</a>" for i in s.entries]
        arts = "".join(
            f"<article id='{escape(i)}'><h3>{escape(ents[i].title)}<a class='self' href='#{escape(i)}' "
            f"title='Link to this topic'>#</a></h3>{to_html(ents[i].body)}{_generated(i)}</article>" for i in s.entries)
        body.append(f"<section id='{escape(s.id)}'><h2>{escape(s.title)}</h2>{to_html(s.intro)}{arts}</section>")
    start_attr = f" data-start='{escape(start)}'" if start and (start in ents or start in {s.id for s in secs}) else ""
    return ("<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' "
            "content='width=device-width,initial-scale=1'><title>Ionomos help</title>"
            f"<style>{PAGE_CSS}</style></head><body{start_attr}><header><h1>Ionomos help</h1>"
            "<input id='q' type='search' placeholder='Find a topic (e.g. volcano, NO_TABLE, imputation)' "
            f"aria-label='Find a topic'><span class='meta'>Ionomos {escape(__version__)} · works offline</span></header>"
            f"<div class='wrap'><nav aria-label='Topics'>{''.join(nav)}</nav><main>{''.join(body)}</main></div>"
            f"<script>{PAGE_JS}</script></body></html>")


def default_dir(log_dir: Path | str | None = None) -> Path:
    """Where help.html goes: <log_dir>/help when the lab's log folder exists, else Ionomos's app-data folder."""
    if log_dir and Path(log_dir).is_dir():
        return Path(log_dir) / "help"
    from ionomos.service import appdata_dir

    return appdata_dir() / "help"


def write_page(out_dir: Path | str, start: str | None = None) -> Path:
    """Write help.html into out_dir (a folder of Ionomos's own; created if needed). Returns its path."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / PAGE_NAME
    path.write_text(page(start), encoding="utf-8")
    return path


def open_help(start: str | None = None, log_dir: Path | str | None = None, opener=None) -> Path:
    """Write the help page (opening at `start`, an entry id or anything topic() understands) and open it
    in the browser. The app's Help button, a pop-up's "More help" and `ionomos help --open` all call this."""
    path = write_page(default_dir(log_dir), topic(start) if start else None)
    if opener is None:
        from ionomos.service import open_path as opener
    opener(str(path))
    return path
