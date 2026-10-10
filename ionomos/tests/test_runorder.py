"""Run order (D78): the acquisition time of each raw file (acqtime.py: the Thermo header, ThermoRawFileParser's
output, the name, the file time), its record at intake and in the QC trend, and the run-order QC (runorder.py):
the trend and confounding statistics checked on simulated data (a drift added in run order; conditions in
blocks against randomised), the whole analysis, the doctor, the report's payload and the export figure.

The Thermo header here is built from the documented layout (unfinnigan / OpenTFRaw: magic 0xA101, "Finnigan" in
UTF-16LE, version at 0x24, audit start and end FILETIMEs at 0x28 and 0x98). No real .raw file was read."""
from __future__ import annotations

import hashlib
import itertools
import json
import os
import random
import re
import struct
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ionomos import acqtime, downstream, qctrend
from ionomos import help as helpdoc
from ionomos.downstream import charts, runorder, simulate
from ionomos.downstream.quant import QuantMatrix

FILETIME_EPOCH = 11644473600


def _ft(dt: datetime) -> int:
    return int(round((dt.timestamp() + FILETIME_EPOCH) * 1e7))


def thermo_header(start: datetime, end: datetime | None = None, version: int = 66, magic: int = 0xA101,
                  signature: str = "Finnigan", size: int = 1356) -> bytes:
    """The first bytes of a Thermo .raw file, laid out as the format documentation says."""
    b = bytearray(size)
    struct.pack_into("<H", b, 0, magic)
    sig = (signature + "\x00").encode("utf-16-le")[:18]
    b[2:2 + len(sig)] = sig
    struct.pack_into("<I", b, 0x20, 0x80000)
    struct.pack_into("<I", b, 0x24, version)
    struct.pack_into("<Q", b, 0x28, _ft(start))
    tag = "Xcalibur_System".encode("utf-16-le")
    b[0x30:0x30 + len(tag)] = tag
    b[0x30 + 50:0x30 + 50 + len("admin") * 2] = "admin".encode("utf-16-le")
    if end is not None:
        struct.pack_into("<Q", b, 0x98, _ft(end))
    return bytes(b)


def write_raw(path: Path, start: datetime, minutes: float = 60.0, body: bytes = b"\x00" * 4096) -> Path:
    """A raw file with a real-shaped header, written as the instrument would: modified when the run ended."""
    end = start + timedelta(minutes=minutes)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(thermo_header(start, end) + body)
    os.utime(path, (end.timestamp(), end.timestamp()))
    return path


T0 = datetime(2026, 9, 30, 21, 30, 15, tzinfo=UTC)


# ------------------------------------------------------------- the header --


def test_header_is_read_from_the_documented_offsets():
    h = acqtime.parse_header(thermo_header(T0, T0 + timedelta(hours=1)))
    assert h == {"start": T0, "end": T0 + timedelta(hours=1), "version": 66}  # no tag text (account names) kept
    for v in (8, 47, 57, 60, 62, 63, 64, 66, 67):
        assert acqtime.parse_header(thermo_header(T0, version=v))["version"] == v
    assert acqtime.parse_header(thermo_header(T0))["end"] is None


@pytest.mark.parametrize("bad", [
    dict(magic=0x0000), dict(signature="Finnigam"), dict(version=999), dict(version=0), dict(size=100),
    dict(start=datetime(1601, 1, 1, 0, 0, 1, tzinfo=UTC)), dict(start=datetime.now(UTC) + timedelta(days=30)),
])
def test_anything_unlike_a_thermo_header_is_not_trusted(bad):
    start = bad.pop("start", T0)
    assert acqtime.parse_header(thermo_header(start, **bad)) is None


def test_random_bytes_and_other_files_are_not_headers(tmp_path):
    rng = random.Random(3)
    for k in range(200):
        assert acqtime.parse_header(bytes(rng.randrange(256) for _ in range(400))) is None, k
    (tmp_path / "x.raw").write_bytes(b"")
    assert acqtime.header(tmp_path / "x.raw") is None and acqtime.header(tmp_path / "missing.raw") is None


def test_read_takes_the_header_and_never_changes_the_file(tmp_path):
    raw = write_raw(tmp_path / "raw" / "DMSO_1.raw", T0)
    before = (hashlib.sha256(raw.read_bytes()).hexdigest(), raw.stat().st_mtime, raw.stat().st_size)
    info = acqtime.read(raw)
    assert (hashlib.sha256(raw.read_bytes()).hexdigest(), raw.stat().st_mtime, raw.stat().st_size) == before
    assert info["source"] == "raw header" and info["approximate"] is False and info["version"] == 66
    assert info["utc"] == "2026-09-30T21:30:15Z"
    assert info["time"] == datetime.fromtimestamp(T0.timestamp()).isoformat()  # local wall-clock time
    assert info["matches_file"] is True and info["end"] == info["file_time"]
    os.utime(raw, (T0.timestamp() + 7200 + 3600, T0.timestamp() + 7200 + 3600))  # copied later, the time kept? no
    assert acqtime.read(raw)["matches_file"] is False


def test_a_header_time_after_the_file_was_written_is_not_used(tmp_path):
    raw = write_raw(tmp_path / "A_1_20260930143015.raw", T0)
    os.utime(raw, ((T0 - timedelta(days=3)).timestamp(),) * 2)
    info = acqtime.read(raw)
    assert info["source"] == "file name" and info["time"] == "2026-09-30T14:30:15" and "not used" in info["note"]


def test_the_other_sources_in_order(tmp_path):
    raw = tmp_path / "A_1.raw"
    raw.write_bytes(b"not a thermo file")
    os.utime(raw, (1790000000, 1790000000))
    info = acqtime.read(raw)
    assert info["source"] == "file time" and info["approximate"] is True
    assert info["time"] == datetime.fromtimestamp(1790000000).isoformat()
    assert acqtime.read(tmp_path / "B_1_20260930143015.raw")["source"] == "file name"
    gone = acqtime.read(tmp_path / "C_1.raw")
    assert gone["time"] is None and gone["source"] is None

    mz = tmp_path / "sage_mzml"  # ThermoRawFileParser's mzML (Sage's conversion) beats the name and the file time
    mz.mkdir()
    (mz / "A_1.mzML").write_text('<?xml version="1.0"?><indexedmzML><mzML><run id="A_1" '
                                 'startTimeStamp="2026-09-30T21:30:15Z" defaultInstrumentConfigurationRef="IC1">',
                                 encoding="utf-8")
    info = acqtime.read(raw, [mz])
    assert info["source"] == "ThermoRawFileParser" and info["utc"] == "2026-09-30T21:30:15Z"
    assert info["from_file"] == "A_1.mzML" and info["approximate"] is False


def test_thermorawfileparser_metadata_files(tmp_path):
    local = datetime(2026, 9, 30, 14, 30, 15).astimezone()
    (tmp_path / "A_1-metadata.json").write_text(json.dumps({"FileProperties": [
        {"accession": "NCIT:C47922", "cvLabel": "NCIT", "name": "Pathname", "value": "C:\\x\\A_1.raw"},
        {"accession": "NCIT:C69199", "cvLabel": "NCIT", "name": "Content Creation Date", "value": "9/30/2026 2:30:15 PM"}]}),
        encoding="utf-8")
    (tmp_path / "B_1-metadata.txt").write_text("#FileProperties\nRAW file path=C:\\x\\B_1.raw\nRAW file version=66\n"
                                               "Creation date=09/30/2026 14:30:15\n", encoding="utf-8")
    for stem in ("A_1", "B_1"):
        when, name = acqtime.from_trfp(stem, [tmp_path])
        assert when == local.astimezone(UTC) and name.startswith(stem)
    assert acqtime.from_trfp("C_1", [tmp_path]) is None
    assert acqtime.parse_time("30.09.2026 14:30:15")[0] == datetime(2026, 9, 30, 14, 30, 15)
    assert acqtime.parse_time("garbage") == (None, False)


def test_intake_records_the_times_and_leaves_the_files_alone(lab):
    from ionomos.intake import IntakeResult, intake
    from ionomos.ledger import Ledger
    from tests.conftest import make_drop

    d = make_drop(lab["inbox"], "Isaac DIA FLAG pulldown (test)", ["DMSO_1.raw", "DMSO_2.raw", "Drug_1.raw", "Drug_2.raw"],
                  raw_sub=True)
    starts = {}
    for k, p in enumerate(sorted((d / "raw").iterdir())):
        starts[p.name] = T0 + timedelta(hours=k)
        write_raw(p, starts[p.name])
    sums = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (d / "raw").iterdir()}
    assert intake(d, lab["cfg"], Ledger(lab["cfg"].database)) == IntakeResult.QUEUED
    dest = lab["general"] / "Isaac" / "Isaac-DIA-FLAG-pulldown-test"
    rec = json.loads((dest / "ionomos.json").read_text(encoding="utf-8"))
    acq = rec["acquisition"]
    assert set(acq) == {x["file"] for x in rec["plan"]["manifest"]}
    for f, info in acq.items():
        assert info["source"] == "raw header"
        assert info["utc"] == starts[Path(f).name].strftime("%Y-%m-%dT%H:%M:%SZ")
    assert {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (dest / "raw").iterdir()} == sums


def test_intake_files_the_folder_even_when_times_cannot_be_read(lab, monkeypatch):
    from ionomos.intake import IntakeResult, intake
    from ionomos.ledger import Ledger
    from tests.conftest import make_drop

    def boom(*_a, **_k):
        raise RuntimeError("acq boom")

    monkeypatch.setattr(acqtime, "for_manifest", boom)
    d = make_drop(lab["inbox"], "Isaac DIA FLAG pulldown (test)", ["DMSO_1.raw", "Drug_1.raw"], raw_sub=True)
    assert intake(d, lab["cfg"], Ledger(lab["cfg"].database)) == IntakeResult.QUEUED
    rec = json.loads((lab["general"] / "Isaac" / "Isaac-DIA-FLAG-pulldown-test" / "ionomos.json").read_text())
    assert rec["acquisition"] == {} and rec["status"] == "queued"


def test_qc_trend_uses_the_recorded_time_then_the_header(tmp_path):
    raw = write_raw(tmp_path / "HeLa_1.raw", T0)
    assert qctrend.acquired(raw, "HeLa_1") == (datetime.fromtimestamp(T0.timestamp()).isoformat(), "raw header")
    known = {"time": "2026-01-02T03:04:05", "source": "ThermoRawFileParser"}
    assert qctrend.acquired(raw, "HeLa_1", "", known) == ("2026-01-02T03:04:05", "ThermoRawFileParser")
    assert qctrend.acquired(raw, "HeLa_1", "", {"time": None}) [1] == "raw header"
    stamped = tmp_path / "HeLa_2_20260930143015.raw"
    stamped.write_bytes(b"x")
    assert qctrend.acquired(stamped, stamped.stem) == ("2026-09-30T14:30:15", "name")  # the store's old word


# ------------------------------------------------------------ statistics --


def _brute_p(sizes, s_obs) -> float:
    tot = hit = 0
    for combo in itertools.product(*[list(itertools.permutations(range(k))) for k in sizes]):
        s = sum(sum(1 if p[j] > p[i] else -1 for i in range(len(p)) for j in range(i + 1, len(p))) for p in combo)
        tot += 1
        hit += abs(s) >= abs(s_obs)
    return hit / tot


def test_mahonian_and_the_exact_p_value_match_enumeration():
    assert [round(x * 24) for x in runorder.mahonian(4)] == [1, 3, 5, 6, 5, 3, 1]
    assert sum(runorder.mahonian(12)) == pytest.approx(1.0)
    o = list(range(1, 10))
    for y in ([1, 2, 3, 4, 5, 6, 7, 8, 9], [3, 1, 2, 4, 9, 6, 7, 5, 8], [9, 8, 7, 1, 2, 3, 6, 5, 4]):
        t = runorder.trend(o, [float(v) for v in y], list("ABCABCABC"))
        assert t["p_method"] == "exact" and t["p"] == pytest.approx(_brute_p([3, 3, 3], t["S"]))
    t = runorder.trend([1, 2, 3, 4, 5, 6, 7], [1, 3, 2, 5, 4, 7, 6.0], list("AAAABBB"))
    assert t["p"] == pytest.approx(_brute_p([4, 3], t["S"]))


def test_trend_size_is_the_within_condition_slope():
    o = list(range(1, 13))
    conds = ["A", "B", "C"] * 4
    y = [1000 + 100 * {"A": 0, "B": 1, "C": 2}[c] - 10 * k for k, c in zip(o, conds, strict=True)]
    t = runorder.trend(o, [float(v) for v in y], conds)
    assert t["slope"] == pytest.approx(-10) and t["change"] == pytest.approx(-110) and t["tau"] == -1
    assert t["rho"] < -0.95  # the residuals from each condition's median, ranked against the order
    assert runorder.trend([1, 2, 3], [1.0, 2.0, 3.0], ["A", "B", "C"]) is None  # no two of one condition


def test_large_experiments_use_the_normal_approximation():
    rng = random.Random(5)
    n = 120
    conds = [f"C{k % 2}" for k in range(n)]
    y = [rng.gauss(0, 1) - 0.02 * k for k in range(n)]
    t = runorder.trend(list(range(1, n + 1)), y, conds)
    assert t["p_method"] == "normal" and t["p"] < 1e-4 and t["change"] < 0


def _sim(seed: int, drift: float = 0.0, blocks: bool = False, nc: int = 3, nr: int = 4, effect: float = 0.0):
    rng = random.Random(seed)
    conds = [f"C{i}" for i in range(nc) for _ in range(nr)]
    if not blocks:
        rng.shuffle(conds)
    ids = [3000 + effect * int(c[1:]) - drift * k + rng.gauss(0, 40) for k, c in enumerate(conds)]
    return list(range(1, len(conds) + 1)), ids, conds


def _flags(order, ids, conds) -> tuple[bool, bool]:
    t = runorder.trend(order, ids, conds)
    cf = runorder.confounding(order, conds, permutations=200)
    drift = t["p"] < runorder.ALPHA and abs(t["change"]) >= 0.10 * 3000
    return drift, cf["eta2"] >= runorder.ETA_WARN


def test_simulated_drift_is_found_and_no_drift_is_not():
    """3 conditions x 4, randomised order, identifications with sd 40: a fall of 40 per run is found nearly
    always; without it nothing is flagged; a difference between conditions run in blocks is not a drift."""
    found = [_flags(*_sim(s, drift=40))[0] for s in range(100)]
    assert sum(found) >= 90
    assert sum(_flags(*_sim(s))[0] for s in range(200)) <= 4
    assert sum(_flags(*_sim(s, blocks=True, effect=400))[0] for s in range(100)) <= 2
    assert sum(_flags(*_sim(s, blocks=True, drift=60))[0] for s in range(100)) >= 70  # within the blocks too


def test_simulated_blocks_are_flagged_and_randomised_orders_mostly_not():
    assert all(_flags(*_sim(s, blocks=True))[1] for s in range(20))
    assert sum(_flags(*_sim(s))[1] for s in range(300)) <= 15  # an order blocked by chance is still blocked
    cf = runorder.confounding(list(range(1, 10)), list("AAABBBCCC"))
    assert cf["eta2"] == pytest.approx(0.9) and cf["p"] < 0.01 and cf["changes"] == 2
    assert cf["positions"] == {"A": "1–3", "B": "4–6", "C": "7–9"}
    cf = runorder.confounding(list(range(1, 10)), list("ABCABCABC"))
    assert cf["eta2"] == pytest.approx(0.1) and cf["changes"] == 8 and cf["positions"]["A"] == "1, 4, 7"
    assert runorder.confounding([1, 2, 3], ["A", "A", "A"]) is None


# -------------------------------------------------------- the samples --


def _pm(samples, conds, kind="intensity", columns=None):
    return QuantMatrix(kind, "protein", [], list(samples), [], dict(zip(samples, conds, strict=True)), "x",
                       columns=columns or {})


def _card(samples, ids):
    return [{"sample": s, "ids": v, "missing_pct": 100 * (1 - v / 4000), "median": 22.0} for s, v in zip(samples, ids, strict=True)]


def test_run_order_from_a_manifest_and_recorded_times(tmp_path):
    conds = ["DMSO", "Drug", "Mid"]
    samples = [f"{c}_{r}" for c in conds for r in (1, 2, 3, 4)]
    order = samples[:]
    random.Random(2).shuffle(order)
    record = {"plan": {"manifest": [{"file": f"raw/{s}.raw", "experiment": s.split("_")[0], "bioreplicate": int(s[-1])}
                                    for s in samples]},
              "acquisition": {f"raw/{s}.raw": {"time": (datetime(2026, 9, 30, 8) + timedelta(hours=k)).isoformat(),
                                               "source": "raw header", "approximate": False}
                              for k, s in enumerate(order)}}
    ids = {s: 3200 - 40 * k for k, s in enumerate(order)}
    pm = _pm(samples, [s.split("_")[0] for s in samples])
    res = runorder.run(pm, record, tmp_path, _card(samples, [ids[s] for s in samples]))
    assert [r["sample"] for r in res.samples] == order and [r["order"] for r in res.samples] == list(range(1, 13))
    codes = [c for c, _m in res.problems]
    assert codes == ["RUN_ORDER_DRIFT"]
    msg = res.problems[0][1]
    assert "identifications falls by" in msg and "missing values" not in msg  # the same fact, said once
    s = runorder.summary(res)
    assert s["ran"] and s["metrics"]["ids"]["flagged"] and s["metrics"]["missing"]["flagged"]
    assert s["samples"][0] == {"sample": order[0], "condition": order[0].split("_")[0], "order": 1,
                               "acquired": "2026-09-30T08:00:00", "source": "raw header", "approximate": False, "files": 1}
    p = runorder.report_payload(res)
    json.dumps(p, allow_nan=False)
    assert p["sources"] == {"raw header": 12} and p["approx"] is False and p["metrics"][0]["key"] == "ids"
    assert p["metrics"][0]["v"][0] == 3200 and p["metrics"][0]["flag"] is True


def test_blocks_without_drift_and_shared_files_and_no_times(tmp_path):
    samples = [f"{c}_{r}" for c in ("A", "B") for r in (1, 2, 3, 4)]
    record = {"plan": {"manifest": [{"file": f"{s}.raw", "experiment": s[0], "bioreplicate": int(s[-1])} for s in samples]},
              "acquisition": {f"{s}.raw": {"time": f"2026-09-30T{8 + k:02d}:00:00", "source": "file time",
                                           "approximate": True} for k, s in enumerate(samples)}}
    rng = random.Random(1)
    res = runorder.run(_pm(samples, [s[0] for s in samples]), record, tmp_path,
                       _card(samples, [3000 + rng.gauss(0, 30) for _ in samples]))
    assert [c for c, _m in res.problems] == ["RUN_ORDER_CONFOUNDED"]
    msg = res.problems[0][1]
    assert "A in runs 1–4; B in runs 5–8" in msg and "approximate" in msg
    assert any("approximate" in n for n in res.notes)

    tmt = {"plan": {"manifest": [{"file": "F1.raw", "experiment": "P", "bioreplicate": 1}]}}
    pm = _pm(["c126", "c127"], ["A", "B"])
    pm.meta["manifest_run"] = {"c126": "F1", "c127": "F1"}
    assert "share raw files" in runorder.run(pm, tmt, tmp_path).reason
    nothing = runorder.run(_pm(samples, [s[0] for s in samples]), None, tmp_path)
    assert nothing.reason == "no raw files were found for the samples" and runorder.report_payload(nothing) is None
    assert runorder.summary(nothing) == {"ran": False, "reason": "no raw files were found for the samples",
                                         "samples": nothing.samples}


# ------------------------------------------------------------ end to end --


def _psm(path: Path, run: str, ppm: float, n: int = 200) -> None:
    from ionomos import testbed

    rng = random.Random(run)
    lines = ["\t".join(testbed.PSM_HEADER)]
    for k in range(n):
        pep = "PEPTIDEK" + "A" * (k % 9)
        calc = 110.0 * len(pep) + 18.0106
        obs = calc * (1 + rng.gauss(ppm, 0.5) * 1e-6)
        row = {"Spectrum": f"{run}.{k + 1:05d}.{k + 1:05d}.2", "Peptide": pep, "Peptide Length": len(pep), "Charge": 2,
               "Retention": 600 + k, "Observed Mass": f"{obs:.6f}", "Calculated Peptide Mass": f"{calc:.6f}",
               "Delta Mass": f"{obs - calc:.6f}", "Number of Missed Cleavages": 0, "Intensity": 1e6,
               "Protein": f"sp|P{k % 40:05d}|X_HUMAN"}
        lines.append("\t".join(str(row.get(c, "")) for c in testbed.PSM_HEADER))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _experiment(tmp_path: Path, blocks: bool) -> tuple[Path, list[str]]:
    """A DIA experiment (3 conditions x 4), its raw files with headers in raw/, and a psm.tsv per run whose mass
    error drifts by 0.6 ppm a run in the order of acquisition."""
    d = tmp_path / ("blocks" if blocks else "random")
    runs = [(f"/x/{c}_{r}.raw", c) for c in ("DMSO", "Drug", "Mid") for r in (1, 2, 3, 4)]
    simulate.dia_pg_matrix(d / "fragpipe" / "report.pg_matrix.tsv", runs, seed=6, n_proteins=120)
    stems = [Path(r).stem for r, _c in runs]
    order = stems[:] if blocks else random.Random(4).sample(stems, len(stems))
    for k, stem in enumerate(order):
        write_raw(d / "raw" / f"{stem}.raw", T0 + timedelta(minutes=70 * k))
        _psm(d / "fragpipe" / stem / "psm.tsv", stem, ppm=-2.0 + 0.6 * k)
    return d, order


def _payload(out) -> dict:
    html = out.report.read_text(encoding="utf-8")
    return json.loads(re.search(r"<script id='ionomos-data' type='application/json'>(.*?)</script>", html, re.S).group(1))


def test_end_to_end_drift_report_and_doctor(tmp_path):
    d, order = _experiment(tmp_path, blocks=False)
    raws = {p: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime) for p in (d / "raw").iterdir()}
    out = downstream.analyze(d, "DIA", analysis_cfg={"enrichment": False})
    assert {p: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime) for p in (d / "raw").iterdir()} == raws
    s = out.summary["run_order"]
    assert s["ran"] and [x["sample"] for x in s["samples"]] == order
    assert {x["source"] for x in s["samples"]} == {"raw header"}
    assert s["metrics"]["ppm"]["flagged"] and s["metrics"]["ppm"]["change_over_run"] == pytest.approx(6.6, abs=0.4)
    assert "RUN_ORDER_DRIFT" in s["flagged"] and "RUN_ORDER_CONFOUNDED" not in s["flagged"]
    assert json.loads((d / "results" / "analysis.json").read_text(encoding="utf-8"))["run_order"] == s
    issues = {i.code: i for i in out.issues}
    assert issues["RUN_ORDER_DRIFT"].severity == "warning" and "median precursor mass error rises" in issues["RUN_ORDER_DRIFT"].message
    assert out.summary["state"] == "ok"
    data = _payload(out)
    run = data["qc"]["run"]
    assert [x["s"] for x in run["samples"]] == order and any(m["key"] == "ppm" and m["flag"] for m in run["metrics"])
    assert data["help"]["entries"]["qc.run"]["t"] == "Run order"
    assert "issue.RUN_ORDER_DRIFT" in {x["id"] for x in data["help"]["issues"]}

    svgs = {n: svg for n, _w, svg in charts.figures(data, charts.style_from(), ["run_order"])}
    svg = svgs["run_order.svg"]
    assert "drift" in svg and "run (order of acquisition)" in svg and "<desc>" in svg
    assert not re.search(r"var\(|<style|class=|foreignObject", svg)
    cols = set(re.findall(r'(?:fill|stroke)="([^"]+)"', svg)) - {"none"}
    assert all(re.fullmatch(r"#[0-9a-f]{6}", c) for c in cols)


def test_end_to_end_blocks_are_a_warning(tmp_path):
    d, _order = _experiment(tmp_path, blocks=True)
    out = downstream.analyze(d, "DIA", analysis_cfg={"enrichment": False})
    issues = {i.code: i for i in out.issues}
    assert "DMSO in runs 1–4; Drug in runs 5–8; Mid in runs 9–12" in issues["RUN_ORDER_CONFOUNDED"].message
    assert "drift was found in this experiment as well" in issues["RUN_ORDER_CONFOUNDED"].message
    assert _payload(out)["qc"]["run"]["confound"]["flag"] is True


def test_samples_left_out_are_left_out_of_the_run_order(tmp_path):
    """2026-10-06 (docs/REAL_RUNS.md): re-running the pull-down without the empty-vector runs failed in this stage
    with KeyError 'EV_2'. The quant table matched every run to the manifest (meta "manifest_run"), and the
    samples left out were still in that map (D84)."""
    d = tmp_path / "pulldown"
    runs = [(f"/x/{c}_{r}.raw", c) for c in ("DMSO", "FPS", "EV") for r in (1, 2, 3)]
    simulate.dia_pg_matrix(d / "fragpipe" / "report.pg_matrix.tsv", runs, seed=6, n_proteins=120)
    order = [Path(r).stem for r, _c in runs]
    random.Random(5).shuffle(order)
    for k, stem in enumerate(order):
        write_raw(d / "raw" / f"{stem}.raw", T0 + timedelta(minutes=70 * k))
    record = {"plan": {"manifest": [{"file": f"raw/{Path(r).name}", "experiment": c, "bioreplicate": int(r[-5])}
                                    for r, c in runs]}}
    left_out = ["EV_1", "EV_2", "EV_3"]
    out = downstream.analyze(d, "DIA", analysis_cfg={"enrichment": False, "exclude_samples": left_out},
                             record=record)
    assert "CRASH_RUN_ORDER" not in {i.code for i in out.issues}
    s = out.summary["run_order"]
    kept = [x for x in order if x not in left_out]
    assert s["ran"] and [x["sample"] for x in s["samples"]] == kept and [x["order"] for x in s["samples"]] == [
        1, 2, 3, 4, 5, 6]  # the order among the samples analysed, not the run's position among all nine
    assert all(x["files"] == 1 for x in s["samples"])

    pm = QuantMatrix("intensity", "protein", [], ["A_1", "A_2"], [], {"A_1": "A", "A_2": "A"},
                     meta={"manifest_run": {"A_1": "A_1", "A_2": "A_2", "B_1": "B_1"}})
    rec = {"plan": {"manifest": [{"file": f"raw/{s}.raw", "experiment": s[0], "bioreplicate": int(s[-1])}
                                 for s in ("A_1", "A_2", "B_1")]}}
    assert runorder.sample_files(pm, rec, tmp_path) == {"A_1": ["raw/A_1.raw"], "A_2": ["raw/A_2.raw"]}


def test_without_raw_files_there_is_no_tab_and_a_crash_costs_nothing(tmp_path, monkeypatch):
    d = tmp_path / "plain"
    simulate.dia_pg_matrix(d / "fragpipe" / "report.pg_matrix.tsv", [(f"/x/{c}_{r}.raw", c) for c in ("A", "B")
                                                                     for r in (1, 2, 3)], seed=6, n_proteins=90)
    out = downstream.analyze(d, "DIA", analysis_cfg={"enrichment": False})
    assert out.summary["run_order"]["ran"] is False and "run" not in _payload(out)["qc"]

    def boom(*_a, **_k):
        raise RuntimeError("run boom")

    monkeypatch.setattr(runorder, "run", boom)
    out = downstream.analyze(d, "DIA", analysis_cfg={"enrichment": False})
    assert out.summary["run_order"]["reason"].startswith("the run-order step failed")
    assert out.report and out.summary["comparisons"] and "CRASH_RUN_ORDER" in {i.code for i in out.issues}


def test_help_explains_both_warnings_and_the_tab():
    ents = helpdoc.entries()
    assert ents["qc.run"].title == "Run order"
    for code in ("RUN_ORDER_DRIFT", "RUN_ORDER_CONFOUNDED"):
        assert helpdoc.issue_entry(code) == f"issue.{code}"
    assert f"{runorder.ETA_WARN:.0%}" in ents["issue.RUN_ORDER_CONFOUNDED"].body
    assert "within each condition" in ents["issue.RUN_ORDER_DRIFT"].body
    assert f"p below {runorder.ALPHA:g}" in ents["qc.run"].body, "the help names the limit the code uses"


def test_qc_tab_lists_match_the_report_script():
    js = (Path(downstream.__file__).parent / "assets" / "report.js").read_text(encoding="utf-8")
    assert '["run", "Run order"]' in js and "run: qcRun" in js and '"run_order"' in js
