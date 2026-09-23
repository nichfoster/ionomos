"""Reference p-values from scipy for tests/test_downstream.py. Run once in an env with scipy; output committed."""
import json
import random

import scipy
from scipy import stats
from scipy.stats import false_discovery_control

rng = random.Random(3)
cases = []
for _i in range(60):
    na, nb = rng.randint(2, 6), rng.randint(2, 6)
    shift = rng.choice([0, 0.3, 1, 2.5, 6])
    a = [round(rng.gauss(20 + shift, rng.uniform(0.05, 1.5)), 4) for _ in range(na)]
    b = [round(rng.gauss(20, rng.uniform(0.05, 1.5)), 4) for _ in range(nb)]
    w = stats.ttest_ind(a, b, equal_var=False)
    o = stats.ttest_1samp(a, 20.0)
    cases.append({"a": a, "b": b, "welch_t": float(w.statistic), "welch_p": float(w.pvalue),
                  "one_t": float(o.statistic), "one_p": float(o.pvalue)})
ps = [c["welch_p"] for c in cases]
out = {"scipy": scipy.__version__, "cases": cases, "bh_input": ps,
       "bh": [float(x) for x in false_discovery_control(ps, method="bh")]}
json.dump(out, open("stats_reference.json", "w"), indent=1)
print("ok", len(cases), "cases")
