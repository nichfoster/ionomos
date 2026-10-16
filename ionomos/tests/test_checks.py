"""Check everything (checks.py, docs/UI.md S3.1): one list from setup, the FragPipe preflight (not started), the
workflow library, health and what needs a person. No Tk; FragPipe is absent on the testbed, so this is quick."""
from __future__ import annotations

import os
import time

import pytest

from ionomos import attention, checks, names, worker
from ionomos.cli import main


def _by_key(rows):
    return {r.key: r for r in rows}


def test_a_row_per_source_on_the_testbed(lab):
    rows = checks.everything(lab["cfg_path"], probe_watcher=False)
    assert {r.source for r in rows} == set(checks.SOURCES)
    assert len({r.key for r in rows}) == len(rows)                       # keys are unique
    assert all(r.key.startswith(f"{r.source}:") for r in rows)
    assert all(r.status in checks.MARK for r in rows)
    assert all(r.fix for r in rows if r.status != "ok")                  # each says what to do (D46)
    by = _by_key(rows)
    assert by["setup:folders"].status == "ok" and by["setup:fragpipe"].status == "todo"
    # preflight rows that repeat the checklist are left out; its own are kept
    assert not any(r.key in ("fragpipe:launcher", "fragpipe:disk", "fragpipe:spaces") for r in rows)
    assert not any(r.key.startswith("fragpipe:write:") for r in rows)
    assert not any(r.key.startswith("fragpipe:method:") and r.key.count(":") == 2 for r in rows)
    assert by["fragpipe:longpaths"].status in ("ok", "warn")
    assert by["fragpipe:method:isoDTB:workflow"].status == "fail"      # 'x' is not a workflow
    assert by["fragpipe:method:isoDTB:workflow"].action == "open_tab:methods"
    assert by["library:updates"].status == "info"                       # never scanned: optional
    assert by["attention:items"].status == "ok" and by["attention:paused"].status == "ok"
    assert by["health:crash"].status == "ok" and by["health:log"].status == "ok"
    # setup's tab numbers become tab names
    assert all(not (r.action or "").startswith("open_tab:") or not r.action.split(":")[1].isdigit() for r in rows)


def test_summary_wording():
    R = checks.Row
    ok = [R("setup:a", "setup", "ok", "A"), R("setup:b", "setup", "ok", "B"), R("library:i", "library", "info", "I")]
    assert checks.summary(ok) == "All 2 checks pass."
    warn = [*ok, R("health:w", "health", "warn", "A crash in the last day", fix="x")]
    assert checks.summary(warn) == "No problems; 1 to look at: A crash in the last day."
    bad = [*warn, R("setup:f", "setup", "todo", "Folders exist and are writable", fix="x"),
           R("fragpipe:x", "fragpipe", "fail", "isoDTB: workflow", fix="x")]
    assert checks.summary(bad) == "2 problems: Folders exist and are writable; isoDTB: workflow (1 to look at)."
    assert checks.summary(bad[:-1]) == "1 problem: Folders exist and are writable (1 to look at)."
    assert [r.key for r in checks.problems(bad)] == ["setup:f", "fragpipe:x"]


def test_summary_of_the_testbed_names_its_problems(lab):
    rows = checks.everything(lab["cfg_path"], probe_watcher=False)
    s = checks.summary(rows)
    n = len(checks.problems(rows))
    assert n and s.startswith(f"{n} problems: ") and "FragPipe found" in s


def test_a_missing_config_gives_rows_not_an_exception(tmp_path):
    rows = checks.everything(tmp_path / "nope" / "config.yaml", probe_watcher=False)
    by = _by_key(rows)
    assert by["setup:config"].status == "todo" and by["setup:config"].action == "save"
    for source in ("fragpipe", "library", "health", "attention"):
        assert by[f"{source}:saved"].status == "info" and by[f"{source}:saved"].action == "save"
    assert {r.source for r in rows} == set(checks.SOURCES)


def test_unsaved_edits_feed_health_and_attention(lab, tmp_path):
    from ionomos import configio

    data = configio.read_config(lab["cfg_path"])
    worker.pause(lab["cfg"].log_dir)
    rows = checks.everything(tmp_path / "not-saved.yaml", data=data, probe_watcher=False)
    by = _by_key(rows)
    assert by["fragpipe:saved"].status == "info"
    assert by["attention:paused"].status == "warn"                       # read from the edits' log folder


def test_a_new_crash_report_is_a_health_warning(lab):
    log_dir = lab["cfg"].log_dir
    old = log_dir / "crash-20200101-000000-000000.txt"
    old.write_text("old", encoding="utf-8")
    os.utime(old, (time.time() - 3 * 86400,) * 2)
    assert _by_key(checks.everything(lab["cfg_path"], probe_watcher=False))["health:crash"].status == "ok"
    new = log_dir / "crash-20991231-235959-000000.txt"
    new.write_text("ionomos crash in main thread", encoding="utf-8")
    row = _by_key(checks.everything(lab["cfg_path"], probe_watcher=False))["health:crash"]
    assert row.status == "warn" and str(new) in row.detail and row.action == f"open_path:{log_dir}" and row.fix


def test_log_errors_are_counted(lab):
    log_file = names.log_file(lab["cfg"].log_dir)
    log_file.write_text("2026-10-15 09:00:00,001 INFO    ionomos: started\n"
                        "2026-10-15 09:01:00,002 WARNING ionomos.intake: odd name\n"
                        "2026-10-15 09:02:00,003 ERROR   ionomos.worker: FragPipe failed\n"
                        "Traceback (most recent call last):\n  boom\n", encoding="utf-8")
    row = _by_key(checks.everything(lab["cfg_path"], probe_watcher=False))["health:log"]
    assert row.status == "info" and row.title == "2 warnings/errors in the log since 2026-10-15 09:01:00"
    assert "FragPipe failed" in row.detail and len(row.detail) <= 160 and row.fix


def test_paused_searches_and_attention_items(lab):
    log_dir = lab["cfg"].log_dir
    worker.pause(log_dir, by="test")
    for i in range(4):
        attention.raise_item(log_dir, "search_failed", f"Job {i} failed", "FragPipe stopped", key=f"job{i}")
    by = _by_key(checks.everything(lab["cfg_path"], probe_watcher=False))
    assert by["attention:paused"].status == "warn" and by["attention:paused"].action == "resume_searches"
    assert by["attention:paused"].fix == "Jobs → Resume searches."
    items = by["attention:items"]
    assert items.status == "warn" and items.title == "4 things need you" and items.action == "show_attention"
    assert "Job 0 failed" in items.detail and "(4)" in items.detail and "Job 3 failed" not in items.detail


def test_one_broken_source_does_not_break_the_list(lab, monkeypatch):
    from ionomos import library

    def boom(*a, **k):
        raise RuntimeError("catalog on fire")

    monkeypatch.setattr(library, "update_check", boom)
    rows = checks.everything(lab["cfg_path"], probe_watcher=False)
    by = _by_key(rows)
    assert by["library:error"].status == "fail" and "catalog on fire" in by["library:error"].detail
    assert by["library:error"].action == "report_problem"
    assert {r.source for r in rows} == set(checks.SOURCES)


def test_text_groups_by_source(lab):
    rows = checks.everything(lab["cfg_path"], probe_watcher=False)
    out = checks.text(rows)
    for title in checks.SOURCES.values():
        assert f"\n{title}\n" in f"\n{out}\n"
    assert "✗" in out and "→ " in out


@pytest.mark.parametrize("broken", [True, False])
def test_doctor(lab, capsys, monkeypatch, broken):
    if not broken:
        monkeypatch.setattr(checks, "everything", lambda *a, **k: [checks.Row("setup:a", "setup", "ok", "Fine"),
                                                                     checks.Row("health:b", "health", "ok", "Good")])
    rc = main(["--config", str(lab["cfg_path"]), "doctor"])
    out = capsys.readouterr().out
    assert "check everything" in out
    if broken:
        assert rc == 1 and "Setup" in out and "Needs you" in out and "problems:" in out
    else:
        assert rc == 0 and "All 2 checks pass." in out and "Health" in out
