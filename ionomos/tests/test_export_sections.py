"""Figures for slides from the dose-response, time-course and liganded-site sections, choosing figures and
features, and PNG from `ionomos export` (D68): downstream/sectionfigs.py, charts.catalog, slides.select /
listing, raster.py. The renderers are stubbed: the tests never need cairosvg, resvg or Inkscape, and check both
the branch where one is found and the branch where none is."""
import json
import re
import struct
import subprocess
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

import pytest

from ionomos import cli, downstream
from ionomos.downstream import charts, raster, sectionfigs, simulate, slides

SVG = "{http://www.w3.org/2000/svg}"
SECTION_KINDS = ("dose_potency", "dose_curves", "time_patterns", "time_profiles", "liganded_rank", "liganded_selectivity",
                 "run_order", "kinase_activity")  # the last two: D78, D79


@pytest.fixture(scope="module")
def dose(tmp_path_factory):
    dest = tmp_path_factory.mktemp("dose") / "e"
    simulate.dose_pg_matrix(dest / "fragpipe/diann-output/report.pg_matrix.tsv", [1, 10, 100, 1000, 10000], n=120, seed=4,
                            curve_fraction=0.3)
    out = downstream.analyze(dest, "DIA", analysis_cfg={"enrichment": False}, context={"experiment": "Dose"})
    return dest, slides.read_payload(out.report)


@pytest.fixture(scope="module")
def time(tmp_path_factory):
    dest = tmp_path_factory.mktemp("time") / "e"
    simulate.time_course_pg_matrix(dest / "report.pg_matrix.tsv", {"Drug": ["0h", "1h", "4h", "24h"],
                                                                  "DMSO": ["0h", "1h", "4h", "24h"]}, seed=2)
    out = downstream.analyze(dest, analysis_cfg={"enrichment": False}, context={"experiment": "Time"})
    return dest, slides.read_payload(out.report)


@pytest.fixture(scope="module")
def cys(tmp_path_factory):
    dest = tmp_path_factory.mktemp("cys") / "e"
    simulate.isodtb_label_quant(dest / "fragpipe" / "combined_modified_peptide_label_quant.tsv",
                                {"CmpdA": [1, 2, 3], "CmpdB": [1, 2, 3]}, seed=3)
    out = downstream.analyze(dest, "isoDTB", {"enrichment": False}, context={"experiment": "Cys"})
    return dest, slides.read_payload(out.report)


def _root(svg: str):
    return ET.fromstring(svg.encode("utf-8"))


def _texts(svg: str) -> list[str]:
    return [t.text or "" for t in _root(svg).iter(f"{SVG}text")]


def _plain(name: str, svg: str) -> None:
    """What every exported figure is (D62 point 4): well-formed, no CSS, colours written out, text as text."""
    root = _root(svg)
    assert root.tag == f"{SVG}svg", name
    assert not re.search(r"var\(|<style|class=|style=|foreignObject", svg), name
    cols = set(re.findall(r'(?:fill|stroke)="([^"]+)"', svg)) - {"none"}
    assert all(re.fullmatch(r"#[0-9a-f]{6}", c) for c in cols), (name, cols)
    assert len(list(root.iter(f"{SVG}text"))) > 5, name
    assert "Export style: 16:9 slide" in root.find(f"{SVG}desc").text, name


def _figs(d, **kw):
    return {n: s for n, _w, s in charts.figures(d, charts.style_from(), **kw)}


# ------------------------------------------------------------------- style --


def test_the_new_kinds_and_their_groups_are_export_figures():
    assert charts.STATIC_FIGURES[4:] == SECTION_KINDS == sectionfigs.SECTION_FIGURES
    assert charts.style_from({"figures": ["dose", "pca"]})["figures"] == ["dose_potency", "dose_curves", "pca"]
    assert charts.style_from({"figures": "time"})["figures"] == ["time_patterns", "time_profiles"]
    assert charts.style_from({"figures": ["liganded", "liganded_rank"]})["figures"] == ["liganded_rank", "liganded_selectivity"]
    assert charts.style_from({"figures": "all"})["figures"] == list(charts.STATIC_FIGURES)
    with pytest.raises(charts.StyleError, match="or dose, time, liganded for both of a section's figures"):
        charts.style_layer({"figures": ["doses"]})


# ----------------------------------------------------------------- figures --


def test_dose_response_figures(dose):
    _dest, d = dose
    S = d["dose"]["series"][0]
    figs = _figs(d, which=["dose_potency", "dose_curves"])
    assert list(figs) == ["dose_potency_Cmpd.svg", "dose_curves_Cmpd.svg"]
    for n, s in figs.items():
        _plain(n, s)
    pot = _root(figs["dose_potency_Cmpd.svg"])
    fitted = sum(1 for k in range(len(S["i"])) if S["pec50"][k] is not None and S["fc"][k] is not None)
    assert len(list(pot.iter(f"{SVG}circle"))) == fitted + 4  # a point per curve + the four classes in the legend
    texts = _texts(figs["dose_potency_Cmpd.svg"])
    assert "pEC50 (−log10 M; higher = more potent)" in texts and f"up {S['cls'].count('up')}" in texts
    assert "Dose-response: potency against effect (Cmpd)" in pot.find(f"{SVG}desc").text
    # the curves: the six most relevant regulated, in the payload's order (relevance), each with its fit and points
    top = [k for k in range(len(S["i"])) if S["cls"][k] in ("up", "down")][:sectionfigs.TOP_PANELS]
    names = [d["f"]["label"][S["i"][k]] or d["f"]["id"][S["i"][k]] for k in top]
    texts = _texts(figs["dose_curves_Cmpd.svg"])
    assert texts[0] == "Cmpd: 6 curves" and texts[1].startswith("Dose-response curves (the 6 most relevant regulated)")
    assert [t for t in texts if t in names] == names
    assert sum(t.startswith("pEC50 ") for t in texts) == 6 and texts.count("ctrl") == 6
    assert {"1 nM", "10 µM"} <= set(texts)
    curves = _root(figs["dose_curves_Cmpd.svg"])
    assert sum(1 for p in curves.iter(f"{SVG}path") if p.get("d", "").count("L") == 120) == 6  # the fitted curves
    assert sum(1 for c in curves.iter(f"{SVG}circle") if c.get("fill") == "none") == 6 * len(S["controls"]) * 3  # open: controls
    # chosen features, a size of one panel, and a name that has no curve
    one = charts.figures(d, charts.style_from(), ["dose_curves"], features=[names[3]])
    assert _texts(one[0][2])[0] == "Cmpd: 1 curve" and names[3] in _texts(one[0][2])
    cat = charts.catalog(d, ["dose_curves"], features=[names[0], "NOPE1"])
    assert cat[0].notes == ["not found in the report: NOPE1"]
    assert len(charts.catalog(d, ["dose_curves"], top=2)) == 1
    assert _texts(charts.figures(d, charts.style_from(), ["dose_curves"], top=2)[0][2])[0] == "Cmpd: 2 curves"
    # one journal column cannot hold six readable panels: fewer are drawn, and the figure says so
    small = _texts(charts.figures(d, charts.style_from({"size": "col1"}), ["dose_curves"])[0][2])
    shown = int(re.match(r"Cmpd: (\d) curves?", small[0]).group(1))
    assert 1 <= shown < 6 and f"{shown} of 6: the rest do not fit this size (a larger size, or --top {shown})" in small


def test_time_course_figures(time):
    _dest, d = time
    drug = d["time"]["series"][0]
    figs = _figs(d, which=["time"])
    assert set(figs) >= {"time_patterns_Drug.svg", "time_profiles_Drug.svg"}
    for n, s in figs.items():
        _plain(n, s)
    texts = _texts(figs["time_patterns_Drug.svg"])
    for j, p in enumerate(drug["patterns"]):
        assert f"Pattern {j + 1} · {p['n']:,} feature{'s' if p['n'] != 1 else ''}" in texts
    assert texts.count("24 h") == len(drug["patterns"])
    prof = figs["time_profiles_Drug.svg"]
    texts = _texts(prof)
    assert texts[0] == "Drug: 6 features over time" and "dashed: DMSO" in texts
    first = drug["i"][0]
    assert d["f"]["label"][first] in texts
    root = _root(prof)
    dashed = [p for p in root.iter(f"{SVG}path") if p.get("stroke-dasharray") == "5 4"]
    assert len(dashed) == 6  # the control series in each panel
    # a feature asked for by its gene, and one by a wildcard
    want = d["f"]["label"][drug["i"][10]]
    chosen = charts.figures(d, charts.style_from(), ["time_profiles"], features=[want])
    assert [n for n, _w, _s in chosen] == ["time_profiles_Drug.svg", "time_profiles_DMSO.svg"]
    assert _texts(chosen[0][2])[0] == "Drug: 1 feature over time" and want in _texts(chosen[0][2])


def test_liganded_site_figures(cys):
    _dest, d = cys
    X = d["cys"]
    figs = _figs(d, which=["liganded"])
    assert list(figs) == ["liganded_rank_CmpdA.svg", "liganded_rank_CmpdB.svg", "liganded_selectivity.svg"]
    for n, s in figs.items():
        _plain(n, s)
    A = X["compounds"][0]
    rank = _root(figs["liganded_rank_CmpdA.svg"])
    measured = sum(1 for v in A["r"] if v is not None)
    assert len(list(rank.iter(f"{SVG}circle"))) == measured + len({c for c, v in zip(A["cls"], A["r"], strict=True) if v is not None})
    texts = _texts(figs["liganded_rank_CmpdA.svg"])
    assert "R = 4" in texts and f"liganded {A['counts']['liganded']}" in texts
    assert "liganded: R ≥ 4 (heavy / light) in at least 2 replicates" in _root(figs["liganded_rank_CmpdA.svg"]).find(f"{SVG}desc").text
    sel = figs["liganded_selectivity.svg"]
    texts = _texts(sel)
    n = sum(1 for v in X["nlig"] if v)
    assert f"{n} of {n} sites liganded by any compound" in texts
    words = [t for t in texts if t in ("selective", "shared", "unresolved")]
    assert len(words) == n and words == sorted(words, key=["selective", "shared", "unresolved"].index)  # selective first
    cells = [r for r in _root(sel).iter(f"{SVG}rect") if r.get("height") not in (None,) and r.get("x") != "0"]
    assert len(cells) == 2 * n
    dots = sum(1 for c in _root(sel).iter(f"{SVG}circle") if c.get("r") != "5")
    assert dots == sum(1 for comp in X["compounds"] for k, v in enumerate(X["nlig"]) if v and comp["cls"][k] == 0)
    # sites asked for: rows of the map, names on the rank plot
    site = d["f"]["label"][X["i"][0]]
    chosen = {nm: s for nm, _w, s in charts.figures(d, charts.style_from(), ["liganded"], features=[site])}
    assert "1 of 1 sites asked for" in _texts(chosen["liganded_selectivity.svg"])
    assert site in _texts(chosen["liganded_rank_CmpdA.svg"])
    # one compound: no selectivity map
    one = json.loads(json.dumps(d))
    one["cys"]["compounds"] = one["cys"]["compounds"][:1]
    assert [n for n, _w, _s in charts.figures(one, charts.style_from(), ["liganded"])] == ["liganded_rank_CmpdA.svg"]


def test_style_reaches_the_section_figures(dose, cys):
    for d, name in ((dose[1], "dose_curves_Cmpd.svg"), (cys[1], "liganded_selectivity.svg")):
        def fig(d=d, name=name, **st):
            return {n: s for n, _w, s in charts.figures(d, charts.style_from(st))}[name]

        grey = set(re.findall(r'(?:fill|stroke)="(#[0-9a-f]{6})"', fig(palette="grey")))
        assert all(c[1:3] == c[3:5] == c[5:7] for c in grey), (name, grey)
        root = _root(fig(size="col1"))
        assert root.get("width") == "85mm" and float(root.get("height")[:-2]) <= 70.01
        assert _root(fig(background="dark")).find(f"{SVG}rect").get("fill") == "#1a1a19"
        assert _root(fig(font_family="Georgia")).get("font-family") == "Georgia, 'Times New Roman', Times, serif"
        assert fig(palette="colorblind") != fig() and fig(palette="custom", up="#123456", down="#abcdef") != fig()


def test_hostile_names_in_the_sections_stay_text(dose, cys):
    for d0 in (dose[1], cys[1]):
        d = json.loads(json.dumps(d0))
        d["f"]["label"] = ['<script>alert(1)</script> & "q" \x07' for _ in d["f"]["label"]]
        for part in ("dose", "cys"):
            for s in (d.get(part) or {}).get("series", []) + (d.get(part) or {}).get("compounds", []):
                s["name"] = '..\\..\\CON</text><script>x</script>'
        for name, _what, svg in charts.figures(d, charts.style_from(), list(SECTION_KINDS)):
            assert re.fullmatch(r"[A-Za-z0-9_+-][A-Za-z0-9_.+-]*\.svg", name) and ".." not in name, name
            assert "<script" not in svg and not re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", svg), name
            _root(svg)


def test_nothing_in_a_section_is_no_figure(dose):
    d = json.loads(json.dumps(dose[1]))
    S = d["dose"]["series"][0]
    S["cls"] = ["not"] * len(S["cls"])
    assert [f.name for f in charts.catalog(d, ["dose"])] == ["dose_potency_Cmpd.svg"]  # no regulated curve to draw
    d["dose"] = {"ran": False, "reason": "no doses"}
    assert charts.catalog(d, list(SECTION_KINDS)) == []


def test_features_match_genes_ids_and_patterns():
    d = {"f": {"label": ["GAPDH;GAPDHS", "EGFR", "", "BTK_C481"], "id": ["sp|P04406|G3P_HUMAN", "P00533", "Q9X", "Q06187_C481"]}}
    assert sectionfigs.match_features(d, ["egfr", "P04406", "gapdhs"]) == ([1, 0], [])
    assert sectionfigs.match_features(d, ["Q9X", "BTK*", "nope"]) == ([2, 3], ["nope"])
    assert sectionfigs.match_features(d, ["*"])[0] == [0, 1, 2, 3]


# ------------------------------------------------------- choosing figures --


def test_select_by_kind_group_name_and_pattern(dose, time):
    d = dose[1]
    assert [f.name for f in slides.select(d, ["dose"])] == ["dose_potency_Cmpd.svg", "dose_curves_Cmpd.svg"]
    assert [f.name for f in slides.select(d, ["dose_curves_Cmpd.svg", "pca"])] == ["pca.svg", "dose_curves_Cmpd.svg"]
    assert [f.name for f in slides.select(d, ["DOSE_*"])] == ["dose_potency_Cmpd.svg", "dose_curves_Cmpd.svg"]
    assert [f.name for f in slides.select(time[1], ["time_profiles_D*"])] == ["time_profiles_Drug.svg", "time_profiles_DMSO.svg"]
    with pytest.raises(slides.SlidesError, match=r"no figure 'upset' in this report; its figures: volcano_"):
        slides.select(d, ["upset"])
    text = slides.listing(d, slides.select(d))
    assert "dose_curves_Cmpd" in text and "choose: --features NAME,... or --top N (now 6)" in text
    assert "Pick with --figures: kinds (volcano, pca" in text


def test_cli_list_figures_features_and_top(dose, tmp_path, capsys, monkeypatch):
    dest, d = dose
    monkeypatch.chdir(tmp_path)
    before = sorted(p.name for p in (dest / "results").iterdir())
    assert cli.main(["export", str(dest), "--list"]) == 0
    said = capsys.readouterr().out
    assert said.startswith("Figures in Dose (") and "dose_potency_Cmpd" in said and "volcano_" in said
    assert sorted(p.name for p in (dest / "results").iterdir()) == before  # --list writes nothing
    S = d["dose"]["series"][0]
    gene = d["f"]["label"][S["i"][2]]
    out = tmp_path / "f"
    assert cli.main(["export", str(dest), "--figures", "dose,pca", "--features", f"{gene},NOPE", "--out", str(out)]) == 0
    io = capsys.readouterr()
    assert "note: dose_curves_Cmpd.svg: not found in the report: NOPE" in io.err
    assert sorted(p.name for p in out.iterdir()) == ["README.txt", "dose_curves_Cmpd.svg", "dose_potency_Cmpd.svg", "pca.svg"]
    assert "Cmpd: 1 curve" in _texts((out / "dose_curves_Cmpd.svg").read_text(encoding="utf-8"))
    readme = (out / "README.txt").read_text(encoding="utf-8-sig")
    assert re.search(r"dose_curves_Cmpd\.svg +Dose-response curves, Cmpd: 1 \(chosen\)", readme)
    assert cli.main(["export", str(dest), "--figures", "dose_curves", "--top", "3", "--out", str(tmp_path / "t")]) == 0
    assert "Cmpd: 3 curves" in _texts((tmp_path / "t" / "dose_curves_Cmpd.svg").read_text(encoding="utf-8"))
    assert cli.main(["export", str(dest), "--top", "99"]) == 2
    assert "--top takes 1 to 24" in capsys.readouterr().err


def test_the_watcher_writes_section_figures_when_asked(tmp_path):
    dest = tmp_path / "e"
    simulate.dose_pg_matrix(dest / "fragpipe/diann-output/report.pg_matrix.tsv", [1, 10, 100, 1000], n=60, seed=4,
                            curve_fraction=0.4)
    out = downstream.analyze(dest, "DIA", analysis_cfg={"enrichment": False, "export": {"figures": ["dose"]}})
    names = sorted(p.name for p in (dest / "results" / "figures").iterdir())
    assert names == ["README.txt", "dose_curves_Cmpd.svg", "dose_potency_Cmpd.svg"]
    assert "results/figures/dose_curves_Cmpd.svg" in out.summary["figures"]


# --------------------------------------------------------------------- PNG --


def _png(w: int, h: int, extra: bytes = b"") -> bytes:
    """A real w x h PNG (white, grey 8-bit), as a renderer would write it."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\xff" * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)) + extra
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _chunks(png: bytes) -> list[tuple[bytes, bytes]]:
    out, p = [], 8
    while p < len(png):
        n = struct.unpack(">I", png[p:p + 4])[0]
        kind, data = png[p + 4:p + 8], png[p + 8:p + 8 + n]
        assert struct.unpack(">I", png[p + 8 + n:p + 12 + n])[0] == zlib.crc32(kind + data) & 0xFFFFFFFF
        out.append((kind, data))
        p += 12 + n
    return out


def test_png_size_and_its_chunks_follow_the_style(cys):
    svg = charts.figures(cys[1], charts.style_from(), ["liganded_rank"])[0][2]
    asked = []

    def fake(r, text, w, h):
        asked.append((r.name, w, h))
        return _png(w, h, extra=b"\x00\x00\x00\x09pHYs" + b"\x00" * 13)  # a pHYs of the renderer's own, to be replaced

    r = raster.Renderer("resvg", "/x/resvg")
    raster_render = raster.render
    try:
        raster.render = fake
        png = raster.to_png(svg, charts.style_from(), r)                       # png_scale 2: 2560 x 1440
        assert asked[-1] == ("resvg", 2560, 1440) and raster.png_dimensions(png) == (2560, 1440)
        kinds = [k for k, _d in _chunks(png)]
        assert kinds[:3] == [b"IHDR", b"pHYs", b"iTXt"] and kinds.count(b"pHYs") == 1
        phys = dict(_chunks(png))[b"pHYs"]
        assert struct.unpack(">IIB", phys) == (7559, 7559, 1)                 # 192 dpi in pixels per metre
        itxt = dict(_chunks(png))[b"iTXt"].decode("utf-8")
        assert itxt.startswith("Description\x00\x00\x00\x00\x00Figure: Liganded sites (CmpdA)")
        assert "Export style: 16:9 slide" in itxt and "liganded: R ≥ 4" in itxt
        raster.to_png(svg, charts.style_from({"png_dpi": 300}), r)            # 300 dpi: 4000 x 2250
        assert asked[-1][1:] == (4000, 2250)
        col = charts.figures(cys[1], charts.style_from({"size": "col1", "png_dpi": 600}), ["liganded_rank"])[0][2]
        raster.to_png(col, charts.style_from({"size": "col1", "png_dpi": 600}), r)  # 85 mm at 600 dpi
        assert asked[-1][1] == round(85 / 25.4 * 600)
    finally:
        raster.render = raster_render


def test_finding_a_renderer(monkeypatch, tmp_path):
    monkeypatch.setattr(raster, "_cairosvg", lambda: None)
    monkeypatch.setattr(raster.shutil, "which", lambda name: None)
    monkeypatch.setattr(raster, "_inkscape_places", lambda: [tmp_path / "Inkscape" / "bin" / "inkscape.exe"])
    assert raster.find() is None and raster.find("resvg") is None
    (tmp_path / "Inkscape" / "bin").mkdir(parents=True)
    (tmp_path / "Inkscape" / "bin" / "inkscape.exe").write_bytes(b"")
    assert raster.find().name == "inkscape"  # the usual install folder, not on PATH
    monkeypatch.setattr(raster.shutil, "which", lambda name: f"/bin/{name}" if name == "rsvg-convert" else None)
    assert raster.find() == raster.Renderer("rsvg-convert", "/bin/rsvg-convert")
    monkeypatch.setattr(raster, "_cairosvg", lambda: object())
    assert raster.find().name == "cairosvg" and raster.find("rsvg-convert").name == "rsvg-convert"


def test_renderer_commands_and_failures(monkeypatch):
    seen = []

    def run(cmd, **kw):
        seen.append(cmd)
        assert kw["timeout"] == raster.TIMEOUT and kw["capture_output"]
        out = next(Path(c.split("=", 1)[-1]) for c in cmd if str(c).endswith(".png"))
        if "fail" in cmd[0]:
            return subprocess.CompletedProcess(cmd, 1, b"", b"line one\nError: font not found\n")
        out.write_bytes(_png(4, 3))
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(raster.subprocess, "run", run)
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="2" height="1.5"></svg>'
    for name, flags in (("resvg", ["--width", "4", "--height", "3"]), ("rsvg-convert", ["--width", "4", "--format", "png"]),
                        ("inkscape", ["--export-type=png", "--export-width=4", "--export-height=3"])):
        assert raster.png_dimensions(raster.render(raster.Renderer(name, f"/x/{name}"), svg, 4, 3)) == (4, 3)
        assert all(f in seen[-1] for f in flags), (name, seen[-1])
    with pytest.raises(raster.RasterError, match=r"failed \(exit 1\): line one Error: font not found"):
        raster.render(raster.Renderer("resvg", "/x/fail-resvg"), svg, 4, 3)
    with pytest.raises(raster.RasterError, match="did not write a PNG"):
        raster.png_meta(b"GIF89a" + b"\x00" * 60, 96, "x")


def test_cli_png_with_a_renderer_and_without(cys, tmp_path, capsys, monkeypatch):
    dest, _d = cys
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(raster, "find", lambda prefer="auto": raster.Renderer("resvg", "/x/resvg"))
    monkeypatch.setattr(raster, "render", lambda r, svg, w, h: _png(w, h))
    out = tmp_path / "both"
    assert cli.main(["export", str(dest), "--figures", "liganded", "--format", "both", "--png-dpi", "150", "--out", str(out)]) == 0
    said = capsys.readouterr().out
    assert "PNG by resvg (/x/resvg)" in said
    names = sorted(p.name for p in out.iterdir())
    assert names == ["README.txt", "liganded_rank_CmpdA.png", "liganded_rank_CmpdA.svg", "liganded_rank_CmpdB.png",
                     "liganded_rank_CmpdB.svg", "liganded_selectivity.png", "liganded_selectivity.svg"]
    png = (out / "liganded_rank_CmpdA.png").read_bytes()
    assert raster.png_dimensions(png) == (2000, 1125)  # 1280 x 720 px at 150 dpi
    readme = (out / "README.txt").read_text(encoding="utf-8-sig")
    assert "liganded_rank_CmpdA.png" in readme and "PNG: the Description field" in readme
    # run again: the PNGs Ionomos wrote are replaced, nothing else is touched
    (out / "mine.png").write_bytes(b"\x89PNG not ours")
    assert cli.main(["export", str(dest), "--figures", "liganded_rank_CmpdA", "--format", "png", "--out", str(out)]) == 0
    assert sorted(p.name for p in out.iterdir()) == sorted(names + ["mine.png"])  # the PNG and README were Ionomos'
    assert (out / "mine.png").read_bytes() == b"\x89PNG not ours"
    assert "liganded_selectivity" not in (out / "README.txt").read_text(encoding="utf-8-sig")  # the new README
    # a renderer that fails: nothing is written
    capsys.readouterr()

    def broken(r, svg, w, h):
        raise raster.RasterError("resvg failed (exit 1): no fonts")

    monkeypatch.setattr(raster, "render", broken)
    assert cli.main(["export", str(dest), "--format", "png", "--out", str(tmp_path / "x")]) == 1
    assert "no PNG made, nothing written: resvg failed" in capsys.readouterr().err and not (tmp_path / "x").exists()
    # no renderer at all: a clear message, exit 2, nothing written; --list still works
    monkeypatch.setattr(raster, "find", lambda prefer="auto": None)
    assert cli.main(["export", str(dest), "--format", "both", "--out", str(tmp_path / "y")]) == 2
    err = capsys.readouterr().err
    assert "none was found" in err and "resvg" in err and "Inkscape" in err and "cairosvg" in err and not (tmp_path / "y").exists()
    assert cli.main(["export", str(dest), "--format", "png", "--renderer", "inkscape", "--out", str(tmp_path / "y")]) == 2
    assert "the renderer inkscape was not found" in capsys.readouterr().err
    assert cli.main(["export", str(dest), "--format", "png", "--list"]) == 0
