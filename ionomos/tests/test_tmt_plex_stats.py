"""Statistics across TMT plexes (D71).

- the composition check and the ratio normalisation (fpa.normalize_info, D64) taken within each plex and combined,
  so a pulldown is seen also when the plex effect is still in the data (no IRS)
- IRS on the plex means spends one residual df per plex (plex.df_spent, fpa.spend_df): checked against limma 3.68.5
  in R on the same steps (tests/golden/tmt_sum/), and not applied when the design holds the plexes itself
The calibration guards on simulated plexes are in test_benchmark.py.
"""
from __future__ import annotations

import csv
import json
import math
import random
from pathlib import Path

import pytest

from ionomos import downstream
from ionomos.downstream import analysis, benchmark, design, fpa, plex
from ionomos.downstream.quant import Feature, QuantMatrix

GOLD = Path(__file__).parent / "golden" / "tmt_sum"
CFG = {"enrichment": False}


def _plexed(n: int = 600, plexes: int = 3, dmso: int = 4, drug: int = 4, up: float = 0.2, effect: float = 1.0,
            plex_sd: float = 1.0, seed: int = 3, with_plex: bool = True) -> tuple[QuantMatrix, set[str]]:
    """TMT channels of several plexes, a plex effect per protein, a loading per channel; `up` of the proteins
    enriched `effect` log2 in Drug (a pulldown). Returns (matrix, the enriched proteins)."""
    rng = random.Random(seed)
    samples = [(f"{c}_{k + 1} Exp{p}", c, f"Exp{p}") for p in range(1, plexes + 1)
               for k, c in enumerate(["DMSO"] * dmso + ["Drug"] * drug)]
    load = [rng.gauss(0, 0.3) for _ in samples]
    rows, feats, hit = [], [], set()
    for i in range(n):
        base, sd = rng.gauss(22, 2), 0.25 * math.exp(rng.gauss(0, 0.4))
        eff = effect if rng.random() < up else 0.0
        if eff:
            hit.add(f"P{i}")
        shift = {p: rng.gauss(0, plex_sd) for _s, _c, p in samples}
        rows.append([base + (eff if c == "Drug" else 0.0) + shift[p] + ld + rng.gauss(0, sd)
                     for (_s, c, p), ld in zip(samples, load, strict=True)])
        feats.append(Feature(f"P{i}", f"G{i}"))
    m = QuantMatrix("intensity", "protein", feats, [s for s, _c, _p in samples], rows,
                    {s: c for s, c, _p in samples}, "sim", exp="TMT")
    if with_plex:
        m.meta["plex"] = {s: p for s, _c, p in samples}
    return m, hit


def _unchanged_offset(m: QuantMatrix, hit: set[str]) -> float:
    """Mean Drug - DMSO of the unchanged proteins, within plexes (the plex effect cancels there)."""
    plexes = m.meta.get("plex") or {s: "" for s in m.samples}
    out = []
    for f, row in zip(m.features, m.values, strict=True):
        if f.id in hit:
            continue
        for p in set(plexes.values()):
            a = [v for s, v in zip(m.samples, row, strict=True) if plexes[s] == p and m.condition[s] == "Drug"]
            b = [v for s, v in zip(m.samples, row, strict=True) if plexes[s] == p and m.condition[s] == "DMSO"]
            out.append(sum(a) / len(a) - sum(b) / len(b))
    return sum(out) / len(out)


# ------------------------------------------------------------ the composition check --


def test_plex_groups_need_every_sample_in_a_plex_and_two_per_plex():
    m, _ = _plexed(n=30, plexes=2, dmso=2, drug=2)
    assert fpa.plex_groups(m) == [[0, 1, 2, 3], [4, 5, 6, 7]]
    m.meta["plex"] = {**m.meta["plex"], m.samples[0]: ""}
    assert fpa.plex_groups(m) is None                       # a sample without a plex: compared all together
    one, _ = _plexed(n=30, plexes=1)
    assert fpa.plex_groups(one) is None
    m2, _ = _plexed(n=30, plexes=2, dmso=1, drug=1)
    assert fpa.plex_groups(m2) == [[0, 1], [2, 3]]
    m2.meta["plex"][m2.samples[3]] = "Exp9"
    assert fpa.plex_groups(m2) is None                       # a plex of one channel has nothing to compare
    for joined in ({"bridge": {"applied": True}}, {"ratio_to_reference": "TMT-Integrator"}):  # on one scale already
        m3, _ = _plexed(n=30, plexes=2, dmso=2, drug=2)
        m3.meta.update(joined)
        assert fpa.plex_groups(m3) is None


def test_a_pulldown_is_seen_within_plexes_without_irs():
    """The plex effect (SD 1 log2) left in, 20 % of the proteins 2-fold up in Drug. Across plexes the ratio method
    and its check see a protein jump with the plex and miss the composition; within each plex they see it."""
    m, hit = _plexed()
    plain = QuantMatrix(**{**m.__dict__, "meta": {}})        # the same data, plexes unknown (before D71)
    before, info0 = fpa.normalize_info(plain, "auto")
    assert not info0["composition"]["exceeded"] and info0["used"] == "median"
    assert _unchanged_offset(before.__class__(**{**before.__dict__, "meta": m.meta}), hit) < -0.15
    after, info = fpa.normalize_info(m, "auto")
    comp = info["composition"]
    assert comp["exceeded"] and comp["plexes"] == 3 and comp["between"] == ["Drug", "DMSO"]
    assert comp["shift"] > 0.15 and info["used"] == "ratio"
    assert abs(_unchanged_offset(after, hit)) < 0.03
    _p, notes = fpa.process(m, normalization="auto")
    assert any("compared within each of the 3 TMT plexes" in n for n in notes)


def test_within_plexes_the_check_stays_quiet_when_nothing_moves_one_way():
    for seed in range(5):
        m, _ = _plexed(up=0.0, seed=seed)
        for i, r in enumerate(m.values[:60]):  # 10 % changed, both ways
            for j, s in enumerate(m.samples):
                if m.condition[s] == "Drug":
                    r[j] += 2.0 if i % 2 else -2.0
        _m, info = fpa.normalize_info(m, "auto")
        assert not info["composition"]["exceeded"] and info["used"] == "median", seed
        assert info["composition"]["plexes"] == 3


def test_ratio_normalisation_within_plexes_keeps_the_plexes_level():
    """The shifts within a plex come from that plex's own channels; the plexes' levels from the ratio method over
    all samples, so a plex loaded heavier as a whole is still put on the others' level."""
    m, hit = _plexed(up=0.0, plex_sd=0.0, seed=8)
    for r in m.values:
        for j, s in enumerate(m.samples):
            if s.endswith("Exp2"):
                r[j] += 1.5
    out, info = fpa.normalize_info(m, "ratio")
    assert info["used"] == "ratio" and info["composition"]["plexes"] == 3
    level = {p: sum(v for r in out.values for v, s in zip(r, out.samples, strict=True) if s.endswith(p))
             for p in ("Exp1", "Exp2", "Exp3")}
    n = len(out.values) * 8
    assert max(level.values()) / n - min(level.values()) / n < 0.02


def test_one_plex_or_none_known_gives_the_check_as_before():
    """Without plexes the check is the D64 one, to the last digit (its numbers are in earlier analyses)."""
    m, _ = _plexed(plexes=1, seed=4)
    _out, info = fpa.normalize_info(m, "auto")
    comp = info["composition"]
    cols = [[row[j] for row in m.values] for j in range(len(m.samples))]
    meds = [fpa.stats.median(c) for c in cols]
    rs, _n = fpa.ratio_shifts(cols)
    d = [a - sum(meds) / len(meds) - b for a, b in zip(meds, rs, strict=True)]
    by = {c: [x for x, s in zip(d, m.samples, strict=True) if m.condition[s] == c] for c in ("DMSO", "Drug")}
    mean = {c: sum(v) / len(v) for c, v in by.items()}
    hi, lo = max(mean, key=mean.get), min(mean, key=mean.get)
    assert comp["shift"] == mean[hi] - mean[lo] and comp["between"] == [hi, lo] and "plexes" not in comp


# ------------------------------------------------- IRS on the plex means: the df it spends --


def _gold(name: str) -> list[dict]:
    with open(GOLD / name, encoding="utf-8") as fh:
        return [{k.strip(): v.strip() for k, v in r.items()} for r in csv.DictReader(fh, delimiter="\t")]


def _num(v: str) -> float:
    return math.nan if v == "NA" else float(v)


def _tmt_sum_matrix() -> QuantMatrix:
    info = _gold("tmt_sum_samples.tsv")
    with open(GOLD / "tmt_sum_matrix.tsv", encoding="utf-8") as fh:
        rows = list(csv.reader(fh, delimiter="\t"))
    samples = rows[0][1:]
    m = QuantMatrix("intensity", "protein", [Feature(r[0], r[0]) for r in rows[1:]], samples,
                    [[None if v == "NA" else float(v) for v in r[1:]] for r in rows[1:]],
                    {r["sample"]: r["condition"] for r in info}, "tmt_sum_matrix.tsv", exp="TMT")
    m.meta["plex"] = {r["sample"]: r["plex"] for r in info}
    return m


def test_irs_on_plex_means_spends_a_df_per_plex_as_limma_in_r():
    """Three plexes of 3 + 3 channels without a reference: IRS on the plex means, the filter, median
    normalisation and limma with each protein's residual df reduced by (the plexes it was scaled in - 1), against
    limma 3.68.5 on the same steps (run_tmt_sum_reference.R) to 1e-8; without the reduction R gives other
    p-values (tmt_sum_limma_plain.tsv)."""
    st = analysis.settings_from({**CFG, "irs": "sum", "normalize": "median"})
    p, diffs = benchmark.run_pipeline(_tmt_sum_matrix(), st)
    assert plex.sum_scaled(p.m)
    want = {r["ID"]: [_num(v) for k, v in r.items() if k != "ID"] for r in _gold("tmt_sum_processed.tsv")}
    assert [f.id for f in p.m.features] == list(want)
    assert max(abs(a - b) for f, row in zip(p.m.features, p.m.values, strict=True)
               for a, b in zip(row, want[f.id], strict=True) if a is not None) < 1e-9
    spent = plex.df_spent(p)
    assert spent == [int(r["spent"]) for r in _gold("tmt_sum_spent.tsv")] and set(spent) == {0, 1, 2}
    model = analysis.make_model(p.m, st, [("Drug", "DMSO")])
    assert model.plex_df and "reduced by the plexes - 1" in model.as_dict(st)["plex_df"]
    (d,) = diffs
    prior = {r["case"]: (float(r["df.prior"]), float(r["s2.prior"])) for r in _gold("tmt_sum_priors.tsv")}
    assert d.prior == pytest.approx(prior["corrected"], rel=1e-9)
    gold = {r["ID"]: r for r in _gold("tmt_sum_limma_corrected.tsv")}
    plain = {r["ID"]: r for r in _gold("tmt_sum_limma_plain.tsv")}
    moved = 0
    for r in d.rows:
        g = gold[r["id"]]
        for ours, key in ((r["log2fc"], "diff"), (r["ci_low"], "CI.L"), (r["ci_high"], "CI.R"), (r["t"], "t"),
                          (r["pvalue"], "p.val"), (r["qvalue"], "p.adj")):
            ours, theirs = (math.nan if ours is None else ours), _num(g[key])
            assert (math.isnan(ours) and math.isnan(theirs)) or ours == pytest.approx(theirs, rel=1e-8, abs=1e-12), \
                (r["id"], key, ours, theirs)
        if r["pvalue"] is not None and abs(r["pvalue"] / _num(plain[r["id"]]["p.val"]) - 1) > 0.01:
            moved += 1
    assert moved > 100  # the reduction changes most p-values; plain limma had them smaller


def test_no_df_is_spent_when_the_design_holds_the_plexes_or_without_irs_on_means():
    st = analysis.settings_from({**CFG, "irs": "sum", "normalize": "median"})
    p, _ = benchmark.run_pipeline(_tmt_sum_matrix(), st)
    by_plex = design.build(p.m, {s: p.m.meta["plex"][s] for s in p.m.samples})
    assert plex.holds_plexes(by_plex, p.m) and plex.df_spent(p, by_plex) is None
    pairs = {s: f"{s.split('_')[2]}{(int(s.split('_')[1]) - 1) % 3}" for s in p.m.samples}
    finer = design.build(p.m, pairs)   # a DMSO and a Drug channel per block, within a plex: finer than the plex
    assert plex.holds_plexes(finer, p.m)
    across = design.build(p.m, {s: str((int(s.split("_")[1]) - 1) % 3) for s in p.m.samples})  # across plexes
    assert not plex.holds_plexes(across, p.m) and plex.df_spent(p, across) == plex.df_spent(p)
    blocked = analysis.settings_from({**CFG, "irs": "sum", "normalize": "median", "block_from": r"_(P\d)$"})
    model = analysis.make_model(p.m, blocked, [("Drug", "DMSO")])
    assert model.design is not None and not model.plex_df and "plex_df" not in model.as_dict(blocked)
    welch = analysis.settings_from({**CFG, "irs": "sum", "normalize": "median", "test": "welch"})
    assert not analysis.make_model(p.m, welch, [("Drug", "DMSO")]).plex_df
    ref = _tmt_sum_matrix()
    p2, _ = benchmark.run_pipeline(ref, analysis.settings_from({**CFG, "irs": "none", "normalize": "median"}))
    assert not plex.sum_scaled(p2.m) and plex.df_spent(p2) is None


def test_spend_df_keeps_the_residual_sum_of_squares():
    s2, df = fpa.spend_df([0.5, 0.2, math.nan, 0.3], [6, 2, 0, 4], [2, 2, 1, 0])
    assert s2[0] == pytest.approx(0.75) and df[0] == 4
    assert math.isnan(s2[1]) and df[1] == 0                  # nothing left
    assert math.isnan(s2[2]) and df[2] == 0 and (s2[3], df[3]) == (0.3, 4)
    assert fpa.spend_df([0.1], [3], None) == ([0.1], [3])


# ------------------------------------------------------------ what a person is told --


def _mq_pulldown(tmp_path: Path) -> tuple[Path, dict[str, str], list[str]]:
    """MaxQuant's proteinGroups.txt of three plexes (4 DMSO + 4 Drug + pool), 20 % of the proteins up 2-fold."""
    from ionomos.downstream import simulate

    mq = tmp_path / "mq"
    conds, _truth = simulate.tmt_plexes(mq / "combined" / "txt" / "proteinGroups.txt", plexes=3, seed=12,
                                        n_proteins=400, changed_fraction=0.2, effect=1.0, direction="up")
    return mq, conds, [s for s, c in conds.items() if c == "Pool"]


def _plex_statement(out) -> dict:
    return next(s for s in out.summary["trust"]["statements"] if s["key"] == "plexes")


def test_without_irs_the_report_and_the_doctor_say_how_the_plexes_were_handled(tmp_path):
    mq, conds, pools = _mq_pulldown(tmp_path)
    base = {**CFG, "sample_conditions": conds, "exclude_samples": pools, "irs": "none"}
    out = downstream.analyze(mq, analysis_cfg=base)
    codes = [i.code for i in out.issues]
    assert "TMT_PLEXES_NOT_IN_MODEL" in codes and "TMT_PLEXES_NOT_NORMALISED" not in codes
    st = _plex_statement(out)
    assert st["level"] == "check" and "neither put on one scale nor in the model" in st["text"]
    info = json.loads((out.results_dir / "analysis.json").read_text(encoding="utf-8"))
    comp = info["normalisation"]["composition"]
    assert comp["plexes"] == 3 and comp["exceeded"] and info["normalisation"]["used"] == "ratio"
    issue = next(i for i in out.issues if i.code == "NORMALISATION_COMPOSITION")
    assert issue.severity == "warning" and "within each of the 3 TMT plexes" in issue.message
    # with the plex as a block: no warning, and the statement says the comparisons are made within the plexes
    blocked = downstream.analyze(mq, analysis_cfg={**base, "block_from": r" (Exp\d+)$"})
    assert "TMT_PLEXES_NOT_IN_MODEL" not in [i.code for i in blocked.issues]
    st = _plex_statement(blocked)
    assert st["level"] == "ok" and "plex as a block" in st["text"]
    # median centring asked for: the doctor asks, also without IRS (before D71 the check could not see it)
    median = downstream.analyze(mq, analysis_cfg={**base, "block_from": r" (Exp\d+)$", "normalize": "median"})
    assert next(i for i in median.issues if i.code == "NORMALISATION_COMPOSITION").severity == "input"


def test_irs_on_plex_means_is_stated_with_its_df(tmp_path):
    mq, conds, pools = _mq_pulldown(tmp_path)
    base = {**CFG, "sample_conditions": conds, "exclude_samples": pools, "irs": "sum"}
    out = downstream.analyze(mq, analysis_cfg=base)
    info = json.loads((out.results_dir / "analysis.json").read_text(encoding="utf-8"))
    assert info["tmt"]["method"] == plex.SUM_METHOD and "plex_df" in info["model"]
    st = _plex_statement(out)
    assert st["level"] == "ok" and st["numbers"]["df_reduced"] and "reduced by the plexes - 1" in st["text"]
    assert not [i for i in out.issues if i.code.startswith("TMT_PLEXES")]
    st = _plex_statement(downstream.analyze(mq, analysis_cfg={**base, "test": "welch"}))
    assert st["level"] == "check" and "slightly too small" in st["text"]
    ref = downstream.analyze(mq, analysis_cfg={**CFG, "sample_conditions": conds, "tmt_reference": ["126"]})
    st = _plex_statement(ref)
    assert st["level"] == "ok" and "reference channel(s)" in st["text"]
