"""Drive the setup/control app through a real Tk root (skipped without a display; the window and every window it
opens are hidden: tests/tkharness.py, docs/UI.md S4.1)."""
import hashlib
import time

import pytest

from ionomos.config import load
from tests.conftest import gui_tests, make_tk_root
from tests.tkharness import destroy_app, make_app, pump_until

_ok, _why = gui_tests()
pytestmark = pytest.mark.skipif(not _ok, reason=f"no GUI: {_why}")


@pytest.fixture
def app(tmp_path, monkeypatch):
    a = make_app(tmp_path, monkeypatch)
    yield a
    destroy_app(a)  # destroys the real root even if a test monkeypatched app.root.destroy (see tkharness)


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
    # collected off the Tk thread: 2.5 s was not always enough on a Windows runner
    _pump_until(app, lambda: "=== check" in app.out.text.get("1.0", "end"), secs=30)
    shown = app.out.text.get("1.0", "end")
    assert "=== check" in shown and "=== config.yaml" in shown
    assert clip and "=== check" in clip[0]
    # anonymised by default (D74): the saved copy has its key next to it, in the log folder
    from ionomos import names

    logs = tmp_path / "Auto" / "logs"
    assert list(logs.glob("diagnostics-*-anonymised.txt")) and list(logs.glob("diagnostics-*" + names.BUNDLE_KEY_SUFFIX))
    # ... and the real text is one button away
    clip.clear()
    app.copy_diagnostics(anonymise=False)
    _pump_until(app, lambda: clip, secs=30)
    assert clip and "=== check" in clip[0] and str(tmp_path) in clip[0]


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
    app.import_workflow()  # the copy runs on a worker thread (S2.2); the window takes the result
    assert _pump_until(app, lambda: app.data["methods"]["isoDTB"]["fasta"] == "db.fas")
    assert app.data["methods"]["isoDTB"]["workflow"] == "isoDTB.workflow"
    assert (tmp_path / "Auto" / "fasta" / "db.fas").is_file()
    assert app.save()
    backups = sorted((tmp_path / "Auto" / "config-backups").glob("config-*.yaml"))
    assert backups
    assert app.restore_config(backups[0])  # Restore a previous config… with the backup picked
    assert app.data["methods"]["isoDTB"]["fasta"] == "human_reviewed_decoys.fas"  # the pre-import version
    assert app.dirty()  # loaded into the window, not written
    # Undo last save: the newest backup (the state before the last Save) comes back into the window
    app.v("watcher.stable_seconds").set("77")
    assert app.save()
    assert load(tmp_path / "Auto" / "config.yaml", check_paths=False).stable_seconds == 77
    assert app.undo_last_save()
    assert app.v("watcher.stable_seconds").get() == "60" and app.dirty()
    assert load(tmp_path / "Auto" / "config.yaml", check_paths=False).stable_seconds == 77  # nothing written yet
    assert app.save()
    assert load(tmp_path / "Auto" / "config.yaml", check_paths=False).stable_seconds == 60
    # the picker window lists the backups with their time and what differs, read off the Tk thread
    app.restore_config()
    win = app.restore_win
    assert _pump_until(app, lambda: any("newest first" in w.cget("text") for w in _labels(win)))
    rows = [w for w in _widgets(app.restore_win) if w.winfo_class() == "Treeview"]
    assert rows and len(rows[0].get_children()) >= 2
    assert "Stable seconds" in rows[0].item(rows[0].get_children()[0])["values"][1]
    win.destroy()


def _widgets(w):
    out = []
    for c in w.winfo_children():
        out.append(c)
        out += _widgets(c)
    return out


def _labels(w):
    return [c for c in _widgets(w) if c.winfo_class() == "TLabel"]


def _pump_until(app, cond, secs=15):
    return pump_until(app, cond, secs)


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

    assert app.nb.select() == str(app.tab_setup)  # a new install: the checklist is what you see first (uinav)
    d = configio.defaults(str(tmp_path / "Auto"), str(tmp_path / "General"))
    monkeypatch.setattr(configio, "defaults", lambda *a, **k: d)
    monkeypatch.setattr(fragpipe, "detect_launcher", lambda: None)
    app.v("paths.inbox").set(str(tmp_path / "Auto" / "inbox"))  # not there yet (a dev PC may have C:/Fragpipe_Auto)
    app.auto_setup()
    assert (tmp_path / "Auto" / "inbox").is_dir() and (tmp_path / "Auto" / "config.yaml").is_file()
    assert _pump_until(app, lambda: any(i.key == "setup:config" and i.status == "ok"
                                        for i in getattr(app, "setup_items", [])))
    keys = {i.key: i.status for i in app.setup_items}
    assert keys["setup:folders"] == "ok" and keys["setup:config"] == "ok" and keys["setup:users"] == "todo"
    # Check everything (S3.1): one list from every source, each with a fix in words
    sources = {i.source for i in app.setup_items}
    assert {"setup", "fragpipe", "library", "health", "attention"} <= sources
    assert all(i.fix for i in app.setup_items if i.status in ("fail", "todo", "warn"))
    assert "problem" in app.setup_summary.cget("text") or "Next" in app.setup_summary.cget("text")
    assert app.today_setup.cget("text").startswith("Checks:") or not app.today_setup.cget("text")
    app._setup_action("open_tab:2")  # the checklist's old numbered actions still work
    assert app.nb.select() == str(app.tab_users)
    app._setup_action("open_tab:methods")
    assert app.nb.select() == str(app.tab_methods)
    assert app.setup_scroll.winfo_exists()  # the list scrolls on a laptop (S1.8)


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


def test_analysis_tab_shows_the_roles_and_saves_a_changed_one(app, tmp_path):
    """D65: each condition's role with its samples, a weak keyword marked to confirm, the comparisons in words,
    and a role chosen in the list saved as analysis.roles."""
    from ionomos.downstream import simulate
    from ionomos.manifest import load_overrides

    _lab_app(app, tmp_path)
    dest = tmp_path / "General" / "Chris" / "pulldown"
    simulate.competition_pg_matrix(dest / "fragpipe" / "report.pg_matrix.tsv",
                                   {"DMSO": 2, "Probe": 4, "Probe_pre": 4}, seed=3, n=150, competition="Probe_pre")
    ed = app.analysis.editor
    ed.load(dest, "DIA")
    assert _pump_until(app, lambda: len(ed.roles_tree.get_children()) == 3)
    rows = {i: list(ed.roles_tree.item(i)["values"]) for i in ed.roles_tree.get_children()}
    assert rows["DMSO"][:3] == ["DMSO", 2, "control"] and rows["Probe"][:3] == ["Probe", 4, "compound"]
    assert rows["Probe_pre"][2] == "competition of Probe  ?" and "ask" in ed.roles_tree.item("Probe_pre")["tags"]
    plan = ed.role_plan.cget("text")
    assert "Probe vs DMSO — enrichment" in plan and "DMSO has 2 samples, Probe 4, Probe_pre 4" in plan
    assert any(p.startswith("Probe_pre: read as Probe plus a competitor") for p in ed.problems())
    # Confirm keeps the guess and settles the question
    ed.roles_tree.selection_set("Probe_pre")
    ed._role_selected()
    assert ed.confirm_btn.instate(["!disabled"]) and "'pre'" in ed.role_ask.cget("text")
    ed.confirm_role()
    assert ed.roles_tree.item("Probe_pre")["values"][2] == "competition of Probe"
    assert not any("Probe_pre" in p for p in ed.problems())
    assert ed.choices()["roles"] == {"Probe_pre": "competition of Probe"}
    # ... or the list says what it is
    ed.roles_tree.selection_set("Probe_pre")
    ed._role_selected()
    ed.var("role").set("compound")
    ed._role_chosen()
    assert ed.roles_tree.item("Probe_pre")["values"][2] == "compound"
    assert "Probe_pre vs DMSO · 4 against 2 samples" in ed.role_plan.cget("text")
    assert ed.save_choices()
    assert load_overrides(dest).analysis["roles"] == {"Probe_pre": "compound"}
    # reopening shows it; automatic removes the entry
    ed.load(dest, "DIA")
    assert _pump_until(app, lambda: ed.roles_tree.exists("Probe_pre")
                       and ed.roles_tree.item("Probe_pre")["values"][2] == "compound")
    ed.roles_tree.selection_set("Probe_pre")
    ed._role_selected()
    assert ed.var("role").get() == "compound"
    ed.var("role").set(ed.role_cb.cget("values")[0])
    ed._role_chosen()
    assert ed.save_choices() and "roles" not in load_overrides(dest).analysis
    # a condition made the control by its role becomes the Control choice
    ed.roles_tree.selection_set("Probe")
    ed._role_selected()
    ed.var("role").set("control")
    ed._role_chosen()
    assert ed.var("control").get() == "Probe"


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
    assert z.parent == tmp_path and z.name.startswith("Ionomos-bundle-") and "-diagnose-" in z.name
    assert clip == [str(z)]
    note = zipfile.ZipFile(z).read("note.txt").decode()
    assert "stopped after lunch" in note and "detailed logging turned on" in note
    assert (tmp_path / "Auto" / "logs" / "DEBUG_UNTIL").is_file()
    # anonymised by default: the key file is next to the zip, and the message says it stays in the lab
    key = app.report_dialog.result.key_path
    assert key.parent == tmp_path and "DO-NOT-SHARE" in key.name and key.name not in zipfile.ZipFile(z).namelist()
    assert "stays in the lab" in shown[0][1] and str(tmp_path) in shown[0][1]


def test_report_window_lists_what_goes_in_and_follows_its_boxes(app, tmp_path, monkeypatch):
    import zipfile
    from tkinter import messagebox

    from ionomos import service

    _lab_app(app, tmp_path)
    shown = []
    monkeypatch.setattr(service, "desktop_dir", lambda: tmp_path)
    monkeypatch.setattr(service, "reveal", lambda p: None)
    monkeypatch.setattr(messagebox, "showinfo", lambda *a, **k: shown.append(a))
    monkeypatch.setattr(app.root, "clipboard_clear", lambda: None)
    monkeypatch.setattr(app.root, "clipboard_append", lambda s: None)
    app.report_problem("the analysis failed")
    d = app.report_dialog

    def listed():
        return d.preview.get("1.0", "end")

    assert d.text.get("1.0", "end").strip() == "the analysis failed"
    assert _pump_until(app, lambda: "Level: diagnose" in listed())
    assert "Estimated size" in listed() and "replaced by pseudonyms" in listed()
    d.validate.set(True)
    d.anonymise.set(False)
    d.refresh()
    assert _pump_until(app, lambda: "Level: validate" in listed() and "NOT anonymised" in listed())
    d.extras.set(True)  # DIA-NN's main report and the peptide / ion tables (D74)
    d.refresh()
    assert _pump_until(app, lambda: "Also: DIA-NN's main report" in listed())
    assert d.choice().options().include == ("diann-report", "peptides")
    d.save()
    assert _pump_until(app, lambda: shown)
    res = d.result
    assert "-validate-" in res.path.name and res.key_path is None and "NOT anonymised" in shown[0][1]
    assert "BUNDLE.json" in zipfile.ZipFile(res.path).namelist()
    assert not list(tmp_path.glob("*DO-NOT-SHARE*"))


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


def test_inbox_scan_displays_mixed_layout_rejection(app, tmp_path):
    """A drop with raws at the top level AND in raw/ raises IntakeError from _find_raws
    (PR #36): the Inbox scan must show that rejection on the drop instead of dying in
    the 2s tick loop (which surfaced as a Tk callback traceback on the lab PC)."""
    inbox = tmp_path / 'inbox'
    inbox.mkdir()
    folder = inbox / 'mixed'
    folder.mkdir()
    (folder / 'stray.raw').write_bytes(b'\0' * 64)
    (folder / 'raw').mkdir()
    (folder / 'raw' / 'EJQ_PK_EJQ-2-027_isoDTB_1uM_3h_1_1.raw').write_bytes(b'\0' * 64)
    app.v('paths.inbox').set(str(inbox))
    app.select_tab("inbox")
    app._inbox_tick()  # reads on a thread while the Inbox tab is shown and re-arms itself; must not raise
    assert _pump_until(app, lambda: app.inbox_tree.exists(str(folder)))
    children = app.inbox_tree.get_children(str(folder))
    assert len(children) == 1
    note = app.inbox_tree.item(children[0], 'text')
    assert 'top level' in note and 'raw/' in note


# ------------------------------------------------- settings and tools (D67) --


def test_figure_style_page_round_trips_and_keeps_other_keys(app, tmp_path, monkeypatch):
    from tkinter import messagebox

    import yaml

    from ionomos import forms

    _lab_app(app, tmp_path)
    p = tmp_path / "Auto" / "config.yaml"
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    raw["analysis"]["export"]["line_scale"] = 1.5  # a key the page has no field for
    p.write_text(yaml.safe_dump(raw), encoding="utf-8")
    app.reload()
    a = app.analysis
    assert a.style_size.get() == forms.size_label("slide169") and app.v("export.font_pt").get() == "14"
    app.v("export.size").set(forms.size_label("col1"))
    a._style_size_changed()
    assert app.v("export.font_pt").get() == "7"  # the text size follows the preset
    assert str(a.style_custom[0].cget("state")) == "disabled"
    app.v("export.palette").set("custom")
    a._style_enable()
    app.v("export.up").set("d55e00")
    app.bv("export.figure.volcano").set(True)
    assert a.check_style() and "7 pt Arial" in a.style_summary.cget("text")
    assert app.save()
    ex = load(p, check_paths=False).analysis["export"]
    assert ex["size"] == "col1" and ex["font_pt"] == 7 and ex["up"] == "#d55e00" and ex["figures"] == ["volcano"]
    assert ex["line_scale"] == 1.5
    errors = []
    monkeypatch.setattr(messagebox, "showerror", lambda *x, **k: errors.append(x))
    app.v("export.font_pt").set("99")
    assert not a.check_style() and "Text size" in a.style_summary.cget("text")
    assert app.save() is False and "Text size" in errors[0][1]
    assert load(p, check_paths=False).analysis["export"]["font_pt"] == 7  # the good file is untouched


def test_notifications_tab_masks_secrets_saves_and_sends_a_test(app, tmp_path, monkeypatch):
    from tkinter import messagebox

    from ionomos import notify

    _lab_app(app, tmp_path)
    secret = "https://hooks.slack.com/services/T0/B0/secret-key-123"
    t = app.notify_tab
    assert t.secret_entries and all(e.cget("show") == "•" for e in t.secret_entries)
    app.bv("notify.enabled").set(True)
    app.v("notify.slack.url").set(secret)
    app.bv("notify.on.held").set(False)
    assert app.save()
    cfg = load(tmp_path / "Auto" / "config.yaml", check_paths=False)
    assert cfg.notify["enabled"] and cfg.notify["slack"]["url"] == secret and cfg.notify["on"] == ["done", "failed"]
    app.bv("notify.show").set(True)
    t._show_secrets()
    assert all(e.cget("show") == "" for e in t.secret_entries)
    app.reload()
    assert all(e.cget("show") == "•" for e in t.secret_entries), "secrets are masked again after a reload"
    sent = []
    monkeypatch.setattr(notify, "send_test", lambda s, env=None: sent.append(s) or [
        notify.Result("slack", True, "HTTP 200")])
    assert t.send_test()
    assert _pump_until(app, lambda: "All sent" in t.result.cget("text"))
    shown = t.out.text.get("1.0", "end")
    assert "✓ slack    sent: HTTP 200" in shown and secret not in shown and sent[0]["slack"]["url"] == secret
    monkeypatch.setattr(messagebox, "showerror", lambda *a, **k: None)
    app.v("notify.slack.url").set("http://plain.example.org/x")  # not https: neither sent nor saved
    assert not t.send_test() and "https" in t.out.text.get("1.0", "end")
    assert app.save() is False


def test_check_accuracy_page_compares_and_benchmarks_off_the_tk_thread(app, tmp_path, monkeypatch):
    from tkinter import messagebox

    from ionomos import downstream
    from ionomos.downstream import benchmark, simulate
    from ionomos.downstream.tables import read_tsv, write_tsv

    _lab_app(app, tmp_path)
    dest = tmp_path / "General" / "EJQ" / "dia"
    runs = [(f"/x/{c}_{r}.raw", c) for c in ("DMSO", "Drug") for r in (1, 2, 3)]
    simulate.dia_pg_matrix(dest / "fragpipe" / "report.pg_matrix.tsv", runs, seed=2, n_proteins=300)
    downstream.analyze(dest, "DIA", analysis_cfg={"enrichment": False})
    _h, rows = read_tsv(dest / "results" / "Drug_vs_DMSO_differential.tsv")
    ref = write_tsv(tmp_path / "limma.tsv", ["ID", "logFC", "P.Value", "adj.P.Val"],
                    [{"ID": r["id"], "logFC": r["log2fc"], "P.Value": r["pvalue"], "adj.P.Val": r["qvalue"]}
                     for r in rows if r["log2fc"] != "NA"])
    opened = []
    monkeypatch.setattr(app, "_open", lambda x: opened.append(x))
    page = app.analysis.accuracy
    app.v("acc.analysis").set(str(dest))
    app.v("acc.reference").set(str(ref))
    assert page.run_compare()
    assert page.busy and str(page.compare_btn.cget("state")) == "disabled"
    assert _pump_until(app, lambda: opened, secs=60)
    assert opened[-1].endswith("compare.html") and "Compare: done" in page.verdict.cget("text")
    assert "agrees" in page.verdict.cget("text") and "verdict: agrees" in page.out.text.get("1.0", "end")
    assert not page.busy and str(page.open_btn.cget("state")) == "normal"
    tiny = {"designs": [(3, 3)], "effects": [2.0], "missing": ["typical"], "settings": ["none + median"],
            "seeds": 1, "proteins": 200}
    monkeypatch.setitem(benchmark.GRIDS, "quick", tiny)
    app.v("acc.kind").set("simulated")
    page._kind_changed()
    assert "ionomos_benchmark" in page.where.cget("text")
    assert page.run_benchmark()
    assert _pump_until(app, lambda: opened[-1].endswith("benchmark_simulated.html"), secs=60)
    assert (tmp_path / "Auto" / "logs" / "ionomos_benchmark" / "benchmark_simulated.html").is_file()
    errors = []
    monkeypatch.setattr(messagebox, "showerror", lambda *a, **k: errors.append(a))
    app.v("acc.kind").set("real")
    assert not page.run_benchmark() and "expected-ratios" in errors[-1][1]


def test_analysis_tab_checks_a_folder_searched_outside_ionomos(app, tmp_path, monkeypatch):
    """D80: a DIA search someone ran in FragPipe themselves, in a folder filed as TMT, dropped on the window: the
    check window says so and recommends DIA; Analyse now runs it with the conditions from FragPipe's manifest. An
    empty folder can't be analysed and the window says why."""
    import json

    from tests.test_fpfolder import dia_output

    _lab_app(app, tmp_path)
    exp = tmp_path / "General" / "Ana" / "HeLa_DIA"
    dia_output(exp / "fp_out")
    (exp / "ionomos.json").write_text(json.dumps({"plan": {"folder": {"method": "TMT"}}}), encoding="utf-8")
    opened = []
    monkeypatch.setattr(app, "_open", lambda p: opened.append(p))
    monkeypatch.setattr("ionomos.experiment_editor.messagebox.askyesno", lambda *a, **k: True)
    tab = app.analysis
    tab.dropped([str(exp)])
    win = tab.check_win
    assert app.nb.select() == str(app.tab_analysis)
    assert _pump_until(app, lambda: win.scan is not None)
    assert win.scan.method == "DIA" and win.kind.get().startswith("DIA")
    assert "Filed as TMT, but this is a DIA search" in win.issues.get("1.0", "end")
    assert win.run_btn.instate(["!disabled"]) and win.review_btn.instate(["!disabled"])
    win.analyse()
    assert not win.alive()
    ed = tab.editor
    assert _pump_until(app, lambda: opened, secs=30)
    assert opened[-1].endswith("report.html") and ed.target["method"] == "DIA"
    assert len(ed.tree.get_children()) == 6 and ed.conditions() == ["DMSO", "Drug"]

    empty = tmp_path / "empty"
    empty.mkdir()
    tab.dropped([str(empty)])
    win = tab.check_win
    assert _pump_until(app, lambda: win.scan is not None)
    assert win.scan.blocking and win.run_btn.instate(["disabled"]) and win.review_btn.instate(["disabled"])
    assert "No FragPipe results in this folder" in win.issues.get("1.0", "end")
    win.close()


def _drop_from_explorer(root, paths):
    """What Explorer sends on a drop: WM_DROPFILES with a DROPFILES block of wide-character names (Windows)."""
    import ctypes
    import struct
    from ctypes import wintypes

    k32, u32 = ctypes.WinDLL("kernel32"), ctypes.WinDLL("user32")
    k32.GlobalAlloc.argtypes, k32.GlobalAlloc.restype = [wintypes.UINT, ctypes.c_size_t], ctypes.c_void_p
    k32.GlobalLock.argtypes, k32.GlobalLock.restype = [ctypes.c_void_p], ctypes.c_void_p
    k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    u32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    u32.PostMessageW.restype = wintypes.BOOL
    blob = struct.pack("<IiiII", 20, 0, 0, 0, 1) + ("\0".join(str(p) for p in paths) + "\0\0").encode("utf-16-le")
    h = k32.GlobalAlloc(0x0042, len(blob))  # GMEM_MOVEABLE | GMEM_ZEROINIT; DragFinish frees it
    ptr = k32.GlobalLock(h)
    ctypes.memmove(ptr, blob, len(blob))
    k32.GlobalUnlock(h)
    assert u32.PostMessageW(root.winfo_id(), 0x0233, h, 0)


@pytest.mark.skipif(__import__("os").name != "nt", reason="drops from Explorer are Windows-only")
def test_a_folder_dropped_from_explorer_opens_the_check(app, tmp_path, monkeypatch):
    """D82: a real WM_DROPFILES on the window (0.18.0 hung here: it called Tk from inside the window procedure).
    The folder's check opens; a second drop works too; a path that isn't there is explained, not a crash."""
    import json

    from tests.test_fpfolder import dia_output

    _lab_app(app, tmp_path)
    assert getattr(app.root, "_ionomos_drop", None) is not None  # drops are on
    exp = tmp_path / "General" / "Ana" / "HeLa_DIA"
    dia_output(exp / "fp_out")
    (exp / "ionomos.json").write_text(json.dumps({"plan": {"folder": {"method": "TMT"}}}), encoding="utf-8")
    _drop_from_explorer(app.root, [exp, tmp_path / "second-is-ignored"])
    assert _pump_until(app, lambda: app.analysis.check_win is not None and app.analysis.check_win.scan is not None)
    win = app.analysis.check_win
    assert win.scan.picked == exp and win.scan.method == "DIA"
    assert app.nb.select() == str(app.tab_analysis)
    win.close()
    told = []
    monkeypatch.setattr("ionomos.analysis_tab.messagebox.showinfo", lambda *a, **k: told.append(a))
    _drop_from_explorer(app.root, [tmp_path / "gone"])
    assert _pump_until(app, lambda: told)
    assert "can't see" in told[0][1]
    empty = tmp_path / "empty"
    empty.mkdir()
    _drop_from_explorer(app.root, [empty])
    assert _pump_until(app, lambda: app.analysis.check_win is not None and app.analysis.check_win.alive()
                       and app.analysis.check_win.scan is not None and app.analysis.check_win.scan.picked == empty)
    app.analysis.check_win.close()


# ------------------------------------------------- robustness (docs/UI.md slice 2) and the untested windows (S4.3) --


def test_run_bg_shows_one_busy_indicator_and_refuses_a_second_start(app):
    """S2.3: one indicator in the bottom bar while work runs; the same work can't start twice; done() gets the
    result or the error on the Tk thread."""
    import threading

    gate, seen = threading.Event(), []
    assert app.run_bg("waiting for the gate", gate.wait, lambda r, e: seen.append((r, e)), key="gate")
    assert not app.run_bg("waiting for the gate", gate.wait, key="gate")  # refused while the first runs
    assert "wait" in app.status_lbl.cget("text") and app.busy("gate")
    app.root.update()
    assert app.busy_lbl.winfo_manager() == "pack" and "waiting for the gate" in app.busy_lbl.cget("text")
    gate.set()
    assert _pump_until(app, lambda: seen)
    assert seen == [(True, None)] and not app.busy("gate")
    assert _pump_until(app, lambda: not app._bg)  # the start-up checks may still be running
    app.root.update()
    assert app.busy_lbl.winfo_manager() == ""  # hidden again when nothing works

    def boom():
        raise OSError("disk gone")

    assert app.run_bg("exploding", boom, lambda r, e: seen.append((r, type(e).__name__)))
    assert _pump_until(app, lambda: len(seen) == 2)
    assert seen[1] == (None, "OSError")


def test_slow_work_never_blocks_the_tk_thread(app, tmp_path, monkeypatch):
    """S2.2: the FASTA copy of Import workflow…, the QC page, Reset testbed and the status tick run on threads: each
    call returns at once while the slow part is still going, and the result arrives through App.post."""
    import threading
    import time as _t
    from tkinter import filedialog, messagebox

    from ionomos import fragpipe, qctrend, testbed

    _lab_app(app, tmp_path)
    slept = threading.Event()

    def slow_import(src, key, wf_dir, fa_dir):
        slept.wait(5)
        return {"workflow": f"{key}.workflow", "fasta": "", "notes": ["(slow copy)"]}

    monkeypatch.setattr(fragpipe, "import_workflow", slow_import)
    monkeypatch.setattr(filedialog, "askopenfilename", lambda **k: str(tmp_path / "x.workflow"))
    monkeypatch.setattr(messagebox, "showinfo", lambda *a, **k: None)
    app.mtree.selection_set("isoDTB")
    app._show_method()
    t0 = _t.monotonic()
    app.import_workflow()
    assert _t.monotonic() - t0 < 1.0 and app.busy("import_workflow")  # back before the copy ended
    slept.set()
    assert _pump_until(app, lambda: not app.busy("import_workflow"))
    # the QC page
    monkeypatch.setattr(qctrend, "page_for", lambda cfg: (_t.sleep(0.5), tmp_path / "qc.html")[1])
    opened = []
    monkeypatch.setattr(app, "_open", lambda p: opened.append(p))
    t0 = _t.monotonic()
    app.open_qc_trend()
    assert _t.monotonic() - t0 < 0.4
    assert _pump_until(app, lambda: opened) and opened[-1].endswith("qc.html")
    # Reset testbed
    (tmp_path / "bed" / "Fragpipe_Auto").mkdir(parents=True)
    app.v("tb.dir").set(str(tmp_path / "bed"))
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: True)
    monkeypatch.setattr(testbed, "reset", lambda d: _t.sleep(0.5))
    t0 = _t.monotonic()
    app.tb_reset()
    assert _t.monotonic() - t0 < 0.4
    assert _pump_until(app, lambda: "testbed reset" in app.out.text.get("1.0", "end"))
    # the 2 s status tick: the ledger and lock file are read on a thread; the strip on every tab gets the line
    app._refresh_status_once()
    assert _pump_until(app, lambda: "Watcher" in app.strip_lbl.cget("text") and "FragPipe" in app.strip_lbl.cget("text"))
    assert app.strip_lbl.cget("text").startswith(app.run_status.cget("text"))


def test_inbox_tab_says_where_it_looks_and_scans_off_the_tk_thread(app, tmp_path):
    from tests.conftest import make_drop

    inbox = tmp_path / "inbox"
    app.v("paths.inbox").set(str(inbox))
    app._refresh_inbox()
    assert "not there" in app.inbox_lbl.cget("text")
    inbox.mkdir()
    app._refresh_inbox()
    assert "empty" in app.inbox_lbl.cget("text")
    make_drop(inbox, "exp", ["a.raw"])
    app.select_tab("inbox")
    app._inbox_tick()  # reads on a thread; the tree fills through App.post
    assert _pump_until(app, lambda: app.inbox_tree.exists(str(inbox / "exp")))
    assert "1 item waiting" in app.inbox_lbl.cget("text")


def test_save_shows_what_changes_and_cancel_writes_nothing(app, tmp_path, monkeypatch):
    """S2.5: the Save button first lists the changes in words; Cancel leaves config.yaml alone; nothing to change
    writes nothing."""
    _lab_app(app, tmp_path)
    p = tmp_path / "Auto" / "config.yaml"
    before = p.read_text(encoding="utf-8")
    assert app.save(confirm=True) and p.read_text(encoding="utf-8") == before
    assert "nothing to save" in app.status_lbl.cget("text")
    app.v("fragpipe.threads").set("12")
    app.v("watcher.stable_seconds").set("45")
    asked = []
    monkeypatch.setattr(app, "_confirm_changes", lambda changes: asked.append(changes) or False)
    assert app.save(confirm=True) is False
    assert p.read_text(encoding="utf-8") == before and len(asked) == 1
    assert any("Threads" in c and "28 → 12" in c for c in asked[0]) and any("45" in c for c in asked[0])
    monkeypatch.setattr(app, "_confirm_changes", lambda changes: True)
    assert app.save(confirm=True)
    assert load(p, check_paths=False).threads == 12 and "2 settings changed" in app.status_lbl.cget("text")
    # the window itself: closing it is Cancel
    monkeypatch.undo()
    app.v("fragpipe.threads").set("16")
    app.root.after(300, lambda: app.confirm_win.destroy() if app.confirm_win.winfo_exists() else None)
    assert app.save(confirm=True) is False and load(p, check_paths=False).threads == 12
    assert "not saved" in app.status_lbl.cget("text")


def test_unsaved_changes_are_not_lost_silently(app, tmp_path, monkeypatch):
    """S2.6: a dot in the title; Quit, Reload and Change… ask Save / Discard / Cancel; nothing asks when clean."""
    from tkinter import messagebox

    _lab_app(app, tmp_path)
    assert not app.dirty()
    app._update_title()
    assert "•" not in app.root.title()
    app.v("fragpipe.threads").set("9")
    assert app.dirty()
    app._update_title()
    assert app.root.title().endswith("•")
    answers = []
    monkeypatch.setattr(messagebox, "askyesnocancel", lambda *a, **k: answers.pop(0))
    answers.append(None)  # Cancel: stay
    assert app._guard_unsaved("quitting") is False
    answers.append(False)  # Discard
    assert app._guard_unsaved("quitting") is True
    answers.append(True)  # Save
    assert app._guard_unsaved("quitting") is True
    assert load(tmp_path / "Auto" / "config.yaml", check_paths=False).threads == 9 and not app.dirty()
    app.v("fragpipe.threads").set("10")
    answers.append(None)
    app.reload()  # Cancel: the edit stays
    assert app.v("fragpipe.threads").get() == "10"
    answers.append(False)
    app.reload()  # Discard: back to the file
    assert app.v("fragpipe.threads").get() == "9" and not app.dirty()
    destroyed = []
    monkeypatch.setattr(app.root, "destroy", lambda: destroyed.append(1))
    app.on_close()  # clean: closes without asking
    assert destroyed and not answers


def test_change_config_switches_the_file(app, tmp_path):
    _lab_app(app, tmp_path)
    other = tmp_path / "Other" / "config.yaml"
    app.change_config(other)
    assert app.config_path == other and app.first_run and "new file" in app.status_lbl.cget("text")
    assert app.v("config_path").get() == str(other)
    app.v("quick.root").set(str(tmp_path / "Other"))
    app.apply_quick()
    app.create_all()
    assert app.save() and other.is_file()


def test_run_and_test_is_split_and_de_duplicated(app):
    """S1.9: a Watcher page and a Testing page; the job-number prompts are gone; one Stop watcher."""
    pages = [app.run_nb.tab(t, "text") for t in app.run_nb.tabs()]
    assert pages == ["Watcher", "Testing"]
    assert not hasattr(app, "retry_job") and not hasattr(app, "_pick_job")
    texts = [w.cget("text") for w in _widgets(app.tab_run) if w.winfo_class() == "TButton"]
    assert texts.count("Stop watcher") == 1 and "Save & Check" not in texts
    assert "Create testbed" in texts and "Start watcher" in texts


def test_test_names_window_reads_a_name(app, tmp_path):
    _lab_app(app, tmp_path)
    win = app.test_names()
    assert win.winfo_exists() and win.title() == "Test your names"
    app.names_in.delete("1.0", "end")
    app.names_in.insert("1.0", "20260902_EJQ_isoDTB_EJQ-2-027\nEJQ_PK_EJQ-2-027_isoDTB_1uM_3h_1_1.raw\n")
    app.names_run()
    out = app.names_out.get("1.0", "end")
    assert "isoDTB" in out and ("EJQ" in out or "user" in out)
    win.destroy()


def test_startup_task_buttons_with_the_service_stubbed(app, tmp_path, monkeypatch):
    from tkinter import messagebox

    from ionomos import service

    calls = []
    monkeypatch.setattr(service, "install_task", lambda cfg: calls.append(("install", cfg)) or (True, "installed ok"))
    monkeypatch.setattr(service, "remove_task", lambda: calls.append(("remove",)) or (True, "removed ok"))
    monkeypatch.setattr(service, "create_desktop_shortcut", lambda: calls.append(("shortcut",)) or (True, "x.lnk"))
    shown = []
    monkeypatch.setattr(messagebox, "showinfo", lambda *a, **k: shown.append(a))
    monkeypatch.setattr(messagebox, "showerror", lambda *a, **k: shown.append(("error", *a)))
    app.install_task()  # no saved config yet: says what to do, installs nothing
    assert shown and shown[-1][0] == "error" and "Save" in shown[-1][2] and not calls
    _lab_app(app, tmp_path)
    app.install_task()
    app.remove_task()
    app.shortcut()
    assert [c[0] for c in calls] == ["install", "remove", "shortcut"]
    assert "installed ok" in app.out.text.get("1.0", "end") and "removed ok" in app.out.text.get("1.0", "end")


def test_colour_picker_sets_the_field(app, monkeypatch):
    from tkinter import colorchooser

    monkeypatch.setattr(colorchooser, "askcolor", lambda **k: ((213, 94, 0), "#D55E00"))
    app.analysis._pick_colour("up")
    assert app.v("export.up").get() == "#d55e00"
    monkeypatch.setattr(colorchooser, "askcolor", lambda **k: (None, None))  # cancelled: unchanged
    app.analysis._pick_colour("up")
    assert app.v("export.up").get() == "#d55e00"


def test_number_fields_are_checked_as_you_type(app):
    """S3.3: a red dot and the problem in the hint while a number field holds what the loader refuses."""
    dot, hint = app.num_fields["watcher.poll_seconds"]
    app.v("watcher.poll_seconds").set("ten")
    assert str(dot.cget("foreground")) == "#c62828" and "number" in hint.cget("text")
    app.v("watcher.poll_seconds").set("10")
    assert str(dot.cget("foreground")) == "#2e7d32" and "scanned" in hint.cget("text")
    dot, hint = app.num_fields["fragpipe.threads"]
    app.v("fragpipe.threads").set("-1")
    assert str(dot.cget("foreground")) == "#c62828"


def test_quick_check_on_start_shows_a_problem_on_today(app, tmp_path):
    """S3.2: the checks run when the app opens; Today's line says how many problems."""
    _lab_app(app, tmp_path)
    app.v("paths.inbox").set(str(tmp_path / "nowhere" / "inbox"))  # a broken path
    app.refresh_setup()
    assert _pump_until(app, lambda: app._last_setup_problems > 0)
    app.select_tab("today")
    app.refresh_today()
    assert _pump_until(app, lambda: app.today_setup.cget("text").startswith("Checks:"))
    assert str(app.today_setup.cget("foreground")) == "#c62828"

def test_app_opens_on_a_config_that_does_not_parse(tmp_path, monkeypatch):
    """#136: Ionomos.exe died at start on a YAML slip in config.yaml. Now it opens on the defaults, shows the
    error and offers Restore a previous config when there are backups; the file is left as it is."""
    import tkinter as tk
    from tkinter import messagebox

    from ionomos import configio
    from ionomos.app import App

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    p = tmp_path / "Auto" / "config.yaml"
    p.parent.mkdir()
    p.write_text("paths:\n  inbox: [unclosed\n", encoding="utf-8")
    good = configio.defaults(str(tmp_path / "Auto"), str(tmp_path / "General"))
    (p.parent / configio.BACKUP_DIR).mkdir()
    (p.parent / configio.BACKUP_DIR / "config-20260101-000000.yaml").write_text(configio.dump_config(good), encoding="utf-8")
    asked, restored = [], []
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: asked.append(a[1]) or False)
    monkeypatch.setattr(messagebox, "showerror", lambda *a, **k: pytest.fail(f"dialog: {a}"))
    root = make_tk_root()
    root.withdraw()
    try:
        a = App(root, p)
        monkeypatch.setattr(a, "restore_config", lambda: restored.append(True))
        assert a.config_error and "line 2, column 10" in a.config_error
        a._say_config_broken()
        assert asked and "Restore a previous config" in asked[0] and "config-20260101-000000.yaml" in asked[0]
        assert "line 2, column 10" in a.status_lbl.cget("text")
        assert p.read_text(encoding="utf-8") == "paths:\n  inbox: [unclosed\n"
    finally:
        tk.Tk.destroy(root)
