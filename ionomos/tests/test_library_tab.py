"""The app side of the library and FASTA sources (library_tab.py, D89/D90).

The logic without Tk (rows, filters, details, the banner rule, the proposals' text, the download dialog's states,
the worker bodies) runs everywhere; the dialogs themselves are driven through a real Tk root at the end, under the
same rule as tests/test_app.py (they run in CI, and locally only with IONOMOS_GUI_TESTS=1)."""
import copy
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from ionomos import configio, fastasource, library, library_tab, setupcheck
from ionomos.config import load
from ionomos.library_tab import DownloadFlow, Row
from tests.test_library import HUMAN, make_fasta, tsv, write_wf

NO_DECOYS = "UP000005640_9606.fasta"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """No real FragPipe of this computer, and no FASTA readings left over from another test."""
    monkeypatch.setattr(library, "INSTALL_GLOBS", ())
    monkeypatch.setattr(library, "_launchers", lambda: [])
    library._FASTA_CACHE.clear()
    yield
    library._FASTA_CACHE.clear()


@pytest.fixture
def pc(tmp_path):
    """A small lab PC: the pinned TMT workflow and human FASTA, a finished TMT search and an isoDTB search in a
    user's folder, a FASTA without decoys elsewhere. The isoDTB method's workflow is missing."""
    auto, general = tmp_path / "Fragpipe_Auto", tmp_path / "Fragpipe_General"
    for f in (auto / "inbox", auto / "logs", general / "EJQ"):
        f.mkdir(parents=True)
    fasta = make_fasta(auto / "fasta" / HUMAN)
    write_wf(auto / "workflows" / "TMT10-MS3.workflow", "TMT", db=fasta)
    run = general / "Aman" / "KL6159A_TMT"
    run_wf = write_wf(run / "fragpipe.workflow", "TMT", saved="23.1", db=fasta, tmtintegrator__groupby="0")
    (run / "log_2026-01-26_10-00-00.txt").write_text("ALL JOBS DONE IN 52.1 MINUTES\n")
    tsv(run / "tmt-report" / "abundance_gene_MD.tsv", ["Index", "DMSO_1_126", "Drug_1_127N"])
    iso = write_wf(general / "EJQ" / "EJQ-2-027_isoDTB" / "fragpipe.workflow", "isoDTB", db=fasta)
    tsv(iso.parent / "combined_protein.tsv", ["Protein", "S1 Intensity", "S2 Intensity"])
    raw_fasta = make_fasta(general / "EJQ" / "downloads" / NO_DECOYS, human=8, decoy=None, contam=0)
    d = configio.defaults(str(auto), str(general))
    d["paths"]["fragpipe_exe"] = str(tmp_path / "nofragpipe" / "bin" / "fragpipe.bat").replace("\\", "/")
    for m in d["methods"].values():
        m["fasta"] = HUMAN
    d["methods"]["isoDTB"]["workflow"] = "isoDTB.workflow"          # not in the workflows folder
    d["methods"]["DIA"]["workflow"] = "TMT10-MS3.workflow"         # keep DIA quiet: its file is there
    cfg_path = configio.write_config(auto / "config.yaml", d, backup=False)
    cfg = load(cfg_path, check_paths=False)
    return {"auto": auto, "general": general, "fasta": fasta, "run_wf": run_wf, "iso": iso, "raw_fasta": raw_fasta,
            "d": d, "cfg": cfg, "cfg_path": cfg_path}


def _cat(pc):
    cat, _ = library_tab.run_scan(pc["cfg"])
    return cat


# ------------------------------------------------------------------ config + rows --


def test_config_from_data_takes_the_unsaved_settings(pc, tmp_path):
    d = copy.deepcopy(pc["d"])
    d["paths"]["fasta_dir"] = str(tmp_path / "other_fasta")
    cfg = library_tab.config_from_data(d)
    assert Path(cfg.fasta_dir) == tmp_path / "other_fasta" and "TMT" in cfg.methods


def test_resolve_file_finds_names_with_or_without_the_suffix(pc):
    wf_dir = pc["auto"] / "workflows"
    assert library_tab.resolve_file(wf_dir, "TMT10-MS3", ".workflow") == wf_dir / "TMT10-MS3.workflow"
    assert library_tab.resolve_file(wf_dir, "TMT10-MS3.workflow") == wf_dir / "TMT10-MS3.workflow"
    assert library_tab.resolve_file(wf_dir, str(pc["run_wf"])) == pc["run_wf"]   # an absolute path
    assert library_tab.resolve_file(wf_dir, "") is None and library_tab.resolve_file(wf_dir, "gone") is None


def test_rows_follow_the_methods_kind_and_the_type(pc):
    cat = _cat(pc)
    rows = library_tab.candidate_rows(cat, "TMT", installed_version="24.0")
    wfs = [r for r in rows if r.type == "workflow"]
    fas = [r for r in rows if r.type == "fasta"]
    assert wfs and all(r.kind.startswith("workflow · TMT") for r in wfs)
    assert all(r.recommended and r.reasons for r in rows)
    assert [r.info.name for r in fas] == [HUMAN, NO_DECOYS]       # the one without decoys last, marked ✗
    assert "✗" in fas[1].flags and fas[1].score < fas[0].score
    assert len(rows[0].values()) == len(library_tab.LibraryDialog.COLS)
    only_wf = library_tab.candidate_rows(cat, "TMT", what="workflow")
    only_fa = library_tab.candidate_rows(cat, "TMT", what="fasta")
    assert {r.type for r in only_wf} == {"workflow"} and {r.type for r in only_fa} == {"fasta"}
    every = library_tab.candidate_rows(cat, "TMT", show_all=True)
    assert len(every) == len(cat.workflows) + len(cat.fastas)
    assert any(r.info.kind == "isoDTB" for r in every if r.type == "workflow")
    assert any(r.name == NO_DECOYS and "✗" in r.flags for r in every)
    assert not any(r.recommended for r in every if r.type == "workflow" and r.info.kind == "isoDTB")
    flags_seen = [r.recommended for r in every if r.type == "workflow"]
    assert flags_seen == sorted(flags_seen, reverse=True)          # recommended ones first


def test_details_say_every_flag_with_its_fix(pc):
    cat = _cat(pc)
    row = next(r for r in library_tab.candidate_rows(cat, "TMT", show_all=True) if r.name == NO_DECOYS)
    text = library_tab.details_text(row)
    assert "FASTA_NO_DECOYS" in text and "→" in text and "problem: " in text and "NO decoys" in text
    iso = next(r for r in library_tab.candidate_rows(cat, "TMT", show_all=True)
               if r.type == "workflow" and r.info.kind == "isoDTB")
    assert "Not among the recommendations" in library_tab.details_text(iso)
    wf = library_tab.candidate_rows(cat, "TMT", what="workflow")[0]
    text = library_tab.details_text(wf)
    assert "Recommended (score" in text and "Settings:" in text and "label type: TMT-10" in text


@pytest.mark.parametrize("text, want", [("", 3), ("tmt", 2), ("isodtb", 1), ("aman tmt", 1), ("nothing", 0)])
def test_filter_rows(text, want):
    rows = [Row("workflow", "workflow · TMT", "fragpipe.workflow", "D:/Aman/x", "A TMT-10 search", "", "", "p1"),
            Row("workflow", "workflow · TMT", "TMT10-MS3.workflow", "C:/Fragpipe_Auto/workflows", "", "", "", "p2"),
            Row("workflow", "workflow · isoDTB", "fragpipe.workflow", "D:/EJQ", "An isoDTB search", "", "", "p3")]
    assert len(library_tab.filter_rows(rows, text)) == want


def test_flag_marks_and_worst():
    F = library.Flag
    flags = [F("A", "error", "x"), F("B", "warn", "y"), F("C", "warn", "z"), F("D", "info", "w")]
    assert library_tab.flag_marks(flags) == "✗1 !2 ·1"
    assert library_tab.flag_marks([]) == "" and library_tab.worst([]) == "ok"
    assert library_tab.worst(flags) == "error" and library_tab.worst(flags[3:]) == "info"


# ------------------------------------------------------------------ banner + proposals --


def test_banner_shows_for_a_missing_or_broken_file_and_not_otherwise(pc):
    cat = library.Catalog()
    problems = library_tab.method_problems(pc["d"], cat)
    assert problems == ["isoDTB: workflow isoDTB.workflow is not in the workflows folder"]
    assert "Ionomos can look for workflows and FASTAs" in library_tab.banner_text(problems, cat)
    cat = _cat(pc)
    text = library_tab.banner_text(problems, cat)
    assert text.startswith(f"Ionomos found {len(cat.workflows)} workflows and {len(cat.fastas)} FASTAs on this PC")
    fine = copy.deepcopy(pc["d"])
    fine["methods"]["isoDTB"]["workflow"] = "TMT10-MS3.workflow"
    assert library_tab.method_problems(fine, cat) == [] and library_tab.banner_text([], cat) == ""
    # a FASTA the scan marked with an error counts too; a method on another engine needs no workflow
    broken = copy.deepcopy(fine)
    (pc["auto"] / "fasta" / NO_DECOYS).write_bytes(pc["raw_fasta"].read_bytes())
    broken["methods"]["TMT"]["fasta"] = NO_DECOYS
    broken["methods"]["DIA"] = {"engine": "diann", "fasta": HUMAN, "aliases": ["dia"], "data_type": "DIA"}
    cat = _cat(pc)
    probs = library_tab.method_problems(broken, cat)
    assert len(probs) == 1 and probs[0].startswith(f"TMT: FASTA {NO_DECOYS}: ")


def test_a_pinned_workflow_the_catalog_does_not_know_is_read_for_the_banner(pc):
    (pc["auto"] / "workflows" / "isoDTB.workflow").write_text("not a workflow\n")
    seen = []

    def describe(p):
        seen.append(p)
        return library.describe_workflow(p, evidence=False)

    probs = library_tab.method_problems(pc["d"], library.Catalog(), describe)
    assert pc["auto"] / "workflows" / "isoDTB.workflow" in seen
    assert probs and probs[0].startswith("isoDTB: workflow isoDTB.workflow: ")


def test_catalog_freshness():
    now = datetime(2026, 10, 15, 12, 0)
    assert not library_tab.catalog_fresh(library.Catalog(), now)
    assert library_tab.catalog_fresh(library.Catalog(scanned_at=(now - timedelta(hours=3)).isoformat()), now)
    assert not library_tab.catalog_fresh(library.Catalog(scanned_at=(now - timedelta(days=2)).isoformat()), now)
    assert library_tab.catalog_age_hours(library.Catalog(scanned_at="garbage"), now) is None
    assert "Not looked for yet" in library_tab.catalog_line(library.Catalog())


def test_proposals_are_listed_with_reasons_and_nothing_is_applied(pc):
    cat = _cat(pc)
    before = sorted(p.name for p in (pc["auto"] / "workflows").iterdir())
    props = library.auto_assign(pc["d"], cat, "24.0")
    assert [(p.method, p.what) for p in props] == [("isoDTB", "workflow")]
    vals = library_tab.proposal_values(props[0])
    assert vals[:4] == ("isoDTB", "workflow", "isoDTB.workflow", "fragpipe.workflow")
    text = library_tab.proposal_text(props[0])
    assert str(pc["iso"]) in text and "Why this one:" in text and "Apply copies it" in text
    assert "1 proposal(s)" in library_tab.proposals_summary(props)
    assert "Nothing to propose" in library_tab.proposals_summary([])
    assert sorted(p.name for p in (pc["auto"] / "workflows").iterdir()) == before


# ------------------------------------------------------------------ workers --


def test_run_scan_saves_the_catalog_and_a_stopped_scan_keeps_the_fuller_one(pc):
    lines = []
    cat, saved = library_tab.run_scan(pc["cfg"], progress=lines.append)
    assert saved.startswith("saved in") and library.catalog_path(pc["cfg"]).is_file() and lines
    full = len(library.load_catalog(pc["cfg"]).workflows)
    stop = threading.Event()
    stop.set()
    partial, saved = library_tab.run_scan(pc["cfg"], stop=stop)
    assert not partial.complete and "kept" in saved
    assert len(library.load_catalog(pc["cfg"]).workflows) == full


def test_use_copies_into_the_pinned_folders_and_never_moves(pc):
    cat = _cat(pc)
    row = next(r for r in library_tab.candidate_rows(cat, "isoDTB", what="workflow"))
    res = library_tab.adopt_row(row, "isoDTB", pc["cfg"], cat)
    assert res["workflow"] == "isoDTB.workflow" and (pc["auto"] / "workflows" / "isoDTB.workflow").is_file()
    assert pc["iso"].is_file()                                     # the original stays
    row = next(r for r in library_tab.candidate_rows(cat, "TMT", show_all=True) if r.name == NO_DECOYS)
    res = library_tab.adopt_row(row, "TMT", pc["cfg"], cat)
    assert res["workflow"] is None and (pc["auto"] / "fasta" / res["fasta"]).is_file() and pc["raw_fasta"].is_file()


def test_fasta_panel_text(pc):
    assert library_tab.fasta_panel_text("", None) == ("No FASTA chosen for this method.", "info")
    text, sev = library_tab.fasta_panel_text("gone.fas", None)
    assert sev == "error" and "not in the FASTA folder" in text
    good = library.describe_fasta(pc["fasta"])
    text, sev = library_tab.fasta_panel_text(HUMAN, good)
    assert text.startswith(f"{HUMAN}: ") and sev in ("ok", "info", "warn")
    bad = library.describe_fasta(pc["raw_fasta"])
    text, sev = library_tab.fasta_panel_text(NO_DECOYS, bad)
    assert sev == "error" and "FASTA_NO_DECOYS" in text and "→" in text


def test_check_for_updates_text_offline(pc, monkeypatch):
    monkeypatch.setenv("IONOMOS_OFFLINE", "1")
    text = library_tab.check_updates(pc["cfg"])
    assert "UniProt's current release: unknown" in text and "nothing has been changed" in text
    assert f"TMT: {HUMAN}: made 2026-01-30" in text and "Find on this PC… first" in text
    assert "current as far as" not in text, "nothing scanned is not 'everything current'"
    _cat(pc)
    assert "Find on this PC… first" not in library_tab.check_updates(pc["cfg"])
    rel = {"release": "2026_03", "date": "2026-09-02"}
    flags = [library.Flag("UPDATE_FASTA_NEWER", "info", "TMT: a newer FASTA", "Adopt it")]
    text = library_tab.update_report(flags, rel, [], {"TMT": HUMAN}, "2026-10-15T10:00:00")
    assert "2026_03 (2026-09-02)" in text and "at least one release behind" in text and "→ Adopt it" in text


# ------------------------------------------------------------------ download dialog states --


class _Res:
    def __init__(self, **kw):
        self.__dict__.update({"rows": [], "error": "", "notes": [], "query": "", "path": None, "reviewed": True,
                              "isoforms": False, "summary": lambda: "summary line"} | kw)

    @property
    def ok(self):
        return not self.error and self.path is not None


ROWS = [{"id": "UP000005640", "organism": "Homo sapiens", "taxon": "9606", "proteins": 82000},
        {"id": "UP000000589", "organism": "Mus musculus", "taxon": "10090", "proteins": 55000}]


def test_download_flow_from_search_to_done(tmp_path):
    fl = DownloadFlow(tmp_path / "fasta")
    assert fl.state == "idle" and not fl.can_download() and not fl.can_search("  ")
    fl.start_search("human")
    assert fl.busy and not fl.can_search("human")
    with pytest.raises(RuntimeError):
        fl.start_search("mouse")
    fl.search_done(_Res(rows=ROWS, query="human"))
    assert fl.state == "results" and "2 proteome(s)" in fl.message and not fl.can_download()
    fl.pick(5)
    assert fl.picked is None
    fl.pick(0)
    assert fl.can_download()
    row = fl.start_download()
    assert row["id"] == "UP000005640" and fl.busy and not fl.can_download()
    fl.progress("downloading UP000005640", 50, 200)
    assert fl.message == "downloading UP000005640 (25 %)"
    made = tmp_path / "fasta" / "2026-10-15-decoys-reviewed-contam-UP000005640_9606.fasta.fas"
    fl.finished(_Res(path=tmp_path / "fasta" / NO_DECOYS, notes=["n1"]), _Res(path=made, notes=["n2"]))
    assert fl.state == "done" and fl.final == made and fl.notes == ["n1", "n2"] and str(made) in fl.message
    assert fl.can_search("mouse") and fl.can_download()          # another one may follow


@pytest.mark.parametrize("got, prep, state, words", [
    (_Res(error="stopped"), None, "stopped", "nothing was kept"),
    (_Res(error="network use is off"), None, "failed", "Not done: network use is off"),
    (_Res(path=Path("raw.fasta")), _Res(error="stopped"), "stopped", "Stopped"),
    (_Res(path=Path("raw.fasta")), _Res(error="already has decoys"), "failed", "The downloaded file without decoys"),
])
def test_download_flow_failures(tmp_path, got, prep, state, words):
    fl = DownloadFlow(tmp_path)
    fl.start_search("human")
    fl.search_done(_Res(rows=ROWS))
    fl.pick(0)
    fl.start_download()
    fl.finished(got, prep)
    assert fl.state == state and words in fl.message and fl.final is None and not fl.busy


def test_download_flow_refuses_a_folder_with_a_space_and_a_failed_search(tmp_path):
    fl = DownloadFlow(tmp_path / "my fasta")
    fl.start_search("human")
    fl.search_done(_Res(rows=ROWS))
    fl.pick(0)
    assert "space" in fl.dest_problem() and not fl.can_download()
    with pytest.raises(RuntimeError, match="space"):
        fl.start_download()
    assert DownloadFlow(None).dest_problem()
    fl = DownloadFlow(tmp_path)
    fl.start_search("zzz")
    fl.search_done(_Res(query="zzz"))
    assert fl.state == "failed" and "no reference proteome for 'zzz'" in fl.message
    fl.start_search("human")
    fl.search_done(_Res(error="UniProt can't be reached"))
    assert fl.state == "failed" and "Search failed: UniProt can't be reached" in fl.message


def test_run_download_prepares_into_the_same_folder(tmp_path, monkeypatch):
    calls = []
    raw = tmp_path / NO_DECOYS

    def download(pid, dest, **kw):
        calls.append(("download", pid, dest, kw["reviewed"], kw["isoforms"]))
        return fastasource.DownloadResult(proteome=pid, path=raw, reviewed=kw["reviewed"], isoforms=kw["isoforms"])

    def prepare(src, dest, **kw):
        calls.append(("prepare", src, dest, kw["tags"], kw["contaminant_candidates"]))
        return fastasource.PrepareResult(source=src, path=dest / "made.fasta.fas")

    monkeypatch.setattr(fastasource, "download_proteome", download)
    monkeypatch.setattr(fastasource, "prepare_for_fragpipe", prepare)
    got, prep = library_tab.run_download("UP000005640", tmp_path, isoforms=True, candidates=[Path("x.fas")])
    assert calls == [("download", "UP000005640", tmp_path, True, True),
                     ("prepare", raw, tmp_path, ["reviewed", "isoforms"], [Path("x.fas")])]
    assert prep.path == tmp_path / "made.fasta.fas"
    monkeypatch.setattr(fastasource, "download_proteome",
                        lambda pid, dest, **kw: fastasource.DownloadResult(proteome=pid, error="offline"))
    got, prep = library_tab.run_download("UP000005640", tmp_path)
    assert got.error == "offline" and prep is None


def test_progress_text_and_candidates(pc):
    assert library_tab.progress_text("reading", 0, None) == "reading"
    assert library_tab.progress_text("reading", 300, 200) == "reading (100 %)"
    assert library_tab.fasta_candidates(pc["auto"] / "fasta") == [pc["fasta"]]
    assert library_tab.fasta_candidates(None) == [] and library_tab.fasta_candidates(pc["auto"] / "nope") == []


# ------------------------------------------------------------------ setup checklist --


def test_setup_check_item_reads_the_last_scan_only(pc, monkeypatch):
    item = setupcheck.library_item(pc["cfg"])
    assert item.status == "ok" and "not looked for" in item.detail
    (pc["auto"] / "fasta" / NO_DECOYS).write_bytes(pc["raw_fasta"].read_bytes())
    _cat(pc)
    monkeypatch.setattr(library, "scan", lambda *a, **k: pytest.fail("a check must not scan"))
    monkeypatch.setattr(library, "describe_fasta", lambda *a, **k: pytest.fail("a check must not read a FASTA"))
    item = setupcheck.library_item(pc["cfg"])
    assert item.key == "library" and item.title == "Workflows and FASTAs"
    assert item.status == "warn" and item.detail.startswith("3 pinned, flags: ✗ FASTA_NO_DECOYS")
    assert item.action == "open_tab:3" and "as of the scan of" in item.detail


def test_setup_checklist_has_the_library_item(lab):
    items = {i.key: i for i in setupcheck.run(lab["cfg_path"], probe_watcher=False)}
    assert items["library"].status == "ok"


# ------------------------------------------------------------------ the dialogs (real Tk) --

from tests.conftest import gui_tests, make_tk_root  # noqa: E402

_ok, _why = gui_tests()
gui = pytest.mark.skipif(not _ok, reason=f"no GUI: {_why}")


@pytest.fixture
def app(pc, monkeypatch, tmp_path):
    import tkinter as tk

    from ionomos.app import App

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    root = make_tk_root()
    root.withdraw()
    a = App(root, pc["cfg_path"])
    yield a
    tk.Tk.destroy(root)


def _pump_until(app, cond, secs=20):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        app.root.update()
        if cond():
            return True
        time.sleep(0.05)
    return False


def _select(app, key):
    app.mtree.selection_set(key)
    app._show_method()


@gui
def test_find_on_this_pc_scans_lists_and_uses(app, pc, monkeypatch):
    from tkinter import messagebox

    monkeypatch.setattr(messagebox, "showinfo", lambda *a, **k: None)
    monkeypatch.setattr(messagebox, "showerror", lambda *a, **k: pytest.fail(f"dialog: {a}"))
    _select(app, "isoDTB")
    dlg = app.library.find()
    assert _pump_until(app, lambda: dlg.rows and not dlg.scanning)
    assert library.catalog_path(pc["cfg"]).is_file()              # saved: the next open is instant
    assert all(r.type == "workflow" and "isoDTB" in r.kind for r in dlg.rows if r.type == "workflow")
    assert "isoDTB" in dlg.details.get("1.0", "end")
    dlg.use()
    assert _pump_until(app, lambda: app.v("m.workflow").get() == "isoDTB.workflow"
                       and (pc["auto"] / "workflows" / "isoDTB.workflow").is_file())
    assert app.data["methods"]["isoDTB"]["workflow"] == "isoDTB.workflow"
    dlg.close()
    again = app.library.find("fasta")                              # the Choose… beside the FASTA box
    assert _pump_until(app, lambda: again.rows)
    assert {r.type for r in again.rows} == {"fasta"} and not again.scanning
    again.show_all.set(True)
    again.show()
    assert any(r.name == NO_DECOYS for r in again.rows)
    again.close()


@gui
def test_banner_and_proposals_apply_through_save(app, pc, monkeypatch):
    from tkinter import messagebox

    monkeypatch.setattr(messagebox, "showinfo", lambda *a, **k: None)
    monkeypatch.setattr(messagebox, "showerror", lambda *a, **k: pytest.fail(f"dialog: {a}"))
    app.nb.select(app.tab_methods)
    app.library.check_banner()
    assert _pump_until(app, lambda: app.library.banner.winfo_manager() == "grid")
    assert "isoDTB" in app.library.banner_lbl.cget("text")
    dlg = app.library.review()
    assert _pump_until(app, lambda: dlg.proposals)
    assert load(pc["cfg_path"], check_paths=False).methods["isoDTB"].workflow == "isoDTB.workflow"
    assert not (pc["auto"] / "workflows" / "isoDTB.workflow").exists()   # nothing before Apply
    dlg.apply()
    assert _pump_until(app, lambda: not dlg.alive)
    assert (pc["auto"] / "workflows" / "isoDTB.workflow").is_file()
    assert list((pc["auto"] / "config-backups").glob("config-*.yaml"))


@gui
def test_download_dialog_runs_off_the_tk_thread(app, pc, monkeypatch):
    from tkinter import messagebox

    monkeypatch.setattr(messagebox, "showinfo", lambda *a, **k: None)
    made = pc["auto"] / "fasta" / "2026-10-15-decoys-reviewed-contam-UP000005640_9606.fasta.fas"
    threads = []
    monkeypatch.setattr(fastasource, "search_proteomes",
                        lambda text, **k: fastasource.SearchResult(query=text, rows=ROWS))

    def fake_run(pid, dest, **kw):
        threads.append(threading.current_thread() is threading.main_thread())
        made.write_bytes(pc["fasta"].read_bytes())
        return (fastasource.DownloadResult(proteome=pid, path=dest / NO_DECOYS),
                fastasource.PrepareResult(source=dest / NO_DECOYS, path=made))

    monkeypatch.setattr(library_tab, "run_download", fake_run)
    _select(app, "TMT")
    dlg = app.library.download()
    dlg.search()
    assert _pump_until(app, lambda: dlg.flow.state == "results")
    dlg.download()
    assert _pump_until(app, lambda: dlg.flow.state == "done")
    assert threads == [False]
    dlg.use()
    assert app.v("m.fasta").get() == made.name
    dlg.close()
