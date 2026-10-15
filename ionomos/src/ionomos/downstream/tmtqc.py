"""
TMT QC after a search (D91): what the reporter ions, the labels and the precursors say about each plex.

    res = run(workdir, maps, props, reference, limits)   # Result; reads FragPipe's psm.tsv files and tmt-report/
    res = run_folder(folder, record=None)                # the same for an experiment folder (maps, workflow found)
    summary(res)                                         # analysis.json "tmt_qc" and results/tmt_qc.json
    report_payload(res)                                  # the report's "TMT plexes and channels" section (report.js
                                                         # renderTmt) and its figures (sectionfigs: tmt_channels,
                                                         # tmt_labelling, tmt_purity)
    write(res, results_dir)                              # results/tmt_qc.json + tmt_qc_<plex>.tsv (names.py)
    text(res)                                            # `ionomos tmt qc`

What is read (FragPipe 24; TMT-Integrator 6's PsmPreProcessor, checked against its source on 2026-10-14): one
psm.tsv per plex folder (FragPipe's <experiment>_<bioreplicate>), with `Peptide`, `Assigned Modifications`
("N-term(229.1629), 7K(229.1629), 3C(57.0215)"), `Purity` (the share of the isolation window's ion signal that is
the precursor), `Intensity` (MS1) and the reporter block: one column per channel of the label type, in the kit's
order, named "Intensity <sample name>" ("Intensity NA" for an unused channel). Older Philosopher tables named the
columns by the sample name alone; those are found by the names of the channel map. tmt-report/abundance_*.tsv is
read for its header only (the channels TMT-Integrator reported).

Per plex and channel: PSMs with signal, the median and quartiles of log2 reporter intensity, the share missing, and
the channel's median log2 ratio to the PSM's median over the named channels ("rel": a bridge / pool channel should
be near 0). Labelling: the share of PSMs whose peptide N-terminus carries the label (an acetylated or pyro-Glu
N-terminus can't and is left out), of K residues labelled (only meaningful when TMT on K is a variable
modification: the stock workflows fix it, so every identified K carries it), and of PSMs fully labelled. Purity: a
histogram, the median and the share below the workflow's tmtintegrator.min_purity (TMT-Integrator drops those).

Doctor issues (problems): TMT_LOW_LABELLING (fully labelled share below analysis.tmt_min_labelling, 0.95),
TMT_LOW_PURITY (a plex's median purity below analysis.tmt_min_purity, 0.7), TMT_CHANNEL_EMPTY (a named channel with
signal in fewer than half the PSMs, or 10-fold below the others), TMT_REFERENCE_WEAK (the reference more than
2-fold from the median), TMT_LABEL_TYPE_MISMATCH (the tables have another number of channels, or other names, than
the map), TMT_CONDITIONS_UNKNOWN (names that carry no condition: FragPipe's own <plex>_<channel>). Nothing is
written to FragPipe's folder; a psm.tsv over MAX_MB is left unread with a note; without psm.tsv only the
tmt-report header is checked.
"""
from __future__ import annotations

import json
import math
import random
import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path

from ionomos import names, tmtkit

MAX_MB = 4096
MIN_LABELLING = 0.95      # analysis.tmt_min_labelling: share of PSMs fully labelled
MIN_PURITY = 0.70         # analysis.tmt_min_purity: a plex's median precursor purity
EMPTY_SHARE = 0.5         # a named channel with signal in fewer PSMs than this is empty
EMPTY_LOG2 = -3.32        # ... or this far below the PSMs' median (10-fold)
REF_FAR_LOG2 = 1.0        # a reference more than 2-fold from the median
MIN_PSMS = 50             # PSMs a plex needs before its labelling or purity is judged
SAMPLE = 20000            # values kept per channel for the quantiles (reservoir)
PURITY_BINS = 20
_MOD = re.compile(r"(N-term|n|\d+)([A-Za-z]?)\(([-+]?\d+(?:\.\d+)?)\)", re.I)
_BLOCKING = ((42.0106, "N-term"), (-17.0265, "Q"), (-18.0106, "E"), (-17.0265, "C"))


@dataclass
class Result:
    plexes: list[dict] = field(default_factory=list)
    labelling: dict = field(default_factory=dict)
    purity: dict = field(default_factory=dict)
    workflow: dict = field(default_factory=dict)
    tables: list[dict] = field(default_factory=list)      # tmt-report/*.tsv: name, channels it reports
    notes: list[str] = field(default_factory=list)
    problems: list[tuple[str, str]] = field(default_factory=list)   # (issue code, message) for the doctor
    limits: dict = field(default_factory=dict)
    reason: str = ""

    @property
    def found(self) -> bool:
        return bool(self.plexes or self.tables)


# ---------------------------------------------------------------- reading --


def _num(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _quantiles(vals: list[float]) -> list[float] | None:
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)

    def q(p):
        k = (n - 1) * p
        lo = int(math.floor(k))
        hi = min(n - 1, lo + 1)
        return s[lo] + (s[hi] - s[lo]) * (k - lo)

    return [round(q(p), 3) for p in (0.05, 0.25, 0.5, 0.75, 0.95)]


class _Reservoir:
    def __init__(self, rng: random.Random, k: int = SAMPLE):
        self.rng, self.k, self.n, self.v = rng, k, 0, []

    def add(self, x: float) -> None:
        self.n += 1
        if len(self.v) < self.k:
            self.v.append(x)
        else:
            j = self.rng.randrange(self.n)
            if j < self.k:
                self.v[j] = x


def psm_files(workdir: Path) -> list[Path]:
    """FragPipe's psm.tsv files: one per <experiment>_<bioreplicate> folder (or the output folder itself)."""
    workdir = Path(workdir)
    out = []
    # sorted(Path) compares case-insensitively on Windows and not on Linux: the same order everywhere
    for p in [workdir / "psm.tsv", *sorted(workdir.glob("*/psm.tsv"), key=lambda q: q.parent.name.lower())]:
        if p.is_file() and "_previous_" not in str(p) and "results" not in p.relative_to(workdir).parts[:1]:
            out.append(p)
    return out


def _plex_name(folder: str, known: set[str]) -> str:
    if folder in known:
        return folder
    m = re.match(r"^(.+)_(\d+)$", folder)
    return m.group(1) if m else folder


def _k_fixed(props: dict, mass: float | None) -> bool:
    fix = props.get("msfragger.table.fix-mods", "")
    for item in fix.split(";"):
        parts = [x.strip() for x in item.split(",")]
        if len(parts) >= 3 and parts[1].upper().startswith("K") and parts[2].lower() == "true":
            m = _num(parts[0])
            if m and (mass is None and min(abs(m - tmtkit.TMT_MASS), abs(m - tmtkit.TMTPRO_MASS)) < 0.01
                      or mass is not None and abs(m - mass) < 0.01):
                return True
    return False


def _is_label(m: float, mass: float | None) -> bool:
    if mass is not None:
        return abs(m - mass) < tmtkit.MASS_TOL
    return abs(m - tmtkit.TMT_MASS) < tmtkit.MASS_TOL or abs(m - tmtkit.TMTPRO_MASS) < tmtkit.MASS_TOL


def _labels(mods: str, pep: str, mass: float | None) -> tuple[str, int, int, int]:
    """(N-terminus: 'label' | 'blocked' | 'free', K residues, K labelled, S/T/Y labelled) for one PSM."""
    nterm, k_lab, over = "free", 0, 0
    for site, aa, m in _MOD.findall(mods or ""):
        mv = float(m)
        if site.lower() in ("n-term", "n"):
            if _is_label(mv, mass):
                nterm = "label"
            elif nterm != "label" and any(abs(mv - b) < 0.01 for b, _a in _BLOCKING):
                nterm = "blocked"
            continue
        aa = aa.upper()
        if _is_label(mv, mass):
            if aa == "K":
                k_lab += 1
            elif aa in "STY":
                over += 1
            if site == "1" and aa != "K" and nterm == "free":
                nterm = "label"
        elif site == "1" and any(abs(mv - b) < 0.01 and aa == a for b, a in _BLOCKING[1:]) and nterm == "free":
            nterm = "blocked"
    return nterm, pep.upper().count("K"), k_lab, over


def _channel_columns(header: list[str], known_names: set[str]) -> tuple[list[int], list[str], str]:
    """(column indexes, sample names, how found) of the reporter block."""
    idx = [i for i, h in enumerate(header) if h.startswith("Intensity ") and h.strip() != "Intensity"]
    if idx:
        return idx, [header[i][len("Intensity "):].strip() for i in idx], "Intensity <sample>"
    idx = [i for i, h in enumerate(header) if h in known_names]
    if idx:
        return idx, [header[i] for i in idx], "sample-name columns (an older Philosopher table)"
    return [], [], ""


def _read_psm(path: Path, plex: str, chmap: dict[str, str], lt, mass: float | None, k_fixed: bool,
              min_purity: float | None, reference: set[str], ref_tag: str, max_mb: float) -> tuple[dict | None, str]:
    """One plex's numbers from its psm.tsv, or (None, why)."""
    try:
        size = path.stat().st_size
    except OSError as exc:
        return None, f"{path.name}: {exc}"
    if size > max_mb * 1e6:
        return None, f"{path.parent.name}/psm.tsv is {size / 1e9:.1f} GB, more than the {max_mb / 1000:g} GB read"
    name_to_ch = {n: c for c, n in chmap.items() if not tmtkit.is_na(n)}
    rng = random.Random(7)
    with open(path, encoding="utf-8", errors="replace", newline="") as fh:
        header = fh.readline().rstrip("\r\n").split("\t")
        col = {h: i for i, h in enumerate(header)}
        cidx, cnames, how = _channel_columns(header, set(name_to_ch))
        if not cidx:
            return None, (f"{path.parent.name}/psm.tsv has no reporter-ion columns (Intensity <sample>): was the "
                          f"workflow's isobaric quantification (TMT-Integrator) on?")
        # which channel each column is: the map's name, else the kit's order, else the token in the name
        chans = []
        for k, n in enumerate(cnames):
            if n in name_to_ch:
                chans.append(tmtkit.channel_key(name_to_ch[n]) or name_to_ch[n])
            elif lt is not None and len(cnames) == lt.n:
                chans.append(lt.channels[k])
            else:
                chans.append((tmtkit.parse_sample_name(n) or tmtkit.SampleName("", None, "", False)).channel
                             or str(k + 1))
        na = [tmtkit.is_na(n) for n in cnames]
        ref = [not na[k] and (cnames[k] in reference or chans[k] in reference or tmtkit.is_reference_name(
            cnames[k], ref_tag)) for k in range(len(cnames))]
        named = [k for k in range(len(cnames)) if not na[k]]
        ipur, imod, ipep = col.get("Purity"), col.get("Assigned Modifications"), col.get("Peptide")
        n = 0
        sig = [0] * len(cidx)
        vals = [_Reservoir(rng) for _ in cidx]
        rel = [_Reservoir(rng) for _ in cidx]
        pur_bins = [0] * PURITY_BINS
        pur_res = _Reservoir(rng)
        below = 0
        lab = {"psms": 0, "nterm_eligible": 0, "nterm_label": 0, "blocked": 0, "k": 0, "k_label": 0, "full": 0,
               "over": 0}
        for line in fh:
            row = line.rstrip("\r\n").split("\t")
            if len(row) < len(header) - 1:
                continue
            n += 1
            logs = []
            for j, i in enumerate(cidx):
                v = _num(row[i]) if i < len(row) else None
                if v is not None and v > 0:
                    sig[j] += 1
                    lv = math.log2(v)
                    vals[j].add(lv)
                    logs.append((j, lv))
            pool = sorted(lv for j, lv in logs if not na[j])
            if len(pool) >= 2:
                mid = pool[len(pool) // 2] if len(pool) % 2 else (pool[len(pool) // 2 - 1] + pool[len(pool) // 2]) / 2
                for j, lv in logs:
                    rel[j].add(lv - mid)
            if ipur is not None and ipur < len(row):
                p = _num(row[ipur])
                if p is not None:
                    pur_bins[min(PURITY_BINS - 1, max(0, int(p * PURITY_BINS)))] += 1
                    pur_res.add(p)
                    if min_purity is not None and p < min_purity:
                        below += 1
            if imod is not None and ipep is not None and imod < len(row) and ipep < len(row):
                nterm, k_all, k_lab, over = _labels(row[imod], row[ipep], mass)
                lab["psms"] += 1
                if nterm == "blocked":
                    lab["blocked"] += 1
                else:
                    lab["nterm_eligible"] += 1
                    lab["nterm_label"] += nterm == "label"
                lab["k"] += k_all
                lab["k_label"] += min(k_lab, k_all)
                lab["over"] += over > 0
                lab["full"] += (nterm in ("label", "blocked")) and k_lab >= k_all
    chs = []
    for j in range(len(cidx)):
        q = _quantiles(vals[j].v)
        r = statistics.median(rel[j].v) if rel[j].v else None
        chs.append({"channel": chans[j], "name": cnames[j], "na": na[j], "ref": ref[j],
                    "condition": "" if na[j] else tmtkit.condition_of(cnames[j]),
                    "psms_with_signal": sig[j], "signal_share": round(sig[j] / n, 4) if n else None,
                    "missing_share": round(1 - sig[j] / n, 4) if n else None,
                    "log2": q, "rel_log2": round(r, 3) if r is not None else None, "flags": []})
    med_pur = statistics.median(pur_res.v) if pur_res.v else None
    out = {"name": plex, "folder": path.parent.name, "psms": n, "columns": how, "channels": chs,
           "named": len(named), "labelling": _lab_shares(lab, k_fixed),
           "purity": {"median": round(med_pur, 3) if med_pur is not None else None, "bins": pur_bins,
                      "below_min": round(below / sum(pur_bins), 4) if sum(pur_bins) and min_purity is not None
                      else None, "n": sum(pur_bins)}}
    return out, ""


def _lab_shares(lab: dict, k_fixed: bool) -> dict:
    def share(a, b):
        return round(a / b, 4) if b else None

    return {"psms": lab["psms"], "nterm": share(lab["nterm_label"], lab["nterm_eligible"]),
            "k": share(lab["k_label"], lab["k"]), "full": share(lab["full"], lab["psms"]),
            "overlabel": share(lab["over"], lab["psms"]), "blocked": lab["blocked"], "k_fixed": k_fixed,
            "counts": dict(lab)}


def _report_tables(workdir: Path) -> list[dict]:
    out = []
    rep = Path(workdir) / "tmt-report"
    if not rep.is_dir():
        return out
    for p in sorted(rep.glob("*.tsv")):
        try:
            with open(p, encoding="utf-8", errors="replace") as fh:
                head = fh.readline().rstrip("\r\n").split("\t")
        except OSError:
            continue
        k = head.index("ReferenceIntensity") + 1 if "ReferenceIntensity" in head else 0
        out.append({"file": f"tmt-report/{p.name}", "channels": head[k:] if k else []})
    return out


# ------------------------------------------------------------------- run --


def run(workdir: Path, maps: dict[str, dict[str, str]] | None = None, props: dict | None = None,
        reference=None, limits: dict | None = None, max_mb: float = MAX_MB) -> Result:
    """The TMT QC of a search's output folder. maps: {plex: {channel: name}} (the annotation files FragPipe used);
    props: the workflow (fragpipe.read_properties); reference: the bridge / pool channels or names
    (tmt_reference / reference_channel); limits: {"min_labelling", "min_purity"}. Reads, writes nothing."""
    workdir = Path(workdir)
    props = props or {}
    maps = {str(k): dict(v) for k, v in (maps or {}).items()}
    lim = {"min_labelling": MIN_LABELLING, "min_purity": MIN_PURITY, **{k: v for k, v in (limits or {}).items()
                                                                       if v is not None}}
    res = Result(limits={**lim, "empty_share": EMPTY_SHARE, "empty_log2": EMPTY_LOG2, "reference_far_log2":
                         REF_FAR_LOG2, "min_psms": MIN_PSMS})
    label = props.get("tmtintegrator.channel_num", "").strip()
    lt = tmtkit.label_type(label)
    if lt is None and maps:
        n = {len(m) for m in maps.values()}
        lt = next((x for x in tmtkit.LABEL_TYPES.values() if {x.n} == n), None)
    mass = lt.mass if lt else None
    k_fixed = _k_fixed(props, mass)
    min_pur = _num(props.get("tmtintegrator.min_purity"))
    ql = props.get("tmtintegrator.quant_level", "").strip()
    res.workflow = {"label": lt.name if lt else label, "quant": {"2": "MS2", "3": "MS3"}.get(ql, ""),
                    "min_purity": min_pur, "add_ref": props.get("tmtintegrator.add_Ref", "").strip(),
                    "ref_tag": props.get("tmtintegrator.ref_tag", "").strip(),
                    "prot_norm": {"0": "None", "1": "MD", "2": "GN", "-1": "all"}.get(
                        props.get("tmtintegrator.prot_norm", "").strip(), ""), "k_fixed": k_fixed,
                    "known": bool(props)}
    ref_tag = res.workflow["ref_tag"] if res.workflow["add_ref"] == "-1" else ""
    refs = {str(r) for r in tmtkit._refs(reference)}
    refs |= {tmtkit.channel_key(r) for r in refs if tmtkit.channel_key(r)}
    res.tables = _report_tables(workdir)
    files = psm_files(workdir)
    fp = {re.sub(r"[^A-Za-z0-9_]", "_", k): k for k in maps}  # FragPipe keeps only A-Z a-z 0-9 _ in experiments
    for p in files:
        plex = _plex_name(p.parent.name if p.parent != workdir else workdir.name, set(maps) | set(fp))
        plex = fp.get(plex, plex)
        chmap = maps.get(plex) or maps.get(p.parent.name) or {}
        got, why = _read_psm(p, plex, chmap, lt, mass, k_fixed, min_pur, refs, ref_tag, max_mb)
        if got is None:
            res.notes.append(why)
            continue
        res.plexes.append(got)
    if not files:
        res.notes.append(f"no psm.tsv in {workdir.name}/: the labelling, purity and per-channel signal need "
                         f"FragPipe's psm.tsv files" + ("; only the tmt-report header was checked" if res.tables
                                                        else ""))
    if not res.found:
        res.reason = "; ".join(res.notes) or f"no TMT output (psm.tsv, tmt-report/) in {workdir.name}/"
        return res
    _totals(res)
    _judge(res, maps, lt)
    return res


def _totals(res: Result) -> None:
    c: dict[str, int] = {}
    for p in res.plexes:
        for k, v in (p["labelling"].get("counts") or {}).items():
            c[k] = c.get(k, 0) + v
    if c:
        res.labelling = _lab_shares(c, res.workflow.get("k_fixed", False))
    bins = [0] * PURITY_BINS
    for p in res.plexes:
        bins = [a + b for a, b in zip(bins, p["purity"]["bins"], strict=True)]
    if sum(bins):
        meds = [p["purity"]["median"] for p in res.plexes if p["purity"]["median"] is not None]
        below = [p["purity"]["below_min"] * p["purity"]["n"] for p in res.plexes if p["purity"]["below_min"] is not None]
        res.purity = {"bins": bins, "n": sum(bins), "median": _bin_median(bins),
                      "plex_medians": meds, "below_min": round(sum(below) / sum(bins), 4) if below else None,
                      "min_purity": res.workflow.get("min_purity")}


def _bin_median(bins: list[int]) -> float | None:
    n = sum(bins)
    if not n:
        return None
    seen = 0
    for k, b in enumerate(bins):
        if seen + b >= n / 2:
            frac = (n / 2 - seen) / b if b else 0
            return round((k + frac) / PURITY_BINS, 3)
        seen += b
    return 1.0


def _names(ps: list[str], limit: int = 6) -> str:
    return ", ".join(ps[:limit]) + (f" and {len(ps) - limit} more" if len(ps) > limit else "")


def _judge(res: Result, maps: dict, lt) -> None:
    lim = res.limits
    judged = [p for p in res.plexes if p["labelling"]["psms"] >= MIN_PSMS]
    low = [p for p in judged if p["labelling"]["full"] is not None and p["labelling"]["full"] < lim["min_labelling"]]
    if low:
        res.problems.append(("TMT_LOW_LABELLING", f"{len(low)} of {len(res.plexes)} plex(es) are labelled below "
                             f"{lim['min_labelling']:.0%}: " + _names([
                                 f"{p['name']} ({p['labelling']['full']:.1%} of PSMs fully labelled, N-termini "
                                 f"{_pct(p['labelling']['nterm'])}, K {_pct(p['labelling']['k'])}"
                                 + (" fixed" if p['labelling']['k_fixed'] else "") + ")" for p in low]) + "."))
    pur = [p for p in res.plexes if p["purity"]["n"] >= MIN_PSMS and p["purity"]["median"] is not None
           and p["purity"]["median"] < lim["min_purity"]]
    if pur:
        res.problems.append(("TMT_LOW_PURITY", f"the median precursor purity is below {lim['min_purity']:g} in "
                             + _names([f"{p['name']} ({p['purity']['median']:.2f})" for p in pur]) + ": co-isolated "
                             "peptides add their reporter ions, which pulls ratios toward 1"
                             + (" (reporter ions from MS2)" if res.workflow.get("quant") == "MS2" else "") + "."))
    empty, far = [], []
    for p in res.plexes:
        for ch in p["channels"]:
            if ch["na"] or p["psms"] < MIN_PSMS:
                continue
            if (ch["signal_share"] is not None and ch["signal_share"] < EMPTY_SHARE) or \
                    (ch["rel_log2"] is not None and ch["rel_log2"] <= EMPTY_LOG2):
                ch["flags"].append("empty")
                empty.append(f"{p['name']} {ch['channel']} {ch['name']} (signal in {_pct(ch['signal_share'])} of "
                             f"PSMs, {_rel(ch['rel_log2'])})")
            elif ch["ref"] and ch["rel_log2"] is not None and abs(ch["rel_log2"]) > REF_FAR_LOG2:
                ch["flags"].append("reference far")
                far.append(f"{p['name']} {ch['channel']} {ch['name']} ({_rel(ch['rel_log2'])})")
    if empty:
        res.problems.append(("TMT_CHANNEL_EMPTY", "named channel(s) with almost no reporter signal: "
                             + _names(empty) + "."))
    if far:
        res.problems.append(("TMT_REFERENCE_WEAK", "the reference (bridge / pool) channel is more than 2-fold from "
                             "the other channels: " + _names(far) + "."))
    # the tables against the map and the label type
    bad = []
    for p in res.plexes:
        n = len(p["channels"])
        if lt is not None and n != lt.n:
            bad.append(f"{p['name']}'s psm.tsv has {n} reporter columns, {lt.name} has {lt.n}")
        m = maps.get(p["name"]) or maps.get(p["folder"])
        if m:
            want = {x for x in m.values() if not tmtkit.is_na(x)}
            have = {c["name"] for c in p["channels"] if not c["na"]}
            if want != have:
                miss = sorted(want - have)
                extra = sorted(have - want)
                bad.append(f"{p['name']}: the map names " + (f"{_names(miss, 3)} that the table does not have"
                                                             if miss else "")
                           + ("; " if miss and extra else "") + (f"the table has {_names(extra, 3)}" if extra else ""))
    if not res.plexes and maps:
        want = {x for m in maps.values() for x in m.values() if not tmtkit.is_na(x)}
        for t in res.tables:
            if t["channels"] and t["file"].endswith("_MD.tsv") and "gene" in t["file"] and set(t["channels"]) != want:
                bad.append(f"{t['file']} reports {len(t['channels'])} channels, the map names {len(want)}")
    if bad:
        res.problems.append(("TMT_LABEL_TYPE_MISMATCH", "the search's tables do not match the channel map: "
                             + "; ".join(bad[:4]) + "."))
    names_seen = [c["name"] for p in res.plexes for c in p["channels"] if not c["na"]] or \
        [n for t in res.tables[:1] for n in t["channels"]]
    plexes = {p["name"] for p in res.plexes} | {p["folder"] for p in res.plexes} | set(maps)
    unknown = [n for n in names_seen if "_" not in n or n.rsplit("_", 1)[0] in plexes]
    if unknown:
        res.problems.append(("TMT_CONDITIONS_UNKNOWN", f"{len(unknown)} channel name(s) carry no condition (e.g. "
                             f"{unknown[0]}): FragPipe named them <plex>_<channel>, or the names have no '_'; the "
                             f"report takes the text before the first '_' as the condition."))


def _pct(v) -> str:
    return "–" if v is None else f"{v:.0%}" if v >= 0.995 or v < 0.1 else f"{v:.1%}"


def _rel(v) -> str:
    return "–" if v is None else f"{2 ** v:.2g}× the median"


# --------------------------------------------------------------- a folder --


def _workflow_props(workdir: Path, dest: Path) -> dict:
    from ionomos.fragpipe import read_properties

    for p in [Path(workdir) / "fragpipe.workflow", *sorted((Path(dest) / names.RUN_DIR).glob("*.workflow"))]:
        try:
            return read_properties(p.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    return {}


def maps_for(dest: Path, record: dict | None, experiments: list[str]) -> tuple[dict[str, dict[str, str]], str]:
    """The channel maps FragPipe used, and where they came from: each plex's annotation file next to its raws
    (Ionomos' or the user's), else experiment.yaml / the filed tmt: block, else what intake read (plan.tmt)."""
    dest = Path(dest)
    plan = (record or {}).get("plan") or {}
    maps: dict[str, dict[str, str]] = {}
    files = tmtkit.channel_map_from_folder(dest)
    folder_of: dict[str, str] = {}
    for m in plan.get("manifest") or []:
        folder_of.setdefault(str(m.get("experiment")), str(Path(str(m.get("file", ""))).parent.as_posix()))
    for exp in experiments:
        f = folder_of.get(exp, "")
        fm = files.get("" if f in (".", "") else f)
        if fm and fm.file and fm.channels:
            maps[exp] = fm.channels
    if len(maps) == len(experiments) and maps:
        return maps, "annotation files"
    tmt = (plan.get("overrides") or {}).get("tmt") or {}
    try:
        from ionomos.manifest import load_overrides

        tmt = load_overrides(dest).tmt or tmt
    except Exception:  # noqa: BLE001 - a broken experiment.yaml only costs the map
        pass
    try:
        for exp, ch in tmtkit.maps_from_block(tmt, experiments).items():
            maps.setdefault(exp, ch)
    except tmtkit.TmtError:
        pass
    for p in (plan.get("tmt") or {}).get("plexes") or []:
        if p.get("channels"):
            maps.setdefault(str(p["name"]), dict(p["channels"]))
    return maps, "experiment.yaml" if maps else ""


def run_folder(folder: Path, record: dict | None = None, workdir: Path | None = None, settings=None,
               max_mb: float = MAX_MB) -> Result:
    """run() for an experiment folder: its FragPipe output, the channel maps (maps_for), the workflow FragPipe
    saved, the reference (experiment.yaml tmt.reference_channel, analysis.tmt_reference) and the limits from
    the analysis settings (tmt_min_labelling, tmt_min_purity)."""
    folder = Path(folder)
    if record is None:
        try:
            record = json.loads(names.status_path(folder).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            record = {}
    if workdir is None:
        workdir = folder / "fragpipe" if (folder / "fragpipe").is_dir() else folder
    exps = sorted({_plex_name(p.parent.name, set()) for p in psm_files(workdir)}
                  | {str(m.get("experiment")) for m in ((record or {}).get("plan") or {}).get("manifest") or []})
    maps, _src = maps_for(folder, record, exps)
    tmt = (((record or {}).get("plan") or {}).get("overrides") or {}).get("tmt") or {}
    reference = list(getattr(settings, "tmt_reference", None) or []) or tmtkit._refs(tmt.get("reference_channel"))
    limits = {"min_labelling": getattr(settings, "tmt_min_labelling", None),
              "min_purity": getattr(settings, "tmt_min_purity", None)}
    return run(workdir, maps, _workflow_props(workdir, folder), reference, limits, max_mb)


# ---------------------------------------------------------------- outputs --


def summary(res: Result | None, files: list[str] | None = None, reason: str = "") -> dict:
    """analysis.json "tmt_qc" (and results/tmt_qc.json, with the channels)."""
    if res is None or not res.found:
        return {"ran": False, "reason": reason or (res.reason if res else "")}
    return {"ran": True, "files": files or [], "workflow": res.workflow, "limits": res.limits,
            "labelling": {k: v for k, v in res.labelling.items() if k != "counts"},
            "purity": {k: v for k, v in res.purity.items() if k != "bins"},
            "plexes": [{"name": p["name"], "folder": p["folder"], "psms": p["psms"],
                        "labelling": {k: v for k, v in p["labelling"].items() if k != "counts"},
                        "purity_median": p["purity"]["median"],
                        "flagged": {c["channel"]: c["flags"] for c in p["channels"] if c["flags"]}}
                       for p in res.plexes],
            "tables": [t["file"] for t in res.tables], "problems": [list(x) for x in res.problems],
            "notes": res.notes}


def report_payload(res: Result | None) -> dict | None:
    """The report's d["tmtqc"] (report.js renderTmt, sectionfigs' tmt_* figures), or None when nothing was read."""
    if res is None or not res.found:
        return None
    return {"workflow": res.workflow, "limits": res.limits, "labelling": {k: v for k, v in res.labelling.items()
                                                                          if k != "counts"},
            "purity": res.purity, "plexes": [
                {"name": p["name"], "folder": p["folder"], "psms": p["psms"], "columns": p["columns"],
                 "labelling": {k: v for k, v in p["labelling"].items() if k != "counts"},
                 "purity": p["purity"],
                 "ch": [{"c": c["channel"], "n": c["name"], "cond": c["condition"], "na": c["na"], "ref": c["ref"],
                         "sig": c["signal_share"], "q": c["log2"], "rel": c["rel_log2"], "flags": c["flags"]}
                        for c in p["channels"]]} for p in res.plexes],
            "tables": res.tables, "notes": res.notes, "problems": [list(x) for x in res.problems]}


COLUMNS = ["plex", "channel", "sample", "condition", "unused", "reference", "psms", "psms_with_signal",
           "signal_share", "log2_q05", "log2_q25", "log2_median", "log2_q75", "log2_q95", "log2_vs_median", "flags"]


def table_rows(plex: dict) -> list[dict]:
    out = []
    for c in plex["channels"]:
        q = c["log2"] or [None] * 5
        out.append({"plex": plex["name"], "channel": c["channel"], "sample": c["name"], "condition": c["condition"],
                    "unused": "yes" if c["na"] else "", "reference": "yes" if c["ref"] else "", "psms": plex["psms"],
                    "psms_with_signal": c["psms_with_signal"], "signal_share": c["signal_share"],
                    "log2_q05": q[0], "log2_q25": q[1], "log2_median": q[2], "log2_q75": q[3], "log2_q95": q[4],
                    "log2_vs_median": c["rel_log2"], "flags": "; ".join(c["flags"])})
    return out


def write(res: Result, results: Path) -> list[Path]:
    """results/tmt_qc.json and a table per plex (tmt_qc_<plex>.tsv). Ionomos' own folder: written over each run."""
    from ionomos.downstream.charts import safe_name
    from ionomos.downstream.tables import write_tsv

    results = Path(results)
    results.mkdir(parents=True, exist_ok=True)
    out = []
    for p in res.plexes:
        out.append(write_tsv(results / safe_name(names.TMT_QC_TABLE.format(plex=p["name"])), COLUMNS,
                             table_rows(p)))
    js = results / names.TMT_QC_FILE
    full = {**summary(res, [f.name for f in out]), "plexes_full": report_payload(res)["plexes"]}
    js.write_text(json.dumps(full, indent=2, default=str), encoding="utf-8")
    return [js, *out]


def text(res: Result) -> list[str]:
    """`ionomos tmt qc`: the numbers in a few lines."""
    if not res.found:
        return [f"no TMT QC: {res.reason}"]
    w = res.workflow
    out = [f"label type {w.get('label') or 'unknown'}" + (f", reporter ions from {w['quant']}" if w.get("quant") else "")
           + (f", TMT-Integrator min purity {w['min_purity']:g}" if w.get("min_purity") is not None else "")]
    lab = res.labelling
    if lab:
        out.append(f"labelling: {_pct(lab.get('full'))} of {lab.get('psms', 0):,} PSMs fully labelled; N-termini "
                   f"{_pct(lab.get('nterm'))}; K {_pct(lab.get('k'))}" + (" (fixed in the workflow)" if
                                                                         lab.get("k_fixed") else "")
                   + f"; S/T/Y over-labelled {_pct(lab.get('overlabel'))}")
    if res.purity:
        out.append(f"precursor purity: median {res.purity['median']:.2f}"
                   + (f", {res.purity['below_min']:.0%} below {res.purity['min_purity']:g}"
                      if res.purity.get("below_min") is not None else ""))
    for p in res.plexes:
        out.append(f"plex {p['name']} ({p['psms']:,} PSMs):")
        for c in p["channels"]:
            q = c["log2"]
            out.append(f"  {c['channel']:>5} {c['name'][:28]:<28} {'NA ' if c['na'] else ''}"
                       f"signal {_pct(c['signal_share'])}, median log2 {q[2] if q else '–'}, "
                       f"vs median {c['rel_log2'] if c['rel_log2'] is not None else '–'}"
                       + (f"  [{', '.join(c['flags'])}]" if c["flags"] else "") + ("  (reference)" if c["ref"] else ""))
    for t in res.tables:
        out.append(f"{t['file']}: {len(t['channels'])} channels")
    for code, msg in res.problems:
        out.append(f"{code}: {msg}")
    out += [f"note: {n}" for n in res.notes]
    return out
