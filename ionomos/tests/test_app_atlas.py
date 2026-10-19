"""The cysteine atlas hand-off in the app (D125, docs/ATLAS.md "In the app"): the Folders tab's Cysteine atlas group,
Send to atlas and the atlas column on the Jobs tab, the Setup checklist's line, the attention item's title and
button, and the help they open.

The checklist line (setupcheck.atlas_item) is tested without Tk; the windows run hidden (tests/tkharness.py, D95)
where tests/conftest.gui_tests() allows (CI; never IONOMOS_GUI_TESTS on a desk). Every path is under tmp_path, and
atlas.dropbox_suggestion is replaced in every test: nothing here reads this PC's own Dropbox, C:/Fragpipe_Auto or
C:/Fragpipe_General.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from tkinter import ttk

import pytest
import yaml

from ionomos import atlas, attention, names, setupcheck, uinav
from ionomos.config import load
from ionomos.ledger import Job, Ledger


@pytest.fixture(autouse=True)
def _no_real_dropbox(monkeypatch):
    """Never this PC's own Dropbox: the Folders tab and the checklist ask for it."""
    monkeypatch.setattr(atlas, "dropbox_suggestion", lambda *a, **k: "")


# ------------------------------------------------------------------- the checklist line, no Tk --


def _dropbox(tmp_path: Path, spaces: bool = True) -> Path:
    """<tmp>/TNRG Dropbox/Nicholas Foster Data (there) / fragpipe-runs (not yet): a path with spaces, as the lab's.
    spaces=False for a folder the app saves: a test's tmp folder is long, and a long value with spaces does not
    survive configio.dump_config yet (test_a_long_dropbox_path_with_spaces_survives_a_save)."""
    parent = tmp_path / ("TNRG Dropbox" if spaces else "TNRG_Dropbox") / \
        ("Nicholas Foster Data" if spaces else "Nicholas_Foster_Data")
    parent.mkdir(parents=True)
    return parent / "fragpipe-runs"


@pytest.mark.xfail(strict=True, reason="configio._y dumps with PyYAML's width of 80, so a long value with a space past "
                                       "column 80 is folded over two lines and config.yaml no longer parses (the "
                                       "app's Save refuses). Fix: width=float('inf') in configio._y, then drop this "
                                       "mark (strict: it fails once fixed)")
def test_a_long_dropbox_path_with_spaces_survives_a_save(tmp_path):
    from ionomos import configio

    d = configio.defaults()
    long = "C:\\Users\\" + "x" * 70 + "\\TNRG Dropbox\\Daniel Nomura\\Nicholas Foster Data\\fragpipe-runs"
    d["atlas"].update(enabled=True, handoff_dir=long)  # a string only: nothing is made there
    p = tmp_path / "config.yaml"
    p.write_text(configio.dump_config(d), encoding="utf-8")
    assert configio.read_config(p)["atlas"]["handoff_dir"] == long


def test_no_line_while_the_hand_off_is_off():
    assert setupcheck.atlas_item(None) is None
    assert setupcheck.atlas_item({}) is None
    assert setupcheck.atlas_item({"enabled": False, "handoff_dir": "C:/somewhere"}) is None


def test_on_without_a_folder_is_a_step_to_take(monkeypatch):
    it = setupcheck.atlas_item({"enabled": True})
    assert (it.key, it.title, it.status, it.action) == ("atlas", "Cysteine atlas hand-off", "todo", "atlas_folder")
    assert "handoff_dir is not set" in it.detail and it.fix.startswith("Pick the lab Dropbox folder on the Folders tab")
    monkeypatch.setattr(atlas, "dropbox_suggestion", lambda *a, **k: r"C:\Users\me\TNRG Dropbox\fragpipe-runs")
    assert "(found: C:\\Users\\me\\TNRG Dropbox\\fragpipe-runs)" in setupcheck.atlas_item({"enabled": True}).fix


@pytest.mark.parametrize("there", ["folder", "parent"])
def test_a_folder_that_is_there_or_made_by_the_first_hand_off_is_ok(tmp_path, there):
    dest = _dropbox(tmp_path)
    if there == "folder":
        dest.mkdir()
    it = setupcheck.atlas_item({"enabled": True, "handoff_dir": str(dest)})
    assert it.status == "ok" and it.action is None, it
    assert "on for isoDTB" in it.detail and str(dest) in it.detail
    assert ("is there" in it.detail) if there == "folder" else ("the first hand-off makes it" in it.detail)
    if there == "folder":
        assert list(dest.iterdir()) == []  # the checklist writes nothing (no test file: write_probe=False)


def test_an_unreachable_folder_is_a_warning_that_says_what_to_do(tmp_path):
    dest = tmp_path / "Offline Dropbox" / "Nicholas Foster Data" / "fragpipe-runs"
    it = setupcheck.atlas_item({"enabled": True, "handoff_dir": str(dest)})
    assert it.status == "warn" and it.action == "atlas_folder"  # the hand-off retries by itself; Ionomos works
    assert it.fix.startswith("Is Dropbox running") and "Folders tab, under Cysteine atlas" in it.fix


def test_a_malformed_atlas_lookup_is_a_warning_for_the_atlas_keeper(tmp_path):
    dest = _dropbox(tmp_path)
    (dest / names.ATLAS_RETURN_DIR).mkdir(parents=True)
    (dest / names.ATLAS_RETURN_DIR / names.ATLAS_LOOKUP_TSV).write_text("x", encoding="utf-8")
    (dest / names.ATLAS_RETURN_DIR / names.ATLAS_LOOKUP_JSON).write_text("{", encoding="utf-8")
    it = setupcheck.atlas_item({"enabled": True, "handoff_dir": str(dest)})
    assert it.status == "warn" and it.action is None and "malformed" in it.detail
    assert it.fix.startswith("Tell the person who keeps the atlas")


def test_settings_the_loader_refuses_fail_the_line():
    it = setupcheck.atlas_item({"enabled": True, "handoff_dir": "fragpipe-runs"})
    assert it.status == "fail" and it.action == "atlas_folder" and "atlas.handoff_dir must be a full folder path" \
        in it.detail and "Folders tab" in it.fix


def test_a_check_that_breaks_is_one_warning(monkeypatch):
    def boom(*a, **k):
        raise OSError("the network is gone")

    monkeypatch.setattr(atlas, "check", boom)
    it = setupcheck.atlas_item({"enabled": True, "handoff_dir": "C:/x"})
    assert it.status == "warn" and "the network is gone" in it.detail and "ionomos atlas check" in it.fix


def _checklist(lab, monkeypatch, **atlas_block) -> dict:
    from ionomos import service

    monkeypatch.setattr(service, "task_status", lambda: "installed")  # no schtasks from a test
    d = dict(lab["cfg_dict"], atlas=atlas_block)
    lab["cfg_path"].write_text(yaml.safe_dump(d), encoding="utf-8")
    return {i.key: i for i in setupcheck.run(lab["cfg_path"], probe_watcher=False)}


def test_the_checklist_has_the_line_only_while_the_hand_off_is_on(lab, monkeypatch):
    assert "atlas" not in _checklist(lab, monkeypatch)
    items = _checklist(lab, monkeypatch, enabled=True, handoff_dir=str(_dropbox(lab["root"])))
    assert items["atlas"].status == "ok"
    keys = list(items)
    assert keys.index("analysis") < keys.index("atlas")  # with the other optional features


def test_the_checklist_uses_the_windows_unsaved_settings(lab, monkeypatch):
    from ionomos import configio

    _checklist(lab, monkeypatch)  # saved: off
    data = configio.read_config(lab["cfg_path"])
    data["atlas"]["enabled"] = True  # ticked in the window, not saved yet
    items = {i.key: i for i in setupcheck.run(lab["cfg_path"], data=data, probe_watcher=False)}
    assert items["atlas"].status == "todo"


# ---------------------------------------------------------------- names, help and the attention kind --


def test_the_attention_kind_has_a_title_and_help_and_no_pop_up():
    from ionomos import help as helpdoc
    from ionomos import popups

    assert popups.KIND_TITLE[atlas.ATTENTION_KIND] == "A search wasn't handed to the atlas"
    assert atlas.ATTENTION_KIND in attention.KINDS and atlas.ATTENTION_KIND not in attention.POPUP_KINDS
    assert helpdoc.topic_for_item({"kind": atlas.ATTENTION_KIND}) == "attention.atlas_handoff_failed"
    assert {"faq.atlas", "attention.atlas_handoff_failed"} <= set(helpdoc.entries())


def test_the_labels_the_help_names_are_the_apps():
    """faq.atlas and the attention entry tell people which buttons to press: the app has each of them."""
    from ionomos import app, popups
    from ionomos import help as helpdoc

    src = Path(app.__file__).read_text(encoding="utf-8")
    text = helpdoc.entries()["faq.atlas"].body + helpdoc.entries()["attention.atlas_handoff_failed"].body
    for label in ("Cysteine atlas", "Browse…", "Use"):
        assert f"**{label}**" in text, label
        assert f'text="{label}"' in src, label
    assert "**Send to atlas**" in text
    assert '("Send to atlas", "atlas")' in src  # the Jobs tab's row of buttons
    assert 'text="Send to atlas"' in Path(popups.__file__).read_text(encoding="utf-8")  # the item's window
    for tab in ("folders", "jobs"):
        assert f"**{uinav.tab_name(tab)}**" in text, tab
    assert 'text="Hand finished isoDTB searches to the cysteine atlas"' in src


# ---------------------------------------------------------------------------------- the windows, hidden --

WORKFLOW = "# FragPipe (24.0) runtime properties\nmsfragger.table.var-mods=561.3387,C,true,1\n"


def _long(p) -> str:
    """A path the file functions open past Windows' 260 characters, as atlas._fs does (the destination repeats the
    test's long tmp folder)."""
    s = os.path.abspath(str(p))
    return "\\\\?\\" + s if os.name == "nt" and not s.startswith("\\\\") else s


def _lab(app, tmp_path, on: bool = True) -> Path:
    """The app's standard layout under tmp_path, saved; the hand-off on into <tmp>/TNRG_Dropbox/…/fragpipe-runs."""
    app.v("quick.root").set(str(tmp_path / "Auto"))
    app.v("quick.users").set(str(tmp_path / "General"))
    app.apply_quick()
    app.create_all()
    dest = _dropbox(tmp_path, spaces=False)
    if on:
        app.bv("atlas.enabled").set(True)
        app.v("atlas.handoff_dir").set(str(dest))
    assert app.save()
    return dest


def _job(tmp_path, name: str, method: str = "isoDTB", status: str = "done") -> tuple[int, Path, Path]:
    """A job in the app's ledger with a FragPipe output folder and its ionomos.json."""
    exp = tmp_path / "General" / "EJQ" / name
    run = exp / "fragpipe"
    for rel, data in {"fragpipe.workflow": WORKFLOW.encode(), "fragpipe.job": b"job",
                      "fragpipe-files.fp-manifest": f"{exp / 'raw' / 'S_1.raw'}\tEXP\t1\tDDA\n".encode(),
                      "exp_1/psm.tsv": b"Peptide\n" * 20}.items():
        p = run.joinpath(*rel.split("/"))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    led = Ledger(tmp_path / "Auto" / "ionomos.db")
    try:
        jid = led.insert(Job(inbox_name=name, user="EJQ", method=method, dest_dir=str(exp)))
        led.set_status(jid, status)
    finally:
        led.close()
    (exp / names.STATUS_FILE).write_text(json.dumps({"status": status, "run": {
        "workdir": str(run), "finished_at": "2026-10-19T14:02:11-07:00", "exit_code": 0}}), encoding="utf-8")
    return jid, exp, run


def _buttons(widget) -> dict[str, ttk.Button]:
    """Every ttk.Button under a widget, by its text."""
    out = {}
    for w in widget.winfo_children():
        if isinstance(w, ttk.Button):
            out[w.cget("text")] = w
        out.update(_buttons(w))
    return out


def _pump(app, cond, secs: float = 20) -> bool:
    from tests.tkharness import pump_until

    return pump_until(app, cond, secs=secs)


def _shown(app) -> list[str]:
    """The Jobs list's columns on screen."""
    return [str(c) for c in app.jtree.cget("displaycolumns")]


def test_the_folders_tab_saves_the_switch_and_the_folder(hidden_app, no_dialogs, tk_errors, tmp_path):
    app = hidden_app
    _lab(app, tmp_path, on=False)
    dest = _dropbox(tmp_path / "other", spaces=False)
    assert not app.bv("atlas.enabled").get() and app.v("atlas.handoff_dir").get() == ""
    app.bv("atlas.enabled").set(True)
    no_dialogs.expect("askdirectory", dest.as_posix())  # Tk's picker answers with /
    app.browse_atlas()
    assert app.v("atlas.handoff_dir").get() == str(dest)  # this PC's own separators
    assert any(c.startswith("Atlas") for c in app.changes()), app.changes()
    assert app.dirty()
    assert app.save()
    cfg = load(tmp_path / "Auto" / "config.yaml")  # the watcher's loader takes what the app wrote
    assert cfg.atlas["enabled"] is True and cfg.atlas["handoff_dir"] == str(dest)
    assert cfg.atlas["methods"] == ["isoDTB"] and not app.dirty()
    assert not no_dialogs.unexpected(), no_dialogs.report()
    app.reload(ask=False)  # read back into the window
    assert app.bv("atlas.enabled").get() and app.v("atlas.handoff_dir").get() == str(dest)


def test_a_folder_the_loader_refuses_is_not_saved(hidden_app, no_dialogs, tk_errors, tmp_path):
    app = hidden_app
    _lab(app, tmp_path, on=False)
    before = (tmp_path / "Auto" / "config.yaml").read_text(encoding="utf-8")
    app.bv("atlas.enabled").set(True)
    app.v("atlas.handoff_dir").set("fragpipe-runs")  # not a full path
    assert app.save() is False
    (call,) = [c for c in no_dialogs.calls if c.name == "showerror"]
    assert "handoff_dir" in call.detail and "Save again" in call.detail
    assert (tmp_path / "Auto" / "config.yaml").read_text(encoding="utf-8") == before


def test_the_dropbox_folder_found_is_offered_never_filled_in(hidden_app, no_dialogs, tk_errors, tmp_path,
                                                                 monkeypatch):
    app = hidden_app
    _lab(app, tmp_path, on=False)
    found = str(_dropbox(tmp_path / "Dropbox"))
    asked = []
    monkeypatch.setattr(atlas, "dropbox_suggestion", lambda *a, **k: asked.append(1) or found)
    assert not app.atlas_found.winfo_manager()  # nothing before it was looked for
    app.select_tab("folders")  # first shown: looked for off the Tk thread
    assert _pump(app, lambda: app._atlas_suggestion is not None)
    assert app.atlas_found.winfo_manager() == "grid" and app.atlas_found.cget("text") == \
        f"Dropbox folder found: {found}"
    assert app.v("atlas.handoff_dir").get() == ""  # offered, not filled in
    app.atlas_use.invoke()
    assert app.v("atlas.handoff_dir").get() == found
    assert not app.atlas_found.winfo_manager() and not app.atlas_use.winfo_manager()  # the field has it now
    app.select_tab("jobs")
    app.select_tab("folders")
    app.root.update()
    assert asked == [1]  # once per session
    app.v("atlas.handoff_dir").set("C:/elsewhere")
    assert app.atlas_found.winfo_manager() == "grid"
    assert not no_dialogs.unexpected(), no_dialogs.report()


def test_send_to_atlas_hands_a_finished_search_over_and_the_column_says_so(hidden_app, no_dialogs, tk_errors,
                                                                            tmp_path):
    app = hidden_app
    root = _lab(app, tmp_path)
    jid, exp, run = _job(tmp_path, "20261019_isoDTB_EJQ-2-027")
    tmt, _, _ = _job(tmp_path, "20261019_TMT_EJQ", method="TMT")
    app.select_tab("jobs")
    app.refresh_jobs()
    assert _pump(app, lambda: app.jtree.set(str(jid), "atlas") == "not handed off")
    assert "atlas" in _shown(app) and app.jtree.set(str(tmt), "atlas") == ""
    app.jtree.selection_set(str(jid))
    app.show_job()
    assert "atlas hand-off: not handed off" in app.jdetail.text.get("1.0", "end")
    app.job_action("atlas")
    assert app.busy(f"atlas:{jid}") and str(app.job_btns["atlas"].cget("state")) == "disabled"
    assert _pump(app, lambda: not app.busy(f"atlas:{jid}"))
    st = atlas.state(exp)
    assert st["state"] == atlas.SENT and st["trigger"] == "app", st
    assert Path(st["record"]).parent == root / names.ATLAS_LOG_DIR and Path(st["record"]).is_file()
    assert os.path.isfile(_long(atlas.destination(run, root) / "exp_1" / "psm.tsv"))
    assert "handed to the atlas" in app.status_lbl.cget("text") and str(app.job_btns["atlas"].cget("state")) != \
        "disabled"
    assert _pump(app, lambda: app.jtree.set(str(jid), "atlas").startswith("sent "))
    assert not no_dialogs.unexpected(), no_dialogs.report()  # success says so without a box


def test_send_to_atlas_explains_a_job_that_isnt_done_or_a_hand_off_that_is_off(hidden_app, no_dialogs, tk_errors,
                                                                                tmp_path):
    app = hidden_app
    _lab(app, tmp_path, on=False)
    failed, _, _ = _job(tmp_path, "20261019_isoDTB_failed", status="failed")
    jid, exp, _ = _job(tmp_path, "20261019_isoDTB_EJQ-2-027")
    app.refresh_jobs()
    app.jtree.selection_set(str(failed))
    app.job_action("atlas")
    assert no_dialogs.calls[-1].name == "showinfo" and "only a finished (done) search" in no_dialogs.calls[-1].detail
    assert not app.busy(f"atlas:{failed}")
    _pump(app, lambda: False, secs=0.5)
    assert "atlas" not in _shown(app)  # off, and no job was ever handed off
    # off: nothing is sent, and the box offers the Folders tab
    app.jtree.selection_set(str(jid))
    no_dialogs.expect("askyesno", True)
    app.job_action("atlas")
    assert _pump(app, lambda: not app.busy(f"atlas:{jid}"))
    call = no_dialogs.calls[-1]
    assert call.name == "askyesno" and "is off" in call.detail and "Cysteine atlas" in call.detail
    assert app._current_tab() == "folders" and atlas.state(exp) == {}
    # ticked in the window but not saved: the box says Save first
    app.bv("atlas.enabled").set(True)
    app.v("atlas.handoff_dir").set(str(_dropbox(tmp_path / "unsaved")))
    no_dialogs.expect("showinfo", None)
    app.send_to_atlas(job=app._jobs_cache[jid])
    assert _pump(app, lambda: not app.busy(f"atlas:{jid}"))
    assert "not saved yet" in no_dialogs.calls[-1].detail and atlas.state(exp) == {}


def test_a_conflict_names_the_files_and_the_items_window_sends_again(hidden_app, no_dialogs, tk_errors, tmp_path):
    app = hidden_app
    root = _lab(app, tmp_path)
    jid, exp, run = _job(tmp_path, "20261019_isoDTB_EJQ-2-027")
    theirs = atlas.destination(run, root) / "exp_1" / "psm.tsv"
    os.makedirs(_long(theirs.parent))
    with open(_long(theirs), "wb") as fh:
        fh.write(b"someone else's copy")
    app.refresh_jobs()
    app.jtree.selection_set(str(jid))
    no_dialogs.expect("askyesno", False)  # "Open the help for this?" (App.fail with the attention entry)
    app.job_action("atlas")
    assert _pump(app, lambda: not app.busy(f"atlas:{jid}"))
    call = no_dialogs.calls[-1]
    assert call.name == "askyesno" and str(theirs) in call.detail and "nothing was copied" in call.detail
    assert "Ask the person who keeps the atlas" in call.detail and "press Send to atlas again" in call.detail
    assert atlas.state(exp)["state"] == atlas.CONFLICT
    assert "differs: " in app.jdetail.text.get("1.0", "end")
    # the attention item: in the list, never a window by itself; its window has Send to atlas
    log_dir = tmp_path / "Auto" / "logs"
    (item,) = [i for i in attention.items(log_dir) if i.kind == atlas.ATTENTION_KIND]
    assert item not in app.popups.due()
    w = app.popups.show(item)
    assert w.win.title() == "Ionomos — A search wasn't handed to the atlas"
    with open(_long(theirs), "wb") as fh:  # the keeper moved theirs aside … (here: made it this run's)
        fh.write((run / "exp_1" / "psm.tsv").read_bytes())
    _buttons(w.win)["Send to atlas"].invoke()
    assert "handing job" in w.msg.cget("text")
    assert _pump(app, lambda: not app.busy(f"atlas:{jid}"))
    assert w.msg.cget("text").startswith("handed to the atlas") and atlas.state(exp)["state"] == atlas.SENT
    assert not [i for i in attention.items(log_dir) if i.kind == atlas.ATTENTION_KIND]  # closed by the send
    w.close()
    assert not no_dialogs.unexpected(), no_dialogs.report()


def test_the_checklist_line_and_its_fix_button(hidden_app, no_dialogs, tk_errors, tmp_path, monkeypatch):
    from ionomos import service

    monkeypatch.setattr(service, "task_status", lambda: "installed")
    app = hidden_app
    _lab(app, tmp_path, on=False)
    app.bv("atlas.enabled").set(True)  # on, no folder (not saved: the checklist shows the window's settings)
    _pump(app, lambda: not app.busy("checks"))
    app.refresh_setup()
    assert _pump(app, lambda: not app.busy("checks") and any(r.key == "setup:atlas"
                                                              for r in getattr(app, "setup_items", [])), 60)
    (row,) = [r for r in app.setup_items if r.key == "setup:atlas"]
    assert row.status == "todo" and row.action == "atlas_folder" and row.title == "Cysteine atlas hand-off"
    assert "Fix" in _buttons(app.setup_list)
    app.select_tab("setup")
    app._setup_action(row.action)
    assert app._current_tab() == "folders"
    assert not no_dialogs.unexpected(), no_dialogs.report()


def test_the_help_button_opens_the_atlas_entry(hidden_app, no_dialogs, tk_errors, tmp_path, monkeypatch):
    app = hidden_app
    opened = []
    monkeypatch.setattr(app, "open_help", lambda topic=None: opened.append(topic))
    group = app.atlas_entry.master
    assert isinstance(group, ttk.LabelFrame) and group.cget("text") == "Cysteine atlas"
    _buttons(group)["Help"].invoke()
    assert opened == ["faq.atlas"]
