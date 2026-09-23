"""
Comparisons and differential statistics on a QuantMatrix.

Settings come from config.yaml `analysis:` (lab defaults) overridden by the
experiment's experiment.yaml `analysis:` block:

    analysis:
      comparisons: ["Drug vs DMSO", "Drug2 vs DMSO"]   # treatment vs control; default: every condition vs the control
      control: DMSO                                     # default: first condition matching control_keywords
      log2fc: 1.0          # |log2 fold change| threshold
      alpha: 0.05          # significance threshold
      use_adjusted: true   # alpha applies to BH q-values (false: raw p)
      min_valid: 2         # values needed per group to test a feature
      normalize: median    # median | none (ratio data is never normalised)
      top_labels: 15       # names drawn on each volcano
      test: moderated      # moderated (limma-style empirical Bayes, default) | welch | student
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, fields

from ionomos.downstream import stats
from ionomos.downstream.quant import QuantMatrix

DEFAULT_CONTROL_KEYWORDS = ("DMSO", "vehicle", "veh", "ctrl", "control", "mock", "untreated", "NT", "WT", "EV",
                            "scr", "scramble", "siNT", "PBS")


@dataclass
class Settings:
    log2fc: float = 1.0
    alpha: float = 0.05
    use_adjusted: bool = True
    min_valid: int = 2
    normalize: str = "median"
    top_labels: int = 15
    test: str = "moderated"
    control: str | None = None
    comparisons: list[tuple[str, str]] = field(default_factory=list)
    control_keywords: tuple[str, ...] = DEFAULT_CONTROL_KEYWORDS

    def describe(self) -> str:
        which = "BH-adjusted q" if self.use_adjusted else "p"
        test = {"moderated": "moderated t-test", "welch": "Welch t-test", "student": "t-test"}[self.test]
        return f"|log2FC| ≥ {self.log2fc:g} and {which} < {self.alpha:g} ({test}; ≥{self.min_valid} values per group)"


class AnalysisError(ValueError):
    pass


def parse_comparison(c) -> tuple[str, str]:
    if isinstance(c, (list, tuple)) and len(c) == 2:
        return str(c[0]).strip(), str(c[1]).strip()
    m = re.match(r"^\s*(.+?)\s+(?:vs\.?|versus|/|-vs-)\s+(.+?)\s*$", str(c), re.IGNORECASE)
    if not m:
        raise AnalysisError(f"comparison {c!r} must look like 'Drug vs DMSO' or [Drug, DMSO]")
    return m.group(1), m.group(2)


def settings_from(*layers: dict | None) -> Settings:
    """Later layers win. Unknown keys are an error (typos shouldn't silently do nothing)."""
    s = Settings()
    names = {f.name for f in fields(Settings)}
    for layer in layers:
        for k, v in (layer or {}).items():
            if k == "enabled":
                continue
            if k not in names:
                raise AnalysisError(f"unknown analysis setting {k!r} (known: {', '.join(sorted(names))})")
            if v is None or v == "":
                continue
            try:
                if k in ("log2fc", "alpha"):
                    v = float(v)
                elif k in ("min_valid", "top_labels"):
                    v = int(v)
                elif k == "use_adjusted":
                    v = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")
                elif k == "test":
                    v = str(v).lower()
                    if v not in ("moderated", "welch", "student"):
                        raise AnalysisError("test must be moderated, welch or student")
                elif k == "normalize":
                    v = str(v).lower()
                    if v not in ("median", "none"):
                        raise AnalysisError("normalize must be 'median' or 'none'")
                elif k == "comparisons":
                    v = [parse_comparison(c) for c in (v if isinstance(v, list) else [v])]
                elif k == "control_keywords":
                    v = tuple(str(x) for x in (v if isinstance(v, (list, tuple)) else str(v).split(",")) if str(x).strip())
                elif k == "control":
                    v = str(v)
            except (TypeError, ValueError) as exc:
                raise AnalysisError(f"analysis.{k}: {exc}") from exc
            setattr(s, k, v)
    if not 0 < s.alpha < 1:
        raise AnalysisError("analysis.alpha must be between 0 and 1")
    if s.log2fc < 0 or s.min_valid < 2:
        raise AnalysisError("analysis.log2fc must be >= 0 and min_valid >= 2")
    return s


def find_control(conditions: list[str], s: Settings) -> str | None:
    if s.control:
        for c in conditions:
            if c.lower() == s.control.lower():
                return c
        raise AnalysisError(f"control {s.control!r} is not one of the conditions: {', '.join(conditions)}")
    for kw in s.control_keywords:
        for c in conditions:
            tokens = re.split(r"[_\-\s.]+", c.lower())
            if kw.lower() in tokens or c.lower() == kw.lower():
                return c
    return None


def choose_comparisons(m: QuantMatrix, s: Settings) -> tuple[list[tuple[str, str | None]], list[str]]:
    """[(treatment, control or None for ratio data)], notes."""
    notes: list[str] = []
    conds = m.conditions
    if m.kind == "ratio":
        return [(c, None) for c in conds], notes
    if s.comparisons:
        for t, c in s.comparisons:
            for x in (t, c):
                if x not in conds:
                    raise AnalysisError(f"comparison uses {x!r}, which is not a condition here "
                                        f"(conditions: {', '.join(conds)})")
        return list(s.comparisons), notes
    if len(conds) < 2:
        notes.append(f"only one condition ({conds[0] if conds else '-'}): quality control only, no comparison")
        return [], notes
    ctrl = find_control(conds, s)
    if ctrl is None:
        ctrl = sorted(conds)[0]
        notes.append(f"no control condition recognised; using {ctrl!r} as control (alphabetically first). "
                     f"Set analysis.control in experiment.yaml to choose.")
    return [(c, ctrl) for c in conds if c != ctrl], notes


@dataclass
class DiffResult:
    name: str
    treatment: str
    control: str | None
    kind: str
    rows: list[dict]
    settings: Settings
    y_threshold_p: float | None  # the p-value where significance starts (for the volcano's horizontal line)
    test_used: str = ""
    prior: tuple[float, float] = (math.nan, math.nan)  # moderated t: (prior df, prior variance)

    @property
    def up(self) -> int:
        return sum(1 for r in self.rows if r["significant"] == "up")

    @property
    def down(self) -> int:
        return sum(1 for r in self.rows if r["significant"] == "down")

    @property
    def tested(self) -> int:
        return sum(1 for r in self.rows if r["pvalue"] is not None and not math.isnan(r["pvalue"]))

    def slug(self) -> str:
        return re.sub(r"[^A-Za-z0-9._-]+", "_", self.name).strip("_") or "comparison"


def _prepared(m: QuantMatrix, s: Settings) -> list[list[float | None]]:
    """Values after optional median normalisation (intensities only)."""
    if m.kind != "intensity" or s.normalize == "none":
        return m.values
    cols = [m.column(x) for x in m.samples]
    cols = stats.median_center(cols)
    return [[cols[j][i] for j in range(len(m.samples))] for i in range(len(m.features))]


MIN_FOR_MODERATION = 10  # fewer testable features than this: the variance prior can't be estimated


def differential(m: QuantMatrix, treatment: str, control: str | None, s: Settings) -> DiffResult:
    """One comparison. control None = ratio data, each feature tested against 0."""
    values = _prepared(m, s)
    ti = [j for j, x in enumerate(m.samples) if m.condition[x] == treatment]
    ci = [j for j, x in enumerate(m.samples) if control is not None and m.condition[x] == control]
    rows, coef, s2, dfs, su, ok = [], [], [], [], [], []
    for f, vals in zip(m.features, values, strict=True):
        a = [vals[j] for j in ti if vals[j] is not None]
        b = [vals[j] for j in ci if vals[j] is not None]
        if control is None:
            good = len(a) >= s.min_valid
            fc = stats.mean(a) if a else math.nan
            c_, v_, d_, u_ = (fc, stats.var(a), len(a) - 1, 1 / math.sqrt(len(a))) if good else (math.nan,) * 4
        else:
            good = len(a) >= s.min_valid and len(b) >= s.min_valid
            fc = stats.mean(a) - stats.mean(b) if (a and b) else math.nan
            if good:  # pooled variance, as limma's linear model uses
                d_ = len(a) + len(b) - 2
                v_ = ((len(a) - 1) * stats.var(a) + (len(b) - 1) * stats.var(b)) / d_
                c_, u_ = fc, math.sqrt(1 / len(a) + 1 / len(b))
            else:
                c_, v_, d_, u_ = (math.nan,) * 4
        coef.append(c_)
        s2.append(v_)
        dfs.append(d_)
        su.append(u_)
        ok.append(good)
        rows.append({"id": f.id, "label": f.label, "description": f.description, "log2fc": fc,
                     "n_treatment": len(a), "n_control": len(b) if control is not None else None,
                     "mean_treatment": stats.mean(a) if a else None,
                     "mean_control": (stats.mean(b) if b else None) if control is not None else None,
                     "_a": a, "_b": b})
    test = s.test
    if test == "moderated" and sum(ok) < MIN_FOR_MODERATION:
        test = "welch" if control is not None else "student"
    if test == "moderated":
        ts, _dft, ps, d0, s0 = stats.moderated_t(coef, s2, dfs, su)
    else:
        ts, ps = [], []
        for r, good in zip(rows, ok, strict=True):
            if not good:
                t, p = math.nan, math.nan
            elif control is None:
                t, _, p = stats.one_sample_t(r["_a"], 0.0)
            elif test == "welch":
                t, _, p = stats.welch_t(r["_a"], r["_b"])
            else:
                t, _, p = stats.student_t(r["_a"], r["_b"])
            ts.append(t)
            ps.append(p)
        d0 = s0 = math.nan
    qs = stats.bh_adjust(ps)
    thr_p = None
    for r, t, p, q in zip(rows, ts, ps, qs, strict=True):
        del r["_a"], r["_b"]
        r["t"] = None if math.isnan(t) else t
        r["pvalue"] = None if math.isnan(p) else p
        r["qvalue"] = None if math.isnan(q) else q
        score = r["qvalue"] if s.use_adjusted else r["pvalue"]
        fc = r["log2fc"]
        sig = score is not None and score < s.alpha and not math.isnan(fc) and abs(fc) >= s.log2fc
        r["significant"] = ("up" if fc > 0 else "down") if sig else ""
        if score is not None and score < s.alpha and r["pvalue"] is not None:
            thr_p = r["pvalue"] if thr_p is None else max(thr_p, r["pvalue"])
    if not s.use_adjusted:
        thr_p = s.alpha
    name = f"{treatment} vs {control}" if control is not None else f"{treatment} (log2 H/L vs 0)"
    rows.sort(key=lambda r: (r["pvalue"] is None, r["pvalue"] if r["pvalue"] is not None else 1.0))
    res = DiffResult(name, treatment, control, m.kind, rows, s, thr_p)
    res.test_used = test
    res.prior = (d0, s0)
    return res


DIFF_COLUMNS = ["id", "label", "description", "log2fc", "pvalue", "qvalue", "significant", "t", "n_treatment",
                "n_control", "mean_treatment", "mean_control"]
