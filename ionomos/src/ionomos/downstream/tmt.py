"""
TMT: experimental annotation from TMT-Integrator's abundance table header, and each sample's plex and channel.

Faithful port of reference/lab-scripts/correct_experimental_annotation_for_Fragpipe-TMT.R
(checked against the R script in tests): keep header columns matching
^[A-Za-z0-9]+_1_\\d{3}[A-Z]?$ (condition_plex_channel), and write
plex, channel, sample, sample_name (first "_1_" -> "_"), condition (text
before the first "_"), replicate (running number within condition).

The R script knows one plex only (the "_1_" in its pattern). Ionomos' multi-plex extension (#155), used when the
search had several plexes (multiplex=True): names condition_<k>_channel are kept for any plex number k, the plex
column is k, sample_name is the whole name (dropping the plex number would make DMSO_1_127N and DMSO_2_127N the
same name), and the replicate runs on within a condition across the plexes, so no replicate number repeats. With
one plex the file is the R script's, byte for byte (tests/golden/R_experimental_annotation.tsv).

    annotation_rows(header, multiplex=False) -> [{plex, channel, sample, sample_name, condition, replicate}]
    write_annotation(abundance_tsv, out, multiplex=False) -> out
    experiments(workdir, record) -> [plex]           the FragPipe experiments (plexes) of a search
    plexes_and_channels(samples, maps) -> ({sample: plex}, {sample: channel})   from the channel maps, else the names
"""
from __future__ import annotations

import re
from pathlib import Path

from ionomos.downstream.tables import read_header, write_tsv

VALID = re.compile(r"^[A-Za-z0-9]+_1_\d{3}[A-Z]?$")             # the R script's
VALID_PLEXES = re.compile(r"^[A-Za-z0-9]+_\d+_\d{3}[A-Z]?$")    # Ionomos' multi-plex extension
HEADER = ["plex", "channel", "sample", "sample_name", "condition", "replicate"]
NAME_NOTE = ("TMT sample names don't follow condition_1_channel (e.g. DMSO_1_126), so the lab's annotation file is "
             "empty; conditions were taken from the text before the first '_'")


def annotation_rows(header: list[str], multiplex: bool = False) -> list[dict]:
    rows, counts = [], {}
    for h in header:
        if not (VALID_PLEXES if multiplex else VALID).match(h):
            continue
        cond = re.match(r"^[^_]+", h).group(0)
        counts[cond] = counts.get(cond, 0) + 1
        rows.append({
            "plex": re.search(r"(?<=_)(\d+)(?=_)", h).group(1),
            "channel": re.search(r"(\d{3}[A-Z]?)$", h).group(1),
            "sample": h,
            "sample_name": h if multiplex else h.replace("_1_", "_", 1),
            "condition": cond,
            "replicate": counts[cond],
        })
    return rows


def write_annotation(abundance_tsv: Path, out: Path, multiplex: bool = False) -> Path:
    return write_tsv(out, HEADER, annotation_rows(read_header(abundance_tsv), multiplex))


def experiments(workdir: Path, record: dict | None) -> list[str]:
    """The search's FragPipe experiments (TMT plexes): ionomos.json's manifest, else the psm.tsv folders."""
    from ionomos.downstream import tmtqc

    got = {str(m.get("experiment")) for m in ((record or {}).get("plan") or {}).get("manifest") or []
           if m.get("experiment") not in (None, "")}
    if not got:
        got = {tmtqc._plex_name(p.parent.name, set()) for p in tmtqc.psm_files(Path(workdir))
               if p.parent != Path(workdir)}
    return sorted(got)


def plexes_and_channels(samples, maps: dict[str, dict[str, str]]) -> tuple[dict[str, str], dict[str, str]]:
    """({sample: plex}, {sample: channel}) for TMT-Integrator's sample columns: the plex whose channel map names the
    sample (FragPipe's annotation files / experiment.yaml, tmtqc.maps_for), else the plex number and channel in a
    condition_plex_channel name; a sample found in neither is left out."""
    from ionomos import tmtkit

    by_name = {str(n): (str(p), tmtkit.channel_key(c) or str(c)) for p, m in (maps or {}).items()
               for c, n in m.items() if not tmtkit.is_na(n) and str(n).strip()}
    plex, channel = {}, {}
    for s in samples:
        if s in by_name:
            plex[s], channel[s] = by_name[s]
            continue
        p = tmtkit.parse_sample_name(s)
        if p and p.plex is not None and p.channel:
            plex[s], channel[s] = str(p.plex), p.channel
    return plex, channel
