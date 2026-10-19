"""
Testbed — a fake lab you can build anywhere (Mac, Windows) to try the whole
tool without a real FragPipe or real raw files.

    ionomos testbed init [DIR]         build DIR (default ./ionomos-testbed): Fragpipe_Auto/,
                                        Fragpipe_General/, config.yaml, samples/, fake FragPipe
    ionomos testbed list [DIR]         the sample drops and what each one exercises
    ionomos testbed drop NAME [DIR]    copy samples/NAME into the inbox (--slow = file by file,
                                        like a real drag from a USB drive)
    ionomos testbed reset [DIR]        empty inbox, user folders, ledger, logs; keep samples
    ionomos testbed gui-demo           open the resolver window with sample data (no drop needed)

Then, in another terminal:   ionomos --config DIR/Fragpipe_Auto/config.yaml run

Sample .raw files are a few KB of random bytes — ionomos never reads raw
contents, only names and sizes.
"""
from __future__ import annotations

import math
import os
import random
import re
import shutil
import stat
import time
from pathlib import Path

import yaml


def default_root() -> Path:
    """Where the testbed goes when no DIR is given.

    Windows: C:/ionomos-testbed, never under the profile folder (usernames often
    contain spaces, and the config loader rejects spaces on Windows). Elsewhere:
    ./ionomos-testbed.
    """
    if os.name == "nt":
        return Path("C:/ionomos-testbed")
    return Path("ionomos-testbed")


DEFAULT_DIR = str(default_root())
USERS = ["EJQ", "Isaac", "Chris", "Aman", "Taylor_Elements"]


def _iso(prefix: str, reps=(1, 2, 3), fracs=(1, 2, 3)) -> list[str]:
    return [f"{prefix}_{r}_{f}.raw" for r in reps for f in fracs]


_TMT10 = ("126", "127N", "127C", "128N", "128C", "129N", "129C", "130N", "130C", "131N")
_TMT16 = (*_TMT10, "131C", "132N", "132C", "133N", "133C", "134N")

# name -> (folder name, files at top level, files in raw/, files in plex folders, other files, experiment.yaml
# dict|None, the fake FragPipe's fault mode for it, what it shows)
SAMPLES: dict[str, dict] = {
    "fp_fail": dict(
        folder="20260910_Chris_DIA_crash-test_FAKEFAIL",
        raws=["DMSO_1.raw", "DMSO_2.raw", "Drug_1.raw", "Drug_2.raw"],
        shows="filed fine, then the fake FragPipe fails on purpose -> job failed, FAILED.txt; Retry re-runs it",
    ),
    "iso_good": dict(
        folder="20260902-isoDTB_EJQ-2-027",
        raws=_iso("EJQ_PK_EJQ-2-027_isoDTB_1uM_3h"), others=["EJQ-2-027_notes.xlsx"],
        shows="clean isoDTB drop: 3 reps x 3 fractions, user EJQ, date from name -> queued",
    ),
    "iso_spaces": dict(
        folder="20260902 isoDTB EJQ-2-027 (1uM 3h)",
        raws=_iso("EJQ PK isoDTB 1uM 3h", reps=(1, 2), fracs=(1, 2)),
        shows="spaces/parentheses in folder AND file names -> sanitised, queued",
    ),
    "dia_good": dict(
        folder="20260914_Isaac_DIA_FLAG-AR-pulldown",
        raw_sub=["DMSO_1.raw", "DMSO_2.raw", "DMSO_3.raw", "Drug_1.raw", "Drug_2.raw", "Drug_3.raw"],
        shows="DIA with raws in a raw/ subfolder: two conditions x 3 bioreps -> queued",
    ),
    "tmt_good": dict(
        folder="20260126_Aman_TMT_KL6159A-9plex",
        raws=[f"KL6159A_TMT_F{i}.raw" for i in range(1, 5)],
        # every channel of the label type is listed (FragPipe refuses a shorter list); NA = not used
        yaml={"tmt": {"tag": "TMT-10", "channels": {"126": "DMSO_1_126", "127N": "DMSO_1_127N", "127C": "DMSO_1_127C",
                                                       "128N": "Drug_1_128N", "128C": "Drug_1_128C",
                                                       "129N": "Drug_1_129N", "129C": "NA", "130N": "NA",
                                                       "130C": "NA", "131N": "NA"}}},
        shows="TMT, 4 fractions, biorep 1, channel map in experiment.yaml -> queued",
    ),
    "glued_initials": dict(
        folder="IJD05_isoDTB_FLAGpull",
        raws=_iso("IJD05_FLAG", reps=(1, 2), fracs=(1, 2)),
        shows="initials glued to an ID (IJD05) resolve via alias IJD -> Isaac -> queued",
    ),
    "method_in_files": dict(
        folder="EJQ_2027_pulldown",
        raws=_iso("EJQ_isoDTB_2027", reps=(1,), fracs=(1, 2)),
        shows="no method in folder name, but the raw file names say isoDTB -> queued",
    ),
    "gui_unknown_user": dict(
        folder="XYZ99_isoDTB_run",
        raws=_iso("XYZ99", reps=(1, 2), fracs=(1, 2)),
        shows="unknown initials -> resolver window (pick user, tick 'remember XYZ')",
    ),
    "gui_no_method": dict(
        folder="EJQ_2027_mystery",
        raws=["DMSO_1.raw", "DMSO_2.raw", "Drug_1.raw", "Drug_2.raw"],
        shows="no method anywhere -> resolver window (pick DIA; files re-parse as cond_biorep)",
    ),
    "gui_bad_tail": dict(
        folder="EJQ_isoDTB_odd-names",
        raws=["Sample_1_1.raw", "Sample_1_2.raw", "Sample_extra.raw"],
        shows="one file has no rep/fraction tail -> resolver window (type its rep/frac)",
    ),
    "gui_incomplete": dict(
        folder="EJQ_isoDTB_partial-copy",
        raws=_iso("EJQ_partial")[:-1],
        shows="rep 3 is missing fraction 3 -> resolver window ('accept uneven' or skip)",
    ),
    "reject_no_raws": dict(
        folder="EJQ_isoDTB_empty",
        raws=[], others=["notes.txt"],
        shows="no .raw files -> watcher waits forever (min_raw_files); nothing happens",
    ),
    "reject_two_methods": dict(
        folder="EJQ_isoDTB_then_TMT",
        raws=["S_1_1.raw"],
        shows="two method keywords -> resolver window (pick one)",
    ),
    # D69: TMT plexes as a drop can hold them, and FragPipe faults the fake acts out per experiment
    "tmt_plexes": dict(
        folder="20260127_Aman_TMT_KL6160-2plex",
        plex_subs={"plexA": ["KL6160A_TMT_F1.raw", "KL6160A_TMT_F2.raw"],
                   "plexB": ["KL6160B_TMT_F1.raw", "KL6160B_TMT_F2.raw"]},
        yaml={"tmt": {"tag": "TMT-10", "plexes": {
            p: {"channels": {ch: (f"{c}_{p}_{ch}" if c else "NA") for ch, (c, r) in zip(_TMT10, (
                ("DMSO", 1), ("DMSO", 2), ("Drug", 1), ("Drug", 2), ("", 0), ("", 0), ("", 0), ("", 0), ("", 0),
                ("", 0)), strict=True)}} for p in ("plexA", "plexB")}}},
        shows="two TMT plexes, each in its own folder (<plex>/*.raw) -> kept as dropped, one annotation.txt per plex",
    ),
    "tmt_flat_plexes": dict(
        folder="20260128_Aman_TMT_KL6161-flat",
        raws=["KL6161A_TMT_F1.raw", "KL6161A_TMT_F2.raw", "KL6161B_TMT_F1.raw", "KL6161B_TMT_F2.raw"],
        shows="two TMT plexes in one folder -> filed as dropped, with a warning: FragPipe names the channels itself",
    ),
    "fp_cut_table": dict(
        folder="20260911_EJQ_isoDTB_cut-table",
        raws=_iso("EJQ_cut", reps=(1, 2), fracs=(1,)), mode="truncated-table",
        shows="the fake FragPipe leaves its main table cut off mid-row -> failed, 'not written to the end'",
    ),
    "fp_hang": dict(
        folder="20260912_EJQ_isoDTB_hang",
        raws=_iso("EJQ_hang", reps=(1, 2), fracs=(1,)), mode="hang,child",
        shows="the fake FragPipe hangs in MSFragger -> killed with everything it started at the time limit",
    ),
    # D91: TMT in depth (tmtkit.py, downstream/tmtqc.py)
    "tmt16_na": dict(
        folder="20260129_Aman_TMT_KL6162-TMTpro",
        raws=[f"KL6162_TMT_F{i}.raw" for i in range(1, 4)],
        yaml={"workflow": "TMT16.workflow", "tmt": {"tag": "TMT-16", "channels": {
            ch: (f"{c}_1_{ch}" if c else "NA") for ch, c in zip(_TMT16, (
                "DMSO", "DMSO", "DMSO", "DMSO", "Drug", "Drug", "Drug", "Drug", "Drug2", "Drug2", "Drug2",
                "", "", "", "", ""), strict=True)}}},
        shows="TMTpro 16 (TMT16.workflow), 11 channels named and 5 NA -> queued, annotation.txt with NA lines",
    ),
    "tmt_tag_mismatch": dict(
        folder="20260130_Aman_TMT_KL6163-wrong-tag",
        raws=["KL6163_TMT_F1.raw", "KL6163_TMT_F2.raw"],
        yaml={"tmt": {"tag": "TMT-16", "channels": {ch: (f"{c}_1_{ch}" if c else "NA") for ch, c in zip(_TMT16, (
            "DMSO", "DMSO", "DMSO", "Drug", "Drug", "Drug", "", "", "", "", "", "", "", "", "", ""), strict=True)}}},
        shows="experiment.yaml says TMT-16 but the method's workflow is TMT-10 -> failed before the search, saying "
              "which to change",
    ),
    "tmt_own_annotation": dict(
        folder="20260131_Aman_TMT_KL6164-own-map",
        raws=["KL6164_TMT_F1.raw", "KL6164_TMT_F2.raw"],
        texts={"annotation.txt": "".join(f"{ch} {c}_1_{ch}\n" if c else f"{ch} NA\n" for ch, c in zip(_TMT10, (
            "Veh", "Veh", "Veh", "Cmpd", "Cmpd", "Cmpd", "", "", "", ""), strict=True))},
        shows="a TMT drop with the user's own annotation.txt and no tmt: block -> read (never written), FragPipe "
              "uses it, the report knows Veh / Cmpd",
    ),
    "tmt_bridge": dict(
        folder="20260201_Aman_TMT_KL6165-bridge",
        plex_subs={"plexA": ["KL6165A_TMT_F1.raw", "KL6165A_TMT_F2.raw"],
                   "plexB": ["KL6165B_TMT_F1.raw", "KL6165B_TMT_F2.raw"]},
        yaml={"tmt": {"tag": "TMT-10", "reference_channel": "126", "plexes": {
            p: {"channels": {ch: (f"{c}_{k}_{ch}" if c else "NA") for ch, c in zip(_TMT10, (
                "Pool", "DMSO", "DMSO", "DMSO", "Drug", "Drug", "Drug", "", "", ""), strict=True)}}
            for k, p in ((1, "plexA"), (2, "plexB"))}}},
        shows="two TMT plexes in their own folders with a pooled bridge on 126 in each -> IRS on the bridge",
    ),
    "tmt_poor_label": dict(
        folder="20260202_Aman_TMT_KL6166-poor-labelling",
        raws=["KL6166_TMT_F1.raw", "KL6166_TMT_F2.raw"],
        yaml={"tmt": {"tag": "TMT-10", "channels": {ch: (f"{c}_1_{ch}" if c else "NA") for ch, c in zip(_TMT10, (
            "DMSO", "DMSO", "DMSO", "Drug", "Drug", "Drug", "", "", "", ""), strict=True)}}},
        mode="tmt-poor-labelling",
        shows="the fake FragPipe's psm.tsv has 20 % of N-termini unlabelled -> done, the TMT QC warns "
              "TMT_LOW_LABELLING",
    ),
    # round 4: the other searches the lab runs, and what a healthy run on the PC also does
    "lfq_good": dict(
        folder="20260915_Chris_labelfree_AR-interactome",
        raws=[f"{c}_{r}.raw" for c in ("DMSO", "Drug") for r in (1, 2, 3)],
        shows="label-free DDA (method LFQ, LFQ-MBR.workflow): IonQuant's combined_protein.tsv -> done, Drug vs "
              "DMSO from the MaxLFQ intensities",
    ),
    "phospho_lfq": dict(
        folder="20260916_Isaac_lfqphospho_EGF-5min",
        raws=[f"{c}_{r}.raw" for c in ("Ctrl", "EGF") for r in (1, 2, 3)],
        yaml={"analysis": {"phospho": True, "phospho_min_localization": 0.75}},
        shows="label-free phospho (method Phospho, 79.96633 STY): combined_site_STY_79.9663.tsv beside "
              "combined_protein.tsv -> done, sites analysed",
    ),
    "dia_fractions": dict(
        folder="20260917_Isaac_DIA_two-fraction",
        raws=[f"{c}_{r}_F{f}.raw" for c in ("DMSO", "Drug") for r in (1, 2, 3) for f in (1, 2)],
        yaml={"files": {f"{c}_{r}_F{f}.raw": {"experiment": c, "bioreplicate": r, "fraction": f}
                        for c in ("DMSO", "Drug") for r in (1, 2, 3) for f in (1, 2)}},
        shows="DIA in two gas-phase fractions per sample, mapped in experiment.yaml -> DIA-NN reports 12 runs for 6 "
              "samples, which the analysis adds up per sample",
    ),
    "open_search": dict(
        folder="20260918_EJQ_opensearch_mass-shifts",
        raws=[f"HEK_{r}.raw" for r in (1, 2)],
        shows="an open search (Open.workflow: -150 to +500 Da, PTM-Shepherd): psm.tsv with mass shifts and "
              "ptm-shepherd-output/, no quantification table -> done, nothing to compare",
    ),
    "open_search_no_los": dict(
        folder="20260918_EJQ_opensearch_no-localisation",
        raws=[f"HEK_{r}.raw" for r in (1, 2)],
        yaml={"workflow": "Open-noLOS.workflow"},
        shows="the open search with 'Localize mass shift' off: FragPipe prints an ERROR line and searches anyway "
              "-> done, the line not taken for a failure",
    ),
    "non_ascii_log": dict(
        folder="20260919_EJQ_isoDTB_umlaut-log",
        raws=_iso("EJQ_umlaut", reps=(1, 2), fracs=(1,)), mode="non-ascii",
        shows="the tools print umlauts, µ and Japanese: '?' on FragPipe's console (cp1252), the real text in "
              "log_<date>.txt -> done",
    ),
    "retry_previous": dict(
        folder="20260920_EJQ_isoDTB_fails-once",
        raws=_iso("EJQ_once", reps=(1, 2), fracs=(1,)), mode="fail-once",
        shows="the first search fails in IonQuant -> failed; Retry -> done, the first attempt kept as "
              "fragpipe_previous_<time>",
    ),
}


def list_text() -> str:
    """`ionomos testbed list`: every sample drop, its folder, the fake's fault mode for it, and what it shows."""
    w = max(len(n) for n in SAMPLES)
    lines = [f"  {'sample':<{w}}  {'folder':<42}  {'fake mode':<18}  what it shows"]
    for n, spec in SAMPLES.items():
        lines.append(f"  {n:<{w}}  {spec['folder']:<42}  {spec.get('mode') or '-':<18}  {spec['shows']}")
    lines.append(f"\n{len(SAMPLES)} samples. The fake FragPipe's fault modes (IONOMOS_FAKE_FP_MODE, or "
                 "fake_fragpipe_mode.txt in an experiment folder):")
    from ionomos.fake_fragpipe import MODES

    wm = max(len(m) for m in MODES)
    lines += [f"  {m:<{wm}}  {what}" for m, what in MODES.items()]
    return "\n".join(lines)


def _write_raw(p: Path, size: int) -> None:
    p.write_bytes(random.randbytes(size))


def build_sample(dest_parent: Path, name: str, size: int = 4096) -> Path:
    spec = SAMPLES[name]
    d = dest_parent / spec["folder"]
    d.mkdir(parents=True, exist_ok=True)
    for f in spec.get("raws", []):
        _write_raw(d / f, size)
    if spec.get("raw_sub"):
        (d / "raw").mkdir(exist_ok=True)
        for f in spec["raw_sub"]:
            _write_raw(d / "raw" / f, size)
    for sub, files in (spec.get("plex_subs") or {}).items():
        (d / sub).mkdir(exist_ok=True)
        for f in files:
            _write_raw(d / sub / f, size)
    for f in spec.get("others", []):
        (d / f).write_text("just a note\n", encoding="utf-8")
    for f, text in (spec.get("texts") or {}).items():  # files with their own content (a user's annotation.txt)
        (d / f).parent.mkdir(parents=True, exist_ok=True)
        (d / f).write_text(text, encoding="utf-8")
    if spec.get("mode"):
        from ionomos.names import FAKE_FP_MODE_FILE

        (d / FAKE_FP_MODE_FILE).write_text(spec["mode"] + "\n", encoding="utf-8")
    if spec.get("yaml"):
        (d / "experiment.yaml").write_text(yaml.safe_dump(spec["yaml"]), encoding="utf-8")
    return d


# ---------------------------------------------------------------------- init --

def fake_fragpipe(argv: list[str]) -> int:
    """Fake FragPipe (`ionomos fake-fragpipe ...`): FragPipe's headless options, checks, console output and
    output files, without a search. See fake_fragpipe.py for what is copied from the real one and from where."""
    from ionomos import fake_fragpipe as fake

    return fake.fake_fragpipe(argv)


def _fake_results(wd: Path, rows: list[list[str]], workflow_name: str, annotations: dict | None = None,
                  label: str = "TMT-10", props: dict | None = None, modes: set[str] | None = None,
                  runs: dict[str, str] | None = None) -> None:
    """Realistic result tables with planted hits (ionomos.downstream.simulate), so reports have content.

    rows: the manifest as FragPipe read it (experiment names already FragPipe's). annotations (TMT):
    {plex: its annotation file or None}; a plex without one gets FragPipe's own names, <plex>_<channel>.
    props: the workflow's settings (TMT: phospho adds the single-site table; label-free IonQuant without labels
    writes combined_protein.tsv, and the phospho site report when the search has 79.96633 STY; no quantification
    at all, as an open search, only psm.tsv); modes: the fake's fault modes (TMT: tmt-poor-labelling,
    tmt-low-purity, tmt-empty-channel change the psm.tsv files). runs: raw path -> the run name DIA-NN reports
    (FragPipe hands it <raw>_uncalibrated.mzML)."""
    import zlib

    from ionomos.downstream import simulate
    from ionomos.fake_fragpipe import TMT_CHANNELS

    seed = zlib.crc32("".join(r[0] for r in rows).encode())
    p = props or {}
    on = lambda k, d=False: p[k].strip().lower() == "true" if k in p else d  # noqa: E731
    if any(len(r) > 3 and r[3] == "DIA" for r in rows):
        out = wd / "dia-quant-output"  # FragPipe 24's folder (CmdDiann); earlier versions wrote diann-output
        out.mkdir(parents=True, exist_ok=True)
        names = [(runs or {}).get(r[0], r[0]) for r in rows]
        (out / "report.tsv").write_text("Run\tProtein.Group\tPrecursor.Quantity\n", encoding="utf-8")
        simulate.dia_pg_matrix(out / "report.pg_matrix.tsv", [(n, r[1]) for n, r in zip(names, rows, strict=True)],
                               seed)
        fake_diann_stats(out / "report.stats.tsv", names)
        fake_psm(wd / "psm.tsv", [r[0] for r in rows])  # Philosopher's report in the one flat folder
    elif props is not None and not on("ionquant.use-labeling") and not on("tmtintegrator.run-tmtintegrator") \
            and "tmt" not in workflow_name.lower():
        groups = _groups(rows)
        lfq = on("quantitation.run-label-free-quant") and on("ionquant.run-ionquant", True)
        if lfq:
            prots = fake_combined_protein(wd / "combined_protein.tsv", [(g, g.rsplit("_", 1)[0]) for g in groups],
                                          seed)
            if re.search(r"79\.966\d*,\s*STY,\s*true", p.get("msfragger.table.var-mods", "")):
                fake_site_report(wd / "combined_site_STY_79.9663.tsv", prots, list(groups), seed)
        open_search = p.get("msfragger.precursor_mass_units", "1").strip() == "0"
        for name, raws in groups.items():
            fake_psm(wd / name / "psm.tsv", raws, open_search=open_search)
    elif annotations is not None or "tmt" in workflow_name.lower():
        plexes: dict[str, list[tuple[str, str]]] = {}
        for exp in dict.fromkeys(r[1] for r in rows):
            ann = (annotations or {}).get(exp)
            if ann is not None and Path(ann).is_file():
                pairs = [ln.split() for ln in Path(ann).read_text(encoding="utf-8").splitlines() if ln.strip()]
                plexes[exp] = [(p[0], p[1]) for p in pairs if len(p) == 2]
            else:
                plexes[exp] = [(ch, f"{exp}_{ch}") for ch in TMT_CHANNELS.get(label, TMT_CHANNELS["TMT-10"])]
        names = [n for pairs in plexes.values() for _c, n in pairs if n.lower() != "na"]
        fake_tmt_report(wd / "tmt-report", names, seed, props)
        exp_of = {f"{r[1]}_{r[2]}": r[1] for r in rows}
        for name, raws in _groups(rows).items():
            fake_tmt_psm(wd / name / "psm.tsv", raws, plexes[exp_of[name]], label, modes)
    else:
        exps: dict[str, list[int]] = {}
        for r in rows:
            exps.setdefault(r[1], [])
            if int(r[2]) not in exps[r[1]]:
                exps[r[1]].append(int(r[2]))
        simulate.isodtb_label_quant(wd / "combined_modified_peptide_label_quant.tsv",
                                    {e: sorted(v) for e, v in exps.items()}, seed)
        for name, raws in _groups(rows).items():
            fake_psm(wd / name / "psm.tsv", raws)


def _groups(rows: list[list[str]]) -> dict[str, list[str]]:
    """FragPipe writes one psm.tsv per <experiment>_<bioreplicate> folder."""
    groups: dict[str, list[str]] = {}
    for r in rows:
        groups.setdefault(f"{r[1]}_{r[2]}", []).append(r[0])
    return groups


DIANN_STATS_HEADER = ["File.Name", "Precursors.Identified", "Proteins.Identified", "Total.Quantity", "MS1.Signal",
                      "MS2.Signal", "FWHM.Scans", "FWHM.RT", "Median.Mass.Acc.MS1", "Median.Mass.Acc.MS1.Corrected",
                      "Median.Mass.Acc.MS2", "Median.Mass.Acc.MS2.Corrected", "MS2.Mass.Instability",
                      "Normalisation.Instability", "Median.RT.Prediction.Acc", "Average.Peptide.Length",
                      "Average.Peptide.Charge", "Average.Missed.Tryptic.Cleavages"]
PSM_HEADER = ["Spectrum", "Spectrum File", "Peptide", "Modified Peptide", "Peptide Length", "Charge", "Retention",
              "Observed Mass", "Calibrated Observed Mass", "Observed M/Z", "Calibrated Observed M/Z",
              "Calculated Peptide Mass", "Calculated M/Z", "Delta Mass", "Expectation", "Hyperscore",
              "Probability", "Number of Enzymatic Termini", "Number of Missed Cleavages", "Intensity",
              "Assigned Modifications", "Is Unique", "Protein", "Protein ID", "Entry Name", "Gene"]


def _qc_rng(name: str) -> tuple[random.Random, float]:
    """Per-run noise, and a quality factor: a run whose name contains QCBAD is a bad injection (45 % fewer IDs)."""
    import zlib

    return random.Random(zlib.crc32(Path(name).name.encode())), (0.55 if "QCBAD" in name.upper() else 1.0)


def fake_diann_stats(path: Path, raws: list[str]) -> None:
    """A DIA-NN report.stats.tsv with DIA-NN's columns and plausible HeLa-like numbers, one row per run."""
    lines = ["\t".join(DIANN_STATS_HEADER)]
    for raw in raws:
        rng, q = _qc_rng(raw)
        prec = int(42000 * q * rng.gauss(1, 0.015))
        vals = [raw, prec, int(6200 * q * rng.gauss(1, 0.01)), f"{2.1e10 * q * rng.gauss(1, 0.04):.6g}",
                f"{8.0e9 * q:.6g}", f"{1.3e10 * q:.6g}", f"{rng.gauss(6.1, 0.1) / q:.3f}",
                f"{rng.gauss(0.152, 0.003) / q:.5f}", f"{rng.gauss(1.6, 0.15):.4f}", f"{rng.gauss(0.2, 0.05):.4f}",
                f"{rng.gauss(3.1, 0.2):.4f}", f"{rng.gauss(0.4, 0.05):.4f}", "0.02", "0.01", "0.03", "10.8",
                f"{rng.gauss(2.45, 0.01):.4f}", f"{rng.gauss(0.11, 0.004):.4f}"]
        lines.append("\t".join(str(v) for v in vals))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


OPEN_SEARCH_SHIFTS = ((0.0, 60), (15.9949, 10), (0.984, 6), (1.0034, 6), (79.9663, 4), (-17.0265, 4), (42.0106, 3),
                      (14.0157, 3), (-18.0106, 2), (114.0429, 2))  # mass shift (Da), weight: an open search's peaks


def fake_psm(path: Path, raws: list[str], n_psms: int = 240, open_search: bool = False) -> None:
    """A FragPipe psm.tsv with its real columns: n_psms PSMs per run from a fixed pool of peptides (stable RTs,
    ~2 ppm precursor error, ~10 % missed cleavages), so the instrument QC trend has DDA runs to read.
    open_search: Delta Mass carries the mass shifts an open search finds (OPEN_SEARCH_SHIFTS), in Da."""
    pool = random.Random(7)
    aa = "ACDEFGHIKLMNPQRSTVWY"
    peptides = ["".join(pool.choice(aa) for _ in range(pool.randint(7, 18))) + pool.choice("KR") for _ in range(160)]
    base_rt = {p: pool.uniform(600, 5400) for p in peptides}
    lines = ["\t".join(PSM_HEADER)]
    for raw in raws:
        rng, q = _qc_rng(raw)
        stem = Path(raw.replace("\\", "/")).stem
        for k in range(int(n_psms * q)):
            pep = peptides[k % len(peptides)]
            z = 2 if rng.random() < 0.62 else 3
            calc = 110.0 * len(pep) + 18.0106
            obs = calc * (1 + rng.gauss(2.0, 1.5) * 1e-6)
            if open_search:
                obs += rng.choices([s for s, _w in OPEN_SEARCH_SHIFTS], [w for _s, w in OPEN_SEARCH_SHIFTS])[0]
            rt = base_rt[pep] + rng.gauss(0, 6)
            mc = 1 if rng.random() < 0.1 else 0
            scan = f"{k + 1:05d}"
            lines.append("\t".join(str(v) for v in (
                f"{stem}.{scan}.{scan}.{z}", f"interact-{stem}.pep.xml", pep, "", len(pep), z, f"{rt:.3f}",
                f"{obs:.5f}", f"{obs:.5f}", f"{(obs + z * 1.00728) / z:.5f}", f"{(obs + z * 1.00728) / z:.5f}",
                f"{calc:.5f}", f"{(calc + z * 1.00728) / z:.5f}", f"{obs - calc:.5f}", "1e-5", "35", "0.999", 2, mc,
                f"{rng.lognormvariate(16, 1) * q:.1f}", "", "true", f"sp|P{k % 90:05d}|PROT{k % 90}_HUMAN",
                f"P{k % 90:05d}", f"PROT{k % 90}_HUMAN", f"GENE{k % 90}")))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def fake_combined_protein(path: Path, samples: list[tuple[str, str]], seed: int, n_proteins: int = 300) -> list[dict]:
    """IonQuant's combined_protein.tsv for a label-free search, in the layout of a real FragPipe file
    (tests/golden/example/AP-MS): the protein columns, then every sample's Spectral Count, Unique Spectral Count,
    Total Spectral Count, Intensity and MaxLFQ Intensity, each block in sample order, then Indistinguishable
    Proteins; intensities written as Java writes floats (1.6315267E7), 0.0 for missing; two contam_ rows first.
    samples: [(<experiment>_<bioreplicate>, condition)]. Returns the proteins written (for a site report)."""
    import tempfile

    from ionomos.downstream import simulate
    from ionomos.fake_fragpipe import _java_float

    with tempfile.TemporaryDirectory() as td:
        pg = Path(td) / "pg.tsv"
        simulate.dia_pg_matrix(pg, samples, seed=seed, n_proteins=n_proteins)
        _head, *rows = [ln.split("\t") for ln in pg.read_text(encoding="utf-8").splitlines()]
    names = [s for s, _c in samples]
    head = ["Protein", "Protein ID", "Entry Name", "Gene", "Protein Length", "Organism", "Protein Existence",
            "Description", "Protein Probability", "Top Peptide Probability", "Combined Total Peptides",
            "Combined Spectral Count", "Combined Unique Spectral Count", "Combined Total Spectral Count",
            *[f"{s} {kind}" for kind in ("Spectral Count", "Unique Spectral Count", "Total Spectral Count",
                                         "Intensity", "MaxLFQ Intensity") for s in names],
            "Indistinguishable Proteins"]
    contam = [["P00330", "ADH1_YEAST", "ADH1", "Saccharomyces cerevisiae (strain ATCC 204508 / S288c)",
               "Alcohol dehydrogenase 1", "contam_sp"],
              ["P00761", "TRYP_PIG", "", "Sus scrofa", "Trypsin", "contam_sp"]]
    out, prots = ["\t".join(head)], []
    rng = random.Random(seed)
    for k, r in enumerate([None, None, *rows]):
        if r is None:
            acc, entry, gene, org, desc, db = contam[k]
            vals = [rng.lognormvariate(16, 1) if rng.random() < 0.7 else 0.0 for _s in names]
            peps = 1
        else:
            acc, entry, gene, org, desc, db = r[0], r[2], r[3], "Homo sapiens", r[4], "sp"
            vals = [float(v) if v else 0.0 for v in r[7:]]
            peps = int(r[5])
        counts = [max(0, round(math.log2(v) - 14)) if v else 0 for v in vals]
        uniq = [max(0, c - (k % 2)) for c in counts]
        intensity = [v * 1.08 if v else 0.0 for v in vals]
        out.append("\t".join([
            f"{db}|{acc}|{entry}", acc, entry, gene, str(200 + k % 700), org, "1:Experimental evidence at protein level",
            desc, "1.0000" if r is not None else "0.9093", "0.9990", str(peps), str(sum(counts)), str(sum(uniq)),
            str(sum(counts)), *map(str, counts), *map(str, uniq), *map(str, counts),
            *map(_java_float, intensity), *map(_java_float, vals), ""]))
        if r is not None:
            prots.append({"acc": acc, "entry": entry, "gene": gene, "values": vals})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return prots


def fake_site_report(path: Path, prots: list[dict], samples: list[str], seed: int) -> None:
    """IonQuant's phosphosite report (combined_site_STY_79.9663.tsv) as downstream/phospho.py documents it: Index
    (P12345_S142), Gene, Protein, Protein ID, Peptide, Best Localization Probability, then per sample
    "<s> Localization Probability", "<s> Intensity" and "<s> MaxLFQ Intensity"; 0 = missing. One or two sites
    per protein, following the protein's level with changes of their own; a fifth poorly localised."""
    from ionomos.fake_fragpipe import _java_float

    rng = random.Random(seed + 79)
    aa = "ACDEFGHIKLMNPQRSTVWY"
    head = ["Index", "Gene", "Protein", "Protein ID", "Peptide", "Best Localization Probability",
            *[f"{s} {kind}" for s in samples for kind in ("Localization Probability", "Intensity", "MaxLFQ Intensity")]]
    out = ["\t".join(head)]
    for k, pr in enumerate(prots):
        for j in range(1 + (k % 3 == 0)):
            res = rng.choice("STY")
            best = rng.uniform(0.4, 0.74) if rng.random() < 0.2 else rng.uniform(0.75, 1.0)
            change = rng.choice((1.0, 1.0, 1.0, 2.5, 0.4)) if j else 1.0
            cells = []
            for i, v in enumerate(pr["values"]):
                site = v * 0.05 * (change if i >= len(samples) // 2 else 1.0) * 2 ** rng.gauss(0, 0.2) if v else 0.0
                cells += [f"{min(1.0, best + rng.uniform(-0.1, 0.0)):.4f}" if v else "0.0", _java_float(site * 1.05),
                          _java_float(site)]
            pep = "".join(rng.choice(aa) for _ in range(6)) + res + "".join(rng.choice(aa) for _ in range(5)) + "K"
            out.append("\t".join([f"{pr['acc']}_{res}{rng.randint(5, 900)}", pr["gene"], f"sp|{pr['acc']}|{pr['entry']}",
                                  pr["acc"], pep, f"{best:.4f}", *cells]))
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def fake_ptm_shepherd(wd: Path, raws: list[str]) -> None:
    """PTM-Shepherd's ptm-shepherd-output/ of an open search: global.profile.tsv and global.modsummary.tsv (the
    folder and file names FragPipe's output guide gives; the columns are invented, short of the real ones)."""
    out = wd / "ptm-shepherd-output"
    out.mkdir(parents=True, exist_ok=True)
    peaks = [s for s, _w in OPEN_SEARCH_SHIFTS]
    names = {0.0: "None", 15.9949: "Oxidation/Hydroxylation", 0.984: "Deamidation", 1.0034: "First isotopic peak",
             79.9663: "Phosphorylation", -17.0265: "Pyro-glu from Q/Loss of ammonia", 42.0106: "Acetylation",
             14.0157: "Methylation", -18.0106: "Pyro-glu from E/Loss of water", 114.0429: "Ubiquitination"}
    rows = ["peak_apex\tpeak_lower\tpeak_upper\tPSMs\tpercent_PSMs\tmapped_mass_1"]
    for k, s in enumerate(peaks):
        rows.append(f"{s:.4f}\t{s - 0.002:.4f}\t{s + 0.002:.4f}\t{240 * len(raws) // (k + 2)}\t{50 / (k + 1):.2f}\t"
                    f"{names[s]}")
    (out / "global.profile.tsv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    (out / "global.modsummary.tsv").write_text(
        "Modification\tTheoretical Mass Shift\tdataset01_PSMs\n"
        + "".join(f"{names[s]}\t{s:.4f}\t{240 * len(raws) // (k + 2)}\n" for k, s in enumerate(peaks) if s),
        encoding="utf-8")


TMT_FAKE_UNLABELLED = 0.02     # share of PSMs whose N-terminus carries no TMT (mode tmt-poor-labelling: 0.2)
TMT_FAKE_PURITY = 0.85         # median precursor purity (mode tmt-low-purity: 0.55)


def fake_tmt_psm(path: Path, raws: list[str], channels: list[tuple[str, str]], label: str = "TMT-10",
                 modes: set[str] | None = None, n_psms: int = 240) -> None:
    """A TMT psm.tsv as FragPipe 24 writes it with IonQuant's isobaric extraction: fake_psm's columns, Purity after
    Assigned Modifications (TMT on every K and on most peptide N-termini, carbamidomethyl C, oxidised M, a few
    acetylated protein N-termini), and one "Intensity <sample>" column per channel of the label type in the kit's
    order, "Intensity NA" for unused ones (TMT-Integrator's PsmPreProcessor.findChannels reads that block). The
    numbers are invented: a loading factor per channel, a few proteins changed in the non-control conditions, the
    reference near the median. modes: tmt-poor-labelling, tmt-low-purity, tmt-empty-channel (the second named
    channel has almost no signal)."""
    import zlib

    from ionomos.tmtkit import LABEL_TYPES, condition_of, is_na, is_reference_name, label_type

    modes = modes or set()
    fake_psm(path, raws, n_psms)
    lt = label_type(label) or LABEL_TYPES["TMT-10"]
    mass = f"{lt.mass:.4f}"
    by_ch = dict(channels)
    cols = [(ch, by_ch.get(ch, "NA")) for ch in lt.channels]
    rng = random.Random(zlib.crc32(str(Path(path).parent.name).encode()))
    load = [2 ** rng.gauss(0, 0.12) for _ in cols]
    named = [k for k, (_c, n) in enumerate(cols) if not is_na(n)]
    empty = named[1] if "tmt-empty-channel" in modes and len(named) > 1 else None
    conds = [condition_of(n) if not is_na(n) else "" for _c, n in cols]
    ref = [not is_na(n) and is_reference_name(n) for _c, n in cols]
    control = next((c for c in conds if c and c.lower() in ("dmso", "vehicle", "veh", "ctrl", "control")), None)
    unlabelled = 0.2 if "tmt-poor-labelling" in modes else TMT_FAKE_UNLABELLED
    purity_mu = 0.55 if "tmt-low-purity" in modes else TMT_FAKE_PURITY
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    head = lines[0].split("\t")
    at = head.index("Assigned Modifications") + 1
    out_head = head[:at] + ["Purity"] + head[at:] + [f"Intensity {n}" for _c, n in cols]
    out = ["\t".join(out_head)]
    ipep, imod, iint, iprot = head.index("Peptide"), head.index("Modified Peptide"), head.index("Intensity"), \
        head.index("Protein")
    for ln in lines[1:]:
        row = ln.split("\t")
        pep = row[ipep]
        mods, shown = [], ""
        acetyl = rng.random() < 0.04
        if acetyl:
            mods.append("N-term(42.0106)")
            shown = "n[43]"
        elif rng.random() >= unlabelled:
            mods.append(f"N-term({mass})")
            shown = f"n[{round(lt.mass + 1.0078)}]"
        for i, aa in enumerate(pep, 1):
            if aa == "K":
                mods.append(f"{i}K({mass})")
            elif aa == "C":
                mods.append(f"{i}C(57.0215)")
            elif aa == "M" and rng.random() < 0.3:
                mods.append(f"{i}M(15.9949)")
        row[imod] = (shown + pep) if mods else ""
        row[at - 1] = ", ".join(mods)
        purity = max(0.15, min(1.0, rng.gauss(purity_mu, 0.12)))
        k = int(re.sub(r"\D", "", row[iprot].split("|")[1]) or 0) if "|" in row[iprot] else 0
        eff = 1.4 if k % 9 == 0 else (-1.2 if k % 11 == 0 else 0.0)
        base = float(row[iint] or 1e6) / 12
        vals = []
        for j, (_c, _n) in enumerate(cols):
            if rng.random() < 0.02:
                vals.append("0")
                continue
            change = eff if conds[j] and conds[j] != control and not ref[j] else (eff / 2 if ref[j] else 0.0)
            v = base * load[j] * 2 ** (change + rng.gauss(0, 0.2))
            if j == empty:
                v = 0 if rng.random() < 0.7 else v * 0.005
            vals.append(f"{v:.1f}")
        out.append("\t".join(row[:at] + [f"{purity:.3f}"] + row[at:] + vals))
    Path(path).write_text("\n".join(out) + "\n", encoding="utf-8")


def fake_tmt_report(out: Path, names: list[str], seed: int, props: dict | None = None) -> None:
    """TMT-Integrator's tmt-report/ as FragPipe 24's stock workflows leave it with groupby -1 (every level):
    abundance_ and ratio_ tables for gene and protein (MD, and gene also None: the normalisation is FragPipe's
    tmtintegrator.prot_norm), the peptide table, and the single-site table when the workflow searches phospho
    (79.96633 STY as a variable modification). Column layouts from FragPipe's output guide; the numbers are
    simulate.tmt_abundance's (log2, ratio-like) and the ratio tables repeat them."""
    from ionomos.downstream import simulate

    out = Path(out)
    simulate.tmt_abundance(out / "abundance_gene_MD.tsv", names, seed)
    head, *rows = [ln.split("\t") for ln in (out / "abundance_gene_MD.tsv").read_text(encoding="utf-8").splitlines()]
    rng = random.Random(seed)
    k0 = head.index("ReferenceIntensity") + 1
    offs = [rng.gauss(0, 0.35) for _ in head[k0:]]

    def write(name: str, h: list[str], body: list[list[str]]) -> None:
        (out / name).write_text("\n".join("\t".join(r) for r in [h, *body]) + "\n", encoding="utf-8")

    write("ratio_gene_MD.tsv", head, rows)
    write("abundance_gene_None.tsv", head, [r[:k0] + [f"{float(v) + o:.4f}" if v else "" for v, o in
                                                      zip(r[k0:], offs, strict=True)] for r in rows])
    prot_head = ["Index", "NumberPSM", "Gene", "MaxPepProb", "ReferenceIntensity", *head[k0:]]
    prot = [[f"sp|{r[2]}|{r[0]}_HUMAN", r[1], r[0], r[3], r[4], *r[k0:]] for r in rows]
    write("abundance_protein_MD.tsv", prot_head, prot)
    write("ratio_protein_MD.tsv", prot_head, prot)
    pep_head = ["Index", "Gene", "ProteinID", "Peptide", "MaxPepProb", "ReferenceIntensity", *head[k0:]]
    aa = "ACDEFGHILMNPQRSTVWY"
    pep = [[f"sp|{r[2]}|{r[0]}_HUMAN_{10 * j + 1}_{10 * j + 9}", r[0], r[2],
            "".join(rng.choice(aa) for _ in range(8)) + "K", r[3], r[4], *r[k0:]] for r in rows[:200] for j in (0, 1)]
    write("abundance_peptide_MD.tsv", pep_head, pep)
    var = (props or {}).get("msfragger.table.var-mods", "")
    if re.search(r"79\.966\d*,\s*STY,\s*true", var) or "phospho" in (props or {}).get("tmtintegrator.mod_tag", "").lower():
        site_head = ["Index", "Gene", "Peptide", "SequenceWindow", "MaxPepProb", "ReferenceIntensity", *head[k0:]]
        sites = [[f"{r[2]}_{rng.choice('STY')}{rng.randint(5, 400)}", r[0], "".join(rng.choice(aa) for _ in range(9)),
                  "".join(rng.choice(aa) for _ in range(15)), r[3], r[4], *r[k0:]] for r in rows]
        write("abundance_single-site_MD.tsv", site_head, sites)


def fake_diann(argv: list[str]) -> int:
    """Fake DIA-NN (`ionomos fake-diann --cfg ionomos_run/diann.cfg`): reads the cfg the runner writes, fails
    like the real one on a missing raw / FASTA / library, then writes report.pg_matrix.tsv, report.stats.tsv
    and report.log.txt.
    IONOMOS_FAKE_FP_MODE=fail exits 1; IONOMOS_FAKE_FP_SECONDS sets the run time (default 2)."""
    import re as _re

    from ionomos.downstream import simulate

    say = lambda *x: print("FAKE DIA-NN:", *x, flush=True)  # noqa: E731
    if "--cfg" not in argv or argv.index("--cfg") + 1 >= len(argv):
        say("usage: --cfg FILE")
        return 2
    opts: dict[str, list[str]] = {}
    for line in Path(argv[argv.index("--cfg") + 1]).read_text(encoding="utf-8").splitlines():
        parts = line.split(" ", 1)
        if parts[0].startswith("--"):
            opts.setdefault(parts[0], []).append(parts[1] if len(parts) > 1 else "")
    for flag in ("--f", "--fasta", "--lib"):
        for f in opts.get(flag, []):
            if not Path(f).is_file():
                say(f"ERROR: cannot open {f}")
                return 1
    if not opts.get("--f") or not opts.get("--out"):
        say("ERROR: no input files or no --out")
        return 1
    if os.environ.get("IONOMOS_FAKE_FP_MODE") == "fail":
        say("ERROR: fake failure")
        return 1
    total = float(os.environ.get("IONOMOS_FAKE_FP_SECONDS", "2"))
    print("DIA-NN 2.2.0 Academia (Data-Independent Acquisition by Neural Networks)", flush=True)
    for step in ("Loading spectral library", "Processing runs", "Protein inference", "Writing report"):
        print(f"[0:0{int(total)}] {step}", flush=True)
        time.sleep(total / 4)
    out = Path(opts["--out"][0])
    out.parent.mkdir(parents=True, exist_ok=True)
    runs = []
    for f in opts["--f"]:
        stem = Path(f).stem
        m = _re.match(r"^(.*?)[_-]?[A-Za-z]?\d+$", stem)
        runs.append((f, m.group(1) if m and m.group(1) else stem))
    stem = out.name.rsplit(".", 1)[0]
    simulate.dia_pg_matrix(out.parent / f"{stem}.pg_matrix.tsv", runs, seed=len(runs))
    fake_diann_stats(out.parent / f"{stem}.stats.tsv", opts["--f"])
    (out.parent / f"{stem}.log.txt").write_text("DIA-NN 2.2.0 Academia (Data-Independent Acquisition by Neural "
                                                "Networks)\nfake run by the Ionomos testbed\n", encoding="utf-8")
    say("done")
    return 0


def write_fake_diann(folder: Path) -> Path:
    """diann.bat / diann.sh in `folder` that runs `ionomos fake-diann` (for `engine: diann` methods in tests)."""
    from ionomos.service import ionomos_command

    cmd = ionomos_command(console=True)
    folder.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        exe = folder / "diann.bat"
        exe.write_text("@echo off\r\n" + " ".join(f'"{c}"' for c in cmd) + " fake-diann %*\r\n", encoding="utf-8")
    else:
        exe = folder / "diann.sh"
        exe.write_text("#!/bin/sh\nexec " + " ".join(f"'{c}'" for c in cmd) + ' fake-diann "$@"\n', encoding="utf-8")
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return exe


FAKE_MQPAR = """<?xml version="1.0" encoding="utf-8"?>
<MaxQuantParams xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
   <fastaFiles>
      <FastaFileInfo>
         <fastaFilePath></fastaFilePath>
         <identifierParseRule>&gt;.*\\|(.*)\\|</identifierParseRule>
         <descriptionParseRule>&gt;(.*)</descriptionParseRule>
         <taxonomyParseRule></taxonomyParseRule>
         <variationParseRule></variationParseRule>
         <modificationParseRule></modificationParseRule>
         <taxonomyId></taxonomyId>
      </FastaFileInfo>
   </fastaFiles>
   <fixedCombinedFolder></fixedCombinedFolder>
   <numThreads>1</numThreads>
   <maxQuantVersion>2.6.7.0</maxQuantVersion>
   <filePaths />
   <experiments />
   <fractions />
   <ptms />
   <paramGroupIndices />
   <referenceChannel />
   <parameterGroups>
      <parameterGroup>
         <lfqMode>0</lfqMode>
         <enzymes><string>Trypsin/P</string></enzymes>
      </parameterGroup>
   </parameterGroups>
</MaxQuantParams>
"""


def fake_maxquant(argv: list[str]) -> int:
    """Fake MaxQuantCmd (`ionomos fake-maxquant`): `--create FILE` writes a template like the real one;
    `MQPAR` checks the FASTA and raw files exist, then writes combined/txt/proteinGroups.txt (LFQ intensity
    per experiment, planted changes) and parameters.txt into fixedCombinedFolder.
    IONOMOS_FAKE_FP_MODE=fail exits 1."""
    import tempfile
    from xml.etree import ElementTree as ET

    from ionomos.downstream import simulate

    say = lambda *x: print("FAKE MaxQuant:", *x, flush=True)  # noqa: E731
    if argv[:1] == ["--create"] and len(argv) > 1:
        Path(argv[1]).write_text(FAKE_MQPAR, encoding="utf-8")
        say(f"template written to {argv[1]}")
        return 0
    if not argv or not Path(argv[-1]).is_file():
        say("usage: MaxQuantCmd mqpar.xml | --create mqpar.xml")
        return 2
    root = ET.parse(argv[-1]).getroot()
    fasta = root.findtext("fastaFiles/FastaFileInfo/fastaFilePath") or ""
    files = [e.text or "" for e in root.findall("filePaths/string")]
    exps = [e.text or "" for e in root.findall("experiments/string")]
    out = Path(root.findtext("fixedCombinedFolder") or ".")
    if not Path(fasta).is_file():
        say(f"ERROR: FASTA file not found: {fasta}")
        return 1
    for f in files:
        if not Path(f).is_file():
            say(f"ERROR: raw file not found: {f}")
            return 1
    if not files or len(exps) != len(files) or len(root.findall("fractions/short")) != len(files):
        say("ERROR: filePaths / experiments / fractions differ in length")
        return 1
    if os.environ.get("IONOMOS_FAKE_FP_MODE") == "fail":
        say("ERROR: fake failure")
        return 1
    for step in ("Configuring", "Feature detection", "MS/MS search", "Protein assembly", "LFQ", "Writing tables"):
        print(f"{step}...", flush=True)
    samples = list(dict.fromkeys(exps))
    with tempfile.TemporaryDirectory() as td:
        pg = Path(td) / "pg.tsv"
        simulate.dia_pg_matrix(pg, [(x, x.rsplit("_", 1)[0]) for x in samples], seed=len(samples), n_proteins=300)
        head, *rows = [ln.split("\t") for ln in pg.read_text(encoding="utf-8").splitlines()]
    txt = out / "combined" / "txt"
    txt.mkdir(parents=True, exist_ok=True)
    cols = ["Protein IDs", "Majority protein IDs", "Gene names", "Protein names", "Peptides",
            "Razor + unique peptides", "Reverse", "Potential contaminant", "Only identified by site",
            *[f"Intensity {x}" for x in samples], *[f"LFQ intensity {x}" for x in samples]]
    body = []
    for r in rows:
        vals = [v or "0" for v in r[7:]]
        body.append([r[0], r[0], r[3], r[4], r[5], r[5], "", "", "", *vals, *vals])
    (txt / "proteinGroups.txt").write_text("\t".join(cols) + "\n" + "\n".join("\t".join(b) for b in body) + "\n",
                                           encoding="utf-8")
    (txt / "parameters.txt").write_text("Parameter\tValue\nVersion\t2.6.7.0\nProtein FDR\t0.01\n", encoding="utf-8")
    say("done")
    return 0


def write_fake_maxquant(folder: Path) -> Path:
    """MaxQuantCmd.bat / .sh in `folder` running `ionomos fake-maxquant` (for `engine: maxquant` tests)."""
    from ionomos.service import ionomos_command

    cmd = ionomos_command(console=True)
    folder.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        exe = folder / "MaxQuantCmd.bat"
        exe.write_text("@echo off\r\n" + " ".join(f'"{c}"' for c in cmd) + " fake-maxquant %*\r\n", encoding="utf-8")
    else:
        exe = folder / "MaxQuantCmd.sh"
        exe.write_text("#!/bin/sh\nexec " + " ".join(f"'{c}'" for c in cmd) + ' fake-maxquant "$@"\n', encoding="utf-8")
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return exe


def _write_wrapper(folder: Path, name: str, sub: str) -> Path:
    """<name>.bat / .sh in `folder` running `ionomos <sub>` with its arguments."""
    from ionomos.service import ionomos_command

    cmd = ionomos_command(console=True)
    folder.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        exe = folder / f"{name}.bat"
        exe.write_text("@echo off\r\n" + " ".join(f'"{c}"' for c in cmd) + f" {sub} %*\r\n", encoding="utf-8")
    else:
        exe = folder / f"{name}.sh"
        exe.write_text("#!/bin/sh\nexec " + " ".join(f"'{c}'" for c in cmd) + f' {sub} "$@"\n', encoding="utf-8")
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return exe


SAGE_NO_TELEMETRY = "--disable-telemetry-i-dont-want-to-improve-sage"
FAKE_SAGE_VERSION = "0.14.7"


def fake_sage(argv: list[str]) -> int:
    """Fake Sage (`ionomos fake-sage [options] sage.json`): --help prints the real usage (with the telemetry
    switch unless IONOMOS_FAKE_SAGE_OLD=1, an older Sage, which then refuses the flag); a run checks the FASTA
    and every mzML like the real one, then writes what the real one would into output_directory: results.sage.tsv
    and results.json, lfq.tsv when quant.lfq is on (_fake_sage_lfq) and tmt.tsv when quant.tmt names a kit
    (_fake_sage_tmt). IONOMOS_FAKE_FP_MODE=fail exits 1."""
    import json

    say = lambda *x: print("FAKE Sage:", *x, flush=True)  # noqa: E731
    old = os.environ.get("IONOMOS_FAKE_SAGE_OLD") == "1"
    if "--help" in argv or "-h" in argv:
        print("Usage: sage [OPTIONS] <parameters> [mzml_paths]...\n\nOptions:\n  -f, --fasta <fasta>\n"
              "  -o, --output_directory <output_directory>\n      --batch-size <batch-size>\n      --parquet\n"
              "      --write-pin\n" + ("" if old else f"      {SAGE_NO_TELEMETRY}\n          Disable sending "
                                                        "telemetry data\n") + "  -h, --help\n  -V, --version")
        return 0
    if SAGE_NO_TELEMETRY in argv and old:
        say(f"error: unexpected argument '{SAGE_NO_TELEMETRY}' found")
        return 2
    params = [a for a in argv if a.lower().endswith(".json")]
    if not params or not Path(params[-1]).is_file():
        say("error: the following required arguments were not provided: <parameters>")
        return 2
    cfg = json.loads(Path(params[-1]).read_text(encoding="utf-8"))
    fasta = (cfg.get("database") or {}).get("fasta") or ""
    paths = cfg.get("mzml_paths") or []
    out = cfg.get("output_directory")
    if not Path(fasta).is_file():
        say(f"Error: Failed to build database from `{fasta}`: No such file or directory")
        return 1
    if not paths or not out:
        say("Error: `mzml_paths` must be set. For more information try '--help'")
        return 1
    for f in paths:
        if not Path(f).exists():
            say(f"Error: failed to read {f}: No such file or directory")
            return 1
    if os.environ.get("IONOMOS_FAKE_FP_MODE") == "fail":
        say("Error: fake failure")
        return 1
    say("telemetry", "off" if SAGE_NO_TELEMETRY in argv else "ON",
        "| threads", os.environ.get("RAYON_NUM_THREADS", "all"))
    names = [Path(f).name for f in paths]
    quant = cfg.get("quant") or {}
    kit = quant.get("tmt")
    channels = SAGE_KITS.get(kit) if isinstance(kit, str) else len((kit or {}).get("User") or [])
    if kit and not channels:
        say(f"Error: unknown variant `{kit}`, expected one of `Tmt6`, `Tmt10`, `Tmt11`, `Tmt16`, `Tmt18`, `User`")
        return 1
    outdir = Path(out)
    outdir.mkdir(parents=True, exist_ok=True)
    psm: list[list[str]] = []
    if kit:
        head = "tmt" if isinstance(kit, str) else "user"
        tmt, psm = _fake_sage_tmt(names, channels)
        (outdir / "tmt.tsv").write_text("\n".join("\t".join(r) for r in [
            ["filename", "scannr", "ion_injection_time", *[f"{head}_{k + 1}" for k in range(channels)]], *tmt]) + "\n",
            encoding="utf-8")
    if quant.get("lfq") or not kit:
        lfq, lfq_psm = _fake_sage_lfq(names)
        psm = psm or lfq_psm
        (outdir / "lfq.tsv").write_text("\n".join("\t".join(r) for r in lfq) + "\n", encoding="utf-8")
    (outdir / "results.sage.tsv").write_text("\n".join("\t".join(r) for r in [SAGE_PSM_HEADER, *psm]) + "\n",
                                             encoding="utf-8")
    (outdir / "results.json").write_text(json.dumps({"version": FAKE_SAGE_VERSION, **cfg}, indent=2), encoding="utf-8")
    say("finished")
    return 0


SAGE_KITS = {"Tmt6": 6, "Tmt10": 10, "Tmt11": 11, "Tmt16": 16, "Tmt18": 18}
# results.sage.tsv as Sage 0.14 / 0.15 writes it (sage-cli runner.rs, write_features)
SAGE_PSM_HEADER = [
    "psm_id", "peptide", "proteins", "protein_groups", "num_proteins", "num_protein_groups", "filename", "scannr",
    "rank", "label", "expmass", "calcmass", "charge", "peptide_len", "missed_cleavages", "semi_enzymatic",
    "isotope_error", "precursor_ppm", "fragment_ppm", "hyperscore", "delta_next", "delta_best", "rt", "aligned_rt",
    "predicted_rt", "delta_rt_model", "ion_mobility", "predicted_mobility", "delta_mobility", "matched_peaks",
    "longest_b", "longest_y", "longest_y_pct", "matched_intensity_pct", "scored_candidates", "poisson",
    "sage_discriminant_score", "posterior_error", "spectrum_q", "peptide_q", "protein_q", "protein_group_q",
    "ms2_intensity"]


def _sage_scan(n: int) -> str:
    return f"controllerType=0 controllerNumber=1 scan={n}"


def _sage_psm(n: int, pep: str, prots: str, filename: str, scan: int, intensity: float = 0.0, label: int = 1,
              rank: int = 1, spectrum_q: float = 0.001, peptide_q: float = 0.001, protein_q: float = 0.001) -> list[str]:
    """One results.sage.tsv row. The mass error is +1.5 ppm with one PSM in seven on the first isotope peak; the
    RT (minutes) belongs to the peptide; every seventh peptide has a missed cleavage, every third is 3+."""
    k = sum(ord(c) for c in pep)
    calc = 900.0 + (k % 1500)
    iso = 1 if n % 7 == 0 else 0
    exp = calc * (1 + 1.5e-6) + iso * 1.00335
    rt = 5.0 + (k % 9000) / 100.0
    row = dict.fromkeys(SAGE_PSM_HEADER, "0.0")
    row.update({"psm_id": str(n), "peptide": pep, "proteins": prots, "protein_groups": "",
                "num_proteins": str(prots.count(";") + 1), "num_protein_groups": "0", "filename": filename,
                "scannr": _sage_scan(scan), "rank": str(rank), "label": str(label), "expmass": f"{exp:.5f}",
                "calcmass": f"{calc:.5f}", "charge": "3" if k % 3 == 0 else "2", "peptide_len": str(len(pep)),
                "missed_cleavages": "1" if k % 7 == 0 else "0", "semi_enzymatic": "0", "isotope_error": f"{iso}.0",
                "precursor_ppm": "1.5", "fragment_ppm": "2.5", "hyperscore": "30.0", "rt": f"{rt:.3f}",
                "aligned_rt": f"{rt / 100:.4f}", "matched_peaks": "12", "longest_b": "4", "longest_y": "8",
                "spectrum_q": f"{spectrum_q:g}", "peptide_q": f"{min(peptide_q, 1):g}", "protein_q": f"{protein_q:g}",
                "protein_group_q": "1.0", "ms2_intensity": f"{intensity:.1f}"})
    return [row[h] for h in SAGE_PSM_HEADER]


def _fake_sage_lfq(names: list[str]) -> tuple[list[list[str]], list[list[str]]]:
    """(lfq.tsv rows with the header, results.sage.tsv rows) of a label-free search: three peptide ions per
    protein, one shared between two proteins, a decoy-only and a high-q row; a PSM per ion and file."""
    import re as _re
    import tempfile

    from ionomos.downstream import simulate

    samples: dict[str, str] = {}  # file -> its sample: fractions (a second trailing number) share one
    for n in names:
        stem = _re.sub(r"(?i)\.mzml(\.gz)?$|\.d$", "", n)
        m = _re.match(r"^(.*_\d+)_\d+$", stem)
        samples[n] = m.group(1) if m else stem
    order = list(dict.fromkeys(samples.values()))
    with tempfile.TemporaryDirectory() as td:
        pg = Path(td) / "pg.tsv"
        simulate.dia_pg_matrix(pg, [(x, _re.sub(r"[_-]?\d+$", "", x) or x) for x in order], seed=len(order),
                               n_proteins=300)
        _head, *rows = [ln.split("\t") for ln in pg.read_text(encoding="utf-8").splitlines()]
    col = {x: 7 + j for j, x in enumerate(order)}
    share = {n: 1.0 / sum(1 for v in samples.values() if v == samples[n]) for n in names}  # split over fractions
    lfq = [["peptide", "charge", "proteins", "q_value", "score", "spectral_angle", *names]]
    psm: list[list[str]] = []
    scans = dict.fromkeys(names, 0)

    def ion(pep, prots, q, factor, r):
        vals = []
        for n in names:
            v = r[col[samples[n]]] if r is not None else ""
            vals.append(f"{float(v) * factor * share[n]:.1f}" if v else "0.0")
            if v:
                scans[n] += 1
                psm.append(_sage_psm(len(psm) + 1, pep, prots, n, scans[n], float(vals[-1]),
                                     label=-1 if prots.startswith("rev_") else 1, peptide_q=q,
                                     protein_q=0.2 if "FAILS" in pep else 0.001))
        lfq.append([pep, "-1", prots, f"{q:g}", "1.5", "0.9", *vals])

    for k, r in enumerate(rows):
        prot = f"sp|{r[0]}|{r[2]}"
        for j, factor in enumerate((1.0, 0.5, 0.25)):
            ion(f"PEPTIDE{k}K{'A' * j}R", prot, 0.001, factor, r)
        if k % 50 == 1:   # shared with the previous protein, which has as many peptides: the razor picks by name
            ion(f"SHARED{k}R", f"sp|{rows[k - 1][0]}|{rows[k - 1][2]};{prot}", 0.001, 0.3, r)
    ion("HIGHQPEPTIDEK", "sp|P99990|HIGHQ_HUMAN", 0.2, 1.0, rows[0])          # above the peptide q-value
    ion("PROTEINFAILSK", "sp|P99991|PROTQ_HUMAN", 0.001, 1.0, rows[0])        # its protein is above the protein q
    ion("DECOYONLYK", "rev_sp|P99992|DECOY_HUMAN", 0.001, 1.0, rows[0])
    return lfq, psm


def fake_sage_tmt_design(channels: int) -> list[str]:
    """The fake TMT experiment's condition per channel, in kit order: a pool in the first and last channel,
    DMSO in the first half of the rest and Drug in the second."""
    inner = channels - 2
    return ["Pool", *["DMSO"] * (inner - inner // 2), *["Drug"] * (inner // 2), "Pool"]


def _fake_sage_tmt(names: list[str], channels: int) -> tuple[list[list[str]], list[list[str]]]:
    """(tmt.tsv rows without the header, results.sage.tsv rows) of a TMT search. A plex is the files that share
    a name up to _F<fraction>; its channels follow fake_sage_tmt_design(); every protein has its own level in
    every plex (the plex effect IRS removes) and changes planted in Drug. Three PSMs per protein and plex,
    spread over the plex's fractions; a shared peptide; and rows the analysis must leave out, each with a huge
    reporter signal: a decoy, PSMs above the spectrum and protein q-values, a rank-2 PSM, plus a reporter row
    for a spectrum that was not identified."""
    import random
    import re as _re
    import tempfile

    from ionomos.downstream import simulate

    plex_of = {n: _re.sub(r"(?i)(?:_TMT)?[_-]F?\d+$", "", _re.sub(r"(?i)\.mzml(\.gz)?$|\.d$", "", n)) for n in names}
    plexes = list(dict.fromkeys(plex_of.values()))
    design = fake_sage_tmt_design(channels)
    cols = [(p, j) for p in plexes for j, c in enumerate(design) if c != "Pool"]
    with tempfile.TemporaryDirectory() as td:
        pg = Path(td) / "pg.tsv"
        simulate.dia_pg_matrix(pg, [(f"{p}_{j}", design[j]) for p, j in cols], seed=len(plexes), n_proteins=300)
        _head, *rows = [ln.split("\t") for ln in pg.read_text(encoding="utf-8").splitlines()]
    at = {pj: 7 + k for k, pj in enumerate(cols)}
    rng = random.Random(len(plexes))
    tmt: list[list[str]] = []
    psm: list[list[str]] = []
    scans = dict.fromkeys(names, 0)

    def spectrum(pep, prots, filename, vals, **kw):
        scans[filename] += 1
        if pep:
            psm.append(_sage_psm(len(psm) + 1, pep, prots, filename, scans[filename], sum(vals), **kw))
        if kw.get("rank", 1) == 1:  # a second PSM of a spectrum shares its reporter row
            tmt.append([filename, _sage_scan(scans[filename]), "50.0", *[f"{v:.2f}" for v in vals]])
        else:
            scans[filename] -= 1
            psm[-1][SAGE_PSM_HEADER.index("scannr")] = _sage_scan(scans[filename])

    for p in plexes:
        files = [n for n in names if plex_of[n] == p]
        huge = [1e12] * channels
        for k, r in enumerate(rows):
            shift = 2 ** rng.gauss(0, 1.2)
            seen = [float(r[at[(p, j)]]) for j, c in enumerate(design) if c != "Pool" and r[at[(p, j)]]]
            pool = sum(seen) / len(seen) if seen else 0.0
            level = [(pool if c == "Pool" else float(r[at[(p, j)]] or 0)) * shift for j, c in enumerate(design)]
            prot = f"sp|{r[0]}|{r[2]}"
            for j, factor in enumerate((1.0, 0.5, 0.25)):
                spectrum(f"PEPTIDE{k}K{'A' * j}R", prot, files[(k + j) % len(files)], [v * factor for v in level])
            if k % 50 == 1:
                spectrum(f"SHARED{k}R", f"sp|{rows[k - 1][0]}|{rows[k - 1][2]};{prot}", files[0],
                         [v * 0.3 for v in level])
        first = f"sp|{rows[0][0]}|{rows[0][2]}"
        spectrum("HIGHQPEPTIDEK", first, files[0], huge, spectrum_q=0.2)
        spectrum("PROTEINFAILSK", "sp|P99991|PROTQ_HUMAN", files[0], huge, protein_q=0.2)
        spectrum("DECOYONLYK", "rev_sp|P99992|DECOY_HUMAN", files[0], huge, label=-1)
        spectrum("SECONDRANKK", first, files[0], huge, rank=2)   # on the DECOYONLYK spectrum
        spectrum("", "", files[0], huge)                          # an MS3 scan whose MS2 was not identified
    return tmt, psm


def fake_rawparser(argv: list[str]) -> int:
    """Fake ThermoRawFileParser (`ionomos fake-rawparser -i=RAW -o=DIR -f=2`): writes DIR/<stem>.mzML.
    IONOMOS_FAKE_CONVERT_MODE=fail exits 1 without writing; =empty exits 0 without writing."""
    opts = dict(a.split("=", 1) for a in argv if a.startswith("-") and "=" in a)
    raw, out = Path(opts.get("-i", "")), Path(opts.get("-o", ""))
    if not raw.is_file() or not out.is_dir():
        print(f"FAKE ThermoRawFileParser: ERROR input {raw} or output folder {out} not found", flush=True)
        return 1
    mode = os.environ.get("IONOMOS_FAKE_CONVERT_MODE")
    if mode == "fail":
        print(f"FAKE ThermoRawFileParser: ERROR RawFileReader could not open {raw.name}", flush=True)
        return 1
    if mode != "empty":
        (out / f"{raw.stem}.mzML").write_text(f"<mzML><!-- fake, from {raw.name} --></mzML>\n", encoding="utf-8")
    print(f"FAKE ThermoRawFileParser: converted {raw.name}", flush=True)
    return 0


def write_fake_sage(folder: Path) -> Path:
    """sage.bat / .sh in `folder` running `ionomos fake-sage` (for `engine: sage` tests)."""
    return _write_wrapper(folder, "sage", "fake-sage")


def write_fake_rawparser(folder: Path) -> Path:
    """ThermoRawFileParser.bat / .sh in `folder` running `ionomos fake-rawparser`."""
    return _write_wrapper(folder, "ThermoRawFileParser", "fake-rawparser")


def write_fake_launcher(folder: Path) -> Path:
    """fragpipe.bat / fragpipe.sh in `folder` that runs `ionomos fake-fragpipe` (works frozen or from a venv)."""
    from ionomos.service import ionomos_command

    cmd = ionomos_command(console=True)
    folder.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        exe = folder / "fragpipe.bat"
        quoted = " ".join(f'"{c}"' for c in cmd)
        exe.write_text(f"@echo off\r\n{quoted} fake-fragpipe %*\r\n", encoding="utf-8")
    else:
        exe = folder / "fragpipe.sh"
        quoted = " ".join(f"'{c}'" for c in cmd)
        exe.write_text(f'#!/bin/sh\nexec {quoted} fake-fragpipe "$@"\n', encoding="utf-8")
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return exe


def workflow_text(kind: str, label: str = "TMT-10") -> str:
    """A small .workflow for the testbed: the switches Ionomos and the fake FragPipe read, with the values of
    FragPipe 24.0's stock chemprot-ABPP-isoDTB / TMT10-MS3 / TMT16 / DIA_SpecLib_Quant workflows (a real one has ~400
    keys). The isoDTB one has match-between-runs on, as the lab's SOP asks. label: a TMT workflow's label type
    (TMT-16 / TMT-18 get TMTpro's mass and MS2 quantification, as the stock TMT16 workflow).
    Also (round 4): LFQ (the stock LFQ-MBR: IonQuant MaxLFQ with match-between-runs), Phospho (LFQ-phospho:
    79.96633 on STY searched, IonQuant's site report), Open (the stock Open search: -150 to +500 Da, PeptideProphet,
    Crystal-C, PTM-Shepherd, mass-shift localisation, no quantification) and Open-noLOS (the same with
    localisation off, which FragPipe answers with an ERROR line and runs anyway)."""
    if kind in ("LFQ", "Phospho", "Open", "Open-noLOS"):
        return _lfq_workflow_text(kind)
    tmt, dia = kind == "TMT", kind == "DIA"
    from ionomos.tmtkit import TMT_MASS, label_type

    lt = label_type(label) if tmt else None
    mass = f"{lt.mass:.5f}" if lt else f"{TMT_MASS:.5f}"
    mass = {"229.16290": "229.16293", "304.20710": "304.20715"}.get(mass, mass)  # the stock workflows' digits
    keys = {
        "crystalc.run-crystalc": "false",
        "database.decoy-tag": "rev_",
        "diann.run-dia-nn": str(dia).lower(),
        "diatracer.run-diatracer": "false",
        "diaumpire.run-diaumpire": "false",
        "freequant.run-freequant": "false",
        "ionquant.heavy": "" if tmt or dia else "C567.3462",
        "ionquant.light": "" if tmt or dia else "C561.3387",
        "ionquant.mbr": "0" if tmt or dia else "1",
        "ionquant.run-ionquant": "true",
        "ionquant.use-labeling": str(not (tmt or dia)).lower(),
        "ionquant.use-lfq": "false",
        "msbooster.run-msbooster": "true",
        "msfragger.calibrate_mass": "2",
        "msfragger.misc.slice-db": "1",
        "msfragger.run-msfragger": "true",
        "msfragger.search_enzyme_name_1": "stricttrypsin",
        "peptide-prophet.run-peptide-prophet": "false",
        "percolator.run-percolator": "true",
        "phi-report.run-report": "true",
        "protein-prophet.run-protein-prophet": "true",
        "ptmprophet.run-ptmprophet": "false",
        "ptmshepherd.run-shepherd": "false",
        "quantitation.run-label-free-quant": str(not (tmt or dia)).lower(),
        "run-psm-validation": "true",
        "run-validation-tab": "true",
        "speclibgen.run-speclibgen": str(dia).lower(),
        "tab-run.delete_temp_files": "false",
        "tmtintegrator.add_Ref": "1" if tmt else "-1",
        "tmtintegrator.channel_num": (lt.name if lt else "TMT-10") if tmt else "TMT-6",
        "tmtintegrator.extraction_tool": "IonQuant",
        "tmtintegrator.ref_tag": "Bridge",
        "tmtintegrator.run-tmtintegrator": str(tmt).lower(),
        "workflow.input.data-type.im-ms": "false",
        "workflow.input.data-type.regular-ms": "true",
        # FragPipe's stock workflows say true and then write <workdir>/sdrf.tsv, which the analysis reads as the
        # experiment's own design (downstream/sdrfdesign.py). Off here until that is settled (docs/ROADMAP.md);
        # tests/test_fragpipe_real.py runs one job with it on.
        "workflow.misc.save-sdrf": "false",
        "workflow.misc.sdrf-type": "Default",
        "workflow.saved-with-ver": "24.0-build27",
    }
    if tmt:  # the stock TMT workflows' modifications and TMT-Integrator settings (TMT10-MS3 / TMT16, FragPipe 24.0)
        keys.update({
            "msfragger.table.fix-mods": f"57.02146,C (cysteine),true,-1; {mass},K (lysine),true,-1",
            "msfragger.table.var-mods": f"15.9949,M,true,3; 42.0106,[^,true,1; 79.96633,STY,false,3; "
                                        f"{mass},n^,true,1; 229.16293,S,false,1",
            "tmtintegrator.groupby": "-1", "tmtintegrator.min_pep_prob": "0.9", "tmtintegrator.min_purity": "0.5",
            "tmtintegrator.min_site_prob": "-1", "tmtintegrator.mod_tag": "none", "tmtintegrator.ms1_int": "true",
            "tmtintegrator.prot_norm": "1", "tmtintegrator.quant_level": "2" if lt and lt.reagent == "TMTpro" else "3",
        })
        keys = dict(sorted(keys.items()))
    return (f"# Workflow: {kind}{' ' + lt.name if lt and lt.name != 'TMT-10' else ''} (Ionomos testbed)\n\n"
            + "".join(f"{k}={v}\n" for k, v in keys.items()) + "database.db-path=FAKE.fas\n")


def _lfq_workflow_text(kind: str) -> str:
    """The label-free and open-search workflows (FragPipe 24.0's stock LFQ-MBR, LFQ-phospho and Open, the keys the
    fake and Ionomos read)."""
    open_search = kind.startswith("Open")
    keys = {
        "crystalc.run-crystalc": str(open_search).lower(),
        "database.decoy-tag": "rev_",
        "diann.run-dia-nn": "false",
        "diatracer.run-diatracer": "false",
        "diaumpire.run-diaumpire": "false",
        "freequant.run-freequant": "false",
        "ionquant.heavy": "",
        "ionquant.light": "",
        "ionquant.maxlfq": "1",
        "ionquant.mbr": "0" if open_search else "1",
        "ionquant.run-ionquant": str(not open_search).lower(),
        "ionquant.use-labeling": "false",
        "ionquant.use-lfq": str(not open_search).lower(),
        "msbooster.run-msbooster": str(not open_search).lower(),
        "msfragger.calibrate_mass": "2",
        "msfragger.localize_delta_mass": "0" if kind == "Open-noLOS" else ("1" if open_search else "0"),
        "msfragger.misc.slice-db": "1",
        "msfragger.precursor_mass_lower": "-150" if open_search else "-20",
        "msfragger.precursor_mass_units": "0" if open_search else "1",
        "msfragger.precursor_mass_upper": "500" if open_search else "20",
        "msfragger.run-msfragger": "true",
        "msfragger.search_enzyme_name_1": "stricttrypsin",
        "msfragger.table.fix-mods": "57.02146,C (cysteine),true,-1",
        "msfragger.table.var-mods": "15.9949,M,true,3; 42.0106,[^,true,1"
                                    + ("; 79.96633,STY,true,3" if kind == "Phospho" else "; 79.96633,STY,false,3"),
        "peptide-prophet.run-peptide-prophet": str(open_search).lower(),
        "percolator.run-percolator": str(not open_search).lower(),
        "phi-report.run-report": "true",
        "protein-prophet.run-protein-prophet": "true",
        "ptmprophet.run-ptmprophet": str(kind == "Phospho").lower(),
        "ptmshepherd.run-shepherd": str(open_search).lower(),
        "quantitation.run-label-free-quant": str(not open_search).lower(),
        "run-psm-validation": "true",
        "run-validation-tab": "true",
        "speclibgen.run-speclibgen": "false",
        "tab-run.delete_temp_files": "false",
        "tmtintegrator.run-tmtintegrator": "false",
        "workflow.input.data-type.im-ms": "false",
        "workflow.input.data-type.regular-ms": "true",
        "workflow.misc.save-sdrf": "false",
        "workflow.misc.sdrf-type": "Default",
        "workflow.saved-with-ver": "24.0-build27",
    }
    return (f"# Workflow: {kind} (Ionomos testbed)\n\n" + "".join(f"{k}={v}\n" for k, v in keys.items())
            + "database.db-path=FAKE.fas\n")


# the methods ionomos testbed init configures beyond isoDTB / TMT / DIA (round 4): key -> (keywords, workflow).
# Keywords no test's folder name holds: 'lfq' or 'phospho' alone made 'isoDTB_sage-lfq' name two methods
EXTRA_METHODS = {"LFQ": (["labelfree"], "LFQ-MBR.workflow"),
                 "Phospho": (["lfqphospho"], "LFQ-phospho.workflow"),
                 "Open": (["opensearch"], "Open.workflow")}


def init(root: Path, slow_defaults: bool = False) -> Path:
    root = Path(root).resolve()
    auto = root / "Fragpipe_Auto"
    general = root / "Fragpipe_General"
    for d in (auto / "inbox", auto / "workflows", auto / "fasta", auto / "logs", root / "samples"):
        d.mkdir(parents=True, exist_ok=True)
    for u in USERS:
        (general / u).mkdir(parents=True, exist_ok=True)
    for wf, kind, label in (("isoDTB.workflow", "isoDTB", ""), ("TMT10-MS3.workflow", "TMT", "TMT-10"),
                            ("TMT16.workflow", "TMT", "TMT-16"), ("DIA.workflow", "DIA", ""),
                            ("LFQ-MBR.workflow", "LFQ", ""), ("LFQ-phospho.workflow", "Phospho", ""),
                            ("Open.workflow", "Open", ""), ("Open-noLOS.workflow", "Open-noLOS", "")):
        p = auto / "workflows" / wf
        if not p.exists():
            p.write_text(workflow_text(kind, label or "TMT-10"), encoding="utf-8", newline="\n")
    (auto / "fasta" / "human_reviewed_decoys.fas").write_text(
        ">sp|FAKE1|FAKE1_HUMAN fake protein 1\nMKVLAAGIVGLLLAC\n>sp|FAKE2|FAKE2_HUMAN fake protein 2\nMSTNPKPQRKTKRNT\n"
        ">rev_sp|FAKE1|FAKE1_HUMAN\nCALLLGVIGAALVKM\n>rev_sp|FAKE2|FAKE2_HUMAN\nTNRKTKRQPKPNTSM\n", encoding="utf-8")

    exe = write_fake_launcher(auto)
    (auto / "fake_fragpipe.py").unlink(missing_ok=True)  # older testbeds

    def s(p: Path) -> str:  # forward slashes, as recommended in config
        return str(p).replace("\\", "/")

    cfg = {
        "paths": {
            "inbox": s(auto / "inbox"), "users_root": s(general), "fragpipe_exe": s(exe),
            "workflow_dir": s(auto / "workflows"), "fasta_dir": s(auto / "fasta"),
            "database": s(auto / "ionomos.db"), "log_dir": s(auto / "logs"),
        },
        "watcher": {"poll_seconds": 1 if not slow_defaults else 10, "stable_seconds": 3 if not slow_defaults else 60,
                    "min_raw_files": 1},
        "fragpipe": {"threads": 4, "ram_gb": 4, "timeout_minutes": 5, "min_free_gb": 0.1},
        "gui": {"enabled": True, "timeout_minutes": 0},
        "users": {"aliases": {"Isaac": ["IJ", "IJD"], "EJQ": ["EJQ_2"]}, "default": ""},
        "methods": {
            "isoDTB": {"aliases": ["isodtb", "iso-dtb"], "workflow": "isoDTB.workflow",
                       "fasta": "human_reviewed_decoys.fas", "data_type": "DDA",
                       "postprocess": ["isodtb_sites"], "isodtb_mod_mass": "561.3387"},
            "TMT": {"aliases": ["tmt"], "workflow": "TMT10-MS3.workflow", "fasta": "human_reviewed_decoys.fas",
                    "data_type": "DDA", "postprocess": ["tmt_annotation"]},
            "DIA": {"aliases": ["dia", "diann", "dia-nn"], "workflow": "DIA.workflow",
                    "fasta": "human_reviewed_decoys.fas", "data_type": "DIA", "postprocess": []},
            **{key: {"aliases": aliases, "workflow": wf, "fasta": "human_reviewed_decoys.fas", "data_type": "DDA",
                     "postprocess": []} for key, (aliases, wf) in EXTRA_METHODS.items()},
        },
        # the lab's own methods read their raw names as isoDTB's do: <sample>_<rep>[_<fraction>] (NAMING_CONVENTION)
        "naming": {"methods": {key: {"files": "{sample}_{rep}[_{fraction}]"} for key in EXTRA_METHODS}},
    }
    cfg_path = auto / "config.yaml"
    cfg_path.write_text("# ionomos TESTBED config - generated by `ionomos testbed init`\n" + yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    for name in SAMPLES:
        build_sample(root / "samples", name)
    (root / "README.txt").write_text(_readme(root, cfg_path), encoding="utf-8")
    return cfg_path


def _readme(root: Path, cfg_path: Path) -> str:
    return f"""ionomos testbed at {root}

Terminal 1 (the watcher):
    ionomos --config "{cfg_path}" run

Terminal 2 (you, being a lab member):
    ionomos testbed list "{root}"
    ionomos testbed drop iso_good "{root}"
    ionomos testbed drop gui_unknown_user "{root}"      # resolver window opens in terminal 1
    ionomos --config "{cfg_path}" status
    ionomos testbed reset "{root}"

Or drag folders from  {root / 'samples'}  into  {root / 'Fragpipe_Auto' / 'inbox'}  with Finder/Explorer.
Results land in  {root / 'Fragpipe_General'}/<user>/ .
"""


# --------------------------------------------------------------------- drop --


def drop(root: Path, name: str, slow: bool = False, delay: float = 0.4) -> Path:
    root = Path(root).resolve()
    src = root / "samples" / SAMPLES[name]["folder"]
    if not src.is_dir():
        build_sample(root / "samples", name)
    dst = root / "Fragpipe_Auto" / "inbox" / src.name
    if dst.exists():
        raise SystemExit(f"already in inbox: {dst}")
    if not slow:
        shutil.copytree(src, dst)
        return dst
    # file by file, with pauses: exercises the stability wait
    dst.mkdir()
    files = sorted(p for p in src.rglob("*") if p.is_file())
    for i, p in enumerate(files, 1):
        rel = p.relative_to(src)
        (dst / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dst / rel)
        print(f"  copied {i}/{len(files)} {rel}")
        time.sleep(delay)
    return dst


def reset(root: Path) -> None:
    root = Path(root).resolve()
    auto = root / "Fragpipe_Auto"
    for d in (auto / "inbox", root / "Fragpipe_General"):
        if d.is_dir():
            shutil.rmtree(d)
        d.mkdir(parents=True)
    for f in [auto / "ionomos.db", auto / "learned_aliases.yaml", *(auto / "logs").glob("ionomos.log*")]:
        if f.exists():
            f.unlink()
    for u in USERS:
        (root / "Fragpipe_General" / u).mkdir(parents=True, exist_ok=True)


def gui_demo() -> int:
    from ionomos.intake import Draft, DraftFile, Kind
    from ionomos.resolve import TkResolver, gui_available

    ok, why = gui_available()
    if not ok:
        print(f"GUI not available: {why}")
        return 1
    import tkinter as tk

    files = [DraftFile(f"EJQ_PK_EJQ-2-027_isoDTB_1uM_3h_{r}_{f}.raw", "EJQ_PK_EJQ-2-027_isoDTB_1uM_3h", str(r), str(f))
             for r in (1, 2, 3) for f in range(1, 8)]
    files.append(DraftFile("EJQ_PK_EJQ-2-027_isoDTB_1uM_3h_extra.raw", "EJQ_PK_EJQ-2-027_isoDTB_1uM_3h_extra", "1", "",
                           error="must end in _rep_fraction"))
    d = Draft(folder="20260902-isoDTB_XYZ-2-027 (1uM 3h)",
              problem="no known user in '20260902-isoDTB_XYZ-2-027 (1uM 3h)'; include your initials or folder name "
                      "(known: Aman, Chris, EJQ, Isaac)",
              kind=Kind.USER, user="", method="isoDTB", date="2026-09-02",
              known_users=USERS, known_methods=["isoDTB", "TMT", "DIA"], files=files)
    root = tk.Tk()
    root.withdraw()
    res = TkResolver(root, remember=lambda u, a: print(f"(would remember alias {a!r} -> {u})"))
    ov = res.resolve(d)
    root.destroy()
    print("answer:", "skipped" if ov is None else yaml.safe_dump(ov.to_dict(), sort_keys=False))
    return 0


# ---------------------------------------------------------------------- cli --


def add_parser(sub):
    p = sub.add_parser("testbed", help="build/drive a fake lab for testing", description=__doc__,
                       formatter_class=__import__("argparse").RawDescriptionHelpFormatter)
    s = p.add_subparsers(dest="tb_cmd", required=True)
    i = s.add_parser("init", help="create the testbed")
    i.add_argument("dir", nargs="?", default=DEFAULT_DIR)
    i.add_argument("--slow-defaults", action="store_true", help="use production poll/stable timings")
    ls = s.add_parser("list", help="list sample drops")
    ls.add_argument("dir", nargs="?", default=DEFAULT_DIR)
    d = s.add_parser("drop", help="copy a sample into the inbox")
    d.add_argument("name", choices=sorted(SAMPLES))
    d.add_argument("dir", nargs="?", default=DEFAULT_DIR)
    d.add_argument("--slow", action="store_true", help="copy file by file with pauses")
    r = s.add_parser("reset", help="empty inbox/users/ledger/logs")
    r.add_argument("dir", nargs="?", default=DEFAULT_DIR)
    s.add_parser("gui-demo", help="open the resolver window with sample data")
    st = s.add_parser("stress", help="many messy drops + chaos against a real watcher/worker; checks invariants")
    st.add_argument("--n", type=int, default=60, help="number of drops (default 60)")
    st.add_argument("--seed", type=int, default=1)
    st.add_argument("--dir", default=None, help="where to build it (default: a temp folder, deleted afterwards)")
    st.add_argument("--keep", action="store_true", help="keep the temp folder to inspect")
    st.add_argument("--no-chaos", action="store_true")
    st.add_argument("--fuzz", type=int, default=3000, help="random names through the parsers (0 = skip)")
    return p


def path_room_note(root: Path) -> str | None:
    """A note when Windows long paths are off and `root` is so deep that the testbed's own samples would pass
    260 characters inside their experiment folders (#138); None otherwise (and off Windows)."""
    from ionomos.preflight import _long_paths_enabled
    from ionomos.stress import BELOW

    if os.name != "nt" or _long_paths_enabled() is not False:
        return None
    longest = max(len(spec["folder"]) for spec in SAMPLES.values())
    user = max(len(u) for u in USERS)
    depth = len(str(Path(root).resolve()))
    need = depth + len("/Fragpipe_General/") + user + 1 + longest + BELOW
    if need < 260:
        return None
    return (f"note: Windows long paths are off and this folder is {depth} characters deep; the longest sample "
            f"needs about {need}, past Windows' 260. A search of a long-named drop will fail on its paths: put "
            "the testbed nearer the drive root, e.g. C:/bed.")


def main(args) -> int:
    if args.tb_cmd == "init":
        cfg = init(Path(args.dir), args.slow_defaults)
        print((Path(args.dir).resolve() / "README.txt").read_text(encoding="utf-8"))
        print(f"config: {cfg}")
        note = path_room_note(Path(args.dir))
        if note:
            print(note)
        return 0
    if args.tb_cmd == "list":
        print(list_text())
        return 0
    if args.tb_cmd == "drop":
        dst = drop(Path(args.dir), args.name, slow=args.slow)
        print(f"dropped {dst}")
        return 0
    if args.tb_cmd == "stress":
        import logging

        from ionomos import stress

        logging.basicConfig(level=logging.WARNING, format="%(levelname)-7s %(name)s: %(message)s")
        bad = stress.fuzz_names(args.fuzz, args.seed) if args.fuzz else []
        print(f"fuzz: {args.fuzz} random names through the parsers — {len(bad)} failure(s)")
        for b in bad[:10]:
            print("  -", b)
        print(f"stress: {args.n} drops, seed {args.seed}{'' if not args.no_chaos else ', no chaos'} …", flush=True)
        rep = stress.run(args.n, args.seed, Path(args.dir) if args.dir else None, keep=args.keep,
                         chaos=not args.no_chaos)
        print(rep.text())
        return 0 if rep.ok and not bad else 1
    if args.tb_cmd == "reset":
        reset(Path(args.dir))
        print("testbed reset")
        return 0
    if args.tb_cmd == "gui-demo":
        return gui_demo()
    return 2
