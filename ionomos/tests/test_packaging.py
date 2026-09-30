"""What `pip install ionomos` ships (ionomos/pyproject.toml). The wheel itself is built and installed into a
fresh venv by the publish workflow (.github/workflows/publish-pypi.yml) before anything is uploaded."""
from pathlib import Path

from ionomos import demo
from ionomos.downstream import enrich

HERE = Path(__file__).resolve().parent
PKG = HERE.parent


def test_package_license_is_the_repo_license():
    # license-files can't point outside the package folder, so ionomos/LICENSE is a copy of ../LICENSE
    assert (PKG / "LICENSE").read_bytes() == (PKG.parent / "LICENSE").read_bytes()


def test_demo_gene_sets_ship_as_package_data():
    assert "assets/*" in (PKG / "pyproject.toml").read_text(encoding="utf-8")
    sets = demo.gene_sets()  # read through importlib.resources, as from an installed wheel
    assert len(sets) >= 10 and all(len(g) >= 10 for g in sets.values())
    genes = [g for members in sets.values() for g in members]
    assert len(genes) == len(set(genes)), "a gene in two demo sets blurs what the demo shows"
    assert set(enrich.parse_library(demo.gmt_text())) == set(sets)
    assert {term for _cond, term, _fraction, _effect in demo.PLANTED} <= set(sets)
    absent = {g for gs in demo.ABSENT.values() for g in gs}
    assert absent - set(genes) <= {f"GENE{i}" for i in range(len(genes), demo.N_PROTEINS)}
