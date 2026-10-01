"""TMT across plexes (downstream/plex.py, D48) and the MSstatsTMT importer (engines.load_msstats_tmt).

- msstats_tmt_summary() against MSstatsTMT 2.20 itself (tests/golden/msstatstmt/, fractions, technical
  replicates, a protein without a Norm value in one mixture), and a median polish checked by hand
- IRS on a planted plex effect: the PCA separates the plexes before and the conditions after
- reference channels from the setting, the SDRF, names; the plex-mean fallback only for balanced designs
- TMT-Integrator abundances are never scaled a second time
- MaxQuant with several experiments and Proteome Discoverer files as plexes, end to end with the report
- Sage's tmt.tsv + results.sage.tsv (engines.load_sage_tmt, D56): the same numbers as the MSstatsTMT summary of
  the same PSMs, plexes and fractions from the file names, channels named by experiment.yaml's tmt: map, and
  IRS on the pool channels, end to end
"""
from __future__ import annotations

import csv
import json
import math
import random
from pathlib import Path

import pytest

from ionomos import downstream
from ionomos.downstream import analysis, anytable, engines, fpa, plex, qc, quant

GOLDEN = Path(__file__).parent / "golden" / "msstatstmt"
TMT10 = plex.TMT_ORDERS[10]
POOL = ("126", "131")
DMSO = ("127N", "127C", "128N", "128C")
DRUG = ("129N", "129C", "130N", "130C")
CFG = {"enrichment": False}


# ------------------------------------------------------------------ helpers --


def _sim(n: int = 160, plexes: int = 3, seed: int = 5, hits: int = 16):
    """{plex: {channel: {protein: linear}}} with a strong plex effect per protein, a pool (126, 131) in every plex,
    4 DMSO and 4 Drug channels; the first `hits` proteins go up 2-fold... 3-fold in Drug."""
    rng = random.Random(seed)
    prots = [f"P{k:05d}" for k in range(n)]
    base = {p: rng.uniform(16, 24) for p in prots}
    up = {p: (rng.uniform(1.2, 1.8) if k < hits else 0.0) for k, p in enumerate(prots)}
    out = {}
    for x in range(1, plexes + 1):
        shift = {p: rng.gauss(0, 1.2) for p in prots}
        load = {c: rng.gauss(0, 0.3) for c in TMT10}
        chans: dict[str, dict[str, float]] = {c: {} for c in TMT10}
        for p in prots:
            truth = {c: base[p] + (up[p] if c in DRUG else 0.0) + rng.gauss(0, 0.25) for c in DMSO + DRUG}
            pool = math.log2(sum(2 ** v for v in truth.values()) / len(truth))
            for c in TMT10:
                v = (pool if c in POOL else truth[c]) + shift[p] + load[c] + rng.gauss(0, 0.1)
                chans[c][p] = 2 ** v
        out[f"Exp{x}"] = chans
    return prots, out, {p for p in prots if up[p]}


def _cond(ch: str) -> str:
    return "Pool" if ch in POOL else "DMSO" if ch in DMSO else "Drug"


def _maxquant(folder: Path, prots, sim, totals: bool = True, summary: bool = True) -> Path:
    txt = folder / "combined" / "txt"
    txt.mkdir(parents=True, exist_ok=True)
    exps = list(sim)
    head = ["Protein IDs", "Majority protein IDs", "Gene names", "Peptides", "Reverse", "Potential contaminant"]
    if totals:
        head += [f"Reporter intensity corrected {k}" for k in range(1, 11)]
    head += [f"Reporter intensity corrected {k} {e}" for e in exps for k in range(1, 11)]
    rows = []
    for p in prots:
        r = [p, p, f"G{p[1:]}", 5, "", ""]
        if totals:
            r += [sum(sim[e][c][p] for e in exps) for c in TMT10]
        r += [sim[e][c][p] for e in exps for c in TMT10]
        rows.append(r)
    with open(txt / "proteinGroups.txt", "w", encoding="utf-8", newline="") as fh:
        fh.write("\t".join(head) + "\n" + "\n".join("\t".join(str(x) for x in r) for r in rows) + "\n")
    if summary:
        lines = ["Raw file\tExperiment\tMS/MS"] + [f"{e}_F{f}\t{e}\t1000" for e in exps for f in (1, 2)] + ["Total\t\t8000"]
        (txt / "summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return txt / "proteinGroups.txt"


def _r2(scores: list[list[float]], groups: list[str], k: int = 0) -> float:
    """One-way ANOVA R² of principal component k by a grouping."""
    xs = [s[k] for s in scores]
    mu = sum(xs) / len(xs)
    tot = sum((x - mu) ** 2 for x in xs) or 1.0
    by: dict[str, list[float]] = {}
    for x, g in zip(xs, groups, strict=True):
        by.setdefault(g, []).append(x)
    between = sum(len(v) * (sum(v) / len(v) - mu) ** 2 for v in by.values())
    return between / tot


def _settings(**kw) -> analysis.Settings:
    return analysis.settings_from({"enrichment": False, **kw})


# ------------------------------------------------------------ channel names --


@pytest.mark.parametrize("label,key", [("TMT126", "126"), ("TMT127N", "127N"), ("tmt10plex-127c", "127C"),
                                       ("TMTpro 134N", "134N"), ("131", "131"), (127, "127"), ("label free sample", ""),
                                       ("AC=MS:1002038;NT=label free sample", "")])
def test_channel_key(label, key):
    assert plex.channel_key(label) == key


def test_same_channel_and_kit_order():
    assert plex.same_channel("TMT131", "131N") and plex.same_channel("126", "TMT126")
    assert not plex.same_channel("131C", "131N") and not plex.same_channel("", "")
    assert plex.channel_from_index(1, 10) == "127N" and plex.channel_from_index(17, 18) == "135N"
    assert plex.channel_from_index(3, 7) == "4"  # an unknown kit size: the position


# ---------------------------------------------------------------- MSstatsTMT --


def test_msstats_tmt_summary_matches_msstatstmt():
    """Golden from MSstatsTMT 2.20.0 (run_msstatstmt.R): fractions combined, global median normalisation, median
    polish per run and the Norm-channel normalisation between runs, to 1e-9 for every protein, run and channel."""
    head, rows = anytable.read_table(GOLDEN / "input.csv")
    s = engines.msstats_tmt_summary(head, rows)
    for name, key in (("expected.csv", "abundance"), ("expected_no_reference_norm.csv", "before")):
        with open(GOLDEN / name, encoding="utf-8") as fh:
            want = {(r["Protein"], r["Run"], r["Channel"]): float(r["Abundance"]) for r in csv.DictReader(fh)}
        got = {(p, tr, ch): v for p, d in s[key].items() for (tr, ch), v in d.items()}
        assert set(got) == set(want), (name, set(got) ^ set(want))
        assert max(abs(got[k] - want[k]) for k in want) < 1e-9, name
    assert s["normalised"] and s["runs"] == 3  # M1_1, M1_2 (technical replicates), M2_1 (two fractions)
    assert not any(k[0] == "M2_1" for k in s["abundance"]["sp|P00007|G7_HUMAN"])  # no Norm there: no values
    assert any(k[0] == "M2_1" for k in s["before"]["sp|P00007|G7_HUMAN"])
    assert any("fractions combined" in n for n in s["notes"])


def _msstats_tmt_rows(rows: list[dict]) -> tuple[list[str], list[list[str]]]:
    cols = ["ProteinName", "PeptideSequence", "Charge", "PSM", "Mixture", "TechRepMixture", "Run", "Channel",
            "Condition", "BioReplicate", "Intensity"]
    return cols, [[str(r.get(c, "")) for c in cols] for r in rows]


def test_msstats_tmt_median_polish_by_hand():
    """One protein, two peptides, three channels, one run: the summary is the Tukey median polish of the
    normalised log2 values, computed here by hand."""
    vals = {"PEPAK": {"126": 2 ** 10, "127": 2 ** 11, "128": 2 ** 13}, "PEPBK": {"126": 2 ** 12, "127": 2 ** 12.5,
                                                                             "128": 2 ** 16}}
    rows = [{"ProteinName": "P1", "PeptideSequence": pep, "Charge": 2, "PSM": f"{pep}_2", "Mixture": "M1",
             "TechRepMixture": 1, "Run": "r1.raw", "Channel": ch, "Condition": "A", "BioReplicate": ch, "Intensity": v}
            for pep, d in vals.items() for ch, v in d.items()]
    s = engines.msstats_tmt_summary(*_msstats_tmt_rows(rows))
    # global norm: channel medians 11, 11.75, 14.5 -> baseline 11.75; shifts +0.75, 0, -2.75
    # normalised: PEPAK 10.75 11 10.25 ; PEPBK 12.75 12.5 13.25
    # row medians 10.75 / 12.75 -> residuals [0, .25, -.5] / [0, -.25, .5]; column medians 0, 0, 0;
    # median of row effects (10.75, 12.75) = 11.75 -> overall 11.75; channel effects 0 -> every channel 11.75
    got = s["abundance"]["P1"]
    assert got[("M1_1", "126")] == pytest.approx(11.75) and got[("M1_1", "128")] == pytest.approx(11.75)
    # an extra channel effect on both peptides comes straight through
    rows2 = [dict(r, Intensity=r["Intensity"] * (4 if r["Channel"] == "128" else 1)) for r in rows]
    s2 = engines.msstats_tmt_summary(*_msstats_tmt_rows(rows2))
    assert s2["abundance"]["P1"][("M1_1", "128")] - s2["abundance"]["P1"][("M1_1", "127")] == pytest.approx(0.0)
    # (the global median normalisation takes a channel-wide shift out; that is MSstatsTMT's global_norm)


def test_msstats_tmt_keeps_the_strongest_psm_and_refuses_empty(tmp_path):
    one = {"Mixture": "M1", "TechRepMixture": 1, "Run": "r1", "Condition": "A", "Charge": 2}
    rows = [{**one, "ProteinName": "P1", "PeptideSequence": "PEPK", "PSM": psm, "Channel": ch, "BioReplicate": ch,
             "Intensity": v} for psm, a, b in (("scan1", 1000, 2000), ("scan2", 3000, 1000))
            for ch, v in (("126", a), ("127", b))]
    rows += [{**one, "ProteinName": "P2", "PeptideSequence": f"PEP{k}K", "PSM": f"PEP{k}K_2", "Channel": ch,
              "BioReplicate": ch, "Intensity": 1000} for k in range(5) for ch in ("126", "127")]  # anchors the medians
    s = engines.msstats_tmt_summary(*_msstats_tmt_rows(rows))
    assert any("several PSMs" in n for n in s["notes"])
    ab = s["abundance"]["P1"]  # scan2 (total 4000) beats scan1 (3000): 127 / 126 = 1000 / 3000
    assert ab[("M1_1", "127")] - ab[("M1_1", "126")] == pytest.approx(math.log2(1000 / 3000))
    cols, bad = _msstats_tmt_rows([{**rows[0], "Intensity": "NA"}])
    f = tmp_path / "t.csv"
    f.write_text(",".join(cols) + "\n" + ",".join(bad[0]) + "\n", encoding="utf-8")
    with pytest.raises(anytable.TableError, match="MSstatsTMT"):
        engines.load_msstats_tmt(f)


def _write_msstats_tmt(path: Path, prots, sim, techreps: int = 1) -> Path:
    rng = random.Random(3)
    peps = {p: [(f"PEP{p[1:]}{a}K", 2 ** rng.uniform(-1.5, 1.5)) for a in "ACD"] for p in prots}
    cols = ["ProteinName", "PeptideSequence", "Charge", "PSM", "Mixture", "TechRepMixture", "Run", "Channel",
            "Condition", "BioReplicate", "Intensity"]
    lines = [",".join(cols)]
    for x, (mix, chans) in enumerate(sim.items(), 1):
        for t in range(1, techreps + 1):
            for p in prots:
                for pep, share in peps[p]:
                    for c in TMT10:
                        cond = "Norm" if c in POOL else _cond(c)
                        bio = "Norm" if cond == "Norm" else f"{cond}_{x}_{c}"
                        v = chans[c][p] * share * 2 ** rng.gauss(0, 0.05)
                        lines.append(",".join([p, pep, "2", f"{pep}_2", mix, str(t), f"{mix}_T{t}.raw", c, cond, bio,
                                               f"{v:.3f}"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_msstats_tmt_end_to_end_normalised_on_norm_channels(tmp_path):
    prots, sim, hits = _sim(n=120, hits=12)
    f = _write_msstats_tmt(tmp_path / "q" / "msstatstmt.csv", prots, sim, techreps=2)
    found = engines.detect(tmp_path / "q")
    assert found.method == "MSstatsTMT"
    m = engines.load_msstats_tmt(f)
    assert len(m.samples) == 3 * 8 and not any(m.condition[s] == "Norm" for s in m.samples)  # Norm channels left out
    assert m.samples[0] == "Exp1_127N" and m.meta["plex"]["Exp1_127N"] == "Exp1" and m.exp == "TMT"
    assert m.meta["bridge"]["applied"] and any("technical replicates" in n for n in m.notes)
    out = downstream.analyze(tmp_path / "q", analysis_cfg=CFG)
    s = json.loads((out.results_dir / "analysis.json").read_text(encoding="utf-8"))
    assert s["tmt"]["method"].startswith("MSstatsTMT") and s["design"]["conditions_from"] == "the engine's table"
    assert s["comparisons"][0]["name"] == "Drug vs DMSO"
    with open(out.results_dir / "Drug_vs_DMSO_differential.tsv", encoding="utf-8") as fh:
        sig = {r["id"] for r in csv.DictReader(fh, delimiter="\t") if r["significant"]}
    assert len(sig & hits) >= 0.8 * len(hits) and len(sig - hits) <= 2
    html = out.report.read_text(encoding="utf-8")
    assert '"pcaBefore"' in html and '"plex"' in html and "as in MSstatsTMT" in html


# ----------------------------------------------------------------------- IRS --


def _loaded(tmp_path, **kw):
    prots, sim, hits = _sim(**kw)
    m = engines.load_maxquant(_maxquant(tmp_path / "mq", prots, sim))
    for s in m.samples:  # the conditions a design (SDRF / sample_conditions) would give
        m.condition[s] = _cond(m.meta["channel"][s])
    return m, hits


def test_maxquant_experiments_are_plexes_and_totals_are_dropped(tmp_path):
    prots, sim, _ = _sim(n=30)
    m = engines.load_maxquant(_maxquant(tmp_path / "mq", prots, sim))
    assert len(m.samples) == 30 and m.samples[0] == "1 Exp1"  # 3 x 10 channels; the 10 totals left out
    assert m.meta["channel"]["2 Exp3"] == "127N" and m.meta["plex"]["2 Exp3"] == "Exp3"
    assert m.meta["runs"]["1 Exp2"] == ["Exp2_F1", "Exp2_F2"]  # summary.txt: the experiment's raw files
    assert any("summed over experiments" in n for n in m.notes)


def test_irs_removes_a_planted_plex_effect(tmp_path):
    m, hits = _loaded(tmp_path)
    st = _settings(tmt_reference=["126", "131"])
    raw, _n = fpa.process(m, normalization="median", imputation="none")
    before = qc.pca(raw.m.values)
    plexes = [raw.m.meta["plex"][s] for s in raw.m.samples]
    assert _r2(before["scores"], plexes) > 0.8  # before: PC1 is the plex
    m2, info, notes = plex.normalise(m, st)
    assert info["applied"] and info["method"] == "IRS (reference channel)" and info["plexes"] == {
        "Exp1": 10, "Exp2": 10, "Exp3": 10}
    assert sorted(info["removed"]) == sorted(s for s in m.samples if m.meta["channel"][s] in POOL)
    assert len(m2.samples) == 24 and "IRS put 3 TMT plexes" in notes[0]
    p, _n = fpa.process(m2, normalization="median", imputation="none")
    after = qc.pca(p.m.values)
    groups = [p.m.meta["plex"][s] for s in p.m.samples]
    conds = [p.m.condition[s] for s in p.m.samples]
    assert _r2(after["scores"], groups) < 0.15 and _r2(after["scores"], conds) > 0.6
    back = plex.pca_before(p, st)  # the report's "before" PCA: same samples, before IRS
    assert len(back["scores"]) == len(p.m.samples) and _r2(back["scores"], groups) > 0.8


def test_irs_math_on_two_plexes():
    """r_ip = log2(mean of the plex's references), g_i = their mean, every channel + g_i - r_ip."""
    samples = ["a_ref", "a_x", "b_ref", "b_x"]
    m = quant.QuantMatrix("intensity", "protein", [quant.Feature("P1", "P1"), quant.Feature("P2", "P2")], samples,
                          [[10.0, 12.0, 14.0, 15.0], [8.0, 9.0, None, 7.0]],
                          {"a_ref": "Pool", "a_x": "X", "b_ref": "Pool", "b_x": "X"}, exp="TMT",
                          meta={"plex": {"a_ref": "A", "a_x": "A", "b_ref": "B", "b_x": "B"}})
    m2, info, _ = plex.normalise(m, _settings())  # the references are found by name (Pool)
    assert m2.samples == ["a_x", "b_x"] and info["reference_from"].startswith("sample / condition names")
    # P1: refs 10 / 14, g = 12 -> a +2, b -2 ; P2: plex B has no reference -> its value is missing
    assert m2.values[0] == [14.0, 13.0] and m2.values[1] == [9.0, None] and info["values_dropped"] == 1


def test_no_reference_unbalanced_is_refused_with_a_warning(tmp_path):
    m, _ = _loaded(tmp_path, n=60)
    for s in m.samples:  # plex 3 holds only Drug: plex means would scale the drug effect away
        if m.meta["plex"][s] == "Exp3" and m.condition[s] == "DMSO":
            m.condition[s] = "Drug"
        if m.condition[s] == "Pool":
            m.condition[s] = "DMSO"  # and nothing is called a pool
    m2, info, notes = plex.normalise(m, _settings())
    assert m2 is m and not info["applied"] and "different mixes of conditions" in info["reason"]
    assert "NOT put on a common scale" in notes[0]
    from ionomos.downstream import doctor

    f = doctor.Findings(method="MaxQuant", loaded=m, settings=_settings(), tmt=info)
    assert "TMT_PLEXES_NOT_NORMALISED" in {i.code for i in doctor.check(f)}


def test_no_reference_balanced_uses_the_plex_means(tmp_path):
    m, _ = _loaded(tmp_path, n=60)
    keep = [j for j, s in enumerate(m.samples) if m.meta["channel"][s] not in POOL]  # no pool channels at all
    m = plex.keep_samples(m, keep)
    m2, info, notes = plex.normalise(m, _settings())
    assert info["method"] == "IRS (plex means)" and len(m2.samples) == len(m.samples)
    p, _n = fpa.process(m2, normalization="median", imputation="none")
    assert _r2(qc.pca(p.m.values)["scores"], [p.m.meta["plex"][s] for s in p.m.samples]) < 0.15
    m3, info3, _ = plex.normalise(m, _settings(irs="reference"))  # asked for a reference that isn't there
    assert m3 is m and not info3["applied"]


def test_irs_none_single_plex_and_label_free_untouched(tmp_path):
    m, _ = _loaded(tmp_path, n=20)
    m2, info, notes = plex.normalise(m, _settings(irs="none"))
    assert m2 is m and info["reason"] == "switched off (irs: none)" and "irs: none" in notes[0]
    one = plex.keep_samples(m, [j for j, s in enumerate(m.samples) if m.meta["plex"][s] == "Exp1"])
    assert plex.normalise(one, _settings())[1] is None
    lf = quant.QuantMatrix("intensity", "protein", [quant.Feature("P", "P")], ["a", "b"], [[1.0, 2.0]],
                           {"a": "A", "b": "B"}, exp="DIA")
    assert plex.normalise(lf, _settings())[0] is lf


def test_tmt_integrator_is_never_scaled_twice():
    path = Path(__file__).parent / "golden" / "tmt_abundance_gene_MD.tsv"
    m = quant.from_tmt_abundance(path)
    m.meta["plex"] = {s: str(k % 2) for k, s in enumerate(m.samples)}  # even with plexes known
    values = [list(r) for r in m.values]
    m2, info, notes = plex.normalise(m, _settings(irs="reference", tmt_reference=["126"]))
    assert m2 is m and m2.values == values and not info["applied"]
    assert "already log2 ratios to the reference" in info["reason"] and notes
    m.meta.pop("plex")
    assert plex.normalise(m, _settings())[2] == []  # one FragPipe TMT run: recorded, not noted


def test_settings_validate_irs_and_lists():
    s = _settings(irs=False, tmt_reference=126, sdrf_factor="compound, dose")
    assert s.irs == "none" and s.tmt_reference == ["126"] and s.sdrf_factor == ["compound", "dose"]
    with pytest.raises(analysis.AnalysisError, match="irs must be"):
        _settings(irs="bridge")


def test_experiment_yaml_reference_channel_is_the_setting(tmp_path):
    from ionomos import postprocess
    from ionomos.manifest import parse_overrides, tmt_annotation_files

    ov = parse_overrides({"tmt": {"reference_channel": 126}})  # no channels: fine for engines FragPipe didn't run
    assert tmt_annotation_files(ov, ["plex1"]) == {}
    (tmp_path / "experiment.yaml").write_text("tmt:\n  reference_channel: 126\nanalysis:\n  log2fc: 1\n",
                                              encoding="utf-8")
    p = postprocess.prepare(tmp_path, None)
    assert p["overrides"]["tmt_reference"] == 126 and _settings(**p["overrides"]).tmt_reference == ["126"]


def test_maxquant_multiplex_end_to_end_with_reference_setting(tmp_path):
    prots, sim, hits = _sim(n=150, hits=15)
    _maxquant(tmp_path / "mq", prots, sim)
    conds = {f"{k} {e}": _cond(TMT10[k - 1]) for e in sim for k in range(1, 11)}
    out = downstream.analyze(tmp_path / "mq", analysis_cfg={**CFG, "tmt_reference": ["126", "131"],
                                                             "sample_conditions": conds})
    s = json.loads((out.results_dir / "analysis.json").read_text(encoding="utf-8"))
    assert s["tmt"]["applied"] and s["tmt"]["method"] == "IRS (reference channel)"
    assert set(s["samples"].values()) == {"DMSO", "Drug"}  # the pools left the analysis
    with open(out.results_dir / "Drug_vs_DMSO_differential.tsv", encoding="utf-8") as fh:
        sig = {r["id"] for r in csv.DictReader(fh, delimiter="\t") if r["significant"]}
    assert len(sig & hits) >= 0.8 * len(hits) and len(sig - hits) <= 2
    html = out.report.read_text(encoding="utf-8")
    assert "internal reference scaling" in html and '"pcaBefore"' in html
    assert "TMT_PLEXES_NOT_NORMALISED" not in {i["code"] for i in s["issues"]}
    # without a reference and without a design, the plexes stay apart and a warning says why
    out2 = downstream.analyze(tmp_path / "mq", analysis_cfg=CFG)
    s2 = json.loads((out2.results_dir / "analysis.json").read_text(encoding="utf-8"))
    assert not s2["tmt"]["applied"] and "TMT_PLEXES_NOT_NORMALISED" in {i["code"] for i in s2["issues"]}


def test_proteome_discoverer_files_are_plexes(tmp_path):
    prots, sim, _ = _sim(n=40, plexes=2)
    head = ["Accession", "Description"] + [f"Abundance: F{x}: {c}, Sample, {_cond(c)}" for x in (1, 2) for c in TMT10]
    rows = [[p, p] + [sim[f"Exp{x}"][c][p] for x in (1, 2) for c in TMT10] for p in prots]
    f = tmp_path / "pd" / "proteins.txt"
    f.parent.mkdir()
    f.write_text("\t".join(head) + "\n" + "\n".join("\t".join(str(v) for v in r) for r in rows) + "\n", encoding="utf-8")
    m = engines.load_pd(f)
    assert m.exp == "TMT" and set(m.meta["plex"].values()) == {"F1", "F2"} and m.meta["channel"][m.samples[1]] == "127N"
    m2, info, _ = plex.normalise(m, _settings())  # the pools are named "Pool" by the export's conditions
    assert info["applied"] and len(m2.samples) == 16


# ------------------------------------------------------------------ Sage TMT --

SAGE_PSM = ["psm_id", "peptide", "proteins", "filename", "scannr", "rank", "label", "charge", "spectrum_q",
            "peptide_q", "protein_q"]
SAGE_TMT = ["filename", "scannr", "ion_injection_time", *[f"tmt_{k}" for k in range(1, 11)]]


def _tsv(path: Path, head: list[str], rows: list[list]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join("\t".join(str(x) for x in r) for r in [head, *rows]) + "\n", encoding="utf-8")
    return path


def _write_sage_tmt(folder: Path, prots, sim) -> tuple[Path, list[dict]]:
    """Sage's output for the simulated plexes: three peptides per protein, one PSM each, spread over the two
    fraction files of a plex (<plex>_F1.mzML, <plex>_F2.mzML). Returns tmt.tsv and the same PSMs as MSstatsTMT
    rows."""
    rng = random.Random(3)
    peps = {p: [(f"PEP{p[1:]}{a}K", 2 ** rng.uniform(-1.5, 1.5)) for a in "ACD"] for p in prots}
    psm, tmt, long = [], [], []
    for mix, chans in sim.items():
        for k, p in enumerate(prots):
            for j, (pep, share) in enumerate(peps[p]):
                file, scan = f"{mix}_F{1 + (k + j) % 2}.mzML", f"controllerType=0 controllerNumber=1 scan={len(psm) + 1}"
                vals = [round(chans[c][p] * share * 2 ** rng.gauss(0, 0.05), 3) for c in TMT10]
                psm.append([len(psm) + 1, pep, f"sp|{p}|G{p[1:]}_HUMAN", file, scan, 1, 1, 2, 0.001, 0.001, 0.001])
                tmt.append([file, scan, 50.0, *vals])
                long += [{"ProteinName": p, "PeptideSequence": pep, "Charge": 2, "PSM": scan, "Mixture": mix,
                          "TechRepMixture": 1, "Run": file, "Channel": c, "Condition": _cond(c), "BioReplicate": c,
                          "Intensity": v} for c, v in zip(TMT10, vals, strict=True)]
    rng.shuffle(tmt)   # Sage writes its rows from parallel workers: in no order
    _tsv(folder / "results.sage.tsv", SAGE_PSM, psm)
    (folder / "results.json").write_text(json.dumps({"version": "0.14.7", "quant": {"tmt": "Tmt10"},
                                                     "database": {"decoy_tag": "rev_"}}), encoding="utf-8")
    return _tsv(folder / "tmt.tsv", SAGE_TMT, tmt), long


def test_sage_tmt_is_the_msstatstmt_summary_of_its_psms(tmp_path):
    prots, sim, _ = _sim(n=40)
    path, long = _write_sage_tmt(tmp_path / "sage", prots, sim)
    assert engines.detect(tmp_path).path == path and engines.detect(tmp_path).method == "Sage"
    m = engines.load_sage_tmt(path)
    assert m.exp == "TMT" and len(m.samples) == 30 and m.samples[:2] == ["Exp1_126", "Exp1_127N"]
    assert m.meta["plex"]["Exp3_131"] == "Exp3" and m.meta["channel"]["Exp3_131"] == "131"   # tmt_10 of a Tmt10 kit
    assert m.meta["runs"]["Exp2_126"] == ["Exp2_F1", "Exp2_F2"]                              # the plex's fractions
    assert set(m.condition.values()) == {plex.UNASSIGNED} and m.replicate == {}
    assert [f.id for f in m.features] == prots and m.features[0].label == "G00000" and m.features[0].peptides == 3
    want = engines.msstats_tmt_summary(*_msstats_tmt_rows(long))["before"]   # no Norm: nothing between the plexes
    for f, row in zip(m.features, m.values, strict=True):
        for s, v in zip(m.samples, row, strict=True):
            assert v == pytest.approx(want[f.id][(f"{m.meta['plex'][s]}_1", m.meta["channel"][s])], abs=1e-9)
    # the plexes are still apart: that is plex.py's job, and it refuses to guess while the channels have no condition
    m2, info, notes = plex.normalise(m, _settings())
    assert m2 is m and not info["applied"] and "no condition yet" in info["reason"]
    m3, info3, _ = plex.normalise(m, _settings(tmt_reference=["126", "131"]))
    assert info3["applied"] and len(m3.samples) == 24


def test_sage_tmt_end_to_end_with_the_experiment_yaml_channel_map(tmp_path):
    from ionomos import postprocess

    prots, sim, hits = _sim(n=150, hits=15)
    _write_sage_tmt(tmp_path / "exp" / "sage", prots, sim)
    names = {c: ("NA" if c == "131" else f"{_cond(c)}_{c}") for c in TMT10}    # the second pool is called unused
    (tmp_path / "exp" / "experiment.yaml").write_text(
        "tmt:\n  reference_channel: 126\n  channels:\n" + "".join(f"    '{c}': {n}\n" for c, n in names.items())
        + "analysis:\n  enrichment: false\n", encoding="utf-8")
    out = postprocess.run_for_folder(tmp_path / "exp", None)
    s = json.loads((out.results_dir / "analysis.json").read_text(encoding="utf-8"))
    assert s["method"] == "Sage" and s["tmt"]["applied"] and s["tmt"]["reference_from"] == "tmt_reference 126"
    assert s["tmt"]["plexes"] == {"Exp1": 9, "Exp2": 9, "Exp3": 9}             # 131 left out before the summary
    assert len(s["samples"]) == 24 and s["samples"]["Exp2_Drug_129N"] == "Drug"
    assert s["design"]["conditions_from"] == "experiment.yaml tmt: channel map"
    assert any("3 channel(s) called NA / empty" in n for n in s["notes"])
    with open(out.results_dir / "Drug_vs_DMSO_differential.tsv", encoding="utf-8") as fh:
        sig = {r["id"] for r in csv.DictReader(fh, delimiter="\t") if r["significant"]}
    assert len(sig & hits) >= 0.8 * len(hits) and len(sig - hits) <= 2
    html = out.report.read_text(encoding="utf-8")
    assert "internal reference scaling" in html and '"pcaBefore"' in html and "Quantities from Sage 0.14.7" in html
    assert not (tmp_path / "exp" / "sage" / "results").exists()                # nothing written into Sage's folder


def test_sage_tmt_filters_and_joins_on_file_and_scan(tmp_path):
    f = "plexA_F1.mzML"
    psm = [
        [1, "AAAK", "sp|P1|ONE_HUMAN", f, "scan=1", 1, 1, 2, 0.001, 0.001, 0.001],
        [2, "AAAK", "sp|P1|ONE_HUMAN", f, "scan=2", 1, 1, 2, 0.001, 0.001, 0.001],    # the weaker PSM of the ion
        [3, "CCCK", "sp|P1|ONE_HUMAN;sp|P2|TWO_HUMAN;rev_sp|P9|X_HUMAN", f, "scan=3", 1, 1, 2, 0.001, 0.001, 0.001],
        [4, "DDDK", "sp|P2|TWO_HUMAN", "plexA_F2.mzML.gz", "scan=1", 1, 1, 3, 0.001, 0.001, 0.001],   # same scan id,
        [5, "DECOYK", "rev_sp|P3|THREE_HUMAN", f, "scan=4", 1, -1, 2, 0.001, 0.001, 0.001],            # another file
        [6, "RANKK", "sp|P4|FOUR_HUMAN", f, "scan=4", 2, 1, 2, 0.001, 0.001, 0.001],
        [7, "SPECQK", "sp|P4|FOUR_HUMAN", f, "scan=5", 1, 1, 2, 0.05, 0.001, 0.001],
        [8, "PEPQK", "sp|P4|FOUR_HUMAN", f, "scan=6", 1, 1, 2, 0.001, 0.05, 0.001],
        [9, "PROTQK", "sp|P4|FOUR_HUMAN", f, "scan=7", 1, 1, 2, 0.001, 0.001, 0.05],
        [10, "CHIMERAK", "sp|P5|FIVE_HUMAN", f, "scan=8", 1, 1, 2, 0.001, 0.001, 0.001],
        [11, "CHIMERBK", "sp|P5|FIVE_HUMAN", f, "scan=8", 1, 1, 2, 0.001, 0.001, 0.001],   # two peptides, one spectrum
        [12, "NOIONSK", "sp|P6|SIX_HUMAN", f, "scan=9", 1, 1, 2, 0.001, 0.001, 0.001],     # reporters all 0
        [13, "NOROWK", "sp|P6|SIX_HUMAN", f, "scan=10", 1, 1, 2, 0.001, 0.001, 0.001],     # no row in tmt.tsv
    ]
    six = ["filename", "scannr", "ion_injection_time", *[f"tmt_{k}" for k in range(1, 7)]]
    big = [1e9] * 6
    tmt = [[f, "scan=1", 20, 400, 800, 1600, 0, 400, 400], [f, "scan=2", 20, 1, 1, 1, 1, 1, 1],
           [f, "scan=3", 20, 100, 200, 400, 100, 100, 100], ["plexA_F2.mzML.gz", "scan=1", 20, 64, 64, 64, 64, 64, 64],
           [f, "scan=3", 20, 1, 1, 1, 1, 1, 1],                 # a second MS3 of scan 3: the larger total is kept
           *[[f, f"scan={k}", 20, *big] for k in (4, 5, 6, 7, 8)], [f, "scan=9", 20, 0, 0, 0, 0, 0, 0],
           [f, "scan=99", 20, *big]]                            # a spectrum nothing identified
    _tsv(tmp_path / "results.sage.tsv", SAGE_PSM, psm)
    path = _tsv(tmp_path / "tmt.tsv", six, tmt)
    m = engines.load_sage_tmt(path, tmt={"channels": {126: "Ctrl_1", 127: "Ctrl_2", 128: "Drug_1", 129: "Drug_2",
                                                      130: "empty", "131": "NA"}})
    assert m.samples == ["Ctrl_1", "Ctrl_2", "Drug_1", "Drug_2"] and m.replicate["Drug_2"] == 2
    assert m.condition == {"Ctrl_1": "Ctrl", "Ctrl_2": "Ctrl", "Drug_1": "Drug", "Drug_2": "Drug"}
    assert m.meta["channel"] == {"Ctrl_1": "126", "Ctrl_2": "127", "Drug_1": "128", "Drug_2": "129"}   # the Tmt6 kit
    assert set(m.meta["plex"].values()) == {"plexA"} and m.meta["runs"]["Ctrl_1"] == ["plexA_F1", "plexA_F2"]
    assert [x.id for x in m.features] == ["P1", "P2"] and [x.peptides for x in m.features] == [2, 1]
    # channel medians over AAAK (scan 1), CCCK (the first scan 3) and DDDK (scan 1 of the other file): 100, 200, 400,
    # so the global median normalisation leaves P1 level over 126-128 and takes the flat DDDK down 1 per channel
    p1, p2 = m.values
    assert p1[0] == pytest.approx(p1[1]) and p1[1] == pytest.approx(p1[2]) and p1[3] is not None
    assert p2[0] - p2[1] == pytest.approx(1.0) and p2[1] - p2[2] == pytest.approx(1.0)
    text = " | ".join(m.notes)
    assert "2 protein groups in 1 plex(es) from 4 PSMs" in text
    assert "of 13 PSMs in results.sage.tsv, left out: 1 decoy, 1 of lower rank, 3 above a q-value" in text
    assert "1 spectra with more than one passing PSM" in text and "2 passing PSM(s) had no reporter ions" in text
    assert "1 peptide(s) shared" in text and "2 channel(s) called NA / empty" in text
    assert "1 features had several PSMs in a run" in text and "fractions combined within 1 mixture run(s)" in text
    # the manifest names the plex of each file; a file it doesn't know keeps the plex in its name
    m2 = engines.load_sage_tmt(path, {"plexA_F1": ("mix1", 1), "gone_F1": ("mix2", 1)})
    assert set(m2.meta["plex"].values()) == {"mix1", "plexA"} and m2.samples[0] == "mix1_126"
    assert m2.meta["missing_runs"] == ["gone_F1"] and m2.meta["unmatched_runs"] == ["plexA_F2"]
    # custom reporter masses: no kit, so no channel labels
    user = _tsv(tmp_path / "u" / "tmt.tsv", [*six[:3], "user_1", "user_2"], [[f, "scan=1", 20, 5, 6]])
    _tsv(tmp_path / "u" / "results.sage.tsv", SAGE_PSM, psm[:1])
    m3 = engines.load_sage_tmt(user)
    assert m3.samples == ["plexA_user_1", "plexA_user_2"] and "channel" not in m3.meta
    assert any("not one of Sage's TMT kits" in n for n in m3.notes)
    # without the PSM table, or with nothing passing, the table can't be used
    (tmp_path / "u" / "results.sage.tsv").unlink()
    with pytest.raises(anytable.TableError, match="results.sage.tsv"):
        engines.load_sage_tmt(user)
    _tsv(tmp_path / "u" / "results.sage.tsv", SAGE_PSM, psm[4:9])
    with pytest.raises(anytable.TableError, match="no PSM"):
        engines.load_sage_tmt(user)


def test_sage_folder_with_both_tables_is_read_as_tmt(tmp_path):
    prots, sim, _ = _sim(n=20, plexes=1)
    path, _long = _write_sage_tmt(tmp_path / "sage", prots, sim)
    _tsv(tmp_path / "sage" / "lfq.tsv", ["peptide", "charge", "proteins", "q_value", "score", "spectral_angle",
                                         "Exp1_F1.mzML"], [["AAAK", 2, "sp|P1|ONE_HUMAN", 0.001, 1, 0.9, 100]])
    assert [d.path.name for d in engines.detect_all(tmp_path)] == ["tmt.tsv", "lfq.tsv"]
    m, _notes = engines.load("Sage", tmp_path)
    assert m.exp == "TMT" and plex.normalise(m, _settings())[1] is None      # one plex: nothing to put on one scale
    prov = engines.provenance(tmp_path, "Sage", m.source, m.meta)
    assert prov["version"] == "0.14.7" and prov["fdr"] == "spectrum, peptide and protein q ≤ 0.01"
    assert prov["files"] == ["sage/results.json"] and prov["table"] == "sage/tmt.tsv"
