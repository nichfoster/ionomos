"""Instrument QC trending (qctrend.py, downstream/qcmetrics.py, downstream/qcpage.py; D45).

Metrics from small tables with the engines' real column names, which runs count as QC-standard runs, the
store (a re-run updates its row), every Westgard rule and the CUSUM drift flag on planted series, the page,
the CLI, the attention item (raised and closed), and that nothing here can fail a job.
"""
import json
import os
import re
import time
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from ionomos import attention, configio, qctrend, testbed
from ionomos.cli import main
from ionomos.config import ConfigError, load
from ionomos.downstream import qcmetrics, qcpage

S = qctrend.settings_from(None)
STATS_HEADER = testbed.DIANN_STATS_HEADER


def _write(path: Path, header: list[str], rows: list[list]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join("\t".join(str(c) for c in r) for r in [header, *rows]) + "\n", encoding="utf-8")
    return path


def _stats_row(name, prec=42000, prot=6200, total=2.1e10, fwhm=0.15, ms1=1.6, ms2=3.1, charge=2.45, missed=0.11):
    return [name, prec, prot, total, 8e9, 1.3e10, 6.1, fwhm, ms1, 0.2, ms2, 0.4, 0.02, 0.01, 0.03, 10.8, charge,
            missed]


# ------------------------------------------------------------------ metrics --


def test_diann_stats_with_real_columns_and_windows_paths(tmp_path):
    wd = tmp_path / "fragpipe"
    _write(wd / "diann-output" / "report.stats.tsv", STATS_HEADER, [
        _stats_row(r"C:\Fragpipe_General\EJQ\qc\raw\HeLa_200ng_1.raw"),
        _stats_row(r"C:\Fragpipe_General\EJQ\qc\raw\HeLa_200ng_2.raw", prec=30000),
        _stats_row(r"C:\elsewhere\not_in_this_job.raw")])
    found, notes = qcmetrics.run_metrics(wd, {"HeLa_200ng_1": ("HeLa_200ng", 1), "HeLa_200ng_2": ("HeLa_200ng", 2)})
    assert notes == [] and set(found) == {"HeLa_200ng_1", "HeLa_200ng_2"}
    m = found["HeLa_200ng_1"]["metrics"]
    assert m == {"precursors": 42000, "proteins": 6200, "signal": 2.1e10, "fwhm": 0.15, "ms1_ppm": 1.6,
                 "ms2_ppm": 3.1, "charge": 2.45, "missed": 0.11}
    assert found["HeLa_200ng_2"]["metrics"]["precursors"] == 30000
    assert "Median.Mass.Acc.MS1" in found["HeLa_200ng_1"]["sources"]["ms1_ppm"]  # uncorrected: drift shows


def test_diann_calibrated_and_stamped_run_names_match(tmp_path):
    wd = tmp_path / "diann"
    _write(wd / "report.stats.tsv", STATS_HEADER, [_stats_row("D:/x/HeLa_QC_20260930143015.mzML")])
    found, _ = qcmetrics.run_metrics(wd, {"HeLa_QC": ("HeLa_QC", 1)})
    assert found["HeLa_QC"]["metrics"]["precursors"] == 42000


def test_pg_matrix_gives_proteins_when_stats_has_none(tmp_path):
    wd = tmp_path / "fragpipe"
    _write(wd / "diann-output" / "report.stats.tsv", ["File.Name", "Precursors.Identified"],
           [["C:\\r\\HeLa_1.raw", 40000]])
    _write(wd / "diann-output" / "report.pg_matrix.tsv", ["Protein.Group", "Genes", "C:\\r\\HeLa_1.raw"],
           [["P1", "A", 1e6], ["P2", "B", ""], ["P3", "C", 5e5], ["P4", "D", 0]])
    found, _ = qcmetrics.run_metrics(wd, {"HeLa_1": ("HeLa", 1)})
    assert found["HeLa_1"]["metrics"] == {"precursors": 40000, "proteins": 2}
    assert "pg_matrix" in found["HeLa_1"]["sources"]["proteins"]


def test_diann_report_rt_of_the_most_intense_precursors(tmp_path):
    wd = tmp_path / "fragpipe"
    rows = [["C:\\r\\HeLa_1.raw", f"PEPTIDE{k}K", 10 + k * 0.5, 0.001, 1000 * k] for k in range(1, 260)]
    rows.append(["C:\\r\\HeLa_1.raw", "BADQK", 50, 0.2, 1e12])  # fails 1 % FDR: never used
    _write(wd / "diann-output" / "report.tsv",
           ["Run", "Stripped.Sequence", "RT", "Q.Value", "Precursor.Quantity"], rows)
    found, _ = qcmetrics.run_metrics(wd, {"HeLa_1": ("HeLa", 1)})
    rt = found["HeLa_1"]["rt"]
    assert len(rt) == qcmetrics.RT_PEPTIDES and "BADQK" not in rt and "PEPTIDE1K" not in rt
    assert rt["PEPTIDE259K"] == pytest.approx(10 + 259 * 0.5)


def test_ppm_error_isotope_correction_and_offsets():
    calc = 1500.0
    assert qcmetrics.ppm_error(calc * (1 + 3e-6), calc) == pytest.approx(3.0, abs=1e-6)
    assert qcmetrics.ppm_error(calc + qcmetrics.ISOTOPE + calc * 2e-6, calc) == pytest.approx(2.0, abs=1e-6)
    assert qcmetrics.ppm_error(calc + 0.5, calc) is None  # a mass offset (333 ppm), not an error
    assert qcmetrics.ppm_error(None, calc, delta=calc * -4e-6) == pytest.approx(-4.0, abs=1e-6)
    assert qcmetrics.spectrum_run("HeLa_QC_1.04512.04512.2") == "HeLa_QC_1"


def test_fragpipe_psm_tsv(tmp_path):
    wd = tmp_path / "fragpipe"
    testbed.fake_psm(wd / "HeLa_1" / "psm.tsv", [r"C:\x\raw\HeLa_1.raw"], n_psms=240)
    found, notes = qcmetrics.run_metrics(wd, {"HeLa_1": ("HeLa", 1)})
    assert notes == []
    m = found["HeLa_1"]["metrics"]
    assert m["psms"] == 240 and m["peptides"] == 160 and m["proteins"] == 90
    assert 1.0 < m["ms1_ppm"] < 3.0  # planted ~2 ppm
    assert 0.02 < m["missed"] < 0.25 and 2.2 < m["charge"] < 2.6
    assert set(found["HeLa_1"]["charges"]) == {"2", "3"}
    assert all(10 <= rt <= 90 for rt in found["HeLa_1"]["rt"].values())  # seconds -> minutes


SAGE_HEAD = ["peptide", "proteins", "filename", "scannr", "rank", "label", "expmass", "calcmass", "charge",
             "missed_cleavages", "precursor_ppm", "rt", "spectrum_q", "peptide_q", "protein_q", "ms2_intensity"]


def _sage_row(pep, prot, file, ppm, rt, inten, charge=2, missed=0, iso=0, rank=1, label=1, sq=0.001, pq=0.001,
              gq=0.001, calc=1000.0):
    return [pep, prot, file, "scan=1", rank, label, calc * (1 + ppm * 1e-6) + iso * qcmetrics.ISOTOPE, calc, charge,
            missed, abs(ppm), rt, sq, pq, gq, inten]


def test_sage_results_tsv(tmp_path):
    """Sage's PSM table (columns from its source): targets of rank 1 at 1 % per file, a signed mass error from the
    masses, RT in minutes as Sage writes it."""
    f = "HeLa_1.mzML"
    rows = [
        _sage_row("AAAK", "sp|P1|ONE_HUMAN", f, 2.0, 20.5, 100),
        _sage_row("AAAK", "sp|P1|ONE_HUMAN", f, 4.0, 20.7, 300, charge=3, iso=1),       # on the first isotope peak
        _sage_row("CCCK", "sp|P2|TWO_HUMAN", f, -1.0, 30.0, 50, missed=1, calc=1500.0),
        _sage_row("DDDK", "sp|P3|THREE_HUMAN", f, 2.0, 40.0, 10, pq=0.5, gq=0.5),       # a PSM, not a peptide / protein
        _sage_row("DECOYK", "rev_sp|P4|FOUR_HUMAN", f, 30.0, 1.0, 1e9, label=-1),
        _sage_row("RANKK", "sp|P5|FIVE_HUMAN", f, 30.0, 1.0, 1e9, rank=2),
        _sage_row("HIGHQK", "sp|P5|FIVE_HUMAN", f, 30.0, 1.0, 1e9, sq=0.2),
        _sage_row("AAAK", "sp|P1|ONE_HUMAN", "HeLa_2.mzML.gz", -3.0, 21.0, 70),
        _sage_row("AAAK", "sp|P1|ONE_HUMAN", "Other.mzML", 9.0, 5.0, 1),                # not a run that was asked for
    ]
    wd = tmp_path / "sage"
    _write(wd / "results.sage.tsv", SAGE_HEAD, rows)
    found, notes = qcmetrics.run_metrics(wd, {"HeLa_1": ("HeLa", 1), "HeLa_2": ("HeLa", 2)})
    assert notes == [] and set(found) == {"HeLa_1", "HeLa_2"}
    m = found["HeLa_1"]["metrics"]
    assert (m["psms"], m["peptides"], m["proteins"], m["signal"]) == (4, 2, 2, 460)
    assert m["ms1_ppm"] == pytest.approx(2.0, abs=0.01) and m["missed"] == 0.25 and m["charge"] == 2.25
    assert found["HeLa_1"]["charges"] == {"2": 75.0, "3": 25.0}
    assert found["HeLa_1"]["rt"] == {"AAAK": 20.7, "CCCK": 30.0, "DDDK": 40.0}          # of its most intense spectrum
    assert "expmass vs calcmass" in found["HeLa_1"]["sources"]["ms1_ppm"] and "ms2_ppm" not in m
    assert found["HeLa_2"]["metrics"]["ms1_ppm"] == pytest.approx(-3.0, abs=0.01)       # signed
    # without the masses, Sage's own precursor_ppm column
    keep = [j for j, h in enumerate(SAGE_HEAD) if h not in ("expmass", "calcmass")]
    _write(wd / "results.sage.tsv", [SAGE_HEAD[j] for j in keep], [[r[j] for j in keep] for r in rows])
    found, _ = qcmetrics.run_metrics(wd, {"HeLa_1": ("HeLa", 1)})
    assert found["HeLa_1"]["metrics"]["ms1_ppm"] == 2.0 and "precursor_ppm" in found["HeLa_1"]["sources"]["ms1_ppm"]
    _write(wd / "results.sage.tsv", ["peptide", "x"], [["A", 1]])
    assert any("not Sage's results.sage.tsv" in n for n in qcmetrics.run_metrics(wd, {"HeLa_1": ("HeLa", 1)})[1])


def test_combined_protein_for_a_single_run_experiment(tmp_path):
    wd = tmp_path / "fragpipe"
    testbed.fake_psm(wd / "HeLa_1" / "psm.tsv", [r"C:\x\HeLa_1.raw"])
    _write(wd / "combined_protein.tsv", ["Protein", "HeLa_1 Spectral Count", "HeLa_1 Intensity"],
           [["P1", 3, 1e6], ["P2", 0, 0], ["P3", 1, 2e5]])
    found, _ = qcmetrics.run_metrics(wd, {"HeLa_1": ("HeLa", 1)})
    assert found["HeLa_1"]["metrics"]["proteins"] == 2
    assert "combined_protein" in found["HeLa_1"]["sources"]["proteins"]


def test_broken_or_huge_tables_leave_notes_not_errors(tmp_path):
    wd = tmp_path / "fragpipe"
    _write(wd / "HeLa_1" / "psm.tsv", ["Nonsense", "Columns"], [["a", "b"]])
    _write(wd / "diann-output" / "report.stats.tsv", STATS_HEADER, [_stats_row("C:\\x\\HeLa_1.raw")])
    found, notes = qcmetrics.run_metrics(wd, {"HeLa_1": ("HeLa", 1)})
    assert found["HeLa_1"]["metrics"]["precursors"] == 42000  # the good table still counts
    assert any("psm.tsv" in n and "Spectrum" in n for n in notes)
    found, notes = qcmetrics.run_metrics(wd, {"HeLa_1": ("HeLa", 1)}, max_mb=1e-9)
    assert found == {} and any("max_file_mb" in n for n in notes)
    assert qcmetrics.run_metrics(tmp_path / "missing", {"x": ("x", 1)})[0] == {}


# ----------------------------------------------------------------- matching --


def _record(folder: str, files: list[tuple[str, str, int]], method="DIA", status="done") -> dict:
    return {"status": status, "queued_at": "2026-09-30T09:00:00",
            "plan": {"folder": {"original": folder, "method": method, "user": "EJQ"},
                     "manifest": [{"file": f, "experiment": e, "bioreplicate": r, "data_type": "DIA"}
                                  for f, e, r in files]},
            "method_config": {"data_type": "DDA" if method != "DIA" else "DIA"}}


@pytest.mark.parametrize("folder, files, n, why", [
    ("20260930_EJQ_DIA_HeLa_200ng", [("HeLa_200ng_1.raw", "HeLa_200ng", 1)], 1, "hela"),
    ("20260930_EJQ_DIA_instrument-check", [("K562-50ng_1.raw", "K562-50ng", 1)], 1, "file names"),
    ("20260930_EJQ_DIA_QC", [("run_1.raw", "run", 1), ("run_2.raw", "run", 2)], 2, "_qc_"),  # QC as a word
    ("20260930_EJQ_DIA_QCtest", [("run_1.raw", "run", 1)], 0, "no name"),                      # not as a word
    ("20260930_EJQ_DIA_qc-std", [("a_1.raw", "a", 1)], 1, "qc_std"),                            # any separator
    # HeLa is also a cell line: a replicated design is an experiment, not a QC standard
    ("20260930_EJQ_DIA_HeLa_KO-vs-WT", [("KO_1.raw", "KO", 1), ("KO_2.raw", "KO", 2), ("WT_1.raw", "WT", 1),
                                         ("WT_2.raw", "WT", 2)], 0, "experiment"),
    ("20260930_EJQ_DIA_Drug", [("HeLa_DMSO_1.raw", "HeLa_DMSO", 1), ("HeLa_DMSO_2.raw", "HeLa_DMSO", 2),
                               ("HeLa_Drug_1.raw", "HeLa_Drug", 1), ("HeLa_Drug_2.raw", "HeLa_Drug", 2)], 0,
     "experiment"),
])
def test_which_runs_are_qc_standards(folder, files, n, why):
    runs, reason = qctrend.qc_runs(_record(folder, files), "DIA", S)
    assert len(runs) == n
    assert why in reason


def test_exclude_and_dedicated_method():
    s = qctrend.settings_from({"exclude": ["hela-ko"], "methods": ["QC"]})
    rec = _record("20260930_EJQ_DIA_HeLa-KO_single", [("HeLa-KO_1.raw", "HeLa-KO", 1)])
    assert qctrend.qc_runs(rec, "DIA", s)[0] == []
    rec = _record("20260930_EJQ_QC_morning", [("std_1.raw", "std", 1), ("std_2.raw", "std", 2),
                                              ("blank_1.raw", "blank", 1), ("blank_2.raw", "blank", 2)], method="QC")
    runs, why = qctrend.qc_runs(rec, "QC", s)
    assert len(runs) == 4 and why == "method QC"  # a dedicated method counts even with a "design"


def test_series_name_carries_standard_and_amount(tmp_path):
    s = {**S, "instrument": "Eclipse"}
    rec = _record("20260930_EJQ_DIA_QC", [("HeLa_200ng_1.raw", "HeLa_200ng", 1), ("K562_50ng_1.raw", "K562_50ng", 1)])
    rows, _ = qctrend.build_rows(tmp_path, rec, "DIA", s)
    assert sorted(r["series"] for r in rows) == ["Eclipse · DIA · HeLa · 200ng", "Eclipse · DIA · K562 · 50ng"]
    assert all(r["notes"] for r in rows)  # no search output: said so on the row, not raised


def test_acquisition_time_from_stamp_else_file_time(tmp_path):
    raw = tmp_path / "HeLa_1.raw"
    raw.write_bytes(b"x")
    os.utime(raw, (1790000000, 1790000000))
    assert qctrend.acquired(raw, "HeLa_1_20260930143015") == ("2026-09-30T14:30:15", "name")
    when, src = qctrend.acquired(raw, "HeLa_1")
    assert src == "file time" and when.startswith(time.strftime("%Y-%m-%d", time.localtime(1790000000)))
    assert qctrend.acquired(tmp_path / "gone.raw", "gone", "2026-09-01T08:00:00+00:00") == \
        ("2026-09-01T08:00:00", "filed")


# ------------------------------------------------------------------- store --


def _row(i: int, series="DIA · HeLa", day=None, **metrics) -> dict:
    return {"key": f"/lab/qc{i}|HeLa_{i}", "series": series, "run": f"HeLa_{i}", "acquisition": "DIA",
            "acquired": day or f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}T09:00:00", "metrics": metrics,
            "experiment": f"qc{i}", "dest": f"/lab/qc{i}", "job_id": i, "rt": {}}


def test_store_updates_a_rerun_instead_of_duplicating(tmp_path):
    qctrend.append(tmp_path, [_row(1, proteins=100), _row(2, proteins=200)])
    qctrend.append(tmp_path, [_row(1, proteins=150)])  # the job re-ran
    rows = {r["run"]: r for r in qctrend.load(tmp_path)}
    assert len(rows) == 2 and rows["HeLa_1"]["metrics"]["proteins"] == 150
    with open(qctrend.store_path(tmp_path), "a", encoding="utf-8") as fh:
        fh.write("{not json\n\n")
    assert len(qctrend.load(tmp_path)) == 2  # a damaged line is skipped
    for k in range(30):
        qctrend.append(tmp_path, [_row(1, proteins=k)])
    lines = qctrend.store_path(tmp_path).read_text(encoding="utf-8").splitlines()
    assert len(lines) < 30  # compacted
    assert {r["run"]: r["metrics"]["proteins"] for r in qctrend.load(tmp_path)} == {"HeLa_1": 29, "HeLa_2": 200}


def test_store_name_comes_from_names_py(tmp_path):
    from ionomos import names

    assert qctrend.store_path(tmp_path).name == names.QC_TREND_STORE == "qc_trend.jsonl"
    assert qctrend.page_path(tmp_path).name == names.QC_TREND_PAGE == "qc_trend.html"


# ------------------------------------------------------------ Westgard rules --

BASE = [1000 + d for d in (-15, 15, -10, 10, -5, 5, -12, 12, 0, 0)]  # mean 1000, SD 9.9 -> floored to 2 % = 20
SD = 20.0


def _series(values: list[float | None], key="proteins", s=S) -> dict:
    rows = [_row(i, **({key: v} if v is not None else {})) for i, v in enumerate(values)]
    (ser,) = qctrend.analyse(rows, s)
    return ser


def _rules(run: dict, key="proteins") -> list[str]:
    return [f["rule"] for f in run["flags"].get(key, [])]


def test_baseline_is_being_collected():
    ser = _series(BASE[:6])
    assert ser["status"] == "baseline" and ser["verdict"] == "Building the baseline: 6 of 10 QC runs"
    ser = _series(BASE + [1001])
    mt = ser["metrics"][0]
    assert mt["mean"] == pytest.approx(1000) and mt["sd"] == pytest.approx(SD)  # SD floor: 2 % of the mean
    assert ser["status"] == "ok" and ser["verdict"] == "All metrics within the baseline"


def test_rule_1_3s():
    ser = _series(BASE + [1000 - 3.5 * SD])
    last = ser["runs"][-1]
    assert "1-3s" in _rules(last) and ser["status"] == "warning"
    assert re.match(r"Proteins 7% below baseline \(1-3s\) — check the column", ser["verdict"])


def test_rule_1_2s_is_only_a_warning_and_2_2s_rejects():
    ser = _series(BASE + [1000 - 2.5 * SD])
    assert _rules(ser["runs"][-1]) == ["1-2s"] and ser["status"] == "watch"
    ser = _series(BASE + [1000 - 2.5 * SD, 1000 - 2.4 * SD])
    assert _rules(ser["runs"][-1]) == ["2-2s"] and ser["status"] == "warning"
    ser = _series(BASE + [1000 - 2.5 * SD, 1000 + 2.4 * SD])  # opposite sides: not 2-2s ...
    assert "2-2s" not in _rules(ser["runs"][-1]) and "R-4s" in _rules(ser["runs"][-1])  # ... but R-4s


def test_rule_r_4s_flags_imprecision_in_either_direction():
    ser = _series(BASE + [1000 - 2.5 * SD, 1000 + 2.5 * SD])
    last = ser["runs"][-1]
    assert _rules(last) == ["R-4s"] and ser["status"] == "warning"  # even though the last one is "better"


def test_rule_10_x():
    ser = _series(BASE + [1000 - 0.6 * SD] * 10)
    assert [bool(_rules(r)) for r in ser["runs"][10:]] == [False] * 9 + [True]
    assert _rules(ser["runs"][-1]) == ["10-x"] and ser["status"] == "warning"


def test_cusum_flags_slow_drift_before_any_other_rule():
    ser = _series(BASE + [1000 - 1.5 * SD] * 7)
    flagged = [_rules(r) for r in ser["runs"][10:]]
    assert flagged[:5] == [[]] * 5 and flagged[5] == ["CUSUM"]  # C- = 6 x (1.5 - 0.5) > 5 at the 6th run
    assert ser["status"] == "warning" and "(CUSUM)" in ser["verdict"]


def test_better_than_baseline_is_watch_not_warning():
    ser = _series(BASE + [1000 + 4 * SD])
    assert _rules(ser["runs"][-1]) == ["1-3s"] and ser["status"] == "watch"
    assert "better than baseline" in ser["verdict"]


def test_broader_peaks_and_mass_error_shifts_are_bad():
    ser = _series([0.150 + d / 1000 for d in (-2, 2, -1, 1, 0, 0, -2, 2, 1, -1)] + [0.20], key="fwhm")
    assert ser["status"] == "warning" and "Peak width 33% above baseline" in ser["verdict"]
    assert _series([0.150] * 10 + [0.10], key="fwhm")["status"] == "watch"  # narrower peaks: good
    ms1 = [1.5 + d / 10 for d in (-1, 1, -1, 1, 0, 0, -1, 1, 0, 0)]
    for shift in (+3.0, -3.0):
        ser = _series(ms1 + [1.5 + shift], key="ms1_ppm")
        assert ser["status"] == "warning" and f"{shift:+.2f} ppm from baseline" in ser["verdict"]
        assert "calibrate the instrument" in ser["verdict"]


def test_signal_is_trended_on_log_scale():
    base = [2e10 * (1 + d / 100) for d in (-3, 3, -2, 2, -1, 1, 0, 0, -3, 3)]
    ser = _series(base + [1.0e10], key="signal")
    assert ser["status"] == "warning" and "Signal 50% below baseline" in ser["verdict"]


def test_pinned_baseline_dates():
    s = qctrend.settings_from({"baseline_from": "2026-01-05", "baseline_to": "2026-01-12"})
    ser = _series([500] * 4 + BASE[:8] + [1000] * 3, s=s)  # the first 4 runs (a bad old column) are ignored
    assert ser["baseline"] == list(range(4, 12)) and "pinned" in ser["baseline_text"]
    assert ser["metrics"][0]["mean"] == pytest.approx(statistics_mean(BASE[:8]))
    assert ser["status"] == "ok"
    s = qctrend.settings_from({"baseline_from": "2030-01-01", "baseline_to": "2030-02-01"})
    assert "using the first 10" in _series(BASE + [1000], s=s)["baseline_text"]


def statistics_mean(xs):
    return sum(xs) / len(xs)


def test_rt_shift_against_the_baseline_peptides():
    peps = {f"PEP{k}K": 10.0 + k for k in range(20)}
    rows = []
    for i in range(12):
        shift = 1.5 if i == 11 else (0.02 if i % 2 else -0.02)
        r = _row(i, proteins=1000 + (i % 3))
        r["rt"] = {p: rt + shift for p, rt in peps.items()}
        rows.append(r)
    (ser,) = qctrend.analyse(rows, S)
    assert ser["runs"][-1]["metrics"]["rt_shift"] == pytest.approx(1.5, abs=0.03)
    assert ser["status"] == "warning" and "RT shift +1.5" in ser["verdict"] and "check the LC" in ser["verdict"]
    assert rows[-1]["metrics"] == {"proteins": 1002}  # analyse() never changes the stored rows


def test_a_run_without_numbers_is_no_data_not_ok():
    rows = [_row(i, proteins=v) for i, v in enumerate(BASE)] + [_row(10)]
    rows[-1]["notes"] = ["no QC numbers found for this run in the search output"]
    (ser,) = qctrend.analyse(rows, S)
    assert ser["status"] == "nodata" and "No QC numbers" in ser["verdict"]


# -------------------------------------------------------------------- page --


def test_page_is_self_contained_and_escapes_names(tmp_path):
    rows = [_row(i, proteins=v, precursors=v * 7) for i, v in enumerate(BASE + [900])]
    rows[-1]["run"] = "<script>alert(1)</script>"
    rows[-1]["experiment"] = "Q&A \"x\""
    rep = tmp_path / "results" / "report.html"
    rep.parent.mkdir()
    rep.write_text("x")
    rows[-1]["report"] = str(rep)
    html = qcpage.render(qctrend.analyse(rows, S), S, store=tmp_path / "qc_trend.jsonl")
    assert qcpage.MARKER in html and "<script" not in html and "&lt;script&gt;" in html
    assert "Q&amp;A &quot;x&quot;" in html and rep.as_uri() in html
    assert not re.search(r"""(src|href)=['"]https?:""", html)  # nothing fetched from the network
    assert html.count("<svg") == 2 and "Levey-Jennings chart of Proteins" in html
    assert "class='p bad'" in html and "<title>" in html  # the broken run is red, every point has a tooltip
    for rule in ("1-3s", "2-2s", "R-4s", "10-x", "CUSUM"):
        assert rule in html


def test_empty_page_explains_what_counts(tmp_path):
    p = qctrend.write_page(tmp_path, S, rows=[])
    html = p.read_text(encoding="utf-8")
    assert "No QC-standard runs yet" in html and "<code>hela</code>" in html and "--rebuild" in html


# --------------------------------------------------- config, CLI, app, jobs --


def test_config_validation(lab):
    assert lab["cfg"].qc_trend == S
    for bad, msg in (({"match": "hela"}, None), ({"match": [""]}, "qc_trend.match"),
                     ({"baseline_runs": 2}, "at least 3"), ({"baseline_from": "2026-09-01"}, "give both"),
                     ({"baseline_from": "2026-09-10", "baseline_to": "2026-09-01"}, "after"),
                     ({"popup": "yes"}, "true or false"), ({"colour": 1}, "qc_trend.colour: unknown"),
                     ([1], "must be a mapping")):
        d = {**lab["cfg_dict"], "qc_trend": bad}
        lab["cfg_path"].write_text(yaml.safe_dump(d))
        if msg is None:
            assert load(lab["cfg_path"]).qc_trend["match"] == ["hela"]
            continue
        with pytest.raises(ConfigError, match=re.escape(msg)):
            load(lab["cfg_path"])


def test_config_example_documents_the_defaults():
    example = Path(__file__).resolve().parent.parent / "config.example.yaml"
    assert load(example, check_paths=False).qc_trend == S


def test_app_config_writer_keeps_qc_trend(tmp_path):
    d = configio.defaults(root=str(tmp_path / "auto"), users_root=str(tmp_path / "users"))
    d["qc_trend"].update({"match": ["hela", "pierce"], "instrument": "Eclipse", "baseline_from": "2026-09-01",
                          "baseline_to": "2026-09-20", "popup": True})
    back = yaml.safe_load(configio.dump_config(d))["qc_trend"]
    assert qctrend.settings_from(back) == {**S, "match": ["hela", "pierce"], "instrument": "Eclipse",
                                           "baseline_from": "2026-09-01", "baseline_to": "2026-09-20", "popup": True}


def _qc_folder(users: Path, day: int, bad: bool = False, dda: bool = False) -> Path:
    """A done QC experiment as the watcher leaves it: raw, ionomos.json, the search's tables."""
    name = f"HeLa_200ng_{day}" + ("_QCBAD" if bad else "")
    dest = users / "EJQ" / f"202609{day:02d}_EJQ_{'isoDTB' if dda else 'DIA'}_HeLa_QC_{day}"
    (dest / "raw").mkdir(parents=True)
    raw = dest / "raw" / f"{name}.raw"
    raw.write_bytes(b"x")
    t = time.mktime((2026, 9, day, 9, 0, 0, 0, 0, -1))
    os.utime(raw, (t, t))
    if dda:
        testbed.fake_psm(dest / "fragpipe" / f"{name}_1" / "psm.tsv", [str(raw)])
    else:
        testbed.fake_diann_stats(dest / "fragpipe" / "diann-output" / "report.stats.tsv", [str(raw)])
    rec = _record(dest.name, [(f"raw/{name}.raw", name, 1)], method="isoDTB" if dda else "DIA")
    rec["job_id"] = day
    (dest / "ionomos.json").write_text(json.dumps(rec), encoding="utf-8")
    return dest


class _Job:
    def __init__(self, dest: Path, method="DIA", job_id=1):
        self.dest_dir, self.method, self.id, self.parsed = str(dest), method, job_id, {}


def test_after_job_raises_then_closes_the_attention_item(lab):
    cfg = lab["cfg"]
    for day in range(1, 11):
        assert qctrend.after_job(_Job(_qc_folder(lab["general"], day), job_id=day), cfg)
    assert not [i for i in attention.items(cfg.log_dir) if i.kind == "qc_trend"]
    lines = qctrend.after_job(_Job(_qc_folder(lab["general"], 11, bad=True), job_id=11), cfg)
    assert lines and lines[0].startswith("HeLa_200ng_11_QCBAD: warning — Precursors 4")
    (it,) = [i for i in attention.items(cfg.log_dir) if i.kind == "qc_trend"]
    assert it.severity == "warning" and it.title == "Instrument QC: DIA · HeLa · 200ng is off its baseline"
    assert it.data["popup"] is False and it.data["page"].endswith("qc_trend.html") and it.job_id == 11
    assert any("column" in c for c in it.causes)
    page = (cfg.log_dir / "qc_trend.html").read_text(encoding="utf-8")
    assert "HeLa_200ng_11_QCBAD" in page and "warning" in page
    # the column is swapped: the next QC run is back within the baseline and the item closes by itself
    lines = qctrend.after_job(_Job(_qc_folder(lab["general"], 12), job_id=12), cfg)
    assert lines[0].startswith("HeLa_200ng_12: ok")
    assert not [i for i in attention.items(cfg.log_dir) if i.kind == "qc_trend"]
    # a re-run of an earlier job updates its row
    qctrend.after_job(_Job(Path(lab["general"] / "EJQ" / "20260903_EJQ_DIA_HeLa_QC_3"), job_id=3), cfg)
    assert len(qctrend.load(cfg.log_dir)) == 12


def test_popup_setting_reaches_the_item(lab):
    cfg = replace(lab["cfg"], qc_trend={**S, "popup": True, "baseline_runs": 3})
    for day in range(1, 4):
        qctrend.after_job(_Job(_qc_folder(lab["general"], day), job_id=day), cfg)
    qctrend.after_job(_Job(_qc_folder(lab["general"], 4, bad=True), job_id=4), cfg)
    (it,) = [i for i in attention.items(cfg.log_dir) if i.kind == "qc_trend"]
    assert it.data["popup"] is True


def test_nothing_happens_for_ordinary_experiments_or_when_off(lab):
    cfg = lab["cfg"]
    dest = lab["general"] / "EJQ" / "20260930_EJQ_DIA_Drug"
    dest.mkdir(parents=True)
    rec = _record(dest.name, [("DMSO_1.raw", "DMSO", 1), ("Drug_1.raw", "Drug", 1)])
    (dest / "ionomos.json").write_text(json.dumps(rec), encoding="utf-8")
    assert qctrend.after_job(_Job(dest), cfg) == []
    assert not qctrend.store_path(cfg.log_dir).exists() and not qctrend.page_path(cfg.log_dir).exists()
    off = replace(cfg, qc_trend={**S, "enabled": False})
    assert qctrend.after_job(_Job(_qc_folder(lab["general"], 1)), off) == []
    assert not qctrend.store_path(cfg.log_dir).exists()


def test_a_broken_qc_read_never_fails_the_job(lab, monkeypatch, caplog):
    from ionomos import postprocess

    dest = _qc_folder(lab["general"], 1)

    def boom(*a, **k):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(qcmetrics, "run_metrics", boom)
    assert qctrend.after_job(_Job(dest), lab["cfg"]) == []
    assert "instrument QC trending failed" in caplog.text
    # and through the postprocess hook, even when the analysis itself crashes
    monkeypatch.setattr(postprocess, "_analyse", lambda *a: ([], {"report": "results/report.html"}))
    monkeypatch.setattr(qctrend, "after_job", boom)
    assert postprocess.run_all(_Job(dest), None, lab["cfg"]) == ([], {"report": "results/report.html"})
    monkeypatch.setattr(postprocess, "_analyse", boom)
    with pytest.raises(RuntimeError):  # the worker's own catch turns this into "analysis crashed"
        postprocess.run_all(_Job(dest), None, lab["cfg"])


def test_dda_qc_runs_from_psm_tsv(lab):
    cfg = lab["cfg"]
    lines = qctrend.after_job(_Job(_qc_folder(lab["general"], 1, dda=True), method="isoDTB"), cfg)
    assert lines and "Building the baseline: 1 of 10" in lines[0]
    (row,) = qctrend.load(cfg.log_dir)
    assert row["acquisition"] == "DDA" and row["metrics"]["psms"] == 240 and row["rt"]
    assert row["acquired_from"] == "file time" and row["acquired"].startswith("2026-09-01T09:00")


def test_cli_rebuild_scans_users_root_read_only(lab, capsys):
    folders = [_qc_folder(lab["general"], d, bad=(d == 12)) for d in range(1, 13)]
    before = {p: p.stat().st_mtime_ns for f in folders for p in f.rglob("*") if p.is_file()}
    assert main(["--config", str(lab["cfg_path"]), "qc-trend"]) == 0  # empty store -> scans by itself
    out = capsys.readouterr().out
    assert "12 QC run(s) found" in out and "DIA · HeLa · 200ng: 12 run(s), warning" in out
    assert {p: p.stat().st_mtime_ns for f in folders for p in f.rglob("*") if p.is_file()} == before
    assert main(["--config", str(lab["cfg_path"]), "qc-trend", "--rebuild"]) == 0
    assert len(qctrend.load(lab["cfg"].log_dir)) == 12  # rebuilt, not doubled
    assert main(["--config", str(lab["cfg_path"]), "qc-trend"]) == 0  # from the store
    assert "looking for" not in capsys.readouterr().out.split("page:")[-1]
    assert (lab["cfg"].log_dir / "qc_trend.html").is_file()


def test_cli_open(lab, monkeypatch, capsys):
    opened = []
    monkeypatch.setattr("ionomos.service.open_path", lambda p: opened.append(Path(p)))
    assert main(["--config", str(lab["cfg_path"]), "qc-trend", "--open"]) == 0
    assert opened == [lab["cfg"].log_dir / "qc_trend.html"]
    assert "no QC-standard runs yet" in capsys.readouterr().out


def test_app_button_helper_rebuilds_a_stale_page(lab):
    page = qctrend.page_for(lab["cfg_path"])
    assert page == lab["cfg"].log_dir / "qc_trend.html" and "No QC-standard runs yet" in page.read_text("utf-8")
    old = page.stat().st_mtime
    os.utime(page, (old - 100, old - 100))
    qctrend.append(lab["cfg"].log_dir, [_row(1, proteins=10)])
    assert "HeLa_1" in qctrend.page_for(lab["cfg_path"]).read_text("utf-8")
    first = page.stat().st_mtime_ns
    assert qctrend.page_for(lab["cfg_path"]) == page and page.stat().st_mtime_ns == first  # fresh: left alone


# ------------------------------------------------------------ end to end --


def test_worker_job_on_a_qc_standard_records_it(tmp_path, monkeypatch):
    """The real pipeline: a HeLa QC drop -> intake -> fake FragPipe (DIA, writes report.stats.tsv) -> analysis ->
    the QC store, the page and the verdict in ionomos.json."""
    from ionomos.intake import intake
    from ionomos.ledger import Ledger
    from ionomos.worker import Worker

    monkeypatch.setenv("IONOMOS_FAKE_FP_SECONDS", "0")
    monkeypatch.setenv("IONOMOS_NO_GUI", "1")
    cfg = load(testbed.init(tmp_path / "bed"))
    cfg = replace(cfg, gui_enabled=False, review_drops=False)
    ledger = Ledger(cfg.database)
    folder = cfg.inbox / "20260930_Isaac_DIA_HeLa-200ng-QC"
    folder.mkdir()
    for k in (1, 2):
        (folder / f"HeLa_200ng_{k}.raw").write_bytes(b"\0" * 64)
    assert intake(folder, cfg, ledger).value == "queued"
    assert Worker(cfg, ledger).run_once()
    job = ledger.get(1)
    assert job.status == "done", job.reason
    rows = qctrend.load(cfg.log_dir)
    assert sorted(r["run"] for r in rows) == ["HeLa_200ng_1", "HeLa_200ng_2"]
    assert all(r["metrics"]["precursors"] > 30000 and r["series"] == "DIA · HeLa · 200ng" for r in rows)
    st = json.loads((Path(job.dest_dir) / "ionomos.json").read_text(encoding="utf-8"))
    assert st["results"]["qc_trend"][0].startswith("HeLa_200ng_1: baseline")
    assert (cfg.log_dir / "qc_trend.html").is_file()
