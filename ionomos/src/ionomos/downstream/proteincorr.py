"""
Site changes corrected for protein abundance (ROADMAP 5C #3, D70): MSstatsPTM's adjustment, on isoDTB site ratios.

A probe-labelled cysteine's heavy / light ratio moves when the compound engages the site, and also when the
compound changes how much of the protein there is (degradation, expression). An unenriched proteome of the same
treatment tells the two apart: the site's change minus its protein's change is the change in the site itself.
Where that proteome comes from is the lab's choice, so it is named explicitly and nothing is guessed:

    analysis:
      protein_correction:
        proteome: D:/Fragpipe_General/EJQ/20261001-DIA_EJQ-2-030   # an analysed Ionomos experiment (its folder or
                                                                   #   results/), or a protein table: MSstats
                                                                   #   groupComparison output (Protein, Label, log2FC,
                                                                   #   SE, DF) or an Ionomos *_differential.tsv
        match: gene                  # gene (gene names) | protein (UniProt accessions, isoforms joined)
        conditions:                  # site condition -> the proteome comparison of the same compound, compound
          EJQ_2_027: Cmpd vs DMSO    #   first; a condition not named here needs a comparison whose treatment has
                                     #   its name, else it is not corrected (PROTEIN_CORRECTION_CONDITIONS)

Off unless `proteome` is set. A relative path is read from the experiment folder. The proteome is only read.

Scale. Site ratios are log2 heavy / light. A proteome comparison "Cmpd vs DMSO" is log2(Cmpd / DMSO); on the
site's scale that is -log2FC when the compound-treated sample carries the light tag (liganded_direction: high,
R = heavy / light) and +log2FC when it carries the heavy tag (low). A proteome analysed as ratios (a comparison
"X (log2 H/L vs 0)") is on the heavy / light scale already and is used as it is.

Per site and condition, as MSstatsPTM's .adjustProteinLevel and .applyPtmAdjustment (2.14.0; checked against
them in tests/test_protein_correction.py, goldens from tests/golden/ptm/run_msstatsptm.R):

    log2FC = site log2FC - protein log2FC
    SE     = sqrt(SE_site^2 + SE_protein^2)
    DF     = (SE_site^2 + SE_protein^2)^2 / (SE_site^4 / DF_site + SE_protein^4 / DF_protein)   (Satterthwaite)
    t      = log2FC / SE, p two-sided on DF; Benjamini-Hochberg per condition over the sites that were tested

The site's log2FC, SE and DF are the moderated one-sample test's (limma: posterior SE, residual + prior df);
the protein's are its comparison's. Both are already moderated, so nothing is moderated again (MSstatsPTM does
the same). Sites whose protein is not in the proteome (or is there twice under the key) keep their uncorrected
result and are flagged; MSstatsPTM leaves them out. The liganded calls (cys.py) stay on the site ratios and show
the protein's ratio beside them.
"""
from __future__ import annotations

import csv
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from ionomos.downstream import fpa, stats
from ionomos.downstream.tables import num, read_tsv

SUFFIX = "protein-corrected"
STATUSES = ("corrected", "protein not found", "protein ambiguous", "protein without SE", "site not tested")
CORRECTION_COLUMNS = ["protein_key", "protein_status", "protein_comparison", "protein_log2_hl", "protein_se",
                      "protein_df", "site_log2fc", "site_se", "site_df", "site_pvalue", "site_qvalue"]
_UNIPROT = re.compile(r"(?:^|\|)(?:sp|tr)\|([^|]+)\|")
_ACCESSION = re.compile(r"^[A-Z0-9]{4,12}(?:-\d+)?$", re.I)
MSSTATS = ("protein", "label", "log2fc", "se", "df")
LIMIT_BYTES = 512 * 1024**2


class ProteomeError(ValueError):
    pass


@dataclass
class Comparison:
    name: str
    treatment: str
    control: str                  # "" for ratios tested against 0
    scale: str                    # "fc": log2(treatment / control) | "hl": log2 heavy / light
    rows: dict[str, tuple] = field(default_factory=dict)   # key -> (log2fc, se, df, protein id as written)
    ambiguous: set[str] = field(default_factory=set)       # keys found on more than one row
    without_se: int = 0


@dataclass
class Proteome:
    path: Path
    kind: str                     # "ionomos" | "msstats" | "differential"
    match: str
    comparisons: list[Comparison]
    notes: list[str] = field(default_factory=list)

    def names(self) -> list[str]:
        return [c.name for c in self.comparisons]


@dataclass
class Result:
    diffs: list                   # analysis.DiffResult, one per corrected site condition
    summary: dict
    protein_hl: dict[str, dict[int, float]]   # condition -> {site index: the protein's log2 H/L}
    problems: list[tuple[str, str, str]] = field(default_factory=list)   # (issue code, severity, message)
    notes: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ keys --


def canonical(acc: str) -> str:
    return acc.strip().split("-")[0].upper()


def protein_keys(text: str) -> list[str]:
    """UniProt accessions in a protein id ('sp|P04406|G3P_HUMAN', 'P04406-2', 'P1;P2'), isoforms joined."""
    out = []
    for part in re.split(r"[;,]\s*", text or ""):
        part = part.strip()
        if not part:
            continue
        m = _UNIPROT.search(part + "|")
        acc = m.group(1) if m else part
        if m or _ACCESSION.match(acc):
            key = canonical(acc)
            if key not in out:
                out.append(key)
    return out


def gene_keys(text: str) -> list[str]:
    out = []
    for part in re.split(r"[;,]\s*", text or ""):
        part = part.strip().upper()
        if part and part not in ("NA", "NAN") and part not in out:
            out.append(part)
    return out


def site_keys(feature, match: str) -> list[str]:
    """A site's protein keys: its accession (from 'sp|P04406|G3P_HUMAN|C152') or its gene ('GAPDH C152')."""
    from ionomos.downstream.cys import site_key

    if match == "protein":
        acc = site_key(feature.id)[0]
        return protein_keys(acc) or ([canonical(acc)] if acc else [])
    name = feature.label.rsplit(" ", 1)[0] if " " in feature.label else feature.label
    return gene_keys(name)


# ---------------------------------------------------------------- reading --


def find(name: str, dest: Path | None) -> Path | None:
    """analysis.protein_correction.proteome: an absolute path, or one relative to the experiment folder."""
    if not name:
        return None
    p = Path(name)
    if not p.is_absolute():
        if dest is None:
            return None
        p = Path(dest) / p
    return p if p.exists() else None


def _df(v) -> float | None:
    if v is not None and str(v).strip().lower() in ("inf", "+inf", "infinity"):
        return math.inf
    return num(v)


def _df_from_quantile(q: float) -> float:
    """The df whose two-sided 95 % t quantile is q (a CI half-width over its SE): for tables without a df column."""
    from ionomos.downstream.rrandom import qnorm

    if not math.isfinite(q) or q <= qnorm(0.975) + 1e-9:
        return math.inf
    lo, hi = 0.05, 1e7
    if stats.qt_upper(0.05, hi) > q:
        return math.inf
    for _ in range(200):
        mid = math.sqrt(lo * hi)
        if stats.qt_upper(0.05, mid) > q:
            lo = mid
        else:
            hi = mid
        if hi / lo < 1 + 1e-12:
            break
    return math.sqrt(lo * hi)


def _estimate(row: dict, cols: dict[str, str]) -> tuple[float | None, float | None, float | None]:
    """(log2fc, se, df) from a result row; se / df from t and the 95 % interval when the table has no se / df."""
    fc = num(row.get(cols["log2fc"]))
    se = num(row.get(cols["se"])) if cols.get("se") else None
    df = _df(row.get(cols["df"])) if cols.get("df") else None
    if fc is not None and (se is None or df is None) and cols.get("t") and cols.get("ci_high"):
        t, hi = num(row.get(cols["t"])), num(row.get(cols["ci_high"]))
        if t not in (None, 0.0) and hi is not None:
            se = abs(fc / t)
            df = _df_from_quantile((hi - fc) / se) if se > 0 else None
    if se is not None and (se <= 0 or not math.isfinite(se)):
        se = None
    return fc, se, df


def _add(comp: Comparison, keys: list[str], est: tuple, pid: str) -> None:
    fc, se, df = est
    if fc is None:
        return
    if se is None or df is None:
        comp.without_se += 1
    for k in keys:
        if k in comp.rows and comp.rows[k][3] != pid:
            comp.ambiguous.add(k)
        comp.rows[k] = (fc, se, df, pid)


def _split(name: str) -> tuple[str, str, str]:
    """'Cmpd vs DMSO' -> (Cmpd, DMSO, fc); 'X (log2 H/L vs 0)' -> (X, '', hl); MSstats 'Cmpd-DMSO' too."""
    from ionomos.downstream.analysis import _split_name

    m = re.match(r"^(.*?) \(log2 H/L vs 0(?:, .*)?\)$", name)
    if m:
        return m.group(1), "", "hl"
    a, b = _split_name(name)
    return a, b, "fc"


def _read_ionomos(results: Path, match: str) -> Proteome:
    try:
        info = json.loads((results / "analysis.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProteomeError(f"{results / 'analysis.json'} can't be read: {exc}") from exc
    if info.get("level") == "site":
        raise ProteomeError(f"{results.parent.name}: this is a site-level analysis (isoDTB), not a proteome; give the "
                            "analysed unenriched (protein) experiment")
    out = Proteome(results, "ionomos", match, [])
    for c in info.get("comparisons") or []:
        name = str(c.get("name", ""))
        if SUFFIX in name or name.endswith(" vs others"):
            continue
        tsv = results / Path(str(c.get("table") or "")).name
        if not tsv.is_file():
            out.notes.append(f"{name}: {tsv.name} is missing from {results}")
            continue
        header, rows = read_tsv(tsv)
        cols = {k: k for k in ("log2fc", "se", "df", "t", "ci_high") if k in header}
        if "log2fc" not in cols:
            out.notes.append(f"{name}: {tsv.name} has no log2fc column")
            continue
        t, ctl, scale = _split(name)
        comp = Comparison(name, t, ctl, scale)
        for r in rows:
            keys = protein_keys(r.get("id", "")) if match == "protein" else gene_keys(r.get("label", ""))
            _add(comp, keys, _estimate(r, cols), r.get("id", ""))
        out.comparisons.append(comp)
    if not out.comparisons:
        raise ProteomeError(f"{results}: the analysis has no comparison with a results table to correct by")
    return out


def _read_table(path: Path, match: str) -> Proteome:
    try:
        if path.stat().st_size > LIMIT_BYTES:
            raise ProteomeError(f"{path.name} is larger than {LIMIT_BYTES // 2**20} MB")
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        raise ProteomeError(f"{path.name} can't be read: {exc}") from exc
    lines = text.splitlines()
    first = next((ln for ln in lines if ln.strip()), "")
    delim = max(("\t", ",", ";"), key=first.count)
    table = [r for r in csv.reader(lines, delimiter=delim) if any(c.strip() for c in r)]
    if len(table) < 2:
        raise ProteomeError(f"{path.name} has no rows")
    head = [h.strip() for h in table[0]]
    low = {h.lower().replace(".", "").replace("_", ""): h for h in head}
    rows = [dict(zip(head, r + [""] * (len(head) - len(r)), strict=False)) for r in table[1:]]
    if all(k in low for k in MSSTATS):  # MSstats / MSstatsTMT groupComparison $ComparisonResult
        gene_col = next((low[k] for k in ("gene", "genes", "genename", "genenames", "genesymbol") if k in low), None)
        cols = {"log2fc": low["log2fc"], "se": low["se"], "df": low["df"]}
        out = Proteome(path, "msstats", match, [])
        comps: dict[str, Comparison] = {}
        for r in rows:
            label = r.get(low["label"], "").strip()
            if not label:
                continue
            if label not in comps:
                t, ctl, _scale = _split(label.replace(" - ", "-"))
                if not ctl and "-" in label:
                    t, ctl = (x.strip() for x in label.split("-", 1))
                comps[label] = Comparison(label, t, ctl, "fc")
            prot = r.get(low["protein"], "")
            if match == "protein":
                keys = protein_keys(prot)
            else:
                keys = gene_keys(r.get(gene_col, "")) if gene_col else gene_keys(prot)
            _add(comps[label], keys, _estimate(r, cols), prot)
        out.comparisons = list(comps.values())
        if match == "gene" and not gene_col:
            out.notes.append(f"{path.name} has no gene column, so its Protein column was matched as gene names")
        return out
    names = {h.lower(): h for h in head}
    if "log2fc" in names and ("id" in names or "label" in names):  # one Ionomos comparison, or a table like it
        cols = {k: names[k] for k in ("log2fc", "se", "df", "t", "ci_high") if k in names}
        stem = re.sub(r"_differential$", "", path.stem)
        t, ctl, scale = _split(stem.replace("_vs_", " vs ").replace("_", " ") if "_vs_" in stem else stem)
        comp = Comparison(stem, t, ctl, scale)
        for r in rows:
            keys = (protein_keys(r.get(names.get("id", ""), "")) if match == "protein"
                    else gene_keys(r.get(names.get("label", ""), "")))
            _add(comp, keys, _estimate(r, cols), r.get(names.get("id", ""), ""))
        return Proteome(path, "differential", match, [comp])
    raise ProteomeError(f"{path.name}: not a protein table Ionomos can correct by. It needs MSstats' columns (Protein, "
                        "Label, log2FC, SE, DF), or an Ionomos *_differential.tsv (id, label, log2fc, se, df)")


def load(path: Path, match: str = "gene") -> Proteome:
    """The proteome named by analysis.protein_correction: an analysed Ionomos experiment, or a protein table."""
    from ionomos.downstream.compare import find_analysis

    path = Path(path)
    if path.is_dir():
        results = find_analysis(path)
        if results is None:
            raise ProteomeError(f"{path} holds no Ionomos analysis (no results/analysis.json). Analyse the proteome "
                                "first, or name its protein table")
        return _read_ionomos(results, match)
    return _read_table(path, match)


# ------------------------------------------------------------- adjustment --


def adjust(s_fc: float, s_se: float, s_df: float, p_fc: float, p_se: float, p_df: float
           ) -> tuple[float, float, float, float, float]:
    """MSstatsPTM .adjustProteinLevel for one site: (log2FC, SE, DF, t, p)."""
    fc = s_fc - p_fc
    s2, r2 = s_se * s_se, p_se * p_se
    se = math.sqrt(s2 + r2)
    denom = (s2 * s2 / s_df if math.isfinite(s_df) else 0.0) + (r2 * r2 / p_df if math.isfinite(p_df) else 0.0)
    df = (s2 + r2) ** 2 / denom if denom > 0 else math.inf
    t = fc / se if se > 0 else math.nan
    if math.isnan(t):
        return fc, se, df, t, math.nan
    if math.isinf(df):
        p = math.erfc(abs(t) / math.sqrt(2))
    else:
        p = stats.t_two_sided_p(t, df)
    return fc, se, df, t, p


def _pick(proteome: Proteome, condition: str, given: dict[str, str]) -> tuple[Comparison | None, str]:
    """The proteome comparison for a site condition: named in `conditions`, else the one whose treatment has the
    condition's name. (comparison or None, why not)."""
    low = {k.lower(): v for k, v in given.items()}
    want = low.get(condition.lower())
    comps = proteome.comparisons
    if want is not None:
        hit = [c for c in comps if c.name.lower() == want.lower()] or \
              [c for c in comps if c.treatment.lower() == want.lower()]
        if len(hit) == 1:
            return hit[0], ""
        return None, (f"{condition}: protein_correction.conditions names {want!r}, which " +
                      ("matches several proteome comparisons" if hit else "is not a comparison of the proteome"))
    hit = [c for c in comps if c.treatment.lower() == condition.lower()]
    if len(hit) == 1:
        return hit[0], ""
    if hit:
        return None, (f"{condition}: the proteome has {len(hit)} comparisons of {condition} "
                      f"({', '.join(c.name for c in hit)}); name one in protein_correction.conditions")
    return None, f"{condition}: no proteome comparison has this name"


def to_hl(comp: Comparison, fc: float, direction: str) -> float:
    """A protein's change on the site's heavy / light scale (see the module docstring)."""
    if comp.scale == "hl":
        return fc
    return -fc if direction == "high" else fc


def run(p, diffs: list, settings, proteome: Proteome) -> Result:
    """The corrected comparisons for every site condition that has its protein comparison. diffs: the site
    comparisons of analyze() (ratio vs 0)."""
    from ionomos.downstream import analysis

    m = p.m
    pc = settings.protein_correction
    given = pc.get("conditions") or {}
    keys = [site_keys(f, proteome.match) for f in m.features]
    out = Result([], {}, {})
    unmatched: list[str] = []
    per_cond = []
    for d in diffs:
        if d.control is not None or d.correction or d.kind != "ratio":
            continue
        comp, why = _pick(proteome, d.treatment, given)
        if comp is None:
            unmatched.append(why)
            per_cond.append({"site_condition": d.treatment, "proteome_comparison": None, "reason": why})
            continue
        rows, pvals, hl_of = [], [], {}
        counts = dict.fromkeys(STATUSES, 0)
        for r in sorted(d.rows, key=lambda x: x["index"]):
            i = r["index"]
            key = next((k for k in keys[i] if k in comp.rows), keys[i][0] if keys[i] else "")
            entry = comp.rows.get(key)
            row = {**{k: r[k] for k in ("index", "id", "label", "description", "n_treatment", "n_control")},
                   "imputed": 0, "mean_control": None, "protein_key": key, "protein_comparison": comp.name,
                   "site_log2fc": r["log2fc"], "site_se": r.get("se"), "site_df": r.get("df"),
                   "site_pvalue": r["pvalue"], "site_qvalue": r["qvalue"], "protein_log2_hl": None,
                   "protein_se": None, "protein_df": None, "log2fc": None, "ci_low": None, "ci_high": None,
                   "pvalue": None, "t": None, "se": None, "df": None, "mean_treatment": None}
            if entry is None:
                status = "protein not found"
            elif key in comp.ambiguous:
                status = "protein ambiguous"
            else:
                hl = to_hl(comp, entry[0], settings.liganded_direction)
                hl_of[i] = hl
                row.update(protein_log2_hl=hl, protein_se=entry[1], protein_df=entry[2])
                if r["log2fc"] is None:
                    status = "site not tested"
                elif r.get("se") is None or r.get("df") is None or r["pvalue"] is None:
                    status = "site not tested"
                    row["log2fc"] = row["mean_treatment"] = r["log2fc"] - hl
                elif entry[1] is None or entry[2] is None:
                    status = "protein without SE"
                    row["log2fc"] = row["mean_treatment"] = r["log2fc"] - hl
                else:
                    status = "corrected"
                    fc, se, df, t, pv = adjust(r["log2fc"], r["se"], r["df"], hl, entry[1], entry[2])
                    q = stats.qt_upper(0.05, df) if math.isfinite(df) else 1.959963984540054
                    row.update(log2fc=fc, se=se, df=df, t=t, pvalue=pv, mean_treatment=fc,
                               ci_low=fc - q * se, ci_high=fc + q * se)
            row["protein_status"] = status
            counts[status] += 1
            rows.append(row)
            pvals.append(math.nan if row["pvalue"] is None else row["pvalue"])
        qs = stats.bh_adjust(pvals)
        thr = None
        for row, q in zip(rows, qs, strict=True):
            row["qvalue"] = None if q != q else q
            score = row["qvalue"] if settings.use_adjusted else row["pvalue"]
            fc = row["log2fc"]
            row["significant"] = fpa.significant(fc if fc is not None else math.nan, score, settings.alpha,
                                                 settings.log2fc)
            if score is not None and score <= settings.alpha and row["pvalue"] is not None:
                thr = row["pvalue"] if thr is None else max(thr, row["pvalue"])
        if not settings.use_adjusted:
            thr = settings.alpha
        rows.sort(key=lambda x: (x["pvalue"] is None, x["pvalue"] if x["pvalue"] is not None else 1.0))
        cd = analysis.DiffResult(f"{d.treatment} (log2 H/L vs 0, {SUFFIX})", d.treatment, None, "ratio", rows,
                                 settings, thr)
        cd.test_used = SUFFIX
        cd.groups = d.groups
        cd.correction = {"proteome": str(proteome.path), "comparison": comp.name, "scale": comp.scale,
                         "match": proteome.match, "counts": counts}
        if d.confidence == "none" or not any(r["pvalue"] is not None for r in rows):
            if any(r["log2fc"] is not None for r in rows):
                analysis.fold_change_only(cd, f"{d.treatment}: the site ratios have no p-values"
                                          if d.confidence == "none" else
                                          f"{d.treatment}: no site has a protein with a standard error")
        out.diffs.append(cd)
        out.protein_hl[d.treatment] = hl_of
        measured = sum(1 for r in rows if r["site_log2fc"] is not None)
        found = sum(1 for r in rows if r["site_log2fc"] is not None and r["protein_status"] not in
                    ("protein not found", "protein ambiguous"))
        per_cond.append({"site_condition": d.treatment, "proteome_comparison": comp.name, "sites": len(rows),
                         "sites_measured": measured, "protein_found": found, **{s.replace(" ", "_"): n
                                                                                 for s, n in counts.items()},
                         "tested": cd.tested, "up": cd.up, "down": cd.down, "name": cd.name})
        if measured and found < 0.5 * measured:
            out.problems.append(("PROTEIN_CORRECTION", "warning",
                                 f"{d.treatment}: the protein of only {found:,} of {measured:,} sites was found in "
                                 f"{comp.name} (match: {proteome.match}); the others keep their uncorrected ratio"))
    if unmatched:
        comps = ", ".join(proteome.names()[:8]) + (" …" if len(proteome.comparisons) > 8 else "")
        out.problems.append(("PROTEIN_CORRECTION_CONDITIONS", "input",
                             "; ".join(unmatched) + f". The proteome's comparisons: {comps}. Those site conditions "
                             "were not corrected."))
    if comp_without := sum(c.without_se for c in proteome.comparisons):
        out.notes.append(f"protein correction: {comp_without:,} proteome rows have no standard error or df; their "
                         "sites get a corrected fold change but no p-value")
    out.notes += proteome.notes
    orient = ("protein log2FC (compound vs control) taken as -log2 H/L: the treated sample carries the light tag "
              "(liganded_direction: high)" if settings.liganded_direction == "high" else
              "protein log2FC (compound vs control) taken as +log2 H/L: the treated sample carries the heavy tag "
              "(liganded_direction: low)")
    out.summary = {"ran": bool(out.diffs), "proteome": str(proteome.path), "source": proteome.kind,
                   "match": proteome.match, "orientation": orient, "conditions": per_cond,
                   "method": "MSstatsPTM adjustment: site log2FC - protein log2FC, SE = sqrt(SE_site² + SE_protein²), "
                             "Satterthwaite df, BH per condition"}
    if not out.diffs:
        out.summary["reason"] = "no site condition could be matched to a proteome comparison"
    return out


def off(reason: str) -> dict:
    return {"ran": False, "reason": reason}
