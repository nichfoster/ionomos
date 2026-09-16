from labwatch.cli import main
from tests.conftest import iso_raws, make_drop


def test_dry_run_accept_and_reject(lab, capsys):
    d = make_drop(lab["inbox"], "20260902-isoDTB_EJQ-2-027 (redo)", iso_raws())
    assert main(["--config", str(lab["cfg_path"]), "dry-run", str(d)]) == 0
    out = capsys.readouterr().out
    assert "WOULD ACCEPT" in out and "renamed to : 20260902-isoDTB_EJQ-2-027-redo" in out
    assert "3 rep(s) x 7 fraction(s)" in out
    assert d.is_dir()  # nothing moved

    bad = make_drop(lab["inbox"], "nobody_isoDTB", ["S_1_1.raw"])
    assert main(["--config", str(lab["cfg_path"]), "dry-run", str(bad)]) == 1
    assert "WOULD REJECT" in capsys.readouterr().out
    assert not (lab["inbox"] / "nobody_isoDTB.REJECTED.txt").exists()


def test_status_empty(lab, capsys):
    assert main(["--config", str(lab["cfg_path"]), "status"]) == 0
    assert "no ledger" in capsys.readouterr().out
