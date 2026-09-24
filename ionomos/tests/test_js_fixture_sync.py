"""Fixture-sync checks for the dev-only JS harness in ``tests/js``.

The jsdom harness loads ``tests/js/fixture.html`` and evaluates the report
script it contains. Both must track the real assets: if report.py's template,
the inlined report.js, or the payload schema drift, these checks fail with a
pointed message instead of letting the JS tests pass against a stale page.

Regenerate the fixture after changing report.py, report.js or report.css:

    cd ionomos && .venv/bin/python tests/js/make_fixture.py
"""

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ASSETS = HERE.parent / "src" / "ionomos" / "downstream" / "assets"

FIXTURE = HERE / "js" / "fixture.html"


def _read_fixture() -> str:
    if not FIXTURE.exists():
        raise AssertionError(
            f"{FIXTURE} is missing. Generate it with: "
            f"cd ionomos && .venv/bin/python tests/js/make_fixture.py"
        )
    return FIXTURE.read_text(encoding="utf-8")


def test_fixture_tracks_the_current_assets():
    fixture = _read_fixture()
    report_js = (ASSETS / "report.js").read_text(encoding="utf-8")
    report_css = (ASSETS / "report.css").read_text(encoding="utf-8")
    assert report_js in fixture, (
        "tests/js/fixture.html does not contain the current report.js — "
        "the harness would test a stale front end. Regenerate: "
        "cd ionomos && .venv/bin/python tests/js/make_fixture.py"
    )
    assert report_css in fixture, (
        "tests/js/fixture.html does not contain the current report.css — "
        "regenerate the fixture: cd ionomos && .venv/bin/python tests/js/make_fixture.py"
    )


def test_fixture_carries_the_data_script_and_marker():
    fixture = _read_fixture()
    assert "<script id='ionomos-data' type='application/json'>" in fixture, (
        "fixture.html lost the #ionomos-data script tag the harness swaps data into"
    )
    assert "ionomos-report-v2" in fixture, "fixture.html lost the report marker"


def test_fixture_payload_shape_matches_the_report_reader():
    """The keys report.js reads must be present in the fixture payload.

    Deliberately minimal: the point is that make_fixture.py keeps up with
    report.py's payload() schema, not a full schema check.
    """
    fixture = _read_fixture()
    m = re.search(
        r"<script id='ionomos-data' type='application/json'>(.*?)</script>",
        fixture,
        re.DOTALL,
    )
    assert m, "no ionomos-data script in fixture.html"
    payload = json.loads(m.group(1))
    for key in ("title", "samples", "cond", "f", "v", "comps", "qc"):
        assert key in payload, f"fixture payload lost key {key!r}"


if __name__ == "__main__":  # pragma: no cover
    sys.exit(__import__("pytest").main([__file__]))
