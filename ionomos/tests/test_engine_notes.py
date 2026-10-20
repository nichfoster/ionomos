"""Which of the engine loaders' notes are information and which are warnings (downstream/engines.py, D110, D118).

quant.Info only says what was read or done as planned: the report and analysis.json list it, a job's warnings
leave it out (postprocess.job_warnings). quant.AsksConditions asks for the conditions of 'unassigned' channels and
goes once the design gives them (sdrfdesign.answered). A plain str is a warning. NOTES lists every note each small
table makes, with its type, so a new note fails here until someone decides which it is."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ionomos.downstream import anytable, engines, sdrfdesign
from ionomos.downstream.analysis import Settings
from ionomos.downstream.quant import AsksConditions, Info
from tests.test_engines import LONG_HEAD, RUNS, _diann_long_rows, _truth, _write
from tests.test_engines import _maxquant as _maxquant_lfq
from tests.test_plexes import SAGE_PSM, _maxquant_one_experiment, _sim, _tsv, _write_msstats_tmt, _write_sage_tmt
from tests.test_plexes import _maxquant as _maxquant_tmt

INFO, ASKS, WARN = Info, AsksConditions, str


def _prots(tmp_path: Path, n: int = 30):
    prots, vals, _truth_ = _truth(tmp_path, n=n)
    return prots, vals


def _spectronaut_long(tmp_path: Path):
    prots, vals = _prots(tmp_path)
    head = ["R.Condition", "R.FileName", "R.Replicate", "PG.ProteinGroups", "PG.Genes", "PG.Quantity", "PG.Qvalue"]
    rows = [[r.split("_")[0], f"20260930_{r}_SN", r.split("_")[1], p, g, vals[r][p], 0.001]
            for p, g, _d, _n in prots for r in RUNS if vals[r][p]]
    rows.append(["DMSO", "20260930_DMSO_1_SN", 1, "PBAD", "BAD", 1e12, 0.2])     # above 1%: left out
    return engines.load_spectronaut(_write(tmp_path / "sn" / "Report_BGS.tsv", head, rows)).notes


def _spectronaut_pivot(tmp_path: Path):
    prots, vals = _prots(tmp_path)
    head = ["PG.ProteinGroups", "PG.Genes"] + [f"[{k}] {r}.PG.Quantity" for k, r in enumerate(RUNS, 1)]
    path = _write(tmp_path / "piv" / "pivot.tsv", head, [[p, g, *[vals[r][p] for r in RUNS]] for p, g, _d, _n in prots])
    return engines.load_spectronaut(path).notes


def _diann_long(tmp_path: Path, rollup: str = "auto"):
    prots, vals = _prots(tmp_path)
    return engines.load_diann_long(_write(tmp_path / "report.tsv", LONG_HEAD, _diann_long_rows(prots, vals)),
                                   rollup).notes


def _diann_long_split(tmp_path: Path):
    """P1's precursor A is measured in the DMSO runs only and B in the Drug runs only: MaxLFQ can't link them."""
    head = ["Run", "Protein.Group", "Genes", "Precursor.Id", "Q.Value", "PG.Q.Value", "PG.MaxLFQ", "Precursor.Normalised"]
    rows = [[r, "P1", "ONE", "AK2" if r.startswith("DMSO") else "BK2", 0.001, 0.001, 1000, 1000 + k]
            for k, r in enumerate(RUNS)]
    rows += [[r, "P2", "TWO", "CK2", 0.001, 0.001, 500, 500 + k] for k, r in enumerate(RUNS)]
    return engines.load_diann_long(_write(tmp_path / "report.tsv", head, rows), "maxlfq").notes


def _msstats(tmp_path: Path):
    prots, vals = _prots(tmp_path)
    head = ["ProteinName", "PeptideSequence", "PrecursorCharge", "FragmentIon", "ProductCharge", "IsotopeLabelType",
            "Condition", "BioReplicate", "Run", "Intensity"]
    rows = []
    for pid, _g, _d, _n in prots:
        for run in RUNS:
            c, r = run.split("_")
            rows += [[pid, f"PEP{k}K", 2, "NA", "NA", "L", c, r, f"{run}.mzML", (vals[run][pid] or 0) * share or "NA"]
                     for k, share in enumerate((0.5, 0.3, 0.2))]
            rows.append([pid, "PEP0K", 2, "NA", "NA", "H", c, r, f"{run}.mzML", 1e9])   # heavy reference: left out
    return engines.load_msstats(_write(tmp_path / "out_msstats.csv", head, rows, sep=",")).notes


def _msstats_tmt(tmp_path: Path, norm: bool = True):
    prots, sim, _hits = _sim(n=20, plexes=2)
    path = _write_msstats_tmt(tmp_path / "msstatstmt.csv", prots, sim, techreps=2)
    if not norm:  # the pools called Pool, not MSstatsTMT's Norm: no normalisation between the runs
        path.write_text(path.read_text(encoding="utf-8").replace(",Norm,Norm,", ",Pool,Pool,"), encoding="utf-8")
    return engines.load_msstats_tmt(path).notes


def _sage_filters(tmp_path: Path, psms: bool = True):
    """test_engines' Sage filters case, with results.json naming a FASTA."""
    files = ["a_1.mzML", "a_2.mzML", "b_1.mzML", "b_2.mzML", "stray.mzML"]
    rows = [["AAAK", 2, "sp|P1|ONE_HUMAN", 0.001, 1, 0.9, 100, 300, 800, 0, 50],
            ["CCCK", 2, "sp|P1|ONE_HUMAN;sp|P2|TWO_HUMAN", 0.001, 1, 0.9, 10, 10, 10, 10, 10],    # razor: to P1
            ["DDDK", 2, "sp|P2|TWO_HUMAN", 0.001, 1, 0.9, 64, 64, 64, 64, 64],
            ["HIGHQK", 2, "sp|P3|THREE_HUMAN", 0.05, 1, 0.9, 1e9, 1e9, 1e9, 1e9, 1e9],            # peptide q > 1%
            ["PROTQK", 2, "sp|P4|FOUR_HUMAN", 0.001, 1, 0.9, 1e9, 1e9, 1e9, 1e9, 1e9]]            # protein q > 1%
    lfq = _write(tmp_path / "lfq.tsv", ["peptide", "charge", "proteins", "q_value", "score", "spectral_angle", *files],
                 rows)
    if psms:
        _write(tmp_path / "results.sage.tsv", ["psm_id", "peptide", "proteins", "label", "protein_q"],
               [[1, "AAAK", "sp|P1|ONE_HUMAN", 1, 0.001], [2, "PROTQK", "sp|P4|FOUR_HUMAN", 1, 0.3]])
    fasta = tmp_path / "human.fasta"
    fasta.write_text(">sp|P1|ONE_HUMAN Protein one OS=Homo sapiens OX=9606 GN=ONE PE=1 SV=1\nMK\n", encoding="utf-8")
    (tmp_path / "results.json").write_text(json.dumps({"version": "0.14.7", "database": {"fasta": str(fasta)}}),
                                           encoding="utf-8")
    smap = {"a_1": ("Ctrl", 1), "a_2": ("Ctrl", 1), "b_1": ("Drug", 1), "b_2": ("Drug", 1), "gone": ("Drug", 2)}
    return engines.load_sage(lfq, smap).notes


def _sage_tmt(tmp_path: Path):
    prots, sim, _hits = _sim(n=20, plexes=2)
    path, _long = _write_sage_tmt(tmp_path / "sage", prots, sim)
    return engines.load_sage_tmt(path).notes


def _sage_tmt_filtered(tmp_path: Path):
    return _sage_tmt_partial_map(tmp_path).notes


def _sage_tmt_partial_map(tmp_path: Path):
    """Each PSM filter of load_sage_tmt once (test_plexes' filter case, shorter), and a channel map that names
    four channels, calls one empty and leaves one out."""
    f, ok = "plexA_F1.mzML", (1, 1, 2, 0.001, 0.001, 0.001)
    psm = [[1, "AAAK", "sp|P1|ONE_HUMAN", f, "scan=1", *ok], [2, "AAAK", "sp|P1|ONE_HUMAN", f, "scan=2", *ok],
           [3, "CCCK", "sp|P1|ONE_HUMAN;sp|P2|TWO_HUMAN", f, "scan=3", *ok], [4, "DDDK", "sp|P2|TWO_HUMAN", f, "scan=4", *ok],
           [5, "DECOYK", "rev_sp|P3|THREE_HUMAN", f, "scan=5", 1, -1, 2, 0.001, 0.001, 0.001],
           [6, "RANKK", "sp|P4|FOUR_HUMAN", f, "scan=5", 2, 1, 2, 0.001, 0.001, 0.001],
           [7, "QK", "sp|P4|FOUR_HUMAN", f, "scan=6", 1, 1, 2, 0.05, 0.001, 0.001],
           [8, "CHIMAK", "sp|P5|FIVE_HUMAN", f, "scan=7", *ok], [9, "CHIMBK", "sp|P5|FIVE_HUMAN", f, "scan=7", *ok],
           [10, "NOIONSK", "sp|P6|SIX_HUMAN", f, "scan=8", *ok]]
    big = [1e9] * 6
    tmt = [[f, "scan=1", 20, 400, 800, 1600, 300, 400, 400], [f, "scan=2", 20, 1, 1, 1, 1, 1, 1],
           [f, "scan=3", 20, 100, 200, 400, 100, 100, 100], [f, "scan=4", 20, 64, 64, 64, 64, 64, 64],
           *[[f, f"scan={k}", 20, *big] for k in (5, 6, 7)], [f, "scan=8", 20, 0, 0, 0, 0, 0, 0]]
    _tsv(tmp_path / "results.sage.tsv", SAGE_PSM, psm)
    path = _tsv(tmp_path / "tmt.tsv", ["filename", "scannr", "ion_injection_time", *[f"tmt_{k}" for k in range(1, 7)]],
                tmt)
    return engines.load_sage_tmt(path, tmt={"channels": {126: "Ctrl_1", 127: "Ctrl_2", 128: "Drug_1", 129: "Drug_2",
                                                         130: "empty"}})


def _proteome_discoverer(tmp_path: Path):
    prots, vals = _prots(tmp_path)
    head = ["Checked", "Master", "Accession", "Description", "# Unique Peptides"] + \
        [f"Abundances (Normalized): F{k}: Sample, {r.split('_')[0]}" for k, r in enumerate(RUNS, 1)]
    path = _write(tmp_path / "pd" / "Proteins.txt", head,
                  [["FALSE", "IsMasterProtein", p, d, n, *[vals[r][p] for r in RUNS]] for p, _g, d, n in prots])
    return engines.load_pd(path).notes


def _alphadia(tmp_path: Path):
    prots, vals = _prots(tmp_path)
    path = _write(tmp_path / "ad" / "pg.matrix.tsv", ["pg", *RUNS], [[p, *[vals[r][p] for r in RUNS]] for p, *_ in prots])
    return engines.load_alphadia(path).notes


BUILD = {
    "MaxQuant LFQ": lambda t: engines.load_maxquant(_maxquant_lfq(t, *_prots(t))).notes,
    "MaxQuant TMT, two experiments": lambda t: engines.load_maxquant(_maxquant_tmt(t / "mq", *_sim(n=20, plexes=2)[:2])
                                                                     ).notes,
    "Spectronaut long report": _spectronaut_long,
    "Spectronaut pivot report": _spectronaut_pivot,
    "DIA-NN long report": _diann_long,
    "DIA-NN long report, maxlfq without precursor quantities": lambda t: _diann_long(t, "maxlfq"),
    "DIA-NN long report, maxlfq, a protein in two unlinked groups": _diann_long_split,
    "MSstats format": _msstats,
    "MSstatsTMT format": _msstats_tmt,
    "MSstatsTMT format without Norm channels": lambda t: _msstats_tmt(t, norm=False),
    "Sage LFQ": _sage_filters,
    "Sage LFQ without results.sage.tsv": lambda t: _sage_filters(t, psms=False),
    "Sage TMT without a channel map": _sage_tmt,
    "Sage TMT, filtered PSMs and a partial channel map": _sage_tmt_filtered,
    "Proteome Discoverer": _proteome_discoverer,
    "AlphaDIA": _alphadia,
}

RAW = "look like raw intensities; log2-transformed"   # anytable's, for an engine's own table (known=True)

NOTES: dict[str, list[tuple[str, type]]] = {  # case -> every note it makes, in order: (how it starts, its type)
    "MaxQuant LFQ": [
        ("2 row(s) flagged Reverse / Potential contaminant / Only identified by site were left out", INFO),
        (f"values in proteinGroups.txt {RAW}", INFO),
        ("6 sample column(s) found by ^LFQ intensity", INFO)],
    "MaxQuant TMT, two experiments": [
        (f"values in proteinGroups.txt {RAW}", INFO),
        ("30 sample column(s) found by ^Reporter intensity corrected", INFO),
        ("10 reporter columns summed over experiments were left out", INFO),
        ("MaxQuant names 20 TMT channels by number only", ASKS)],
    "Spectronaut long report": [
        ("Spectronaut report Report_BGS.tsv: protein quantity PG.Quantity; conditions and replicates from R.Condition "
         "/ R.Replicate; 1 rows above q 0.01 left out", INFO)],
    "Spectronaut pivot report": [
        (f"values in pivot.tsv {RAW}", INFO),
        ("6 sample column(s) found by", INFO)],
    "DIA-NN long report": [
        ("DIA-NN long report report.tsv: protein quantity PG.MaxLFQ, precursors and protein groups at q ≤ 0.01 (30 "
         "rows above it left out)", INFO)],
    "DIA-NN long report, maxlfq without precursor quantities": [
        ("DIA-NN long report report.tsv: protein quantity PG.MaxLFQ", INFO),
        ("analysis.rollup: maxlfq needs Precursor.Id and Precursor.Normalised", WARN)],
    "DIA-NN long report, maxlfq, a protein in two unlinked groups": [
        ("DIA-NN long report report.tsv: precursors (Precursor.Normalised) rolled up to protein groups by MaxLFQ", INFO),
        ("1 protein(s) have samples that share no feature with the others", WARN)],
    "MSstats format": [
        ("MSstats-format out_msstats.csv: 30 proteins summarised from 90 features by Tukey median polish (MSstats' "
         "default); 180 heavy / reference rows left out", INFO)],
    "MSstatsTMT format": [
        ("MSstatsTMT-format msstatstmt.csv: 20 proteins in 2 mixture(s)", INFO),
        ("technical replicates of a mixture were averaged", INFO)],
    "MSstatsTMT format without Norm channels": [
        ("MSstatsTMT-format msstatstmt.csv: 20 proteins in 2 mixture(s)", INFO),
        ("no 'Norm' channel (Condition Norm) in the MSstatsTMT table", WARN),
        ("technical replicates of a mixture were averaged", INFO)],
    "Sage LFQ": [
        ("Sage 0.14.7 lfq.tsv: 2 protein groups from 3 peptide ions at peptide q ≤ 0.01 and protein q ≤ 0.01", INFO),
        ("1 ion(s) above the peptide q-value and 1 above the protein q-value were left out", INFO),
        ("1 peptide(s) shared between protein groups were given to the group with the most peptides", INFO),
        ("the intensities of each sample's fractions were added (2 sample(s) with 2–2 files)", INFO),
        ("gene names from human.fasta", INFO),
        ("Run stray did not match the manifest", WARN),
        ("Expected runs missing from Sage's lfq.tsv: gone", WARN)],
    "Sage LFQ without results.sage.tsv": [   # no protein q-value filter: the summary says so, as a warning
        ("Sage 0.14.7 lfq.tsv: 3 protein groups from 4 peptide ions at peptide q ≤ 0.01 (no results.sage.tsv beside "
         "it, so no protein q-value filter)", WARN),
        ("1 ion(s) above the peptide q-value and 0 above the protein q-value", INFO),
        ("1 peptide(s) shared between protein groups", INFO),
        ("the intensities of each sample's fractions were added", INFO),
        ("gene names from human.fasta", INFO),
        ("Run stray did not match the manifest", WARN),
        ("Expected runs missing from Sage's lfq.tsv: gone", WARN)],
    "Sage TMT without a channel map": [
        ("Sage 0.14.7 tmt.tsv: 20 protein groups in 2 plex(es) from 120 PSMs", INFO),
        ("fractions combined within 2 mixture run(s)", INFO),
        ("20 of 20 TMT channels have no name in an experiment.yaml tmt: map", ASKS)],
    "Sage TMT, filtered PSMs and a partial channel map": [
        ("Sage tmt.tsv: 2 protein groups in 1 plex(es) from 4 PSMs", INFO),
        ("of 10 PSMs in results.sage.tsv, left out: 1 decoy, 1 of lower rank, 1 above a q-value", INFO),
        ("1 spectra with more than one passing PSM were left out", INFO),
        ("1 passing PSM(s) had no reporter ions in tmt.tsv and were left out", WARN),
        ("1 peptide(s) shared between protein groups", INFO),
        ("1 features had several PSMs in a run", INFO),
        ("1 channel(s) called NA / empty in experiment.yaml's tmt: map were left out", INFO),
        ("1 of 5 TMT channels have no name in an experiment.yaml tmt: map", ASKS)],
    "Proteome Discoverer": [
        (f"values in Proteins.txt {RAW}", INFO),
        ("6 sample column(s) found by ^Abundances?", INFO)],
    "AlphaDIA": [
        (f"values in pg.matrix.tsv {RAW}", INFO),
        ("6 sample column(s) found by numeric columns", INFO)],
}


@pytest.mark.parametrize("case", list(BUILD))
def test_every_engine_note_is_listed_with_its_kind(tmp_path, case):
    notes = BUILD[case](tmp_path)
    want = NOTES[case]
    got = [(next((start for start, _k in want if n.startswith(start)), str(n)), type(n).__name__) for n in notes]
    assert got == [(start, kind.__name__) for start, kind in want]


def test_a_table_read_as_any_table_keeps_its_guesses_as_warnings(tmp_path):
    """The same pg.matrix.tsv read by the any-table loader (D33), not as AlphaDIA's: which columns are samples and
    whether the values are log2 is a guess there, so both notes warn."""
    prots, vals = _prots(tmp_path)
    path = _write(tmp_path / "t.tsv", ["pg", *RUNS], [[p, *[vals[r][p] for r in RUNS]] for p, *_ in prots])
    notes = anytable.load(path).notes
    assert [type(n) for n in notes] == [str, str] and RAW in notes[0] and "sample column(s) found by" in notes[1]
    assert [type(n) for n in anytable.load(path, known=True).notes] == [Info, Info]


# ---------------------------------------------------------- the question for the conditions, answered (D118) --


def test_the_question_for_conditions_goes_once_the_design_answers_every_channel(tmp_path):
    """MaxQuant's numbered channels: the note stays while one channel analysed is still 'unassigned' and goes when
    sample_conditions, exclude_samples and reference channels (the tmt_reference setting, an SDRF's pooled rows)
    leave none."""
    prots, sim, _hits = _sim(n=20, plexes=1)
    m = engines.load_maxquant(_maxquant_one_experiment(tmp_path / "mq", prots, sim))
    (asks,) = [n for n in m.notes if isinstance(n, AsksConditions)]
    rest = [n for n in m.notes if n is not asks]
    every = {s: ("DMSO" if k % 2 else "Drug") for k, s in enumerate(m.samples)}
    some = {s: every[s] for s in m.samples[:8]}
    pools = m.samples[8:]

    def after(**kw):
        return sdrfdesign.answered(m.notes, m, Settings(**kw))

    assert after() == m.notes and after(sample_conditions=some) == m.notes   # two channels still unassigned
    assert after(sample_conditions=every) == rest
    assert after(sample_conditions=some, exclude_samples=pools) == rest
    assert after(sample_conditions=some, tmt_reference=[m.meta["channel"][s] for s in pools]) == rest
    assert after(sample_conditions={**some, pools[0]: " "}) == m.notes       # a blank condition answers nothing
    m.meta["reference_samples"] = pools                                        # what an SDRF's pooled rows set
    assert after(sample_conditions=some) == rest


def test_sages_question_goes_too_and_nothing_else_does(tmp_path):
    m = _sage_tmt_partial_map(tmp_path)
    unnamed = [s for s in m.samples if m.condition[s] == "unassigned"]
    assert len(unnamed) == 1
    kept = sdrfdesign.answered(m.notes, m, Settings(sample_conditions={unnamed[0]: "Drug"}))
    assert kept == [n for n in m.notes if not isinstance(n, AsksConditions)] and len(kept) == len(m.notes) - 1
    assert sdrfdesign.answered(["a warning", Info("a fact")], m, Settings()) == ["a warning", "a fact"]
