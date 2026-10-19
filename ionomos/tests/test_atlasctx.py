"""The lab's cysteine atlas beside the liganded sites (downstream/atlasctx.py, D124): reading the lookup the atlas
publishes (good, missing, every kind of malformed), the join (canonical human sites, new sites, isoforms and other
species left blank), the 1-based site numbering, and the report with and without a lookup. Temporary folders only."""
from __future__ import annotations

import html as htmllib
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from ionomos import atlas, names, postprocess
from ionomos.downstream import atlasctx, isodtb, quant, report, simulate
from tests.test_report_contrast import AA, THEMES, _ink, _rgb, contrast

HEADER = list(atlasctx.TSV_COLUMNS)
ROWS = [("P04406", 152, "high", 12, 340, 0),
        ("P04406", 156, "medium", 3, 20, 1),
        ("Q15084", 55, "low", 1, 2, 0)]
META = {"contract": "ionomos-atlas-lookup/1", "atlas_sha256": "ab" * 32, "built": "2026-10-19T08:12:00Z",
        "rule": "Dan's spectra rule of 2026-10-14", "sources": ["isoDTB"], "set": "human", "quotable": False}
DROP = object()


def _publish(folder: Path, rows=ROWS, meta: dict | None = None, header=HEADER, bom=False, crlf=False,
             tsv: bytes | None = None, js: bytes | None = None) -> Path:
    """Write the two lookup files the atlas publishes into folder (an _atlas folder in tmp_path)."""
    folder.mkdir(parents=True, exist_ok=True)
    nl = "\r\n" if crlf else "\n"
    text = nl.join(["\t".join(header)] + ["\t".join(map(str, r)) for r in rows]) + nl
    m = {**META, "rows": len(rows)}
    for k, v in (meta or {}).items():
        if v is DROP:
            m.pop(k, None)
        else:
            m[k] = v
    bom_bytes = b"\xef\xbb\xbf" if bom else b""
    (folder / names.ATLAS_LOOKUP_TSV).write_bytes(tsv if tsv is not None else bom_bytes + text.encode("utf-8"))
    (folder / names.ATLAS_LOOKUP_JSON).write_bytes(js if js is not None else bom_bytes + json.dumps(m).encode("utf-8"))
    return folder


def _row(**change) -> list:
    """ROWS with the first row changed: _row(site="0")."""
    first = dict(zip(HEADER, ROWS[0], strict=True))
    first.update(change)
    return [tuple(first.values()), *ROWS[1:]]


# ---------------------------------------------------------------------------------------------- read_lookup --


@pytest.mark.parametrize(("bom", "crlf"), [(False, False), (True, False), (False, True), (True, True)])
def test_a_good_lookup_is_read_with_or_without_bom_and_crlf(tmp_path, bom, crlf):
    folder = _publish(tmp_path / "_atlas", bom=bom, crlf=crlf)
    lk, note = atlasctx.read_lookup(folder)
    assert lk is not None, note
    assert note == lk.describe() == ("3 sites, built 2026-10-19, rule Dan's spectra rule of 2026-10-14, quotable: no")
    assert lk.folder == folder and lk.meta["atlas_sha256"] == "ab" * 32 and lk.quotable is False
    assert lk.get("P04406", 152) == atlasctx.Site("high", 12, 340, False)
    assert lk.get("p04406 ", "156") == atlasctx.Site("medium", 3, 20, True)    # a row has tier, experiments, ...
    assert (lk.get("P04406", 153), lk.get("P04406", "x"), lk.get("P04406-2", 152)) == (None, None, None)
    assert lk.knows("Q15084") and not lk.knows("P99999")


def test_quotable_tier_case_extra_columns_and_column_order(tmp_path):
    header = ["qc_suspect", "spectra", "experiments", "tier", "site", "accession", "gene"]
    rows = [(r[5], r[4], r[3], r[2].upper(), r[1], r[0], "G") for r in ROWS]
    lk, _note = atlasctx.read_lookup(_publish(tmp_path, rows, {"quotable": True}, header=header))
    assert lk.quotable and lk.describe().endswith("quotable: yes")
    assert lk.get("P04406", 156) == atlasctx.Site("medium", 3, 20, True)


def _missing_folder(tmp):
    return tmp / "never"


def _empty_folder(tmp):
    (tmp / "_atlas").mkdir()
    return tmp / "_atlas"


def _no_json(tmp):
    f = _publish(tmp / "_atlas")
    (f / names.ATLAS_LOOKUP_JSON).unlink()
    return f


def _no_tsv(tmp):
    f = _publish(tmp / "_atlas")
    (f / names.ATLAS_LOOKUP_TSV).unlink()
    return f


def _tsv_is_a_folder(tmp):
    f = _publish(tmp / "_atlas")
    (f / names.ATLAS_LOOKUP_TSV).unlink()
    (f / names.ATLAS_LOOKUP_TSV).mkdir()
    return f


@pytest.mark.parametrize("make", [_missing_folder, _empty_folder, _no_json, _no_tsv, _tsv_is_a_folder])
def test_not_published_yet_is_not_malformed(tmp_path, make):
    lk, note = atlasctx.read_lookup(make(tmp_path))
    assert lk is None and note.startswith("not published yet: no ") and not note.startswith("malformed")
    assert atlasctx.read_lookup(None)[1].startswith("not published yet")


MALFORMED = [
    ("other contract", {"meta": {"contract": "ionomos-atlas-lookup/2"}}, "'ionomos-atlas-lookup/2', not ionomos-atlas-lookup/1"),
    ("no contract", {"meta": {"contract": DROP}}, "has no contract"),
    ("a key missing", {"meta": {"rule": DROP, "set": DROP}}, "lacks rule, set"),
    ("quotable as text", {"meta": {"quotable": "no"}}, "quotable is 'no', not true or false"),
    ("rows as text", {"meta": {"rows": "3"}}, "rows is '3', not a count"),
    ("rows disagree", {"meta": {"rows": 99}}, "says 99 rows, atlas_sites_lookup.tsv has 3"),
    ("built not text", {"meta": {"built": 20261019}}, "built is not text"),
    ("not JSON", {"js": b"{oops"}, "is not JSON"),
    ("JSON list", {"js": b"[]"}, "is not a JSON object"),
    ("JSON not UTF-8", {"js": json.dumps({**META, "rows": 3, "rule": "régle"}, ensure_ascii=False).encode("latin-1")},
     "not UTF-8"),
    ("TSV not UTF-8", {"tsv": "\t".join(HEADER).encode() + b"\nP04406\t152\th\xe9gh\t1\t1\t0\n"}, "not UTF-8"),
    ("TSV empty", {"tsv": b""}, "is empty"),
    ("a column missing", {"header": [h for h in HEADER if h != "tier"]}, "lacks the column tier"),
    ("two columns missing", {"header": ["accession", "site", "tier", "experiments"]}, "columns spectra, qc_suspect"),
    ("site 0", {"rows": _row(site="0")}, "site '0' is not a positive whole number"),
    ("site negative", {"rows": _row(site="-3")}, "site '-3'"),
    ("site decimal", {"rows": _row(site="152.0")}, "site '152.0'"),
    ("site as C152", {"rows": _row(site="C152")}, "site 'C152'"),
    ("site blank", {"rows": _row(site="")}, "site ''"),
    ("site in other digits", {"rows": _row(site="١٥")}, "is not a positive whole number"),
    ("tier misspelt", {"rows": _row(tier="hgh")}, "tier 'hgh' is not high, medium or low"),
    ("tier blank", {"rows": _row(tier="")}, "tier ''"),
    ("qc_suspect 2", {"rows": _row(qc_suspect="2")}, "qc_suspect '2' is not 0 or 1"),
    ("qc_suspect yes", {"rows": _row(qc_suspect="yes")}, "qc_suspect 'yes'"),
    ("experiments not a count", {"rows": _row(experiments="many")}, "experiments 'many' is not a count"),
    ("spectra negative", {"rows": _row(spectra="-1")}, "spectra '-1' is not a count"),
    ("no accession", {"rows": _row(accession=" ")}, "line 2 has no accession"),
    ("a site twice", {"rows": [*ROWS, ROWS[0]]}, "line 5 repeats P04406 site 152"),
    ("a short row", {"rows": [*ROWS, ("P04406", 160, "high")]}, "line 5 has 3 columns, the header 6"),
]


@pytest.mark.parametrize(("why", "kw", "says"), MALFORMED, ids=[m[0] for m in MALFORMED])
def test_a_malformed_lookup_is_refused_with_a_note(tmp_path, why, kw, says):
    folder = _publish(tmp_path / "_atlas", **kw)
    lk, note = atlasctx.read_lookup(folder)
    assert lk is None and note.startswith("malformed: "), note
    assert says in note, note
    assert sorted(p.name for p in folder.iterdir()) == [names.ATLAS_LOOKUP_JSON, names.ATLAS_LOOKUP_TSV]  # read only


def test_a_large_lookup_is_read_in_well_under_a_second_or_two(tmp_path):
    rows = [(f"P{10000 + i // 20:05d}", 1 + (i % 20) * 7, ("high", "medium", "low")[i % 3], i % 9, i % 50, i % 2)
            for i in range(100_000)]
    folder = _publish(tmp_path, rows, crlf=True)
    t0 = time.perf_counter()
    lk, note = atlasctx.read_lookup(folder)
    took = time.perf_counter() - t0
    assert lk is not None and len(lk.sites) == 100_000, note
    assert took < 10, f"{took:.1f} s for 100,000 rows"   # ~0.3 s here; a loaded CI runner gets room


def test_the_reader_and_the_hand_off_share_one_contract(tmp_path):
    assert atlasctx.CONTRACT == atlas.LOOKUP_CONTRACT
    cfg = SimpleNamespace(atlas={"enabled": True, "handoff_dir": str(tmp_path)})
    _publish(tmp_path / names.ATLAS_RETURN_DIR)
    found = [f for f in atlas.check(cfg, write_probe=False) if "atlas site lookup" in f.what]
    assert found and found[0].ok and "3 sites, built 2026-10-19" in found[0].what
    _publish(tmp_path / names.ATLAS_RETURN_DIR, meta={"quotable": "yes"})
    found = [f for f in atlas.check(cfg, write_probe=False) if "atlas site lookup" in f.what]
    assert found and not found[0].ok and "malformed" in found[0].what


# ------------------------------------------------------------------------------------------------- the join --


@pytest.fixture
def lookup(tmp_path):
    lk, note = atlasctx.read_lookup(_publish(tmp_path / "_atlas"))
    assert lk is not None, note
    return lk


HIT = {"tier": "high", "experiments": 12, "spectra": 340, "qc_suspect": False, "new": False}
NEW = {"tier": None, "experiments": None, "spectra": None, "qc_suspect": False, "new": True}
JOIN = [
    ("sp|P04406|G3P_HUMAN|C152", HIT),                                     # GAPDH's active-site cysteine, held
    ("sp|P04406|G3P_HUMAN|C156", {**HIT, "tier": "medium", "experiments": 3, "spectra": 20, "qc_suspect": True}),
    ("sp|P04406|G3P_HUMAN|C247", NEW),                                     # a human site the atlas doesn't have
    ("sp|Q9Y3A3|PHOCN_HUMAN|C7", NEW),                                     # human by its entry name
    ("tr|A0A024R161|A0A024R161_HUMAN|C10", NEW),                           # a 10-character accession
    ("P04406|C152", HIT),                                                  # no entry name: matched all the same
    ("P04406|C999", NEW),                                                  # ... and human: the atlas has the protein
    ("Q00000|C5", None),                                                   # no entry name, protein not in the atlas
    ("sp|P04406-2|G3P_HUMAN|C152", None),                                  # an isoform: never "new"
    ("sp|P16858|G3P_MOUSE|C150", None),                                    # another species: blank
    ("sp|P0A9B2|G3P1_ECOLI|C150", None),
    ("contam_sp|P02768|ALBU_HUMAN|C58", None),                             # a FragPipe contaminant
    ("ENSP00000229239|C152", None),                                        # not a UniProt accession
    ("sp|P04406|G3P_HUMAN|K152", None),                                    # not a cysteine
    ("sp|P04406|G3P_HUMAN", None),                                         # no site
]


@pytest.mark.parametrize(("feature", "want"), JOIN, ids=[j[0] for j in JOIN])
def test_site_context(lookup, feature, want):
    assert atlasctx.site_context(lookup, feature) == want


def test_the_report_payload_gets_one_atlas_column_per_site(lookup):
    ids = [j[0] for j in JOIN]
    cys = {"ran": True, "i": [0, 1, 2, 8, 9, 3], "nlig": [1, 0, 2, 1, 0, 0], "rule": "R ≥ 4"}
    at = atlasctx.with_atlas(cys, lookup, ids)["atlas"]
    assert at["tier"] == [0, 1, None, None, None, None] and at["exp"] == [12, 3, None, None, None, None]
    assert at["spec"] == [340, 20, None, None, None, None] and at["qc"] == [0, 1, 0, 0, 0, 0]
    assert at["new"] == [0, 0, 1, 0, 0, 1]
    assert at["counts"] == {"held": 2, "new": 2, "blank": 2, "ligHeld": 1, "ligNew": 1}
    assert (at["built"], at["quotable"], at["sites"], at["tiers"]) == ("2026-10-19", False, 3, ["high", "medium", "low"])
    assert "internal cysteine atlas" in at["line"] and "not quotable" in at["line"]
    assert "tier rule: Dan's spectra rule of 2026-10-14" in at["line"]
    assert atlasctx.with_atlas({"ran": False, "reason": "x"}, lookup, ids) == {"ran": False, "reason": "x"}


def test_the_site_table_columns(lookup):
    rows = [{"id": j[0], "site": "x"} for j in JOIN[:4]] + [{"id": "sp|P16858|G3P_MOUSE|C150", "site": "y"}]
    cols, out = atlasctx.add_tsv_columns(lookup, ["id", "site"], rows)
    assert cols == ["id", "site", "atlas_tier", "atlas_experiments", "atlas_spectra", "atlas_qc_suspect", "new_to_atlas"]
    cells = [[r[c] for c in cols[2:]] for r in out]
    assert cells == [["high", 12, 340, "no", "no"], ["medium", 3, 20, "yes", "no"], ["", "", "", "", "yes"],
                     ["", "", "", "", "yes"], ["", "", "", "", ""]]


def test_the_site_position_is_the_1_based_position_in_the_protein(tmp_path):
    """The lab's R script (isodtb.py) numbers a site Start + its place in the peptide - 1, with FragPipe's 1-based
    Start: the residue's 1-based position in the protein sequence, the atlas' own numbering."""
    seq = "MAGDKPLTCSEERAGCWTK"
    pep = "PLTCSEER"
    start = seq.find(pep) + 1  # FragPipe's Start counts from 1
    head = ["Peptide Sequence", "Light Modified Peptide", "Start", "Protein", "Protein ID", "Entry Name", "Gene",
            "Protein Description", "S_1 Log2 Ratio HL", "S_2 Log2 Ratio HL"]
    rows = [[pep, "PLTC[561.3387]SEER", str(start), "sp|P99999|TEST_HUMAN", "P99999", "TEST_HUMAN", "TEST", "t",
             "2.1", "2.3"],
            # GAPDH's IISNASCTTNCLAPLAK starts at residue 146: its cysteines are C152 and C156 (CysDB: P04406_C152)
            ["IISNASCTTNCLAPLAK", "IISNASC[561.3387]TTNC[561.3387]LAPLAK", "146", "sp|P04406|G3P_HUMAN", "P04406",
             "G3P_HUMAN", "GAPDH", "GAPDH", "1.0", "1.2"]]
    lq = tmp_path / isodtb.LABEL_FILE
    lq.write_text("\n".join("\t".join(r) for r in [head, *rows]) + "\n", encoding="utf-8")
    (tmp_path / "results").mkdir()
    [sites] = isodtb.write_site_tables(lq, tmp_path / "results")
    ids = [f.id for f in quant.from_isodtb_sites(sites).features]
    assert sorted(ids) == ["sp|P04406|G3P_HUMAN|C152", "sp|P04406|G3P_HUMAN|C156", "sp|P99999|TEST_HUMAN|C9"]
    acc, pos, human = atlasctx.canonical("sp|P99999|TEST_HUMAN|C9")
    assert (acc, pos, human) == ("P99999", 9, True) and seq[pos - 1] == "C" and pos == seq.index("C") + 1
    lk, _ = atlasctx.read_lookup(_publish(tmp_path / "_atlas", [*ROWS, ("P99999", 9, "low", 1, 1, 0)]))
    assert [atlasctx.site_context(lk, i)["tier"] for i in sorted(ids)] == ["high", "medium", "low"]


# ----------------------------------------------------------------------------------------------- the report --


def _isodtb(tmp_path: Path) -> tuple[Path, set[str]]:
    d = tmp_path / "exp"
    hits = simulate.isodtb_label_quant(d / "fragpipe" / isodtb.LABEL_FILE, {"CmpdA": [1, 2, 3]}, seed=3, n_sites=90,
                                       hit_fraction=0.2)
    return d, hits


def _page(out) -> tuple[str, dict]:
    text = out.report.read_text(encoding="utf-8")
    data = json.loads(text.split("<script id='ionomos-data' type='application/json'>")[1].split("</script>")[0])
    return htmllib.unescape(text), data


def _cfg(handoff: Path) -> SimpleNamespace:
    """What run_for_folder reads of a lab config: the analysis settings, the methods, the atlas hand-off."""
    return SimpleNamespace(analysis={"enrichment": False}, methods={},
                           atlas={"enabled": True, "handoff_dir": str(handoff)})


def test_atlas_context_comes_from_the_hand_off_settings(tmp_path):
    assert postprocess.atlas_context(None) == {}
    assert postprocess.atlas_context(SimpleNamespace(atlas={})) == {}
    assert postprocess.atlas_context(SimpleNamespace(atlas={"enabled": False, "handoff_dir": str(tmp_path)})) == {}
    assert postprocess.atlas_context(SimpleNamespace(atlas={"enabled": True, "handoff_dir": ""})) == {}
    assert postprocess.atlas_context(_cfg(tmp_path)) == {"atlas_lookup": tmp_path / names.ATLAS_RETURN_DIR}


def test_a_report_with_the_lookup_shows_the_atlas_columns_and_the_line(tmp_path):
    d, hits = _isodtb(tmp_path)
    held = sorted(hits)[0]
    acc, pos, _human = atlasctx.canonical(held)
    _publish(tmp_path / "dropbox" / names.ATLAS_RETURN_DIR, [*ROWS, (acc, pos, "high", 7, 99, 1)])
    out = postprocess.run_for_folder(d, _cfg(tmp_path / "dropbox"), "isoDTB")
    text, data = _page(out)
    at = data["cys"]["atlas"]
    k = [i for i, t in enumerate(at["tier"]) if t is not None]
    assert len(k) == 1 and data["f"]["id"][data["cys"]["i"][k[0]]] == held
    assert (at["exp"][k[0]], at["spec"][k[0]], at["qc"][k[0]], at["new"][k[0]]) == (7, 99, 1, 0)
    n = len(data["cys"]["i"])
    assert len(at["new"]) == n and at["counts"]["new"] == n - 1 and at["counts"]["blank"] == 0  # every other site
    assert at["counts"]["ligHeld"] == (1 if data["cys"]["nlig"][k[0]] else 0)                   # is human and new
    line = ("Atlas columns: the lab's internal cysteine atlas, not a published resource (built 2026-10-19; tier rule: "
            "Dan's spectra rule of 2026-10-14; 4 human sites). The atlas marks this set as not quotable")
    assert f"<p class='muted' id='cysatlas'>{line}" in text
    assert "looked up in the lab's internal cysteine atlas (built 2026-10-19; 4 human sites)" in text   # Methods
    assert not [x for x in data["notes"] if "atlas" in x]
    head, *rows = (d / "results" / "cysteine_sites.tsv").read_text(encoding="utf-8").splitlines()
    assert head.split("\t")[-len(atlasctx.SITE_COLUMNS):] == atlasctx.SITE_COLUMNS
    mine = next(r.split("\t") for r in rows if r.split("\t")[0] == held)
    assert mine[-5:] == ["high", "7", "99", "yes", "no"]


def test_a_report_without_a_lookup_has_no_atlas_and_a_malformed_one_a_note(tmp_path):
    d, _hits = _isodtb(tmp_path)
    dropbox = tmp_path / "dropbox"
    out = postprocess.run_for_folder(d, _cfg(dropbox), "isoDTB")    # on, but nothing published yet
    text, data = _page(out)
    assert data["cys"]["ran"] and "atlas" not in data["cys"] and "id='cysatlas'" not in text
    assert not [x for x in data["notes"] if "atlas" in x] and "internal cysteine atlas" not in text
    assert "new_to_atlas" not in (d / "results" / "cysteine_sites.tsv").read_text(encoding="utf-8")
    _publish(dropbox / names.ATLAS_RETURN_DIR, _row(tier="hgh"))
    out = postprocess.run_for_folder(d, _cfg(dropbox), "isoDTB")
    text, data = _page(out)
    assert "atlas" not in data["cys"] and "id='cysatlas'" not in text
    note = [x for x in data["notes"] if "atlas" in x]
    assert note == ["cysteine atlas lookup ignored (malformed: atlas_sites_lookup.tsv line 2: tier 'hgh' is not "
                    "high, medium or low)"]
    assert note[0] in text and not [x for x in out.warnings if "atlas" in x]   # the report's note, not the job's


def test_no_atlas_without_liganded_sites(tmp_path):
    folder = _publish(tmp_path / "_atlas", _row(tier="hgh"))
    off = {"ran": False, "reason": "not site ratio data (isoDTB)"}
    assert report._cys_atlas(off, {"atlas_lookup": folder}, None) == (off, "")
    assert report._cys_atlas(None, {"atlas_lookup": folder}, None) == (None, "")
    assert report._cys_atlas({"ran": True, "i": []}, {}, None) == ({"ran": True, "i": []}, "")


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_new_badge_reaches_aa(theme):
    ink = _ink(".badge.anew", THEMES[theme])
    for bg in ("--sunk", "--surface"):
        assert contrast(ink, _rgb(THEMES[theme][bg])) >= AA
