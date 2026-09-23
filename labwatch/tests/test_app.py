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
