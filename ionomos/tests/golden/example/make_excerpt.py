"""Builds the excerpt of `ionomos example`'s data that tests/test_example.py analyses offline. Run once; the output
is committed (README.txt says what it is and its licence).

    python -I make_excerpt.py <FragPipe_Analyst_Manuscript_Datasets.zip from Zenodo record 13147961>

Each table keeps its header and its rows unchanged, byte for byte: the rows of a few named proteins (the hits a
test checks) and every k-th other row, ROWS in all. The annotation files are copied whole. Nothing is invented.
"""
import sys
import zipfile
from pathlib import Path

PREFIX = "FragPipe_Analyst_Manuscript_Datasets/"
ROWS = 240
# member -> (gene column, genes kept on purpose)
TABLES = {
    "AP-MS/combined_protein.tsv": ("Gene", {"CCND1", "CDK4", "CDK6", "CDK2", "CDK5", "CDKN1A", "CDKN1B", "CDKN1C",
                                            "PCNA", "PPIF"}),
    "DIA_4plex/diann-output.pg_matrix.tsv": ("Genes", {"CA9", "AHNAK2", "NDUFV2", "PIGR", "SFRP1"}),
    "TMT_4plex/ratio_protein_MD.tsv": ("Gene", {"CA9", "AHNAK2", "NDUFV2", "PIGR", "SFRP1"}),
}
WHOLE = ("AP-MS/experiment_annotation.tsv", "DIA_4plex/experiment_annotation_clean.tsv",
         "TMT_4plex/experiment_annotation_clean.tsv")


def excerpt(data: bytes, gene_col: str, keep: set[str]) -> bytes:
    lines = data.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    head, rows = lines[0], lines[1:]
    col = head.decode().rstrip("\r").split("\t").index(gene_col)
    named = [i for i, r in enumerate(rows) if r.decode().split("\t")[col] in keep]
    step = max(1, len(rows) // (ROWS - len(named)))
    picked = sorted(set(named) | set(range(0, len(rows), step)))
    while len(picked) > ROWS:  # drop from the end of the regular sample, never a named row
        drop = next(i for i in reversed(picked) if i not in named)
        picked.remove(drop)
    return b"\n".join([head, *(rows[i] for i in picked)]) + b"\n"


def main(zip_path: str) -> None:
    here = Path(__file__).resolve().parent
    with zipfile.ZipFile(zip_path) as zf:
        for member, (gene_col, keep) in TABLES.items():
            out = here / member
            out.parent.mkdir(exist_ok=True)
            out.write_bytes(excerpt(zf.read(PREFIX + member), gene_col, keep))
        for member in WHOLE:
            (here / member).write_bytes(zf.read(PREFIX + member))


if __name__ == "__main__":
    main(sys.argv[1])
