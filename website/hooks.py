"""
MkDocs hooks for the documentation site (website/mkdocs.yml, D132): https://nichfoster.github.io/ionomos/

The site's pages are the repository's own files, read where they are; nothing is copied into website/:

    PAGES               the docs/*.md and CHANGELOG.md the site shows (mkdocs.yml's nav lists the same names)
    help/<name>.md      ionomos/src/ionomos/help/<name>.md, its text rendered by ionomos.help itself (help_page), so
                        the site says what `ionomos help` and the report's ? buttons say; the headings keep their
                        {#id} (attr_list), so the table of contents, the search and a link to an entry work
    reports/index.md    the example's reports when IONOMOS_SITE_EXAMPLE names a folder `ionomos example` wrote,
                        else a page saying they were not built this time (reports_page; never a broken link)
    pages/              the site's own pages (index.md)

A relative link to a file the site doesn't show (DECISIONS.md, ../ionomos/src/...) goes to that file on GitHub
(rewrite_links), at IONOMOS_SITE_REF (the tag being built; default master). A link to a file that doesn't exist is
a warning, so `mkdocs build --strict` fails on it as it fails on a broken link or anchor between pages. Anchors
are GitHub's (github_slug, the rule tests/test_docs_sync.py checks the docs' links with), so a link such as
`VISION.md#the-road-to-publishing` works on GitHub and on the site alike.

Everything but the on_* hooks is plain Python with no MkDocs import: tests/test_site.py runs it without MkDocs.
"""
from __future__ import annotations

import html
import json
import logging
import os
import posixpath
import re
import sys
from functools import cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
GITHUB = "https://github.com/nichfoster/ionomos"
RAW = "https://raw.githubusercontent.com/nichfoster/ionomos"
HELP_SRC = "ionomos/src/ionomos/help"
REPORTS = "reports/index.md"
EXAMPLE_ENV = "IONOMOS_SITE_EXAMPLE"   # a folder `ionomos example` wrote; unset: the reports page says so
REF_ENV = "IONOMOS_SITE_REF"           # the git ref links to GitHub point at (CI: the tag); default master

# site page -> the repository file it is. The help pages come from ionomos.help.FILES (help_pages()).
PAGES = {
    "quickstart.md": "docs/QUICKSTART.md",
    "example.md": "docs/EXAMPLE.md",
    "engines.md": "docs/ENGINES.md",
    "validation.md": "docs/VALIDATION.md",
    "vision.md": "docs/VISION.md",
    "scope.md": "docs/SCOPE.md",
    "changelog.md": "CHANGELOG.md",
}

log = logging.getLogger("mkdocs.hooks.ionomos")  # under "mkdocs": a warning here fails `mkdocs build --strict`


@cache
def _ionomos():
    """ionomos, ionomos.help and ionomos.example from this checkout (standard library only; nothing installed)."""
    src = str(REPO / "ionomos" / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    import ionomos
    from ionomos import example
    from ionomos import help as helpdoc

    return ionomos, helpdoc, example


def help_pages() -> dict[str, str]:
    """help/<name>.md -> its file in the package, in the help's own page order."""
    helpdoc = _ionomos()[1]
    return {f"help/{name}.md": f"{HELP_SRC}/{name}.md" for name in helpdoc.FILES}


def sources() -> dict[str, str]:
    """Every site page that is a file of the repository -> that file."""
    return {**PAGES, **help_pages()}


def ref() -> str:
    return os.environ.get(REF_ENV, "").strip() or "master"


# -------------------------------------------------------------------- links --


def github_slug(value: str, separator: str = "-") -> str:
    """A heading's anchor as GitHub makes it: lower case, punctuation dropped (not - or _), each space a hyphen."""
    return re.sub(r"[^\w\- ]", "", value.strip().lower()).replace(" ", separator)


# fenced code blocks (indented ones too, inside lists), then code spans that don't cross a blank line
_CODE = re.compile(r"^[ \t]*(```+|~~~+)[^\n]*\n.*?^[ \t]*\1[ \t]*$|(`+)(?:(?!\n[ \t]*\n).)+?\2", re.S | re.M)
_LINK = re.compile(r"(\]\()(<?)([^)\s>]+)(>?)((?:\s+\"[^\"]*\")?\))")
_SCHEME = re.compile(r"[a-z][a-z0-9+.-]*:", re.I)


def rewrite_links(markdown: str, source: str, page: str, on_site: dict[str, str], at: str = "master"
                  ) -> tuple[str, list[str]]:
    """The links of `source` (a file of the repository, shown as the site page `page`) as the site needs them: one
    to another page of the site goes to that page, one to any other file of the repository goes to it on GitHub
    at `at` (an image to its raw file). Links in code are left alone. on_site maps a file to its site page.
    Returns the Markdown and the links that lead nowhere (no such file), which the build reports as warnings."""
    broken: list[str] = []
    here = posixpath.dirname(source)
    start = posixpath.dirname(page) or "."

    def fix(seg: str, m: re.Match) -> str:
        target = m.group(3)
        if target.startswith("#") or _SCHEME.match(target):
            return m.group(0)
        path, hash_, anchor = target.partition("#")
        rel = posixpath.normpath(posixpath.join(here, path))
        if path.startswith("/") or rel == ".." or rel.startswith("../"):
            broken.append(f"{source}: {target} (outside the repository)")
            return m.group(0)
        if rel in on_site:
            new = posixpath.relpath(on_site[rel], start)
        elif (REPO / rel).is_file():
            bracket = seg.rfind("[", 0, m.start())
            image = bracket > 0 and seg[bracket - 1] == "!"
            new = f"{RAW}/{at}/{rel}" if image else f"{GITHUB}/blob/{at}/{rel}"
        elif (REPO / rel).is_dir():
            new = f"{GITHUB}/tree/{at}" + ("" if rel == "." else f"/{rel}")
        else:
            broken.append(f"{source}: {target} (no such file)")
            return m.group(0)
        return f"{m.group(1)}{m.group(2)}{new}{hash_}{anchor}{m.group(4)}{m.group(5)}"

    out, pos = [], 0
    for code in _CODE.finditer(markdown):
        seg = markdown[pos:code.start()]
        out += [_LINK.sub(lambda m, s=seg: fix(s, m), seg), code.group(0)]
        pos = code.end()
    seg = markdown[pos:]
    out.append(_LINK.sub(lambda m: fix(seg, m), seg))
    return "".join(out), broken


# --------------------------------------------------------------------- help --

HELP_NOTE = ("*Part of the help Ionomos carries with it: `ionomos help --open` shows the same text offline, and "
             "the **?** buttons in every report the parts about the report. From Ionomos {version}.*")


def _md_text(s: str) -> str:
    """Plain text (a help title, which help.html shows escaped and unformatted) as Markdown that shows it so."""
    return re.sub(r"([\\`*_{}\[\]#!])", r"\\\1", html.escape(s, quote=False))


def _block(rendered: str) -> str:
    """Rendered HTML as one raw block, which Markdown passes through as it is."""
    return f'<div class="ionomos-help">{rendered}</div>'


def help_page(name: str, url=None) -> str:
    """help/<name>.md as the site shows it. The section and its entries become Markdown headings with their own
    ids ("# Title {#start}", "## Title {#start.naming}"); each text is rendered by ionomos.help.to_html, the
    renderer of help.html and of the report's help, so nothing reads differently here (the same subset, the same
    escaping, the lists made from the code). A link to an entry on another help page goes to url(page)#id
    (default: <page>.html, MkDocs passes the real relative URL)."""
    ionomos, helpdoc, _ = _ionomos()
    secs = dict(zip(helpdoc.FILES, helpdoc.sections(), strict=True))
    sec, ents = secs[name], helpdoc.entries()
    page_of: dict[str, str] = {}
    for other, s in secs.items():
        page_of[s.id] = other
        page_of.update(dict.fromkeys(s.entries, other))
    url = url or (lambda other: f"{other}.html")

    def link(hid: str, inner: str) -> str:
        where = page_of.get(hid)
        if where is None:  # a link to no entry: help.check() and tests/test_help.py report it
            return inner
        href = f"#{hid}" if where == name else f"{url(where)}#{hid}"
        return f'<a href="{html.escape(href)}">{inner}</a>'

    out = [f"# {_md_text(sec.title)} {{#{sec.id}}}", "", HELP_NOTE.format(version=ionomos.__version__), ""]
    if sec.intro:
        out += [_block(helpdoc.to_html(sec.intro, link)), ""]
    for hid in sec.entries:
        body = helpdoc.to_html(ents[hid].body, link) + helpdoc._generated(hid)  # search.failed: FragPipe's causes
        out += [f"## {_md_text(ents[hid].title)} {{#{hid}}}", "", _block(body), ""]
    return "\n".join(out)


# ------------------------------------------------------------------ example --

_LISTING = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<style>body{{font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif;margin:24px;}}</style></head>
<body><h1>{title}</h1><ul>{items}</ul><p><a href="{up}">Back to the report</a></p></body></html>
"""


def example_files(folder: Path) -> tuple[dict[str, Path], dict[str, str], list[str]]:
    """What of a folder `ionomos example` wrote goes on the site, under reports/: each dataset's results/ (the
    report and every file it links to), README.txt (what to look for) and experiment.yaml (the design), and the
    folder's README.txt (the source, the licence, what was changed). Not the downloaded zip and not the source
    tables: those are Zenodo's to give, and the page links to the record. Returns {site path: file}, {site path:
    content} for an index.html in each folder of results/ the report links to, and the datasets with a report."""
    example = _ionomos()[2]
    files: dict[str, Path] = {}
    made: dict[str, str] = {}
    built: list[str] = []
    for key in example.DATASETS:
        ds = folder / key
        results = ds / "results"
        if not (results / "report.html").is_file():
            continue
        built.append(key)
        for p in sorted(results.rglob("*")):
            if p.is_file() and not p.is_symlink():
                files[f"reports/{key}/results/{p.relative_to(results).as_posix()}"] = p
        for d in sorted(p for p in results.rglob("*") if p.is_dir() and not (p / "index.html").exists()):
            rel = d.relative_to(results).as_posix()
            items = "".join(f'<li><a href="{html.escape(f.name)}">{html.escape(f.name)}</a></li>'
                            for f in sorted(d.iterdir()) if f.is_file())
            up = "../" * (rel.count("/") + 1) + "report.html"
            made[f"reports/{key}/results/{rel}/index.html"] = _LISTING.format(
                title=html.escape(f"{key}/results/{rel}/"), items=items, up=up)
        for name in ("README.txt", "experiment.yaml"):
            if (ds / name).is_file():
                files[f"reports/{key}/{name}"] = ds / name
    if built and (folder / "README.txt").is_file():
        files["reports/README.txt"] = folder / "README.txt"
    return files, made, built


def _summary(results: Path) -> tuple[str, str]:
    """('CCND1 vs CONTROL: 10 up, 0 down of 1,360 tested', '0.20.0') from analysis.json, or blanks."""
    try:
        a = json.loads((results / "analysis.json").read_text(encoding="utf-8"))
        line = "; ".join(f"{c['name']}: {c['up']:,} up, {c['down']:,} down of {c['tested']:,} tested"
                         for c in a.get("comparisons") or [])
        return line, str(a.get("ionomos_version") or "")
    except (OSError, ValueError, KeyError, TypeError):
        return "", ""


def reports_page(folder: Path | None, built: list[str]) -> str:
    """reports/index.md: the reports that were built, each with what it shows and the data's attribution (CC BY
    4.0: the source, the licence, what was changed); or, when none was, a page that says so and how to make
    them. It links only to files that are on the site."""
    ionomos, _, example = _ionomos()
    a = example.ARCHIVE
    data = (f"*FragPipe-Analyst Manuscript Datasets*, Yi Hsiao, Zenodo 2024, "
            f"[doi:{a.doi}](https://doi.org/{a.doi}) ([the record]({a.record}))")
    out = ["# Example reports", ""]
    if not built:
        why = ("this copy of the site was built without them (a pull request's check, or a local build without "
               "`--example`)" if folder is None else
               "`ionomos example` could not make them when the site was built (the download from Zenodo or the "
               "analysis failed; the build's log says which)")
        out += ['!!! warning "Not built this time"', "",
                f"    The example reports are not on this copy of the site: {why}. Nothing here links to a report "
                "that isn't there.", "",
                "To see them on your own computer (about a minute, one 20.9 MB download), install Ionomos as the "
                "[quickstart](../quickstart.md) says, then:", "",
                "```bash", "ionomos example --open", "```", "",
                f"The data are the example datasets of FragPipe-Analyst's paper: {data}, "
                f"[{a.licence}]({a.licence_url}). [The real-data example](../example.md) says what they are and "
                "walks through the three reports.", ""]
        return "\n".join(out)

    versions = set()
    out += ["Reports that Ionomos wrote from real, published data: the example datasets of FragPipe-Analyst's own "
            "paper, downloaded from Zenodo and analysed by `ionomos example`, as it does on your computer. Each "
            "report is one HTML file with its data inside: open it here, or save it and open it offline. "
            "[The real-data example](../example.md) walks through them and says what Ionomos does differently "
            "from FragPipe-Analyst.", ""]
    for key in built:
        ds = example.DATASETS[key]
        report = folder / key / "results" / "report.html"
        line, version = _summary(report.parent)
        versions.add(version)
        links = [f"**[Open the report]({key}/results/report.html)** ({report.stat().st_size / 1e6:.1f} MB)"]
        if (folder / key / "README.txt").is_file():
            links.append(f"[what to look for]({key}/README.txt)")
        if (folder / key / "experiment.yaml").is_file():
            links.append(f"[the design, `experiment.yaml`]({key}/experiment.yaml)")
        out += [f"## {_md_text(ds.title)}", "", " · ".join(links), ""]
        if line:
            out += [f"{_md_text(line)}.", ""]
        out += [_md_text(" ".join(ds.about.split())), ""]
    missing = [k for k in example.DATASETS if k not in built]
    if missing:
        out += ['!!! note "Not built this time"', "",
                f"    {', '.join(missing)}: `ionomos example` made no report for "
                f"{'it' if len(missing) == 1 else 'them'} when the site was built (the build's log says why).", ""]
    versions.discard("")
    made_by = f"Ionomos {', '.join(sorted(versions))}" if versions else f"Ionomos {ionomos.__version__}"
    out += ["## The data and its licence", "",
            f"- **Source**: {data}.",
            f"- **Licence**: [{a.licence}]({a.licence_url}).",
            f"- **Cite**: {_md_text(a.citation)}",
            f"- **Changes**: the tables were analysed by {made_by} with the settings each report states. Each "
            "`experiment.yaml` (the design, taken from the authors' `experiment_annotation.tsv`) and the "
            "`README.txt` files are Ionomos' own. The source tables are not on this site; they are on Zenodo.",
            "- " + ("[README.txt](README.txt): all of this, as `ionomos example` wrote it."
                    if (folder / "README.txt").is_file() else "Each report's Methods section lists its settings."),
            ""]
    return "\n".join(out)


# -------------------------------------------------------------- MkDocs hooks --


def on_config(config):
    """Anchors as GitHub makes them, so the docs' #links work in both places."""
    config.mdx_configs.setdefault("toc", {})["slugify"] = github_slug
    return config


def on_files(files, config):
    from mkdocs.structure.files import File

    own = Path(config.docs_dir).resolve().relative_to(REPO).as_posix()
    for f in files.documentation_pages():
        f.edit_uri = f"{own}/{f.src_uri}"
    for page, src in sources().items():
        f = File.generated(config, page, abs_src_path=str(REPO / src))
        f.edit_uri = src
        files.append(f)
    given = os.environ.get(EXAMPLE_ENV, "").strip()
    folder = Path(given).expanduser().resolve() if given else None
    shown, made, built = example_files(folder) if folder and folder.is_dir() else ({}, {}, [])
    for page, path in shown.items():
        files.append(File.generated(config, page, abs_src_path=str(path)))
    for page, content in made.items():
        files.append(File.generated(config, page, content=content))
    files.append(File.generated(config, REPORTS, content=reports_page(folder, built)))
    log.info(f"example reports: {', '.join(built) or 'none'} ({folder or f'{EXAMPLE_ENV} not set'})")
    return files


def on_page_markdown(markdown, page, config, files):
    uri = page.file.src_uri
    if uri in help_pages():
        return help_page(posixpath.basename(uri)[:-3], url=lambda other: files.get_file_from_path(
            f"help/{other}.md").url_relative_to(page.file))
    src = PAGES.get(uri)
    if src is None:
        return markdown
    text, broken = rewrite_links(markdown, src, uri, {v: k for k, v in sources().items()}, ref())
    for b in broken:
        log.warning(f"a link to a file that doesn't exist: {b}")
    if uri == "example.md":  # on the site the reports are a click away
        head, nl, rest = text.partition("\n")
        text = (f"{head}{nl}\n!!! tip \"See the reports\"\n\n    [Example reports](reports/index.md): the three reports "
                f"`ionomos example` writes, to open before you install anything.\n{rest}")
    return text
