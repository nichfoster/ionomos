"""Finding your way around the app (uinav.py, docs/UI.md Slice 1): the logic everywhere, the window in CI only."""
import json
import re
from pathlib import Path

import pytest

from ionomos import names, uinav
from ionomos.ledger import Job, Ledger

SRC = Path(uinav.__file__).parent


# ------------------------------------------------------------------- tabs and help --


def test_every_tab_has_a_help_topic_that_exists():
    from ionomos import help as helpdoc

    entries = helpdoc.entries()
    for key, _label, topic in uinav.TABS:
        assert topic in entries, f"tab {key}: help topic {topic} is missing"
    assert uinav.tab_help(None) == uinav.tab_help("no such tab") == "faq.app"


def test_the_tab_list_matches_the_tabs_the_app_builds():
    """One nb.add per tab, in app.py and the two tab modules; and app.py maps every key."""
    adds = (SRC / "app.py").read_text(encoding="utf-8").count("self.nb.add(")
    adds += sum((SRC / m).read_text(encoding="utf-8").count("app.nb.add(") for m in ("analysis_tab.py", "notify_tab.py"))
    assert adds == len(uinav.TABS)
    app_src = (SRC / "app.py").read_text(encoding="utf-8")
    for key in uinav.TAB_KEYS:
        assert f'("{key}", "' in app_src, f"app._tab_frames has no {key}"
    assert set(uinav.DIGIT_TABS.values()) <= set(uinav.TAB_KEYS)
    for digit, key in uinav.DIGIT_TABS.items():
        label = next(lbl for k, lbl, _ in uinav.TABS if k == key)
        assert digit == "0" or label.startswith(f"{digit} "), (digit, label)  # Ctrl+6 is the tab labelled 6


def test_the_shortcuts_are_listed_in_the_help_tab_and_the_faq():
    text = uinav.shortcuts_text()
    faq = (SRC / "help" / "faq.md").read_text(encoding="utf-8")
    entry = faq.split("{#faq.app}", 1)[1].split("\n## ", 1)[0]
    for _event, keys, _what in uinav.SHORTCUTS:
        assert keys in text
        for k in re.findall(r"F\d+|Ctrl\+\w+", keys):
            assert k in entry, f"{k} is not in the help entry faq.app"


# ------------------------------------------------------------------- window state --


def test_state_round_trips_next_to_the_config(tmp_path):
    cfg = tmp_path / "Fragpipe_Auto" / "config.yaml"
    p = uinav.state_path(cfg)
    assert p == cfg.parent / names.UI_STATE_FILE and " " not in names.UI_STATE_FILE
    assert uinav.save_state(p, {"geometry": "1200x800+10+20", "tab": "jobs"}) is False  # folder not created here
    assert not cfg.parent.exists()
    cfg.parent.mkdir()
    assert uinav.save_state(p, {"geometry": "1200x800+10+20", "zoomed": False, "tab": "jobs", "junk": 1})
    assert uinav.load_state(p) == {"geometry": "1200x800+10+20", "zoomed": False, "tab": "jobs"}
    assert json.loads(p.read_text(encoding="utf-8")).keys() == {"geometry", "zoomed", "tab"}
    assert not list(cfg.parent.glob(".ui-*"))  # no temp file left


@pytest.mark.parametrize("text", ["", "not json", "[1, 2]", '{"geometry": 5, "tab": "nowhere", "zoomed": "yes"}'])
def test_a_bad_state_file_is_ignored(tmp_path, text):
    p = tmp_path / names.UI_STATE_FILE
    p.write_text(text, encoding="utf-8")
    assert uinav.load_state(p) == {}
    assert uinav.load_state(tmp_path / "missing.json") == {}


@pytest.mark.parametrize("geom, screen, want", [
    ("1200x800+10+20", (1920, 1080), "1200x800+10+20"),       # fits: unchanged
    ("2600x1400+0+0", (1366, 768), "1350x708+0+0"),          # from a 4K monitor onto a laptop
    ("1200x800+3000+20", (1920, 1080), "1200x800+720+20"),   # left on a second monitor that is gone
    ("1200x800+-8+-8", (1920, 1080), "1200x800+0+0"),        # Windows' maximised offsets
    ("400x300+10+10", (1920, 1080), "900x640+10+10"),         # never below the minimum
    ("400x300+10+10", (800, 600), "784x540+10+0"),             # ... unless the screen is smaller
    ("garbage", (1920, 1080), None),
    ("1200x800", (1920, 1080), None),
])
def test_clamp_geometry(geom, screen, want):
    assert uinav.clamp_geometry(geom, *screen) == want


def test_parse_geometry():
    assert uinav.parse_geometry("1200x800+10+-8") == (1200, 800, 10, -8)
    assert uinav.parse_geometry("0x800+1+1") is None
    assert uinav.parse_geometry(None) is None


@pytest.mark.parametrize("first_run, remembered, want", [
    (True, "jobs", "setup"), (False, "jobs", "jobs"), (False, None, "today"), (False, "gone", "today")])
def test_start_tab(first_run, remembered, want):
    assert uinav.start_tab(first_run, remembered) == want


# --------------------------------------------------------------------------- jobs --


def _job(i, status="done", reason=None, user="EJQ", method="isoDTB", name="20260902-isoDTB_EJQ-2-027"):
    return Job(inbox_name=name, user=user, method=method, dest_dir=f"/x/{user}/{name}", status=status, reason=reason,
               id=i)


@pytest.mark.parametrize("query, hit", [
    ("", True), ("   ", True), ("ejq", True), ("EJQ isodtb", True), ("ejq tmt", False), ("12", True),
    ("#12", True), ("1", False), ("027", True), ("0902", True), ("done", True), ("failed", False), ("needs", False),
])
def test_job_matches(query, hit):
    assert uinav.job_matches(_job(12), query) is hit


def test_job_state_matches_the_jobs_tab():
    assert uinav.job_state(_job(1, "queued", "waiting: no FASTA")) == "waiting"
    assert uinav.job_state(_job(1, "done", "analysis needs a decision: one condition")) == "needs a look"
    assert uinav.job_state(_job(1, "failed", "x")) == "failed"
    assert uinav.job_matches(_job(1, "queued", "waiting: no FASTA"), "waiting fasta")


# -------------------------------------------------------------------------- today --


def test_snapshot_reads_attention_and_recent_jobs(tmp_path):
    from ionomos import attention

    logs, db = tmp_path / "logs", tmp_path / "ionomos.db"
    logs.mkdir()
    led = Ledger(db)
    try:
        ids = [led.insert(_job(None, "queued", name=f"exp{i}")) for i in range(20)]
        led.set_status(ids[-1], "failed", "FragPipe ran out of memory")
        led.set_status(ids[-2], "done")
        led.set_status(ids[-3], "queued", "waiting: no FASTA")
    finally:
        led.close()
    attention.raise_item(logs, "search_failed", "FragPipe failed on EJQ/exp19", "out of memory", severity="error",
                         job_id=ids[-1])
    snap = uinav.snapshot(logs, db)
    assert not snap.problem
    assert [i.title for i in snap.attention] == ["FragPipe failed on EJQ/exp19"]
    assert len(snap.jobs) == 15 and snap.jobs[0].id == ids[-1]           # newest first, the last 15
    assert snap.jobs[0].state == "failed" and "memory" in snap.jobs[0].note
    assert snap.jobs[2].state == "waiting" and snap.jobs[0].job.id == ids[-1]
    assert snap.counts == {"failed": 1, "done": 1, "waiting": 1, "queued": 17}
    assert snap.headline().startswith("1 thing needs you · ") and "1 failed" in snap.headline()
    assert snap.jobs[1].report is None and not snap.paused


def test_snapshot_never_raises(tmp_path):
    assert uinav.snapshot(None, None).headline() == "Nothing needs you · no jobs yet"
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"not a database")
    snap = uinav.snapshot(tmp_path / "no-logs", bad)
    assert snap.jobs == [] and snap.attention == []
    from ionomos.worker import pause

    pause(tmp_path / "logs", "test")
    snap = uinav.snapshot(tmp_path / "logs", None)
    assert snap.paused and snap.headline().endswith("searches PAUSED")


def test_the_report_link_is_shown_when_the_report_exists(tmp_path):
    db = tmp_path / "ionomos.db"
    dest = tmp_path / "EJQ" / "exp"
    (dest / "results").mkdir(parents=True)
    (dest / "results" / "report.html").write_text("<html></html>", encoding="utf-8")
    led = Ledger(db)
    try:
        jid = led.insert(Job(inbox_name="exp", user="EJQ", method="DIA", dest_dir=str(dest)))
        led.set_status(jid, "done")
    finally:
        led.close()
    row = uinav.snapshot(None, db).jobs[0]
    assert row.report == str(dest / "results" / "report.html") and row.note == "report ready"


# ------------------------------------------------------- the window (CI only, hidden) --


def test_today_tab_keys_and_remembered_window(tmp_path, monkeypatch):
    """Hidden window: Today lists a job and an item, F1 opens the tab's topic, Ctrl+6 is Jobs, Find filters,
    and the size and tab come back on the next start. Runs where tests/test_app.py runs (CI)."""
    from tests.conftest import gui_tests, make_tk_root

    ok, why = gui_tests()
    if not ok:
        pytest.skip(f"no GUI: {why}")
    import tkinter as tk

    from ionomos import attention
    from ionomos.app import App

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    cfg = tmp_path / "Auto" / "config.yaml"
    root = make_tk_root()
    root.withdraw()
    try:
        app = App(root, cfg)
        assert app.nb.select() == str(app.tab_setup)  # a new install opens on the checklist
        assert [app.nb.tab(t, "text").strip().replace("  ", " ") for t in app.nb.tabs()] == \
            [lbl for _, lbl, _ in uinav.TABS]
        app.v("quick.root").set(str(tmp_path / "Auto"))
        app.v("quick.users").set(str(tmp_path / "General"))
        app.apply_quick()
        app.create_all()
        assert app.save()
        led = app._ledger() or Ledger(app._active_paths()["database"])
        try:
            jid = led.insert(Job(inbox_name="exp1", user="EJQ", method="DIA", dest_dir=str(tmp_path / "x")))
            led.set_status(jid, "failed", "out of memory")
            led.insert(Job(inbox_name="other", user="Isaac", method="TMT", dest_dir=str(tmp_path / "y")))
        finally:
            led.close()
        attention.raise_item(app._active_log_dir(), "search_failed", "FragPipe failed on EJQ/exp1", "oom",
                             severity="error", job_id=jid)
        app.select_tab("today")
        app.refresh_today()
        for _ in range(100):
            root.update()
            if app.today_jobs.get_children() and app.today_attn.get_children():
                break
            __import__("time").sleep(0.05)
        assert app.today_jobs.exists(str(jid)) and "need" in app.today_head.cget("text")
        opened = []
        monkeypatch.setattr(app, "open_help", lambda topic=None: opened.append(topic))
        assert app._keys["<F1>"]() == "break" and opened == ["faq.app"]  # Today's topic
        app._keys["<Control-Key-7>"]()
        assert app.nb.select() == str(app.tab_analysis)
        app._keys["<F1>"]()
        assert opened[-1] == "faq.rerun"
        app._keys["<Control-Key-6>"]()
        assert app.nb.select() == str(app.tab_jobs)
        app.v("jobs.find").set("isaac")
        assert len(app.jtree.get_children()) == 1 and "1 shown" in app.jobs_state.cget("text")
        app.v("jobs.find").set("")
        assert len(app.jtree.get_children()) == 2
        app.today_jobs.selection_set(str(jid))
        app._today_job("show")
        assert app.nb.select() == str(app.tab_jobs) and app.jtree.selection() == (str(jid),)
        root.geometry("1000x700+30+40")
        root.update()
        app._save_ui_state()
        state = uinav.load_state(uinav.state_path(cfg))
        # a withdrawn window keeps its size until it is shown (Tk on Windows), so compare with what Tk reports
        assert state["tab"] == "jobs" and state["geometry"] == root.geometry(), state
        uinav.save_state(uinav.state_path(cfg), {**state, "geometry": "1000x700+30+40"})
        app.popups.stop()
    finally:
        tk.Tk.destroy(root)
    root = make_tk_root()
    root.withdraw()
    try:
        app = App(root, cfg)
        assert app.nb.select() == str(app.tab_jobs)  # the tab used last
        assert uinav.parse_geometry(root.geometry())[2:] == (30, 40)  # put where it was
        app.popups.stop()
    finally:
        tk.Tk.destroy(root)
