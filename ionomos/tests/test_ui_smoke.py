"""Every tab and every window, opened hidden (docs/UI.md S4.2), and the main buttons on a 1366x768 screen (S4.5).

Runs where tests/test_app.py runs (CI; locally only with IONOMOS_GUI_TESTS=1): the harness withdraws every window,
records every dialog and captures every Tk error (tests/tkharness.py)."""
import time

import pytest

from ionomos import attention, tkutil, uinav
from tests.conftest import gui_tests
from tests.tkharness import TkErrors, fits_screen, pump_until, toplevels

_ok, _why = gui_tests()
pytestmark = pytest.mark.skipif(not _ok, reason=f"no GUI: {_why}")

SMALL = (1366, 768)


def _scale() -> float:
    """1366x768 at 100 % scaling; the budget grows with the font where the display is scaled (150 % on the lab PC)."""
    return max(tkutil.char_width() / 7.0, 1.0)


def _fits(win) -> bool:
    s = _scale()
    return fits_screen(win, int(SMALL[0] * s), int(SMALL[1] * s))


def _lab(app, tmp_path):
    app.v("quick.root").set(str(tmp_path / "Auto"))
    app.v("quick.users").set(str(tmp_path / "General"))
    app.apply_quick()
    app.create_all()
    assert app.save()


def test_every_tab_refreshes_and_opens_its_help(hidden_app, no_dialogs, tk_errors, tmp_path, monkeypatch):
    app = hidden_app
    _lab(app, tmp_path)
    opened = []
    monkeypatch.setattr(app, "open_help", lambda topic=None: opened.append(topic))
    for key in uinav.TAB_KEYS:
        app.select_tab(key)
        app.root.update()
        assert app._current_tab() == key
        app._keys["<F5>"]()
        app._keys["<F1>"]()
        assert opened[-1] == uinav.tab_help(key)
        pump_until(app, lambda: False, secs=0.3)  # let the tab's own refresh threads post back
    assert len(opened) == len(uinav.TAB_KEYS)
    for page in app.analysis.nb.tabs():
        app.analysis.nb.select(page)
        app.root.update()
    for page in app.run_nb.tabs():
        app.run_nb.select(page)
        app.root.update()
    assert not no_dialogs.unexpected(), no_dialogs.unexpected()


def _item(log_dir, kind, dest=None, **kw):
    return attention.raise_item(log_dir, kind, f"a {kind} item", "what happened", key=f"smoke:{kind}",
                                severity="error" if "failed" in kind else "input", dest=dest,
                                causes=["the most likely cause"], fixes=["what to do"], details="the log's tail",
                                **kw)


def test_every_window_opens_hidden_and_fits_a_laptop_screen(hidden_app, no_dialogs, tk_errors, tmp_path, monkeypatch):
    """Each window of the inventory in docs/UI.md, with fake data, opened and closed; each fits 1366x768."""
    app = hidden_app
    _lab(app, tmp_path)
    log_dir = app._active_log_dir()
    seen: dict[str, bool] = {}

    def check(name, win):
        app.root.update_idletasks()
        assert win.winfo_exists(), name
        assert win.state() == "withdrawn", f"{name} is on screen"
        seen[name] = _fits(win)
        sizes[name] = (win.winfo_reqwidth(), win.winfo_reqheight())

    sizes: dict[str, tuple] = {}
    # (the main window's natural size is bigger than a laptop; uinav.fit_size shrinks it to the screen on start
    # and test_main_buttons_are_on_screen_at_1366x768 checks what matters then)
    # Test names
    check("Test names", app.test_names())
    app.names_run()
    app.names_win.destroy()
    # the attention list, Ask about this, and a pop-up of each kind
    check("needs attention list", app.popups.center())
    dest = tmp_path / "General" / "EJQ" / "exp"
    (dest / "fragpipe").mkdir(parents=True)
    for kind in ("search_failed", "search_waiting", "intake_rejected", "analysis_failed", "analysis_input"):
        it = _item(log_dir, kind, dest=dest if kind.startswith("analysis") else None, job_id=1,
                   data={"method": "DIA", "issues": [{"code": "ONE_CONDITION", "severity": "input",
                                                      "message": "every sample has the same condition"}]}
                   if kind.startswith("analysis") else {})
        w = app.popups.show(it)
        pump_until(app, lambda: False, secs=0.3)
        check(f"pop-up: {kind}", w.win)
        w.close()
    ask = app.popups.ask_about(_item(log_dir, "search_waiting"))
    pump_until(app, lambda: not ask.alive() or ask.win.winfo_exists(), secs=1)
    check("Ask about this", ask.win)
    ask.close()
    # Report a problem
    app.report_problem("smoke")
    check("Report a problem", app.report_win)
    pump_until(app, lambda: "Level" in app.report_dialog.preview.get("1.0", "end"), secs=10)
    app.report_dialog.win.destroy()
    # the folder check (an empty folder: the window says so)
    empty = tmp_path / "empty"
    empty.mkdir()
    app.analysis.check(empty)
    win = app.analysis.check_win
    assert pump_until(app, lambda: win.scan is not None)
    check("folder check", win.win)
    win.close()
    # the library dialogs: Find on this PC…, Download from UniProt…, Add decoys… (network and Philosopher stubbed)
    from ionomos import fastasource

    monkeypatch.setattr(fastasource, "search_proteomes", lambda text, **k: fastasource.SearchResult(query=text, rows=[]))
    monkeypatch.setattr(fastasource, "prepare_for_fragpipe",
                        lambda src, dest, **k: fastasource.PrepareResult(source=src, error="stubbed in the smoke test"))
    app.mtree.selection_set("isoDTB")
    app._show_method()
    find = app.library.find()
    assert find is not None
    pump_until(app, lambda: not find.scanning, secs=20)
    check("Find on this PC", find.win)
    find.close()
    dl = app.library.download()
    assert dl is not None
    check("Download from UniProt", dl.win)
    dl.close()
    fasta = tmp_path / "plain.fasta"
    fasta.write_text(">sp|A\nMK\n", encoding="utf-8")
    no_dialogs.expect("askopenfilename", str(fasta))
    prep = app.library.add_decoys()
    assert prep is not None
    pump_until(app, lambda: "stubbed" in prep.msg.get("1.0", "end"), secs=5)
    check("Add decoys and contaminants", prep.win)
    prep.close()
    # Restore a previous config… and Save's "what will change"
    app.restore_config()
    check("Restore a previous config", app.restore_win)
    pump_until(app, lambda: not app.busy("backups"), secs=5)
    app.restore_win.destroy()
    app.root.after(300, lambda: app.confirm_win.destroy() if app.confirm_win.winfo_exists() else None)
    app._confirm_changes(["Watcher → Stable seconds: 60 → 45"])
    check("what will change", app.confirm_win) if app.confirm_win.winfo_exists() else None
    # the resolver window demo, skipped by itself after a moment
    before = set(toplevels(app.root))

    def skip_demo():
        new = [w for w in toplevels(app.root) if w not in before]
        if new:
            check("resolver demo", new[0])
            new[0].destroy()
        else:
            app.root.after(100, skip_demo)

    app.root.after(200, skip_demo)
    app.tb_gui_demo()
    assert "demo answer: skipped" in app.out.text.get("1.0", "end")
    # the Today / Jobs pop-up windows closed; nothing left open, no Tk error, no dialog nobody expected
    for w in toplevels(app.root):
        w.destroy()
    too_big = {n: sizes[n] for n, ok in seen.items() if not ok}
    assert not too_big, f"wider or taller than 1366x768 (at {_scale():.2f}x): {too_big}"
    assert "resolver demo" in seen and len(seen) >= 14, sorted(seen)
    assert not no_dialogs.unexpected(), no_dialogs.unexpected()


def test_main_buttons_are_on_screen_at_1366x768(hidden_app, no_dialogs, tmp_path):
    """S4.5: with the window at a laptop's size, Save, Help, the attention badge's row, Start watcher, Run analysis
    and Check again are mapped and inside the window. The root is mapped fully transparent for the measurement."""
    import tkinter as tk

    app = hidden_app
    _lab(app, tmp_path)
    root = app.root
    root.geometry(f"{SMALL[0]}x{SMALL[1]}+0+0")
    try:
        root.attributes("-alpha", 0.0)
    except tk.TclError:
        pytest.skip("this Tk cannot make a window transparent")
    tk.Wm.wm_deiconify(root)  # the harness made deiconify a no-op; the base method maps it (invisible: alpha 0)
    try:
        root.update()
        for _ in range(10):
            root.update()
            time.sleep(0.05)
        bottom = root.winfo_rooty() + root.winfo_height()
        right = root.winfo_rootx() + root.winfo_width()
        assert root.winfo_height() <= SMALL[1] + 40, root.winfo_height()

        def visible(w, name):
            assert w.winfo_ismapped(), f"{name} is not shown"
            assert w.winfo_rooty() + w.winfo_height() <= bottom, f"{name} is below the window's edge"
            assert w.winfo_rootx() + w.winfo_width() <= right, f"{name} is right of the window's edge"

        with TkErrors(app) as errors:
            visible(app.save_btn, "Save")
            visible(app.help_btn, "Help")
            visible(app.report_btn, "Report a problem")
            visible(app.strip_lbl, "the status strip")
            app.select_tab("run")
            root.update()
            visible(app.start_btn, "Start watcher")
            app.select_tab("analysis")
            app.analysis.nb.select(0)
            root.update()
            visible(app.analysis.editor.run_btn, "Run analysis")
            app.select_tab("setup")
            root.update()
            visible(app.check_btn, "Check again")
            app.select_tab("jobs")
            root.update()
            visible(app.pause_btn, "Pause searches")
            app.select_tab("today")
            root.update()
            visible(app.today_jobs, "the recent jobs list")
            errors.assert_none()
    finally:
        root.withdraw()
