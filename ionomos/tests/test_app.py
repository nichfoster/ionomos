"""Drive the setup/control app through a real Tk root (skipped without a display)."""
import hashlib
import time

import pytest

from ionomos.config import load
from ionomos.resolve import gui_available
from tests.conftest import make_tk_root

_ok, _why = gui_available()
pytestmark = pytest.mark.skipif(not _ok, reason=f"no GUI: {_why}")


@pytest.fixture
def app(tmp_path, monkeypatch):
    import tkinter as tk

    from ionomos.app import App

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    root = make_tk_root()
    root.withdraw()
    a = App(root, tmp_path / "Auto" / "config.yaml")
    yield a
    # Unbound call: the finalizer must destroy the real root even if a test
    # monkeypatched app.root.destroy to a no-op (a patched instance attribute
    # would otherwise leak this Tk interpreter into the rest of the session).
    tk.Tk.destroy(root)


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
    from ionomos import fragpipe
    from ionomos.ledger import Job, Ledger

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
    led = Ledger(tmp_path / "Auto" / "ionomos.db")
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

    from ionomos.ledger import Job, Ledger

    _lab_app(app, tmp_path)
    dest = tmp_path / "General" / "EJQ" / "exp"
    dest.mkdir(parents=True)
    led = Ledger(tmp_path / "Auto" / "ionomos.db")
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


def _update_release(sha256=None, size=1024):
    from ionomos import updates

    return updates.Release(version="9.9.9", page="https://github.com/nichfoster/ionomos/releases/tag/v9.9.9",
                           asset_name="Ionomos-Setup-9.9.9.exe", asset_url="https://x/Ionomos-Setup-9.9.9.exe",
                           size=size, sha256=sha256)


def _installable_update(app, monkeypatch, shown, installed, downloaded):
    """Make install_update() believe this is an installed Windows build; record dialogs, installs and downloads."""
    from tkinter import messagebox

    from ionomos import health, updates

    monkeypatch.setattr(updates, "can_self_update", lambda: True)
    monkeypatch.setattr(health, "is_locked", lambda *a, **k: False)
    monkeypatch.setattr(updates, "install", lambda *a, **k: installed.append(a[0]))
    monkeypatch.setattr(updates, "download", lambda *a, **k: downloaded.append(a[0]))
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: True)
    monkeypatch.setattr(messagebox, "showerror", lambda *a, **k: shown.append(a))
    monkeypatch.setattr(app.root, "destroy", lambda: None)  # finish() quits the app; keep the fixture's Tk alive


def test_setup_tab_checklist_and_auto_setup(app, tmp_path, monkeypatch):
    from ionomos import configio, fragpipe

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
    # FragPipe-Analyst settings on the Lab defaults page
    assert cfg.analysis["imputation"] == "auto" and cfg.analysis["filter_condition_pct"] == 50
    app.analysis._preset(__import__("ionomos.analysis_tab", fromlist=["x"]).FRAGPIPE_ANALYST_DEFAULTS)
    app.bv("analysis.lib.KEGG").set(True)
    app.bv("analysis.lib.Reactome").set(False)
    assert app.save()
    cfg = load(tmp_path / "Auto" / "config.yaml")
    assert cfg.analysis["de_type"] == "all" and cfg.analysis["normalize"] == "none"
    assert cfg.analysis["filter_condition_pct"] == 0 and cfg.analysis["test"] == "limma"
    assert cfg.analysis["enrichment_libraries"] == ["Hallmark", "GO Biological Process", "KEGG"]


def test_analysis_tab_edits_samples_and_runs(app, tmp_path, monkeypatch):
    from ionomos.downstream import simulate
    from ionomos.ledger import Job, Ledger
    from ionomos.manifest import load_overrides

    _lab_app(app, tmp_path)
    dest = tmp_path / "General" / "Chris" / "dia"
    runs = [(f"/x/{c}_{r}.raw", c) for c in ("DMSO", "Drug") for r in (1, 2, 3)]
    simulate.dia_pg_matrix(dest / "fragpipe" / "report.pg_matrix.tsv", runs, seed=3)
    led = Ledger(tmp_path / "Auto" / "ionomos.db")
    job_id = led.insert(Job(inbox_name="dia", user="Chris", method="DIA", dest_dir=str(dest), status="done"))
    led.close()
    opened = []
    monkeypatch.setattr(app, "_open", lambda p: opened.append(p))
    app.refresh_jobs()
    app.jtree.selection_set(str(job_id))
    app.job_action("studio")  # Jobs tab -> Analysis tab with this job
    ed = app.analysis.editor
    assert app.nb.select() == str(app.tab_analysis)
    assert _pump_until(app, lambda: len(ed.tree.get_children()) == 6)
    assert ed.var("control").get() == "DMSO" and "6 samples in 2 condition(s)" in ed.info.cget("text")
    ed.set_condition(["Drug_3"], "DMSO")  # a mislabelled sample
    ed.toggle_used(["DMSO_1"])  # a failed run
    assert ed.tree.item("DMSO_1")["values"][3] == "left out" and ed.tree.item("Drug_3")["values"][1] == "DMSO"
    ed.var("log2fc").set("0.8")
    ed.var("imputation").set("none")
    assert ed.save_choices()
    an = load_overrides(dest).analysis
    assert an["sample_conditions"] == {"Drug_3": "DMSO"} and an["exclude_samples"] == ["DMSO_1"]
    assert an["log2fc"] == 0.8 and an["imputation"] == "none" and an["de_type"] == "control"
    ed.run(confirm=False)
    assert _pump_until(app, lambda: opened, secs=30)
    assert opened[-1].endswith("report.html")
    summary = __import__("json").loads((dest / "results" / "analysis.json").read_text(encoding="utf-8"))
    assert summary["samples"]["Drug_3"] == "DMSO" and "DMSO_1" not in summary["samples"]
    assert summary["settings"]["log2fc"] == 0.8 and summary["imputation"] == "none"
    # reopening shows the saved choices
    ed.load(dest, "DIA")
    assert _pump_until(app, lambda: ed.tree.exists("DMSO_1") and ed.tree.item("DMSO_1")["values"][3] == "left out")
    assert ed.var("log2fc").get() == "0.8"
    # a comparison that can't work is caught before running
    ed.var("de_type").set("control")
    ed.var("control").set("Placebo")
    assert any("Placebo" in p for p in ed.problems())


def _experiment_needing_conditions(tmp_path):
    """A DIA experiment where every file got the same condition (the one-condition case)."""
    import json

    from ionomos import names
    from ionomos.downstream import simulate

    dest = tmp_path / "General" / "Chris" / "CS_22rv1"
    runs = [(f"/x/CS_22rv1_{c}_{r}.raw", "CS") for c in ("DMSO", "MA25") for r in (1, 2, 3)]
    simulate.dia_pg_matrix(dest / "fragpipe" / "report.pg_matrix.tsv", runs, seed=5)
    rec = {"plan": {"folder": {"method": "DIA", "user": "Chris"}, "manifest": [
        {"file": f"CS_22rv1_{c}_{r}.raw", "experiment": "CS_22rv1", "bioreplicate": k}
        for k, (c, r) in enumerate(((c, r) for c in ("DMSO", "MA25") for r in (1, 2, 3)), 1)]}}
    (dest / names.STATUS_FILE).write_text(json.dumps(rec), encoding="utf-8")
    return dest


def test_analysis_that_needs_input_pops_up_and_is_fixed_in_the_window(app, tmp_path, monkeypatch):
    from ionomos import attention, postprocess
    from ionomos.config import load

    _lab_app(app, tmp_path)
    cfg = load(tmp_path / "Auto" / "config.yaml", check_paths=False)
    dest = _experiment_needing_conditions(tmp_path)
    out = postprocess.run_for_folder(dest, cfg, "DIA", {"enrichment": False})
    postprocess.record_issues(cfg.log_dir, dest, out, 7)
    its = attention.items(cfg.log_dir)
    assert len(its) == 1 and its[0].kind == "analysis_input" and "same condition" in its[0].title
    opened = []
    monkeypatch.setattr(app, "_open", lambda p: opened.append(p))
    app.popups.check()  # the poller's pass: badge + window
    assert "1 needs attention" in app.attn_btn.cget("text")
    win = app.popups.window
    assert win is not None and win.alive() and win.editor is not None
    ed = win.editor
    assert _pump_until(app, lambda: len(ed.tree.get_children()) == 6)
    assert "same condition" in ed.issues.get("1.0", "end")
    ed.guess_conditions()
    assert sorted(set(ed.conditions())) == ["DMSO", "MA25"]
    ed.var("control").set("DMSO")
    ed.run(confirm=False)
    assert _pump_until(app, lambda: opened, secs=30)
    assert not attention.items(cfg.log_dir)  # resolved by the clean re-analysis
    summary = __import__("json").loads((dest / "results" / "analysis.json").read_text(encoding="utf-8"))
    assert summary["comparisons"][0]["name"] == "MA25 vs DMSO" and summary["state"] in ("ok",)
    assert (dest / summary["comparisons"][0]["volcano"]).is_file()
    app.popups.check()
    assert not app.attn_btn.winfo_ismapped() or app.attn_btn.cget("text") == ""
    # an item pops up once; snoozed/dismissed ones don't come back by themselves
    it = attention.raise_item(cfg.log_dir, "search_waiting", "x waits", "no FASTA", key="w1")
    app.popups.window.close()
    app.popups.check()
    assert app.popups.window.item.id == it.id
    app.popups.window.snooze()
    app.popups.check()
    assert not app.popups.window.alive()


def test_failed_search_window_retries_the_job(app, tmp_path):
    from ionomos import attention
    from ionomos.config import load
    from ionomos.ledger import Job, Ledger

    _lab_app(app, tmp_path)
    cfg = load(tmp_path / "Auto" / "config.yaml", check_paths=False)
    led = Ledger(cfg.database)
    jid = led.insert(Job(inbox_name="x", user="EJQ", method="DIA", dest_dir=str(tmp_path / "x"), status="failed"))
    led.close()
    attention.raise_item(cfg.log_dir, "search_failed", "FragPipe failed on EJQ/x", "exit code 1", severity="error",
                         key=f"search_failed:job{jid}", job_id=jid, dest=tmp_path / "x",
                         causes=["FragPipe ran out of memory"], details="java.lang.OutOfMemoryError")
    app.popups.check()
    win = app.popups.window
    assert win.alive() and "out of memory" in str(win.item.causes).lower()
    win.retry()
    led = Ledger(cfg.database)
    assert led.get(jid).status == "queued"
    led.close()
    assert not attention.items(cfg.log_dir)
    # the list window
    attention.raise_item(cfg.log_dir, "intake_rejected", "Couldn't take in y", "no raw files", key="y",
                         severity="error", data={"folder": str(tmp_path / "Auto" / "inbox" / "y")})
    (tmp_path / "Auto" / "inbox" / "y").mkdir()
    c = app.popups.center()
    assert len(app.popups._center_tree.get_children()) == 1
    c.destroy()
    (tmp_path / "Auto" / "inbox" / "y").rmdir()  # the folder was dealt with -> the item closes itself
    app.popups.check()
    assert not attention.items(cfg.log_dir)

def test_jobs_report_and_rerun(app, tmp_path, monkeypatch):
    from ionomos.downstream import simulate
    from ionomos.ledger import Job, Ledger

    _lab_app(app, tmp_path)
    dest = tmp_path / "General" / "EJQ" / "dia"
    runs = [(f"/x/{c}_{r}.raw", c) for c in ("DMSO", "Drug") for r in (1, 2, 3)]
    simulate.dia_pg_matrix(dest / "fragpipe" / "report.pg_matrix.tsv", runs, seed=1)
    led = Ledger(tmp_path / "Auto" / "ionomos.db")
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


def test_report_a_problem_puts_one_zip_on_the_desktop(app, tmp_path, monkeypatch):
    import zipfile
    from tkinter import messagebox

    from ionomos import service

    _lab_app(app, tmp_path)
    shown, revealed, clip = [], [], []
    monkeypatch.setattr(service, "desktop_dir", lambda: tmp_path)
    monkeypatch.setattr(service, "reveal", revealed.append)
    monkeypatch.setattr(messagebox, "showinfo", lambda *a, **k: shown.append(a))
    monkeypatch.setattr(app.root, "clipboard_clear", lambda: None)
    monkeypatch.setattr(app.root, "clipboard_append", clip.append)
    app.report_problem()
    app.report_text.insert("1.0", "the watcher stopped after lunch")
    app.report_debug.set(True)
    app.report_create()
    assert _pump_until(app, lambda: shown)
    z = revealed[0]
    assert z.parent == tmp_path and z.name.startswith("Ionomos-report-") and clip == [str(z)]
    note = zipfile.ZipFile(z).read("note.txt").decode()
    assert "stopped after lunch" in note and "detailed logging turned on" in note
    assert (tmp_path / "Auto" / "logs" / "DEBUG_UNTIL").is_file()


def test_update_banner_appears_for_a_downloaded_installer(app, tmp_path, monkeypatch):
    from ionomos import updates

    (tmp_path / "Ionomos-Setup-9.9.9.exe").write_text("x", encoding="utf-8")
    monkeypatch.setattr(updates, "downloads_dir", lambda: tmp_path)
    app.check_downloaded_update()
    assert _pump_until(app, lambda: app.update_btn.cget("text") == "Update to 9.9.9")


def test_install_update_refuses_a_digest_less_release(app, tmp_path, monkeypatch):
    """A Downloads installer is never run without a published SHA-256 for its exact version."""

    shown, installed, downloaded = [], [], []
    _installable_update(app, monkeypatch, shown, installed, downloaded)
    installer = tmp_path / "Ionomos-Setup-9.9.9.exe"
    installer.write_bytes(b"MZ fake installer")
    app._release, app._update_found = None, (installer, "9.9.9")
    app.install_update()
    assert len(shown) == 1 and "releases" in shown[0][1]
    assert not installed


def test_install_update_refuses_an_installer_from_a_different_version(app, tmp_path, monkeypatch):
    """A Downloads installer claiming a version the release doesn't cover has no digest reference — refuse."""

    shown, installed, downloaded = [], [], []
    _installable_update(app, monkeypatch, shown, installed, downloaded)
    payload = b"MZ fake installer"
    installer = tmp_path / "Ionomos-Setup-9.9.10.exe"
    installer.write_bytes(payload)
    app._release = _update_release(sha256=hashlib.sha256(payload).hexdigest(), size=len(payload))
    app._update_found = (installer, "9.9.10")
    app.install_update()
    assert len(shown) == 1 and "releases" in shown[0][1]
    assert not installed and not downloaded


def test_install_update_refuses_an_installer_that_fails_its_checksum(app, tmp_path, monkeypatch):

    shown, installed, downloaded = [], [], []
    _installable_update(app, monkeypatch, shown, installed, downloaded)
    payload = b"MZ fake installer"
    installer = tmp_path / "Ionomos-Setup-9.9.9.exe"
    installer.write_bytes(b"X" * len(payload))  # same size, wrong content: the digest check must catch it
    app._release = _update_release(sha256=hashlib.sha256(payload).hexdigest(), size=len(payload))
    app._update_found = (installer, "9.9.9")
    app.install_update()
    assert len(shown) == 1 and "checksum" in shown[0][1]
    assert not installed and not downloaded


def test_install_update_runs_a_verified_downloads_installer(app, tmp_path, monkeypatch):

    shown, installed, downloaded = [], [], []
    _installable_update(app, monkeypatch, shown, installed, downloaded)
    payload = b"MZ fake installer"
    installer = tmp_path / "Ionomos-Setup-9.9.9.exe"
    installer.write_bytes(payload)
    app._release = _update_release(sha256=hashlib.sha256(payload).hexdigest(), size=len(payload))
    app._update_found = (installer, "9.9.9")
    app.install_update()
    assert installed == [installer]
    assert not shown and not downloaded


def test_install_update_wont_download_a_digest_less_release(app, monkeypatch):

    shown, installed, downloaded = [], [], []
    _installable_update(app, monkeypatch, shown, installed, downloaded)
    app._release, app._update_found = _update_release(sha256=None), None
    app.install_update()
    assert len(shown) == 1 and "releases" in shown[0][1]
    assert not downloaded and not installed


def test_inbox_delete_refreshes_and_preserves_raw(app, tmp_path):
    from tests.conftest import make_drop

    inbox = tmp_path / 'inbox'
    inbox.mkdir()
    folder = make_drop(inbox, 'bad', ['bad.raw'])
    app.v('paths.inbox').set(str(inbox))
    app._refresh_inbox()
    app.inbox_tree.selection_set(str(folder / 'bad.raw'))
    app._delete_inbox()
    assert not app.inbox_tree.exists(str(folder / 'bad.raw'))
    assert list((inbox / '.removed').rglob('bad.raw'))
