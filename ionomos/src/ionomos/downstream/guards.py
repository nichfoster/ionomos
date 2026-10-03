"""
Guards for input that is malformed but plausible, and for statistics that can go wrong without an error (D60).

    notes = check_input(m)                           # right after loading, in place
    found = statistics(processed, diffs, settings, model)   # after the comparisons
    issues(found)                                    # -> doctor issues (the doctor calls this)

check_input() makes a loaded QuantMatrix safe for everything after it and says what it changed:

    values no instrument produces (|log2| > 100, NaN, infinity)   -> missing, counted in a note
    two columns with one sample name (the reader can only keep one) -> the repeat is dropped, with a note
    feature IDs used by several rows, rows without an ID            -> a note (each row stays its own feature)
    a sample without a single value                                 -> a note

statistics() looks at each comparison for the cases where a p-value is computed but does not mean what
it says, and reports each one (never hides or silently repairs it):

    NO_RESIDUAL_DF     features tested with no replicate spread of their own (one value per group):
                       their p-values rest on the variance prior alone
    VARIANCE_PRIOR     limma's prior could not be estimated (fewer than 3 features with replicate spread),
                       or its fit did not converge (it stopped at the lower edge of its search range)
    ZERO_VARIANCE      features whose replicates are exactly equal in every group (rounded, copied or
                       constant values): limma replaces the variance by the prior, a t-test gives p = 0
    IDENTICAL_SAMPLES  two samples with exactly the same values (a file loaded twice, a copied column)

Nothing here changes a number of a well-formed analysis: the checks only read, and check_input() only
touches values that could not be analysed anyway.
"""
from __future__ import annotations

import math

LOG2_LIMIT = 100.0        # |log2 value| above this is not a measurement (2^100 = 1e30)
PRIOR_DF_BOUND = 5000.0   # the ML fit of the prior df searches from 2 up to ~9998 (stats._fit_f_dist_unequal);
PRIOR_DF_FLOOR = 2.001    # above the first it is "one pooled variance", at the second it did not converge
ZERO_VAR_SHARE = 0.05     # ZERO_VARIANCE is raised from this share of the tested features (or any hit)


def check_input(m) -> list[str]:
    """Make a freshly loaded QuantMatrix safe to analyse, in place. Returns notes on what was found."""
    notes: list[str] = []
    if m is None:
        return notes
    seen: set[str] = set()
    keep, dropped = [], []
    for j, s in enumerate(m.samples):
        if s in seen:
            dropped.append(s)
        else:
            seen.add(s)
            keep.append(j)
    if dropped:
        m.samples = [m.samples[j] for j in keep]
        m.values = [[row[j] for j in keep] for row in m.values]
        names = list(dict.fromkeys(dropped))
        notes.append("the table has more than one column named " + ", ".join(names[:6]) + ("…" if len(names) > 6 else "")
                     + "; only one of each could be read, so the repeats were left out. Give each sample its own name")
    bad = 0
    for row in m.values:
        for j, v in enumerate(row):
            if v is not None and not (isinstance(v, (int, float)) and math.isfinite(v) and abs(v) <= LOG2_LIMIT):
                row[j] = None
                bad += 1
    if bad:
        notes.append(f"{bad:,} value(s) are outside any measurable range (log2 beyond ±{LOG2_LIMIT:g}, or not a "
                     "finite number) and count as missing")
    for c in m.meta.get("precomputed") or []:  # a results table: fold changes as given
        for key in ("fc", "p", "q"):
            col = c.get(key)
            if col is not None:
                c[key] = [v if v is not None and math.isfinite(v) and (key != "fc" or abs(v) <= LOG2_LIMIT) else None
                          for v in col]
    counts: dict[str, int] = {}
    for f in m.features:
        counts[f.id] = counts.get(f.id, 0) + 1
    blank = counts.pop("", 0)
    dup = {k: n for k, n in counts.items() if n > 1}
    if dup:
        top = sorted(dup, key=lambda k: -dup[k])[:3]
        notes.append(f"{sum(dup.values()):,} rows share their ID with another row (" +
                     ", ".join(f"{k[:40]} ×{dup[k]}" for k in top) + ("…" if len(dup) > 3 else "") +
                     "); each row is analysed as its own feature")
    if blank:
        notes.append(f"{blank:,} row(s) have no ID; they are analysed, and show under their label or as blank")
    if m.values and m.samples:
        empty = [s for j, s in enumerate(m.samples) if not any(row[j] is not None for row in m.values)]
        if empty and len(empty) < len(m.samples):
            notes.append("sample(s) without a single value: " + ", ".join(empty[:8]) + ("…" if len(empty) > 8 else ""))
    return notes


def identical_samples(measured, samples: list[str], min_shared: int = 3) -> list[list[str]]:
    """Groups of samples whose measured values are exactly equal wherever both have one."""
    ns = len(samples)
    cols = [[row[j] for row in measured] for j in range(ns)]
    group_of: dict[int, int] = {}
    groups: list[list[int]] = []
    for a in range(ns):
        if a in group_of:
            continue
        for b in range(a + 1, ns):
            if b in group_of:
                continue
            shared = 0
            same = True
            for x, y in zip(cols[a], cols[b], strict=True):
                if (x is None) != (y is None):
                    same = False
                    break
                if x is not None:
                    if x != y:
                        same = False
                        break
                    shared += 1
            if same and shared >= min_shared:
                if a not in group_of:
                    group_of[a] = len(groups)
                    groups.append([a])
                group_of[b] = group_of[a]
                groups[group_of[a]].append(b)
    return [[samples[j] for j in g] for g in groups]


def statistics(p, diffs: list, settings, model=None) -> list[dict]:
    """Findings for the comparisons of a processed matrix: [{"code", ...}] (see the module docstring).
    p: fpa.Processed; diffs: analysis.DiffResult after to_diff(); model: analysis.Model."""
    from ionomos.downstream import fpa

    out: list[dict] = []
    m = p.m
    if not m.features or not m.samples:
        return out
    same = identical_samples(p.measured, m.samples)
    if same:
        out.append({"code": "IDENTICAL_SAMPLES", "groups": same})
    if m.kind == "ratio":
        groups_of = {d.name: [[j for j, x in enumerate(m.samples) if m.condition[x] == d.treatment]] for d in diffs}
    else:
        conds = [[j for j, x in enumerate(m.samples) if m.condition[x] == c] for c in m.conditions]
        groups_of = {d.name: conds for d in diffs}
    fits: dict[int, tuple] = {}
    designed = model is not None and getattr(model, "design", None) is not None
    for d in diffs:
        if d.confidence == "none" or d.test_used in ("as given", "protein-corrected"):
            continue  # a protein-corrected site comparison (D70) rests on the site comparison checked here already
        groups = groups_of[d.name]
        if id(groups) not in fits:
            _ns, _means, s2, df = fpa._group_fit(m.values, groups)
            fits[id(groups)] = (s2, df)
        s2, df = fits[id(groups)]
        tested = [r for r in d.rows if r["pvalue"] is not None]
        if not tested:
            continue
        hits = {r["index"] for r in tested if r["significant"]}
        zero_df = [r["index"] for r in tested if df[r["index"]] <= 0]
        zero_var = [r["index"] for r in tested if df[r["index"]] > 0 and s2[r["index"]] <= 1e-24]
        if zero_df and d.test_used == "limma" and not designed:
            out.append({"code": "NO_RESIDUAL_DF", "comparison": d.name, "features": len(zero_df),
                        "tested": len(tested), "hits": len(hits & set(zero_df))})
        if zero_var and (len(zero_var) >= max(3, ZERO_VAR_SHARE * len(tested)) or hits & set(zero_var)):
            out.append({"code": "ZERO_VARIANCE", "comparison": d.name, "features": len(zero_var),
                        "tested": len(tested), "hits": len(hits & set(zero_var)), "test": d.test_used,
                        "p_zero": sum(1 for r in tested if r["pvalue"] == 0)})
        if d.test_used == "limma":
            d0, s0 = d.prior if d.prior else (math.nan, math.nan)
            if d0 == 0 or (isinstance(s0, float) and math.isnan(s0)):
                out.append({"code": "VARIANCE_PRIOR", "comparison": d.name, "what": "none", "tested": len(tested)})
            elif d0 <= PRIOR_DF_FLOOR and len({df[r["index"]] for r in tested}) > 1:
                out.append({"code": "VARIANCE_PRIOR", "comparison": d.name, "what": "floor", "prior_df": d0,
                            "tested": len(tested)})
    return out


def pooled_prior(d0: float) -> bool:
    """The prior df is infinite, or the fit stopped at the top of its range: limma's "one variance for every
    feature". Not a fault (it is what homogeneous variances give); trust.py states it."""
    return d0 == d0 and d0 > PRIOR_DF_BOUND


def notes(p, diffs: list, settings) -> list[str]:
    """Things worth a line in the report's notes, not an issue."""
    out = []
    m = p.m
    if (m.kind == "intensity" and settings.normalize != "none" and 0 < len(m.features) < 3 and len(m.samples) > 1
            and any(d.tested == 0 and d.confidence != "none" for d in diffs)):
        out.append(f"only {len(m.features)} feature(s): median normalisation sets every sample to the same value, so "
                   "nothing is left to test. For a table this small use normalize: none (--normalize none)")
    return out


def issues(found: list[dict] | None) -> list:
    """Guard findings as doctor issues (warnings: shown in the report and the attention list, no pop-up)."""
    from ionomos.downstream.doctor import Issue

    out = []
    for g in found or []:
        code = g.get("code")
        name = g.get("comparison", "")
        if code == "IDENTICAL_SAMPLES":
            text = "; ".join(" = ".join(grp[:6]) + ("…" if len(grp) > 6 else "") for grp in g["groups"][:4])
            out.append(Issue("IDENTICAL_SAMPLES", "warning", "Some samples hold exactly the same values",
                             f"{text}. Identical samples are not replicates: they make the spread look smaller than "
                             "it is, so the p-values come out too small.",
                             ["One raw file or one column was loaded under two names",
                              "A column was copied in a spreadsheet, or the table was filled with one value"],
                             ["Leave the copy out (Analysis tab, or exclude_samples in experiment.yaml) and Run "
                              "analysis", "Check the table the values came from: each sample needs its own column"],
                             {"groups": g["groups"]}))
        elif code == "NO_RESIDUAL_DF":
            out.append(Issue("NO_RESIDUAL_DF", "warning",
                             f"{name}: {g['features']:,} features were tested without replicate spread of their own",
                             f"{g['features']:,} of {g['tested']:,} tested features ({g['hits']:,} of them hits) have "
                             "one value per group, so they have no residual degrees of freedom. limma still gives a "
                             "p-value, from the variance of the other features alone (the prior). It says how unusual "
                             "the fold change is for a typical feature, not for this one.",
                             ["A group has one sample, or missing values leave one value per group (imputation off)",
                              "Replicates were given different condition names"],
                             ["Treat these hits as leads; sort the table by n to see them",
                              "Add replicates, or check the conditions on the Analysis tab"],
                             {k: v for k, v in g.items() if k != "code"}))
        elif code == "ZERO_VARIANCE":
            how = ("limma replaces their variance by the prior, so their p-values reflect the other features"
                   if g.get("test") == "limma" else
                   f"a t-test divides by zero for them ({g.get('p_zero', 0):,} p-values of exactly 0)")
            out.append(Issue("ZERO_VARIANCE", "warning",
                             f"{name}: {g['features']:,} features have identical replicates",
                             f"{g['features']:,} of {g['tested']:,} tested features ({g['hits']:,} of them hits) have "
                             f"exactly the same value in every replicate of each group; {how}. Real measurements "
                             "always differ a little.",
                             ["The values were rounded, capped or filled with a constant before Ionomos read them",
                              "Missing values were imputed with one number (imputation min, zero or mindet)",
                              "The same column was used for several samples"],
                             ["Look at the values of one such feature (click it in the table)",
                              "Use the unrounded table, or imputation 'auto' / 'none' for this experiment"],
                             {k: v for k, v in g.items() if k != "code"}))
        elif code == "VARIANCE_PRIOR":
            if g.get("what") == "none":
                msg = ("limma estimates a variance prior from all features; here fewer than 3 features have "
                       "replicate spread, so no prior could be estimated and the p-values are ordinary t-tests. "
                       "With few replicates they have little power.")
            else:
                msg = (f"The estimate of the prior degrees of freedom did not converge: it stopped at the lower edge "
                       f"of its search range ({g.get('prior_df', 0):.2f}). The features' variances differ so much "
                       "that almost nothing is shared between them, so the p-values are close to ordinary t-tests "
                       "and have little power with few replicates.")
            out.append(Issue("VARIANCE_PRIOR", "warning", f"{name}: the variance prior could not be estimated as usual",
                             msg,
                             ["Very few features (a table of selected proteins)",
                              "A few features with extreme spread (outlier values, a mislabelled sample), or two "
                              "kinds of data in one table"],
                             ["Nothing to fix in the settings; read the p-values with this in mind",
                              "For a short list of proteins, a Welch test (analysis.test: welch) makes no assumption "
                              "across features"],
                             {k: v for k, v in g.items() if k != "code"}))
    return out
