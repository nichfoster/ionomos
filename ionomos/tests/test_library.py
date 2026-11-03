"""The workflow and FASTA library (library.py, D89, docs/LIBRARY.md).

A fake lab PC is built in tmp_path from real names in reference/pc-inventory/2026-09-15/system_report.txt: Aman's
TMT folder with fragpipe_pax8.workflow (under "012626_from Cdrive", a path with a space), Chris's DIA searches,
Kai's shotgun and open searches, Alex's offset search, a recycle bin, FragPipe 24.0's installation with stock
workflows and its window's settings cache, and the 22.0 zip in Downloads with the stock isoDTB-ABPP.workflow.
The workflows carry the keys of FragPipe 24.0's stock TMT10-MS3 / chemprot-ABPP-isoDTB / DIA_SpecLib_Quant /
Open / Mass-Offset-CommonPTMs workflows (checked on GitHub, docs/LIBRARY.md), abridged."""
import json
import os
import re
import threading
import time
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

from ionomos import cli, configio, library
from ionomos import help as helpdoc
from ionomos.config import ConfigError, load
from ionomos.library import Context

HUMAN = "2026-01-30-decoys-contam-UP000005640_9606.fasta.fas"   # the lab PC's FASTA (PROTEOMICS_PC.md)

BASE = {
    "database.decoy-tag": "rev_",
    "msfragger.run-msfragger": "true",
    "msfragger.search_enzyme_name_1": "stricttrypsin",
    "msfragger.search_enzyme_name_2": "null",
    "msfragger.allowed_missed_cleavage_1": "2",
    "msfragger.table.fix-mods": "0.0,C-Term Peptide,true,-1; 0.0,N-Term Peptide,true,-1; "
                                "57.02146,C (cysteine),true,-1; 0.0,K (lysine),true,-1",
    "msfragger.table.var-mods": "15.9949,M,true,3; 42.0106,[^,true,1; 79.96633,STY,false,3; 0.0,site_08,false,1",
    "msfragger.precursor_mass_lower": "-20",
    "msfragger.precursor_mass_upper": "20",
    "msfragger.precursor_mass_units": "1",
    "msfragger.precursor_mass_mode": "selected",
    "msfragger.mass_offsets": "0",
    "msfragger.mass_offsets_detailed": "",
    "msfragger.fragment_mass_tolerance": "20",
    "msfragger.fragment_mass_units": "1",
    "msfragger.misc.slice-db": "1",
    "percolator.run-percolator": "true",
    "peptide-prophet.run-peptide-prophet": "false",
    "phi-report.run-report": "true",
    "phi-report.filter": "--sequential --picked --prot 0.01",
    "quantitation.run-label-free-quant": "false",
    "ionquant.run-ionquant": "true",
    "ionquant.use-labeling": "false",
    "ionquant.mbr": "0",
    "tmtintegrator.run-tmtintegrator": "false",
    "tmtintegrator.channel_num": "TMT-6",
    "diann.run-dia-nn": "false",
    "speclibgen.run-speclibgen": "false",
    "diatracer.run-diatracer": "false",
    "workflow.misc.save-sdrf": "false",
    "workflow.saved-with-ver": "24.0-build27",
}
KIND_KEYS = {
    "TMT": {  # TMT10-MS3.workflow, FragPipe 24.0
        "msfragger.table.fix-mods": "0.0,C-Term Peptide,true,-1; 57.02146,C (cysteine),true,-1; "
                                    "229.16293,K (lysine),true,-1",
        "msfragger.table.var-mods": "15.9949,M,true,3; 42.0106,[^,true,1; 79.96633,STY,false,3; "
                                    "229.16293,n^,true,1; 229.16293,S,false,1",
        "msfragger.fragment_mass_tolerance": "0.6", "msfragger.fragment_mass_units": "0",
        "tmtintegrator.run-tmtintegrator": "true", "tmtintegrator.channel_num": "TMT-10",
        "tmtintegrator.add_Ref": "1", "tmtintegrator.ref_tag": "Bridge", "tmtintegrator.groupby": "-1",
        "tmtintegrator.prot_norm": "1", "tmtintegrator.min_purity": "0.5", "tmtintegrator.extraction_tool": "IonQuant",
    },
    "isoDTB": {  # chemprot-ABPP-isoDTB.workflow (MBR on, as the lab's SOP asks)
        "msfragger.search_enzyme_name_1": "trypsin",
        "msfragger.table.var-mods": "15.9949,M,true,2; 42.0106,[^,true,1; 561.3387,C,true,1; 567.3462,C,true,1",
        "quantitation.run-label-free-quant": "true", "ionquant.use-labeling": "true", "ionquant.mbr": "1",
        "ionquant.light": "C561.3387", "ionquant.heavy": "C567.3462", "phi-report.filter": "--sequential --prot 0.01",
    },
    "DIA": {  # DIA_SpecLib_Quant.workflow
        "diann.run-dia-nn": "true", "speclibgen.run-speclibgen": "true", "msfragger.allowed_missed_cleavage_1": "1",
        "phi-report.filter": "--picked --prot 0.01 --minPepLen 8",
    },
    "LFQ": {"quantitation.run-label-free-quant": "true", "ionquant.mbr": "1"},
    "phospho": {"quantitation.run-label-free-quant": "false",
                "msfragger.table.var-mods": "15.9949,M,true,3; 79.96633,STY,true,3"},
    "open": {  # Open.workflow
        "msfragger.precursor_mass_lower": "-150", "msfragger.precursor_mass_upper": "500",
        "msfragger.precursor_mass_units": "0", "msfragger.precursor_mass_mode": "corrected",
        "percolator.run-percolator": "false", "peptide-prophet.run-peptide-prophet": "true",
    },
    "offset": {  # Mass-Offset-CommonPTMs.workflow
        "msfragger.mass_offsets": "", "msfragger.precursor_mass_lower": "-10", "msfragger.precursor_mass_upper": "10",
        "msfragger.mass_offsets_detailed": "541.06110(aa\\=DKETSNRH_d\\=136.06232)",
    },
    "other": {},
}


def wf_text(kind: str, saved: str = "24.0-build27", db: str | Path | None = None, desc: str = "", **over) -> str:
    keys = dict(BASE)
    keys.update(KIND_KEYS[kind])
    keys["workflow.saved-with-ver"] = saved
    hyphen = {"workflow.misc.save_sdrf": "workflow.misc.save-sdrf", "msfragger.table.var_mods": "msfragger.table.var-mods"}
    keys.update({hyphen.get(k.replace("__", "."), k.replace("__", ".")): v for k, v in over.items()})
    lines = ["---", f"# Workflow: {kind}", ""]
    if desc:
        lines.append(f'workflow.description=<p style\\="margin-top\\: 0">{desc}</p>')
    for k, v in sorted(keys.items()):
        lines.append(f"{k}={v}")
    if db is not None:   # FragPipe escapes ':' in values
        lines.append("database.db-path=" + str(db).replace("\\", "/").replace(":", "\\:"))
    return "\n".join(lines) + "\n"


def write_wf(path: Path, kind: str, **kw) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(wf_text(kind, **kw), encoding="utf-8")
    return path


def make_fasta(path: Path, human: int = 12, yeast: int = 0, ecoli: int = 0, tr: int = 0, iso: int = 0,
               decoy: str | None = "rev_", contam: int = 2, decoy_share: float = 1.0, crlf: bool = False,
               trailing_newline: bool = True) -> Path:
    entries = []
    for i in range(human):
        acc = f"P{i:05d}" + ("-2" if i < iso else "")
        db = "tr" if i < tr else "sp"
        entries.append((f"{db}|{acc}|PROT{i}_HUMAN Protein {i} OS=Homo sapiens OX=9606 GN=G{i} PE=1 SV=1",
                        "MKVLAAGIVGLLLACSTNPKPQRKTKRNT" * 3))
    for i in range(yeast):
        entries.append((f"sp|Q{i:05d}|Y{i}_YEAST Yeast {i} OS=Saccharomyces cerevisiae (strain ATCC 204508 / S288c) "
                        f"OX=559292 GN=Y{i}", "MSTNPKPQRK" * 5))
    for i in range(ecoli):
        entries.append((f"sp|E{i:05d}|E{i}_ECOLI Coli {i} OS=Escherichia coli (strain K12) OX=83333 GN=E{i}",
                        "MKKLLPT" * 6))
    for i in range(contam):
        entries.append((f"contam_sp|P0276{i}|ALBU_BOVIN Albumin OS=Bos taurus OX=9913 GN=ALB", "MKWVTFISLLLLFSSAYS"))
    n_decoys = int(round(len(entries) * decoy_share)) if decoy else 0
    decoys = [(f"{decoy}{h}", s[::-1]) for h, s in entries[:n_decoys]]
    nl = "\r\n" if crlf else "\n"
    text = nl.join(f">{h}{nl}{s[:60]}{nl}{s[60:]}" if len(s) > 60 else f">{h}{nl}{s}"
                   for h, s in entries + decoys)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((text + (nl if trailing_newline else "")).encode())
    return path


def tsv(path: Path, header: list[str], rows: int = 2) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["\t".join(header)] + ["\t".join(f"{c}{r}" if i == 0 else str(10 + r) for i, c in enumerate(header))
                                   for r in range(rows)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """No real FragPipe of this computer, and no FASTA readings left over from another test."""
    monkeypatch.setattr(library, "INSTALL_GLOBS", ())
    monkeypatch.setattr(library, "_launchers", lambda: [])
    library._FASTA_CACHE.clear()
    yield
    library._FASTA_CACHE.clear()


@pytest.fixture
def pc(tmp_path):
    """The fake lab PC. Returns a dict of the paths, the Config and its file."""
    root = tmp_path / "pc"
    auto, general, d = root / "Fragpipe_Auto", root / "Fragpipe_General", root / "D"
    for f in (auto / "inbox", auto / "logs", general / "EJQ"):
        f.mkdir(parents=True)
    fasta = make_fasta(auto / "fasta" / HUMAN)
    write_wf(auto / "workflows" / "TMT10-MS3.workflow", "TMT", db=fasta)
    write_wf(auto / "workflows" / "isoDTB.workflow", "isoDTB", db=fasta)
    write_wf(auto / "workflows" / "DIA.workflow", "DIA", db=fasta)
    # FragPipe 24.0 as installed on the PC, with what install_report looks for
    fp = root / "C_FragPipe" / "FragPipe-24.0"
    for f in ("bin/fragpipe.bat", "bin/FragPipe-24.0.exe", "lib/fragpipe-24.0.jar", "jre/bin/java.exe",
              "tools/MSFragger-4.3/MSFragger-4.3.jar", "tools/IonQuant-1.11.11.jar", "tools/diann/2.3.2/DiaNN.exe",
              "tools/Philosopher/philosopher-v5.1.2", "python/python.exe", "tools/diaTracer-1.3.3.jar"):
        (fp / f).parent.mkdir(parents=True, exist_ok=True)
        (fp / f).write_text("x")
    (fp / "tools" / "MSFragger-4.3" / "ext" / "thermo").mkdir(parents=True)
    write_wf(fp / "tools" / "inside" / "not-a-stock.workflow", "TMT")       # program files: never scanned
    stock_tmt = write_wf(fp / "workflows" / "TMT10-MS3.workflow", "TMT", workflow__misc__save_sdrf="true",
                         desc="TMT 10-plex workflow, with quantification from MS3")
    write_wf(fp / "workflows" / "chemprot-ABPP-isoDTB.workflow", "isoDTB", ionquant__mbr="0",
             workflow__misc__save_sdrf="true")
    write_wf(fp / "workflows" / "DIA_SpecLib_Quant.workflow", "DIA", workflow__misc__save_sdrf="true")
    elsewhere = make_fasta(root / "Elsewhere" / "2025-01-10-decoys-contam-UP000005640_9606.fasta.fas", human=10)
    (fp / "cache").mkdir()
    (fp / "cache" / "fragpipe-ui.cache").write_text(
        "#ui state\ndatabase.db-path=" + str(elsewhere).replace("\\", "/").replace(":", "\\:")
        + "\nworkflow.workflow-option=TMT10-MS3\nworkflow.last-save-dir=D\\:/Aman\n", encoding="utf-8")
    # the 22.0 zip in Downloads
    zipfp = root / "Downloads" / "FragPipe-jre-22.0" / "fragpipe"
    (zipfp / "bin").mkdir(parents=True)
    (zipfp / "bin" / "fragpipe.bat").write_text("x")
    write_wf(zipfp / "workflows" / "isoDTB-ABPP.workflow", "isoDTB", saved="22.0", ionquant__mbr="0")
    # D:\<user>\ archive, real folder names from the 2026-09-15 inventory
    aman = d / "Aman" / "012626_from Cdrive" / "KL6159A_159B_9plex_TMT"
    aman_fasta = make_fasta(d / "Aman" / "fasta files" / "2025-09-01-decoys-contam-UP000005640_9606.fasta.fas",
                            human=11)
    pax8 = write_wf(aman / "fragpipe_pax8.workflow", "TMT", saved="23.1", db=aman_fasta,
                    tmtintegrator__groupby="0")
    (aman / "fragpipe.job").write_text("x")
    (aman / "log_2026-01-26_10-00-00.txt").write_text("FragPipe version 23.1\n...\nALL JOBS DONE IN 52.1 MINUTES\n")
    tsv(aman / "tmt-report" / "abundance_gene_MD.tsv", ["Index", "DMSO_1_126", "Drug_1_127N"])
    chris = d / "Chris" / "CS_22rv1_175-12c5uM_24h_DIA"
    for sub, db in (("Fragpipe-Results", fasta), ("wt_VS_KD", Path("D:/Chris/old_human.fasta"))):
        write_wf(chris / sub / "fragpipe.workflow", "DIA", db=db)
        tsv(chris / sub / "dia-quant-output" / "report.pg_matrix.tsv", ["Protein.Group", "a.raw", "b.raw"])
    (chris / "wt_VS_KD" / "dia-quant-output" / "report.pg_matrix.tsv").touch()
    os.utime(chris / "wt_VS_KD" / "dia-quant-output" / "report.pg_matrix.tsv", (1_788_000_000, 1_788_000_000))
    carolyn = d / "Kai"
    write_wf(carolyn / "EN3170-Shotgun" / "output" / "fragpipe.workflow", "LFQ", db=fasta)
    tsv(carolyn / "EN3170-Shotgun" / "output" / "combined_protein.tsv", ["Protein", "S1 Intensity", "S2 Intensity"])
    write_wf(carolyn / "SUMO-open" / "fragpipe.workflow", "open", db=fasta)
    write_wf(d / "Alex" / "QS_shotgunP3F_L1C14_100uM" / "Offset-search" / "fragpipe.workflow", "offset")
    write_wf(d / "EJQ" / "EJQ123_TMT" / "fragpipe.workflow", "TMT", db=Path("D:/EJQ/gone.fas"))
    write_wf(d / "$RECYCLE.BIN" / "S-1-5-21-4178110133" / "$R37VZLS" / "fragpipe.workflow", "TMT")
    make_fasta(d / "Melody" / "BSA_19A" / "mixed species.fasta", human=6, yeast=5, ecoli=3, decoy=None)
    cfg_dict = configio.defaults(str(auto), str(general))
    cfg_dict["paths"]["fragpipe_exe"] = str(fp / "bin" / "fragpipe.bat").replace("\\", "/")
    for m, wf in (("isoDTB", "isoDTB.workflow"), ("TMT", "TMT10-MS3.workflow"), ("DIA", "DIA.workflow")):
        cfg_dict["methods"][m]["workflow"] = wf
        cfg_dict["methods"][m]["fasta"] = HUMAN
    cfg_path = configio.write_config(auto / "config.yaml", cfg_dict, backup=False)
    cfg = load(cfg_path, check_paths=False)
    return {"root": root, "auto": auto, "d": d, "fp": fp, "zip": zipfp, "fasta": fasta, "elsewhere": elsewhere,
            "pax8": pax8, "aman_fasta": aman_fasta, "chris": chris, "stock_tmt": stock_tmt, "cfg": cfg,
            "cfg_path": cfg_path, "roots": [auto / "workflows", auto / "fasta", fp, zipfp.parent, d]}


def _scan(pc, **kw):
    return library.scan(pc["roots"], cfg=pc["cfg"], **kw)


# --------------------------------------------------------------- workflows --


@pytest.mark.parametrize("kind, want_kind, traits, settings, words", [
    ("TMT", "TMT", [], {"label type": "TMT-10", "fixed mods": "carbamidomethyl C, TMT K",
                        "variable mods": "oxidation M, acetyl protein N-term, TMT peptide N-term",
                        "fragment tolerance": "0.6 Da", "precursor tolerance": "±20 ppm",
                        "TMT reference": "a virtual reference (average of the channels)",
                        "TMT normalisation": "median centring (MD)", "TMT group by": "all levels",
                        "TMT min purity": "0.5", "FDR": "1 % protein FDR", "missed cleavages": "2"},
     ["A TMT-10 search with TMT-Integrator", "carbamidomethyl C, TMT K fixed", "stricttrypsin, 2 missed cleavages",
      "Saved with FragPipe 24.0"]),
    ("isoDTB", "isoDTB", [], {"MS1 quant": "on, labelled (light/heavy)", "match-between-runs": "on",
                                            "variable mods": "oxidation M, acetyl protein N-term, isoDTB light C, "
                                                             "isoDTB heavy C", "enzyme": "trypsin"},
     ["An isoDTB search", "MBR on"]),
    ("DIA", "DIA", [], {"DIA-NN": "on", "spectral library": "built from the search", "fragment tolerance": "20 ppm",
                        "PSM validation": "Percolator"},
     ["A DIA search with DIA-NN with a spectral library"]),
    ("LFQ", "LFQ", [], {"MS1 quant": "on", "match-between-runs": "on"}, ["A label-free DDA search", "MBR on"]),
    ("phospho", "phospho", ["phospho"], {"variable mods": "oxidation M, phospho STY"}, ["A phospho search"]),
    ("open", "open", ["open"], {"precursor tolerance": "-150 to 500 Da", "PSM validation": "PeptideProphet"},
     ["An open search (precursor window -150 to 500 Da)"]),
    ("offset", "open", ["mass-offset"], {"precursor tolerance": "±10 ppm"}, ["A mass-offset search"]),
    ("other", "other", [], {"MS1 quant": "off"}, ["A FragPipe search without quantification"]),
])
def test_describe_workflow_in_plain_words(tmp_path, kind, want_kind, traits, settings, words):
    p = write_wf(tmp_path / "w" / f"{kind}.workflow", kind)
    info = library.describe_workflow(p)
    assert info.kind == want_kind and info.traits == traits and not info.error
    for k, v in settings.items():
        assert info.settings[k] == v, k
    for w in words:
        assert w in info.summary, (w, info.summary)
    assert info.saved_with == "24.0-build27" and info.decoy_tag == "rev_"
    assert info.needs["MSFragger"] is True
    assert info.as_dict()["type"] == "workflow"
    assert library.WorkflowInfo.from_dict(json.loads(json.dumps(info.as_dict()))).summary == info.summary


# FragPipe 24.0's stock MS1-labelled workflows: ionquant.light / .heavy and the label mods as they ship (abridged)
LABELLED_STOCK = {
    "SILAC3": ("SILAC", {"ionquant__light": "K0;R0", "ionquant__medium": "K4.025107;R6.020129",
                         "ionquant__heavy": "K8.014199;R10.008269", "msfragger__table__var_mods":
                         "15.9949,M,true,2; 4.025107,K,true,3; 6.020129,R,true,3; 8.014199,K,true,3; "
                         "10.008269,R,true,3"}),
    "SILAC3-phospho": ("SILAC", {"ionquant__light": "K0;R0", "ionquant__heavy": "K8.014199;R10.008269",
                                 "msfragger__table__var_mods": "15.9949,M,true,2; 79.96633,STY,true,2; "
                                                               "8.014199,K,true,3; 10.008269,R,true,3"}),
    "chemprot-ABPP-ipIAA": ("ipIAA", {"ionquant__light": "C463.2366", "ionquant__heavy": "C467.2529",
                                      "msfragger__table__var_mods": "15.9949,M,true,3; 463.2366,C,true,3; "
                                                                    "467.2529,C,true,3; 57.02146,C,true,3"}),
    "chemprot-ABPP-isoTOP": ("isoTOP", {"ionquant__light": "C464.28596", "ionquant__heavy": "C470.29977",
                                        "msfragger__table__var_mods": "15.9949,M,true,2; 464.28595,C,true,2; "
                                                                      "470.29977,C,true,2"}),
    "chemprot-PAL": ("PAL", {"ionquant__light": "*436.2256", "ionquant__heavy": "*442.2633",
                             "msfragger__table__var_mods": "15.9949,M,true,2; 57.02146,C,true,3"}),
    "chemprot-ABPP-isoDTB": ("isoDTB", {}),
}


@pytest.mark.parametrize("name", sorted(LABELLED_STOCK))
def test_ms1_labelled_workflows_are_told_apart(tmp_path, name):
    """#148: SILAC, PAL, isoTOP and ipIAA use IonQuant's labelling like isoDTB but are not isoDTB."""
    kind, over = LABELLED_STOCK[name]
    info = library.describe_workflow(write_wf(tmp_path / f"{name}.workflow", "isoDTB", **over))
    assert info.kind == kind, info.summary
    assert ("isoDTB" in info.summary) is (kind == "isoDTB"), info.summary
    if kind == "SILAC":
        assert "SILAC" in info.summary and "cysteine" not in info.summary
    assert kind in library.KINDS


def test_stock_ties_are_never_broken_by_file_name(tmp_path):
    """#148: with a method's workflow missing, every stock workflow of the kind scores the same; the proposal is
    FragPipe's own workflow for the experiment (or the one the method names), never the first path among equals,
    and when the score can't tell them apart nothing is proposed and the notes say why."""
    inst = tmp_path / "FragPipe-24.0"
    (inst / "bin").mkdir(parents=True)
    (inst / "bin" / "fragpipe.bat").write_text("x")
    wfs = inst / "workflows"
    for name, (_kind, over) in LABELLED_STOCK.items():
        write_wf(wfs / f"{name}.workflow", "isoDTB", **over)
    for name, label in (("Nonspecific-HLA-TMT10", "TMT-10"), ("TMT10-MS3", "TMT-10"), ("TMT10", "TMT-10"),
                        ("TMT16", "TMT-16"), ("TMT10-acetyl", "TMT-10")):
        write_wf(wfs / f"{name}.workflow", "TMT", tmtintegrator__channel_num=label, msfragger__misc__tag=name)
    for name in ("DIA_DIA-Umpire_SpecLib_Quant", "DIA_SpecLib_Quant", "DIA_SpecLib_Quant_diaPASEF"):
        write_wf(wfs / f"{name}.workflow", "DIA", msfragger__misc__tag=name)
    ctx = Context(install_roots=[inst], installed_version="24.0")
    cat = library.Catalog(workflows=[library.describe_workflow(p, ctx) for p in sorted(wfs.glob("*.workflow"))])
    assert {w.source for w in cat.workflows} == {"stock"}
    library.judge_catalog(cat, ctx)
    empty = tmp_path / "Fragpipe_Auto" / "workflows"
    empty.mkdir(parents=True)
    d = {"paths": {"workflow_dir": str(empty), "fasta_dir": str(empty)},
         "methods": {"isoDTB": {"workflow": "isoDTB.workflow"}, "TMT": {"workflow": "TMT.workflow"},
                     "TMTpro": {"workflow": "TMT16.workflow"}, "DIA": {"workflow": "DIA.workflow"}},
         "naming": {"methods": {"TMTpro": {"like": "TMT"}}}}
    notes = []
    got = {(p.method, p.what): p.candidate.info.name for p in library.auto_assign(d, cat, "24.0", notes)}
    assert got[("isoDTB", "workflow")] == "chemprot-ABPP-isoDTB.workflow"
    assert got[("TMT", "workflow")] == "TMT10-MS3.workflow"
    assert got[("TMTpro", "workflow")] == "TMT16.workflow"          # the name the method asks for
    assert got[("DIA", "workflow")] == "DIA_SpecLib_Quant.workflow"
    best = library.recommend(cat, "TMT", "24.0")[0]
    assert any("FragPipe's own TMT10-MS3" in r for r in best.reasons)
    # no canonical one: the TMT ones that are left tie -> nothing proposed, and the note says why
    for name in ("TMT10-MS3", "TMT10", "TMT16"):
        cat.workflows = [w for w in cat.workflows if w.name != f"{name}.workflow"]
    notes = []
    props = library.auto_assign(d, cat, "24.0", notes)
    assert ("TMT", "workflow") not in {(p.method, p.what) for p in props}
    note = next(n for n in notes if n.startswith("TMT: "))
    assert "fit equally well" in note and "Nonspecific-HLA-TMT10.workflow" in note and "adopt" in note


def test_words_are_grammatical(tmp_path):
    """#156 item 1: 'An iTRAQ-4 search', '1 protein', '1 contaminant'."""
    itraq = library.describe_workflow(write_wf(tmp_path / "iTRAQ4.workflow", "TMT", tmtintegrator__channel_num="iTRAQ-4"))
    assert itraq.summary.startswith("An iTRAQ-4 search with TMT-Integrator"), itraq.summary
    one = library.describe_fasta(make_fasta(tmp_path / "one.fasta", human=1, contam=1))
    assert "(1 protein)" in one.summary and "1 contaminant" in one.summary and "contaminants" not in one.summary


def test_the_organism_reason_says_where_it_came_from(pc):
    """#156 item 2: the organism was inferred (the FASTA folder's files), nobody asked for it."""
    cat = _scan(pc)
    d = configio.read_config(pc["cfg_path"])
    d["methods"]["DIA"]["fasta"] = "gone.fas"
    prop = next(p for p in library.auto_assign(d, cat, "24.0") if p.what == "fasta")
    assert "Human, the organism of the FASTA folder's files" in prop.candidate.reasons
    assert not any("as asked" in r for r in prop.candidate.reasons)


def test_list_with_an_unknown_kind_says_so(pc, capsys):
    """#156 item 3: `library list --kind foo` printed the header and nothing else, exit 0."""
    library.save_catalog(pc["cfg"], _scan(pc))
    assert cli.main(["--config", str(pc["cfg_path"]), "library", "list", "--kind", "foo"]) == 2
    err = capsys.readouterr().err
    assert "no workflows of kind foo" in err and "kinds: isoDTB, TMT" in err and "found: isoDTB" in err
    assert cli.main(["--config", str(pc["cfg_path"]), "library", "list", "--kind", "PAL"]) == 0
    assert "no workflows of kind PAL in the last scan" in capsys.readouterr().err


def test_identical_copies_are_represented_by_the_newest_fragpipe_and_fragpipes_name(tmp_path):
    """#156 item 4: the same settings saved by 22.0 (a newer file: the zip unpacked after the install) and by 24.0
    are represented by the 24.0 one; identical FASTAs by the one with FragPipe's name, not dup-human.fas."""
    inst = tmp_path / "FragPipe-24.0"
    (inst / "bin").mkdir(parents=True)
    (inst / "bin" / "fragpipe.bat").write_text("x")
    old = tmp_path / "Downloads" / "FragPipe-jre-22.0" / "fragpipe"
    (old / "bin").mkdir(parents=True)
    (old / "bin" / "fragpipe.bat").write_text("x")
    new_wf = write_wf(inst / "workflows" / "chemprot-ABPP-isoDTB.workflow", "isoDTB")
    old_wf = write_wf(old / "workflows" / "isoDTB-ABPP.workflow", "isoDTB", saved="22.0")
    os.utime(new_wf, (1_700_000_000, 1_700_000_000))
    ctx = Context(install_roots=[inst, old], installed_version="24.0")
    cat = library.Catalog(workflows=[library.describe_workflow(p, ctx) for p in (old_wf, new_wf)])
    best = library.recommend(cat, "isoDTB", "24.0")[0]
    assert best.copies == 2 and Path(best.info.path) == new_wf
    assert "saved with the installed FragPipe 24.0" in best.reasons
    good = make_fasta(tmp_path / "fasta" / HUMAN)
    dup = tmp_path / "fasta" / "dup-human.fas"
    dup.write_bytes(good.read_bytes())
    os.utime(good, (1_700_000_000, 1_700_000_000))
    fcat = library.Catalog(fastas=[library.describe_fasta(p) for p in (dup, good)])
    assert Path(library.recommend_fastas(fcat, "9606")[0].info.path) == good


def test_a_fasta_without_line_breaks_is_read_in_linear_time(tmp_path, monkeypatch):
    """#156 item 5: a file with no line break kept the whole buffer as carry, so each chunk copied all before it
    (a 1 GB zero-filled .fasta took 60 s). Now the carry is at most one header line."""
    monkeypatch.setattr(library, "CHUNK", 4096)
    zeros = tmp_path / "sparse.fasta"
    with open(zeros, "wb") as fh:
        fh.truncate(16 << 20)
    t0 = time.monotonic()
    info = library.describe_fasta(zeros)
    assert time.monotonic() - t0 < 5 and info.entries == 0 and info.error
    # a header longer than MAX_HEADER is cut, and the entries after it are still found
    monkeypatch.setattr(library, "MAX_HEADER", 1000)
    long = tmp_path / "long.fasta"
    long.write_bytes(b">" + b"A" * 50_000 + b"\nMKV\n>sp|P1|X_HUMAN OX=9606\nMKV\n")
    with open(long, "rb") as fh:
        got = list(library._headers(fh, None, None))
    assert len(got) == 2 and len(got[0]) == 1000 and got[1] == b"sp|P1|X_HUMAN OX=9606"


def test_tmt_phospho_stays_tmt_and_says_phospho(tmp_path):
    p = write_wf(tmp_path / "TMT10-phospho.workflow", "TMT",
                 msfragger__table__var_mods="15.9949,M,true,3; 79.96633,STY,true,3; 229.16293,n^,true,1")
    info = library.describe_workflow(p)
    assert (info.kind, info.traits) == ("TMT", ["phospho"])
    assert "phospho (STY) variable" in info.summary


def test_description_html_and_escapes_are_cleaned_and_database_is_read(tmp_path):
    fa = make_fasta(tmp_path / "f" / HUMAN)
    p = write_wf(tmp_path / "x.workflow", "TMT", db=fa, desc="TMT 10-plex workflow, with quantification from MS3")
    info = library.describe_workflow(p)
    assert info.description == "TMT 10-plex workflow, with quantification from MS3"
    assert Path(info.database) == fa and info.database_exists
    assert "A TMT-10 MS3 search" in info.summary and f"made for {HUMAN}" in info.summary


def test_content_hash_groups_the_same_settings_whatever_the_name_fasta_or_version(tmp_path):
    a = library.describe_workflow(write_wf(tmp_path / "a.workflow", "DIA", db="C:/x/a.fas"))
    b = library.describe_workflow(write_wf(tmp_path / "sub" / "fragpipe.workflow", "DIA", db="D:/y b/b.fas",
                                           saved="23.1", desc="another text"))
    c = library.describe_workflow(write_wf(tmp_path / "c.workflow", "DIA", ionquant__mbr="1"))
    assert a.content_hash == b.content_hash != c.content_hash


@pytest.mark.parametrize("extra", [
    r"workdir=C\:\\Users\\lab\\Fragpipe_General\\EJQ\\x\\fragpipe",      # what FragPipe writes in a run's copy
    "workdir=/Users/lab/Fragpipe_General/EJQ/x/fragpipe",
    r"fragpipe-config.bin-diann=C\:/FragPipe/FragPipe-24.0/tools/diann/2.3.2/DiaNN.exe",
    r"fragpipe-config.tools-folder=C\:/FragPipe/FragPipe-24.0/tools",
    r"speclibgen.easypqp.extras.rt_lib=D\:/libs/irt.tsv",                     # any absolute path as a value
    "workflow.ram=64\nworkflow.threads=31",
])
def test_content_hash_leaves_out_a_runs_paths(tmp_path, extra):
    pinned = write_wf(tmp_path / "TMT10-MS3.workflow", "TMT", db="C:/Fragpipe_Auto/fasta/h.fas")
    run = tmp_path / "x" / "fragpipe" / "fragpipe.workflow"
    run.parent.mkdir(parents=True)
    run.write_text(pinned.read_text(encoding="utf-8") + extra + "\n", encoding="utf-8")
    assert library.describe_workflow(run).content_hash == library.describe_workflow(pinned).content_hash
    other = tmp_path / "other.workflow"
    other.write_text(pinned.read_text(encoding="utf-8") + "tmtintegrator.min_purity=0.7\n", encoding="utf-8")
    assert library.describe_workflow(other).content_hash != library.describe_workflow(pinned).content_hash


@pytest.mark.parametrize("kind, kw, ctx, code, severity", [
    ("TMT", {"tmtintegrator__channel_num": ""}, Context(), "WF_LABEL_TYPE_UNSET", "error"),
    ("TMT", {"workflow__misc__save_sdrf": "true"}, Context(), "WF_SDRF_ON", "warn"),
    ("DIA", {"db": "Z:/nowhere/gone.fas"}, Context(), "WF_DATABASE_MISSING", "warn"),
    ("DIA", {"saved": "22.0"}, Context(installed_version="24.0"), "WF_OLDER_FRAGPIPE", "warn"),
    ("DIA", {"saved": "25.1-build3"}, Context(installed_version="24.0"), "WF_NEWER_FRAGPIPE", "warn"),
    ("DIA", {}, Context(tools={"MSFragger": True, "IonQuant": True, "diaTracer": True, "DIA-NN": False,
                               "Python": True}), "WF_TOOL_MISSING", "error"),
])
def test_workflow_flags(tmp_path, kind, kw, ctx, code, severity):
    info = library.describe_workflow(write_wf(tmp_path / "w.workflow", kind, **kw), ctx)
    got = {f.code: f for f in info.flags}
    assert code in got and got[code].severity == severity
    assert got[code].fix and got[code].subject == info.path
    if code == "WF_TOOL_MISSING":
        assert "DIA-NN" in got[code].text
    same = library.describe_workflow(write_wf(tmp_path / "ok.workflow", "isoDTB"), Context(installed_version="24.0"))
    assert [f.code for f in same.flags] == []


def test_space_unreadable_and_elsewhere_flags(tmp_path):
    fa = make_fasta(tmp_path / "far" / "h.fas")
    info = library.describe_workflow(write_wf(tmp_path / "with space" / "w.workflow", "LFQ", db=fa),
                                     Context(fasta_dir=tmp_path / "fasta"))
    assert {"WF_SPACE_IN_PATH", "WF_DATABASE_ELSEWHERE"} <= {f.code for f in info.flags}
    junk = tmp_path / "Automator.workflow"
    junk.write_text("<plist>not FragPipe</plist>")
    bad = library.describe_workflow(junk)
    assert bad.error and [f.code for f in bad.flags] == ["WF_UNREADABLE"]
    gone = library.describe_workflow(tmp_path / "missing.workflow")
    assert gone.error and gone.flags[0].code == "WF_UNREADABLE"


# ------------------------------------------------------------------ FASTAs --


def test_describe_fasta_counts_and_summary(tmp_path):
    p = make_fasta(tmp_path / HUMAN, human=20, iso=3, contam=4)
    info = library.describe_fasta(p, Context(today=date(2026, 10, 14)))
    assert (info.entries, info.targets, info.decoys, info.contaminants) == (48, 24, 24, 4)
    assert (info.decoy_tag, info.reviewed, info.unreviewed, info.isoforms) == ("rev_", 20, 0, 3)
    assert (info.proteome, info.taxon, info.name_date, info.fragpipe_style) == ("UP000005640", "9606", "2026-01-30",
                                                                                 True)
    assert info.top_species == "Human" and info.top_share == 1.0 and info.proteins == 20
    assert info.summary == ("Human, reviewed UniProt (20 proteins, 3 isoforms) with decoys (rev_) and 4 contaminants, "
                            "made 2026-01-30 (proteome UP000005640).")
    assert info.flags == [] and info.fingerprint.startswith(f"{p.stat().st_size}-48-")


@pytest.mark.parametrize("kw, name, code, severity", [
    ({"decoy": None}, "h.fasta", "FASTA_NO_DECOYS", "error"),
    ({"decoy_share": 0.3}, "h.fasta", "FASTA_DECOY_SHARE", "error"),
    ({"contam": 0}, "h.fasta", "FASTA_NO_CONTAMINANTS", "warn"),
    ({"human": 5, "yeast": 4, "ecoli": 2}, "h.fasta", "FASTA_MIXED_SPECIES", "warn"),
    ({"tr": 10}, "h.fasta", "FASTA_UNREVIEWED", "warn"),
    ({}, "2024-05-01-decoys-contam-UP000005640_9606.fasta.fas", "FASTA_OLD", "warn"),
    ({}, "with space.fasta", "FASTA_SPACE_IN_PATH", "warn"),
    ({"decoy": "DECOY_"}, "h.fasta", "FASTA_DECOY_TAG", "info"),
])
def test_fasta_flags(tmp_path, kw, name, code, severity):
    info = library.describe_fasta(make_fasta(tmp_path / name, **kw), Context(today=date(2026, 10, 14)))
    got = {f.code: f for f in info.flags}
    assert code in got and got[code].severity == severity and got[code].fix, info.flags


def test_decoy_tag_is_detected_not_assumed(tmp_path):
    for tag in library.DECOY_TAGS:
        info = library.describe_fasta(make_fasta(tmp_path / f"{tag}.fasta", decoy=tag))
        assert info.decoy_tag == tag and info.decoys == info.targets


def test_mixed_species_summary(tmp_path):
    info = library.describe_fasta(make_fasta(tmp_path / "m.fasta", human=6, yeast=3, ecoli=1, contam=0))
    assert info.summary.startswith("Mixed: Human 60 % / Yeast 30 % / E. coli 10 %, reviewed UniProt")
    assert [s[0] for s in info.species] == ["9606", "559292", "83333"]


def gencode_fasta(path: Path, targets: int = 60, bovine: int = 6) -> Path:
    """Like FragPipe 24.0's own 2024-08-05-decoys-Gencode_v42...FragPipe.2.fasta.fas: GENCODE / Ensembl and smORF
    targets without OX=, a few UniProt-style bovine contaminants that are not prefixed contam_, rev_ decoys."""
    entries = []
    for i in range(targets):
        if i % 3:
            entries.append(f"ENSP{i:011d}.5|ENST{i:011d}.6|ENSG{i:011d}.12|ARF{i}-201|ARF{i}|GN=ARF{i}|")
        else:
            entries.append(f"A1BG_19:58858248-5885838{i}:-|GN=A1BG|smORF_v4|")
    for i in range(bovine):
        db = "sp" if i % 2 else "tr"
        entries.append(f"{db}|A0JN6{i}|CE12{i}_BOVIN Centrosomal OS=Bos taurus OX=9913 GN=C{i} PE=1 SV=1")
    lines = [f">{h}\nMKVLAAGIVGLLLACSTNPK" for h in entries] + [f">rev_{h}\nKPNTSCALLLGVIGAALVKM" for h in entries]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_organism_is_not_named_from_a_few_headers(tmp_path):
    """#151: the organism comes from the targets, and only when most of them name one: FragPipe's human GENCODE
    FASTA is not 'Cow, 59 % unreviewed UniProt' because of its contaminants' OX=9913."""
    p = gencode_fasta(tmp_path / "2024-08-05-decoys-Gencode_v42.basicPCnr.602contams.FragPipe.2.fasta.fas")
    info = library.describe_fasta(p, Context(today=date(2026, 10, 14)))
    assert (info.targets, info.decoys) == (66, 66)
    assert info.top_species == "" and info.taxon == "" and info.ox_share < 0.5
    assert "Cow" not in info.summary and info.summary.startswith("Organism unknown")
    assert "mostly not UniProt-style headers (6 UniProt-style)" in info.summary
    codes = {f.code for f in info.flags}
    assert not codes & {"FASTA_UNREVIEWED", "FASTA_MIXED_SPECIES"}, info.flags
    cat = library.Catalog(fastas=[info])
    cand = library.recommend_fastas(cat, taxon="9606")[0]
    assert not any("another organism" in r for r in cand.reasons) and any("can't be read" in r for r in cand.reasons)
    # a UniProt FASTA with a few headers lacking OX= is still named
    named = library.describe_fasta(make_fasta(tmp_path / "h.fasta", human=12, contam=2))
    assert named.top_species == "Human" and named.ox_share == 1.0
    # an older reading (before the fix) in a catalog is read again, not lent
    old = library.FastaInfo.from_dict({**info.as_dict(), "reader": 0, "top_species": "Cow"})
    library._FASTA_CACHE.clear()
    cat = library.scan([tmp_path], previous=library.Catalog(fastas=[old]))
    assert cat.find(p).top_species == ""


@pytest.mark.parametrize("crlf, trailing", [(False, True), (True, True), (False, False), (True, False)])
@pytest.mark.parametrize("chunk", [1, 2, 3, 7, 64, 1 << 20])
def test_headers_survive_any_chunk_boundary(tmp_path, monkeypatch, chunk, crlf, trailing):
    p = make_fasta(tmp_path / "f.fasta", human=9, yeast=2, contam=2, crlf=crlf, trailing_newline=trailing)
    want = [ln[1:].rstrip("\r") for ln in p.read_text().splitlines() if ln.startswith(">")]
    monkeypatch.setattr(library, "CHUNK", chunk)
    with open(p, "rb") as fh:
        got = [h.decode() for h in library._headers(fh, None, None)]
    assert got == want


def test_fasta_is_read_once_per_version(tmp_path, monkeypatch):
    p = make_fasta(tmp_path / "f.fasta")
    first = library.describe_fasta(p)

    def boom(*a, **k):
        raise AssertionError("read again")

    monkeypatch.setattr(library, "_headers", boom)
    assert library.describe_fasta(p).entries == first.entries   # cached on (path, mtime, size)
    monkeypatch.undo()
    monkeypatch.setattr(library, "INSTALL_GLOBS", ())
    make_fasta(p, human=30)
    assert library.describe_fasta(p).entries != first.entries   # a new version is read


def test_a_fasta_read_is_stopped_and_says_so(tmp_path):
    stop = threading.Event()
    stop.set()
    info = library.describe_fasta(make_fasta(tmp_path / "f.fasta"), stop=stop)
    assert not info.complete and "only part of the file was read" in info.summary
    assert not library._FASTA_CACHE       # a partial reading is never cached


def test_not_a_fasta(tmp_path):
    p = tmp_path / "notes.fa"
    p.write_text("just some text\n")
    info = library.describe_fasta(p)
    assert info.error and [f.code for f in info.flags] == ["FASTA_UNREADABLE"]


# -------------------------------------------------------------------- scan --


def test_scan_the_fake_pc(pc):
    seen = []
    cat = _scan(pc, progress=seen.append)
    assert cat.complete and not cat.stopped and seen and "workflow" in " ".join(seen)
    by = {Path(w.path).relative_to(pc["root"]).as_posix(): w for w in cat.workflows}
    assert not any("RECYCLE" in k for k in by), "the recycle bin is skipped"
    assert not any("/tools/" in k for k in by), "FragPipe's program folders are skipped"
    assert any("$RECYCLE.BIN" in s for s in cat.skipped) and any("tools" in s for s in cat.skipped)
    src = {k: w.source for k, w in by.items()}
    assert src["Fragpipe_Auto/workflows/TMT10-MS3.workflow"] == "pinned"
    assert src["C_FragPipe/FragPipe-24.0/workflows/TMT10-MS3.workflow"] == "stock"
    assert src["Downloads/FragPipe-jre-22.0/fragpipe/workflows/isoDTB-ABPP.workflow"] == "stock"
    pax8 = by["D/Aman/012626_from Cdrive/KL6159A_159B_9plex_TMT/fragpipe_pax8.workflow"]
    assert pax8.source == "run" and pax8.kind == "TMT"
    assert pax8.runs["tables"] == ["TMT"] and pax8.runs["finished"] and pax8.runs["all_done"]
    assert {"WF_SPACE_IN_PATH", "WF_OLDER_FRAGPIPE", "WF_DATABASE_ELSEWHERE", "WF_DIFFERS_FROM_PINNED"} <= \
        {f.code for f in pax8.flags}
    differs = next(f for f in pax8.flags if f.code == "WF_DIFFERS_FROM_PINNED")
    assert "TMT group by: gene (pinned: all levels)" in differs.text
    ejq = by["D/EJQ/EJQ123_TMT/fragpipe.workflow"]
    assert {"WF_DATABASE_MISSING", "WF_NO_RUNS"} <= {f.code for f in ejq.flags}
    assert by["D/Kai/SUMO-open/fragpipe.workflow"].kind == "open"
    assert by["D/Kai/EN3170-Shotgun/output/fragpipe.workflow"].runs["tables"] == ["LFQ"]
    assert by["D/Alex/QS_shotgunP3F_L1C14_100uM/Offset-search/fragpipe.workflow"].traits == ["mass-offset"]
    stock = by["C_FragPipe/FragPipe-24.0/workflows/TMT10-MS3.workflow"]
    assert "WF_SDRF_ON" in {f.code for f in stock.flags} and "WF_NO_RUNS" in {f.code for f in stock.flags}
    assert not any(f.code == "WF_TOOL_MISSING" for w in cat.workflows for f in w.flags), "every tool is installed"
    # FASTAs
    fas = {Path(f.path).name: f for f in cat.fastas}
    assert fas[HUMAN].source == "pinned" and fas[HUMAN].flags == [] or \
        {f.code for f in fas[HUMAN].flags} <= {"FASTA_OLD"}
    assert {"FASTA_NO_DECOYS", "FASTA_MIXED_SPECIES", "FASTA_SPACE_IN_PATH"} <= \
        {f.code for f in fas["mixed species.fasta"].flags}
    # FragPipe's window remembered a FASTA outside every root: described as a hint
    assert cat.hints and cat.hints[0]["workflow"] == "TMT10-MS3" and Path(cat.hints[0]["database"]) == pc["elsewhere"]
    assert fas[pc["elsewhere"].name].source == "cache"


def test_scan_groups_identical_settings_and_marks_duplicate_fastas(pc):
    copy = pc["d"] / "Isaac" / "IJD05" / HUMAN
    copy.parent.mkdir(parents=True)
    copy.write_bytes(pc["fasta"].read_bytes())
    cat = _scan(pc)
    chris = [w for w in cat.workflows if "CS_22rv1" in w.path]
    assert len(chris) == 2 and len({w.content_hash for w in chris}) == 1
    dup = [f for f in cat.fastas if Path(f.path).name == HUMAN]
    assert len(dup) == 2 and all("FASTA_DUPLICATE" in {x.code for x in f.flags} for f in dup)


def _searched(pc, name: str = "20260126_Aman_TMT_KL6159A") -> Path:
    """An experiment Ionomos searched with the pinned TMT workflow, as the worker leaves it: ionomos_run/ with its
    copy (only database.db-path differs), fragpipe/ with FragPipe's own fragpipe.workflow (+ workdir, threads,
    RAM), a group folder with psm.tsv and Philosopher's protein.fas (no decoys), the TMT-Integrator table."""
    exp = pc["root"] / "Fragpipe_General" / "Aman" / name
    pinned = (pc["auto"] / "workflows" / "TMT10-MS3.workflow").read_text(encoding="utf-8")
    run_dir = exp / "ionomos_run"
    run_dir.mkdir(parents=True)
    other_db = "database.db-path=" + str(pc["fasta"]).replace("\\", "/").replace(":", "\\:")
    (run_dir / "TMT10-MS3.workflow").write_text(re.sub(r"(?m)^database\.db-path=.*$", lambda _m: other_db,
                                                       pinned), encoding="utf-8")
    (run_dir / "fragpipe-files.fp-manifest").write_text("x")
    wd = exp / "fragpipe"
    wd.mkdir()
    workdir = str(wd).replace("\\", "\\\\").replace(":", "\\:")   # as FragPipe escapes it
    (wd / "fragpipe.workflow").write_text(pinned + f"workdir={workdir}\nworkflow.ram=4\nworkflow.threads=4\n",
                                          encoding="utf-8")
    (wd / "fragpipe.job").write_text("x")
    (wd / "log_2026-01-26_10-00-00.txt").write_text("ALL JOBS DONE IN 5.0 MINUTES\n")
    tsv(wd / "tmt-report" / "abundance_gene_MD.tsv", ["Index", "DMSO_1_126", "Drug_1_127N"])
    tsv(wd / "KL6159A_1" / "psm.tsv", ["Spectrum", "Peptide", "Protein"])
    make_fasta(wd / "KL6159A_1" / "protein.fas", human=8, decoy=None, contam=0)
    return exp


def test_a_search_by_ionomos_is_evidence_for_the_pinned_workflow(pc, capsys):
    exp = _searched(pc)
    db = make_fasta(pc["root"] / "Fragpipe_General" / "Aman" / "databases" / "protein.fas")   # a real one
    cat = library.scan([pc["auto"] / "workflows", pc["root"] / "Fragpipe_General"], cfg=pc["cfg"])
    by = {Path(w.path).relative_to(pc["root"]).as_posix(): w for w in cat.workflows}
    assert not any("ionomos_run" in k for k in by), "Ionomos' own run folders are skipped"
    assert any(s.startswith(str(exp / "ionomos_run")) for s in cat.skipped)
    pinned = by["Fragpipe_Auto/workflows/TMT10-MS3.workflow"]
    run = by[f"Fragpipe_General/Aman/{exp.name}/fragpipe/fragpipe.workflow"]
    assert run.source == "run" and run.finished and run.content_hash == pinned.content_hash
    tmt = [w for w in cat.workflows if w.kind == "TMT"]
    assert len(tmt) == 2 and len(library._groups(tmt)) == 1, "one set of settings, not two rivals"
    assert "WF_NO_RUNS" not in {f.code for f in pinned.flags}
    assert "WF_DIFFERS_FROM_PINNED" not in {f.code for f in run.flags}
    best = library.recommend(cat, "TMT", "24.0", pinned)[0]
    assert best.info is pinned and best.copies == 2 and "used in 1 finished search(es)" in best.reasons[0]
    assert library.used_in(cat, pinned) == f"used in 1 finished search(es), the latest {run.runs['when']}"
    # FragPipe's per-search protein list is not a database; a protein.fas elsewhere still is
    fas = {Path(f.path) for f in cat.fastas}
    assert exp / "fragpipe" / "KL6159A_1" / "protein.fas" not in fas and db in fas
    assert any(s.startswith(str(exp / "fragpipe" / "KL6159A_1" / "protein.fas")) and "not a search database" in s
               for s in cat.skipped)
    # list and show say what the pinned workflow has been used for
    library.save_catalog(pc["cfg"], cat)
    cfg = str(pc["cfg_path"])
    assert cli.main(["--config", cfg, "library", "list", "workflows", "--kind", "TMT"]) == 0
    out = capsys.readouterr().out
    assert out.count("used in 1 finished search(es)") == 1
    assert cli.main(["--config", cfg, "library", "show", pinned.path]) == 0
    assert "used in 1 finished search(es)" in capsys.readouterr().out


def test_protein_fas_under_a_fragpipe_workdir_is_skipped(tmp_path):
    wd = tmp_path / "exp" / "fragpipe"
    write_wf(wd / "fragpipe.workflow", "LFQ")
    make_fasta(wd / "S1" / "protein.fas", decoy=None)            # psm.tsv not (yet) written: the workdir says it
    make_fasta(tmp_path / "lab" / "protein.fas")                  # someone's database that happens to be named so
    cat = library.scan([tmp_path])
    assert [Path(f.path) for f in cat.fastas] == [tmp_path / "lab" / "protein.fas"]
    assert any("S1" in s and "not a search database" in s for s in cat.skipped)


def test_scan_depth_limit(pc):
    shallow = library.scan([pc["d"]], cfg=pc["cfg"], max_depth=1)
    assert not any("CS_22rv1" in w.path for w in shallow.workflows)
    deep = library.scan([pc["d"]], cfg=pc["cfg"], max_depth=6)
    assert sum("CS_22rv1" in w.path for w in deep.workflows) == 2


def test_scan_time_limit_and_stop_say_how_far(pc):
    cat = _scan(pc, time_limit_s=-1)
    assert not cat.complete and cat.stopped == "time limit" and "stopped (time limit)" in cat.how_far()
    stop = threading.Event()
    cat = _scan(pc, stop=stop, progress=lambda _t: stop.set())
    assert not cat.complete and cat.stopped == "stopped" and cat.reached
    assert cat.folders < _scan(pc).folders


def test_scan_follows_a_link_or_junction_once(pc):
    link = pc["d"] / "Aman" / "link_to_chris"
    target = pc["d"] / "Chris"
    try:
        if os.name == "nt":
            import _winapi

            _winapi.CreateJunction(str(target), str(link))
        else:
            os.symlink(target, link, target_is_directory=True)
    except (OSError, AttributeError, ImportError) as exc:
        pytest.skip(f"no links here: {exc}")
    cat = library.scan([pc["d"]], cfg=pc["cfg"])
    assert sum("CS_22rv1" in w.path for w in cat.workflows) == 2
    assert any("already looked at" in s for s in cat.skipped)


def test_scan_excludes_patterns_and_reuses_fasta_readings(pc, monkeypatch):
    cat = library.scan([pc["d"]], cfg=pc["cfg"], exclude=["Kai"])
    assert not any("Kai" in w.path for w in cat.workflows) and any("excluded (Kai)" in s for s in cat.skipped)
    library._FASTA_CACHE.clear()
    monkeypatch.setattr(library, "_headers", lambda *a, **k: (_ for _ in ()).throw(AssertionError("read again")))
    again = library.scan([pc["d"]], cfg=pc["cfg"], previous=cat)
    assert {f.path for f in again.fastas if not f.error} >= {str(pc["aman_fasta"])}


def test_default_roots(pc):
    cfg = pc["cfg"]
    roots = library.default_roots(cfg)
    assert roots[:2] == [cfg.workflow_dir, cfg.fasta_dir]
    assert pc["fp"] in roots and cfg.users_root in roots
    assert library.installed_version(cfg) == "24.0"


def test_only_real_fragpipe_installations_count(pc, tmp_path, monkeypatch):
    """#153: C:/FragPipe* also matches the lab's own C:/Fragpipe_Auto and C:/Fragpipe_General; only a folder with
    bin/fragpipe.bat or lib/fragpipe-*.jar inside is an installation (the zip's is one level down, fragpipe/)."""
    c = tmp_path / "C"
    inst = c / "FragPipe-23.1"
    (inst / "lib").mkdir(parents=True)
    (inst / "lib" / "fragpipe-23.1.jar").write_text("x")
    lab = c / "FragPipe_Auto"
    write_wf(lab / "workflows" / "TMT10-MS3.workflow", "TMT")
    (lab / "jobs").mkdir()
    (c / "FragPipe_General").mkdir()
    zipped = c / "FragPipe-jre-22.0" / "fragpipe"
    (zipped / "bin").mkdir(parents=True)
    (zipped / "bin" / "fragpipe.bat").write_text("x")
    monkeypatch.setattr(library, "INSTALL_GLOBS", (str(c / "FragPipe*"),))
    roots = library.install_roots()
    assert inst in roots and zipped in roots
    assert lab not in roots and c / "FragPipe_General" not in roots and c / "FragPipe-jre-22.0" not in roots
    ctx = library.Context(install_roots=roots)
    assert library.describe_workflow(lab / "workflows" / "TMT10-MS3.workflow", ctx).source == "other"
    assert not library._is_install_dir(lab) and library._is_install_dir(inst)


def test_a_partial_scan_never_replaces_a_fuller_catalog(pc, capsys):
    """#153: `scan --time` that stops early, or `--only --root` one folder, keeps what the last full scan found
    that it did not look at (files still on disk, unchanged); --replace keeps only the new scan; --root adds."""
    cfg = str(pc["cfg_path"])
    roots = [a for r in pc["roots"] for a in ("--root", str(r))]
    assert cli.main(["--config", cfg, "library", "scan", "--only", *roots]) == 0
    capsys.readouterr()
    full = library.load_catalog(pc["cfg"])
    n_full = (len(full.workflows), len(full.fastas))
    assert cli.main(["--config", cfg, "library", "scan", "--only", "--root", str(pc["auto"] / "workflows")]) == 0
    out = capsys.readouterr().out
    after = library.load_catalog(pc["cfg"])
    assert (len(after.workflows), len(after.fastas)) == n_full and "kept" in out
    assert any("scan --replace` keeps only this scan" in n for n in after.notes)
    stock = after.find(pc["stock_tmt"])
    assert stock is not None and stock.source == "stock"
    # a file deleted since is not kept
    gone = pc["d"] / "Alex" / "QS_shotgunP3F_L1C14_100uM" / "Offset-search" / "fragpipe.workflow"
    gone.unlink()
    assert cli.main(["--config", cfg, "library", "scan", "--only", "--root", str(pc["auto"]), "--time", "5"]) == 0
    capsys.readouterr()
    assert library.load_catalog(pc["cfg"]).find(gone) is None
    assert len(library.load_catalog(pc["cfg"]).workflows) == n_full[0] - 1
    # a stopped scan (the time limit) keeps the rest too
    assert cli.main(["--config", cfg, "library", "scan", "--time", "-1"]) == 0
    capsys.readouterr()
    assert len(library.load_catalog(pc["cfg"]).workflows) == n_full[0] - 1
    # --replace: only what this scan found
    assert cli.main(["--config", cfg, "library", "scan", "--only", "--root", str(pc["auto"]), "--replace"]) == 0
    capsys.readouterr()
    only = library.load_catalog(pc["cfg"])
    assert only.workflows and all(library._inside(w.path, pc["auto"]) for w in only.workflows)
    # --root adds to the default roots (the pinned folders, the installation, users_root)
    assert cli.main(["--config", cfg, "library", "scan", "--root", str(pc["d"]), "--replace"]) == 0
    capsys.readouterr()
    cat = library.load_catalog(pc["cfg"])
    assert cat.find(pc["stock_tmt"]) is not None and cat.find(pc["pax8"]) is not None
    assert cli.main(["--config", cfg, "library", "scan", "--only"]) == 2
    assert "--only needs" in capsys.readouterr().err


def test_a_configured_root_that_does_not_exist_is_said(pc, capsys):
    """#153: library.roots: [E:/not-plugged-in] (a USB drive unplugged, a typo) is a note in the scan's output, the
    catalog and `list`, never dropped without a word."""
    gone = pc["root"] / "not-plugged-in"
    d = configio.read_config(pc["cfg_path"])
    d["library"]["roots"] = [str(gone)]
    configio.write_config(pc["cfg_path"], d, backup=False)
    cfg = load(pc["cfg_path"], check_paths=False)
    notes = []
    assert gone not in library.default_roots(cfg, notes)
    assert notes and str(gone) in notes[0] and "library: roots" in notes[0] and "does not exist" in notes[0]
    assert any(str(gone) in n for n in library.scan(cfg=cfg).notes)
    assert cli.main(["--config", str(pc["cfg_path"]), "library", "scan", "--root", str(pc["d"])]) == 0
    assert f"note: {gone} does not exist" in capsys.readouterr().out
    assert cli.main(["--config", str(pc["cfg_path"]), "library", "list"]) == 0
    assert f"note: {gone} does not exist" in capsys.readouterr().out
    from ionomos import library_tab
    assert str(gone) in library_tab.catalog_line(library.load_catalog(cfg))


# ----------------------------------------------------------------- catalog --


def test_catalog_round_trip_and_bad_files(pc):
    cfg = pc["cfg"]
    assert "no scan yet" in library.load_catalog(cfg).notes[0]
    cat = _scan(pc)
    p = library.save_catalog(cfg, cat)
    assert p == cfg.log_dir / "library.json"
    back = library.load_catalog(cfg)
    assert back.as_dict() == json.loads(json.dumps(cat.as_dict()))
    assert back.find(pc["pax8"]).summary == cat.find(pc["pax8"]).summary
    p.write_text("{not json")
    broken = library.load_catalog(cfg)
    assert broken.workflows == [] and "could not be read" in broken.notes[0]


# --------------------------------------------------------------- recommend --


def test_recommend_orders_and_explains(pc):
    cat = _scan(pc)
    pinned = cat.find(pc["auto"] / "workflows" / "TMT10-MS3.workflow")
    cands = library.recommend(cat, "TMT", "24.0", pinned)
    paths = [Path(c.info.path) for c in cands]
    assert paths[0] == pc["auto"] / "workflows" / "TMT10-MS3.workflow"
    assert "it is the one the method uses now" in cands[0].reasons
    assert "saved with the installed FragPipe 24.0" in cands[0].reasons
    pax = next(c for c in cands if Path(c.info.path) == pc["pax8"])
    assert any(r.startswith("used in 1 finished search(es), the latest ") for r in pax.reasons)
    assert "saved with FragPipe 23.1, older than the installed 24.0" in pax.reasons
    assert paths[-1] == pc["d"] / "EJQ" / "EJQ123_TMT" / "fragpipe.workflow" or \
        cands[-1].score <= pax.score
    dia = library.recommend(cat, "DIA", "24.0")
    assert dia[0].info.source == "pinned" and dia[0].copies == 3   # the pinned DIA.workflow has Chris's settings
    assert any(r.startswith("used in 2 finished search(es)") for r in dia[0].reasons)
    assert library.recommend(cat, "isoDTB", "24.0")[0].info.source == "pinned"


def test_recommend_sinks_errors(tmp_path):
    good = library.describe_workflow(write_wf(tmp_path / "a.workflow", "TMT"))
    bad = library.describe_workflow(write_wf(tmp_path / "b.workflow", "TMT", tmtintegrator__channel_num="",
                                             ionquant__mbr="1"))
    cat = library.Catalog(workflows=[bad, good])
    cands = library.recommend(cat, "TMT", "24.0")
    assert [c.info.path for c in cands] == [good.path, bad.path]
    assert any(r.startswith("problem: A TMT workflow without a label type") for r in cands[1].reasons)


def test_recommend_fastas(pc):
    cat = _scan(pc)
    cands = library.recommend_fastas(cat, taxon="9606")
    assert Path(cands[0].info.path) == pc["fasta"]
    assert "already in the FASTA folder" in cands[0].reasons and "decoys (rev_) as FragPipe needs them" in \
        cands[0].reasons
    assert Path(cands[-1].info.path).name == "mixed species.fasta"
    yeast = library.recommend_fastas(cat, taxon="559292")
    assert all("another organism" in " ".join(c.reasons) for c in yeast if c.info.taxon != "559292")


def test_auto_assign_fills_only_what_is_missing_or_broken(pc):
    cat = _scan(pc)
    d = configio.read_config(pc["cfg_path"])
    assert library.auto_assign(d, cat, "24.0") == []          # everything is fine: nothing proposed
    d["methods"]["TMT"]["workflow"] = "TMT-gone.workflow"
    d["methods"]["DIA"]["fasta"] = "gone.fas"
    props = library.auto_assign(d, cat, "24.0")
    assert {(p.method, p.what) for p in props} == {("TMT", "workflow"), ("DIA", "fasta")}
    tmt = next(p for p in props if p.what == "workflow")
    assert tmt.candidate.info.kind == "TMT" and "not found in" in tmt.why
    dia = next(p for p in props if p.what == "fasta")
    assert Path(dia.candidate.info.path) == pc["fasta"]
    assert "Proposed" in tmt.text() and tmt.as_dict()["proposed"]["path"] == tmt.candidate.info.path
    # a method whose pinned workflow has an error gets a proposal too
    write_wf(pc["auto"] / "workflows" / "isoDTB.workflow", "TMT", tmtintegrator__channel_num="")
    cat = _scan(pc)
    d = configio.read_config(pc["cfg_path"])
    props = library.auto_assign(d, cat, "24.0")
    assert [(p.method, p.what) for p in props] == [("isoDTB", "workflow")]


# ------------------------------------------------------------------- adopt --


def test_adopt_copies_never_moves_and_keeps_a_backup(pc):
    cfg = pc["cfg"]
    before = pc["pax8"].read_bytes()
    old_pinned = (pc["auto"] / "workflows" / "TMT.workflow")
    old_pinned.write_text("the lab's earlier TMT workflow\n")
    cat = _scan(pc)
    res = library.adopt(cat.find(pc["pax8"]), "TMT", cfg, cat)
    assert res["workflow"] == "TMT.workflow"
    assert pc["pax8"].read_bytes() == before, "the original is never changed"
    baks = list((pc["auto"] / "workflows").glob("TMT.workflow.bak-*"))
    assert len(baks) == 1 and baks[0].read_text() == "the lab's earlier TMT workflow\n"
    copied = pc["auto"] / "fasta" / pc["aman_fasta"].name
    assert copied.is_file() and pc["aman_fasta"].is_file() and res["fasta"] == copied.name
    new = library.describe_workflow(pc["auto"] / "workflows" / "TMT.workflow")
    assert Path(new.database) == copied and new.content_hash == cat.find(pc["pax8"]).content_hash
    log = json.loads((cfg.log_dir / "library.json").read_text(encoding="utf-8"))
    assert [a["type"] for a in log["adopted"]] == ["fasta", "workflow"]
    assert log["adopted"][1]["origin"] == str(pc["pax8"]) and log["adopted"][1]["who"] == "adopt"
    again = library.adopt(cat.find(pc["pax8"]), "TMT", cfg, cat)
    assert "already holds exactly this workflow" in again["notes"][-1]
    assert len(list((pc["auto"] / "workflows").glob("TMT.workflow.bak-*"))) == 1


def test_adopt_fasta_safe_name_and_never_over_another_file(pc):
    cfg = pc["cfg"]
    src = pc["d"] / "Melody" / "BSA_19A" / "mixed species.fasta"
    res = library.adopt_fasta(src, cfg)
    assert res["fasta"] == "mixed_species.fasta" and res["copied"]
    assert (pc["auto"] / "fasta" / "mixed_species.fasta").read_bytes() == src.read_bytes() and src.is_file()
    again = library.adopt_fasta(src, cfg)
    assert not again["copied"] and again["fasta"] == "mixed_species.fasta"
    other = make_fasta(pc["root"] / "x" / "mixed species.fasta", human=3)
    third = library.adopt_fasta(other, cfg)
    assert third["fasta"] == "mixed_species-2.fasta"
    assert (pc["auto"] / "fasta" / "mixed_species.fasta").read_bytes() == src.read_bytes()
    same = library.adopt_fasta(pc["fasta"], cfg)
    assert not same["copied"] and "already in the FASTA folder" in same["notes"][0]
    assert library._free_name(pc["auto"] / "fasta", HUMAN).name == HUMAN.replace(".fasta.fas", "-2.fasta.fas")


def test_apply_proposals_edits_the_dict_only(pc):
    cat = _scan(pc)
    d = configio.read_config(pc["cfg_path"])
    d["methods"]["TMT"]["workflow"] = "TMT-gone.workflow"
    text_before = pc["cfg_path"].read_text(encoding="utf-8")
    props = library.auto_assign(d, cat, "24.0")
    lines = library.apply_proposals(pc["cfg"], d, props, cat)
    assert d["methods"]["TMT"]["workflow"] == "TMT.workflow" and lines[0].startswith("TMT: workflow TMT.workflow")
    assert pc["cfg_path"].read_text(encoding="utf-8") == text_before, "config.yaml is the caller's to write"


# ------------------------------------------------------------------- check --


def test_update_check(pc):
    cfg = pc["cfg"]
    write_wf(pc["auto"] / "workflows" / "TMT10-MS3.workflow", "TMT", saved="23.1")
    old_name = "2024-03-01-decoys-contam-UP000005640_9606.fasta.fas"
    make_fasta(pc["auto"] / "fasta" / old_name)
    d = configio.read_config(pc["cfg_path"])
    d["methods"]["DIA"]["fasta"] = old_name
    d["methods"]["isoDTB"]["workflow"] = "gone.workflow"
    configio.write_config(pc["cfg_path"], d, backup=False)
    cfg = load(pc["cfg_path"], check_paths=False)
    cat = _scan(pc)
    flags = library.update_check(cat, cfg, "24.0")
    codes = {(f.subject, f.code) for f in flags}
    assert ("TMT", "UPDATE_STOCK_NEWER") in codes and ("isoDTB", "PINNED_MISSING") in codes
    assert ("DIA", "FASTA_OLD") in codes and ("DIA", "UPDATE_FASTA_NEWER") in codes
    newer = next(f for f in flags if f.code == "UPDATE_FASTA_NEWER")
    assert HUMAN in newer.text and "2026-01-30" in newer.text


# ------------------------------------------------------------------ config --


@pytest.mark.parametrize("raw, ok", [
    (None, True), ({}, True), ({"roots": ["D:/", "//server/share"], "max_depth": 4}, True),
    ({"roots": "D:/"}, True), ({"time_limit_s": 30.5}, True), ({"exclude": ["*/raw"]}, True),
    ({"rootz": []}, False), ({"max_depth": 0}, False), ({"max_depth": True}, False),
    ({"time_limit_s": 1}, False), ({"roots": [""]}, False), ([], False),
])
def test_settings_from(raw, ok):
    if ok:
        s = library.settings_from(raw)
        assert set(s) == set(library.DEFAULTS)
    else:
        with pytest.raises(library.LibraryError):
            library.settings_from(raw)


def test_config_round_trip(tmp_path):
    d = configio.defaults(str(tmp_path / "Auto"), str(tmp_path / "General"))
    assert d["library"] == {"roots": [], "exclude": [], "max_depth": 6, "time_limit_s": 120}
    d["library"]["roots"] = ["D:/"]
    p = configio.write_config(tmp_path / "config.yaml", d)
    text = p.read_text(encoding="utf-8")
    assert "library:" in text and yaml.safe_load(text)["library"]["roots"] == ["D:/"]
    cfg = load(p, check_paths=False)
    assert cfg.library["roots"] == ["D:/"] and cfg.library["max_depth"] == 6
    assert configio.read_config(p)["library"]["roots"] == ["D:/"]
    d["library"]["max_depht"] = 3
    configio.write_config(p, d, backup=False)
    with pytest.raises(ConfigError, match="library.max_depht: unknown setting"):
        load(p, check_paths=False)
    old = tmp_path / "old.yaml"     # a config from before the library: the defaults
    old.write_text(text.split("library:")[0] + "methods:" + text.split("\nmethods:", 1)[1], encoding="utf-8")
    assert load(old, check_paths=False).library == library.settings_from(None)


# --------------------------------------------------------------- help, docs --


def _codes() -> set[str]:
    src = Path(library.__file__).read_text(encoding="utf-8")
    return set(re.findall(r'(?:Flag\(|add\()\s*"([A-Z][A-Z0-9_]+)"', src))


def test_every_flag_code_has_help_and_is_documented():
    codes = _codes()
    assert {"FASTA_NO_DECOYS", "WF_DIFFERS_FROM_PINNED", "UPDATE_FASTA_NEWER", "PINNED_MISSING"} <= codes
    ents = helpdoc.entries()
    assert sorted(c for c in codes if f"library.{c.lower()}" not in ents) == []
    assert helpdoc.topic("FASTA_NO_DECOYS") == "library.fasta_no_decoys"
    doc = (Path(__file__).resolve().parents[2] / "docs" / "LIBRARY.md").read_text(encoding="utf-8")
    assert sorted(c for c in codes if f"`{c}`" not in doc) == []


# --------------------------------------------------------------------- CLI --


def test_cli(pc, capsys):
    cfg = str(pc["cfg_path"])
    roots = [a for r in pc["roots"] for a in ("--root", str(r))]
    assert cli.main(["--config", cfg, "library", "scan", *roots]) == 0
    out = capsys.readouterr().out
    assert "workflow(s)" in out and "TMT:" in out and "ionomos library recommend" in out
    assert cli.main(["--config", cfg, "library", "list", "workflows", "--kind", "TMT", "--flags"]) == 0
    out = capsys.readouterr().out
    assert "fragpipe_pax8.workflow" in out and "[WF_OLDER_FRAGPIPE]" in out and "[FASTA]" not in out
    assert cli.main(["--config", cfg, "library", "list", "fastas", "--json"]) == 0
    assert any(x["name"] == HUMAN for x in json.loads(capsys.readouterr().out))
    assert cli.main(["--config", cfg, "library", "show", str(pc["pax8"])]) == 0
    out = capsys.readouterr().out
    assert "A TMT-10 search with TMT-Integrator" in out and "label type: TMT-10" in out and "ALL JOBS DONE" in out
    assert cli.main(["--config", cfg, "library", "show", str(pc["fasta"]), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["decoy_tag"] == "rev_"
    assert cli.main(["--config", cfg, "library", "recommend"]) == 0
    assert "nothing proposed" in capsys.readouterr().out
    # the TMT method's workflow disappears: recommend --apply puts the best one back and saves config.yaml
    (pc["auto"] / "workflows" / "TMT10-MS3.workflow").rename(pc["root"] / "moved-away.workflow")
    assert cli.main(["--config", cfg, "library", "recommend", "--apply"]) == 0
    out = capsys.readouterr().out
    assert "applied: TMT: workflow TMT.workflow" in out
    assert load(cfg, check_paths=False).methods["TMT"].workflow == "TMT.workflow"
    assert list((pc["auto"] / "config-backups").glob("config-*.yaml"))
    # adopt the 22.0 stock isoDTB workflow for isoDTB
    stock = pc["zip"] / "workflows" / "isoDTB-ABPP.workflow"
    assert cli.main(["--config", cfg, "library", "adopt", str(stock), "--method", "isoDTB"]) == 0
    assert "isoDTB now uses workflow isoDTB.workflow" in capsys.readouterr().out
    assert list((pc["auto"] / "workflows").glob("isoDTB.workflow.bak-*")) and stock.is_file()
    assert cli.main(["--config", cfg, "library", "adopt", str(stock), "--method", "Nope"]) == 2
    assert "no method 'Nope'" in capsys.readouterr().err
    assert cli.main(["--config", cfg, "library", "check"]) in (0, 1)
    assert "isoDTB" in capsys.readouterr().out
    assert cli.main(["--config", cfg, "library", "show", str(pc["root"] / "nothing.workflow")]) == 2


def test_adopt_no_config_never_changes_the_live_workflow(pc, capsys):
    """#150 (1): --no-config copies under a name no method uses, so the method's next search is unchanged."""
    cfg = str(pc["cfg_path"])
    d = configio.read_config(pc["cfg_path"])
    d["methods"]["TMT"]["workflow"] = "TMT.workflow"         # the copy's own name is the live one
    configio.write_config(pc["cfg_path"], d, backup=False)
    write_wf(pc["auto"] / "workflows" / "TMT.workflow", "TMT", tmtintegrator__groupby="1")
    live, before = pc["auto"] / "workflows" / "TMT.workflow", (pc["auto"] / "workflows" / "TMT.workflow").read_bytes()
    cfg_before = pc["cfg_path"].read_text(encoding="utf-8")
    assert cli.main(["--config", cfg, "library", "adopt", str(pc["pax8"]), "--method", "TMT", "--no-config"]) == 0
    out = capsys.readouterr().out
    assert live.read_bytes() == before, "the method's live workflow is never replaced by --no-config"
    assert (pc["auto"] / "workflows" / "TMT-2.workflow").is_file()
    assert "copied as TMT-2.workflow" in out and "TMT still uses TMT.workflow" in out
    assert pc["cfg_path"].read_text(encoding="utf-8") == cfg_before
    assert not list((pc["auto"] / "workflows").glob("TMT.workflow.bak-*"))
    # without --no-config the method's own file is replaced (kept as .bak) and config.yaml says so
    assert cli.main(["--config", cfg, "library", "adopt", str(pc["pax8"]), "--method", "TMT"]) == 0
    capsys.readouterr()
    assert list((pc["auto"] / "workflows").glob("TMT.workflow.bak-*"))
    # another method's live file is never replaced either: isoDTB searches with DIA.workflow here
    d = configio.read_config(pc["cfg_path"])
    d["methods"]["isoDTB"]["workflow"] = "DIA.workflow"
    configio.write_config(pc["cfg_path"], d, backup=False)
    dia = (pc["auto"] / "workflows" / "DIA.workflow").read_bytes()
    stock = pc["fp"] / "workflows" / "DIA_SpecLib_Quant.workflow"
    assert cli.main(["--config", cfg, "library", "adopt", str(stock), "--method", "DIA"]) == 0
    capsys.readouterr()
    assert (pc["auto"] / "workflows" / "DIA.workflow").read_bytes() == dia
    assert load(cfg, check_paths=False).methods["DIA"].workflow == "DIA-2.workflow"


def test_adopt_refuses_another_kind_or_an_error_mark_unless_forced(pc, capsys):
    """#150 (2, 3): a DIA workflow for TMT, or a FASTA without decoys, is refused with the reason (nothing copied,
    config.yaml unchanged); --force adopts it."""
    cfg = str(pc["cfg_path"])
    dia = pc["fp"] / "workflows" / "DIA_SpecLib_Quant.workflow"
    wf_dir = pc["auto"] / "workflows"
    files, text = sorted(p.name for p in wf_dir.iterdir()), pc["cfg_path"].read_text(encoding="utf-8")
    assert cli.main(["--config", cfg, "library", "adopt", str(dia), "--method", "TMT"]) == 1
    err = capsys.readouterr().err
    assert "[WF_KIND_MISMATCH]" in err and "is a DIA workflow" in err and "not a TMT one" in err and "--force" in err
    assert sorted(p.name for p in wf_dir.iterdir()) == files and pc["cfg_path"].read_text(encoding="utf-8") == text
    nodecoys = make_fasta(pc["root"] / "dbs" / "nodecoys.fasta", decoy=None)
    assert cli.main(["--config", cfg, "library", "adopt", str(nodecoys), "--method", "DIA"]) == 1
    err = capsys.readouterr().err
    assert "[FASTA_NO_DECOYS]" in err and "Make a FASTA with decoys" in err
    assert not (pc["auto"] / "fasta" / "nodecoys.fasta").exists()
    assert cli.main(["--config", cfg, "library", "adopt", str(nodecoys), "--method", "DIA", "--force"]) == 0
    assert "adopted although: [FASTA_NO_DECOYS]" in capsys.readouterr().out
    assert load(cfg, check_paths=False).methods["DIA"].fasta == "nodecoys.fasta"
    assert cli.main(["--config", cfg, "library", "adopt", str(dia), "--method", "TMT", "--force"]) == 0
    capsys.readouterr()
    assert load(cfg, check_paths=False).methods["TMT"].workflow == "TMT.workflow"
    # a workflow whose decoy tag does not match its FASTA
    decoy = make_fasta(pc["root"] / "dbs" / "decoy_tagged.fasta", decoy="DECOY_")
    bad = write_wf(pc["root"] / "w" / "tag.workflow", "TMT", db=decoy)
    flags = library.adopt_problems(library.describe_workflow(bad), "TMT")
    assert [f.code for f in flags] == ["WF_DECOY_TAG_MISMATCH"]
    assert library.adopt_problems(library.describe_workflow(pc["pax8"]), "TMT") == []
    assert cli.main(["--config", cfg, "library", "adopt", str(pc["root"] / "nope.workflow"), "--method", "TMT"]) == 1


def test_adopt_keeps_the_learned_aliases_beside_the_config(pc, capsys):
    """#149: a config without learned_aliases_file reads it beside config.yaml; saving it through adopt (configio
    fills in every default) must not re-point it at the default root C:/Fragpipe_Auto."""
    p = pc["cfg_path"]
    text = p.read_text(encoding="utf-8")
    p.write_text(re.sub(r"(?m)^\s*learned_aliases_file:.*\n", "", text), encoding="utf-8")
    want = p.parent / "learned_aliases.yaml"
    assert load(p, check_paths=False).learned_aliases_file == want
    assert cli.main(["--config", str(p), "library", "adopt", str(pc["pax8"]), "--method", "TMT"]) == 0
    capsys.readouterr()
    assert load(p, check_paths=False).learned_aliases_file == want
    assert "Fragpipe_Auto/learned" not in p.read_text(encoding="utf-8").replace(str(pc["auto"]).replace("\\", "/"), "")
    assert configio.read_config(pc["root"] / "elsewhere" / "config.yaml")["users"]["learned_aliases_file"] == \
        (pc["root"] / "elsewhere" / "learned_aliases.yaml").as_posix()


def test_cli_without_a_scan(pc, capsys):
    assert cli.main(["--config", str(pc["cfg_path"]), "library", "list"]) == 1
    assert "no scan yet" in capsys.readouterr().out
    assert cli.main(["--config", str(pc["cfg_path"]), "library", "recommend"]) == 1
    capsys.readouterr()
    assert cli.main(["--config", str(pc["cfg_path"]), "library", "check"]) == 0
    out = capsys.readouterr().out
    assert "current as far as" not in out
    assert "nothing has been scanned yet" in out and "ionomos library scan" in out and "Find on this PC" in out


def test_fasta_old_uses_the_name_date(tmp_path):
    today = date(2026, 10, 14)
    young = (today - timedelta(days=200)).isoformat()
    old = (today - timedelta(days=400)).isoformat()
    for day, flagged in ((young, False), (old, True)):
        info = library.describe_fasta(make_fasta(tmp_path / f"{day}-decoys-contam-UP000005640_9606.fasta.fas"),
                                      Context(today=today))
        assert ("FASTA_OLD" in {f.code for f in info.flags}) is flagged


# --- fix-fasta (round 4)


def test_a_fasta_with_a_byte_order_mark_counts_its_first_protein(tmp_path):
    """#152: `library show` said "2 proteins" for a 3-protein FASTA saved as UTF-8 with BOM (Notepad, Excel)."""
    plain = make_fasta(tmp_path / "plain.fasta", human=3, contam=1)
    bom = tmp_path / "bom.fasta"
    bom.write_bytes(b"\xef\xbb\xbf" + plain.read_bytes())
    a, b = library.describe_fasta(plain), library.describe_fasta(bom)
    assert (b.entries, b.targets, b.decoys, b.contaminants, b.reviewed) == (8, 4, 4, 1, 3)
    assert (b.entries, b.targets, b.proteins, b.summary) == (a.entries, a.targets, a.proteins, a.summary)
    with open(bom, "rb") as fh:
        assert next(library._headers(fh, None, None)).startswith(b"sp|P00000|")


def test_a_200_mb_file_without_line_breaks_is_read_in_linear_time(tmp_path):
    """#156 item 5: `_headers` kept the whole buffer when a chunk had no line break: 60 s for a 1 GB zero-filled
    file (the carry grew to the file's size). Now a chunk with no header costs one search and carries one byte."""
    import time
    import tracemalloc

    zeros, header = tmp_path / "zeros.fasta", tmp_path / "header.fasta"
    for p, head in ((zeros, b""), (header, b">sp|P1|A_HUMAN A OS=Homo sapiens OX=9606 ")):
        with open(p, "wb") as fh:
            fh.write(head)
            fh.truncate(200 << 20)  # zeros, no line break (sparse where the file system allows)
    started = time.monotonic()
    tracemalloc.start()
    try:
        info = library.describe_fasta(zeros)
        one = library.describe_fasta(header)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert info.entries == 0 and "not a FASTA" in info.error
    assert (one.entries, one.reviewed) == (1, 1) and one.taxon == "9606"
    assert peak < 6 * library.CHUNK, f"bounded memory: a few chunks, not the file ({peak / 1e6:.0f} MB)"
    assert time.monotonic() - started < 15, "linear: a few seconds for 200 MB, not minutes"
