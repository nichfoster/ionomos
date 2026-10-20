"""#160: the FASTA readers outside fastasource (bundle.fasta_facts, sdrf.fasta_organism, engines._fasta_names,
benchmark.read_fasta_species) read headers through fastasource.fasta_headers: the first entry of a file saved with
a UTF-8 byte-order mark (Notepad's / Excel's "UTF-8 with BOM") is counted, and a file without line breaks is read in
bounded chunks, never as one line."""
from __future__ import annotations

import hashlib
import tracemalloc

import pytest

from ionomos import bundle
from ionomos.downstream import benchmark, engines, sdrf

BOM = b"\xef\xbb\xbf"
HUMAN = b">sp|P11111|AAA_HUMAN Alpha OS=Homo sapiens OX=9606 GN=AAA\nMK\n"
YEAST = b">sp|P22222|BBB_YEAST Beta OS=Saccharomyces cerevisiae OX=559292 GN=BBB\nMK\n"
DECOY = b">rev_sp|P22222|BBB_YEAST Beta OS=Saccharomyces cerevisiae OX=559292 GN=BBB\nKM\n"


def _facts(p):
    f = bundle.fasta_facts(p)
    return f["entries"], f["decoys"]


def _organism(p):
    sdrf._FASTA_CACHE.clear()
    return sdrf.fasta_organism(p)[0]


def _names(p):
    return engines._fasta_names(p)


def _species(p):
    return sorted(benchmark.read_fasta_species(p))


# (reader, file) -> what it reads; the same with a BOM in front as without
LAYOUTS = {
    "plain": b"",
    "bom": BOM,
}
FILES = {
    "human, yeast": HUMAN + YEAST,
    "human only": HUMAN,
    "with a decoy": HUMAN + YEAST + DECOY,
    "crlf": (HUMAN + YEAST).replace(b"\n", b"\r\n"),
}
EXPECTED = {
    ("facts", "human, yeast"): (2, 0),
    ("facts", "human only"): (1, 0),
    ("facts", "with a decoy"): (3, 1),
    ("facts", "crlf"): (2, 0),
    ("organism", "human, yeast"): None,  # 1 of 2 human: not >= 90 %, so not decided
    ("organism", "human only"): "homo sapiens",
    ("organism", "with a decoy"): None,  # the decoy is not counted
    ("organism", "crlf"): None,
    ("names", "human, yeast"): {"P11111": ("AAA", "Alpha"), "P22222": ("BBB", "Beta")},
    ("names", "human only"): {"P11111": ("AAA", "Alpha")},
    ("names", "with a decoy"): {"P11111": ("AAA", "Alpha"), "P22222": ("BBB", "Beta")},
    ("names", "crlf"): {"P11111": ("AAA", "Alpha"), "P22222": ("BBB", "Beta")},
    ("species", "human, yeast"): ["P11111", "P22222"],
    ("species", "human only"): ["P11111"],
    ("species", "with a decoy"): ["P11111", "P22222"],
    ("species", "crlf"): ["P11111", "P22222"],
}
READERS = {"facts": _facts, "organism": _organism, "names": _names, "species": _species}


@pytest.mark.parametrize("reader, file", sorted(EXPECTED))
@pytest.mark.parametrize("layout", sorted(LAYOUTS))
def test_every_reader_counts_the_first_entry_of_a_bom_file(tmp_path, reader, file, layout):
    p = tmp_path / "db.fasta"
    p.write_bytes(LAYOUTS[layout] + FILES[file])
    assert READERS[reader](p) == EXPECTED[(reader, file)]


def test_the_issue_file_with_a_bom(tmp_path):
    """The steps of #160: human first, yeast second, saved with a BOM."""
    p = tmp_path / "bom2.fasta"
    p.write_bytes(BOM + HUMAN + YEAST)
    facts = bundle.fasta_facts(p)
    assert facts["entries"] == 2 and facts["bytes"] == p.stat().st_size
    assert facts["sha256"] == hashlib.sha256(p.read_bytes()).hexdigest()  # of every byte, the BOM included
    sdrf._FASTA_CACHE.clear()
    assert sdrf.fasta_organism(p) == (None, "bom2.fasta holds several species (Homo sapiens, Saccharomyces "
                                            "cerevisiae); set analysis.sdrf.organism")
    assert engines._fasta_names(p)["P11111"] == ("AAA", "Alpha")
    species = benchmark.read_fasta_species(p)
    assert species["P11111"] and species["P22222"] and species["P11111"] != species["P22222"]


@pytest.mark.parametrize("reader", sorted(READERS))
def test_a_file_without_line_breaks_is_read_in_bounded_memory(tmp_path, reader):
    """A FASTA-named file without a line break (a sequence pasted as one line, a binary saved as .fasta) is read in
    chunks: the reader's peak memory stays far below the file's size."""
    p = tmp_path / "flat.fasta"
    size = 16 << 20
    p.write_bytes(BOM + HUMAN.rstrip(b"\n") + b"A" * size)  # one header running on to the end of the file
    tracemalloc.start()
    try:
        READERS[reader](p)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < size // 8
