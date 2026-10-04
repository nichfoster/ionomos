"""Phosphoproteomics, opt-in (downstream/phospho.py, D79): the three site tables and the localisation filter, KSEA
against KSEAapp 2.0 (tests/golden/ksea/, no R needed here), the kinase-substrate and STRING readers, the protein
correction of site comparisons, the doctor's issues, and whole analyses of a simulated phospho experiment, with
phospho off (nothing changes) and on."""
from __future__ import annotations

import gzip
import json
import math
import random
from pathlib import Path

import pytest

from ionomos import downstream
from ionomos.downstream import analysis, fpa, phospho, proteincorr
from ionomos.downstream.analysis import AnalysisError, Settings, settings_from
from ionomos.downstream.tables import num, read_tsv

GOLDEN = Path(__file__).parent / "golden" / "ksea"
SAMPLES = [f"{c}_{r}" for c in ("DMSO", "Drug") for r in (1, 2, 3)]


# ------------------------------------------------------------------------------------------------ helpers --


def _write(path: Path, header: list[str], rows: list[list]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\t".join(header) + "\n")
        for r in rows:
            fh.write("\t".join("" if v is None else str(v) for v in r) + "\n")
    return path


def _phospho_experiment(dest: Path, n: int = 240, seed: int = 79, up_genes: tuple[str, ...] = ("GENE001", "GENE002",
                        "GENE003", "GENE004", "GENE005", "GENE006"), low_loc: int = 20) -> dict:
    """A FragPipe LFQ-phospho output: combined_protein.tsv and combined_site_STY_79.9663.tsv (the real columns:
    Index, Gene, Protein, Protein ID, Peptide, Best Localization Probability, per sample Localization Probability,
    Intensity, MaxLFQ Intensity; 0 = missing). The sites of up_genes go up 4-fold in Drug; low_loc sites have a best
    localisation probability below 0.75. Returns what was planted."""
    rng = random.Random(seed)
    fp = dest / "fragpipe"
    sites, truth = [], {}
    for i in range(n):
        g = i // 3 + 1
        gene, acc = f"GENE{g:03d}", f"Q{g:05d}"
        rsd = f"{'STY'[i % 3]}{10 + 7 * i}"
        base = rng.uniform(14, 22)
        up = 2.0 if gene in up_genes else 0.0
        best = rng.uniform(0.2, 0.7) if i < low_loc else rng.uniform(0.8, 1.0)
        vals, locs = [], []
        for s in SAMPLES:
            v = base + (up if s.startswith("Drug") else 0.0) + rng.gauss(0, 0.25)
            miss = rng.random() < 0.04
            vals.append(0 if miss else 2.0 ** v)
            locs.append(0 if miss else round(min(best, 1.0) - rng.uniform(0, 0.05) * (i % 2), 4))
        sites.append([f"{acc}_{rsd}", gene, f"sp|{acc}|{gene}_HUMAN", acc, "PEPTIDE", round(best, 4), *locs,
                      *[round(v, 3) for v in vals], *[round(v * 1.01, 3) for v in vals]])
        truth[f"{gene} {rsd}"] = up
    head = ["Index", "Gene", "Protein", "Protein ID", "Peptide", "Best Localization Probability",
            *[f"{s} Localization Probability" for s in SAMPLES], *[f"{s} Intensity" for s in SAMPLES],
            *[f"{s} MaxLFQ Intensity" for s in SAMPLES]]
    _write(fp / "combined_site_STY_79.9663.tsv", head, sites)
    prot = []
    for g in range(1, n // 3 + 1):
        base = rng.uniform(18, 26)
        prot.append([f"sp|Q{g:05d}|GENE{g:03d}_HUMAN", f"Q{g:05d}", f"GENE{g:03d}", "a protein", 5,
                     *[round(2.0 ** (base + rng.gauss(0, 0.2)), 2) for _ in SAMPLES]])
    _write(fp / "combined_protein.tsv", ["Protein", "Protein ID", "Gene", "Description", "Combined Total Peptides",
                                         *[f"{s} MaxLFQ Intensity" for s in SAMPLES]], prot)
    return {"truth": truth, "n": n, "low": low_loc}


def _ks_table(path: Path, rows: list[tuple[str, str, str]], organism: str = "human", preamble: bool = True,
              gz: bool = False) -> Path:
    """A kinase-substrate table in PhosphoSitePlus's Kinase_Substrate_Dataset layout (licence lines first)."""
    head = ["GENE", "KINASE", "KIN_ACC_ID", "KIN_ORGANISM", "SUBSTRATE", "SUB_GENE_ID", "SUB_ACC_ID", "SUB_GENE",
            "SUB_ORGANISM", "SUB_MOD_RSD", "SITE_GRP_ID", "SITE_+/-7_AA", "DOMAIN", "IN_VIVO_RXN", "IN_VITRO_RXN", "CST_CAT#"]
    lines = (["10282024", "Made-up rows for a test; not PhosphoSitePlus data", ""] if preamble else []) + ["\t".join(head)]
    for kin, gene, rsd in rows:
        acc = "Q" + gene[4:].zfill(5) if gene.startswith("GENE") else "Q99999"
        lines.append("\t".join([kin, kin.lower(), "P00001", organism, gene, "1", acc, gene, organism, rsd, "1",
                                "_______", "", "X", "", ""]))
    text = "\n".join(lines) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if gz:
        path.write_bytes(gzip.compress(text.encode("utf-8")))
    else:
        path.write_text(text, encoding="utf-8")
    return path


# ------------------------------------------------------------------------------------------- KSEA golden --


def _golden_case(networkin: bool) -> tuple[list[dict], dict]:
    s = Settings(ksea_networkin=networkin, ksea_networkin_score=5, ksea_min_substrates=5)
    ks = phospho.load_kinase_substrates(GOLDEN / "ksea_ksdata.tsv", s)
    _h, px = read_tsv(GOLDEN / "ksea_px.tsv")
    sites = [((r["Gene"].upper(), r["Residue.Both"]), math.log2(abs(float(r["FC"])))) for r in px]
    res = phospho.ksea_scores(sites, ks, s.ksea_min_substrates)
    _h, ref = read_tsv(GOLDEN / ("ksea_networkin.tsv" if networkin else "ksea_psp.tsv"))
    return res, {r["Kinase.Gene"].strip(): r for r in ref}


@pytest.mark.parametrize("networkin", [False, True])
def test_ksea_matches_kseaapp(networkin):
    """Rscript run_kseaapp.R: KSEAapp 2.0's KSEA.Scores on made-up sites and kinase-substrate pairs (duplicated
    sites, a pair listed under two names of one kinase, NetworKIN rows with scores)."""
    res, ref = _golden_case(networkin)
    assert {r["kinase"] for r in res} == set(ref)
    for r in res:
        g = ref[r["kinase"]]
        assert r["m"] == int(g["m"]), r["kinase"]
        for mine, theirs in (("mS", "mS"), ("enrichment", "Enrichment"), ("z", "z.score"),
                             ("p_one_sided", "p.value"), ("p", "p_two_sided")):
            assert r[mine] == pytest.approx(float(g[theirs]), rel=1e-10, abs=1e-14), (r["kinase"], mine)
        if num(g["fdr_min5"]) is None:
            assert r["fdr"] is None and r["m"] < 5
        else:
            assert r["fdr"] == pytest.approx(float(g["fdr_min5"]), rel=1e-10), r["kinase"]
    # the planted kinases come out: KIN03 up (its substrates went up), and the scored ones are sorted first
    assert any(r["kinase"] == "KIN03" and r["z"] > 3 for r in res)
    assert all(r["fdr"] is not None for r in res[: sum(1 for x in res if x["fdr"] is not None)])


def test_ksea_by_hand():
    """z = (mS - mean) * sqrt(m) / SD on four sites, two of them substrates of K."""
    ks = phospho.KinaseSubstrates("t", "gene", {("A", "S1"): [("K", "PhosphoSitePlus")],
                                                 ("B", "S2"): [("K", "PhosphoSitePlus")]})
    sites = [(("A", "S1"), 2.0), (("B", "S2"), 1.0), (("C", "S3"), 0.0), (("D", "T4"), -1.0)]
    (r,) = phospho.ksea_scores(sites, ks, 1)
    mu, sd = 0.5, math.sqrt(((1.5 ** 2) + 0.25 + 0.25 + 1.5 ** 2) / 3)
    z = (1.5 - mu) * math.sqrt(2) / sd
    assert r["m"] == 2 and r["mS"] == pytest.approx(1.5) and r["z"] == pytest.approx(z)
    assert r["p"] == pytest.approx(math.erfc(z / math.sqrt(2))) and r["fdr"] == pytest.approx(r["p"])
    assert phospho.ksea_scores(sites, ks, 3)[0]["fdr"] is None   # below ksea_min_substrates: shown, not called
    assert phospho.ksea_scores(sites[:2], ks, 1) == []           # too few sites for a mean and a spread


def test_kinase_substrate_reader(tmp_path):
    rows = [("MAPK1", "GENE001", "S10"), ("MAPK1", "GENE002", "T24"), ("AKT1", "GENE003", "Y-p")]
    p = _ks_table(tmp_path / "Kinase_Substrate_Dataset.gz", rows, gz=True)
    ks = phospho.load_kinase_substrates(p, Settings())
    assert ks.rows_used == 2 and ks.kinases == 1 and ks.by_site[("GENE001", "S10")] == [("MAPK1", "PhosphoSitePlus")]
    assert "without a readable site" in ks.notes[0]
    mouse = _ks_table(tmp_path / "mouse.tsv", rows[:2], organism="mouse", preamble=False)
    with pytest.raises(phospho.DownloadError, match="no row left"):
        phospho.load_kinase_substrates(mouse, Settings())
    assert phospho.load_kinase_substrates(mouse, Settings(ksea_organism="")).rows_used == 2
    by_acc = phospho.load_kinase_substrates(p, Settings(ksea_match="protein"))
    assert ("Q00001", "S10") in by_acc.by_site
    (tmp_path / "x.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(phospho.DownloadError, match="not a kinase-substrate table"):
        phospho.load_kinase_substrates(tmp_path / "x.csv", Settings())


# ------------------------------------------------------------------------------------------- site tables --


def test_lfq_site_table_and_localisation_filter(tmp_path):
    plan = _phospho_experiment(tmp_path / "e")
    path = tmp_path / "e" / "fragpipe" / "combined_site_STY_79.9663.tsv"
    assert phospho.kind_of(path) == "lfq"
    m = phospho.load(path, "", Settings(phospho=True), tmp_path / "e" / "fragpipe")
    info = m.meta["phospho"]
    assert (m.kind, m.level, m.exp) == ("intensity", "site", "LFQ") and m.samples == SAMPLES
    assert info["sites_kept"] == plan["n"] - plan["low"] and info["sites_below"] == plan["low"]
    assert info["quantity"] == "MaxLFQ Intensity" and sum(info["hist"]) == plan["n"]
    assert set(info["residues"]) == {"S", "T", "Y"}
    f = m.features[0]
    assert f.id == f"sp|Q00007|GENE007_HUMAN|{'STY'[20 % 3]}{10 + 7 * 20}" and f.label.startswith("GENE007 ")
    assert phospho.site_parts(f) == ("Q00007", "GENE007", f"{'STY'[20 % 3]}{150}")
    assert m.condition["Drug_2"] == "Drug" and m.replicate["Drug_2"] == 2
    strict = phospho.load(path, "lfq", Settings(phospho=True, phospho_min_localization=0.95,
                                                phospho_localization_per_sample=True), tmp_path)
    assert strict.meta["phospho"]["sites_kept"] < info["sites_kept"]
    assert strict.meta["phospho"]["values_dropped"] > 0
    with pytest.raises(phospho.SiteTableError, match="no site passed"):
        phospho.load(path, "lfq", Settings(phospho_min_localization=1.0), tmp_path)


def test_tmt_single_site_and_diann_tables(tmp_path):
    work = tmp_path / "fragpipe"
    head = ["Index", "Gene", "ProteinID", "Peptide", "SequenceWindow", "Start", "End", "MaxPepProb",
            "ReferenceIntensity", "DMSO_1_126", "DMSO_1_127N", "Drug_1_128N", "Drug_1_128C"]
    _write(work / "tmt-report" / "abundance_single-site_MD.tsv", head,
           [[f"Q{i:05d}_S{10 + i}", f"GENE{i:03d}", f"Q{i:05d}", "PEPsTIDE", "AAAAAAASAAAAAAA", 1, 9, 0.99, 20.1,
             0.1, -0.1, 1.0 if i < 5 else 0.0, 1.1 if i < 5 else 0.05] for i in range(1, 31)])
    (work / "fragpipe.workflow").write_text("tmtintegrator.min_site_prob=0.5\n", encoding="utf-8")
    path, kind = phospho.find_table(work, tmp_path, "TMT", Settings(phospho=True))
    assert kind == "tmt" and path.name == "abundance_single-site_MD.tsv"
    m = phospho.load(path, kind, Settings(phospho=True), work)
    assert m.level == "site" and m.exp == "TMT" and m.features[0].label == "GENE001 S11"
    assert m.condition["Drug_1_128N"] == "Drug" and m.meta["ratio_to_reference"] == "TMT-Integrator"
    assert m.meta["phospho"]["upstream_min"] == 0.5 and "can't be applied" in m.meta["phospho"]["problem"]
    (work / "fragpipe.workflow").write_text("tmtintegrator.min_site_prob=0.75\n", encoding="utf-8")
    assert "problem" not in phospho.load(path, kind, Settings(phospho=True), work).meta["phospho"]

    dia = tmp_path / "dia"
    runs = [f"C:\\data\\raw\\{s}.raw" for s in SAMPLES]
    _write(dia / "diann-output" / "report.phosphosites_90.tsv",
           ["Protein", "Protein.Names", "Gene.Names", "Residue", "Site", "Sequence", *runs],
           [[f"Q{i:05d}", f"N{i}_HUMAN", f"GENE{i:03d}", "T", 100 + i, "AAAAAAATAAAAAAA",
             *[0 if (i + j) % 11 == 0 else 1000 + 10 * i + j for j in range(6)]] for i in range(1, 21)])
    _write(dia / "diann-output" / "report.phosphosites_99.tsv",
           ["Protein", "Protein.Names", "Gene.Names", "Residue", "Site", "Sequence", *runs],
           [["Q00001", "N1_HUMAN", "GENE001", "S", 5, "A", *[1000] * 6]])
    smap = {s: (s.split("_")[0], int(s.split("_")[1])) for s in SAMPLES}
    path, kind = phospho.find_table(dia, tmp_path, "DIA", Settings(phospho=True))
    assert path.name == "report.phosphosites_90.tsv" and kind == "diann"
    m = phospho.load(path, kind, Settings(phospho=True), dia, sample_map=smap)
    assert m.samples == SAMPLES and m.exp == "DIA" and m.features[0].label == "GENE001 T101"
    assert m.values[10][0] is None and m.meta["phospho"]["upstream_min"] == 0.9 and "problem" not in m.meta["phospho"]
    path99, _k = phospho.find_table(dia, tmp_path, "DIA", Settings(phospho=True, phospho_min_localization=0.95))
    assert path99.name == "report.phosphosites_99.tsv"
    strict = phospho.load(path, kind, Settings(phospho=True, phospho_min_localization=0.95), dia, sample_map=smap)
    assert "0.9" in strict.meta["phospho"]["problem"]


# ------------------------------------------------------------------------------------------------ settings --


def test_settings():
    s = settings_from({"phospho": "yes", "phospho_min_localization": "0.9", "ksea_match": "UniProt",
                       "ksea_organism": "", "string_min_score": 400})
    assert s.phospho is True and s.phospho_min_localization == 0.9 and s.ksea_match == "protein"
    assert s.ksea_organism == "" and s.string_min_score == 400
    assert Settings().phospho is False and Settings().kinase_substrates == "" and Settings().string_network == ""
    for bad in ({"phospho_min_localization": 1.5}, {"ksea_min_substrates": 0}, {"ksea_match": "kinase"},
                {"string_min_score": 2000}):
        with pytest.raises(AnalysisError):
            settings_from(bad)


# ------------------------------------------------------------------------------------- whole analyses --


def test_phospho_off_changes_nothing(tmp_path):
    """An LFQ-phospho search analysed without phospho: the proteins, as before, and no phospho section."""
    d = tmp_path / "e"
    _phospho_experiment(d)
    out = downstream.analyze(d, "LFQ", {"enrichment": False})
    info = json.loads((d / "results" / "analysis.json").read_text(encoding="utf-8"))
    assert info["level"] == "protein" and info["phospho"] == {"ran": False, "reason": "not asked for (analysis.phospho)"}
    assert not (d / "results" / "kinase_activity.tsv").exists()
    assert not any(i.code.startswith(("PHOSPHO", "KINASE", "STRING")) for i in out.issues)
    html = (d / "results" / "report.html").read_text(encoding="utf-8")
    assert "<section id='phos' hidden>" in html


def test_phospho_analysis_end_to_end(tmp_path):
    d = tmp_path / "e"
    plan = _phospho_experiment(d)
    # KIN_UP phosphorylates the planted sites (they go up in Drug), KIN_FLAT unchanged sites
    up = [k for k, v in plan["truth"].items() if v > 0]
    flat = [k for k, v in plan["truth"].items() if v == 0][40:52]
    rows = [("KIN_UP", k.split()[0], k.split()[1]) for k in up] + [("KIN_FLAT", k.split()[0], k.split()[1]) for k in flat]
    _ks_table(d / "Kinase_Substrate_Dataset.gz", rows, gz=True)
    links = d / "string" / "9606.protein.links.v12.0.txt"
    links.parent.mkdir(parents=True)
    links.write_text("protein1 protein2 combined_score\n9606.E1 9606.E2 900\n9606.E2 9606.E1 900\n"
                     "9606.E1 9606.E3 300\n9606.E2 9606.E40 950\n", encoding="utf-8")
    (links.parent / "9606.protein.info.v12.0.txt").write_text(
        "#string_protein_id\tpreferred_name\tprotein_size\tannotation\n9606.E1\tGENE001\t1\tx\n9606.E2\tGENE002\t1\tx\n"
        "9606.E3\tGENE003\t1\tx\n9606.E40\tGENE040\t1\tx\n", encoding="utf-8")
    cfg = {"enrichment": False, "phospho": True, "kinase_substrates": "Kinase_Substrate_Dataset.gz",
           "ksea_min_substrates": 5, "string_network": str(links), "export": {"figures": ["kinase_activity"]}}
    out = downstream.analyze(d, "LFQ", cfg)
    res = d / "results"
    info = json.loads((res / "analysis.json").read_text(encoding="utf-8"))
    assert info["level"] == "site" and info["phospho"]["ran"] is True
    loc = info["phospho"]["localisation"]
    assert loc["sites_kept"] == plan["n"] - plan["low"] and loc["kind"] == "lfq"
    assert [c["name"] for c in info["comparisons"]] == ["Drug vs DMSO"]
    ka = info["phospho"]["kinase_activity"]
    assert ka["ran"] and ka["comparisons"][0]["up"] == ["KIN_UP"] and ka["comparisons"][0]["down"] == []
    _h, kt = read_tsv(res / "kinase_activity.tsv")
    byk = {r["kinase"]: r for r in kt}
    assert float(byk["KIN_UP"]["z"]) > 4 and byk["KIN_UP"]["significant"] == "up"
    assert byk["KIN_FLAT"]["significant"] == "" and int(byk["KIN_UP"]["m"]) == len(up)
    st = info["phospho"]["string"]
    assert st["ran"] and st["comparisons"][0]["edges"] >= 1
    _h, sp = read_tsv(res / "string_partners.tsv")
    g1 = next(r for r in sp if r["gene"] == "GENE001")
    assert g1["partners"].startswith("GENE002 (900)") and "GENE003" not in g1["partners"]   # 300 < 700
    assert (res / "figures" / "kinase_activity_Drug_vs_DMSO.svg").is_file()
    html = (res / "report.html").read_text(encoding="utf-8")
    assert "<section id='phos'>" in html and "KSEA" in html
    assert not [i for i in out.issues if i.code.startswith(("PHOSPHO", "KINASE", "STRING"))]
    # the planted sites are the hits
    _h, diff = read_tsv(res / "Drug_vs_DMSO_differential.tsv")
    hits = {r["label"] for r in diff if r["significant"] == "up"}
    assert len(hits & set(up)) >= 0.8 * len([u for u in up if u in {r["label"] for r in diff}])


def test_phospho_protein_correction(tmp_path):
    """Each site comparison minus the same comparison of an unenriched proteome (an MSstats table), MSstatsPTM's
    adjustment (proteincorr.adjust, checked against MSstatsPTM in test_protein_correction.py)."""
    d = tmp_path / "e"
    _phospho_experiment(d)
    _write(d / "proteome.tsv", ["Protein", "Label", "log2FC", "SE", "Tvalue", "DF", "pvalue", "adj.pvalue"],
           [[f"sp|Q{g:05d}|GENE{g:03d}_HUMAN", "Drug-DMSO", 1.0 if g == 10 else 0.1, 0.2, 1, 8, 0.5, 0.5]
            for g in range(1, 60)])
    cfg = {"enrichment": False, "phospho": True,
           "protein_correction": {"proteome": "proteome.tsv", "match": "protein"}}
    out = downstream.analyze(d, "LFQ", cfg)
    info = json.loads((d / "results" / "analysis.json").read_text(encoding="utf-8"))
    names = [c["name"] for c in info["comparisons"]]
    assert names == ["Drug vs DMSO", "Drug vs DMSO (protein-corrected)"]
    assert info["protein_correction"]["ran"] and info["phospho"]["protein_correction"] is True
    _h, rows = read_tsv(d / "results" / "Drug_vs_DMSO_protein-corrected_differential.tsv")
    r = next(x for x in rows if x["label"].startswith("GENE010 ") and x["protein_status"] == "corrected")
    fc, se, df, _t, p = proteincorr.adjust(float(r["site_log2fc"]), float(r["site_se"]), float(r["site_df"]),
                                          1.0, 0.2, 8.0)
    assert float(r["log2fc"]) == pytest.approx(fc) and float(r["se"]) == pytest.approx(se)
    assert float(r["pvalue"]) == pytest.approx(p) and float(r["protein_log2fc"]) == 1.0
    missing = [x for x in rows if x["protein_status"] == "protein not found"]
    assert missing and all(x["pvalue"] in ("", "NA") for x in missing)
    assert not [i for i in out.issues if i.code.startswith("PROTEIN_CORRECTION")]
    # a proteome without the same comparison: a question, never a guess
    _write(d / "proteome2.tsv", ["Protein", "Label", "log2FC", "SE", "DF"],
           [["sp|Q00001|GENE001_HUMAN", "Other-DMSO", 1.0, 0.2, 8]])
    out = downstream.analyze(d, "LFQ", {**cfg, "protein_correction": {"proteome": "proteome2.tsv"}})
    assert "PROTEIN_CORRECTION_CONDITIONS" in [i.code for i in out.issues]


def test_doctor_issues_for_phospho(tmp_path):
    d = tmp_path / "e"
    _phospho_experiment(d)
    (d / "fragpipe" / "combined_site_STY_79.9663.tsv").rename(d / "fragpipe" / "elsewhere.tsv")
    out = downstream.analyze(d, "LFQ", {"enrichment": False, "phospho": True, "kinase_substrates": "nope.tsv",
                                        "string_network": "nope.txt"})
    codes = {i.code: i for i in out.issues}
    assert codes["PHOSPHO_TABLE"].severity == "input" and "proteins were analysed instead" in codes["PHOSPHO_TABLE"].message
    info = json.loads((d / "results" / "analysis.json").read_text(encoding="utf-8"))
    assert info["level"] == "protein"                       # the proteins, as without phospho
    assert info["phospho"]["kinase_activity"]["ran"] is False   # kinase activity needs sites
    assert codes["STRING_NETWORK"].severity == "warning"
    # the table named explicitly, the kinase table missing
    out = downstream.analyze(d, "LFQ", {"enrichment": False, "phospho": True, "phospho_table": "fragpipe/elsewhere.tsv",
                                        "kinase_substrates": "nope.tsv"})
    codes = {i.code: i for i in out.issues}
    assert "PHOSPHO_TABLE" not in codes and codes["KINASE_SUBSTRATES"].severity == "warning"
    for c in ("PHOSPHO_TABLE", "KINASE_SUBSTRATES", "STRING_NETWORK", "PHOSPHO_LOCALISATION"):
        assert phospho_help(c)


def phospho_help(code: str) -> bool:
    from ionomos import help as helpdoc

    return helpdoc.issue_entry(code) is not None


def test_string_website_export(tmp_path):
    p = tmp_path / "string_interactions.tsv"
    p.write_text("#node1\tnode2\tnode1_string_id\tnode2_string_id\tcombined_score\nA\tB\tx\ty\t0.912\nA\tC\tx\tz\t0.4\n",
                 encoding="utf-8")
    net = phospho.load_string(p, {"A", "B", "C"}, 700)
    assert net.edges == {"A": {"B": 912}, "B": {"A": 912}}
    with pytest.raises(phospho.DownloadError, match="protein.info"):
        q = tmp_path / "links" / "9606.protein.links.v12.0.txt"
        q.parent.mkdir()
        q.write_text("protein1 protein2 combined_score\n", encoding="utf-8")
        phospho.load_string(q, {"A"}, 700)


def test_report_payload_is_compact():
    v = phospho.report_payload({"table": "t", "sites_kept": 3}, {"ran": True, "comparisons": [
        {"name": "a vs b", "slug": "a_vs_b", "sites": 10, "matched": 4, "kinases": [
            {"kinase": "K", "m": 5, "mS": 1.23456789, "z": 2.5, "p": 0.01, "fdr": 0.02, "significant": "up",
             "substrates": ["A S1"]},
            {"kinase": "L", "m": 1, "mS": 0.1, "z": 0.1, "p": 0.9, "fdr": None, "significant": "", "substrates": []}]}],
        "file": "f", "min_substrates": 5}, None, None)
    assert v["ran"] and v["ksea"]["comps"][0]["k"] == [["K", 5, 1.2346, 2.5, 0.01, 0.02, "up", ["A S1"]]]
    p, _n = fpa.process(analysis_matrix())
    assert p.m.features


def analysis_matrix():
    from ionomos.downstream.quant import Feature, QuantMatrix

    feats = [Feature(f"sp|Q{i:05d}|G_HUMAN|S{i}", f"G{i} S{i}") for i in range(10)]
    vals = [[20.0 + i + 0.1 * j for j in range(6)] for i in range(10)]
    return QuantMatrix("intensity", "site", feats, SAMPLES, vals, {s: s.split("_")[0] for s in SAMPLES}, "x", exp="LFQ")
