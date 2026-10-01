"""Figures for slides without a browser (D62): the export style (charts.STYLE_DEFAULTS, analysis.export), the
static SVG figures drawn from the report's data (charts.figures), `ionomos export` and the watcher's
results/figures/ (slides.py), and the config writer's export block. The browser side (the report's Export
dialog, PNG, the .zip) is tested in tests/js/test/export.test.mjs."""
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import yaml

from ionomos import cli, configio, downstream
from ionomos.config import ConfigError, load
from ionomos.downstream import analysis, charts, simulate, slides

SVG = "{http://www.w3.org/2000/svg}"
REPORT_JS = Path(charts.__file__).parent / "assets" / "report.js"


@pytest.fixture(scope="module")
def experiment(tmp_path_factory):
    """A small analysed DIA experiment: 3 conditions, 2 comparisons."""
    dest = tmp_path_factory.mktemp("exp") / "e"
    runs = [(f"/x/{c}_{r}.raw", c) for c in ("DMSO", "DrugA", "DrugB") for r in (1, 2, 3)]
    simulate.dia_pg_matrix(dest / "fragpipe/report.pg_matrix.tsv", runs, seed=6, n_proteins=120)
    out = downstream.analyze(dest, "DIA", context={"experiment": "Export test"})
    assert out.report is not None
    return dest, out


@pytest.fixture(scope="module")
def payload(experiment):
    return slides.read_payload(experiment[1].report)


def _texts(root) -> list[str]:
    return [t.text or "" for t in root.iter(f"{SVG}text")]


def _fills(svg: str) -> set[str]:
    return set(re.findall(r'(?:fill|stroke)="([^"]+)"', svg))


# -------------------------------------------------------------------- style --


def test_style_defaults_are_complete_and_layers_win_in_order():
    s = charts.style_from()
    assert s == {**charts.STYLE_DEFAULTS, "figures": []}
    assert s["size"] == "slide169" and s["font_pt"] == 14 and s["font_family"] == "Arial"
    s = charts.style_from({"palette": "grey", "figures": "all"}, {"palette": "colorblind"}, None, {})
    assert s["palette"] == "colorblind" and s["figures"] == list(charts.STATIC_FIGURES)
    assert charts.style_from({"figures": ["PCA", "volcano", "pca"]})["figures"] == ["pca", "volcano"]
    assert charts.style_from({"figures": []})["figures"] == [] and charts.style_from({"figures": "none"})["figures"] == []


def test_a_size_without_a_text_size_takes_the_size_that_suits_it():
    assert charts.style_from({"size": "col1"})["font_pt"] == 7
    assert charts.style_from({"size": "half"})["font_pt"] == 12
    assert charts.style_from({"size": "col1", "font_pt": 9})["font_pt"] == 9
    assert charts.style_from({"font_pt": 20}, {"size": "col2"})["font_pt"] == 7  # the later layer names the size
    assert charts.style_from({"size": "custom", "width": 300, "height": 200})["font_pt"] == 14


@pytest.mark.parametrize("bad, says", [
    ({"sise": "slide169"}, "unknown export setting 'sise'"),
    ({"size": "poster"}, "export.size must be one of slide169"),
    ({"font_pt": 200}, "export.font_pt must be a number from 4 to 48"),
    ({"font_pt": "14"}, "export.font_pt must be a number"),
    ({"title": "yes"}, "export.title must be true or false"),
    ({"up": "red"}, 'export.up must be a colour like "#e34948"'),
    ({"up": "#12345"}, "export.up must be a colour"),
    ({"font_family": 'Arial" onload="x'}, "export.font_family must be a font's name"),
    ({"font_family": "<script>"}, "export.font_family must be a font's name"),
    ({"label_count": -1}, "export.label_count must be a whole number"),
    ({"png_dpi": 10}, "export.png_dpi must be 0"),
    ({"figures": ["volcano", "upset"]}, "export.figures must list any of volcano, pca, heatmap, correlation"),
    ("slide169", "export must be a mapping"),
])
def test_a_bad_style_is_refused_with_what_the_key_takes(bad, says):
    with pytest.raises(charts.StyleError, match=re.escape(says)):
        charts.style_layer(bad)
    assert charts.style_from(bad, lenient=True) == charts.style_from()  # a report never fails on it


def test_the_report_and_python_share_one_style(payload):
    """report.js and charts.py each hold the defaults, sizes and palettes: they must not drift apart."""
    js = REPORT_JS.read_text(encoding="utf-8")

    def literal(name: str):
        m = re.search(rf"const {name} = (\{{.*?\}});\n", js, re.S)
        text = re.sub(r",(\s*[}\]])", r"\1", re.sub(r"//[^\n]*", "", m.group(1)))  # no comments, no trailing commas
        return json.loads(re.sub(r'([{,]\s*)([A-Za-z_]\w*)\s*:', r'\1"\2":', text))

    assert literal("STYLE_DEFAULTS") == charts.STYLE_DEFAULTS
    assert {k: tuple(v) for k, v in literal("SIZES").items()} == charts.SIZES
    assert {k: tuple(v) for k, v in literal("PALETTES").items()} == charts.PALETTES
    enums = json.loads(re.sub(r'([{,]\s*)([A-Za-z_]\w*)\s*:', r'\1"\2":', re.search(
        r"const STYLE_ENUMS = (\{.*?\});\n", js, re.S).group(1).replace("Object.keys(SIZES)", "[]")))
    assert {k: tuple(v) for k, v in enums.items() if k != "size"} == {k: v for k, v in charts._ENUMS.items() if k != "size"}
    assert configio._EXPORT_FONT == {k: v[4] for k, v in charts.SIZES.items() if k != "custom"}
    assert {k: tuple(v) for k, v in literal("STYLE_RANGES").items()} == charts._RANGES
    assert re.search(r'const STATIC_FIGS = \["volcano", "pca", "heatmap", "correlation"\]', js)
    assert tuple(cli._EXPORT_SIZES) == tuple(k for k in charts.SIZES if k != "custom")
    assert payload["exportDefaults"] == charts.style_from()  # what the report's dialog starts from


@pytest.mark.parametrize("name, safe", [
    ("volcano_Drug vs DMSO.svg", "volcano_Drug_vs_DMSO.svg"),
    ("../../etc/passwd", "etc_passwd"),
    ("..\\..\\evil<script>:*?\"|.svg", "evil_script.svg"),
    ("name.exe.svg", "name.exe.svg"), ("a..b__c._.d.svg", "a_b_c_d.svg"), ("-rf.svg", "rf.svg"),
    ("CON", "_CON"), ("nul.svg", "_nul.svg"), ("COM1.txt", "_COM1.txt"), ("console.svg", "console.svg"),
    (".hidden", "hidden"), ("trailing. ", "trailing"), ("", "figure"), ("___", "figure"),
    ("a" * 300 + ".svg", "a" * 110 + ".svg"),
    ("µ-opioid β2.svg", "opioid_2.svg"),
    ("line\nbreak\x00.svg", "line_break.svg"),
])
def test_file_names_are_safe_on_windows(name, safe):
    got = charts.safe_name(name)
    assert got == safe
    assert re.fullmatch(r"[A-Za-z0-9_+-][A-Za-z0-9_.+-]*", got) and len(got) <= 120 and not got.endswith(".")


def test_font_stack_names_the_font_then_fallbacks():
    assert charts.font_stack("Arial") == "Arial, Helvetica, sans-serif"
    assert charts.font_stack("Segoe UI") == "'Segoe UI', Arial, Helvetica, sans-serif"
    assert charts.font_stack("Times New Roman") == "'Times New Roman', Times, serif"
    assert charts.font_stack("Courier New") == "'Courier New', Courier, monospace"


# ------------------------------------------------------------------ figures --


def test_every_static_figure_is_plain_editable_svg(payload, experiment):
    figs = charts.figures(payload, charts.style_from(), generator="Made by a test")
    assert [n for n, _w, _s in figs] == ["volcano_DrugA_vs_DMSO.svg", "volcano_DrugB_vs_DMSO.svg", "pca.svg", "heatmap.svg",
                                         "correlation.svg"]
    summary = {c["name"]: c for c in experiment[1].summary["comparisons"]}
    for name, what, svg in figs:
        root = ET.fromstring(svg.encode("utf-8"))  # well-formed XML
        assert root.tag == f"{SVG}svg"
        assert not re.search(r"var\(|<style|class=|style=|foreignObject", svg), name  # nothing that needs CSS
        assert root.get("font-family") == "Arial, Helvetica, sans-serif"
        assert all(re.fullmatch(r"#[0-9a-f]{6}", c) for c in _fills(svg)), (name, _fills(svg))
        assert len(_texts(root)) > 5  # text stays text
        desc = root.find(f"{SVG}desc").text
        assert "Experiment: Export test" in desc and "Export style: 16:9 slide (1280.0 × 720.0 px), text 14 pt Arial" in desc
        assert "Made by a test" in desc and "Analysis: limma moderated t-test; normalisation: median" in desc
        assert what
    for name in ("volcano_DrugA_vs_DMSO.svg", "pca.svg"):  # plots that fill the size give exactly the size
        root = ET.fromstring(dict((n, s) for n, _w, s in figs)[name].encode("utf-8"))
        assert (root.get("width"), root.get("height")) == ("1280", "720")
        assert root.get("viewBox") == "0 0 822.86 462.86"
    # the volcano's hits are the analysis' hits (the report's data, the saved cut-offs)
    root = ET.fromstring(figs[0][2].encode("utf-8"))
    texts = _texts(root)
    c = summary["DrugA vs DMSO"]
    assert texts[:2] == ["DrugA vs DMSO", "Volcano plot · Export test"]
    assert f"Up {c['up']}" in texts and f"Down {c['down']}" in texts and "Not significant" in texts
    assert "Cut-offs: |log2FC| ≥ 1 and adjusted p ≤ 0.05" in root.find(f"{SVG}desc").text
    assert any(t.startswith("Export test · hits: |log2FC| ≥ 1 and adjusted p ≤ 0.05 · limma") for t in texts)
    assert "DrugA ↑" in texts and "↑ DMSO" in texts
    assert len(list(root.iter(f"{SVG}circle"))) == c["tested"] + 3  # every tested feature + the legend
    # the heatmap and the correlation keep their own shape: never stretched, never larger than the size
    for name in ("heatmap.svg", "correlation.svg"):
        root = ET.fromstring(dict((n, s) for n, _w, s in figs)[name].encode("utf-8"))
        assert float(root.get("width")) <= 1280 and float(root.get("height")) <= 720


def test_style_options_change_the_figures(payload):
    def fig(name="volcano_DrugA_vs_DMSO.svg", **style):
        return dict((n, s) for n, _w, s in charts.figures(payload, charts.style_from(style)))[name]

    svg = fig(size="col1")
    root = ET.fromstring(svg.encode("utf-8"))
    assert (root.get("width"), root.get("height")) == ("85mm", "70mm")
    assert root.find(f"{SVG}rect").get("fill") == "#ffffff"
    assert "#e34948" in _fills(fig())
    cb = _fills(fig(palette="colorblind"))
    assert "#d55e00" in cb and "#0072b2" in cb and "#e34948" not in cb
    for name in ("volcano_DrugA_vs_DMSO.svg", "heatmap.svg", "correlation.svg", "pca.svg"):
        grey = _fills(fig(name, palette="grey"))
        assert all(c[1:3] == c[3:5] == c[5:7] for c in grey), (name, grey)
    own = _fills(fig(palette="custom", up="#123456", down="#abcdef", neutral="#777777"))
    assert {"#123456", "#abcdef", "#777777"} <= own
    assert ET.fromstring(fig(background="dark").encode("utf-8")).find(f"{SVG}rect").get("fill") == "#1a1a19"
    assert ET.fromstring(fig(background="transparent").encode("utf-8")).find(f"{SVG}rect") is None
    assert ET.fromstring(fig(font_family="Times New Roman").encode("utf-8")).get("font-family") == "'Times New Roman', Times, serif"
    bare = _texts(ET.fromstring(fig(title=False, subtitle=False, legend=False, note=False).encode("utf-8")))
    assert "DrugA vs DMSO" not in bare and not any(t.startswith(("Up ", "Export test")) for t in bare)
    hits = {payload["f"]["label"][i] for i, q in enumerate(payload["comps"][0]["q"])
            if q is not None and q <= 0.05 and abs(payload["comps"][0]["fc"][i]) >= 1}

    def named(**style):
        return [t for t in _texts(ET.fromstring(fig(**style).encode("utf-8"))) if t in hits]

    assert len(named()) >= 3 and named(labels="none") == [] and len(named(label_count=2)) == 2
    thick = fig(line_scale=3, point_scale=2)
    assert 'stroke-width="3" stroke-dasharray="4 4"' in thick and 'r="6.8"' in thick
    assert [n for n, _w, _s in charts.figures(payload, charts.style_from(), ["pca", "correlation"])] == ["pca.svg", "correlation.svg"]


def test_hostile_names_stay_text_and_never_shape_a_file_name(payload):
    d = json.loads(json.dumps(payload))
    d["title"] = '..\\..\\CON<script>alert(1)</script>\r\nExported: never'
    d["comps"][0]["name"] = '<<"&">> vs </text><script>alert(1)</script>\nforged: line'
    d["comps"][0]["slug"] = '..\\../evil<script>:*?"|\x00name.exe'
    d["comps"][0]["t1"] = "</text><script>alert(1)</script>"
    d["f"]["label"] = ['<img src=x onerror=alert(1)> & "q" \x07' for _ in d["f"]["label"]]
    d["samples"] = [f'<script>alert("{i}")</script>' for i in range(len(d["samples"]))]
    d["conditions"] = ["<c&d>", 'D"MS\'O&', "x\x00y"]
    d["cond"] = [d["conditions"][i % 3] for i in range(len(d["cond"]))]
    for name, _what, svg in charts.figures(d, charts.style_from()):
        assert re.fullmatch(r"[A-Za-z0-9_+-][A-Za-z0-9_.+-]*\.svg", name) and ".." not in name, name
        assert not re.search(r"<script|<img|</text><script", svg), name
        assert not re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", svg), name
        root = ET.fromstring(svg.encode("utf-8"))
        assert {el.tag for el in root.iter()} <= {f"{SVG}{t}" for t in ("svg", "title", "desc", "rect", "text", "circle", "line", "g")}
        assert not any(k.lower().startswith("on") for el in root.iter() for k in el.attrib)
    volcano = charts.figures(d, charts.style_from(), ["volcano"])[0]
    assert volcano[0] == "volcano_evil_script_name.exe.svg"
    desc = ET.fromstring(volcano[2].encode("utf-8")).find(f"{SVG}desc").text
    assert 'Comparison: <<"&">> vs </text><script>alert(1)</script> forged: line' in desc
    assert not any(line.startswith(("forged", "Exported")) for line in desc.split("\n"))  # a name adds no line of its own


def test_a_comparison_without_replicates_is_a_fold_change_plot_that_says_so(payload):
    d = json.loads(json.dumps(payload))
    c = d["comps"][0]
    c.update(conf="none", p=[None] * len(c["p"]), q=[None] * len(c["q"]))
    svg = charts.figure_volcano(d, 0, charts.style_from({"title": False}))
    texts = _texts(ET.fromstring(svg.encode("utf-8")))
    assert "FOLD CHANGE ONLY: no replicates, no p-values" in texts and "Below the cut-off" in texts
    assert "mean log2 abundance" in texts and "log2 fold change" in texts
    c["conf"] = "low"
    assert "LOW CONFIDENCE: a group has one sample; p-values borrowed" in _texts(
        ET.fromstring(charts.figure_volcano(payload | {"comps": [dict(payload["comps"][0], conf="low")]}, 0,
                                            charts.style_from({"legend": False, "note": False})).encode("utf-8")))


def test_nothing_to_draw_is_no_figure():
    d = {"title": "x", "comps": [], "f": {"id": [], "label": []}, "qc": {}, "settings": {}}
    assert charts.figures(d, charts.style_from()) == []


# ------------------------------------------------------------ ionomos export --


def test_cli_export_writes_the_figures_and_a_readme(experiment, tmp_path, capsys, monkeypatch):
    dest, _out = experiment
    monkeypatch.chdir(tmp_path)  # no config.yaml here: the export needs none
    out = tmp_path / "figs"
    assert cli.main(["export", str(dest), "--out", str(out)]) == 0
    said = capsys.readouterr().out
    assert "style: 16:9 slide (1280.0 × 720.0 px), text 14 pt Arial, palette default, light background" in said
    assert "5 figure(s) and README.txt" in said and "PNG" in said
    names = sorted(p.name for p in out.iterdir())
    assert names == ["README.txt", "correlation.svg", "heatmap.svg", "pca.svg", "volcano_DrugA_vs_DMSO.svg", "volcano_DrugB_vs_DMSO.svg"]
    readme = (out / "README.txt").read_bytes().decode("utf-8")
    assert readme.startswith("﻿Figures from the Ionomos report\r\n")  # a BOM and CRLF: readable in Notepad
    assert "Experiment:  Export test\r\n" in readme and "|log2FC| ≥ 1 and adjusted p ≤ 0.05\r\n" in readme
    for n in names[1:]:
        assert f"  {n}" in readme
    assert "volcano_DrugA_vs_DMSO.svg  Volcano plot, DrugA vs DMSO. Cut-offs: |log2FC| ≥ 1 and adjusted p ≤ 0.05" in readme
    assert "ionomos export (Ionomos " in ET.fromstring((out / "pca.svg").read_bytes()).find(f"{SVG}desc").text
    # the results folder and report.html work as the target too; the default place is results/figures
    assert cli.main(["export", str(dest / "results"), "--figures", "pca", "--out", str(tmp_path / "one")]) == 0
    assert sorted(p.name for p in (tmp_path / "one").iterdir()) == ["README.txt", "pca.svg"]
    report_before = (dest / "results" / "report.html").read_bytes()
    assert cli.main(["export", str(dest / "results" / "report.html"), "--preset", "col1", "--palette", "grey",
                     "--font-family", "Times New Roman", "--no-note", "--labels", "0", "--background", "transparent"]) == 0
    svg = (dest / "results" / "figures" / "volcano_DrugA_vs_DMSO.svg").read_text(encoding="utf-8")
    root = ET.fromstring(svg.encode("utf-8"))
    assert root.get("width") == "85mm" and root.get("font-family") == "'Times New Roman', Times, serif"
    assert root.find(f"{SVG}rect") is None and "text 7 pt" in root.find(f"{SVG}desc").text
    assert (dest / "results" / "report.html").read_bytes() == report_before  # the report itself is only read
    capsys.readouterr()
    assert cli.main(["export", str(dest), "--width", "400", "--height", "300", "--unit", "px", "--font-pt", "7",
                     "--out", str(tmp_path / "c")]) == 0
    root = ET.fromstring((tmp_path / "c" / "pca.svg").read_bytes())
    assert (root.get("width"), root.get("height")) == ("400", "300")


def test_cli_export_says_plainly_what_it_cannot_do(experiment, tmp_path, capsys, monkeypatch):
    dest, _out = experiment
    monkeypatch.chdir(tmp_path)
    assert cli.main(["export", str(dest), "--format", "png", "--out", str(tmp_path / "p")]) == 2
    err = capsys.readouterr().err
    assert "writes SVG only" in err and "report.html" in err and not (tmp_path / "p").exists()
    assert cli.main(["export", str(tmp_path / "nowhere")]) == 2
    assert "no report.html" in capsys.readouterr().err
    (tmp_path / "plain").mkdir()
    (tmp_path / "plain" / "report.html").write_text("<html><body>the simplified report</body></html>", encoding="utf-8")
    assert cli.main(["export", str(tmp_path / "plain")]) == 2
    assert "carries no data" in capsys.readouterr().err
    assert cli.main(["export", str(dest), "--up", "red", "--palette", "custom", "--out", str(tmp_path / "q")]) == 2
    assert "export.up must be a colour" in capsys.readouterr().err
    assert cli.main(["export", str(dest), "--figures", "volcano,upset", "--out", str(tmp_path / "q")]) == 2
    assert "export.figures must list" in capsys.readouterr().err
    assert cli.main(["export", "17"]) == 2  # a job id needs the lab's ledger
    assert "no job ledger" in capsys.readouterr().err


def test_cli_export_takes_a_style_file_saved_from_the_report(experiment, tmp_path, capsys, monkeypatch):
    dest, _out = experiment
    monkeypatch.chdir(tmp_path)
    style = tmp_path / "export_style.json"  # what the report's "Save style" writes
    style.write_text(json.dumps({"ionomos_export_style": 1, **charts.style_from({"palette": "colorblind", "size": "slide43"})}),
                     encoding="utf-8")
    assert cli.main(["export", str(dest), "--style", str(style), "--out", str(tmp_path / "s"), "--font-pt", "18"]) == 0
    root = ET.fromstring((tmp_path / "s" / "volcano_DrugA_vs_DMSO.svg").read_bytes())
    assert (root.get("width"), root.get("height")) == ("960", "720")
    assert "palette colorblind" in root.find(f"{SVG}desc").text and "text 18 pt" in root.find(f"{SVG}desc").text
    style.write_text('{"ionomos_export_style": 1, "palette": "rainbow"}', encoding="utf-8")
    assert cli.main(["export", str(dest), "--style", str(style), "--out", str(tmp_path / "t")]) == 2
    assert "export.palette must be one of" in capsys.readouterr().err
    style.write_text("not json", encoding="utf-8")
    assert cli.main(["export", str(dest), "--style", str(style), "--out", str(tmp_path / "t")]) == 2
    assert "cannot read the style file" in capsys.readouterr().err


def test_export_uses_the_labs_style_from_config_yaml(experiment, lab, capsys):
    dest, _out = experiment
    d = configio.read_config(lab["cfg_path"])
    d["analysis"]["export"] = {"size": "half", "palette": "grey", "font_family": "Calibri"}
    configio.write_config(lab["cfg_path"], d, backup=False)
    out = lab["root"] / "figs"
    assert cli.main(["--config", str(lab["cfg_path"]), "export", str(dest), "--out", str(out), "--palette", "default"]) == 0
    desc = ET.fromstring((out / "pca.svg").read_bytes()).find(f"{SVG}desc").text
    assert "Half a slide (640.0 × 600.0 px), text 12 pt Calibri, palette default" in desc  # the flag wins over the lab


def test_export_never_replaces_a_file_it_did_not_write(experiment, tmp_path, monkeypatch):
    dest, _out = experiment
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "figs"
    out.mkdir()
    (out / "pca.svg").write_text("<svg>my own drawing</svg>", encoding="utf-8")
    (out / "README.txt").write_text("my notes", encoding="utf-8")
    (out / "notes.docx").write_text("keep me", encoding="utf-8")
    for _ in range(2):  # the second run replaces only what the first one wrote
        assert cli.main(["export", str(dest), "--out", str(out)]) == 0
    assert (out / "pca.svg").read_text(encoding="utf-8") == "<svg>my own drawing</svg>"
    assert (out / "README.txt").read_text(encoding="utf-8") == "my notes"
    assert (out / "notes.docx").read_text(encoding="utf-8") == "keep me"
    assert sorted(p.name for p in out.iterdir()) == ["README.txt", "README_2.txt", "correlation.svg", "heatmap.svg", "notes.docx",
                                                     "pca.svg", "pca_2.svg", "volcano_DrugA_vs_DMSO.svg", "volcano_DrugB_vs_DMSO.svg"]
    assert "pca_2.svg" in (out / "README_2.txt").read_text(encoding="utf-8")


# ------------------------------------------------- the watcher / the analysis --


def test_analysis_writes_the_static_figures_the_lab_asks_for(tmp_path):
    runs = [(f"/x/{c}_{r}.raw", c) for c in ("DMSO", "Drug") for r in (1, 2, 3)]
    simulate.dia_pg_matrix(tmp_path / "e/fragpipe/report.pg_matrix.tsv", runs, seed=6, n_proteins=90)
    lab_cfg = {"export": {"figures": ["volcano", "pca"], "palette": "colorblind", "size": "slide43"}}
    out = downstream.analyze(tmp_path / "e", "DIA", analysis_cfg=lab_cfg, overrides={"export": {"background": "dark"}},
                             context={"experiment": "Lab style"})
    figs = tmp_path / "e" / "results" / "figures"
    assert sorted(p.name for p in figs.iterdir()) == ["README.txt", "pca.svg", "volcano_Drug_vs_DMSO.svg"]
    root = ET.fromstring((figs / "volcano_Drug_vs_DMSO.svg").read_bytes())
    assert (root.get("width"), root.get("height")) == ("960", "720") and root.find(f"{SVG}rect").get("fill") == "#1a1a19"
    assert "palette colorblind, dark background" in root.find(f"{SVG}desc").text
    assert out.summary["figures"] == ["results/figures/volcano_Drug_vs_DMSO.svg", "results/figures/pca.svg",
                                      "results/figures/README.txt"]
    assert out.summary["settings"]["export"] == {"figures": ["volcano", "pca"], "palette": "colorblind", "size": "slide43",
                                                 "font_pt": 14, "background": "dark"}
    html = out.report.read_text(encoding="utf-8")
    assert "<a href='figures/pca.svg'>figures/pca.svg</a>" in html  # listed under Files
    d = slides.read_payload(out.report)
    assert d["exportDefaults"]["palette"] == "colorblind" and d["exportDefaults"]["background"] == "dark"
    assert d["exportDefaults"]["size"] == "slide43" and d["exportDefaults"]["font_family"] == "Arial"
    assert not (tmp_path / "e" / "results" / "analysis_error.txt").exists()


def test_no_figures_folder_unless_asked_and_a_bad_style_costs_nothing(tmp_path):
    runs = [(f"/x/{c}_{r}.raw", c) for c in ("DMSO", "Drug") for r in (1, 2, 3)]
    simulate.dia_pg_matrix(tmp_path / "e/fragpipe/report.pg_matrix.tsv", runs, seed=6, n_proteins=90)
    out = downstream.analyze(tmp_path / "e", "DIA", overrides={"export": {"palette": "rainbow"}})
    assert out.report is not None and not (tmp_path / "e" / "results" / "figures").exists()
    assert out.summary["figures"] == []
    assert any("analysis setting 'export' ignored" in w and "export.palette must be one of" in w for w in out.warnings)
    assert slides.read_payload(out.report)["exportDefaults"] == charts.style_from()


def test_export_settings_layer_like_the_other_analysis_settings():
    s = analysis.settings_from({"export": {"palette": "grey", "figures": ["pca"]}}, {"export": {"size": "col1"}})
    assert s.export == {"palette": "grey", "figures": ["pca"], "size": "col1", "font_pt": 7}
    with pytest.raises(analysis.AnalysisError, match=r"analysis\.export\.size must be one of"):
        analysis.settings_from({"export": {"size": "A4"}})
    with pytest.raises(analysis.AnalysisError, match="unknown export setting 'colour'"):
        analysis.settings_from({"export": {"colour": "blue"}})
    assert analysis.as_dict(s)["export"]["size"] == "col1"


# ------------------------------------------------------------------- config --


def test_the_config_writer_round_trips_the_export_block(tmp_path):
    d = configio.defaults(str(tmp_path / "Auto"), str(tmp_path / "General"))
    assert d["analysis"]["export"] == {"size": "slide169", "font_pt": 14, "font_family": "Arial", "palette": "default",
                                       "background": "light", "figures": []}
    d["analysis"]["export"] = {"size": "custom", "width": 900, "height": 600, "unit": "px", "font_pt": 11.5,
                               "font_family": "Segoe UI", "palette": "custom", "up": "#d55e00", "down": "#0072b2",
                               "neutral": "#b3b3b3", "background": "transparent", "figures": ["volcano", "pca"],
                               "title": False, "label_count": 8, "line_scale": 1.5}
    p = configio.write_config(tmp_path / "config.yaml", d)
    text = p.read_text(encoding="utf-8")
    assert "  export:   # exported figures: the lab's style." in text
    assert '    size: custom   # slide169 (16:9, 1280 x 720 px) | slide43' in text
    assert "    figures: [volcano, pca]   # static SVG written to results/figures after each analysis" in text
    assert yaml.safe_load(text)["analysis"]["export"] == d["analysis"]["export"]
    assert configio.read_config(p)["analysis"]["export"] == d["analysis"]["export"]
    cfg = load(p, check_paths=False)  # the real loader accepts what was written ...
    assert cfg.analysis["export"]["up"] == "#d55e00"
    assert analysis.settings_from(cfg.analysis).export["figures"] == ["volcano", "pca"]
    configio.write_config(p, configio.read_config(p))  # ... and writing it again changes nothing
    assert p.read_text(encoding="utf-8") == text
    # a config from before the block gets it, with the defaults
    old = tmp_path / "old.yaml"
    old.write_text(re.sub(r"  export:.*?\n(?=\S|\n)", "", text, flags=re.S), encoding="utf-8")
    assert "export" not in (yaml.safe_load(old.read_text(encoding="utf-8"))["analysis"])
    assert configio.read_config(old)["analysis"]["export"]["size"] == "slide169"


def test_a_typo_in_the_export_block_fails_at_load_time(tmp_path):
    d = configio.defaults(str(tmp_path / "Auto"), str(tmp_path / "General"))
    d["analysis"]["export"]["palette"] = "colourblind"
    p = configio.write_config(tmp_path / "config.yaml", d)
    with pytest.raises(ConfigError, match=r"analysis: analysis\.export\.palette must be one of default, colorblind"):
        load(p, check_paths=False)


def test_the_example_config_documents_the_block():
    text = (Path(__file__).resolve().parents[1] / "config.example.yaml").read_text(encoding="utf-8")
    block = yaml.safe_load(text)["analysis"]["export"]
    assert charts.style_from(block)["size"] == "slide169" and block["figures"] == []
    for key in ("line_scale", "point_scale", "label_count", "png_scale", "png_dpi", "zip_format", "width", "height", "unit"):
        assert key in text, f"config.example.yaml does not mention export.{key}"
