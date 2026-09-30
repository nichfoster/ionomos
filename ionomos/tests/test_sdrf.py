"""results/sdrf.tsv (downstream/sdrf.py): SDRF-Proteomics v1.1.0 sample metadata for every analysed experiment.

Structure is checked here against the spec's rules (column order, required columns, reserved words, row
uniqueness); when the official validator (sdrf-pipelines, dev-only) is installed, it checks the files too."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from ionomos import downstream
from ionomos.downstream import analysis, isodtb, sdrf, simulate

NO_ENRICH = {"enrichment": False}
REQUIRED = ["source name", "characteristics[organism]", "characteristics[organism part]",
            "characteristics[biological replicate]", "assay name", "technology type",
            "comment[proteomics data acquisition method]", "comment[label]", "comment[instrument]",
            "comment[cleavage agent details]", "comment[fraction identifier]", "comment[technical replicate]",
            "comment[data file]"]
LAB = {"sdrf": {"instrument": "Orbitrap Eclipse"}}  # what a lab sets once in config.yaml


def _read(path: Path) -> tuple[list[str], list[list[str]]]:
    """Header + rows, keeping repeated columns (comment[modification parameters])."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return lines[0].split("\t"), [ln.split("\t") for ln in lines[1:]]


def _col(header, rows, name) -> list[str]:
    j = header.index(name)
    return [r[j] for r in rows]


def _by(header, rows, key="source name") -> dict[str, dict]:
    return {r[header.index(key)] + "|" + r[header.index("comment[label]")] + "|" + r[header.index("assay name")]:
            dict(zip(header, r, strict=True)) for r in rows}


def _check_structure(path: Path) -> tuple[list[str], list[list[str]]]:
    header, rows = _read(path)
    assert header[0] == "source name" and header[-1].startswith("factor value[")
    for h in REQUIRED:
        assert h in header, h
    # spec order: sample metadata, then data-file metadata, then factor values
    kinds = ["c" if h.startswith("characteristics[") else "f" if h.startswith("factor value[") else
             "s" if h == "source name" else "d" for h in header]
    assert kinds == sorted(kinds, key="scdf".index), header
    assert header.index("assay name") < header.index("technology type") < header.index("comment[label]")
    assert all(h == h.lower() or "[" in h for h in header)  # column names are lower-case
    assert rows
    seen = set()
    files_of_assay: dict[str, str] = {}
    for r in rows:
        assert len(r) == len(header)
        assert all(c and c == c.strip() and "\t" not in c and "  " not in c for c in r), r
        cells = dict(zip(header, r, strict=False))
        for c in r:
            if c.lower() in sdrf.RESERVED:
                assert c == c.lower()  # reserved words must be lower-case
        key = (cells["source name"], cells["assay name"], cells["comment[label]"])
        assert key not in seen, key  # MUST be unique
        seen.add(key)
        assert files_of_assay.setdefault(cells["assay name"], cells["comment[data file]"]) == cells["comment[data file]"]
        assert cells["characteristics[biological replicate]"].isdigit() or \
            cells["characteristics[biological replicate]"] == "pooled"
        assert cells["comment[fraction identifier]"].isdigit() and cells["comment[technical replicate]"].isdigit()
        assert cells["technology type"] == sdrf.TECHNOLOGY
        assert cells["comment[sdrf version]"] == sdrf.SPEC_VERSION
    return header, rows


def _workflow(folder: Path, fasta: Path | None = None, extra: str = "", enzyme: str = "stricttrypsin") -> Path:
    """The lines of a FragPipe .workflow the SDRF reads (FragPipe leaves a copy in its output folder)."""
    lines = [
        "# FragPipe workflow (test)",
        f"database.db-path={str(fasta).replace(chr(92), '/').replace(':', chr(92) + ':')}" if fasta else "",
        "database.decoy-tag=rev_",
        f"msfragger.search_enzyme_name_1={enzyme}",
        "msfragger.search_enzyme_name_2=null",
        "msfragger.table.fix-mods=0.0,C-Term Peptide,true,-1; 57.02146,C (cysteine),true,-1; 0.0,Z,true,-1",
        "msfragger.table.var-mods=15.9949,M,true,3; 42.0106,[^,true,1; 79.96633,STY,false,3; "
        "-17.0265,nQnC,true,1; 0.0,site_10,false,1" if "var-mods" not in extra else "",
        extra,
    ]
    p = folder / "fragpipe.workflow"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(x for x in lines if x) + "\n", encoding="utf-8")
    return p


def _fasta(path: Path, species: dict[str, int]) -> Path:
    lines = []
    k = 0
    for os_, n in species.items():
        for _ in range(n):
            k += 1
            lines += [f">sp|P{k:05d}|G{k}_X Protein {k} OS={os_} OX=1 GN=G{k} PE=1 SV=1", "MKT"]
            lines += [f">rev_sp|P{k:05d}|G{k}_X", "TKM"]
    lines += [">contam_sp|P99999|ALBU_BOVIN Serum albumin OS=Bos taurus OX=9913", "MKW"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _dia(tmp_path: Path, reps=(1, 2, 3), extra_files=()) -> tuple[Path, dict]:
    dest = tmp_path / "20260902_KC_DIA_test"
    runs = [(f"C:\\Fragpipe_General\\KC\\exp\\raw\\{c}_{r}.raw", c) for c in ("DMSO", "Drug") for r in reps]
    simulate.dia_pg_matrix(dest / "fragpipe/diann-output/report.pg_matrix.tsv", runs, seed=21)
    fasta = _fasta(tmp_path / "fasta" / "human.fas", {"Homo sapiens": 30})
    _workflow(dest / "fragpipe", fasta)
    manifest = [{"file": f"raw/{c}_{r}.raw", "experiment": c, "bioreplicate": r, "data_type": "DIA"}
                for c in ("DMSO", "Drug") for r in reps]
    manifest += [{"file": f"raw/{f}", "experiment": f.split("_")[0], "bioreplicate": int(f[-5]), "data_type": "DIA"}
                 for f in extra_files]
    return dest, {"plan": {"manifest": manifest, "raw_dir": "raw"}, "run": {"fasta": str(fasta)}}


# --------------------------------------------------------------------- DIA --


def test_dia_one_row_per_raw_file_with_the_analysis_conditions(tmp_path):
    dest, record = _dia(tmp_path, extra_files=["DMSO_4.raw"])  # DMSO_4 was searched but has no column
    overrides = {"exclude_samples": ["Drug_3"], "sample_conditions": {"Drug_1": "DMSO"}}
    out = downstream.analyze(dest, "DIA", {**NO_ENRICH, **LAB}, overrides, record=record)
    header, rows = _check_structure(dest / "results/sdrf.tsv")
    assert _col(header, rows, "comment[data file]") == [f"{c}_{r}.raw" for c in ("DMSO", "Drug") for r in (1, 2, 3)] \
        + ["DMSO_4.raw"]
    cells = {r[header.index("comment[data file]")]: dict(zip(header, r, strict=True)) for r in rows}
    assert cells["Drug_1.raw"]["factor value[condition]"] == "DMSO"  # the renamed condition, as analysed
    # a replicate number is never reused within a condition: DMSO already has 1-3 (and 4), so Drug_1 gets 5
    assert [cells[f"DMSO_{r}.raw"]["characteristics[biological replicate]"] for r in (1, 2, 3, 4)] == ["1", "2", "3", "4"]
    assert cells["Drug_1.raw"]["characteristics[biological replicate]"] == "5"
    assert cells["Drug_3.raw"]["factor value[condition]"] == "Drug"  # left out of the analysis, still listed
    assert cells["DMSO_1.raw"]["comment[label]"] == "label free sample"
    assert cells["DMSO_1.raw"]["comment[proteomics data acquisition method]"] == sdrf.DIA
    assert cells["DMSO_1.raw"]["assay name"] == "DMSO_1" and cells["DMSO_1.raw"]["source name"] == "DMSO_1"
    assert set(_col(header, rows, "characteristics[organism]")) == {"homo sapiens"}  # from the FASTA's OS=
    assert set(_col(header, rows, "comment[instrument]")) == {"Orbitrap Eclipse"}  # the lab's setting
    assert set(_col(header, rows, "comment[cleavage agent details]")) == {"NT=Trypsin;AC=MS:1001251"}
    mods = [r[j] for r in rows[:1] for j, h in enumerate(header) if h == "comment[modification parameters]"]
    assert mods == ["NT=Carbamidomethyl;AC=UNIMOD:4;TA=C;MT=fixed;PP=Anywhere",
                    "NT=Oxidation;AC=UNIMOD:35;TA=M;MT=variable;PP=Anywhere",
                    "NT=Acetyl;AC=UNIMOD:1;MT=variable;PP=Protein N-term",
                    "NT=Gln->pyro-Glu;AC=UNIMOD:28;TA=Q;MT=variable;PP=Any N-term",
                    "NT=Ammonia-loss;AC=UNIMOD:385;TA=C;MT=variable;PP=Any N-term"]
    assert set(_col(header, rows, "characteristics[disease]")) == {"not available"}
    info = out.summary["sdrf"]
    assert info["file"] == "results/sdrf.tsv" and info["rows"] == 7 and info["data_files"] == 7
    assert info["complete"] is True and info["fill_in"] == []
    assert info["derived"] == {"organism": "FASTA", "cleavage_agent": "fragpipe.workflow",
                               "modifications": "fragpipe.workflow", "instrument": "analysis.sdrf"}
    assert any("Drug_3" in n and "still listed" in n for n in info["notes"])
    assert json.loads((dest / "results/analysis.json").read_text(encoding="utf-8"))["sdrf"] == info
    assert dest / "results/sdrf.tsv" in out.files
    html = out.report.read_text(encoding="utf-8")
    assert "Sample metadata (SDRF)" in html and "href='sdrf.tsv'" in html and "Fill in" not in html


def test_unknown_metadata_is_not_available_and_named_for_the_person(tmp_path):
    dest, record = _dia(tmp_path)
    (dest / "fragpipe/fragpipe.workflow").unlink()
    record.pop("run")  # no workflow, no FASTA: nothing to derive from
    out = downstream.analyze(dest, "DIA", NO_ENRICH, record=record)
    header, rows = _check_structure(dest / "results/sdrf.tsv")
    assert set(_col(header, rows, "comment[instrument]")) == {"not available"}
    assert "comment[modification parameters]" not in header  # recommended, left out when unknown
    info = out.summary["sdrf"]
    assert info["complete"] is False
    assert info["fill_in"] == ["characteristics[organism]", "comment[instrument]", "comment[cleavage agent details]"]
    html = out.report.read_text(encoding="utf-8")
    assert "Fill in characteristics[organism], comment[instrument], comment[cleavage agent details]" in html


def test_experiment_metadata_overrides_the_lab_and_values_are_sanitised(tmp_path):
    dest, record = _dia(tmp_path)
    lab = {**NO_ENRICH, "sdrf": {"instrument": "Q Exactive", "organism": "mus musculus"}}
    exp = {"sdrf": {"organism": "Homo  sapiens", "cell type": "HEK\t293\nT", "disease": "Not Available",
                    "cleavage_agent": "LysC"},
           "sample_conditions": {"Drug_2": "Drug\tlow  dose"}}
    out = downstream.analyze(dest, "DIA", lab, exp, record=record)
    header, rows = _check_structure(dest / "results/sdrf.tsv")
    first = dict(zip(header, rows[0], strict=True))
    assert first["characteristics[organism]"] == "Homo sapiens"  # the experiment's value wins over the lab's
    assert first["comment[instrument]"] == "Q Exactive"  # the lab's value stays where the experiment is silent
    assert first["characteristics[cell type]"] == "HEK 293 T"  # tabs / newlines can't break the table
    assert first["characteristics[disease]"] == "not available"  # reserved words are lower-case
    assert first["comment[cleavage agent details]"] == "NT=Lys-C;AC=MS:1001309"  # a plain enzyme name gets its term
    drug2 = next(r for r in rows if r[header.index("comment[data file]")] == "Drug_2.raw")
    assert drug2[header.index("factor value[condition]")] == "Drug low dose"
    assert out.summary["sdrf"]["derived"]["organism"] == "analysis.sdrf"


@pytest.mark.parametrize("bad, msg", [({"sdrf": {"tissue": "liver"}}, "unknown sdrf field"),
                                      ({"sdrf": "Orbitrap"}, "must map a field")])
def test_sdrf_settings_are_validated(bad, msg):
    with pytest.raises(analysis.AnalysisError, match=msg):
        analysis.settings_from(bad)
    s = analysis.settings_from({"sdrf": {"instrument": "A", "organism": "x"}}, {"sdrf": {"Organism": "y"}})
    assert s.sdrf == {"instrument": "A", "organism": "y"}  # layers merge key by key


def test_no_manifest_uses_the_run_columns(tmp_path):
    dest = tmp_path / "e"
    runs = [(f"/data/{c}_{r}.raw", c) for c in ("DMSO", "Drug") for r in (1, 2)]
    simulate.dia_pg_matrix(dest / "fragpipe/report.pg_matrix.tsv", runs, seed=3)
    out = downstream.analyze(dest, "DIA", NO_ENRICH)
    header, rows = _check_structure(dest / "results/sdrf.tsv")
    assert _col(header, rows, "comment[data file]") == [f"{c}_{r}.raw" for c in ("DMSO", "Drug") for r in (1, 2)]
    assert _col(header, rows, "factor value[condition]") == ["DMSO", "DMSO", "Drug", "Drug"]
    assert out.summary["sdrf"]["rows"] == 4


# ------------------------------------------------------------------ isoDTB --


def test_isodtb_light_and_heavy_rows_share_the_run(tmp_path):
    dest = tmp_path / "20260902_EJQ_isoDTB_EJQ-2-027"
    simulate.isodtb_label_quant(dest / "fragpipe" / isodtb.LABEL_FILE, {"EJQ_2_027": [1, 2]}, seed=8)
    _workflow(dest / "fragpipe", extra="ionquant.light=C561.3387\nionquant.heavy=C567.3462\n"
              "msfragger.table.var-mods=15.9949,M,true,2; 561.3387,C,true,1; 567.3462,C,true,1; 57.02146,C,false,3",
              enzyme="trypsin")
    record = {"plan": {"manifest": [{"file": f"EJQ_2_027_{r}_{f}.raw", "experiment": "EJQ_2_027", "bioreplicate": r,
                                     "data_type": "DDA"} for r in (1, 2) for f in (1, 2, 3)]}}
    out = downstream.analyze(dest, "isoDTB", {**NO_ENRICH, "sdrf": {"organism": "homo sapiens",
                                                                    "instrument": "Orbitrap Eclipse"}}, record=record)
    header, rows = _check_structure(dest / "results/sdrf.tsv")
    assert len(rows) == 12  # 6 raw files x (light, heavy)
    first, second = (dict(zip(header, r, strict=True)) for r in rows[:2])
    assert first["comment[data file]"] == second["comment[data file]"] == "EJQ_2_027_1_1.raw"
    assert first["assay name"] == second["assay name"] == "EJQ_2_027_1_1"
    assert (first["comment[label]"], second["comment[label]"]) == ("ICAT light", "ICAT heavy")
    assert (first["source name"], second["source name"]) == ("EJQ_2_027_1_light", "EJQ_2_027_1_heavy")
    assert _col(header, rows, "comment[fraction identifier]") == [str(f) for _r in (1, 2) for f in (1, 2, 3)
                                                                  for _lab in (0, 1)]
    assert _col(header, rows, "characteristics[biological replicate]") == [str(r) for r in (1, 2) for _ in range(6)]
    assert set(_col(header, rows, "factor value[condition]")) == {"EJQ_2_027"}
    assert set(_col(header, rows, "comment[proteomics data acquisition method]")) == {sdrf.DDA}
    mods = [rows[0][j] for j, h in enumerate(header) if h == "comment[modification parameters]"]
    assert "NT=isoDTB light;TA=C;MT=variable;PP=Anywhere;MM=561.3387" in mods
    assert "NT=isoDTB heavy;TA=C;MT=variable;PP=Anywhere;MM=567.3462" in mods
    assert not any("57.02146" in x and "variable" in x for x in mods)  # disabled rows are not searched
    assert set(_col(header, rows, "comment[cleavage agent details]")) == {"NT=Trypsin;AC=MS:1001251"}
    assert out.summary["sdrf"]["complete"] is True


def test_isodtb_without_a_record_still_describes_the_samples(tmp_path):
    dest = tmp_path / "e"
    simulate.isodtb_label_quant(dest / "fragpipe" / isodtb.LABEL_FILE, {"EJQ_2_027": [1, 2, 3]}, seed=8)
    out = downstream.analyze(dest, "isoDTB", NO_ENRICH)
    header, rows = _check_structure(dest / "results/sdrf.tsv")
    assert len(rows) == 6 and set(_col(header, rows, "comment[data file]")) == {"not available"}
    assert "comment[data file]" in out.summary["sdrf"]["fill_in"]


# --------------------------------------------------------------------- TMT --


def _tmt(dest: Path, samples: list[str], manifest: list[dict], overrides: dict | None = None) -> dict:
    simulate.tmt_abundance(dest / "fragpipe/tmt-report/abundance_gene_MD.tsv", samples, seed=4)
    return {"plan": {"manifest": manifest, "raw_dir": "", "overrides": overrides or {}}}


def test_tmt_one_row_per_file_and_channel_from_annotation_txt(tmp_path):
    dest = tmp_path / "20260902_IJ_TMT_x"
    samples = ["DMSO_a", "DMSO_b", "Drug_a", "Drug_b"]
    manifest = [{"file": f"plex1_{f}.raw", "experiment": "plex1", "bioreplicate": 1, "data_type": "DDA"}
                for f in (1, 2)]
    # experiment.yaml's map says something else: annotation.txt is what FragPipe actually used
    record = _tmt(dest, samples, manifest, {"tmt": {"channels": {126: "wrong", "127N": "wrong2"}}})
    (dest / "annotation.txt").write_text("126 DMSO_a\n127N DMSO_b\n127C Drug_a\n128N Drug_b\n131 pool\n",
                                         encoding="utf-8")
    out = downstream.analyze(dest, "TMT", {**NO_ENRICH, **LAB}, record=record)
    header, rows = _check_structure(dest / "results/sdrf.tsv")
    assert len(rows) == 10  # 2 fractions x 5 channels
    assert _col(header, rows, "comment[label]")[:5] == ["TMT126", "TMT127N", "TMT127C", "TMT128N", "TMT131"]
    assert _col(header, rows, "comment[fraction identifier]") == ["1"] * 5 + ["2"] * 5
    assert set(_col(header, rows, "assay name")) == {"plex1_1", "plex1_2"}
    cells = _by(header, rows)
    pool = cells["pool|TMT131|plex1_1"]
    assert pool["characteristics[biological replicate]"] == "pooled" and pool["factor value[condition]"] == "pooled"
    assert cells["Drug_b|TMT128N|plex1_1"]["factor value[condition]"] == "Drug"
    assert cells["Drug_b|TMT128N|plex1_1"]["characteristics[biological replicate]"] == "2"
    assert "wrong" not in _col(header, rows, "source name")
    assert out.summary["sdrf"]["rows"] == 10


def test_tmt_plexes_from_experiment_yaml(tmp_path):
    dest = tmp_path / "e"
    samples = ["DMSO_1", "Drug_1", "DMSO_2", "Drug_2"]
    manifest = [{"file": f"{p}_{f}.raw", "experiment": p, "bioreplicate": 1, "data_type": "DDA"}
                for p in ("plexA", "plexB") for f in (1, 2)]
    record = _tmt(dest, samples, manifest, {"tmt": {"plexes": {
        "plexA": {"channels": {126: "DMSO_1", "127N": "Drug_1"}},
        "plexB": {"channels": {126: "DMSO_2", "127N": "Drug_2"}}}}})
    downstream.analyze(dest, "TMT", {**NO_ENRICH, **LAB}, {"exclude_samples": ["Drug_2"]}, record=record)
    header, rows = _check_structure(dest / "results/sdrf.tsv")
    cells = _by(header, rows)
    assert len(rows) == 8
    assert set(cells) >= {"DMSO_1|TMT126|plexA_1", "Drug_1|TMT127N|plexA_2", "DMSO_2|TMT126|plexB_1",
                          "Drug_2|TMT127N|plexB_2"}
    assert not any(k.startswith("DMSO_1|") and "plexB" in k for k in cells)  # a channel belongs to its own plex
    assert cells["Drug_2|TMT127N|plexB_1"]["factor value[condition]"] == "Drug"  # left out, still described
    assert [cells[f"{s}|TMT126|plex{p}_1"]["characteristics[biological replicate]"]
            for s, p in (("DMSO_1", "A"), ("DMSO_2", "B"))] == ["1", "2"]


def test_tmt_channels_from_the_lab_sample_names(tmp_path):
    dest = tmp_path / "e"
    samples = [f"DMSO_1_{c}" for c in ("126", "127N", "127C")] + [f"Drug_1_{c}" for c in ("128N", "128C", "129N")]
    simulate.tmt_abundance(dest / "fragpipe/tmt-report/abundance_gene_MD.tsv", samples, seed=4)
    (dest / "plex1.raw").write_bytes(b"raw")  # no record: the raw file in the folder is the data file
    downstream.analyze(dest, None, NO_ENRICH)
    header, rows = _check_structure(dest / "results/sdrf.tsv")
    assert _col(header, rows, "comment[label]") == ["TMT126", "TMT127N", "TMT127C", "TMT128N", "TMT128C", "TMT129N"]
    assert set(_col(header, rows, "comment[data file]")) == {"plex1.raw"}
    assert _col(header, rows, "characteristics[biological replicate]") == ["1", "2", "3", "1", "2", "3"]


# ------------------------------------------------------------ table / errors --


def test_a_table_alone_gets_no_sdrf(tmp_path):
    table = tmp_path / "proteins.tsv"
    genes = [f"G{i}" for i in range(80)]
    table.write_text("Gene\t" + "\t".join(f"{c}_{r}" for c in ("DMSO", "Drug") for r in (1, 2, 3)) + "\n" +
                     "".join(f"{g}\t" + "\t".join(str(1000 + 10 * i + j) for j in range(6)) + "\n"
                             for i, g in enumerate(genes)), encoding="utf-8")
    out = downstream.analyze(tmp_path / "proteins_ionomos", table=table, analysis_cfg=NO_ENRICH)
    assert out.method == "table"
    assert not (tmp_path / "proteins_ionomos/results/sdrf.tsv").exists()
    assert out.summary["sdrf"]["file"] is None and "raw files" in out.summary["sdrf"]["reason"]
    assert "Sample metadata (SDRF)" not in out.report.read_text(encoding="utf-8")


def test_a_failing_sdrf_step_never_breaks_the_report(tmp_path, monkeypatch):
    dest, record = _dia(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("sdrf exploded")

    monkeypatch.setattr(sdrf, "build", boom)
    out = downstream.analyze(dest, "DIA", NO_ENRICH, record=record)
    assert out.report is not None and out.report.is_file() and out.summary["comparisons"]
    assert out.summary["sdrf"]["file"] is None
    assert any("analysis step sdrf failed" in w for w in out.warnings)
    assert "stage sdrf" in (dest / "results/analysis_error.txt").read_text(encoding="utf-8")


# ------------------------------------------------------------------ pieces --


@pytest.mark.parametrize("name, value", [("stricttrypsin", "NT=Trypsin;AC=MS:1001251"),
                                         ("trypsin", "NT=Trypsin;AC=MS:1001251"),
                                         ("lysc", "NT=Lys-C;AC=MS:1001309"),
                                         ("nonspecific", "NT=unspecific cleavage;AC=MS:1001956"),
                                         ("gluc", "NT=glutamyl endopeptidase;AC=MS:1001917"),
                                         ("custom", None), ("null", None)])
def test_enzyme_names(name, value):
    got, notes = sdrf.enzyme({"msfragger.search_enzyme_name_1": name})
    assert got == value
    assert bool(notes) == (value is None and name != "null")


@pytest.mark.parametrize("fix, var, expect", [
    ("229.16293,K (lysine),true,-1; 229.16293,N-Term Peptide,true,-1", "",
     ["NT=TMT6plex;AC=UNIMOD:737;TA=K;MT=fixed;PP=Anywhere", "NT=TMT6plex;AC=UNIMOD:737;MT=fixed;PP=Any N-term"]),
    ("304.20715,K (lysine),true,-1", "", ["NT=TMTpro;AC=UNIMOD:2016;TA=K;MT=fixed;PP=Anywhere"]),
    ("", "79.96633,STY,true,3", ["NT=Phospho;AC=UNIMOD:21;TA=S,T,Y;MT=variable;PP=Anywhere"]),
    ("", "114.04293,K,true,2; 0.0,site_2,true,1", ["NT=GG;AC=UNIMOD:121;TA=K;MT=variable;PP=Anywhere"]),
    ("", "12.3456,W,true,1", ["NT=mass shift 12.3456;TA=W;MT=variable;PP=Anywhere;MM=12.3456"]),
])
def test_modifications_from_the_msfragger_tables(fix, var, expect):
    mods, notes = sdrf.modifications({"msfragger.table.fix-mods": fix, "msfragger.table.var-mods": var})
    assert mods == expect
    assert bool(notes) == any("MM=" in x for x in expect)


def test_fasta_organism_needs_one_clear_species(tmp_path):
    assert sdrf.fasta_organism(_fasta(tmp_path / "a.fas", {"Homo sapiens": 19, "Mus musculus": 1}))[0] == \
        "homo sapiens"
    org, note = sdrf.fasta_organism(_fasta(tmp_path / "b.fas", {"Homo sapiens": 10, "Mus musculus": 10}))
    assert org is None and "several species" in note
    assert sdrf.fasta_organism(tmp_path / "missing.fas") == (None, "")


def test_config_writer_keeps_the_lab_metadata(tmp_path):
    from ionomos import configio
    from ionomos.config import load

    d = configio.defaults(str(tmp_path / "Auto"), str(tmp_path / "General"))
    d["analysis"]["sdrf"] = {"instrument": "Orbitrap Eclipse", "organism": "homo sapiens"}
    p = configio.write_config(tmp_path / "config.yaml", d)
    assert configio.read_config(p)["analysis"]["sdrf"] == d["analysis"]["sdrf"]
    assert load(p, check_paths=False).analysis["sdrf"]["instrument"] == "Orbitrap Eclipse"
    d["analysis"].pop("sdrf")
    configio.write_config(p, d)  # nothing set: an empty entry, which loads as "no metadata"
    load(p, check_paths=False)


# -------------------------------------------------------- official validator --

needs_validator = pytest.mark.skipif(importlib.util.find_spec("sdrf_pipelines") is None,
                                     reason="sdrf-pipelines (the official SDRF validator) is not installed")


def _validate(path: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "sdrf_pipelines.parse_sdrf", "validate-sdrf", "--sdrf_file",
                           str(path), "--skip-ontology"], capture_output=True, text=True, timeout=300)


@needs_validator
def test_official_validator_accepts_every_method(tmp_path):
    full = {**NO_ENRICH, "sdrf": {"instrument": "Orbitrap Eclipse", "organism": "homo sapiens"}}
    dest, record = _dia(tmp_path / "dia")
    downstream.analyze(dest, "DIA", full, {"sample_conditions": {"Drug_1": "DMSO"}}, record=record)
    iso = tmp_path / "iso"
    simulate.isodtb_label_quant(iso / "fragpipe" / isodtb.LABEL_FILE, {"EJQ_2_027": [1, 2]}, seed=8)
    _workflow(iso / "fragpipe", extra="ionquant.light=C561.3387\nionquant.heavy=C567.3462")
    downstream.analyze(iso, "isoDTB", full, record={"plan": {"manifest": [
        {"file": f"EJQ_2_027_{r}_{f}.raw", "experiment": "EJQ_2_027", "bioreplicate": r} for r in (1, 2) for f in (1, 2)]}})
    tmt = tmp_path / "tmt"
    rec = _tmt(tmt, ["DMSO_a", "Drug_a"], [{"file": f"p_{f}.raw", "experiment": "p", "bioreplicate": 1}
                                         for f in (1, 2)])
    (tmt / "annotation.txt").write_text("126 DMSO_a\n127N Drug_a\n128N pool\n", encoding="utf-8")
    _workflow(tmt / "fragpipe")
    downstream.analyze(tmt, "TMT", full, record=rec)
    for d in (dest, iso, tmt):
        r = _validate(d / "results/sdrf.tsv")
        assert r.returncode == 0, (d.name, r.stdout + r.stderr)


@needs_validator
def test_official_validator_names_what_is_missing(tmp_path):
    dest, record = _dia(tmp_path)
    out = downstream.analyze(dest, "DIA", NO_ENRICH, record=record)
    r = _validate(dest / "results/sdrf.tsv")
    assert r.returncode != 0
    assert out.summary["sdrf"]["fill_in"] == ["comment[instrument]"]
    assert "comment[instrument]" in r.stdout + r.stderr
