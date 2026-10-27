"""The documentation site (website/, D132), checked without MkDocs.

website/hooks.py makes the site's pages from the repository's own files, and the docs-site workflow builds them
with `mkdocs build --strict`. These tests cover what that build can't see, or should fail on before CI does:

    nav         mkdocs.yml's nav lists every page the hooks make and nothing else; every page's file exists
    links       every link of a docs page on the site leads to a site page or to a file on GitHub; the rule itself
    help        every help entry is on exactly one page with its own id, every link between help pages resolves
                (they are rendered HTML, which MkDocs doesn't check), and the text is ionomos.help's own rendering
    example     the reports page links only to files that are on the site; without reports it says so
    workflow    a pull request touching any page's file builds the site; the deploy needs the Pages permissions

The tests marked "Markdown" also render the pages with Python-Markdown, which comes with MkDocs: they run in the
docs-site workflow and skip where MkDocs isn't installed (this repository's dev venv).
"""
from __future__ import annotations

import fnmatch
import html
import importlib.util
import json
import re
from pathlib import Path

import pytest
import yaml

from ionomos import help as helpdoc

REPO = Path(__file__).resolve().parents[2]  # this checkout (ionomos may be installed from elsewhere)
SITE = REPO / "website"
WORKFLOW = REPO / ".github" / "workflows" / "docs-site.yml"

pytestmark = pytest.mark.skipif(not (SITE / "hooks.py").is_file(), reason="no website/ (not a checkout)")


@pytest.fixture(scope="module")
def hooks():
    spec = importlib.util.spec_from_file_location("ionomos_site_hooks", SITE / "hooks.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _nav_pages(nav) -> list[str]:
    out = []
    for item in nav:
        for value in (item.values() if isinstance(item, dict) else [item]):
            out += _nav_pages(value) if isinstance(value, list) else [value]
    return out


# ---------------------------------------------------------------------- nav --


def test_the_nav_lists_every_page_and_only_those(hooks):
    config = yaml.safe_load((SITE / "mkdocs.yml").read_text(encoding="utf-8"))
    nav = _nav_pages(config["nav"])
    assert len(nav) == len(set(nav)), f"a page is in the nav twice: {nav}"
    own = {p.relative_to(SITE / config["docs_dir"]).as_posix() for p in (SITE / config["docs_dir"]).rglob("*.md")}
    want = own | set(hooks.sources()) | {hooks.REPORTS}
    assert set(nav) == want, f"in the nav, not made: {sorted(set(nav) - want)}; made, not in the nav: " \
                             f"{sorted(want - set(nav))}"


def test_every_page_is_a_file_of_the_repository(hooks):
    for page, src in hooks.sources().items():
        assert (REPO / src).is_file(), f"{page}: {src} doesn't exist"
    assert [p.removeprefix("help/").removesuffix(".md") for p in hooks.help_pages()] == list(helpdoc.FILES)
    assert {"docs/QUICKSTART.md", "docs/ENGINES.md", "docs/VALIDATION.md", "docs/EXAMPLE.md"} <= set(
        hooks.PAGES.values()), "VISION.md item 6: the site holds QUICKSTART, ENGINES, VALIDATION and the example"


def test_the_build_tools_are_pinned():
    reqs = [ln.strip() for ln in (SITE / "requirements.txt").read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")]
    assert reqs and all(re.fullmatch(r"[A-Za-z0-9_.-]+==[0-9][0-9A-Za-z.]*", r) for r in reqs), reqs
    assert {r.split("==")[0] for r in reqs} >= {"mkdocs", "mkdocs-material"}
    package = (REPO / "ionomos" / "pyproject.toml").read_text(encoding="utf-8")
    assert "mkdocs" not in package, "the site's tools are not a dependency of the package (D132)"


# -------------------------------------------------------------------- links --


def test_every_link_of_a_docs_page_leads_somewhere(hooks):
    on_site = {v: k for k, v in hooks.sources().items()}
    for page, src in hooks.PAGES.items():
        text, broken = hooks.rewrite_links((REPO / src).read_text(encoding="utf-8"), src, page, on_site)
        assert not broken, broken
        for target in re.findall(r"\]\(<?([^)\s>]+)", text):
            if target.startswith(("#", "https://", "http://", "mailto:")):
                continue
            path = target.partition("#")[0]
            assert path in hooks.sources() or path in {"index.md", hooks.REPORTS}, f"{page}: {target}"


def _rewrite(hooks, text, source="docs/QUICKSTART.md", page="quickstart.md", at="master"):
    return hooks.rewrite_links(text, source, page, {v: k for k, v in hooks.sources().items()}, at)


BLOB = "https://github.com/nichfoster/ionomos/blob"


@pytest.mark.parametrize("text, source, page, want", [
    ("[v](VALIDATION.md)", None, None, "[v](validation.md)"),
    ("[v](VALIDATION.md#check-it-on-your-own-experiment-ionomos-compare)", None, None,
     "[v](validation.md#check-it-on-your-own-experiment-ionomos-compare)"),
    ("[d](DECISIONS.md#d24)", None, None, f"[d]({BLOB}/master/docs/DECISIONS.md#d24)"),
    ("[r](../README.md#license)", None, None, f"[r]({BLOB}/master/README.md#license)"),
    ("[s](../ionomos/src/ionomos/example.py)", None, None, f"[s]({BLOB}/master/ionomos/src/ionomos/example.py)"),
    ("[h](../ionomos/src/ionomos/help/)", None, None,
     "[h](https://github.com/nichfoster/ionomos/tree/master/ionomos/src/ionomos/help)"),
    ("![l](../LICENSE)", None, None, "![l](https://raw.githubusercontent.com/nichfoster/ionomos/master/LICENSE)"),
    ("[t](<ENGINES.md> \"title\")", None, None, "[t](<engines.md> \"title\")"),
    ("[e](https://example.org/DECISIONS.md) [m](mailto:a@b.c) [a](#here)", None, None,
     "[e](https://example.org/DECISIONS.md) [m](mailto:a@b.c) [a](#here)"),
    ("`[c](DECISIONS.md)` and ``[c](DECISIONS.md)``", None, None, "`[c](DECISIONS.md)` and ``[c](DECISIONS.md)``"),
    ("1. a list\n   ```md\n   [c](DECISIONS.md)\n   ```\n[q](QUICKSTART.md)", None, None,
     "1. a list\n   ```md\n   [c](DECISIONS.md)\n   ```\n[q](quickstart.md)"),
    ("[e](docs/ENGINES.md) [w](docs/WORKFLOWS.md)", "CHANGELOG.md", "changelog.md",
     f"[e](engines.md) [w]({BLOB}/master/docs/WORKFLOWS.md)"),
    ("[q](../../../../docs/QUICKSTART.md)", "ionomos/src/ionomos/help/faq.md", "help/faq.md",
     "[q](../quickstart.md)"),
])
def test_links_go_to_the_site_page_or_to_github(hooks, text, source, page, want):
    out, broken = _rewrite(hooks, text, source or "docs/QUICKSTART.md", page or "quickstart.md")
    assert (out, broken) == (want, [])


def test_links_to_github_point_at_the_tag_being_built(hooks):
    out, _ = _rewrite(hooks, "[d](DECISIONS.md)", at="v0.21.0")
    assert out == f"[d]({BLOB}/v0.21.0/docs/DECISIONS.md)"


def test_a_link_to_nothing_is_reported_and_left(hooks):
    text = "[n](NOPE.md) [o](../../outside.md) [a](/abs.md)"
    out, broken = _rewrite(hooks, text)
    assert out == text
    assert broken == ["docs/QUICKSTART.md: NOPE.md (no such file)", "docs/QUICKSTART.md: ../../outside.md (outside "
                      "the repository)", "docs/QUICKSTART.md: /abs.md (outside the repository)"]


def test_the_anchors_are_githubs(hooks):
    """The same rule as tests/test_docs_sync.py's _slug, which checks the docs' own #links on GitHub."""
    for heading, slug in [("The road to publishing", "the-road-to-publishing"), ("Wave 2: the app", "wave-2-the-app"),
                          ("D59 — FragPipe is run", "d59--fragpipe-is-run"),
                          ('What "done for v1.0" means', "what-done-for-v10-means")]:
        assert hooks.github_slug(heading) == slug


# --------------------------------------------------------------------- help --

_HEADING = re.compile(r"^(#{1,2}) .*\{#([^}]+)\}$", re.M)


def _ids(md: str) -> list[str]:
    return [m.group(2) for m in _HEADING.finditer(md)]


def test_every_help_entry_is_on_one_page_with_its_id(hooks):
    seen: list[str] = []
    for name, sec in zip(helpdoc.FILES, helpdoc.sections(), strict=True):
        ids = _ids(hooks.help_page(name))
        assert ids == [sec.id, *sec.entries], name
        seen += ids
    assert sorted(seen) == sorted([s.id for s in helpdoc.sections()] + list(helpdoc.entries()))


def test_every_link_between_help_pages_resolves(hooks):
    pages = {name: hooks.help_page(name) for name in helpdoc.FILES}
    ids = {name: set(_ids(md)) for name, md in pages.items()}
    n = 0
    for name, md in pages.items():
        for href in re.findall(r'<a href="([^"]+)"', md):
            href = html.unescape(href)
            if href.startswith(("https://", "http://")):
                continue
            target, _, hid = href.partition("#")
            other = target.removesuffix(".html") if target else name
            assert other in ids and hid in ids[other], f"help/{name}.md: {href}"
            n += 1
    assert n > 100  # the help links to itself a lot; a renderer that dropped the links would pass otherwise


def test_the_help_text_is_the_packages_own_rendering(hooks):
    """An entry with no link to another entry is, byte for byte, what help.html and the report show."""
    md = "\n".join(hooks.help_page(name) for name in helpdoc.FILES)
    plain = [e for e in helpdoc.entries().values() if not helpdoc.links(e.body)]
    assert len(plain) > 50
    for e in plain:
        assert hooks._block(helpdoc.to_html(e.body) + helpdoc._generated(e.id)) in md, e.id
    assert "<li>" in hooks.help_page("troubleshooting").split("{#search.failed}")[1].split("\n## ")[0], \
        "search.failed lists FragPipe's failure causes, as help.html does"


def test_a_help_title_shows_as_written(hooks):
    assert hooks._md_text("a *b* `c` {#x} <d> & [e]_f_ #") == r"a \*b\* \`c\` \{\#x\} &lt;d&gt; &amp; \[e\]\_f\_ \#"


# ------------------------------------------------------------------ example --


def _example(root: Path, keys=("apms",)) -> Path:
    (root / "download").mkdir(parents=True)
    (root / "download" / "FragPipe_Analyst_Manuscript_Datasets.zip").write_bytes(b"zip")
    (root / "README.txt").write_text("source and licence", encoding="utf-8")
    for key in keys:
        res = root / key / "results"
        (res / "fragpipe-analyst").mkdir(parents=True)
        (res / "report.html").write_text("<!doctype html><title>r</title>", encoding="utf-8")
        (res / "analysis.json").write_text(json.dumps({"ionomos_version": "9.9.9", "comparisons": [
            {"name": "CCND1 vs CONTROL", "up": 10, "down": 0, "tested": 1360}]}), encoding="utf-8")
        (res / "protein_results.tsv").write_text("x", encoding="utf-8")
        (res / "fragpipe-analyst" / "reproduce_in_R.R").write_text("x", encoding="utf-8")
        (root / key / "README.txt").write_text("look", encoding="utf-8")
        (root / key / "experiment.yaml").write_text("analysis: {}", encoding="utf-8")
        (root / key / "combined_protein.tsv").write_text("the source table", encoding="utf-8")
    return root


def test_the_reports_go_on_the_site_without_the_source_data(hooks, tmp_path):
    folder = _example(tmp_path / "ionomos_example")
    files, made, built = hooks.example_files(folder)
    assert built == ["apms"]
    assert set(files) == {"reports/README.txt", "reports/apms/README.txt", "reports/apms/experiment.yaml",
                          "reports/apms/results/report.html", "reports/apms/results/analysis.json",
                          "reports/apms/results/protein_results.tsv",
                          "reports/apms/results/fragpipe-analyst/reproduce_in_R.R"}
    listing = made["reports/apms/results/fragpipe-analyst/index.html"]
    assert 'href="reproduce_in_R.R"' in listing and 'href="../report.html"' in listing

    page = hooks.reports_page(folder, built)
    assert "10 up, 0 down of 1,360 tested" in page and "Ionomos 9.9.9" in page
    assert "https://creativecommons.org/licenses/by/4.0/" in page and "10.5281/zenodo.13147961" in page
    assert "dia, tmt" in page and "Not built this time" in page  # the two that made no report
    for target in re.findall(r"\]\(([^)\s]+)\)", page):
        if not target.startswith("https://"):
            assert f"reports/{target}" in files or target in {"../example.md", "../quickstart.md"}, target


@pytest.mark.parametrize("given, why", [(None, "built without them"), ("empty", "could not make them")])
def test_without_reports_the_page_says_so(hooks, tmp_path, given, why):
    page = hooks.reports_page(tmp_path if given else None, [])
    assert "Not built this time" in page and why in page
    assert "report.html" not in page and "ionomos example --open" in page
    assert hooks.example_files(tmp_path) == ({}, {}, [])


# ----------------------------------------------------------------- workflow --


def test_a_pull_request_that_changes_a_page_builds_the_site(hooks):
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    on = wf[True]  # YAML 1.1 reads the key `on` as true
    paths = on["pull_request"]["paths"]
    watched = [*hooks.sources().values(), "website/mkdocs.yml", "website/hooks.py", "website/pages/index.md",
               "website/requirements.txt", ".github/workflows/docs-site.yml", "scripts/build_site.sh"]
    for f in watched:
        assert any(fnmatch.fnmatchcase(f, p) for p in paths), f"a change to {f} doesn't build the site"
    assert on["push"] == {"tags": ["v*"]} and "workflow_dispatch" in on
    build = " ".join(str(s.get("run", "")) for s in wf["jobs"]["build"]["steps"])
    assert "mkdocs build --strict" in build and "website/requirements.txt" in build
    deploy = wf["jobs"]["deploy"]
    assert deploy["permissions"] == {"pages": "write", "id-token": "write"}
    assert deploy["environment"]["name"] == "github-pages"
    assert "pull_request" not in deploy["if"] and "push" in deploy["if"]
    uses = [s.get("uses", "") for job in wf["jobs"].values() for s in job["steps"]]
    assert any(u.startswith("actions/upload-pages-artifact@") for u in uses)
    assert any(u.startswith("actions/deploy-pages@") for u in uses)


# ----------------------------------------------- Markdown (with MkDocs only) --


def _markdown(hooks):
    markdown = pytest.importorskip("markdown", reason="Python-Markdown comes with MkDocs (the docs-site workflow)")
    return markdown.Markdown(extensions=["toc", "attr_list", "admonition", "tables", "fenced_code"],
                             extension_configs={"toc": {"slugify": hooks.github_slug}})


def test_markdown_keeps_the_help_ids_titles_and_text(hooks):
    md = _markdown(hooks)
    ents = helpdoc.entries()
    for name, sec in zip(helpdoc.FILES, helpdoc.sections(), strict=True):
        source = hooks.help_page(name)
        out = md.reset().convert(source)
        assert re.findall(r'<h[12] id="([^"]+)"', out) == [sec.id, *sec.entries], name
        tokens = md.toc_tokens[0]
        assert html.unescape(tokens["name"]) == sec.title
        assert [html.unescape(t["name"]) for t in tokens["children"]] == [ents[i].title for i in sec.entries]
        for block in re.findall(r'^<div class="ionomos-help">.*</div>$', source, re.M):
            assert block in out, f"{name}: Markdown changed a rendered help text"


def test_markdown_anchors_are_the_ones_github_makes(hooks):
    md = _markdown(hooks)
    out = md.reset().convert("## What the app does (`library_tab.py`)\n\n## D59 — FragPipe is run\n")
    assert re.findall(r'id="([^"]+)"', out) == ["what-the-app-does-library_tabpy", "d59--fragpipe-is-run"]
