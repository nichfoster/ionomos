"""
`ionomos compare`: an Ionomos analysis against a reference result for the same experiment (D60).

    a = load_side(experiment_folder)            # an Ionomos analysis: results/analysis.json + *_differential.tsv
    b = load_side(reference)                    # another Ionomos analysis, or any results table
    res = compare(a, b)                         # per pair of comparisons: matching, agreement, a verdict
    write(res, out_dir)                         # compare.tsv, compare.json, compare.html

What a reference can be: another Ionomos run (a folder), or a table with a fold-change and a p-value
column per comparison, read by anytable.py: a FragPipe-Analyst export, a limma topTable, a Perseus
matrix (-Log p), the lab's old R output; and MSstats' long format (one row per protein and Label).

Per pair of comparisons:

    matching      features matched by ID (UniProt accession; any member of a protein group) or by gene,
                  whichever matches more (--by decides); matched / only in Ionomos / only in the reference
    fold change   Pearson and Spearman of log2FC; slope (major axis, so neither side is "x") and offset
                  (median Ionomos - reference: a normalisation difference shows here)
    hit calls     both / only Ionomos / only reference / opposite direction, at each side's own cut-offs
                  and at common ones
    p-values      Spearman of -log10 p, the median ratio, the share within a factor of 10
    disagreements the features whose fold changes differ most once the offset is taken out
    verdict       "agrees", "agrees after an offset of ...", "differs: ..." or "not judged: ...", from the
                  thresholds below, which the page states

Nothing here changes either result. The output goes into the Ionomos results folder (Ionomos' own
files), where the next `ionomos analyze` shows the verdict at the top of the report (trust.py).
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from html import escape
from pathlib import Path

from ionomos.downstream import anytable, stats
from ionomos.downstream.tables import num, read_tsv, write_tsv

# ---- the verdict's thresholds (stated on the page and in docs/VALIDATION.md)
R_MIN = 0.95            # Pearson r of log2FC at or above this: the fold changes agree
SLOPE_RANGE = (0.9, 1.1)
OFFSET_MAX = 0.10       # |median difference| in log2 above this is called an offset
JACCARD_MIN = 0.70      # shared hits / all hits at the common cut-offs
MIN_HITS = 10           # hit lists are only judged when together they hold at least this many
MIN_MATCHED = 20        # fewer matched features than this: not judged
MIN_MATCHED_SHARE = 0.5  # ... or less than this share of the smaller side
TOP = 15                # disagreements listed

COLUMNS = ["comparison", "status", "id", "label", "reference_id", "log2fc_ionomos", "log2fc_reference", "difference",
           "p_ionomos", "p_reference", "p_adj_ionomos", "p_adj_reference", "call_ionomos", "call_reference",
           "call_ionomos_common", "call_reference_common"]


class CompareError(ValueError):
    pass


@dataclass
class Rows:
    """One comparison of one side."""
    name: str
    ids: list[str]
    labels: list[str]
    fc: list[float | None]
    p: list[float | None]
    q: list[float | None]
    sig: list[str] | None = None      # the side's own calls ("up" / "down" / ""), None when it has none


@dataclass
class Side:
    name: str
    kind: str                          # "ionomos" | "table"
    path: Path
    comparisons: list[Rows]
    alpha: float | None = None         # its own cut-offs, when known
    log2fc: float | None = None
    use_adjusted: bool = True
    notes: list[str] = field(default_factory=list)
    analysis: dict = field(default_factory=dict)   # ionomos: generated_at, settings_digest, version
    results_dir: Path | None = None


# ------------------------------------------------------------------ reading --


def find_analysis(path: Path) -> Path | None:
    """results/ of an Ionomos analysis for a folder: the experiment folder, its results/, or <table>_ionomos/."""
    path = Path(path)
    for cand in (path, path / "results"):
        if (cand / "analysis.json").is_file():
            return cand
    return None


def _load_ionomos(results: Path) -> Side:
    try:
        info = json.loads((results / "analysis.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CompareError(f"cannot read {results / 'analysis.json'}: {exc}") from exc
    s = info.get("settings") or {}
    exp = results.parent.name if results.name == "results" else results.name
    side = Side(exp, "ionomos", results, [], alpha=s.get("alpha"), log2fc=s.get("log2fc"),
                use_adjusted=bool(s.get("use_adjusted", True)), results_dir=results,
                analysis={"generated_at": info.get("generated_at"), "ionomos_version": info.get("ionomos_version"),
                          "settings_digest": (info.get("trust") or {}).get("settings_digest", "")})
    for c in info.get("comparisons") or []:
        tsv = results / Path(str(c.get("table", ""))).name
        if not tsv.is_file():
            side.notes.append(f"{c.get('name')}: {tsv.name} is missing from {results}")
            continue
        _header, rows = read_tsv(tsv)
        side.comparisons.append(Rows(
            str(c.get("name")), [r.get("id", "") for r in rows], [r.get("label", "") for r in rows],
            [num(r.get("log2fc")) for r in rows], [num(r.get("pvalue")) for r in rows],
            [num(r.get("qvalue")) for r in rows],
            [r.get("significant", "") if r.get("significant") in ("up", "down") else "" for r in rows]))
        if c.get("confidence") == "none":
            side.notes.append(f"{c.get('name')}: fold change only in the Ionomos analysis (no p-values)")
    if not side.comparisons:
        raise CompareError(f"{results} holds an Ionomos analysis without a comparison to compare")
    return side


_LABEL_COL = ("label", "comparison", "contrast", "comparisons", "contrasts")
_TRUE = {"true", "t", "+", "1", "yes", "y", "up", "down", "significant", "sig"}


def _long_format(path: Path, header: list[str], rows: list[list[str]], notes: list[str]) -> list[Rows] | None:
    """MSstats groupComparison output and tables like it: one row per feature and comparison, the
    comparison named in a Label / comparison / contrast column."""
    low = {h.lower(): j for j, h in enumerate(header)}
    lab = next((low[k] for k in _LABEL_COL if k in low), None)
    names = {r[lab].strip() for r in rows} - {""} if lab is not None else set()
    if not names or len(names) > 50 or len(rows) < 3 * len(names):  # a few comparisons, each with many rows
        return None
    fc = next((j for j, h in enumerate(header) if anytable._FC.search(h) and not anytable._P.search(h)), None)
    pc = next((j for j, h in enumerate(header) if anytable._P.search(h) and not anytable._Q.search(h)
               and j != fc), None)
    qc = next((j for j, h in enumerate(header) if anytable._Q.search(h)), None)
    if fc is None or pc is None:
        return None
    idc = header.index(anytable._id_column(header, rows))
    if idc == lab:
        idc = next((j for j in range(len(header)) if j not in (lab, fc, pc, qc)), idc)
    labc = anytable._pick(header, anytable._LABEL_NAMES)
    labj = header.index(labc) if labc else idc
    by: dict[str, Rows] = {}
    dropped = 0
    for r in rows:
        name = r[lab].strip()
        if not name:
            continue
        x = by.setdefault(name, Rows(name, [], [], [], [], []))
        f = num(r[fc])
        dropped += f is None and r[fc].strip() not in ("", "NA")
        pv, qv = num(r[pc]), (num(r[qc]) if qc is not None else None)
        x.ids.append(r[idc].strip())
        x.labels.append(r[labj].strip().split(";")[0] or r[idc].strip())
        x.fc.append(f)
        x.p.append(pv if pv is not None and 0 <= pv <= 1 else None)
        x.q.append(qv if qv is not None and 0 <= qv <= 1 else None)
    if dropped:
        notes.append(f"{path.name}: {dropped:,} fold changes are infinite or not a number (a feature missing in one "
                     "condition) and are not compared")
    out = list(by.values())
    for x in out:
        if qc is None:
            x.q = [None if v != v else v for v in stats.bh_adjust([math.nan if v is None else v for v in x.p])]
    notes.append(f"{path.name}: long format, comparisons from the {header[lab]} column: " +
                 ", ".join(x.name for x in out[:8]))
    return out


def _own_calls(header: list[str], rows: list[list[str]], fc_col: str, single: bool, fc: list) -> list[str] | None:
    """A "significant" column for this comparison (FragPipe-Analyst <comparison>_significant, Perseus '+')."""
    key = anytable._stem(fc_col).lower()
    cands = [h for h in header if re.search(r"signif", h, re.I)]
    col = next((h for h in cands if anytable._stem(re.sub(r"(?i)significan(t|ce)", " ", h)).lower() == key), None)
    if col is None and single and len(cands) == 1:
        col = cands[0]
    if col is None:
        return None
    j = header.index(col)
    out = []
    for r, f in zip(rows, fc, strict=True):
        v = r[j].strip().lower()
        out.append(("up" if (f or 0) > 0 else "down") if v in _TRUE and f is not None else "")
    return out


def _load_table(path: Path) -> Side:
    side = Side(path.name, "table", path, [])
    try:
        header, rows = anytable.read_table(path, side.notes)
    except (anytable.TableError, OSError, UnicodeError) as exc:
        raise CompareError(f"cannot read {path.name}: {exc}") from exc
    long = _long_format(path, header, rows, side.notes) if rows else None
    if long:
        side.comparisons = long
        return side
    try:
        m = anytable.load(path)
    except (anytable.TableError, OSError, UnicodeError) as exc:
        raise CompareError(f"cannot read {path.name}: {exc}") from exc
    pre = m.meta.get("precomputed") or []
    if not pre:
        raise CompareError(f"{path.name} holds quantities, not results: a reference needs a fold-change column and a "
                           "p-value column per comparison (analyse it with `ionomos analyze` and compare the folder)")
    side.notes = [n for n in m.notes if "plotted as given" not in n]
    header = anytable._unique(header, [])
    flags = [f for f in anytable._MQ_FLAGS if f in header]
    if flags:  # anytable.load left these rows out; keep the two readings aligned
        rows = [r for r in rows if not any(r[header.index(f)].strip() == "+" for f in flags)]
    ids = [f.id for f in m.features]
    labels = [f.label for f in m.features]
    for c in pre:
        q = c["q"]
        if q is None:
            q = [None if v != v else v for v in stats.bh_adjust([math.nan if v is None else v for v in c["p"]])]
            side.notes.append(f"{c['name']}: no adjusted p in {path.name}; Benjamini-Hochberg computed from its p")
        sig = _own_calls(header, rows, c["columns"][0], len(pre) == 1, c["fc"]) if len(rows) == len(ids) else None
        side.comparisons.append(Rows(c["name"], ids, labels, list(c["fc"]), list(c["p"]), list(q), sig))
    return side


def load_side(path: str | Path) -> Side:
    """An Ionomos analysis (a folder) or a results table (a file)."""
    path = Path(path)
    if path.is_dir():
        results = find_analysis(path)
        if results is None:
            raise CompareError(f"{path} holds no Ionomos analysis (no results/analysis.json). Run `ionomos analyze` "
                               "on it first, or give a results table")
        return _load_ionomos(results)
    if path.is_file():
        if path.name == "analysis.json":
            return _load_ionomos(path.parent)
        return _load_table(path)
    raise CompareError(f"not a folder or a table: {path}")


# ----------------------------------------------------------------- matching --


def _tokens(fid: str) -> list[str]:
    """'sp|P04406|G3P_HUMAN;P00338' -> ['P04406', 'P00338'] (upper case)."""
    out = []
    for t in re.split(r"[;,\s]+", fid.strip()):
        parts = t.split("|")
        t = parts[1] if len(parts) >= 3 and parts[1] else t
        if t:
            out.append(t.upper())
    return out


def _gene(label: str) -> str:
    return re.split(r"[;,\s]+", label.strip())[0].upper() if label.strip() else ""


def match(a: Rows, b: Rows, by: str = "auto") -> tuple[list[tuple[int, int]], str, dict]:
    """[(index in a, index in b)], the key used ("id" | "gene"), and counts of repeated keys."""
    def pairs(key_a, keys_b):
        index: dict[str, int] = {}
        repeats = 0
        for j in range(len(b.ids)):
            for k in keys_b(j):
                if k in index:
                    repeats += index[k] != j
                else:
                    index[k] = j
        used: set[int] = set()
        out = []
        for i in range(len(a.ids)):
            for k in key_a(i):
                j = index.get(k)
                if j is not None and j not in used:
                    used.add(j)
                    out.append((i, j))
                    break
        return out, repeats

    by_id, rep_id = pairs(lambda i: _tokens(a.ids[i]), lambda j: _tokens(b.ids[j]))
    by_gene, rep_gene = pairs(lambda i: [g for g in (_gene(a.labels[i]),) if g],
                              lambda j: [g for g in (_gene(b.labels[j]), *_tokens(b.ids[j])) if g])
    if by == "id" or (by == "auto" and len(by_id) >= len(by_gene)):
        return by_id, "id", {"repeated_keys": rep_id}
    return by_gene, "gene", {"repeated_keys": rep_gene}


def _split(name: str) -> tuple[str, str]:
    from ionomos.downstream.analysis import _split_name

    t, c = _split_name(re.sub(r"\s*\(log2 H/L vs 0\)$", "", name))
    return t.strip().lower(), c.strip().lower()


def pair_comparisons(a: Side, b: Side, want: str | None = None, want_ref: str | None = None
                     ) -> list[tuple[Rows, Rows, bool, str]]:
    """[(Ionomos comparison, reference comparison, flipped, note)]: by name (treatment and control, either
    order: a reference named the other way round is flipped), else the only comparison of each side."""
    def pick(side: Side, name: str) -> Rows:
        hit = [c for c in side.comparisons if c.name.lower() == name.lower()] or \
              [c for c in side.comparisons if name.lower() in c.name.lower()]
        if len(hit) != 1:
            raise CompareError(f"{side.name} has no single comparison named {name!r} (it has: " +
                               ", ".join(c.name for c in side.comparisons) + ")")
        return hit[0]

    if want or want_ref:
        ca = pick(a, want) if want else None
        cb = pick(b, want_ref) if want_ref else None
        if ca is None:
            ca = a.comparisons[0] if len(a.comparisons) == 1 else pick(a, cb.name)
        if cb is None:
            cb = b.comparisons[0] if len(b.comparisons) == 1 else pick(b, ca.name)
        flipped = _split(ca.name) == _split(cb.name)[::-1] and _split(ca.name)[1] != ""
        return [(ca, cb, flipped, "")]
    out = []
    left = list(b.comparisons)
    for ca in a.comparisons:
        ta = _split(ca.name)
        for cb in left:
            tb = _split(cb.name)
            same = ta == tb or ca.name.lower() == cb.name.lower() or (ta[0] == tb[0] and "" in (ta[1], tb[1]))
            flipped = ta[1] != "" and ta == tb[::-1]
            if same or flipped:
                out.append((ca, cb, flipped and not same, ""))
                left.remove(cb)
                break
    if not out and len(a.comparisons) == 1 and len(b.comparisons) == 1:
        ca, cb = a.comparisons[0], b.comparisons[0]
        out.append((ca, cb, False, f"paired by position: the names differ ({ca.name} / {cb.name})"))
    if not out:
        raise CompareError("no comparison of the reference matches one of Ionomos' by name. Ionomos: " +
                           ", ".join(c.name for c in a.comparisons) + ". Reference: " +
                           ", ".join(c.name for c in b.comparisons) + ". Name them with --comparison and "
                           "--ref-comparison")
    return out


# --------------------------------------------------------------- statistics --


def _pearson(x: list[float], y: list[float]) -> float | None:
    n = len(x)
    if n < 3:
        return None
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((v - mx) ** 2 for v in x)
    syy = sum((v - my) ** 2 for v in y)
    if sxx <= 0 or syy <= 0:
        return None
    return sum((u - mx) * (v - my) for u, v in zip(x, y, strict=True)) / math.sqrt(sxx * syy)


def _major_axis(x: list[float], y: list[float]) -> float | None:
    """Slope of y on x by the major axis (orthogonal regression): both sides carry noise, so neither is "x"."""
    n = len(x)
    if n < 3:
        return None
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((v - mx) ** 2 for v in x)
    syy = sum((v - my) ** 2 for v in y)
    sxy = sum((u - mx) * (v - my) for u, v in zip(x, y, strict=True))
    if sxy == 0:
        return None
    return (syy - sxx + math.sqrt((syy - sxx) ** 2 + 4 * sxy * sxy)) / (2 * sxy)


def _call(fc: float | None, score: float | None, alpha: float, lfc: float) -> str:
    if fc is None or score is None or score > alpha or abs(fc) < lfc:
        return ""
    return "up" if fc > 0 else "down"


def _calls_table(ca: list[str], cb: list[str]) -> dict:
    both = sum(1 for x, y in zip(ca, cb, strict=True) if x and x == y)
    opposite = sum(1 for x, y in zip(ca, cb, strict=True) if x and y and x != y)
    only_a = sum(1 for x, y in zip(ca, cb, strict=True) if x and not y)
    only_b = sum(1 for x, y in zip(ca, cb, strict=True) if y and not x)
    union = both + opposite + only_a + only_b
    return {"both": both, "only_ionomos": only_a, "only_reference": only_b, "opposite_direction": opposite,
            "neither": len(ca) - union, "jaccard": (both / union) if union else None}


def _spearman(x: list[float], y: list[float]) -> float | None:
    from ionomos.downstream.insights import spearman

    return spearman(x, y)


def compare_pair(a: Side, b: Side, ca: Rows, cb: Rows, flipped: bool = False, by: str = "auto",
                 alpha: float | None = None, log2fc: float | None = None, use_adjusted: bool | None = None,
                 ref_alpha: float | None = None, ref_log2fc: float | None = None, note: str = "") -> dict:
    """Everything about one pair of comparisons. alpha / log2fc / use_adjusted: the common cut-offs (default:
    the Ionomos side's own). ref_alpha / ref_log2fc: the reference's own cut-offs when its table has no calls."""
    alpha = alpha if alpha is not None else (a.alpha if a.alpha is not None else 0.05)
    log2fc = log2fc if log2fc is not None else (a.log2fc if a.log2fc is not None else 1.0)
    adj = use_adjusted if use_adjusted is not None else a.use_adjusted
    sign = -1.0 if flipped else 1.0
    fb = [None if v is None else sign * v for v in cb.fc]
    pairs, key, extra = match(ca, cb, by)
    in_a, in_b = {i for i, _ in pairs}, {j for _, j in pairs}
    notes = [note] if note else []
    if flipped:
        notes.append(f"the reference is {cb.name}, the other way round: its fold changes were flipped")
    if extra["repeated_keys"]:
        notes.append(f"{extra['repeated_keys']:,} {key} keys occur on several rows of the reference; the first row "
                     "was used")

    def own(side: Side, rows: Rows, fc: list, a_: float | None, l_: float | None) -> tuple[list[str], str]:
        if rows.sig is not None:
            s = rows.sig if fc is rows.fc else [{"up": "down", "down": "up"}.get(x, "") for x in rows.sig]
            known = side.alpha is not None and side.log2fc is not None
            return list(s), "its own calls" + (f" ({'adjusted p' if side.use_adjusted else 'p'} ≤ {side.alpha:g} and "
                                               f"|log2FC| ≥ {side.log2fc:g})" if known else "")
        aa = a_ if a_ is not None else (side.alpha if side.alpha is not None else alpha)
        ll = l_ if l_ is not None else (side.log2fc if side.log2fc is not None else log2fc)
        given = a_ is not None or side.alpha is not None
        score = rows.q if side.use_adjusted else rows.p
        return ([_call(f, s, aa, ll) for f, s in zip(fc, score, strict=True)],
                f"{'adjusted p' if side.use_adjusted else 'p'} ≤ {aa:g} and |log2FC| ≥ {ll:g}" +
                ("" if given else " (assumed: the table has no calls of its own, so Ionomos' cut-offs were used)"))

    own_a, own_a_how = own(a, ca, ca.fc, None, None)
    own_b, own_b_how = own(b, cb, fb if flipped else cb.fc, ref_alpha, ref_log2fc)
    com_a = [_call(f, s, alpha, log2fc) for f, s in zip(ca.fc, ca.q if adj else ca.p, strict=True)]
    com_b = [_call(f, s, alpha, log2fc) for f, s in zip(fb, cb.q if adj else cb.p, strict=True)]

    both_fc = [(i, j) for i, j in pairs if ca.fc[i] is not None and fb[j] is not None]
    x = [fb[j] for _, j in both_fc]          # reference
    y = [ca.fc[i] for i, _ in both_fc]       # Ionomos
    diffs = [v - u for u, v in zip(x, y, strict=True)]
    offset = stats.median(diffs) if diffs else None
    spread = stats.mad(diffs) if len(diffs) >= 2 else None
    r = _pearson(x, y)
    slope = _major_axis(x, y)
    both_p = [(i, j) for i, j in pairs if ca.p[i] and cb.p[j]]
    lpa = [-math.log10(ca.p[i]) for i, _ in both_p]
    lpb = [-math.log10(cb.p[j]) for _, j in both_p]
    ratio = [u - v for u, v in zip(lpb, lpa, strict=True)]  # log10(p Ionomos / p reference)

    res = {
        "comparison": ca.name, "reference_comparison": cb.name, "flipped": flipped, "matched_by": key,
        "features_ionomos": len(ca.ids), "features_reference": len(cb.ids), "matched": len(pairs),
        "only_ionomos": len(ca.ids) - len(pairs), "only_reference": len(cb.ids) - len(pairs),
        "matched_with_fold_change": len(both_fc),
        "pearson": r, "spearman": _spearman(x, y), "slope": slope, "offset": offset, "difference_mad": spread,
        "intercept": (sum(y) / len(y) - slope * sum(x) / len(x)) if slope is not None and x else None,
        "hits": {
            "own": {**_calls_table([own_a[i] for i, _ in pairs], [own_b[j] for _, j in pairs]),
                    "ionomos_cutoffs": own_a_how, "reference_cutoffs": own_b_how,
                    "ionomos_hits_unmatched": sum(1 for i, v in enumerate(own_a) if v and i not in in_a),
                    "reference_hits_unmatched": sum(1 for j, v in enumerate(own_b) if v and j not in in_b)},
            "common": {**_calls_table([com_a[i] for i, _ in pairs], [com_b[j] for _, j in pairs]),
                       "cutoffs": f"{'adjusted p' if adj else 'p'} ≤ {alpha:g} and |log2FC| ≥ {log2fc:g}"},
        },
        "p_values": {"compared": len(both_p), "spearman": _spearman(lpb, lpa),
                     "median_log10_ratio": stats.median(ratio) if ratio else None,
                     "within_factor_10": (sum(1 for v in ratio if abs(v) <= 1) / len(ratio)) if ratio else None},
        "notes": notes,
    }
    res["verdict"], res["verdict_reasons"] = verdict(res)
    order = sorted(range(len(both_fc)), key=lambda k: -abs(diffs[k] - (offset or 0.0)))
    res["largest_disagreements"] = [
        {"id": ca.ids[i], "label": ca.labels[i], "log2fc_ionomos": ca.fc[i], "log2fc_reference": fb[j],
         "difference": ca.fc[i] - fb[j], "p_ionomos": ca.p[i], "p_reference": cb.p[j],
         "call_ionomos": com_a[i], "call_reference": com_b[j]}
        for i, j in (both_fc[k] for k in order[:TOP])]
    rows = []
    for i, j in pairs:
        d = ca.fc[i] - fb[j] if ca.fc[i] is not None and fb[j] is not None else None
        rows.append([ca.name, "matched", ca.ids[i], ca.labels[i], cb.ids[j], ca.fc[i], fb[j], d, ca.p[i], cb.p[j],
                     ca.q[i], cb.q[j], own_a[i], own_b[j], com_a[i], com_b[j]])
    rows += [[ca.name, "only_ionomos", ca.ids[i], ca.labels[i], None, ca.fc[i], None, None, ca.p[i], None, ca.q[i],
              None, own_a[i], None, com_a[i], None] for i in range(len(ca.ids)) if i not in in_a]
    rows += [[ca.name, "only_reference", cb.ids[j], cb.labels[j], cb.ids[j], None, fb[j], None, None, cb.p[j], None,
              cb.q[j], None, own_b[j], None, com_b[j]] for j in range(len(cb.ids)) if j not in in_b]
    res["_rows"] = rows
    res["_points"] = {"fc": [(fb[j], ca.fc[i], com_a[i], com_b[j], ca.labels[i]) for i, j in both_fc],
                      "p": [(u, v, com_a[i], com_b[j], ca.labels[i]) for (i, j), u, v in zip(both_p, lpb, lpa,
                                                                                             strict=True)]}
    return res


def verdict(res: dict) -> tuple[str, list[str]]:
    """One plain line for a pair, and the reasons behind it."""
    n, small = res["matched_with_fold_change"], min(res["features_ionomos"], res["features_reference"])
    if n < MIN_MATCHED or (small and res["matched"] / small < MIN_MATCHED_SHARE):
        why = (f"only {res['matched']:,} of {small:,} features matched by {res['matched_by']} "
               f"({n:,} with a fold change on both sides)")
        return f"not judged: {why}", [why + f"; at least {MIN_MATCHED} and {MIN_MATCHED_SHARE:.0%} are needed. "
                                      "Are these the same experiment, and do the IDs look alike?"]
    r, slope, off = res["pearson"], res["slope"], res["offset"]
    if r is None or slope is None:
        return "not judged: the fold changes do not vary", ["a correlation needs fold changes that differ"]
    bad = []
    if r <= -0.5:
        bad.append(f"the fold changes point the opposite way (r = {r:.2f}); the reference is probably the comparison "
                   "the other way round (--flip)")
    elif r < R_MIN:
        bad.append(f"the fold changes correlate at r = {r:.3f} (below {R_MIN})")
    if r > -0.5 and not SLOPE_RANGE[0] <= slope <= SLOPE_RANGE[1]:
        bad.append(f"the slope is {slope:.2f} (outside {SLOPE_RANGE[0]} to {SLOPE_RANGE[1]}): Ionomos' fold changes "
                   f"are {'smaller' if slope < 1 else 'larger'} than the reference's")
    has_offset = abs(off) > OFFSET_MAX
    h = res["hits"]["common"]
    union = h["both"] + h["only_ionomos"] + h["only_reference"] + h["opposite_direction"]
    if not bad and not has_offset and union >= MIN_HITS and h["jaccard"] < JACCARD_MIN:
        bad.append(f"the fold changes agree (r = {r:.3f}), but the hit lists share only {h['jaccard']:.0%} of "
                   f"{union:,} hits at common cut-offs: the tests, the imputation or the correction for many tests "
                   "differ")
    if bad:
        return "differs: " + "; ".join(bad), bad
    fine = [f"r = {r:.3f}", f"slope {slope:.2f}", f"offset {off:+.2f} log2"]
    if union >= MIN_HITS and not has_offset:
        fine.append(f"{h['jaccard']:.0%} of {union:,} hits shared at common cut-offs")
    if has_offset:
        return (f"agrees after an offset of {off:+.2f} log2 (Ionomos - reference; a normalisation difference). "
                f"r = {r:.3f}, slope {slope:.2f}"), fine + ["the hit lists were not judged: with an offset, the "
                                                            "fold-change cut-off falls on different features"]
    return "agrees (" + ", ".join(fine) + ")", fine


def compare(a: Side, b: Side, by: str = "auto", comparison: str | None = None, ref_comparison: str | None = None,
            flip: bool = False, **cutoffs) -> dict:
    """All pairs of comparisons of an Ionomos analysis (a) and a reference (b)."""
    pairs = [compare_pair(a, b, ca, cb, flipped != flip, by, note=note, **cutoffs)
             for ca, cb, flipped, note in pair_comparisons(a, b, comparison, ref_comparison)]
    return {
        "ionomos": str(a.path), "ionomos_name": a.name, "reference": str(b.path), "reference_name": b.name,
        "reference_kind": "another Ionomos analysis" if b.kind == "ionomos" else "a results table",
        "analysis": a.analysis, "generated_at": datetime.now().isoformat(timespec="seconds"),
        "thresholds": {"pearson_min": R_MIN, "slope_range": list(SLOPE_RANGE), "offset_max_log2": OFFSET_MAX,
                       "hit_jaccard_min": JACCARD_MIN, "min_hits_to_judge": MIN_HITS, "min_matched": MIN_MATCHED,
                       "min_matched_share": MIN_MATCHED_SHARE},
        "notes": a.notes + b.notes,
        "pairs": pairs,
        "verdicts": [f"{p['comparison']}: {p['verdict']}" for p in pairs],
    }


# ------------------------------------------------------------------ writing --


def _fmt(v, digits: int = 3) -> str:
    if v is None:
        return "–"
    if isinstance(v, float):
        return f"{v:.{digits}g}" if abs(v) < 1e-3 and v != 0 else f"{v:.{digits}f}"
    return f"{v:,}" if isinstance(v, int) else str(v)


def _cls(a: str, b: str) -> str:
    return "c2" if a and b else "c1" if a else "c0" if b else "ns"


LEGEND = [("c2", "hit in both"), ("c1", "only Ionomos"), ("c0", "only reference"), ("ns", "neither")]


def page(res: dict) -> str:
    from ionomos.downstream import plots

    t = res["thresholds"]
    b = [f"<p class='sub'>Ionomos analysis <b>{escape(res['ionomos_name'])}</b> against "
         f"<b>{escape(res['reference_name'])}</b> ({escape(res['reference_kind'])}). Nothing in either result was "
         "changed.</p>"]
    if res["notes"]:
        b.append("<details class='notes'><summary><b>Notes</b> (" + str(len(res["notes"])) + ")</summary><ul>" +
                 "".join(f"<li>{escape(n)}</li>" for n in res["notes"]) + "</ul></details>")
    for p in res["pairs"]:
        ok = p["verdict"].startswith("agrees")
        b.append(f"<section><h2>{escape(p['comparison'])}</h2>"
                 f"<div class='card verdict'><span class='pill {'ok' if ok else 'warn'}'>"
                 f"{escape(p['verdict'].split(':')[0].split(' (')[0].split(' after')[0])}</span> "
                 f"<b>{escape(p['verdict'])}</b></div>")
        if p["notes"]:
            b.append("<div class='notes'><ul>" + "".join(f"<li>{escape(n)}</li>" for n in p["notes"]) + "</ul></div>")
        kv = [("Reference comparison", p["reference_comparison"] + (" (flipped)" if p["flipped"] else "")),
              ("Matched by", p["matched_by"]),
              ("Features", f"{p['matched']:,} matched; {p['only_ionomos']:,} only in Ionomos; "
                           f"{p['only_reference']:,} only in the reference"),
              ("log2FC correlation", f"Pearson {_fmt(p['pearson'])}, Spearman {_fmt(p['spearman'])} "
                                     f"({p['matched_with_fold_change']:,} features)"),
              ("Slope (major axis)", _fmt(p["slope"])),
              ("Offset (median Ionomos − reference)", f"{_fmt(p['offset'])} log2 (spread of the differences, MAD "
                                                      f"{_fmt(p['difference_mad'])})"),
              ("p-values", f"Spearman of −log10 p {_fmt(p['p_values']['spearman'])}; median log10(p Ionomos / p "
                           f"reference) {_fmt(p['p_values']['median_log10_ratio'])}; "
                           f"{_fmt(100 * p['p_values']['within_factor_10'], 0) if p['p_values']['within_factor_10'] is not None else '–'}"
                           f"% within a factor of 10 ({p['p_values']['compared']:,} features)")]
        b.append("<div class='kv card'>" + "".join(f"<div>{escape(k)}</div><div>{escape(str(v))}</div>"
                                                    for k, v in kv) + "</div>")
        b.append("<h3>Hit calls among the matched features</h3><div class='tablewrap'><table><thead><tr><th>Cut-offs</th>"
                 "<th>both</th><th>only Ionomos</th><th>only reference</th><th>opposite direction</th><th>neither</th>"
                 "<th>shared / all hits</th></tr></thead><tbody>")
        for name, h, how in (("each side's own", p["hits"]["own"],
                              f"Ionomos: {p['hits']['own']['ionomos_cutoffs']}; reference: "
                              f"{p['hits']['own']['reference_cutoffs']}"),
                             ("common", p["hits"]["common"], p["hits"]["common"]["cutoffs"])):
            b.append(f"<tr><td class='desc'><b>{name}</b><br><span class='muted'>{escape(how)}</span></td>"
                     + "".join(f"<td class='n'>{h[k]:,}</td>" for k in ("both", "only_ionomos", "only_reference",
                                                                         "opposite_direction", "neither"))
                     + f"<td class='n'>{_fmt(h['jaccard'], 2)}</td></tr>")
        b.append("</tbody></table></div>")
        un = p["hits"]["own"]
        if un["ionomos_hits_unmatched"] or un["reference_hits_unmatched"]:
            b.append(f"<p class='sub'>Hits on features the other side does not list: {un['ionomos_hits_unmatched']:,} "
                     f"of Ionomos', {un['reference_hits_unmatched']:,} of the reference's.</p>")
        pts = p.get("_points") or {"fc": [], "p": []}
        line = (p["slope"], p["intercept"]) if p["slope"] is not None and p["intercept"] is not None else None
        fc = plots.scatter([{"x": x, "y": y, "cls": _cls(ca, cb), "label": lab} for x, y, ca, cb, lab in pts["fc"]],
                           "log2 fold change, reference", "log2 fold change, Ionomos",
                           f"log2 fold change, Ionomos against the reference, {p['comparison']}", LEGEND, line)
        pv = plots.scatter([{"x": x, "y": y, "cls": _cls(ca, cb), "label": lab} for x, y, ca, cb, lab in pts["p"]],
                           "−log10 p, reference", "−log10 p, Ionomos",
                           f"p-values, Ionomos against the reference, {p['comparison']}", LEGEND)
        b.append(f"<div class='grid2 wide'><div class='card chart'>{fc}</div><div class='card chart'>{pv}</div></div>"
                 "<p class='sub'>Solid line: equal values. Dashed line: the fitted slope. Colours: hit calls at the "
                 "common cut-offs.</p>")
        if p["largest_disagreements"]:
            b.append("<h3>Largest disagreements in fold change (offset taken out)</h3><div class='tablewrap'><table>"
                     "<thead><tr><th>Feature</th><th>ID</th><th>log2FC Ionomos</th><th>log2FC reference</th>"
                     "<th>difference</th><th>p Ionomos</th><th>p reference</th><th>calls (Ionomos / reference)</th>"
                     "</tr></thead><tbody>")
            for d in p["largest_disagreements"]:
                b.append(f"<tr><td>{escape(d['label'])}</td><td>{escape(d['id'][:40])}</td>"
                         f"<td class='n'>{_fmt(d['log2fc_ionomos'])}</td><td class='n'>{_fmt(d['log2fc_reference'])}</td>"
                         f"<td class='n'>{_fmt(d['difference'])}</td><td class='n'>{_fmt(d['p_ionomos'])}</td>"
                         f"<td class='n'>{_fmt(d['p_reference'])}</td>"
                         f"<td>{escape(d['call_ionomos'] or '–')} / {escape(d['call_reference'] or '–')}</td></tr>")
            b.append("</tbody></table></div>")
        b.append("</section>")
    b.append("<section><h2>How the verdict is made</h2><p class='methods'>"
             f"<b>agrees</b>: Pearson r of the log2 fold changes ≥ {t['pearson_min']}, slope (major axis) between "
             f"{t['slope_range'][0]} and {t['slope_range'][1]}, |offset| ≤ {t['offset_max_log2']} log2, and, when the "
             f"two hit lists together hold at least {t['min_hits_to_judge']} features, at least "
             f"{t['hit_jaccard_min']:.0%} of them shared at the common cut-offs. <b>agrees after an offset</b>: the "
             "same, with a larger offset (the hit lists are then not judged). <b>differs</b>: any of these fails; "
             f"the line says which. <b>not judged</b>: fewer than {t['min_matched']} features, or less than "
             f"{t['min_matched_share']:.0%} of the smaller side, matched. These thresholds are Ionomos' own choice, "
             "not a standard; the numbers above are what to read. Every matched and unmatched feature is in "
             "<a href='compare.tsv'>compare.tsv</a>; the numbers on this page are in "
             "<a href='compare.json'>compare.json</a>.</p></section>")
    return plots.page(f"{res['ionomos_name']}: compared with {res['reference_name']}",
                      f"generated {res['generated_at']} · Ionomos {res['analysis'].get('ionomos_version') or ''}",
                      "".join(b))


def write(res: dict, out_dir: Path) -> list[Path]:
    """compare.tsv, compare.json and compare.html in out_dir (created; Ionomos' own files, rewritten each run)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = [r for p in res["pairs"] for r in p.get("_rows", [])]
    files = [write_tsv(out_dir / "compare.tsv", COLUMNS, rows)]
    html = page(res)
    public = {**res, "pairs": [{k: v for k, v in p.items() if not k.startswith("_")} for p in res["pairs"]]}
    (out_dir / "compare.json").write_text(json.dumps(public, indent=2, default=str), encoding="utf-8")
    (out_dir / "compare.html").write_text(html, encoding="utf-8")
    return files + [out_dir / "compare.json", out_dir / "compare.html"]


def summary_lines(res: dict) -> list[str]:
    """What the command line prints."""
    out = []
    for p in res["pairs"]:
        h, o = p["hits"]["common"], p["hits"]["own"]
        out.append(f"{p['comparison']}  vs  {p['reference_comparison']}" + ("  (flipped)" if p["flipped"] else ""))
        out.append(f"  verdict: {p['verdict']}")
        out.append(f"  features: {p['matched']:,} matched by {p['matched_by']}, {p['only_ionomos']:,} only in Ionomos, "
                   f"{p['only_reference']:,} only in the reference")
        out.append(f"  log2FC: Pearson {_fmt(p['pearson'])}, Spearman {_fmt(p['spearman'])}, slope {_fmt(p['slope'])}, "
                   f"offset {_fmt(p['offset'])}")
        out.append(f"  hits at common cut-offs ({h['cutoffs']}): {h['both']:,} both, {h['only_ionomos']:,} only "
                   f"Ionomos, {h['only_reference']:,} only reference, {h['opposite_direction']:,} opposite")
        out.append(f"  hits at own cut-offs: {o['both']:,} both, {o['only_ionomos']:,} only Ionomos, "
                   f"{o['only_reference']:,} only reference")
        out.append(f"  p-values: Spearman {_fmt(p['p_values']['spearman'])}, median log10 ratio "
                   f"{_fmt(p['p_values']['median_log10_ratio'])}")
        for n in p["notes"]:
            out.append(f"  note: {n}")
    return out
