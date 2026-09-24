"""Generate tests/js/fixture.html by running a real DIA analysis end to end.

The JS harness tests report.js against the exact HTML the Python side ships,
so the fixture is produced by the same pipeline a lab run uses
(``simulate.dia_pg_matrix`` -> ``downstream.analyze`` -> ``report.render``).

Run it with the project venv from anywhere::

    .venv/bin/python tests/js/make_fixture.py

Output is deterministic (fixed simulation and imputation seeds). The committed
fixture.html is kept in sync by ``test_sync.py`` (run by pytest), which
re-renders and compares the embedded JSON against the committed fixture, so a
change to report.py's payload can't leave the JS tests testing stale data.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

TESTS_JS = Path(__file__).resolve().parent
SRC = TESTS_JS.parent.parent / "src"  # ionomos/src


def make_fixture_html() -> str:
    """Render report.html for a small simulated DIA experiment and return its text."""
    import sys

    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))

    from ionomos import downstream
    from ionomos.downstream import simulate

    with tempfile.TemporaryDirectory(prefix="ionomos-js-fixture-") as td:
        dest = Path(td) / "e"
        runs = [(f"C:\\Fragpipe_General\\Fix\\exp\\raw\\{c}_{r}.raw", c)
                for c in ("DMSO", "Drug") for r in (1, 2, 3)]
        simulate.dia_pg_matrix(
            dest / "fragpipe/diann-output/report.pg_matrix.tsv", runs, seed=6, n_proteins=90
        )
        record = {"plan": {"manifest": [{"file": f"raw/{c}_{r}.raw", "experiment": c, "bioreplicate": r}
                                        for c in ("DMSO", "Drug") for r in (1, 2, 3)]}}
        out = downstream.analyze(
            dest, "DIA", record=record,
            context={"experiment": "Fixture_Exp", "user": "Fix", "date": "2026-09-24"},
        )
        report = out.summary.get("report") if isinstance(out.summary, dict) else None
        if not report:
            report = "results/report.html"
        report = dest / report  # summary carries it relative to the experiment folder
        assert report.exists(), f"no report written: {sorted(p.name for p in dest.rglob('*'))}"
        return report.read_text(encoding="utf-8")


def main() -> None:
    html = make_fixture_html()
    target = TESTS_JS / "fixture.html"
    target.write_text(html, encoding="utf-8", newline="\n")
    print(f"wrote {target} ({len(html)} bytes)")


if __name__ == "__main__":
    main()
