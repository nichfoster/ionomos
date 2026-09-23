"""Drive the setup/control app through a real Tk root (skipped without a display)."""
import time

import pytest

from labwatch.config import load
from labwatch.resolve import gui_available

_ok, _why = gui_available()
pytestmark = pytest.mark.skipif(not _ok, reason=f"no GUI: {_why}")


@pytest.fixture
def app(tmp_path, monkeypatch):
    import tkinter as tk

    from labwatch.app import App

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    try:
        root = tk.Tk()
    except tk.TclError:  # GitHub's Windows runners sometimes fail Tcl init after many interpreters; once more
        import gc

        gc.collect()
        root = tk.Tk()
    root.withdraw()
    a = App(root, tmp_path / "Auto" / "config.yaml")
    yield a
    root.destroy()


def test_first_run_wizard_flow(app, tmp_path):
    assert app.first_run
    root = tmp_path / "Auto"
    # 1 Folders: quick layout + create all
    app.v("quick.root").set(str(root))
    app.v("quick.users").set(str(tmp_path / "General"))
    app.apply_quick()
    assert app.v("paths.inbox").get() == f"{root}/inbox".replace("\\", "/")
    app.create_all()
    assert (root / "inbox").is_dir() and (root / "logs").is_dir() and (tmp_path / "General").is_dir()
    # 2 Users: add two, alias one
    app.v("users.new").set("Isaac")
    app.add_user()
    app.v("users.new").set("EJQ")
    app.add_user()
    assert (tmp_path / "General" / "Isaac").is_dir()
    assert list(app.user_list.get(0, "end")) == ["EJQ", "Isaac"]
    app.user_list.selection_clear(0, "end")
    app.user_list.selection_set(1)
    app._show_user()
    app.v("users.sel_aliases").set("IJ, IJD")
    app.apply_aliases()
    # 3 Methods: change the DIA workflow name
    app.mtree.selection_set("DIA")
    app._show_method()
    app.v("m.workflow").set("DIA_SpecLib.workflow")
    app.apply_method()
    # 4 Advanced: a number
    app.v("watcher.stable_seconds").set("45")
    # Save -> file exists, loader accepts it, values present
    assert app.save()
    cfg = load(root / "config.yaml")
    assert cfg.user_aliases["Isaac"] == ["IJ", "IJD"]
    assert cfg.methods["DIA"].workflow == "DIA_SpecLib.workflow"
    assert cfg.stable_seconds == 45
    assert cfg.known_users() == ["EJQ", "Isaac"]
    assert not app.first_run


def test_save_rejects_bad_number(app, monkeypatch):
    from tkinter import messagebox

    shown = []
    monkeypatch.setattr(messagebox, "showerror", lambda *a, **k: shown.append(a))
    app.v("watcher.poll_seconds").set("ten")
    assert app.save() is False
    assert shown and "poll_seconds" in shown[0][1]


def test_testbed_from_app(app, tmp_path):
    app.v("tb.dir").set(str(tmp_path / "bed"))
    app.tb_init()
    assert (tmp_path / "bed" / "Fragpipe_Auto" / "config.yaml").is_file()
    app.tb_sample.set("iso_good")
    app.tb_drop(False)
    import time

    for _ in range(50):
        if (tmp_path / "bed" / "Fragpipe_Auto" / "inbox" / "20260902-isoDTB_EJQ-2-027").is_dir():
            break
        time.sleep(0.1)
    assert (tmp_path / "bed" / "Fragpipe_Auto" / "inbox" / "20260902-isoDTB_EJQ-2-027").is_dir()


def test_start_and_stop_testbed_watcher(app, tmp_path):
    app.v("tb.dir").set(str(tmp_path / "bed"))
    app.tb_init()
    app.start_watcher(testbed=True)
    assert app.proc is not None and app.proc.poll() is None
    app.stop_watcher()
    assert app.proc is None


def test_dev_section_and_diagnostics(app, tmp_path, monkeypatch):
    from tkinter import messagebox

    # running the tests from the checkout -> the dev box exists
    assert app.repo is not None and app.dev_lbl.winfo_exists()
    monkeypatch.setattr(messagebox, "showinfo", lambda *a, **k: None)
    monkeypatch.setattr(messagebox, "showerror", lambda *a, **k: pytest.fail(f"dialog: {a}"))
    clip = []
    monkeypatch.setattr(app.root, "clipboard_clear", lambda: clip.clear())
    monkeypatch.setattr(app.root, "clipboard_append", clip.append)  # real pasteboard + destroy() segfaults macOS Tk
    app.v("quick.root").set(str(tmp_path / "Auto"))
    app.v("quick.users").set(str(tmp_path / "General"))
    app.apply_quick()
    app.create_all()
    assert app.save()
    app.copy_diagnostics()
    for _ in range(50):
        app.root.update()
        if "=== check" in app.out.text.get("1.0", "end"):
            break
        time.sleep(0.05)
    shown = app.out.text.get("1.0", "end")
    assert "=== check" in shown and "=== config.yaml" in shown
    assert clip and "=== check" in clip[0]


def test_fragpipe_controls(app, tmp_path, monkeypatch):
    from labwatch import fragpipe
    from labwatch.ledger import Job, Ledger

    app.v("quick.root").set(str(tmp_path / "Auto"))
    app.v("quick.users").set(str(tmp_path / "General"))
    app.apply_quick()
    app.create_all()
    # Find FragPipe fills the launcher path
    fake = tmp_path / "FragPipe-24.0" / "fragpipe" / "bin" / "fragpipe.bat"
    monkeypatch.setattr(fragpipe, "detect_launcher", lambda: fake)
    app.find_fragpipe()
    assert app.v("paths.fragpipe_exe").get() == str(fake).replace("\\", "/")
    # auto_run round-trips through config.yaml
    app.bv("fragpipe.auto_run").set(False)
    assert app.save()
    assert load(tmp_path / "Auto" / "config.yaml", check_paths=False).auto_run is False
    # job summary reads the ledger
    assert app._jobs_summary() == "FragPipe: no jobs yet"
    led = Ledger(tmp_path / "Auto" / "labwatch.db")
    led.insert(Job(inbox_name="a", user="EJQ", method="isoDTB", dest_dir=str(tmp_path)))
    led.insert(Job(inbox_name="b", user="EJQ", method="DIA", dest_dir=str(tmp_path)))
    led.start_attempt(1)
    led.set_status(2, "failed", "boom")
    s = app._jobs_summary()
    assert "RUNNING job 1" in s and "0 queued, 1 failed, 0 done" in s


def _lab_app(app, tmp_path):
    app.v("quick.root").set(str(tmp_path / "Auto"))
    app.v("quick.users").set(str(tmp_path / "General"))
    app.apply_quick()
    app.create_all()
    assert app.save()


def test_invalid_save_never_replaces_a_good_config(app, tmp_path, monkeypatch):
    from tkinter import messagebox

    _lab_app(app, tmp_path)
    good = (tmp_path / "Auto" / "config.yaml").read_text(encoding="utf-8")
    errors = []
    monkeypatch.setattr(messagebox, "showerror", lambda *a, **k: errors.append(a))
    app.v("paths.inbox").set(str(tmp_path / "does not exist" / "inbox"))  # missing folder -> invalid
    assert not app.save()
    assert errors and (tmp_path / "Auto" / "config.yaml").read_text(encoding="utf-8") == good
    assert not list((tmp_path / "Auto").glob(".config.checking.yaml"))


def test_tk_errors_are_caught_and_reported(app, tmp_path, monkeypatch):
    from tkinter import messagebox

    _lab_app(app, tmp_path)
    shown = []
    monkeypatch.setattr(messagebox, "showerror", lambda *a, **k: shown.append(a))
    try:
        raise ValueError("button exploded")
    except ValueError:
        import sys

        app._on_tk_error(*sys.exc_info())
    assert shown and "button exploded" in shown[0][1]
    assert list((tmp_path / "Auto" / "logs").glob("crash-*.txt"))
    app.post(lambda: 1 / 0)  # a failing UI callback must not stop the pump
    app._pump_ui()
    assert len(shown) == 2


def test_jobs_tab_lists_and_acts(app, tmp_path, monkeypatch):
    from tkinter import messagebox

    from labwatch.ledger import Job, Ledger

    _lab_app(app, tmp_path)
    dest = tmp_path / "General" / "EJQ" / "exp"
    dest.mkdir(parents=True)
    led = Ledger(tmp_path / "Auto" / "labwatch.db")
    led.insert(Job(inbox_name="exp", user="EJQ", method="isoDTB", dest_dir=str(dest)))
    led.set_status(1, "failed", "FragPipe exited with code 1")
    led.insert(Job(inbox_name="exp2", user="EJQ", method="DIA", dest_dir=str(dest)))
    app.refresh_jobs()
    assert set(app.jtree.get_children()) == {"1", "2"}
    assert app.jtree.set("1", "status") == "failed"
    app.jtree.selection_set("1")
    app.show_job()
    assert "FragPipe exited with code 1" in app.jdetail.text.get("1.0", "end")
    app.job_action("retry")
    assert led.get(1).status == "queued" and led.get(1).attempts == 0
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: True)
    app.refresh_jobs()
    app.jtree.selection_set("2")
    app.job_action("cancel")
    assert led.get(2).status == "failed" and "cancelled" in led.get(2).reason
    app.toggle_pause()
    assert (tmp_path / "Auto" / "logs" / "PAUSED").exists()
    app.refresh_jobs()
    assert "PAUSED" in app.jobs_state.cget("text")
    app.toggle_pause()
    assert not (tmp_path / "Auto" / "logs" / "PAUSED").exists()


def test_import_workflow_and_restore_config(app, tmp_path, monkeypatch):
    from tkinter import filedialog, messagebox

    _lab_app(app, tmp_path)
    fa = tmp_path / "db.fas"
    fa.write_text(">sp|A\nMK\n>rev_sp|A\nKM\n", encoding="utf-8")
    wf = tmp_path / "good_run" / "fragpipe.workflow"
    wf.parent.mkdir()
    wf.write_text(f"database.db-path={fa.as_posix()}\n", encoding="utf-8")
    monkeypatch.setattr(filedialog, "askopenfilename", lambda **k: str(wf))
    monkeypatch.setattr(messagebox, "showinfo", lambda *a, **k: None)
    app.mtree.selection_set("isoDTB")
    app._show_method()
    app.import_workflow()
    assert app.data["methods"]["isoDTB"]["workflow"] == "isoDTB.workflow"
    assert app.data["methods"]["isoDTB"]["fasta"] == "db.fas"
    assert (tmp_path / "Auto" / "fasta" / "db.fas").is_file()
    assert app.save()
    backups = sorted((tmp_path / "Auto" / "config-backups").glob("config-*.yaml"))
    assert backups
    monkeypatch.setattr(filedialog, "askopenfilename", lambda **k: str(backups[0]))
    app.restore_config()
    assert app.data["methods"]["isoDTB"]["fasta"] == "human_reviewed_decoys.fas"  # the pre-import version


def _pump_until(app, cond, secs=15):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        app.root.update()
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_setup_tab_checklist_and_auto_setup(app, tmp_path, monkeypatch):
    from labwatch import configio, fragpipe

    assert app.nb.index(app.nb.select()) == 0  # the checklist is what you see first
    d = configio.defaults(str(tmp_path / "Auto"), str(tmp_path / "General"))
    monkeypatch.setattr(configio, "defaults", lambda *a, **k: d)
    monkeypatch.setattr(fragpipe, "detect_launcher", lambda: None)
    app.auto_setup()
    assert (tmp_path / "Auto" / "inbox").is_dir() and (tmp_path / "Auto" / "config.yaml").is_file()
    assert _pump_until(app, lambda: getattr(app, "setup_items", None))
    keys = {i.key: i.status for i in app.setup_items}
    assert keys["folders"] == "ok" and keys["config"] == "ok" and keys["users"] == "todo"
    assert "ready" in app.setup_summary.cget("text")
    app._setup_action("open_tab:2")
    assert app.nb.select() == str(app.tab_users)


def test_analysis_tab_round_trips(app, tmp_path):
    _lab_app(app, tmp_path)
    app.v("analysis.log2fc").set("0.58")
    app.v("analysis.test").set("welch")
    app.bv("analysis.use_adjusted").set(False)
    app.v("analysis.control_keywords").set("DMSO, Veh")
    assert app.save()
    cfg = load(tmp_path / "Auto" / "config.yaml")
    assert cfg.analysis["log2fc"] == 0.58 and cfg.analysis["test"] == "welch"
    assert cfg.analysis["use_adjusted"] is False and cfg.analysis["control_keywords"] == ["DMSO", "Veh"]


def test_jobs_report_and_rerun(app, tmp_path, monkeypatch):
    from labwatch.downstream import simulate
    from labwatch.ledger import Job, Ledger

    _lab_app(app, tmp_path)
    dest = tmp_path / "General" / "EJQ" / "dia"
    runs = [(f"/x/{c}_{r}.raw", c) for c in ("DMSO", "Drug") for r in (1, 2, 3)]
    simulate.dia_pg_matrix(dest / "fragpipe" / "report.pg_matrix.tsv", runs, seed=1)
    led = Ledger(tmp_path / "Auto" / "labwatch.db")
    led.insert(Job(inbox_name="dia", user="EJQ", method="DIA", dest_dir=str(dest), status="done"))
    opened = []
    monkeypatch.setattr(app, "_open", lambda p: opened.append(p))
    app.refresh_jobs()
    app.jtree.selection_set("1")
    app.job_action("analyze")
    assert _pump_until(app, lambda: opened)
    assert opened[-1].endswith("report.html") and (dest / "results" / "report.html").is_file()
    app.job_action("report")
    assert opened[-1].endswith("report.html")
