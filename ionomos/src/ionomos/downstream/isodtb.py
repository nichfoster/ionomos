"""
isoDTB: merge FragPipe's labelled peptides into labelled sites.

Faithful port of reference/lab-scripts/isoDTB_Fragpipe_merge-individual-peptides-to-Site.R
(checked against the R script on the same input in tests/test_downstream.py):

  for every "[561.3387]" in `Light Modified Peptide`:
      position in peptide = number of letters before the bracket
      residue             = the character just before the bracket
      position in protein = Start + position in peptide - 1
  group by (Protein, Protein ID, Entry Name, Gene, Protein Description, residue, position):
      PeptideCount     = distinct `Peptide Sequence`
      ExamplePeptides  = first 3 distinct peptides, "; "-joined
      Mean_<col>       = mean of each "<prefix>_<n> Log2 Ratio HL" column (NA ignored)
      Mean_Log2_Ratio_HL = mean over all those columns and rows (NA ignored)
  sorted by Protein, position

Differences from the R script, all additive:
  * the sample prefix is found from the column names (every prefix gets its
    own table) instead of being typed into the script;
  * quirk kept on purpose, for identical output: letters in N-terminal mod
    tags (e.g. "n[42.0106]") are counted like the R script counts them.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

from ionomos.downstream.tables import num, read_tsv, write_tsv

LABEL_FILE = "combined_modified_peptide_label_quant.tsv"
RATIO_SUFFIX = " Log2 Ratio HL"
META = ("Protein", "Protein ID", "Entry Name", "Gene", "Protein Description")
_RATIO = re.compile(r"^(.+)_(\d+) Log2 Ratio HL$")


class SiteError(ValueError):
    pass


def ratio_prefixes(header: list[str]) -> dict[str, list[str]]:
    """{sample prefix: [its '<prefix>_<n> Log2 Ratio HL' columns, in header order]}."""
    out: dict[str, list[str]] = {}
    for h in header:
        m = _RATIO.match(h)
        if m:
            out.setdefault(m.group(1), []).append(h)
    return out


def _mean(vals: list[float]) -> float:
    """R's mean.default algorithm (summary.c): sum, divide, then add the mean residual.

    R accumulates in `long double`: 64-bit on Apple Silicon (this code matches it
    bit for bit), 80-bit on x86 Windows (agreement to ~1e-15 relative).
    """
    if not vals:
        return float("nan")
    n = len(vals)
    s = 0.0
    for v in vals:
        s += v
    s /= n
    if math.isfinite(s):
        t = 0.0
        for v in vals:
            t += v - s
        s += t / n
    return s


def site_table(header: list[str], rows: list[dict], prefix: str, mod_mass: str = "561.3387"):
    """(output header, output rows) for one sample prefix — the R script's summary_df."""
    for col in ("Light Modified Peptide", "Start", "Protein", "Peptide Sequence"):
        if col not in header:
            raise SiteError(f"{LABEL_FILE} has no '{col}' column")
    ratio_cols = [h for h in header if re.fullmatch(re.escape(prefix) + r"_[0-9]+ Log2 Ratio HL", h)]
    if not ratio_cols:
        raise SiteError(f"no '{prefix}_<n>{RATIO_SUFFIX}' columns")
    meta = [c for c in META if c in header]
    tag = "[" + mod_mass + "]"
    groups: dict[tuple, dict] = {}
    for r in rows:
        mod = r.get("Light Modified Peptide") or ""
        start = num(r.get("Start"))
        pos = mod.find(tag)
        while pos != -1:
            prefix_str = mod[:pos]
            in_pep = sum(ch.isascii() and ch.isalpha() for ch in prefix_str)
            residue = prefix_str[-1:] if prefix_str else ""
            in_prot = None if start is None else start + in_pep - 1
            key = tuple(r.get(c) or None for c in meta) + (residue, in_prot)  # readr: empty cell -> NA
            g = groups.setdefault(key, {"peptides": [], "vals": {c: [] for c in ratio_cols}})
            pep = r.get("Peptide Sequence", "")
            if pep not in g["peptides"]:
                g["peptides"].append(pep)
            for c in ratio_cols:
                v = num(r.get(c))
                if v is not None:
                    g["vals"][c].append(v)
            pos = mod.find(tag, pos + len(tag))
    if not groups:
        raise SiteError(f"no labelled sites: no '{tag}' in the Light Modified Peptide column")

    out_header = [*meta, "ModifiedResidue", "ResiduePositionInProtein", "PeptideCount", "ExamplePeptides",
                  *[f"Mean_{c}" for c in ratio_cols], "Mean_Log2_Ratio_HL"]
    out = []
    for key, g in groups.items():
        row = dict(zip([*meta, "ModifiedResidue", "ResiduePositionInProtein"], key, strict=True))
        row["PeptideCount"] = len(g["peptides"])
        row["ExamplePeptides"] = "; ".join(g["peptides"][:3])
        allv = []
        for c in ratio_cols:
            row[f"Mean_{c}"] = _mean(g["vals"][c])
            allv += g["vals"][c]
        row["Mean_Log2_Ratio_HL"] = _mean(allv)
        out.append(row)

    def sort_key(row):
        p = row["ResiduePositionInProtein"]
        return (row["Protein"] or "", math.inf if p is None else p)

    out.sort(key=sort_key)
    return out_header, out


def write_site_tables(label_quant: Path, results_dir: Path, mod_mass: str = "561.3387") -> list[Path]:
    """One <prefix>_sites.tsv per sample prefix found in the label-quant table."""
    header, rows = read_tsv(label_quant)
    prefixes = ratio_prefixes(header)
    if not prefixes:
        raise SiteError(f"{label_quant.name} has no '<sample>_<n>{RATIO_SUFFIX}' columns")
    written = []
    for prefix in prefixes:
        h, out = site_table(header, rows, prefix, mod_mass)
        written.append(write_tsv(Path(results_dir) / f"{prefix}_sites.tsv", h, out))
    return written
