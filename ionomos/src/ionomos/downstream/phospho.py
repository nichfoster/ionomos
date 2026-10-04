"""
Phosphoproteomics, opt-in (ROADMAP 5C #8 / #9, D79): phosphosites instead of proteins, a localisation filter,
kinase activity (KSEA) and interaction partners among the hits (STRING).

Nothing here runs unless the lab asks for it, so an experiment analysed before is analysed exactly as before:

    analysis:
      phospho: true                     # analyse the search's phosphosite table instead of its protein table
      phospho_min_localization: 0.75    # a site is kept when its best localisation probability reaches this
      phospho_localization_per_sample: false   # true: also drop a sample's value whose own probability is lower
      phospho_table: ""                 # a site table to read instead of the one found in the search output
      kinase_substrates: D:/downloads/Kinase_Substrate_Dataset.gz   # a kinase-substrate table the lab downloaded
      ksea_min_substrates: 5            # a kinase needs this many measured substrate sites to be scored
      ksea_networkin: false             # true: also NetworKIN predictions (KSEAapp's PSP&NetworKIN file)
      ksea_networkin_score: 5           # ... at least this NetworKIN score
      ksea_organism: human              # rows of other organisms in the table are left out ("" keeps every row)
      ksea_match: gene                  # gene (substrate gene + residue, as KSEAapp) | protein (UniProt accession)
      string_network: D:/downloads/9606.protein.links.v12.0.txt.gz   # optional: STRING partners among the hits
      string_min_score: 700             # STRING combined score (0-1000) an interaction needs
      protein_correction: {proteome: ..., match: gene}   # optional: site change minus protein change (D70's
                                        #   MSstatsPTM adjustment), per comparison

The site tables (one row per site; the values become the QuantMatrix the rest of the analysis tests, level
"site", kind "intensity"):
    FragPipe LFQ   IonQuant's combined_site_STY_79.9663.tsv: Index (P12345_S142), Gene, Protein, Protein ID,
                   Peptide, Best Localization Probability, then per sample "<s> Localization Probability",
                   "<s> Intensity" and "<s> MaxLFQ Intensity" (MaxLFQ is used when it has values). 0 = missing.
                   The localisation filter is applied here, on Best Localization Probability (and per sample
                   with phospho_localization_per_sample).
    FragPipe TMT   TMT-Integrator's abundance_single-site_MD.tsv (Index, Gene, ProteinID, Peptide,
                   SequenceWindow, ..., ReferenceIntensity, then the channels as log2 ratios). The table has no
                   probabilities: TMT-Integrator filtered with its own min_site_prob, which is read from
                   fragpipe.workflow and compared with the setting.
    DIA (DIA-NN)   report.phosphosites_90.tsv / report.phosphosites_99.tsv (Protein, Protein.Names, Gene.Names,
                   Residue, Site, Sequence, then the runs; 0 = missing): DIA-NN keeps sites localised with
                   confidence 0.9 / 0.99. The 99 file is read when the setting is above 0.9.

KSEA (Casado et al., Sci. Signal. 2013; KSEAapp, Wiredja et al., Bioinformatics 2017), per comparison, on every
site with a log2 fold change. As KSEAapp's KSEA.Scores (2.0, checked against it in tests/test_phospho.py,
goldens from tests/golden/ksea/run_kseaapp.R):
    mean and SD (n - 1) of the log2 fold changes of every site;
    a kinase's substrates: the measured sites the table lists for it (matched on substrate gene + residue, e.g.
        "MAPK1" + "T185"), a site measured twice (two proteins, two isoforms) averaged first;
    mS = the mean log2 fold change of its m substrates; Enrichment = mS / |mean|;
    z = (mS - mean) * sqrt(m) / SD.
Two choices differ from KSEAapp, and the table keeps KSEAapp's numbers beside them:
    p is two-sided, 2 * pnorm(-|z|) (KSEAapp reports the one-sided pnorm(-|z|): p_one_sided), because a
        kinase may go either way;
    Benjamini-Hochberg runs over the kinases with at least ksea_min_substrates substrates, the ones reported
        (KSEAapp adjusts over every kinase with one substrate or more, then filters).
The kinase-substrate table is never bundled: PhosphoSitePlus is free for non-commercial use only (CC BY-NC-SA),
so each lab downloads it. It is read from the path given, read only.

STRING (CC BY 4.0), also a download: protein.links (protein1 protein2 combined_score, 9606.ENSP... ids) with
the protein.info file next to it for the names, or a network exported from the STRING website (#node1, node2,
..., combined_score). Per comparison: which hits interact with which other hits.
"""
from __future__ import annotations

import csv
import gzip
import io
import math
import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path

from ionomos.downstream import quant, stats
from ionomos.downstream.quant import Feature, QuantMatrix
from ionomos.downstream.tables import num, read_tsv

LFQ_GLOB = ("combined_site_STY_79.966*.tsv",)
TMT_GLOB = ("abundance_single-site_MD.tsv", "abundance_single-site_*.tsv")
DIANN_FILES = ("report.phosphosites_90.tsv", "report.phosphosites_99.tsv", "*phosphosites_90.tsv",
               "*phosphosites_99.tsv")
KINDS = {"lfq": "FragPipe (IonQuant) site report", "tmt": "TMT-Integrator single-site report",
         "diann": "DIA-NN phosphosite matrix"}
MATCH = ("gene", "protein")
LIMIT_BYTES = 1024**3
_INDEX = re.compile(r"^(.+?)_([A-Za-z])(\d+)$")
_UNIPROT = re.compile(r"(?:^|\|)(?:sp|tr)\|([^|]+)\|")
_RSD = re.compile(r"^([A-Za-z])(\d+)(?:-p)?$")
KSEA_COLUMNS = ["kinase", "m", "mS", "enrichment", "z", "p", "p_one_sided", "fdr", "significant", "substrates"]
CORRECTION_COLUMNS = ["protein_key", "protein_status", "protein_comparison", "protein_log2fc", "protein_se",
                      "protein_df", "site_log2fc", "site_se", "site_df", "site_pvalue", "site_qvalue"]
STRING_COLUMNS = ["comparison", "gene", "direction", "partners", "n_partners", "best_score"]


class SiteTableError(ValueError):
    pass


class DownloadError(ValueError):
    """A file the lab downloaded (kinase-substrate table, STRING network) that can't be used."""


# ------------------------------------------------------------------ reading the site table --


def _rglob(root: Path, patterns) -> Path | None:
    for pat in patterns:
        hits = sorted((h for h in Path(root).rglob(pat)
                       if "results" not in h.relative_to(root).parts[:1] and "_previous_" not in str(h)),
                      key=lambda p: (len(p.parts), str(p)))
        if hits:
            return hits[0]
    return None


def find_table(workdir: Path, dest: Path | None, method: str | None, settings) -> tuple[Path | None, str]:
    """(the site table, its kind). analysis.phospho_table wins (a full path or one in the experiment folder);
    otherwise the search output is searched in the order the method suggests."""
    if settings.phospho_table:
        p = Path(settings.phospho_table)
        if not p.is_absolute() and dest is not None:
            p = Path(dest) / p
        return (p, kind_of(p)) if p.is_file() else (None, "")
    if not Path(workdir).is_dir():
        return None, ""
    strict = (settings.phospho_min_localization or 0) > 0.9  # DIA-NN's 0.99 matrix first
    diann = tuple(sorted(DIANN_FILES, key=lambda g: ("_99" not in g, g.startswith("*")))) if strict else DIANN_FILES
    order = {"TMT": ("tmt", "lfq", "diann"), "DIA": ("diann", "lfq", "tmt"),
             "DIA-NN": ("diann", "lfq", "tmt")}.get(method or "", ("lfq", "diann", "tmt"))
    globs = {"lfq": LFQ_GLOB, "tmt": TMT_GLOB, "diann": diann}
    for k in order:
        hit = _rglob(Path(workdir), globs[k])
        if hit is not None:
            return hit, k
    return None, ""


def kind_of(path: Path) -> str:
    """Which site table a file is, from its header."""
    head = set(_header(path))
    if "Best Localization Probability" in head or any(h.endswith(" Localization Probability") for h in head):
        return "lfq"
    if {"Residue", "Site"} <= head and ("Protein" in head or "Protein.Names" in head):
        return "diann"
    if "Index" in head and ("ReferenceIntensity" in head or "SequenceWindow" in head):
        return "tmt"
    raise SiteTableError(f"{Path(path).name} is not a phosphosite table Ionomos reads: it needs IonQuant's "
                         "combined_site_STY_79.9663.tsv, TMT-Integrator's abundance_single-site_MD.tsv or DIA-NN's "
                         "report.phosphosites_90.tsv columns")


def _header(path: Path) -> list[str]:
    from ionomos.downstream.tables import read_header

    return read_header(path)


def _site_of(index: str, protein: str, gene: str) -> tuple[str, str, str, int | None]:
    """(feature id, label, residue, position) for one site: 'sp|P12345|ABC_HUMAN|S142', 'ABC S142'."""
    m = _INDEX.match(index or "")
    if not m:
        return index or protein, gene or index, "", None
    acc, residue, pos = m.group(1), m.group(2).upper(), int(m.group(3))
    prot = protein if protein and _UNIPROT.search(protein + "|") else acc
    name = (gene or "").split(";")[0].strip() or acc
    return f"{prot}|{residue}{pos}", f"{name} {residue}{pos}", residue, pos


def _hist(probs: list[float]) -> list[int]:
    out = [0] * 20
    for v in probs:
        out[min(19, max(0, int(v * 20)))] += 1
    return out


def _cond(sample: str) -> str:
    m = re.match(r"^(.*)_\d+$", sample)
    return m.group(1) if m else sample


def _reps(samples: list[str]) -> dict[str, int]:
    return {x: int(x.rsplit("_", 1)[1]) for x in samples if x.rsplit("_", 1)[-1].isdigit()}


def _load_lfq(path: Path, settings) -> QuantMatrix:
    header, rows = read_tsv(path)
    if "Index" not in header:
        raise SiteTableError(f"{path.name} has no Index column")
    loc = [h for h in header if h.endswith(" Localization Probability") and h != "Best Localization Probability"]
    samples = [h[: -len(" Localization Probability")] for h in loc]
    maxlfq = [f"{s} MaxLFQ Intensity" for s in samples]
    inten = [f"{s} Intensity" for s in samples]
    use_maxlfq = all(c in header for c in maxlfq) and any((num(r.get(c)) or 0) > 0 for r in rows[:500] for c in maxlfq)
    cols = maxlfq if use_maxlfq else inten
    if not samples or not all(c in header for c in cols):
        raise SiteTableError(f"{path.name}: no per-sample Intensity columns next to the Localization Probability ones")
    thr = settings.phospho_min_localization
    feats, vals, best_all = [], [], []
    below = dropped_values = 0
    residues: dict[str, int] = {}
    for r in rows:
        best = num(r.get("Best Localization Probability"))
        if best is None:
            per = [num(r.get(h)) for h in loc]
            best = max((v for v in per if v is not None), default=None)
        if best is not None:
            best_all.append(best)
        if best is None or best < thr - 1e-12:
            below += 1
            continue
        fid, label, residue, _pos = _site_of(r.get("Index", ""), r.get("Protein", ""), r.get("Gene", ""))
        row = [quant._log2(num(r.get(c))) for c in cols]
        if settings.phospho_localization_per_sample:
            for j, h in enumerate(loc):
                p = num(r.get(h))
                if row[j] is not None and (p is None or p < thr - 1e-12):
                    row[j] = None
                    dropped_values += 1
        feats.append(Feature(fid, label, r.get("Protein Description", "") or ""))
        vals.append(row)
        residues[residue or "?"] = residues.get(residue or "?", 0) + 1
    info = {"table": path.name, "kind": "lfq", "source": KINDS["lfq"],
            "quantity": "MaxLFQ Intensity" if use_maxlfq else "Intensity", "min_localization": thr,
            "filter": (f"best localisation probability ≥ {thr:g}, applied by Ionomos"
                       + (f"; a sample's value also needs its own probability ≥ {thr:g}"
                          if settings.phospho_localization_per_sample else "")),
            "sites_in_table": len(rows), "sites_kept": len(feats), "sites_below": below,
            "values_dropped": dropped_values, "residues": residues, "hist": _hist(best_all)}
    return QuantMatrix("intensity", "site", feats, samples, vals, {s: _cond(s) for s in samples}, str(path),
                       notes=[f"phosphosites: {len(feats):,} of {len(rows):,} sites with best localisation "
                              f"probability ≥ {thr:g}; quantity: {info['quantity']}"],
                       exp="LFQ", replicate=_reps(samples), columns=dict(zip(samples, cols, strict=True)),
                       meta={"phospho": info})


def _workflow_value(workdir: Path, record, key: str) -> float | None:
    from ionomos.downstream.sdrf import workflow

    try:
        props, _name = workflow(record, workdir)
    except Exception:  # noqa: BLE001 - a workflow that can't be read only loses a note
        return None
    return num(props.get(key))


def _load_tmt(path: Path, settings, workdir: Path, record) -> QuantMatrix:
    from ionomos.downstream import tmt

    header = _header(path)
    m = quant.from_tmt_abundance(path, tmt.annotation_rows(header))
    _h, rows = read_tsv(path)
    feats, residues = [], {}
    for f, r in zip(m.features, rows, strict=True):
        fid, label, residue, _pos = _site_of(r.get("Index", ""), r.get("ProteinID", ""), r.get("Gene", ""))
        feats.append(Feature(fid, label, "", f.peptides))
        residues[residue or "?"] = residues.get(residue or "?", 0) + 1
    m.features = feats
    m.level = "site"
    thr = settings.phospho_min_localization
    used = _workflow_value(workdir, record, "tmtintegrator.min_site_prob")
    if used is None:
        filt = "by TMT-Integrator (its min_site_prob was not found in fragpipe.workflow)"
    elif used < 0:
        filt = "none: TMT-Integrator's min_site_prob is -1 (no filter)"
    else:
        filt = f"by TMT-Integrator, min_site_prob {used:g}"
    info = {"table": path.name, "kind": "tmt", "source": KINDS["tmt"], "quantity": "log2 ratio to the reference",
            "min_localization": thr, "filter": filt, "upstream_min": used, "sites_in_table": len(rows),
            "sites_kept": len(feats), "sites_below": None, "values_dropped": 0, "residues": residues, "hist": []}
    if used is None or used < thr - 1e-12:
        info["problem"] = (f"{path.name} has no localisation probabilities, so the filter (≥ {thr:g}) can't be applied "
                           f"here; TMT-Integrator filtered {'with min_site_prob ' + format(used, 'g') if used is not None else 'with a threshold not found in fragpipe.workflow'}"
                           ". Set tmtintegrator.min_site_prob in the workflow and search again for a stricter filter")
    m.meta["phospho"] = info
    m.notes.append(f"phosphosites: {len(feats):,} sites from TMT-Integrator; localisation filter: {filt}")
    return m


_DIANN_META = ("Protein", "Protein.Names", "Gene.Names", "Genes", "Residue", "Site", "Sequence")


def _load_diann(path: Path, settings, sample_map: dict | None) -> QuantMatrix:
    header, rows = read_tsv(path)
    for need in ("Residue", "Site"):
        if need not in header:
            raise SiteTableError(f"{path.name} has no {need} column")
    sample_map = sample_map or {}
    runs = [h for h in header if h not in _DIANN_META]
    samples, cond, reps, colmap, unmatched, matched = [], {}, {}, {}, [], set()
    for h in runs:
        stem = quant.run_stem(h)
        key = quant.match_run_stem(stem, sample_map)
        if key is not None:
            matched.add(key)
            c, rep = sample_map[key]
            s = f"{c}_{rep}"
            reps[s] = int(rep)
        else:
            clean = re.sub(r"_(?:uncalibrated|calibrated)$", "", stem, flags=re.IGNORECASE)
            s, c = stem, quant._dia_condition(clean)
            if sample_map:
                unmatched.append(stem)
        base, k = s, 2
        while s in cond:
            s, k = f"{base}.{k}", k + 1
        samples.append(s)
        cond[s] = c
        colmap[s] = h
    feats, vals, residues = [], [], {}
    for r in rows:
        acc = (r.get("Protein") or "").split(";")[0]
        residue = (r.get("Residue") or "").strip().upper()
        pos = (r.get("Site") or "").strip()
        gene = (r.get("Gene.Names") or r.get("Genes") or "").split(";")[0]
        fid, label, residue, _p = _site_of(f"{acc}_{residue}{pos}", "", gene)
        feats.append(Feature(fid, label, ""))
        vals.append([quant._log2(num(r.get(h))) for h in runs])
        residues[residue or "?"] = residues.get(residue or "?", 0) + 1
    level = 0.99 if "99" in path.stem.rsplit("_", 1)[-1] else 0.9
    thr = settings.phospho_min_localization
    info = {"table": path.name, "kind": "diann", "source": KINDS["diann"], "quantity": "Top 1 precursor (DIA-NN)",
            "min_localization": thr, "filter": f"by DIA-NN: site localisation confidence ≥ {level:g}",
            "upstream_min": level, "sites_in_table": len(rows), "sites_kept": len(feats), "sites_below": None,
            "values_dropped": 0, "residues": residues, "hist": []}
    if level < thr - 1e-12:
        info["problem"] = (f"{path.name} keeps sites localised with confidence ≥ {level:g}, below the "
                           f"phospho_min_localization of {thr:g}; DIA-NN writes no stricter matrix than 0.99")
    missing = sorted(set(sample_map) - matched)
    notes = [f"phosphosites: {len(feats):,} sites from {path.name} (localisation confidence ≥ {level:g})"]
    if missing:
        notes.append("Expected runs missing from the phosphosite matrix: " + ", ".join(missing))
    for s in samples:
        if s not in reps and s.rsplit("_", 1)[-1].isdigit():
            reps[s] = int(s.rsplit("_", 1)[1])
    return QuantMatrix("intensity", "site", feats, samples, vals, cond, str(path), notes=notes, exp="DIA",
                       replicate=reps, columns=colmap,
                       meta={"phospho": info, "missing_runs": missing, "unmatched_runs": unmatched})


def load(path: Path, kind: str, settings, workdir: Path, record=None, sample_map: dict | None = None) -> QuantMatrix:
    """The site table as a QuantMatrix (level "site", kind "intensity"), with what the localisation filter did in
    m.meta["phospho"]. Raises SiteTableError when the file can't be read as a site table."""
    path = Path(path)
    try:
        if path.stat().st_size > LIMIT_BYTES:
            raise SiteTableError(f"{path.name} is larger than {LIMIT_BYTES // 2**20} MB")
    except OSError as exc:
        raise SiteTableError(f"{path.name} can't be read: {exc}") from exc
    kind = kind or kind_of(path)
    if kind == "lfq":
        m = _load_lfq(path, settings)
    elif kind == "tmt":
        m = _load_tmt(path, settings, workdir, record)
    elif kind == "diann":
        m = _load_diann(path, settings, sample_map)
    else:
        raise SiteTableError(f"{path.name}: unknown site table")
    if not m.features:
        raise SiteTableError(f"{path.name}: no site passed the localisation filter (≥ {settings.phospho_min_localization:g})"
                             if kind == "lfq" else f"{path.name} has no sites")
    return m


def site_parts(f: Feature) -> tuple[str, str, str]:
    """(UniProt accession, gene, residue + position) of a site feature: ('P28482', 'MAPK1', 'T185')."""
    from ionomos.downstream.cys import site_key

    acc, residue, pos = site_key(f.id)
    gene = f.label.rsplit(" ", 1)[0] if " " in f.label else f.label
    return acc.split("-")[0].upper(), gene.split(";")[0].strip().upper(), f"{residue}{pos}" if pos is not None else ""


# ------------------------------------------------------------- protein correction --


def _pick(proteome, d, given: dict) -> tuple[object, str]:
    """The proteome comparison for site comparison d: named in protein_correction.conditions (by the comparison's
    name or its treatment), else the one with the same treatment and control. Never a guess."""
    low = {k.lower(): v for k, v in given.items()}
    want = low.get(d.name.lower()) or low.get(d.treatment.lower())
    comps = [c for c in proteome.comparisons if c.scale == "fc"]
    if want is not None:
        hit = [c for c in comps if c.name.lower() == want.lower()]
        if len(hit) == 1:
            return hit[0], ""
        return None, f"{d.name}: protein_correction.conditions names {want!r}, which is not a comparison of the proteome"
    hit = [c for c in comps if c.treatment.lower() == d.treatment.lower() and c.control.lower() == str(d.control).lower()]
    if len(hit) == 1:
        return hit[0], ""
    return None, (f"{d.name}: the proteome has {'several comparisons' if hit else 'no comparison'} of {d.treatment} "
                  f"against {d.control}")


def correct(p, diffs: list, settings, proteome):
    """Phosphosite comparisons corrected for protein abundance, MSstatsPTM's adjustment (proteincorr.adjust, D70):
    site log2FC - protein log2FC of the same comparison in an unenriched proteome. Returns a proteincorr.Result."""
    from ionomos.downstream import proteincorr as pc

    given = settings.protein_correction.get("conditions") or {}
    keys = [pc.site_keys(f, proteome.match) for f in p.m.features]
    out = pc.Result([], {}, {})
    unmatched, per = [], []
    for d in diffs:
        if d.kind != "intensity" or d.control in (None, "others") or d.correction:
            continue
        comp, why = _pick(proteome, d, given)
        if comp is None:
            unmatched.append(why)
            per.append({"site_comparison": d.name, "proteome_comparison": None, "reason": why})
            continue
        cd, counts, _of = pc.correct_comparison(d, comp, keys, settings, lambda fc: fc, f"{d.name} ({pc.SUFFIX})",
                                                proteome, "protein_log2fc")
        out.diffs.append(cd)
        measured = sum(1 for r in cd.rows if r["site_log2fc"] is not None)
        found = sum(1 for r in cd.rows if r["site_log2fc"] is not None
                    and r["protein_status"] not in ("protein not found", "protein ambiguous"))
        per.append({"site_comparison": d.name, "proteome_comparison": comp.name, "sites": len(cd.rows),
                    "sites_measured": measured, "protein_found": found,
                    **{s.replace(" ", "_"): n for s, n in counts.items()}, "tested": cd.tested, "up": cd.up,
                    "down": cd.down, "name": cd.name})
        if measured and found < 0.5 * measured:
            out.problems.append(("PROTEIN_CORRECTION", "warning",
                                 f"{d.name}: the protein of only {found:,} of {measured:,} sites was found in "
                                 f"{comp.name} (match: {proteome.match}); the others keep their uncorrected change"))
    if unmatched:
        names = ", ".join(proteome.names()[:8]) + (" …" if len(proteome.comparisons) > 8 else "")
        out.problems.append(("PROTEIN_CORRECTION_CONDITIONS", "input",
                             "; ".join(unmatched) + f". The proteome's comparisons: {names}. Those site comparisons "
                             "were not corrected."))
    out.notes += proteome.notes
    out.summary = {"ran": bool(out.diffs), "proteome": str(proteome.path), "source": proteome.kind,
                   "match": proteome.match, "orientation": "the proteome's comparison of the same two conditions, "
                   "as it is (log2 treatment / control on both sides)", "conditions": per,
                   "method": "MSstatsPTM adjustment: site log2FC - protein log2FC, SE = sqrt(SE_site² + SE_protein²), "
                             "Satterthwaite df, BH per comparison"}
    if not out.diffs:
        out.summary["reason"] = "no site comparison could be matched to a proteome comparison"
    return out


# ---------------------------------------------------------------------- downloads --


def find_download(name: str, dest: Path | None) -> Path | None:
    """A file the lab downloaded: an absolute path, or one in the experiment folder."""
    if not name:
        return None
    p = Path(name)
    if not p.is_absolute():
        if dest is None:
            return None
        p = Path(dest) / p
    return p if p.is_file() else None


def _text(path: Path) -> str:
    try:
        if path.stat().st_size > LIMIT_BYTES:
            raise DownloadError(f"{path.name} is larger than {LIMIT_BYTES // 2**20} MB")
        raw = path.read_bytes()
    except OSError as exc:
        raise DownloadError(f"{path.name} can't be read: {exc}") from exc
    if raw[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(raw)
        except (OSError, EOFError) as exc:
            raise DownloadError(f"{path.name} is a damaged .gz file: {exc}") from exc
    return raw.decode("utf-8-sig", errors="replace")


@dataclass
class KinaseSubstrates:
    file: str
    match: str
    by_site: dict[tuple[str, str], list[tuple[str, str]]]  # (substrate key, residue+position) -> [(kinase, source)]
    rows_read: int = 0
    rows_used: int = 0
    kinases: int = 0
    sources: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


_KS_COLS = {"kinase": ("GENE", "KINASE_GENE", "KINASE"), "sub_gene": ("SUB_GENE", "SUBSTRATE_GENE"),
            "sub_acc": ("SUB_ACC_ID", "SUBSTRATE_ACC", "SUB_ACC"), "site": ("SUB_MOD_RSD", "SITE", "RESIDUE"),
            "kin_org": ("KIN_ORGANISM",), "sub_org": ("SUB_ORGANISM",), "source": ("SOURCE",),
            "score": ("NETWORKIN_SCORE",)}


def load_kinase_substrates(path: Path, settings) -> KinaseSubstrates:
    """A kinase-substrate table the lab downloaded: PhosphoSitePlus's Kinase_Substrate_Dataset (.gz or not; its
    licence lines before the header are skipped), KSEAapp's PSP&NetworKIN file, or any table with a kinase gene,
    a substrate gene (or accession) and a residue column (S102)."""
    path = Path(path)
    lines = _text(path).splitlines()
    head_at = next((i for i, ln in enumerate(lines[:200]) if re.search(r"SUB_MOD_RSD|SUB_GENE", ln, re.I)), None)
    if head_at is None:
        raise DownloadError(f"{path.name}: not a kinase-substrate table. It needs PhosphoSitePlus's columns (GENE or "
                            "KINASE, SUB_GENE or SUB_ACC_ID, SUB_MOD_RSD), as in Kinase_Substrate_Dataset or KSEAapp's "
                            "PSP&NetworKIN file")
    first = lines[head_at]
    delim = max(("\t", ",", ";"), key=first.count)
    table = list(csv.reader(io.StringIO("\n".join(lines[head_at:])), delimiter=delim))
    head = [h.strip().upper().replace(" ", "_") for h in table[0]]
    col = {k: next((head.index(c) for c in cands if c in head), None) for k, cands in _KS_COLS.items()}
    if col["kinase"] is None or col["site"] is None or (col["sub_gene"] if settings.ksea_match == "gene"
                                                        else col["sub_acc"]) is None:
        need = "SUB_GENE" if settings.ksea_match == "gene" else "SUB_ACC_ID"
        raise DownloadError(f"{path.name}: the columns GENE (or KINASE), {need} and SUB_MOD_RSD are needed "
                            f"(ksea_match: {settings.ksea_match})")
    org = (settings.ksea_organism or "").strip().lower()
    out = KinaseSubstrates(path.name, settings.ksea_match, {})
    kin_set = set()
    skipped_org = skipped_src = bad = 0

    def cell(r, k):
        j = col[k]
        return r[j].strip() if j is not None and j < len(r) else ""

    for r in table[1:]:
        if not any(c.strip() for c in r):
            continue
        out.rows_read += 1
        if org and ((col["kin_org"] is not None and cell(r, "kin_org").lower() != org)
                    or (col["sub_org"] is not None and cell(r, "sub_org").lower() != org)):
            skipped_org += 1
            continue
        source = cell(r, "source") or "PhosphoSitePlus"
        if settings.ksea_networkin:
            sc = cell(r, "score")
            score = math.inf if sc.lower() in ("inf", "+inf") else num(sc)
            if score is None:
                score = math.inf if "phosphositeplus" in source.lower() else None
            if score is None or score < settings.ksea_networkin_score:
                skipped_src += 1
                continue
        elif "PhosphoSitePlus" not in source:
            skipped_src += 1
            continue
        kin = cell(r, "kinase") or ""
        m = _RSD.match(cell(r, "site"))
        sub = cell(r, "sub_gene") if settings.ksea_match == "gene" else cell(r, "sub_acc").split("-")[0]
        if not kin or not m or not sub:
            bad += 1
            continue
        key = (sub.upper(), f"{m.group(1).upper()}{int(m.group(2))}")
        out.by_site.setdefault(key, []).append((kin, source))
        out.sources[source] = out.sources.get(source, 0) + 1
        kin_set.add(kin)
        out.rows_used += 1
    if not out.by_site:
        raise DownloadError(f"{path.name}: no row left to use (organism {org or 'any'}, "
                            f"{'NetworKIN ≥ ' + format(settings.ksea_networkin_score, 'g') if settings.ksea_networkin else 'PhosphoSitePlus rows only'})")
    out.kinases = len(kin_set)
    out.notes.append(f"kinase-substrate table {path.name}: {out.rows_used:,} relationships of {out.kinases:,} kinases"
                     + (f"; {skipped_org:,} rows of other organisms left out" if skipped_org else "")
                     + (f"; {skipped_src:,} rows not from the sources asked for" if skipped_src else "")
                     + (f"; {bad:,} rows without a readable site" if bad else ""))
    return out


# ---------------------------------------------------------------------------- KSEA --


def ksea_scores(sites: list[tuple[tuple[str, str], float]], ks: KinaseSubstrates, min_substrates: int) -> list[dict]:
    """KSEA for one comparison. sites: [((substrate key, residue+position), log2 fold change)], every measured site
    (a site listed twice counts twice in the mean and SD, as in KSEAapp). One dict per kinase with at least one
    substrate, sorted by z; fdr only for those with m >= min_substrates."""
    fcs = [fc for _k, fc in sites if fc is not None and math.isfinite(fc)]
    if len(fcs) < 3:
        return []
    mu, sd = statistics.fmean(fcs), statistics.stdev(fcs)
    if not sd > 0:
        return []
    per: dict[str, dict[tuple, list[float]]] = {}
    for key, fc in sites:
        if fc is None or not math.isfinite(fc):
            continue
        for kin, source in ks.by_site.get(key, ()):
            per.setdefault(kin, {}).setdefault((key, source), []).append(fc)
    out = []
    for kin, subs in per.items():
        means = [statistics.fmean(v) for v in subs.values()]
        m = len(means)
        ms = statistics.fmean(means)
        z = (ms - mu) * math.sqrt(m) / sd
        p1 = 0.5 * math.erfc(abs(z) / math.sqrt(2))
        out.append({"kinase": kin, "m": m, "mS": ms, "enrichment": ms / abs(mu) if mu else math.nan, "z": z,
                    "p": min(1.0, 2 * p1), "p_one_sided": p1, "fdr": None,
                    "substrates": sorted(f"{k[0]} {k[1]}" for (k, _s) in subs)})
    scored = [r for r in out if r["m"] >= min_substrates]
    for r, q in zip(scored, stats.bh_adjust([r["p"] for r in scored]), strict=True):
        r["fdr"] = q
    out.sort(key=lambda r: (r["fdr"] is None, -abs(r["z"]), r["kinase"]))
    return out


def run_ksea(p, diffs: list, settings, ks: KinaseSubstrates) -> dict:
    """KSEA for every site comparison: {"ran", "file", "comparisons": [{name, sites, matched, kinases: [...]}]}."""
    parts = [site_parts(f) for f in p.m.features]
    key_of = [((acc if ks.match == "protein" else gene), rsd) for acc, gene, rsd in parts]
    comps = []
    for d in diffs:
        sites = [(key_of[r["index"]], r["log2fc"]) for r in d.rows if r["log2fc"] is not None]
        res = ksea_scores(sites, ks, settings.ksea_min_substrates)
        matched = sum(1 for k, _fc in sites if k in ks.by_site)
        for r in res:
            r["significant"] = ("up" if r["z"] > 0 else "down") if (
                r["fdr"] is not None and r["fdr"] <= settings.alpha) else ""
        comps.append({"name": d.name, "slug": d.slug(), "sites": len(sites), "matched": matched,
                      "scored": sum(1 for r in res if r["fdr"] is not None), "kinases": res,
                      "corrected": bool(d.correction)})
    return {"ran": any(c["kinases"] for c in comps), "file": ks.file, "match": ks.match,
            "min_substrates": settings.ksea_min_substrates, "networkin": settings.ksea_networkin,
            "networkin_score": settings.ksea_networkin_score if settings.ksea_networkin else None,
            "organism": settings.ksea_organism, "relationships": ks.rows_used, "kinases_in_file": ks.kinases,
            "sources": ks.sources, "alpha": settings.alpha, "comparisons": comps, "notes": list(ks.notes)}


def ksea_rows(info: dict) -> list[dict]:
    """results/kinase_activity.tsv: one row per comparison and kinase."""
    out = []
    for c in info.get("comparisons") or []:
        for r in c["kinases"]:
            out.append({"comparison": c["name"], **{k: r.get(k) for k in KSEA_COLUMNS if k != "substrates"},
                        "substrates": "; ".join(r["substrates"])})
    return out


# -------------------------------------------------------------------------- STRING --


@dataclass
class Network:
    file: str
    edges: dict[str, dict[str, int]]   # gene -> {partner gene: combined score}, both directions
    min_score: int
    notes: list[str] = field(default_factory=list)


def load_string(path: Path, genes: set[str], min_score: int) -> Network:
    """The interactions among `genes` (upper case) with a combined score ≥ min_score, from a STRING download:
    protein.links(.detailed / .full) with the protein.info file next to it (or in the same folder), or a network
    exported from the website (#node1, node2, ..., combined_score)."""
    path = Path(path)
    text = _text(path)
    lines = text.splitlines()
    if not lines:
        raise DownloadError(f"{path.name} is empty")
    head = re.split(r"[\t ]+", lines[0].lstrip("#").strip())
    low = [h.lower() for h in head]
    edges: dict[str, dict[str, int]] = {}

    def add(a: str, b: str, s: int) -> None:
        if a == b:
            return
        edges.setdefault(a, {})[b] = max(s, edges.get(a, {}).get(b, 0))
        edges.setdefault(b, {})[a] = max(s, edges.get(b, {}).get(a, 0))

    if "node1" in low and "node2" in low:  # the website's export: names already
        i1, i2 = low.index("node1"), low.index("node2")
        isc = low.index("combined_score") if "combined_score" in low else None
        if isc is None:
            raise DownloadError(f"{path.name}: a STRING export needs a combined_score column")
        for ln in lines[1:]:
            parts = ln.split("\t")
            if len(parts) <= max(i1, i2, isc):
                continue
            a, b = parts[i1].strip().upper(), parts[i2].strip().upper()
            sc = num(parts[isc])
            if sc is None:
                continue
            sc = int(round(sc * 1000)) if sc <= 1 else int(sc)
            if sc >= min_score and a in genes and b in genes:
                add(a, b, sc)
        return Network(path.name, edges, min_score, [f"STRING network {path.name} (website export)"])
    if low[:2] != ["protein1", "protein2"] or "combined_score" not in low:
        raise DownloadError(f"{path.name}: not a STRING file. Give protein.links (protein1 protein2 combined_score) "
                            "with its protein.info file in the same folder, or a network exported from the website")
    info = _string_info(path)
    ids = {sid for sid, name in info.items() if name in genes}
    isc = low.index("combined_score")
    n = 0
    for ln in lines[1:]:
        parts = ln.split()
        if len(parts) <= isc or parts[0] not in ids or parts[1] not in ids:
            continue
        sc = int(parts[isc]) if parts[isc].isdigit() else 0
        if sc >= min_score:
            add(info[parts[0]], info[parts[1]], sc)
            n += 1
    return Network(path.name, edges, min_score, [f"STRING network {path.name} with names from its protein.info file"])


def _string_info(links: Path) -> dict[str, str]:
    """STRING id -> preferred name, from the protein.info file next to a protein.links file."""
    folder = links.parent
    stem = links.name.split(".protein.links")[0]
    cands = sorted(folder.glob(f"{stem}.protein.info*")) or sorted(folder.glob("*protein.info*"))
    if not cands:
        raise DownloadError(f"{links.name}: the STRING protein.info file (the names of the proteins) must be in the "
                            f"same folder ({folder}); download it with the links file")
    out = {}
    for ln in _text(cands[0]).splitlines()[1:]:
        parts = ln.split("\t")
        if len(parts) >= 2:
            out[parts[0].strip()] = parts[1].strip().upper()
    if not out:
        raise DownloadError(f"{cands[0].name} has no proteins")
    return out


def hit_genes(diffs: list) -> set[str]:
    from ionomos.downstream.enrich import gene_symbol

    return {gene_symbol(r["label"]) for d in diffs for r in d.rows if r["significant"] and r["label"]}


def string_partners(diffs: list, net: Network, limit: int = 400) -> dict:
    """Per comparison: each hit gene with its partners among the same comparison's hits."""
    from ionomos.downstream.enrich import gene_symbol

    comps = []
    for d in diffs:
        dirs: dict[str, str] = {}
        for r in d.rows:
            if r["significant"] and r["label"]:
                g = gene_symbol(r["label"])
                dirs[g] = r["significant"] if dirs.get(g, r["significant"]) == r["significant"] else "both"
        rows = []
        for g in sorted(dirs):
            partners = {b: s for b, s in net.edges.get(g, {}).items() if b in dirs}
            if partners:
                order = sorted(partners, key=lambda b: (-partners[b], b))
                rows.append({"gene": g, "direction": dirs[g], "partners": order, "scores": [partners[b] for b in order]})
        rows.sort(key=lambda r: (-len(r["partners"]), r["gene"]))
        edges = sum(len(r["partners"]) for r in rows) // 2
        comps.append({"name": d.name, "hits": len(dirs), "connected": len(rows), "edges": edges, "genes": rows[:limit]})
    return {"ran": True, "file": net.file, "min_score": net.min_score, "comparisons": comps, "notes": net.notes}


def string_rows(info: dict) -> list[dict]:
    out = []
    for c in info.get("comparisons") or []:
        for r in c["genes"]:
            out.append({"comparison": c["name"], "gene": r["gene"], "direction": r["direction"],
                        "partners": "; ".join(f"{b} ({s})" for b, s in zip(r["partners"], r["scores"], strict=True)),
                        "n_partners": len(r["partners"]), "best_score": max(r["scores"])})
    return out


# ------------------------------------------------------------------------ outputs --


def _r(v, d: int = 4):
    """Rounded for the report; a small p-value keeps 4 significant digits instead of becoming 0."""
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return None
    return float(f"{v:.4g}") if v != 0 and abs(v) < 1e-4 else round(v, d)


def report_payload(local: dict | None, ksea: dict | None, net: dict | None, correction: dict | None) -> dict:
    """The report's "phos" data (report.js renderPhos): the localisation filter, kinase activity per comparison
    (kinases with enough substrates: name, m, mS, z, p, fdr), STRING partners per comparison."""
    out: dict = {"ran": bool(local), "loc": local or {}}
    if ksea and ksea.get("comparisons"):
        out["ksea"] = {k: ksea.get(k) for k in ("ran", "file", "match", "min_substrates", "networkin",
                                                 "networkin_score", "relationships", "alpha", "reason")}
        out["ksea"]["comps"] = [
            {"name": c["name"], "slug": c["slug"], "sites": c["sites"], "matched": c["matched"],
             "k": [[r["kinase"], r["m"], _r(r["mS"]), _r(r["z"], 3), _r(r["p"], 8), _r(r["fdr"], 8), r["significant"],
                    r["substrates"][:40]] for r in c["kinases"] if r["fdr"] is not None]}
            for c in ksea["comparisons"]]
    elif ksea:
        out["ksea"] = {"ran": False, "reason": ksea.get("reason", "")}
    if net:
        out["string"] = net if not net.get("ran") else {
            **{k: net[k] for k in ("ran", "file", "min_score")},
            "comps": [{"name": c["name"], "hits": c["hits"], "connected": c["connected"], "edges": c["edges"],
                       "genes": [[r["gene"], r["direction"], r["partners"][:30], r["scores"][:30]] for r in c["genes"][:200]]}
                      for c in net["comparisons"]]}
    if correction:
        out["corrected"] = bool(correction.get("ran"))
    return out


# ---------------------------------------------------------------- analyze() stages --


def read_sites(method: str | None, workdir: Path, dest: Path, record, settings, sample_map: dict | None
               ) -> tuple[QuantMatrix | None, list[str], list[tuple[str, str, str]]]:
    """analysis.phospho: the site table as the experiment's quantities, or None (then the proteins are analysed
    as before, and a problem says why). Returns (matrix, notes, [(issue code, severity, message)])."""
    from ionomos.downstream import sdrfdesign

    path, kind = find_table(Path(workdir), dest, method, settings)
    if path is None:
        where = (f"analysis.phospho_table {settings.phospho_table!r} was not found" if settings.phospho_table else
                 f"no phosphosite table in {Path(workdir).name}/ (combined_site_STY_79.9663.tsv, "
                 "abundance_single-site_MD.tsv or report.phosphosites_90.tsv)")
        return None, [], [("PHOSPHO_TABLE", "input", f"phospho is on, but {where}; the proteins were analysed instead")]
    try:
        m = load(path, kind, settings, Path(workdir), record, sample_map)
    except SiteTableError as exc:
        return None, [], [("PHOSPHO_TABLE", "input", f"{exc}; the proteins were analysed instead")]
    m, dnotes = sdrfdesign.load_into(m, Path(dest), Path(workdir), None, settings.sdrf_factor)
    problems = []
    info = m.meta.get("phospho") or {}
    if info.get("problem"):
        problems.append(("PHOSPHO_LOCALISATION", "warning", info["problem"]))
    return m, dnotes, problems


def applies(m) -> bool:
    return m is not None and m.level == "site" and m.kind == "intensity" and bool(m.meta.get("phospho"))


def stage(p, diffs: list, settings, results: Path, dest: Path) -> dict:
    """Kinase activity (KSEA) on phosphosite comparisons and STRING partners among the hits of any comparison:
    {"ksea", "string", "files", "problems", "notes"}. Each part only when its download is named."""
    from ionomos.downstream.tables import write_tsv

    out: dict = {"ksea": None, "string": None, "files": [], "problems": [], "notes": []}
    site = applies(p.m)
    if settings.kinase_substrates and not site:
        out["ksea"] = {"ran": False, "reason": "kinase activity needs phosphosites (analysis.phospho: true)"}
    elif settings.kinase_substrates and diffs:
        path = find_download(settings.kinase_substrates, dest)
        if path is None:
            msg = (f"kinase_substrates: {settings.kinase_substrates!r} was not found (a full path, or a file in "
                   f"{Path(dest).name}/)")
            out["ksea"] = {"ran": False, "reason": msg}
            out["problems"].append(("KINASE_SUBSTRATES", "warning", msg))
        else:
            try:
                ks = load_kinase_substrates(path, settings)
            except DownloadError as exc:
                out["ksea"] = {"ran": False, "reason": str(exc)}
                out["problems"].append(("KINASE_SUBSTRATES", "warning", str(exc)))
            else:
                info = run_ksea(p, diffs, settings, ks)
                out["ksea"] = info
                out["notes"] += info["notes"]
                if info["ran"]:
                    out["files"].append(write_tsv(results / "kinase_activity.tsv", ["comparison", *KSEA_COLUMNS],
                                                  ksea_rows(info)))
                    info["table"] = "results/kinase_activity.tsv"
                if not any(c["scored"] for c in info["comparisons"]):
                    matched = max((c["matched"] for c in info["comparisons"]), default=0)
                    msg = (f"{ks.file}: {matched:,} measured sites are substrates in the table, and no kinase has "
                           f"{settings.ksea_min_substrates} or more (ksea_min_substrates), so no kinase was scored"
                           + ("; with ksea_match: gene both need the same gene names" if ks.match == "gene" else ""))
                    info["reason"] = msg
                    out["problems"].append(("KINASE_SUBSTRATES", "warning", msg))
    if settings.string_network and diffs:
        path = find_download(settings.string_network, dest)
        if path is None:
            msg = (f"string_network: {settings.string_network!r} was not found (a full path, or a file in "
                   f"{Path(dest).name}/)")
            out["string"] = {"ran": False, "reason": msg}
            out["problems"].append(("STRING_NETWORK", "warning", msg))
        else:
            try:
                net = load_string(path, hit_genes(diffs), settings.string_min_score)
            except DownloadError as exc:
                out["string"] = {"ran": False, "reason": str(exc)}
                out["problems"].append(("STRING_NETWORK", "warning", str(exc)))
            else:
                info = string_partners(diffs, net)
                out["string"] = info
                out["notes"] += info["notes"]
                rows = string_rows(info)
                if rows:
                    out["files"].append(write_tsv(results / "string_partners.tsv", STRING_COLUMNS, rows))
                    info["table"] = "results/string_partners.tsv"
    return out


def summary(m, got: dict | None, correction: dict | None = None) -> dict:
    """analysis.json "phospho": the localisation filter, the kinase activity (the kinases called per comparison)
    and the STRING partners in a few fields."""
    info = (m.meta.get("phospho") if m is not None else None) or {}
    out: dict = {"ran": bool(info)}
    if info:
        out["localisation"] = {k: v for k, v in info.items() if k != "hist"}
    else:
        out["reason"] = "not asked for (analysis.phospho)"
    k = (got or {}).get("ksea")
    if k:
        out["kinase_activity"] = {kk: k.get(kk) for kk in ("ran", "reason", "file", "match", "min_substrates",
                                                              "networkin", "networkin_score", "organism",
                                                              "relationships", "table") if kk in k}
        if k.get("comparisons"):
            out["kinase_activity"]["method"] = ("KSEA (Casado et al. 2013, as KSEAapp 2.0): z = (mS - mean) * sqrt(m) "
                                                "/ SD over every site's log2FC; p two-sided; BH over the kinases with "
                                                f"m ≥ {k['min_substrates']}")
            out["kinase_activity"]["comparisons"] = [
                {"name": c["name"], "sites": c["sites"], "substrate_sites": c["matched"], "kinases_scored": c["scored"],
                 "up": [r["kinase"] for r in c["kinases"] if r["significant"] == "up"],
                 "down": [r["kinase"] for r in c["kinases"] if r["significant"] == "down"]}
                for c in k["comparisons"]]
    s = (got or {}).get("string")
    if s:
        out["string"] = {kk: s.get(kk) for kk in ("ran", "reason", "file", "min_score", "table") if kk in s}
        if s.get("comparisons"):
            out["string"]["comparisons"] = [{"name": c["name"], "hits": c["hits"], "connected": c["connected"],
                                             "edges": c["edges"]} for c in s["comparisons"]]
    if correction is not None and info:
        out["protein_correction"] = bool(correction.get("ran"))
    return out
