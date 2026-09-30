"""
TMT across plexes: every plex on one scale before the statistics (D48).

    m, info, notes = normalise(m, settings)       # after loading, before fpa.process
    pca = pca_before(processed, settings)         # the report's PCA of the same data before it

A TMT plex (a MaxQuant experiment, an MSstatsTMT mixture, a Proteome Discoverer file, a group of SDRF files
sharing their channels) measures its channels in the same scans, so channels compare well within a plex. Between
plexes the same protein jumps with the peptides that happened to be picked for that plex: a PCA of several plexes
separates them by plex, not by condition. IRS, internal reference scaling (Plubell et al., Mol Cell Proteomics
2017, 16:873; pwilmart/IRS_normalization), fixes that with a reference channel, the same pooled sample, in every
plex. In log2, per protein i and plex p:

    r_ip = log2(mean of plex p's reference channels)        several references in a plex: their (linear) mean
    g_i  = mean of r_ip over the plexes                     the geometric mean of the references
    every channel of plex p, protein i:  + (g_i - r_ip)     the reference channels then agree across plexes

Choices (settings `irs`, `tmt_reference`; docs/DECISIONS.md D48):
    reference   the channels named by `tmt_reference` (a channel such as 126 or 134N, or a sample name), else the
                SDRF's pooled rows, else samples or conditions named pool / pooled / bridge / reference / norm.
                They carry no biology, so they leave the matrix after scaling (MSstatsTMT removes its Norm channels
                too). A protein whose reference is missing in a plex can't be put on the common scale: its values in
                that plex become missing (Plubell's protocol requires complete references).
    sum         without a reference, each plex's own mean (plex-sum IRS, as in pwilmart's notebooks without a
                pool). It is only valid when every plex holds the same mix of conditions, otherwise it would scale
                real differences away: "auto" uses it only for such balanced designs and otherwise leaves the data
                alone with a warning (the doctor's TMT_PLEXES_NOT_NORMALISED).
    order       IRS runs on the loaded values; sample-loading normalisation (analysis.normalize) follows in
                fpa.process. In log2 both are additive, so this equals Plubell's SL -> IRS order up to a constant
                per sample, which the median centring removes.
    not twice   TMT-Integrator abundances are already ratios to the reference (or virtual reference) channel, and
                MSstatsTMT input is normalised to its Norm channels by the loader (engines.load_msstats_tmt, the
                way MSstatsTMT does it): neither is scaled again, and the report says so.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import replace

from ionomos.downstream.quant import QuantMatrix

# reporter channels in kit order: MaxQuant numbers its "Reporter intensity corrected N" columns in this order, and
# OpenMS / quantms write the index (1..n) of this order in MSstatsTMT's Channel column
TMT_ORDERS = {
    2: ["126", "127"],
    6: ["126", "127", "128", "129", "130", "131"],
    8: ["113", "114", "115", "116", "117", "118", "119", "121"],  # iTRAQ 8-plex
    4: ["114", "115", "116", "117"],  # iTRAQ 4-plex
    10: ["126", "127N", "127C", "128N", "128C", "129N", "129C", "130N", "130C", "131"],
    11: ["126", "127N", "127C", "128N", "128C", "129N", "129C", "130N", "130C", "131N", "131C"],
    16: ["126", "127N", "127C", "128N", "128C", "129N", "129C", "130N", "130C", "131N", "131C", "132N", "132C",
         "133N", "133C", "134N"],
    18: ["126", "127N", "127C", "128N", "128C", "129N", "129C", "130N", "130C", "131N", "131C", "132N", "132C",
         "133N", "133C", "134N", "134C", "135N"],
}
REFERENCE_WORDS = re.compile(r"(?i)(?:^|[_\-\s.])(?:pool(?:ed)?|bridge|ref(?:erence)?|norm|irs)(?:$|[_\-\s.\d])")
IRS_MODES = ("auto", "reference", "sum", "none")


def channel_key(label) -> str:
    """'TMT127N', 'TMTpro 134N', 'tmt10plex-127C', '127n', 127 -> '127N' / '127' ('' when it is no channel)."""
    m = re.search(r"(\d{3})\s*([NC]?)(?:D)?\s*$", str(label or "").strip(), re.I)
    return (m.group(1) + m.group(2).upper()) if m else ""


def same_channel(a: str, b: str) -> bool:
    """126 = 126; 131 = 131N (the TMT 10-plex's last channel is written both ways)."""
    a, b = channel_key(a), channel_key(b)
    return bool(a) and (a == b or {a, b} == {"131", "131N"})


def channel_from_index(k: int, n: int) -> str:
    """Channel k (0-based) of an n-channel kit, or str(k + 1) for a kit size not listed."""
    order = TMT_ORDERS.get(n)
    return order[k] if order and 0 <= k < len(order) else str(k + 1)


# ------------------------------------------------------------------ helpers --


def plexes_of(m: QuantMatrix) -> dict[str, str]:
    """sample -> plex, for the samples that have one."""
    return {s: str(p) for s, p in (m.meta.get("plex") or {}).items() if s in m.condition and p not in (None, "")}


def find_references(m: QuantMatrix, wanted: list[str] | None = None) -> tuple[list[str], str]:
    """(reference samples, where they came from). wanted: the tmt_reference setting (channels or sample names)."""
    channel = m.meta.get("channel") or {}
    if wanted:
        want = [str(w).strip() for w in wanted if str(w).strip()]
        hits = [s for s in m.samples
                if any(w.lower() == s.lower() or w.lower() == m.condition[s].lower()
                       or (channel.get(s) and channel_key(w) and same_channel(w, channel[s])) for w in want)]
        return hits, "tmt_reference " + ", ".join(want)
    listed = [s for s in m.samples if s in set(m.meta.get("reference_samples") or [])]
    if listed:
        return listed, m.meta.get("reference_from") or "the design"
    named = [s for s in m.samples if REFERENCE_WORDS.search(m.condition[s]) or REFERENCE_WORDS.search(s)
             or m.condition[s].lower() in ("norm", "pool", "pooled", "bridge", "reference", "ref")]
    return named, "sample / condition names (pool, bridge, reference, norm)"


def keep_samples(m: QuantMatrix, keep: list[int], values=None) -> QuantMatrix:
    """m with only the samples at these indices (values: new values for all samples, default m.values)."""
    values = m.values if values is None else values
    samples = [m.samples[j] for j in keep]
    meta = dict(m.meta)
    for k, v in list(meta.items()):  # per-sample findings follow the samples that stay
        if isinstance(v, dict) and k in ("plex", "channel", "runs", "design", "techrep", "fraction"):
            meta[k] = {s: x for s, x in v.items() if s in samples}
    return replace(m, samples=samples, values=[[row[j] for j in keep] for row in values],
                   condition={s: m.condition[s] for s in samples},
                   replicate={s: m.replicate[s] for s in samples if s in m.replicate},
                   columns={s: m.columns[s] for s in samples if s in m.columns}, notes=list(m.notes), meta=meta)


def _log_mean(vals: list[float]) -> float:
    """log2 of the linear mean of log2 values (the mean of two reference channels, or a plex's channels)."""
    return math.log2(sum(2.0 ** v for v in vals) / len(vals))


def before_matrix(m: QuantMatrix) -> dict:
    """What the report's 'before' PCA needs: the values of every feature and sample as they were."""
    return {"ids": [f.id for f in m.features], "samples": list(m.samples), "values": [list(r) for r in m.values]}


# ---------------------------------------------------------------------- IRS --


def normalise(m: QuantMatrix, settings) -> tuple[QuantMatrix, dict | None, list[str]]:
    """IRS across the plexes of a TMT matrix. Returns (matrix, what was done for analysis.json or None, notes).
    Anything that isn't several TMT plexes comes back unchanged (info None)."""
    notes: list[str] = []
    mode = (getattr(settings, "irs", "auto") or "auto").lower()
    wanted = list(getattr(settings, "tmt_reference", []) or [])
    explicit = mode in ("reference", "sum") or bool(wanted)
    if m is None or not m.features or m.kind != "intensity" or m.meta.get("precomputed"):
        return m, None, notes
    done = m.meta.get("bridge")
    if isinstance(done, dict) and done.get("applied"):  # the loader did it already (MSstatsTMT's Norm channels)
        return m, done, notes
    if m.meta.get("ratio_to_reference"):
        info = {"method": "none", "applied": False,
                "reason": f"{m.meta['ratio_to_reference']} abundances are already log2 ratios to the reference "
                          "channel, so IRS is not applied a second time"}
        if explicit or len(set(plexes_of(m).values())) > 1:
            notes.append(info["reason"])
        return m, info, notes
    plex = plexes_of(m)
    groups: dict[str, list[int]] = {}
    for j, s in enumerate(m.samples):
        if s in plex:
            groups.setdefault(plex[s], []).append(j)
    if m.exp != "TMT" or len(groups) < 2:
        if explicit and m.exp == "TMT":
            notes.append("IRS: one plex only (or no plex known for the samples), nothing to put on a common scale")
        return m, None, notes
    info: dict = {"method": "none", "applied": False, "plexes": {p: len(ix) for p, ix in groups.items()}}
    if mode == "none":
        info["reason"] = "switched off (irs: none)"
        notes.append(f"{len(groups)} TMT plexes were not put on a common scale (irs: none); expect the PCA to "
                     "separate them by plex")
        return m, info, notes
    unplexed = [s for s in m.samples if s not in plex]
    refs, ref_from = find_references(m, wanted)
    ref_set = set(refs)
    ref_plexes = {plex[s] for s in refs if s in plex}
    excluded = set(getattr(settings, "exclude_samples", []) or [])
    renamed = dict(getattr(settings, "sample_conditions", {}) or {})

    def balanced() -> bool:
        mix = [Counter(renamed.get(m.samples[j], m.condition[m.samples[j]]) for j in ix
                       if m.samples[j] not in excluded and m.samples[j] not in ref_set) for ix in groups.values()]
        return all(c == mix[0] for c in mix) and bool(mix[0])

    use = None
    if mode in ("auto", "reference") and refs and ref_plexes == set(groups):
        use = "reference"
    elif mode == "sum" or (mode == "auto" and balanced()):
        use = "sum"
    if use is None:
        if mode == "reference" or wanted:
            why = (f"no reference channel matched {', '.join(wanted)}" if wanted and not refs else
                   "plexes without a reference channel: " + ", ".join(sorted(set(groups) - ref_plexes)))
        else:
            why = ("no reference (bridge) channel was found, and the plexes hold different mixes of conditions, so "
                   "each plex's own mean can't stand in for one")
        info["reason"] = why
        notes.append(f"the {len(groups)} TMT plexes were NOT put on a common scale: {why}. Set analysis.tmt_reference "
                     "to the pooled channel (e.g. 126), or mark it pooled in the SDRF")
        return m, info, notes
    if use == "sum" and not balanced():
        notes.append("IRS from each plex's own mean although the plexes hold different mixes of conditions "
                     "(irs: sum): real differences between plexes may be scaled away")
    before = before_matrix(m)
    vals = [list(r) for r in m.values]
    scaled = dropped = 0
    for i, row in enumerate(m.values):
        per: dict[str, float] = {}
        for p, ix in groups.items():
            if use == "reference":
                obs = [row[j] for j in ix if m.samples[j] in ref_set and row[j] is not None]
            else:
                cols = [j for j in ix if m.samples[j] not in excluded]
                obs = [row[j] for j in cols] if cols and all(row[j] is not None for j in cols) else []
            if obs:
                per[p] = _log_mean(obs)
        if not per:
            continue
        g = sum(per.values()) / len(per)
        scaled += 1
        for p, ix in groups.items():
            for j in ix:
                if vals[i][j] is None:
                    continue
                if p in per:
                    vals[i][j] = vals[i][j] + g - per[p]
                else:
                    vals[i][j] = None
                    dropped += 1
    keep = [j for j, s in enumerate(m.samples) if not (use == "reference" and s in ref_set)]
    out = keep_samples(m, keep, vals)
    out.meta["bridge_before"] = {**before, "samples": [before["samples"][j] for j in keep],
                                 "values": [[r[j] for j in keep] for r in before["values"]]}
    how = (f"reference channel{'s' if len(refs) > 1 else ''} {', '.join(refs[:6])}{'…' if len(refs) > 6 else ''} "
           f"(from {ref_from})" if use == "reference" else "each plex's own mean (no reference channel; the plexes "
                                                            "hold the same mix of conditions)")
    info.update({"method": "IRS (reference channel)" if use == "reference" else "IRS (plex means)", "applied": True,
                 "reference": refs if use == "reference" else [], "reference_from": ref_from if use == "reference"
                 else "", "proteins_scaled": scaled, "values_dropped": dropped,
                 "removed": [m.samples[j] for j in range(len(m.samples)) if j not in keep]})
    notes.append(f"IRS put {len(groups)} TMT plexes on one scale using {how}: {scaled:,} proteins scaled"
                 + (f", {dropped:,} values missing where a plex had no reference for that protein" if dropped else "")
                 + (f"; the reference channels were then left out ({len(refs)})" if use == "reference" else ""))
    if unplexed:
        notes.append("IRS: samples without a plex were left as they are: " + ", ".join(unplexed[:8]))
    out.meta["bridge"] = info
    return out, info, notes


def pca_before(p, settings) -> dict | None:
    """The PCA of the analysed features and samples as they were before the plexes were put on one scale,
    with the same sample normalisation, so the report can show the plex effect and its removal."""
    from ionomos.downstream import fpa, qc

    pm = p.m
    b = pm.meta.get("bridge_before")
    if not b or not pm.features:
        return None
    col = {s: j for j, s in enumerate(b["samples"])}
    if any(s not in col for s in pm.samples):
        return None
    first: dict[str, int] = {}
    for k, fid in enumerate(b["ids"]):
        first.setdefault(fid, k)
    rows = [[b["values"][first[f.id]][col[s]] for s in pm.samples] if f.id in first else [None] * len(pm.samples)
            for f in pm.features]
    m0 = replace(pm, values=rows)
    m0 = fpa.normalize(m0, getattr(settings, "normalize", "median"))
    return qc.pca(m0.values, getattr(settings, "pca_features", 500))
