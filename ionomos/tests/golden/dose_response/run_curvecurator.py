"""Run the real CurveCurator on the simulated titrations (make_dose_inputs.py) and keep its answers as the golden files.

Dev-only, never a dependency of Ionomos. CurveCurator 0.6.0 needs Python 3.11-3.13 (numpy, scipy, pandas,
statsmodels); for example:

    uv venv --python 3.12 /tmp/ccvenv && uv pip install --python /tmp/ccvenv/bin/python curve-curator==0.6.0
    /tmp/ccvenv/bin/python tests/golden/dose_response/run_curvecurator.py

It calls CurveCurator's own pipeline functions (quantification.run_pipeline, thresholding.
apply_significance_thresholds) with the settings its TOML parser fills in by default, plus alpha 0.05 and
fc_lim 0.45 (the paper's decryptM settings): OLS, "standard" speed, no imputation, no normalisation (the
simulated runs have equal loading), ratios to the mean of the DMSO runs. Output: <design>_curvecurator.tsv.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from curve_curator import __version__, quantification, thresholding

HERE = Path(__file__).resolve().parent
KEEP = {"pEC50": "pec50", "Curve Slope": "slope", "Curve Front": "front", "Curve Back": "back",
        "Curve Fold Change": "curve_fold_change", "pEC50 Error": "pec50_error", "Curve RMSE": "rmse",
        "Curve R2": "r2", "Null Model": "null_intercept", "Null RMSE": "null_rmse", "Curve F_Value": "f_value",
        "Curve P_Value": "pvalue", "Curve Relevance Score": "relevance", "Curve Regulation": "class"}


def config(experiments, doses) -> dict:
    n = len(experiments)
    return {
        "Meta": {}, "Paths": {},
        "Experiment": {"experiments": np.array(experiments), "doses": np.array(doses, dtype=float),
                       "control_experiment": np.array([e for e, d in zip(experiments, doses, strict=True) if d == 0]),
                       "dose_scale": 1e-9, "dose_unit": "M"},
        "Processing": {"available_cores": 1, "imputation": False, "imputation_pct": 0.005, "normalization": False,
                       "max_missing": n, "max_imputation": n, "ratio_range": None},
        "Curve Fit": {"weights": None, "interpolation": False, "type": "OLS", "speed": "standard",
                      "max_iterations": 100 * n, "control_fold_change": False},
        "F Statistic": {"alpha": 0.05, "fc_lim": 0.45, "decoy_ratio": 1.0, "optimized_dofs": True, "loc": 0.12,
                        "scale": 1.0, "two_sided": False, "quality_min": -np.inf, "mtc_method": "sam",
                        "not_rmse_limit": 0.1, "not_p_limit": np.inf, "pEC50_filter": [-np.inf, np.inf]},
    }


def run(design: str) -> None:
    des = pd.read_csv(HERE / f"{design}_design.tsv", sep="\t")
    mx = pd.read_csv(HERE / f"{design}_matrix.tsv", sep="\t", keep_default_na=False, na_values=[""])
    samples = list(des["sample"])
    df = pd.DataFrame({"Name": mx["id"]})
    for s in samples:
        df[f"Raw {s}"] = 2.0 ** mx[s].astype(float)
    cfg = config(samples, list(des["dose_nM"]))
    out = quantification.run_pipeline(df, config=cfg)
    out = thresholding.apply_significance_thresholds(out, config=cfg)
    res = out[["Name", *KEEP]].rename(columns={"Name": "id", **KEEP})
    res["class"] = res["class"].fillna("unclear")
    res.to_csv(HERE / f"{design}_curvecurator.tsv", sep="\t", index=False, float_format="%.10g", na_rep="NA")
    print(f"{design}: CurveCurator {__version__}, {len(res)} curves, " + ", ".join(
        f"{k} {v}" for k, v in res["class"].value_counts().items()))


if __name__ == "__main__":
    for d in ("A", "B"):
        run(d)
