"""
TMT: experimental annotation from TMT-Integrator's abundance table header.

Faithful port of reference/lab-scripts/correct_experimental_annotation_for_Fragpipe-TMT.R
(checked against the R script in tests): keep header columns matching
^[A-Za-z0-9]+_1_\\d{3}[A-Z]?$ (condition_plex_channel), and write
plex, channel, sample, sample_name (first "_1_" -> "_"), condition (text
before the first "_"), replicate (running number within condition).
"""
from __future__ import annotations

import re
from pathlib import Path

from ionomos.downstream.tables import read_header, write_tsv

VALID = re.compile(r"^[A-Za-z0-9]+_1_\d{3}[A-Z]?$")
HEADER = ["plex", "channel", "sample", "sample_name", "condition", "replicate"]


def annotation_rows(header: list[str]) -> list[dict]:
    rows, counts = [], {}
    for h in header:
        if not VALID.match(h):
            continue
        cond = re.match(r"^[^_]+", h).group(0)
        counts[cond] = counts.get(cond, 0) + 1
        rows.append({
            "plex": re.search(r"(?<=_)(\d+)(?=_)", h).group(1),
            "channel": re.search(r"(\d{3}[A-Z]?)$", h).group(1),
            "sample": h,
            "sample_name": h.replace("_1_", "_", 1),
            "condition": cond,
            "replicate": counts[cond],
        })
    return rows


def write_annotation(abundance_tsv: Path, out: Path) -> Path:
    return write_tsv(out, HEADER, annotation_rows(read_header(abundance_tsv)))
