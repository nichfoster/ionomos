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


# ------------------------------------------------------------- what ships where --

def _pyproject() -> dict:
    import tomllib

    return tomllib.loads((PKG / "pyproject.toml").read_text(encoding="utf-8"))


def _check_frozen():
    """deploy/check_frozen.py, the build-time check build_exe.ps1 runs on both exes."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("check_frozen", PKG.parent / "deploy" / "check_frozen.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_every_data_file_in_the_package_is_package_data():
    """A file the code reads at run time (report.js, help pages, scenarios) that no package-data glob covers is
    left out of the wheel: `pip install ionomos` would fail where a checkout works."""
    import fnmatch

    globs = _pyproject()["tool"]["setuptools"]["package-data"]
    _modules, data = _check_frozen().expected(PKG / "src")
    assert {"ionomos/downstream/assets/report.js", "ionomos/help/faq.md"} <= data
    uncovered = []
    for rel in sorted(data):
        parts = rel.split("/")
        covered = False
        for i in range(1, len(parts)):  # the deepest package wins in setuptools; any covering glob will do here
            pats = globs.get(".".join(parts[:i]), [])
            if any(fnmatch.fnmatch("/".join(parts[i:]), p) for p in pats):
                covered = True
        if not covered:
            uncovered.append(rel)
    assert not uncovered, f"add these to [tool.setuptools.package-data] in pyproject.toml: {uncovered}"


def test_the_exe_spec_bundles_every_package_data_folder():
    """deploy/ionomos.spec lists `datas` by hand: each package-data folder of the wheel must be there too."""
    spec = (PKG.parent / "deploy" / "ionomos.spec").read_text(encoding="utf-8")
    for pkg, pats in _pyproject()["tool"]["setuptools"]["package-data"].items():
        for pat in pats:
            dest = "/".join([*pkg.split("."), *pat.split("/")[:-1]])
            assert f'"{dest}")' in spec, f"deploy/ionomos.spec datas lacks {dest} (package data {pkg}: {pat})"


def test_the_frozen_check_reports_what_an_exe_lacks():
    cf = _check_frozen()
    want = cf.expected(PKG / "src")
    mods, data = want
    assert {"ionomos.cli", "ionomos.downstream.tmtqc", "ionomos.library", "ionomos.uinav"} <= mods
    assert "ionomos.__main__" not in mods and not any(m.startswith("ionomos.runners") for m in mods)
    assert cf.missing(want, (set(mods) | {"yaml"}, set(data))) == []
    assert cf.missing(want, (mods - {"ionomos.tmtkit"}, data - {"ionomos/help/faq.md"})) == [
        "module ionomos.tmtkit", "data   ionomos/help/faq.md"]


def test_the_sdist_leaves_out_the_tests():
    """setuptools puts tests/test_*.py into an sdist by default, without conftest.py, golden files or ../deploy:
    a test suite that cannot run. MANIFEST.in prunes it (the CI `package` job checks the built sdist)."""
    lines = (PKG / "MANIFEST.in").read_text(encoding="utf-8").splitlines()
    assert "prune tests" in lines


def test_the_python_classifiers_are_the_versions_ci_tests():
    """A `Programming Language :: Python :: 3.x` classifier tells pip users that version works: CI must run it."""
    import yaml

    claimed = {c.rsplit(" ", 1)[-1] for c in _pyproject()["project"]["classifiers"]
               if c.startswith("Programming Language :: Python :: 3.")}
    ci = yaml.safe_load((PKG.parent / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8"))
    tested = {str(row["python"]) for row in ci["jobs"]["test"]["strategy"]["matrix"]["include"]}
    assert claimed == tested, f"classifiers {sorted(claimed)} vs tests.yml {sorted(tested)}"
    floor = _pyproject()["project"]["requires-python"].removeprefix(">=")
    assert floor == min(tested, key=lambda v: tuple(map(int, v.split("."))))
