"""
TMT post-processing: corrected experimental_annotation.tsv for FragPipe Analyst.

Port of reference/lab-scripts/correct_experimental_annotation_for_Fragpipe-TMT.R

Contract (Phase 3):
    write_experimental_annotation(abundance_tsv, output_tsv, channel_map=None) -> pd.DataFrame

    abundance_tsv = <workdir>/tmt-report/abundance_gene_MD.tsv (header only is read)
    The R keeps columns matching ^[A-Za-z0-9]+_1_\\d{3}[A-Z]?$ and derives
      plex        = the middle number
      channel     = trailing 126 / 127N / ...
      sample      = header as-is
      sample_name = header with "_1_" -> "_"
      condition   = prefix before first "_"
      replicate   = row number within condition
    Prefer deriving condition/replicate from the experiment.yaml channel map
    when present; fall back to the regex. Note the regex hard-codes plex "1".
"""
from __future__ import annotations


def write_experimental_annotation(abundance_tsv, output_tsv, channel_map=None):
    raise NotImplementedError("Phase 3")
