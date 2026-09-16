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


def test_check(lab, capsys):
    assert main(["--config", str(lab["cfg_path"]), "check"]) == 0
    out = capsys.readouterr().out
    assert "config parses" in out and "EJQ" in out and "alias IJ, IJD" in out


def test_testbed_init_list_drop_reset(tmp_path, capsys):
    bed = tmp_path / "bed"
    assert main(["testbed", "init", str(bed)]) == 0
    assert (bed / "Fragpipe_Auto" / "config.yaml").is_file()
    assert main(["testbed", "list", str(bed)]) == 0
    assert "iso_good" in capsys.readouterr().out
    assert main(["testbed", "drop", "iso_good", str(bed)]) == 0
    assert (bed / "Fragpipe_Auto" / "inbox" / "20260902-isoDTB_EJQ-2-027").is_dir()
    assert main(["--config", str(bed / "Fragpipe_Auto" / "config.yaml"), "check"]) == 0
    assert main(["--config", str(bed / "Fragpipe_Auto" / "config.yaml"), "dry-run",
                 str(bed / "Fragpipe_Auto" / "inbox" / "20260902-isoDTB_EJQ-2-027")]) == 0
    assert main(["testbed", "reset", str(bed)]) == 0
    assert not any((bed / "Fragpipe_Auto" / "inbox").iterdir())


def test_status_empty(lab, capsys):
    assert main(["--config", str(lab["cfg_path"]), "status"]) == 0
    assert "no ledger" in capsys.readouterr().out


def test_no_args_opens_setup(monkeypatch):
    import labwatch.app as app_mod

    called = []
    monkeypatch.setattr(app_mod, "main", lambda cfg=None: called.append(cfg) or 0)
    assert main([]) == 0
    assert called == [None]
    assert main(["--config", "x.yaml", "setup"]) == 0
    assert str(called[1]) == "x.yaml"


def test_diagnose(lab, capsys):
    assert main(["--config", str(lab["cfg_path"]), "diagnose"]) == 0
    out = capsys.readouterr().out
    assert "=== check" in out and "=== config.yaml" in out and "(saved to" in out
    assert list((lab["auto"] / "logs").glob("diagnostics-*.txt"))


def test_update_outside_checkout(monkeypatch, capsys):
    from labwatch import service

    monkeypatch.setattr(service, "source_checkout", lambda: None)
    assert main(["update"]) == 2
    assert "not running from a git checkout" in capsys.readouterr().err
