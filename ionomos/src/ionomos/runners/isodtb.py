"""
isoDTB runner entry point: merge modified peptides to labelled sites.

Thin adapter over ionomos/downstream/isodtb.py — the faithful port of
reference/lab-scripts/isoDTB_Fragpipe_merge-individual-peptides-to-Site.R
(checked byte-identical to the R output in tests/test_downstream.py).

This entry keeps the R script's user-facing shape: one input table, one
sample prefix, one output file.

Contract (Phase 2):
    merge_to_sites(input_tsv, output_tsv, sample_prefix, mod_mass="561.3387") -> Path

    input_tsv     = <workdir>/combined_modified_peptide_label_quant.tsv
    output_tsv    = written exactly as the caller names it
    sample_prefix = FragPipe experiment name (RawName.sample)
    Errors: SiteError with the R-parity messages — no labelled sites found;
    no ratio columns match the prefix.

The merge algorithm lives only in downstream/isodtb.py; the runner adds
nothing. The downstream differences from the R script (prefixes auto-detected
in the pipeline driver, the N-terminal letter-counting quirk kept for
identical output) apply here too and are documented there.
"""
from __future__ import annotations

from pathlib import Path

from ionomos.downstream.isodtb import SiteError, site_table
from ionomos.downstream.tables import read_tsv, write_tsv

__all__ = ["SiteError", "merge_to_sites"]


def merge_to_sites(input_tsv: Path, output_tsv: Path, sample_prefix: str, mod_mass: str = "561.3387") -> Path:
    """Write the R script's summary_df for one prefix; return the written file."""
    header, rows = read_tsv(Path(input_tsv))
    out_header, out_rows = site_table(header, rows, sample_prefix, mod_mass)
    return write_tsv(Path(output_tsv), out_header, out_rows)
