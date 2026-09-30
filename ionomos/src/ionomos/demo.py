"""
`ionomos demo [FOLDER]`: a small simulated experiment and its report, to try Ionomos in minutes.

    folder = demo.write_demo(demo.pick_folder(None))   # ./ionomos_demo (or ionomos_demo_2, ...)
    out = demo.analyze(folder)                         # downstream.Outcome; out.report is the page

What it writes (every number is simulated by downstream/simulate.py, with planted truth):

    ionomos_demo/
      README.txt                                    what this is and what to look for
      experiment.yaml                               analysis settings: control DMSO, demo gene sets only
      demo_gene_sets.gmt                            12 simplified gene sets (bundled with the package)
      fragpipe/diann-output/report.pg_matrix.tsv    DIA-NN protein matrix: DMSO, DrugA, DrugB x 4 replicates
      results/report.html                           written by the analysis

It never touches an existing folder's contents: the default name moves on to ionomos_demo_2, _3, ...
when taken, and a folder given by the user must be new or empty. It needs no network (no Enrichr
download: the gene sets are the bundled .gmt), no lab config and no Tk.
"""
from __future__ import annotations

import random
from importlib import resources
from pathlib import Path

DEFAULT_NAME = "ionomos_demo"
GMT_NAME = "demo_gene_sets.gmt"
CONDITIONS = ("DMSO", "DrugA", "DrugB")
REPLICATES = 4
N_PROTEINS = 600
SEED = 2026

# (condition, gene set, fraction of its members moved, log2 effect): strong effects on part of a set show up
# as hits and in the over-representation test; small effects on a whole set only in the rank-based test.
PLANTED = (
    ("DrugA", "DEMO_HEAT_SHOCK_RESPONSE", 0.7, 2.0),
    ("DrugA", "DEMO_PROTEASOME", 1.0, 0.6),
    ("DrugA", "DEMO_DNA_REPLICATION", 0.6, -1.6),
    ("DrugB", "DEMO_CHOLESTEROL_BIOSYNTHESIS", 0.7, 1.8),
    ("DrugB", "DEMO_NRF2_TARGETS", 1.0, 0.6),
    ("DrugB", "DEMO_RIBOSOME", 1.0, -0.5),
)
# on/off proteins: never measured in these conditions
ABSENT = {"DMSO": ["HSPA1A", "HSPA1B", "INSIG1"], "DrugA": ["INSIG1"], "DrugB": ["HSPA1A", "HSPA1B",
                                                                               "GENE250", "GENE251"]}

README = """\
Ionomos demo experiment (simulated)
===================================

Every number in this folder was invented by `ionomos demo`, so you can see what
an Ionomos report looks like before pointing it at your own data.

The experiment: a DIA-NN protein matrix (fragpipe/diann-output/report.pg_matrix.tsv)
with {n} proteins, conditions DMSO (control), DrugA and DrugB, 4 replicates each.
Planted on purpose:
  - DrugA: heat-shock proteins strongly up, the whole proteasome slightly up,
    DNA-replication proteins down; HSPA1A and HSPA1B are only seen in DrugA.
  - DrugB: cholesterol-biosynthesis enzymes up, NRF2 targets slightly up,
    ribosomal proteins slightly down; INSIG1 is only seen in DrugB, GENE250 and
    GENE251 only in DMSO.
  - About 2% of the other proteins change at random.
The gene sets (demo_gene_sets.gmt) are simplified for this demo, not curated
pathways. experiment.yaml tells the analysis to use them instead of downloading
the Enrichr libraries.

Open results/report.html in a browser. Things to look at:
  - Key findings at the top, then the volcano plot per comparison.
  - "Only in one condition": the on/off proteins above.
  - Gene sets: heat shock / cholesterol found by the hit lists; proteasome and
    NRF2 only by the rank-based test (every member moves a little).
  - Compare comparisons: DrugA against DrugB.
  - Sample QC: the scorecard, PCA and the p-value histograms.

Re-run with other settings, e.g.:
  ionomos analyze {folder} --de-type all
  ionomos analyze {folder} --log2fc 0.5 --open
Then try your own table: ionomos analyze path/to/report.pg_matrix.tsv --open
"""

EXPERIMENT_YAML = f"""\
# Written by `ionomos demo`. Edit freely, then: ionomos analyze <this folder>
# (keys: docs/NAMING_CONVENTION.md, "analysis:")
notes: Simulated DIA experiment made by ionomos demo; every number is invented.
analysis:
  control: DMSO
  enrichment_libraries: []         # no downloads: only the demo gene sets below
  enrichment_gmt: {GMT_NAME}   # a relative path is read from this folder
"""


class DemoError(Exception):
    pass


def gmt_text() -> str:
    return resources.files("ionomos.downstream").joinpath("assets", GMT_NAME).read_text(encoding="utf-8")


def gene_sets() -> dict[str, list[str]]:
    """The bundled demo sets in file order: {term: [genes]}."""
    out: dict[str, list[str]] = {}
    for line in gmt_text().splitlines():
        parts = line.split("\t")
        if len(parts) >= 3:
            out[parts[0]] = [g for g in parts[2:] if g]
    return out


def pick_folder(folder: str | Path | None, cwd: Path | None = None) -> Path:
    """The folder to write. None: <cwd>/ionomos_demo, or the first free ionomos_demo_<n>. Given: it must not
    exist yet or be empty (the demo never writes into a folder that holds anything)."""
    if folder is None:
        base = Path(cwd or Path.cwd())
        p = base / DEFAULT_NAME
        n = 2
        while p.exists():
            p = base / f"{DEFAULT_NAME}_{n}"
            n += 1
        return p
    p = Path(folder).expanduser().resolve()
    if p.exists() and (not p.is_dir() or any(p.iterdir())):
        raise DemoError(f"{p} already exists and is not an empty folder; the demo only writes to a new folder "
                        "(leave the folder out to get ./ionomos_demo, or ionomos_demo_2 if that is taken)")
    return p


def write_demo(folder: Path, seed: int = SEED) -> dict[str, dict[str, int]]:
    """Write the simulated experiment into folder (created; must be new or empty). Returns the planted truth
    {condition: {gene: +1/-1}} as simulate.dia_pg_matrix reports it."""
    from ionomos.downstream import simulate

    folder = pick_folder(folder)  # the same check again: nothing that exists is ever written over
    folder.mkdir(parents=True, exist_ok=True)
    sets = gene_sets()
    genes = list(dict.fromkeys(g for members in sets.values() for g in members))
    rng = random.Random(seed)
    planted: dict[str, dict[str, float]] = {}
    for cond, term, fraction, effect in PLANTED:
        members = sets[term]
        moved = members if fraction >= 1 else rng.sample(members, round(fraction * len(members)))
        for g in moved:
            planted.setdefault(cond, {})[g] = effect * rng.uniform(0.8, 1.2)
    runs = [(f"{c}_{r}.raw", c) for c in CONDITIONS for r in range(1, REPLICATES + 1)]
    truth = simulate.dia_pg_matrix(folder / "fragpipe" / "diann-output" / "report.pg_matrix.tsv", runs, seed=seed,
                                   n_proteins=N_PROTEINS, changed_fraction=0.02, effect=1.5, genes=genes,
                                   planted=planted, absent=ABSENT)
    (folder / GMT_NAME).write_text(gmt_text(), encoding="utf-8")
    (folder / "experiment.yaml").write_text(EXPERIMENT_YAML, encoding="utf-8")
    shown = f'"{folder}"' if " " in str(folder) else str(folder)
    (folder / "README.txt").write_text(README.format(n=N_PROTEINS, folder=shown), encoding="utf-8")
    return truth


def analyze(folder: Path, progress=None):
    """The same analysis `ionomos analyze <folder>` runs, without a lab config. Returns downstream.Outcome."""
    from ionomos import postprocess

    return postprocess.run_for_folder(Path(folder), None, None, None, progress=progress)
