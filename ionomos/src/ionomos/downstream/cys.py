"""
Cysteine chemoproteomics: which sites a compound engages (D52, ROADMAP 5C #3).

For site-level ratio data (isoDTB: log2 heavy/light per replicate), each condition is a compound competing
with the probe: a competition experiment by construction (roles.py gives every condition that role; the vehicle
is the other isotopic tag of the same run, so there is no control condition). A site is **liganded** when its competition ratio R (control / treated) reaches a
threshold in enough replicates; the usual rule is R >= 4 in at least 2 replicates.

    analysis:
      liganded: true               # false: no calls
      liganded_ratio: 4            # R (linear) a replicate must reach
      liganded_min_replicates: 2   # ... in at least this many replicates
      liganded_direction: high     # high: R = heavy / light (the compound-treated sample carries the light tag)
                                   # low:  R = light / heavy (it carries the heavy tag)
      site_annotation: cysdb.csv   # optional: a site table the lab downloaded (CysDB), for known / new sites

Per site and compound:
    liganded        R >= liganded_ratio in >= liganded_min_replicates replicates
    inconsistent    reached in some replicates, but fewer than that
    not liganded    measured in enough replicates, reached in none
    too few         measured in fewer replicates than needed

Across compounds: the liganded fraction of each (liganded / sites with enough replicates), and for each site
whether it is selective (liganded by one compound and measured as not liganded by every other), shared
(liganded by several) or unresolved (liganded by one, the others not measured well enough). Per protein: how
many of its quantified cysteines are liganded, since most of a protein's sites moving together points at the
protein amount rather than one site.

The calls are made on the ratios as measured (never normalised, never imputed). Nothing here is a p-value:
the moderated one-sample test in the Differential section answers "is the ratio different from 1"; this
answers the chemoproteomics convention "is the site engaged by at least 1 - 1/R".
"""
from __future__ import annotations

import csv
import math
import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path

CLASSES = ("liganded", "inconsistent", "not liganded", "too few")
DIRECTIONS = ("high", "low")
SITE_COLUMNS = ["id", "site", "protein", "position", "description"]
# a site key as CysDB writes it (UniProtKBID_CYS#): P04406_C152; also P04406_CYS152, P04406-2_C152, P04406_152
_SITE_KEY = re.compile(r"^([A-Z][A-Z0-9]{1,14}(?:-\d+)?)[_:|]\s*(?:C(?:YS)?)?(\d+)$", re.I)
_UNIPROT = re.compile(r"(?:^|\|)(?:sp|tr)\|([^|]+)\|")
_TRUE = {"1", "true", "yes", "y", "+", "x", "t"}
_FALSE = {"0", "false", "no", "n", "-", "", "na", "nan", "f"}
# annotation columns Ionomos reads a meaning into (CysDB's three measures), in the order they win
KNOWN_FLAGS = (("ligandable", "known liganded"), ("hyperreactive", "known hyperreactive"),
               ("identified", "seen before"))


@dataclass
class Compound:
    name: str
    samples: list[str]
    min_replicates: int                       # the rule used (the setting, or fewer when there are fewer samples)
    counts: dict[str, int] = field(default_factory=dict)
    other_side: int = 0                       # sites that would be liganded with the opposite direction

    @property
    def assessed(self) -> int:
        return sum(self.counts.get(k, 0) for k in CLASSES[:3])

    @property
    def fraction(self) -> float | None:
        return self.counts.get("liganded", 0) / self.assessed if self.assessed else None


@dataclass
class Result:
    ratio: float
    min_replicates: int
    direction: str
    compounds: list[Compound]
    rows: list[dict]                          # one per site (see table_rows)
    proteins: list[dict]
    selectivity: dict[str, int]
    annotation: dict
    notes: list[str] = field(default_factory=list)
    problems: list[tuple[str, str]] = field(default_factory=list)   # (severity, message) for the doctor


def site_key(feature_id: str) -> tuple[str, str, int | None]:
    """'sp|P04406|G3P_HUMAN|C152' -> ('P04406', 'C', 152); the protein as written when it is not UniProt-style."""
    protein, _, site = feature_id.rpartition("|")
    m = re.match(r"^([A-Za-z]?)(\d+)$", site)
    if not protein or not m:
        return feature_id, "", None
    acc = _UNIPROT.search(protein + "|")
    return (acc.group(1) if acc else protein), m.group(1).upper(), int(m.group(2))


def applies(m) -> bool:
    return m is not None and m.kind == "ratio" and m.level == "site" and bool(m.features)


def classify(values: list[float | None], log2_threshold: float, min_replicates: int) -> tuple[str, int, int]:
    """(class, replicates measured, replicates at or over the threshold) for one site in one compound. values are
    oriented log2 competition ratios (positive = competed)."""
    obs = [v for v in values if v is not None]
    over = sum(1 for v in obs if v >= log2_threshold - 1e-12)
    if len(obs) < min_replicates:
        return "too few", len(obs), over
    if over >= min_replicates:
        return "liganded", len(obs), over
    return ("inconsistent" if over else "not liganded"), len(obs), over


def run(p, settings, annotation: dict | None = None) -> Result:
    """Liganded-site calls on a processed site-ratio matrix (fpa.Processed). annotation: load_annotation()."""
    m = p.m
    sign = 1.0 if settings.liganded_direction == "high" else -1.0
    thr = math.log2(settings.liganded_ratio)
    notes: list[str] = []
    problems: list[tuple[str, str]] = []
    compounds: list[Compound] = []
    cols: dict[str, list[int]] = {}
    for c in m.conditions:
        idx = [j for j, s in enumerate(m.samples) if m.condition[s] == c]
        cols[c] = idx
        need = min(settings.liganded_min_replicates, len(idx))
        if need < settings.liganded_min_replicates:
            notes.append(f"{c} has {len(idx)} replicate{'s' if len(idx) != 1 else ''}, so a site is called liganded "
                         f"when {'that one reaches' if need == 1 else f'all {need} reach'} R ≥ "
                         f"{settings.liganded_ratio:g} (the rule asks for {settings.liganded_min_replicates})")
        compounds.append(Compound(c, [m.samples[j] for j in idx], need, {k: 0 for k in CLASSES}))
    known = (annotation or {}).get("sites") or {}
    rows: list[dict] = []
    for i, f in enumerate(m.features):
        acc, residue, pos = site_key(f.id)
        row = {"index": i, "id": f.id, "site": f.label, "protein": acc, "position": pos, "residue": residue,
               "description": f.description, "per": {}}
        for comp in compounds:
            vals = [None if p.measured[i][j] is None else sign * p.measured[i][j] for j in cols[comp.name]]
            cls, n, over = classify(vals, thr, comp.min_replicates)
            obs = [v for v in vals if v is not None]
            med = statistics.median(obs) if obs else None
            comp.counts[cls] += 1
            if classify([None if v is None else -v for v in vals], thr, comp.min_replicates)[0] == "liganded":
                comp.other_side += 1
            row["per"][comp.name] = {"log2": med, "n": n, "over": over, "class": cls,
                                     "engagement": None if med is None else max(0.0, 1.0 - 2.0 ** -med)}
        by = [c.name for c in compounds if row["per"][c.name]["class"] == "liganded"]
        clear = [c.name for c in compounds if row["per"][c.name]["class"] == "not liganded"]
        row["liganded_by"] = by
        if not by:
            row["selectivity"] = ""
        elif len(by) > 1:
            row["selectivity"] = "shared"
        elif len(compounds) == 1:
            row["selectivity"] = ""
        else:
            row["selectivity"] = "selective" if len(clear) == len(compounds) - 1 else "unresolved"
        if annotation is not None:
            hit = known.get((acc.split("-")[0].upper(), pos)) if pos is not None else None
            row["annotation"] = hit
            row["status"] = _status(hit)
        rows.append(row)
    for comp in compounds:
        lig = comp.counts["liganded"]
        if comp.other_side >= 10 and comp.other_side > 3 * max(lig, 1):
            other = "low" if settings.liganded_direction == "high" else "high"
            problems.append(("warning",
                             f"{comp.name}: {lig} site{'s' if lig != 1 else ''} reach R ≥ {settings.liganded_ratio:g} "
                             f"as {'heavy / light' if sign > 0 else 'light / heavy'}, but {comp.other_side} would with "
                             f"the ratio the other way round (liganded_direction: {other})"))
    sel = {k: sum(1 for r in rows if r["selectivity"] == k) for k in ("selective", "shared", "unresolved")}
    ann = {}
    if annotation is not None:
        lig_rows = [r for r in rows if r["liganded_by"]]
        ann = {"file": annotation["file"], "sites_in_file": len(known), "flags": annotation["flags"],
               "matched": sum(1 for r in rows if r.get("annotation") is not None),
               "liganded_known": sum(1 for r in lig_rows if r.get("annotation") is not None),
               "liganded_new": sum(1 for r in lig_rows if r.get("annotation") is None)}
        notes += annotation.get("notes", [])
    return Result(settings.liganded_ratio, settings.liganded_min_replicates, settings.liganded_direction, compounds,
                  rows, _proteins(rows, compounds), sel, ann, notes, problems)


def _status(hit: dict | None) -> str:
    if hit is None:
        return "new"
    for flag, label in KNOWN_FLAGS:
        if hit.get(flag):
            return label
    return "in annotation"


def _proteins(rows: list[dict], compounds: list[Compound]) -> list[dict]:
    """Per protein and compound: cysteines with enough replicates, how many are liganded, and whether most
    move together (3+ assessed, at least half liganded): then suspect the protein amount, not the site."""
    by: dict[str, dict] = {}
    for r in rows:
        e = by.setdefault(r["protein"], {"protein": r["protein"], "gene": r["site"].rsplit(" ", 1)[0], "sites": 0,
                                         "idx": [],
                                         "per": {c.name: {"assessed": 0, "liganded": 0} for c in compounds}})
        e["sites"] += 1
        e["idx"].append(r["index"])
        for c in compounds:
            cls = r["per"][c.name]["class"]
            if cls != "too few":
                e["per"][c.name]["assessed"] += 1
            if cls == "liganded":
                e["per"][c.name]["liganded"] += 1
    out = []
    for e in by.values():
        if not any(v["liganded"] for v in e["per"].values()):
            continue
        for v in e["per"].values():
            v["most"] = v["assessed"] >= 3 and v["liganded"] / v["assessed"] >= 0.5
        out.append(e)
    out.sort(key=lambda e: (-max(v["liganded"] for v in e["per"].values()), e["gene"]))
    return out


# ---------------------------------------------------------------- annotation --


class AnnotationError(ValueError):
    pass


def _flag(v: str) -> bool | None:
    t = v.strip().lower()
    return True if t in _TRUE else False if t in _FALSE else None


def load_annotation(path: Path, limit_bytes: int = 512 * 1024**2) -> dict:
    """A site table the lab downloaded (CysDB's Identifiers export, or their own list):
    {"file", "sites": {(accession, position): {flag: bool}}, "flags": [...], "notes": [...]}.

    The site is read from one column of keys like P04406_C152 (CysDB's UniProtKBID_CYS#), or from a protein
    accession column plus a residue-number column. Every other column that holds only yes / no values becomes a
    flag; CysDB's `ligandable`, `hyperreactive` and `identified` are the ones the report names."""
    path = Path(path)
    try:
        if path.stat().st_size > limit_bytes:
            raise AnnotationError(f"{path.name} is larger than {limit_bytes // 2**20} MB")
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        raise AnnotationError(f"{path.name} can't be read: {exc}") from exc
    first = next((ln for ln in text.splitlines() if ln.strip()), "")
    delim = max(("\t", ",", ";"), key=first.count)
    table = list(csv.reader(text.splitlines(), delimiter=delim))
    if len(table) < 2:
        raise AnnotationError(f"{path.name} has no rows")
    head = [h.strip() for h in table[0]]
    body = [r + [""] * (len(head) - len(r)) for r in table[1:] if any(c.strip() for c in r)]
    probe = body[:500]

    def share(j, test) -> float:
        vals = [r[j].strip() for r in probe if r[j].strip()]
        return sum(1 for v in vals if test(v)) / len(vals) if vals else 0.0

    key = next((j for j in range(len(head)) if share(j, lambda v: bool(_SITE_KEY.match(v))) >= 0.9), None)
    acc_col = pos_col = None
    if key is None:
        low = [h.lower().replace(" ", "").replace("_", "") for h in head]
        acc_col = next((j for j, h in enumerate(low) if h in ("proteinid", "uniprot", "uniprotid", "accession",
                                                               "uniprotkbid", "protein", "entry")), None)
        pos_col = next((j for j, h in enumerate(low) if h in ("position", "residue", "resid", "site", "cysteine",
                                                               "residuenumber", "residuepositioninprotein")
                        and share(j, lambda v: bool(re.fullmatch(r"(?i)c?(?:ys)?\d+", v))) >= 0.9), None)
        if acc_col is None or pos_col is None:
            raise AnnotationError(f"{path.name}: no site column found. It needs a column of keys like P04406_C152 "
                                  "(CysDB), or a protein accession column and a residue number column")
    flags = [j for j, h in enumerate(head) if j not in (key, acc_col, pos_col) and h
             and share(j, lambda v: _flag(v) is not None) == 1.0 and any(r[j].strip() for r in probe)]
    sites: dict[tuple[str, int], dict] = {}
    bad = 0
    for r in body:
        if key is not None:
            mm = _SITE_KEY.match(r[key].strip())
            if not mm:
                bad += 1
                continue
            acc, pos = mm.group(1), int(mm.group(2))
        else:
            mm = re.search(r"\d+", r[pos_col])
            acc = r[acc_col].strip()
            got = _UNIPROT.search(acc + "|")
            acc = got.group(1) if got else acc
            if not acc or not mm:
                bad += 1
                continue
            pos = int(mm.group(0))
        entry = sites.setdefault((acc.split("-")[0].upper(), pos), {})
        for j in flags:
            entry[head[j].strip().lower()] = bool(entry.get(head[j].strip().lower())) or bool(_flag(r[j]))
    if not sites:
        raise AnnotationError(f"{path.name}: no row has a readable site")
    notes = [f"site annotation {path.name}: {len(sites):,} sites" +
             (f" with {', '.join(head[j] for j in flags)}" if flags else "") +
             (f"; {bad:,} rows without a readable site left out" if bad else "")]
    return {"file": path.name, "sites": sites, "flags": [head[j].strip().lower() for j in flags], "notes": notes}


def find_annotation(name: str, dest: Path | None) -> Path | None:
    """analysis.site_annotation: an absolute path, or a file in the experiment folder."""
    if not name:
        return None
    p = Path(name)
    if p.is_absolute():
        return p if p.is_file() else None
    if dest is not None and (Path(dest) / name).is_file():
        return Path(dest) / name
    return None


# ------------------------------------------------------------------- outputs --


def _num(v, digits: int = 4):
    return None if v is None or not math.isfinite(v) else round(v, digits)


def columns(res: Result) -> list[str]:
    out = list(SITE_COLUMNS)
    for c in res.compounds:
        out += [f"{c.name} log2_R", f"{c.name} R", f"{c.name} engagement_pct", f"{c.name} replicates",
                f"{c.name} replicates_over", f"{c.name} class"]
    out += ["liganded_by", "n_liganded", "selectivity"]
    if res.annotation:
        out += ["annotation", *res.annotation["flags"]]
    return out


def table_rows(res: Result) -> list[dict]:
    """results/cysteine_sites.tsv: every site, liganded ones first (most compounds, then the strongest ratio)."""
    out = []
    for r in res.rows:
        row = {"id": r["id"], "site": r["site"], "protein": r["protein"], "position": r["position"],
               "description": r["description"]}
        for c in res.compounds:
            x = r["per"][c.name]
            row[f"{c.name} log2_R"] = _num(x["log2"])
            row[f"{c.name} R"] = None if x["log2"] is None else _num(2.0 ** x["log2"], 3)
            row[f"{c.name} engagement_pct"] = None if x["engagement"] is None else _num(100 * x["engagement"], 1)
            row[f"{c.name} replicates"] = x["n"]
            row[f"{c.name} replicates_over"] = x["over"]
            row[f"{c.name} class"] = x["class"]
        row["liganded_by"] = "; ".join(r["liganded_by"])
        row["n_liganded"] = len(r["liganded_by"])
        row["selectivity"] = r["selectivity"]
        if res.annotation:
            row["annotation"] = r["status"]
            for fl in res.annotation["flags"]:
                row[fl] = "" if r["annotation"] is None else ("yes" if r["annotation"].get(fl) else "no")
        out.append(row)
    best = {r["id"]: max((x["log2"] for x in r["per"].values() if x["log2"] is not None), default=-math.inf)
            for r in res.rows}
    out.sort(key=lambda row: (-row["n_liganded"], -best[row["id"]], row["id"]))
    return out


def protein_columns(res: Result) -> list[str]:
    out = ["protein", "gene", "sites"]
    for c in res.compounds:
        out += [f"{c.name} assessed", f"{c.name} liganded", f"{c.name} pattern"]
    return out


def protein_rows(res: Result) -> list[dict]:
    out = []
    for e in res.proteins:
        row = {"protein": e["protein"], "gene": e["gene"], "sites": e["sites"]}
        for c in res.compounds:
            v = e["per"][c.name]
            row[f"{c.name} assessed"] = v["assessed"]
            row[f"{c.name} liganded"] = v["liganded"]
            row[f"{c.name} pattern"] = "most sites" if v["most"] else "site-specific" if v["liganded"] else ""
        out.append(row)
    return out


def rule_text(res: Result) -> str:
    return (f"R ≥ {res.ratio:g} ({'heavy / light' if res.direction == 'high' else 'light / heavy'}) in at least "
            f"{res.min_replicates} replicate{'s' if res.min_replicates != 1 else ''}")


def summary(res: Result | None, reason: str = "", table: str | None = None, proteins: str | None = None) -> dict:
    """analysis.json "cysteines"."""
    if res is None:
        return {"ran": False, "reason": reason}
    return {"ran": True, "rule": rule_text(res), "ratio": res.ratio, "min_replicates": res.min_replicates,
            "direction": res.direction, "table": table, "proteins_table": proteins,
            "compounds": [{"name": c.name, "replicates": len(c.samples), "min_replicates": c.min_replicates,
                           "assessed": c.assessed, **c.counts,
                           "liganded_fraction": None if c.fraction is None else round(c.fraction, 4)}
                          for c in res.compounds],
            "sites_liganded": sum(1 for r in res.rows if r["liganded_by"]),
            **({"selectivity": res.selectivity} if len(res.compounds) > 1 else {}),
            **({"annotation": res.annotation} if res.annotation else {})}


def report_payload(res: Result | None, reason: str = "") -> dict:
    """The report's "cys" data (report.js renderCys): per compound its counts and, as columns over the sites,
    the median log2 R, replicates measured / over and the class index; per site its selectivity and status."""
    if res is None:
        return {"ran": False, "reason": reason}
    sel = {"": 0, "selective": 1, "shared": 2, "unresolved": 3}
    out = {"ran": True, "rule": rule_text(res), "ratio": res.ratio, "minRep": res.min_replicates,
           "dir": res.direction, "classes": list(CLASSES), "i": [r["index"] for r in res.rows],
           "nlig": [len(r["liganded_by"]) for r in res.rows], "sel": [sel[r["selectivity"]] for r in res.rows],
           "selNames": ["", "selective", "shared", "unresolved"], "selectivity": res.selectivity,
           "compounds": [{"name": c.name, "reps": len(c.samples), "minRep": c.min_replicates, "counts": c.counts,
                          "assessed": c.assessed, "fraction": _num(c.fraction),
                          "r": [_num(r["per"][c.name]["log2"], 3) for r in res.rows],
                          "n": [r["per"][c.name]["n"] for r in res.rows],
                          "over": [r["per"][c.name]["over"] for r in res.rows],
                          "cls": [CLASSES.index(r["per"][c.name]["class"]) for r in res.rows]}
                         for c in res.compounds],
           "proteins": [{"p": e["protein"], "g": e["gene"], "s": e["sites"], "x": e["idx"],
                         "per": [[e["per"][c.name]["assessed"], e["per"][c.name]["liganded"],
                                  1 if e["per"][c.name]["most"] else 0] for c in res.compounds]}
                        for e in res.proteins[:2000]],
           "notes": res.notes}
    if res.annotation:
        out["annotation"] = {**res.annotation, "status": [r["status"] for r in res.rows]}
    return out
