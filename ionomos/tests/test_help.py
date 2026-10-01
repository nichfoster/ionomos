"""The help for users (ionomos/help, D46): one Markdown source shown in every report, in help.html (the app's
Help button, a pop-up's "More help", `ionomos help`) and in the terminal.

The coverage tests read the code, so a new doctor issue code, attention kind, intake rejection or held-search
reason can't ship without help. The JS side (the ? buttons and the Help section in report.html) is tested in
tests/js/test/help.test.mjs."""
import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from ionomos import attention, cli
from ionomos import help as helpdoc
from ionomos.intake import Kind

SRC = Path(helpdoc.__file__).resolve().parents[1]  # src/ionomos
PKG = SRC.parents[1]                                # the ionomos/ project folder


class _Tags(HTMLParser):
    """Every tag with its attributes, and every id, of a page."""

    def __init__(self):
        super().__init__()
        self.tags: list[tuple[str, dict]] = []
        self.ids: set[str] = set()

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.tags.append((tag, a))
        if a.get("id"):
            self.ids.add(a["id"])


def _parse(html: str) -> _Tags:
    p = _Tags()
    p.feed(html)
    return p


# ------------------------------------------------------------------ content --


def test_content_parses_links_resolve_and_ids_are_unique():
    secs = helpdoc.sections()
    assert [s.id for s in secs] == ["start", "report", "glossary", "trouble", "safety", "faq"]
    assert helpdoc.check() == []
    ents = helpdoc.entries()
    assert len(ents) > 120
    assert all(e.title and e.body for e in ents.values()), "every entry has a title and some text"
    for s in secs:
        assert s.entries and all(ents[i].section == s.id for i in s.entries)


def test_every_doctor_issue_code_has_help():
    """Scan the package for Issue("CODE", ...) so a new code without an entry fails here."""
    codes = set()
    for py in SRC.rglob("*.py"):
        for m in re.finditer(r'Issue\(\s*f?"([A-Z][A-Z0-9_]*)', py.read_text(encoding="utf-8")):
            codes.add(m.group(1))
    assert {"NO_TABLE", "SAMPLE_OUTLIER", "BATCH_SUSPECT", "LOW_SAMPLE", "CRASH_", "NO_RESULTS_FOLDER"} <= codes
    missing = sorted(c for c in codes if helpdoc.issue_entry(c) is None)
    assert missing == [], f"doctor issue codes without an entry in help/troubleshooting.md: {missing}"
    for stage in ("read", "statistics", "qc", "report", "sdrf"):  # CRASH_<STAGE> share one entry
        assert helpdoc.issue_entry(f"CRASH_{stage.upper()}") == "issue.CRASH"


def test_every_attention_kind_and_intake_rejection_has_help():
    ents = helpdoc.entries()
    assert [k for k in attention.KINDS if f"attention.{k}" not in ents] == []
    assert [k for k in attention.POPUP_KINDS if f"attention.{k}" not in ents] == []
    assert [k.value for k in Kind if f"intake.{k.value}" not in ents] == []


def test_every_held_search_reason_has_help():
    """Each `raise Hold("...")` in the runners is matched by HOLD_TOPICS, which points at an entry."""
    ents = helpdoc.entries()
    assert all(hid in ents for _rx, hid in helpdoc.HOLD_TOPICS)
    reasons = []
    for name in ("fragpipe.py", "diann.py", "maxquant.py", "sage.py"):
        text = (SRC / name).read_text(encoding="utf-8")
        reasons += [re.sub(r"\{[^}]*\}", "", m) for m in re.findall(r'raise Hold\(f?"([^"]*)"', text)]
    assert len(reasons) >= 8
    assert [r for r in reasons if helpdoc.hold_topic(r) is None] == []
    assert helpdoc.hold_topic("FASTA for DIA missing: C:/x.fas (and the workflow ...)") == "search.hold-fasta"
    assert helpdoc.hold_topic("low disk space: 3 GB free on C:\\") == "search.hold-disk"


def test_the_never_do_page_covers_the_promises():
    ents = helpdoc.entries()
    for hid in ("safety.moves", "safety.overwrite", "safety.failures", "safety.attempts", "safety.where"):
        assert hid in ents
    text = " ".join(ents[i].body for i in helpdoc.sections()[4].entries)
    for promise in ("fragpipe_previous_", "REJECTED", "checksum", "never deleted", "_ionomos", "results",
                    "inbox\\.removed"):
        assert promise in text, promise


# ---------------------------------------------------------------- rendering --


def test_markdown_is_escaped_and_only_safe_links_are_links():
    html = helpdoc.to_html("A <script>x</script> & **bold** *it* `a<b` [bad](javascript:alert(1)) "
                           "[web](https://example.org/x?a=1&b=2) [topic](#glossary.pca)\n\n- one\n  more\n- two\n\n1. first")
    assert "<script" not in html and "&lt;script&gt;x&lt;/script&gt; &amp;" in html
    assert "<b>bold</b>" in html and "<i>it</i>" in html and "<code>a&lt;b</code>" in html
    assert "javascript" not in html.lower().replace("javascript:alert", "") and ">bad<" not in html
    assert "bad" in html  # the text stays, the link goes
    assert '<a href="https://example.org/x?a=1&amp;b=2" target="_blank" rel="noopener">web</a>' in html
    assert '<a href="#glossary.pca" data-help="glossary.pca">topic</a>' in html
    assert "<ul><li>one more</li><li>two</li></ul><ol><li>first</li></ol>" in html
    assert helpdoc.to_html("**`desc:`** words, [`x`](#faq.rerun)") == (
        '<p><b><code>desc:</code></b> words, <a href="#faq.rerun" data-help="faq.rerun"><code>x</code></a></p>')


def test_no_markup_is_left_over_in_any_entry():
    for hid, e in helpdoc.entries().items():
        html = helpdoc.to_html(e.body)
        assert "**" not in html and "](" not in html, hid
        assert not re.search(r"(?<![\w<])\*\w", re.sub(r"<code>.*?</code>", "", html)), f"{hid}: a stray *"


def test_parse_refuses_a_heading_without_an_id():
    with pytest.raises(helpdoc.HelpError, match="needs an id"):
        helpdoc.parse("# Section {#s}\n\n## No id here\ntext\n", "x.md")
    with pytest.raises(helpdoc.HelpError, match="no '# Section"):
        helpdoc.parse("just text\n", "x.md")


def test_plain_text_for_the_terminal():
    t = helpdoc.text("issue.LOW_SAMPLE")
    assert t.startswith("A sample has far fewer identifications\n=====")
    assert "**" not in t and "](#" not in t and "`" not in t
    assert "How do I leave a sample out?" in t
    sec = helpdoc.text("glossary")
    assert "log2 fold change   (ionomos help glossary.log2fc)" in sec
    assert helpdoc.text("no.such.thing") == ""


def test_topic_understands_codes_kinds_words_and_sections():
    assert helpdoc.topic("NO_TABLE") == helpdoc.topic("no_table") == "issue.NO_TABLE"
    assert helpdoc.topic("CRASH_QC") == "issue.CRASH"
    assert helpdoc.topic("search_failed") == "attention.search_failed"
    assert helpdoc.topic("pca") == "glossary.pca"
    assert helpdoc.topic("glossary") == "glossary"
    assert helpdoc.topic("qc.power") == "qc.power"
    assert helpdoc.topic("#faq.rerun") == "faq.rerun"
    assert helpdoc.topic("Imputation") == "glossary.imputation"
    assert helpdoc.topic("no such topic at all") is None and helpdoc.topic("") is None


def test_topic_for_an_attention_item():
    it = attention.Item(id="x", kind="analysis_input", title="t", message="m",
                        data={"issues": [{"code": "NO_HITS", "severity": "warning"},
                                         {"code": "NO_CONTROL", "severity": "input"},
                                         {"code": "CRASH_READ", "severity": "error"}]})
    assert helpdoc.topic_for_item(it) == "issue.CRASH"  # the most serious issue first
    assert helpdoc.topic_for_item({"kind": "analysis_failed", "data": {"issues": [{"code": "NEW_CODE?"}]}}) == \
        "attention.analysis_failed"
    assert helpdoc.topic_for_item({"kind": "search_waiting", "message": "workflow file for DIA missing: x"}) == \
        "search.hold-workflow"
    assert helpdoc.topic_for_item({"kind": "search_waiting", "message": "something new"}) == "attention.search_waiting"
    assert helpdoc.topic_for_item({"kind": "search_failed"}) == "attention.search_failed"
    assert helpdoc.topic_for_item({"kind": "intake_rejected"}) == "attention.intake_rejected"
    assert helpdoc.topic_for_item({"kind": "who knows"}) == "trouble"


# --------------------------------------------------------------------- page --


def test_help_page_is_self_contained_and_every_anchor_resolves():
    html = helpdoc.page()
    low = html.lower()
    for external in ("src=", "<link", "@import", "url(", "<iframe", "<img"):
        assert external not in low, external
    tags = _parse(html)
    for s in helpdoc.sections():
        assert s.id in tags.ids
    for hid in ("start.naming", "start.notes", "report.search", "qc.pca", "glossary.pi0", "attention.search_failed",
                "issue.NO_TABLE", "issue.SAMPLE_OUTLIER", "issue.CRASH", "intake.dest", "search.hold-fasta",
                "safety.moves", "faq.rerun"):
        assert hid in tags.ids, hid
    for tag, a in tags.tags:
        href = a.get("href")
        if href is None:
            continue
        if href.startswith("#"):
            assert href[1:] in tags.ids, f"link to a missing anchor {href}"
        else:
            assert tag == "a" and href.startswith("https://") and a.get("rel") == "noopener", href
    # the list of FragPipe failure causes comes from the code, so it can't fall behind it
    from html import escape

    from ionomos.fragpipe import EXPLANATIONS

    failed = html.split("id='search.failed'")[1].split("</article>")[0]
    assert [msg for _rx, msg in EXPLANATIONS if escape(msg) not in failed] == []


def test_help_page_opens_at_a_topic():
    assert "<body data-start='issue.NO_TABLE'>" in helpdoc.page("issue.NO_TABLE")
    assert "<body>" in helpdoc.page("<script>not a topic</script>") and "<script>not" not in helpdoc.page("<script>not")
    assert "<body>" in helpdoc.page(None)


def test_write_and_open_help(tmp_path, monkeypatch):
    p = helpdoc.write_page(tmp_path / "out", "glossary.pca")
    assert p == tmp_path / "out" / "help.html" and "data-start='glossary.pca'" in p.read_text(encoding="utf-8")
    logs = tmp_path / "logs"
    logs.mkdir()
    opened = []
    got = helpdoc.open_help("NO_TABLE", log_dir=logs, opener=opened.append)
    assert got == logs / "help" / "help.html" and opened == [str(got)]
    assert "data-start='issue.NO_TABLE'" in got.read_text(encoding="utf-8")
    # no lab log folder (a pip install, or not set up yet): the app-data folder, never a new log folder
    from ionomos import service

    monkeypatch.setattr(service, "appdata_dir", lambda: tmp_path / "appdata")
    assert helpdoc.default_dir(tmp_path / "not-there") == tmp_path / "appdata" / "help"
    assert not (tmp_path / "not-there").exists()
    assert helpdoc.default_dir(None) == tmp_path / "appdata" / "help"


def test_cli_help(tmp_path, monkeypatch, capsys):
    out = tmp_path / "h"
    assert cli.main(["help", "NO_TABLE", "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert printed.startswith("No result table") and str(out / "help.html") in printed
    assert "id='issue.NO_TABLE'" in (out / "help.html").read_text(encoding="utf-8")
    assert cli.main(["help", "--out", str(out)]) == 0
    assert "Glossary   (ionomos help glossary)" in capsys.readouterr().out
    assert cli.main(["help", "certainly-not-a-topic", "--out", str(out)]) == 1
    assert "no help topic" in capsys.readouterr().err
    from ionomos import service

    opened = []
    monkeypatch.setattr(service, "open_path", lambda p: opened.append(Path(p)))
    monkeypatch.setattr(service, "appdata_dir", lambda: tmp_path / "appdata")
    assert cli.main(["--config", str(tmp_path / "no-config.yaml"), "help", "pca", "--open"]) == 0
    assert opened == [tmp_path / "appdata" / "help" / "help.html"]


def test_cli_help_uses_the_lab_log_folder(lab, monkeypatch, capsys):
    from ionomos import service

    monkeypatch.setattr(service, "open_path", lambda p: None)
    assert cli.main(["--config", str(lab["cfg_path"]), "help", "safety"]) == 0
    assert (lab["auto"] / "logs" / "help" / "help.html").is_file()


# ------------------------------------------------------------------- report --


def test_report_payload_embeds_what_the_report_links_to():
    issues = [{"code": "SAMPLE_OUTLIER", "severity": "warning", "title": "2 sample(s) look like outliers"},
              {"code": "CRASH_QC", "severity": "warning", "title": "The qc step failed"},
              {"code": "NOT_A_CODE_YET", "severity": "warning", "title": "x"}]
    p = helpdoc.report_payload(issues)
    ents = p["entries"]
    assert [x["id"] for x in p["issues"]] == ["issue.SAMPLE_OUTLIER", "issue.CRASH", None]
    assert all(i in ents for i in p["report"] + p["glossary"] + p["more"])
    assert {"qc.card", "qc.power", "report.search", "glossary.log2fc"} <= set(ents)
    assert not any(i.startswith(("start.", "trouble.", "safety.")) for i in ents)  # the full help's, not the report's
    for hid, e in ents.items():
        assert "<script" not in e["h"]
        for target in re.findall(r'data-help="([^"]+)"', e["h"]):
            assert target in ents, f"{hid} links to {target}, which the report doesn't carry"
        assert all(h == f"#help-{t}" for h, t in re.findall(r'href="([^"]+)" data-help="([^"]+)"', e["h"]))
    assert len(json.dumps(p)) < 90_000, "keep the report's payload modest"   # ~110 entries by 0.14; a report is ~400 kB


def test_report_has_the_help_nav_section_and_payload(tmp_path):
    from ionomos import downstream
    from ionomos.downstream import simulate

    runs = [(f"/x/{c}_{r}.raw", c) for c in ("DMSO", "Drug") for r in (1, 2, 3)]
    simulate.dia_pg_matrix(tmp_path / "e/fragpipe/report.pg_matrix.tsv", runs, seed=6, n_proteins=90)
    out = downstream.analyze(tmp_path / "e", "DIA", context={"experiment": "help test"})
    html = out.report.read_text(encoding="utf-8")
    assert "<a href='#help'>Help</a>" in html and "<section id='help'>" in html and "id='helpbody'" in html
    data = json.loads(re.search(r"<script id='ionomos-data' type='application/json'>(.*?)</script>", html, re.S).group(1))
    assert data["help"]["entries"]["qc.pca"]["t"] == "PCA"
    assert [x["code"] for x in data["help"]["issues"]] == [i.code for i in out.issues]


def test_a_broken_help_never_costs_a_report(monkeypatch):
    from ionomos.downstream import report

    def boom(_issues):
        raise RuntimeError("help broke")

    monkeypatch.setattr(helpdoc, "report_payload", boom)
    assert report._help_payload([]) == {}


# ---------------------------------------------------------------- packaging --


def test_help_ships_with_the_package_and_the_exe():
    assert '"ionomos.help" = ["*.md"]' in (PKG / "pyproject.toml").read_text(encoding="utf-8")
    spec = (PKG.parent / "deploy" / "ionomos.spec").read_text(encoding="utf-8")
    assert '"help" / "*.md"' in spec and '"ionomos.help"' in spec
    for name in helpdoc.FILES:
        assert helpdoc._source(name).startswith("# ")  # read through importlib.resources, as from a wheel
