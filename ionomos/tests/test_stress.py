"""Stress + fuzz: many messy drops and chaos against a real watcher/worker (see ionomos/stress.py)."""
import pytest

from ionomos import stress


def test_fuzz_names_never_crash_the_parsers():
    assert stress.fuzz_names(3000, seed=7) == []


def test_the_stress_tmt_drops_carry_a_map_fragpipe_would_take():
    """The tmt drops' experiment.yaml listed 4 of TMT-10's 10 channels, so since D91 every one failed before the
    search ("lists 4 channel(s) but the workflow's label type is TMT-10") and the stress run searched no TMT."""
    import yaml

    from ionomos import tmtkit

    block = yaml.safe_load(stress.TMT_MAP)["tmt"]
    flags = tmtkit.validate({"plex": {str(k): str(v) for k, v in block["channels"].items()}}, "TMT-10")
    assert not [f for f in flags if f.severity == "error"], flags


@pytest.mark.parametrize("seed", [1, 2])
def test_stress_invariants(seed, tmp_path, monkeypatch):
    monkeypatch.setenv("IONOMOS_FAKE_FP_SECONDS", "0.2")
    rep = stress.run(n=30, seed=seed, root=tmp_path / "stress", timeout=180)
    assert rep.ok, rep.text()
    assert sum(v for k, v in rep.outcomes.items() if k in ("done", "failed")) > 5
