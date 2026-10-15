import os
from pathlib import Path

from ionomos.cli import main
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
    import ionomos.app as app_mod

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
    from ionomos import service

    monkeypatch.setattr(service, "source_checkout", lambda: None)
    assert main(["update"]) == 2
    assert "not running from a git checkout" in capsys.readouterr().err


def _fc_table(folder, name="fc.tsv"):
    rows = ["Gene\tlogFC\tP.Value"] + [f"G{i}\t{2 if i < 5 else 0}\t{0.001 if i < 5 else 0.5}" for i in range(40)]
    p = folder / name
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return p


def test_analyze_outside_the_lab_ignores_the_pc_lab_config(lab, tmp_path, monkeypatch, capsys):
    """#134: a scratch table on a PC with a lab install is analysed with Ionomos' defaults and posts nothing to
    the lab's attention list, unless --config (or IONOMOS_CONFIG) names the lab config; a table under the lab's
    users folder is a lab experiment and uses the lab's settings."""
    import ionomos.cli as cli

    monkeypatch.delenv("IONOMOS_CONFIG", raising=False)
    monkeypatch.setattr(cli, "default_config_path", lambda: lab["cfg_path"])  # what service finds on the PC
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    table = _fc_table(scratch)
    assert main(["analyze", str(table), "--quiet", "--no-enrichment"]) == 0
    out = capsys.readouterr().out
    assert "settings: Ionomos' defaults" in out and str(lab["cfg_path"]) in out and "--config applies it" in out
    assert not list((lab["auto"] / "logs").rglob("*.json"))  # nothing in the lab's attention list

    assert main(["--config", str(lab["cfg_path"]), "analyze", str(table), "--quiet", "--no-enrichment"]) == 0
    out = capsys.readouterr().out
    assert f"settings: {lab['cfg_path']}" in out and "defaults" not in out.split("\n")[0]
    assert not list((lab["auto"] / "logs").rglob("*.json"))  # still not a lab experiment

    monkeypatch.setenv("IONOMOS_CONFIG", str(lab["cfg_path"]))
    assert main(["analyze", str(table), "--quiet", "--no-enrichment"]) == 0
    assert f"settings: {lab['cfg_path']}" in capsys.readouterr().out
    monkeypatch.delenv("IONOMOS_CONFIG")

    mine = _fc_table(lab["general"] / "EJQ", "20260901-DIA_EJQ-1.tsv")
    assert main(["analyze", str(mine), "--quiet", "--no-enrichment"]) == 0
    assert "(a lab experiment)" in capsys.readouterr().out


def test_testbed_init_warns_when_the_folder_leaves_no_room_for_long_paths(tmp_path, monkeypatch, capsys):
    """#138: a testbed 150 characters deep with Windows long paths off fails its searches on 260+ character
    paths; init now says so, as stress already did."""
    import types

    from ionomos import preflight, testbed

    fake = types.SimpleNamespace(**{k: getattr(os, k) for k in dir(os) if not k.startswith("__")})
    fake.name = "nt"
    monkeypatch.setattr(testbed, "os", fake)
    monkeypatch.setattr(preflight, "_long_paths_enabled", lambda: False)
    deep = Path("C:/" + "d" * 150)
    note = testbed.path_room_note(deep)
    assert note and "long paths are off" in note and "past Windows' 260" in note
    assert testbed.path_room_note(Path("C:/bed")) is None
    monkeypatch.setattr(preflight, "_long_paths_enabled", lambda: True)
    assert testbed.path_room_note(deep) is None
