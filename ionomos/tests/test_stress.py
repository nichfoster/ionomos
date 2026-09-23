"""Stress + fuzz: many messy drops and chaos against a real watcher/worker (see ionomos/stress.py)."""
import pytest

from ionomos import stress


def test_fuzz_names_never_crash_the_parsers():
    assert stress.fuzz_names(3000, seed=7) == []


@pytest.mark.parametrize("seed", [1, 2])
def test_stress_invariants(seed, tmp_path, monkeypatch):
    monkeypatch.setenv("IONOMOS_FAKE_FP_SECONDS", "0.2")
    rep = stress.run(n=30, seed=seed, root=tmp_path / "stress", timeout=180)
    assert rep.ok, rep.text()
    assert sum(v for k, v in rep.outcomes.items() if k in ("done", "failed")) > 5
