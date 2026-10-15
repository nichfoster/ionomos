"""Adversarial tests of the security / privacy boundaries (docs/SECURITY_NOTES.md).

Each block names the boundary it attacks: a bundle someone else made (unpack, inspect), the YAML loaders, the
notification channels, the command lines Ionomos builds, the assistant's tools, the library scan and the update
path; then the secrets a lab plants in config.yaml and its logs, searched for in everything Ionomos writes or
sends. No test opens a socket to anything but 127.0.0.1."""
import http.server
import json
import os
import shutil
import threading
import warnings
import zipfile
from pathlib import Path

import pytest
import yaml

from ionomos import bundle, names, notify

# ------------------------------------------------- a bundle from someone else ----


def _zip(path: Path, members: dict, jobs=None, level="validate") -> Path:
    """A zip that looks like a bundle: BUNDLE.json (with `jobs`) plus `members` {name: bytes | str}."""
    manifest = {"format": 1, "level": level, "jobs": jobs or [], "files": []}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # "Duplicate name": the point of one test
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(names.BUNDLE_MANIFEST, json.dumps(manifest))
            for n, data in members.items() if isinstance(members, dict) else members:
                z.writestr(n, data)
    return path


def _files_under(root: Path) -> set[str]:
    """Every file under `root` but the BUNDLE.json that unpack copies into _bundle/."""
    return ({p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and p.name != names.BUNDLE_MANIFEST}
            if root.exists() else set())


CLIMBING = ["../outside.txt", "..\\outside_win.txt", "/abs.txt", "\\abs_win.txt", "C:/Windows/Temp/drive.txt",
            "C:drive_relative.txt", "\\\\server\\share\\unc.txt", "//server/share/unc2.txt",
            "jobs/../../../up.txt", "_bundle/../../up2.txt"]
ODD = ["_bundle/a.txt:stream", "_bundle/CON", "_bundle/nul.txt", "_bundle/COM1.log", "_bundle/trailing.",
       "_bundle/trailing ", "_bundle/ctl\x01.txt"]


def test_unpack_keeps_every_member_inside_the_folder(tmp_path):
    z = _zip(tmp_path / "evil.zip", {**{n: b"x" for n in CLIMBING + ODD}, "notes/ok.txt": b"fine"})
    dest = tmp_path / "a" / "b" / "un"
    before = _files_under(tmp_path)
    out = bundle.unpack(z, dest)
    assert _files_under(dest) == {"_bundle/notes/ok.txt"}
    assert _files_under(tmp_path) - before == {"a/b/un/_bundle/notes/ok.txt"}  # nothing anywhere else
    said = " ".join(out["notes"])
    assert f"left out {len(CLIMBING + ODD)} file(s)" in said


@pytest.mark.parametrize("folder", ["../../escaped", "..", ".", "C:/Windows/Temp/escaped", "C:escaped",
                                    "\\\\server\\share", "a/b", "_bundle", "_LAB", "config.yaml", "", None, 7])
def test_unpack_refuses_a_job_folder_name_from_the_manifest_that_is_not_one_plain_name(tmp_path, folder):
    """BUNDLE.json names each job's folder; a sender who writes ../../x there must not choose where files go."""
    jobs = [{"arc": "jobs/exp001", "folder": folder}, {"arc": "jobs/exp002", "folder": "exp002"}]
    z = _zip(tmp_path / "evil.zip", {"jobs/exp001/x.tsv": b"x", "jobs/exp002/y.tsv": b"y"}, jobs)
    dest = tmp_path / "w" / "un"
    before = _files_under(tmp_path)
    out = bundle.unpack(z, dest)
    if folder in ("", None):  # no folder: the arc's own last part, which is a plain name
        assert _files_under(dest) == {"exp001/x.tsv", "exp002/y.tsv"}
        return
    assert _files_under(dest) == {"exp002/y.tsv"}  # the bad job is left out whole, not spilled into _bundle
    assert _files_under(tmp_path) - before == {"w/un/exp002/y.tsv"}
    assert out["experiments"] == [dest / "exp002"]
    assert any("left out job 'jobs/exp001'" in n for n in out["notes"])


def test_unpack_refuses_a_job_arc_that_climbs_and_two_jobs_with_one_folder(tmp_path):
    jobs = [{"arc": "jobs/..", "folder": "x"}, {"arc": "jobs/a", "folder": "same"}, {"arc": "jobs/b", "folder": "SAME"}]
    z = _zip(tmp_path / "e.zip", {"jobs/a/1.tsv": b"1", "jobs/b/2.tsv": b"2"}, jobs)
    out = bundle.unpack(z, tmp_path / "un")
    assert _files_under(tmp_path / "un") == {"same/1.tsv"}
    assert sum("left out job" in n for n in out["notes"]) == 2


def test_unpack_two_members_with_one_name_is_a_note_not_a_traceback(tmp_path):
    z = _zip(tmp_path / "dup.zip", [("_bundle_x/a.txt", b"first"), ("_bundle_x/a.txt", b"second"),
                                    ("_bundle_x/A.txt", b"third")])
    out = bundle.unpack(z, tmp_path / "un")
    assert [p.read_bytes() for p in (tmp_path / "un").rglob("*.txt")] == [b"first"]
    assert any("left out 2 file(s)" in n for n in out["notes"])


def _with_declared_size(path: Path, member: str, size: int, data: bytes = b"\0" * 1000) -> Path:
    """A zip whose central directory says `member` unpacks to `size` bytes (a 10 GB bomb without writing one):
    zipfile writes the directory from its ZipInfo list at close, so the size is changed just before."""
    with zipfile.ZipFile(path, "a" if path.exists() else "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(member, data)
        z.filelist[-1].file_size = size
    return path


def test_unpack_refuses_a_10_gb_zip_bomb_before_writing_anything(tmp_path):
    z = _zip(tmp_path / "bomb.zip", {"_bundle_x/ok.txt": b"ok"})
    _with_declared_size(z, "_bundle_x/bomb.tsv", 10 << 30)
    with zipfile.ZipFile(z) as zz:
        assert zz.getinfo("_bundle_x/bomb.tsv").file_size == 10 << 30
    dest = tmp_path / "un"
    with pytest.raises(bundle.BundleError, match="zip bomb"):
        bundle.unpack(z, dest)
    assert not dest.exists()
    from ionomos import cli

    assert cli.main(["bundle", "unpack", str(z), str(tmp_path / "un2")]) == 1


def test_unpack_refuses_more_than_the_disk_has_free(tmp_path, monkeypatch):
    z = _zip(tmp_path / "big.zip", {"_bundle_x/t.tsv": b"a\tb\n" * 1000})
    real = shutil.disk_usage
    monkeypatch.setattr(shutil, "disk_usage", lambda p: real(p)._replace(free=bundle.FREE_MARGIN_BYTES + 100))
    with pytest.raises(bundle.BundleError, match="free"):
        bundle.unpack(z, tmp_path / "un")
    assert not (tmp_path / "un").exists()


def test_inspect_refuses_a_manifest_that_says_it_is_gigabytes(tmp_path):
    z = tmp_path / "m.zip"
    _with_declared_size(z, names.BUNDLE_MANIFEST, 10 << 30, b"{}")
    with pytest.raises(bundle.BundleError, match="not a bundle Ionomos made"):
        bundle.inspect(z)
    with zipfile.ZipFile(tmp_path / "list.zip", "w") as zz:
        zz.writestr(names.BUNDLE_MANIFEST, "[1, 2]")
    with pytest.raises(bundle.BundleError, match="not a JSON object"):
        bundle.inspect(tmp_path / "list.zip")


def test_unpack_a_damaged_member_is_a_bundle_error(tmp_path):
    z = _zip(tmp_path / "bad.zip", {"_bundle_x/t.tsv": os.urandom(4000)})
    raw = bytearray(z.read_bytes())
    at = raw.find(b"_bundle_x/t.tsv") + len("_bundle_x/t.tsv") + 50  # inside the deflated data
    raw[at:at + 64] = bytes(64)
    z.write_bytes(bytes(raw))
    with pytest.raises(bundle.BundleError, match="damaged"):
        bundle.unpack(z, tmp_path / "un")


PWNED = "IONOMOS_PWNED.txt"


def _bundle_config(tmp_path: Path, text: str) -> dict:
    z = _zip(tmp_path / "cfg.zip", {"config.yaml": text})
    return bundle.unpack(z, tmp_path / "un")


def test_unpacked_config_with_a_python_tag_runs_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out = _bundle_config(tmp_path, f"paths: {{}}\nx: !!python/object/apply:os.system ['echo x > {PWNED}']\n")
    assert out["config"] is None and any("does not load" in n for n in out["notes"])
    assert not list(tmp_path.rglob(PWNED))


def test_unpacked_config_keeps_no_program_the_sender_named(tmp_path):
    cfg = {"paths": {"inbox": "x"}, "methods": {
        "DIA": {"engine": "diann", "data_type": "DIA", "workflow": "DIA.workflow", "fasta": "h.fas",
                "diann_exe": "C:/evil/diann.exe"},
        "LFQ": {"engine": "maxquant", "data_type": "DDA", "workflow": "x.xml", "fasta": "h.fas",
                "maxquant_exe": "C:/evil/MaxQuantCmd.exe"}},
        "fragpipe": {"config_tools_folder": "\\\\evil\\tools", "config_python": "C:/evil/python"},
        "library": {"roots": ["\\\\evil\\share"]},
        "notify": {"enabled": True, "slack": "https://hooks.slack.com/services/T0/B0/x"},
        "assistant": {"enabled": True, "base_url": "http://127.0.0.1:1/v1"}}
    out = _bundle_config(tmp_path, yaml.safe_dump(cfg))
    written = (tmp_path / "un" / "config.yaml").read_text(encoding="utf-8")
    for word in ("evil", "notify", "assistant", "library", "hooks.slack"):
        assert word not in written, word
    assert out["config"] == tmp_path / "un" / "config.yaml" or any("does not load" in n for n in out["notes"])


# ------------------------------------------------------------- notifications ----

PASSWORD = "Sup3rS3cretPw"


@pytest.mark.parametrize("url", [f"https://bob:{PASSWORD}@hooks.example.org/services/x",
                                 f"https://{PASSWORD}@hooks.example.org/x", f"http://bob:{PASSWORD}@127.0.0.1:9/x"])
def test_a_webhook_address_with_a_password_is_refused_without_showing_it(url, monkeypatch):
    """urllib reads user:password@host as a host with a port named 'password@host' and says so in its error, which
    the log and `ionomos notify-test` print: the address is refused when it is read instead."""
    with pytest.raises(notify.NotifyError, match="user name or password") as exc:
        notify.settings_from({"enabled": True, "webhook": {"url": url}})
    assert PASSWORD not in str(exc.value)
    monkeypatch.setenv("IONOMOS_TEST_HOOK", url)  # the same address from the environment, read when sending
    s = notify.settings_from({"enabled": True, "timeout_seconds": 1, "slack": {"url_env": "IONOMOS_TEST_HOOK"}})
    (r,) = notify.send_test(s)
    assert not r.ok and "user name or password" in r.detail and PASSWORD not in r.detail


def test_a_partial_address_in_an_error_is_hidden_too():
    s = notify.settings_from({"enabled": True,
                              "webhook": {"url": "https://lab.example.org/hooks/ionomos/KEY-0123456789"}})
    assert notify.scrub("404 for /hooks/ionomos/KEY-0123456789", notify.secrets(s)) == "404 for ***"


class _Recorder(http.server.BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - the http.server name
        self.server.got.append(self.path)
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.send_response(self.server.code if self.server.location else 200)
        if self.server.location:
            self.send_header("Location", self.server.location)
        self.end_headers()

    do_GET = do_POST  # noqa: N815

    def log_message(self, *a):
        pass


@pytest.fixture
def servers():
    made = []

    def start(location: str = "", code: int = 302):
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
        srv.daemon_threads, srv.got, srv.location, srv.code = True, [], location, code
        srv.url = f"http://127.0.0.1:{srv.server_address[1]}"
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        made.append(srv)
        return srv

    yield start
    for srv in made:
        srv.shutdown()
        srv.server_close()


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
def test_a_hook_that_redirects_to_a_private_address_is_not_followed(servers, code):
    """The second server stands for 169.254.169.254 (a cloud metadata service) or a printer on the lab network."""
    inside = servers()
    hook = servers(location=inside.url + "/latest/meta-data", code=code)
    s = notify.settings_from({"enabled": True, "timeout_seconds": 2, "webhook": {"url": hook.url + "/h"}})
    (r,) = notify.send_test(s)
    assert not r.ok and str(code) in r.detail
    assert hook.got == ["/h"] and inside.got == []


def test_slack_shows_a_name_or_reason_as_written_and_pings_nobody():
    msg = notify.Message("Ionomos: a/b failed", (("Reason", "<!channel> look at <https://x.example|this> & fix"),))
    text = notify.slack_payload(msg)["text"]
    assert "<" not in text and ">" not in text
    assert "&lt;!channel&gt; look at &lt;https://x.example|this&gt; &amp; fix" in text


BROKEN_CONFIGS = [  # each fails to parse, so file_secrets() knows no value and the text alone must be enough
    "notify: {enabled: true, webhook: {url: https://example.org/hook/FLOW-SECRET}}\n  broken: [\n",
    "notify:\n  email: {host: h, username: u, password: FLOW-SECRET-2, from: a@b.c}\n  oops: [\n",
    "notify:\n  webhook:\n    url: >-\n      https://example.org/hook/FOLDED-SECRET\n  oops: [\n",
    "notify:\n  email:\n    password: |\n      BLOCK-SECRET\n  oops: [\n",
    "notify:\n  webhook:\n    url:\n      https://example.org/NEXT-LINE-SECRET\n    url_env: X\n  oops: [\n",
    "notify:\n  hooks:\n    - url: https://example.org/LIST-SECRET\n  oops: [\n",
]


@pytest.mark.parametrize("text", BROKEN_CONFIGS)
def test_a_config_that_does_not_parse_is_still_redacted(text, tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(text, encoding="utf-8")
    assert notify.file_secrets(p) == []
    out = notify.redact_config_text(text, notify.file_secrets(p))
    assert "SECRET" not in out and "***" in out
    assert out.count("\n") == text.count("\n")  # line for line: the report still shows where the error is


def test_redaction_leaves_the_rest_of_the_config_readable():
    text = ("assistant:\n  base_url: http://127.0.0.1:11434/v1\nnotify:\n  webhook:\n    url:\n      https://x/SECRET\n"
            "    url_env: HOOK_URL\n  email: {host: smtp.example.org, port: 587}\n")
    out = notify.redact_config_text(text)
    assert "base_url: http://127.0.0.1:11434/v1" in out and "url_env: HOOK_URL" in out
    assert "{host: smtp.example.org, port: 587}" in out and "SECRET" not in out


# ------------------------------------------------------- command lines Ionomos builds ----

ODD_ARGS = ["C:/R&D/users/x.workflow", "C:/a|b", "C:/in<out", "C:/out>in", "C:/x^y", "C:/100%PATH%x",
            'C:/say"hi', "C:/line\nbreak", "C:/line\rbreak"]


@pytest.mark.parametrize("arg", ODD_ARGS)
def test_a_batch_launcher_is_never_started_with_an_argument_cmd_would_read_as_a_command(arg):
    from ionomos import fragpipe

    why = fragpipe.batch_problem(["C:/FragPipe/bin/fragpipe.bat", "--headless", "--workflow", arg])
    assert "batch file" in why and "Nothing was started" in why and "Rename" in why
    assert fragpipe.batch_problem(["C:/R&D/FragPipe/bin/fragpipe.cmd", "--help"])  # the launcher's own path too
    # a program that is not a batch file is started without a shell: every argument reaches it as written
    assert fragpipe.batch_problem(["C:/DIA-NN/diann.exe", "--cfg", arg]) == ""
    assert fragpipe.batch_problem(["/opt/fragpipe/bin/fragpipe", arg]) == ""
    assert fragpipe.batch_problem(["C:/Fragpipe_Auto/users/EJQ/20260902_EJQ_x/fragpipe.bat", "--workflow",
                                   "C:/Fragpipe_Auto/users/EJQ/20260902_EJQ_isoDTB_x-1.2_3/ionomos_run/a.workflow"]) == ""


def test_a_search_in_a_folder_with_an_ampersand(tmp_path, monkeypatch):
    """The testbed's fake FragPipe is fragpipe.bat on Windows: under C:/.../R&D/ cmd.exe would run `D/...` as a
    command. The job fails before anything starts, saying what to rename. Elsewhere (fragpipe.sh, no shell) it runs."""
    from ionomos import testbed
    from ionomos.config import load
    from ionomos.intake import intake
    from ionomos.ledger import Ledger
    from ionomos.worker import Worker

    monkeypatch.setenv("IONOMOS_FAKE_FP_SECONDS", "0")
    cfg = load(testbed.init(tmp_path / "R&D"))
    ledger = Ledger(cfg.database)
    d = cfg.inbox / "20260301_Isaac_DIA_BRD4"
    (d / "raw").mkdir(parents=True)
    for r in ("DMSO_1", "DMSO_2", "Drug_1", "Drug_2"):
        (d / "raw" / f"{r}.raw").write_bytes(b"\0" * 256)
    assert intake(d, cfg, ledger).value == "queued"
    Worker(cfg, ledger).run_once()
    job = ledger.list()[-1]
    if Path(str(cfg.fragpipe_exe)).suffix.lower() in (".bat", ".cmd"):
        assert job.status == "failed" and "batch file" in job.reason and "'&'" in job.reason
        from ionomos import fragpipe

        out = Path(job.dest_dir) / fragpipe.WORKDIR
        assert not out.exists() or not any(out.iterdir())  # FragPipe never ran: it wrote nothing
    else:
        assert job.status == "done"
    ledger.close()


def test_preflight_never_starts_a_batch_launcher_in_such_a_folder(tmp_path):
    from ionomos import preflight

    bat = tmp_path / "R&D" / "fragpipe.bat"
    bat.parent.mkdir()
    bat.write_text("@echo off\r\necho started > started.txt\r\n", encoding="ascii")
    code, text, timed_out = preflight._start([str(bat), "--help"], {}, tmp_path / "out.log", tmp_path, 30)
    assert code is None and "batch file" in text and not timed_out
    assert not (tmp_path / "started.txt").exists()


QUOTES = ["O'Brien", "O\u2019Brien", "a\u2018b\u201ac\u201bd", "x';Start-Process calc;'", "$(calc)", "a&b;c"]


@pytest.mark.parametrize("name", QUOTES)
def test_the_desktop_shortcut_script_keeps_a_profile_path_one_string(name, tmp_path):
    import base64

    from ionomos import service

    lit = service.ps_literal(f"C:/Users/{name}/Desktop/Ionomos.lnk")
    body = lit[1:-1]
    assert lit[0] == lit[-1] == "'"
    for q in "'\u2018\u2019\u201a\u201b":  # every quote PowerShell ends a string at comes in pairs
        assert body.replace(q * 2, "").count(q) == 0
    script = base64.b64decode(service.shortcut_script(Path(f"C:/Users/{name}/Desktop/x.lnk"),
                                                      Path("C:/Program Files/Ionomos/Ionomos.exe"))).decode("utf-16-le")
    assert script.count("CreateShortcut(") == 1 and "$s.Save()" in script


@pytest.mark.skipif(os.name != "nt" or not shutil.which("powershell"), reason="PowerShell parses the literal")
def test_powershell_reads_the_literal_back_as_written():
    import subprocess

    from ionomos import service

    for name in QUOTES:
        text = f"C:\\Users\\{name}\\Desktop"
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                            f"[Console]::OutputEncoding=[Text.Encoding]::UTF8; Write-Output {service.ps_literal(text)}"],
                           capture_output=True, timeout=60)
        assert r.stdout.decode("utf-8").strip() == text, (name, r.stdout, r.stderr)


def test_the_startup_task_is_not_installed_with_a_percent_in_its_command(tmp_path, monkeypatch):
    import subprocess
    import types

    from ionomos import service

    monkeypatch.setattr(service, "os", types.SimpleNamespace(name="nt", environ=os.environ))
    monkeypatch.setattr(service.subprocess, "CREATE_NEW_PROCESS_GROUP", 0, raising=False)
    calls = []
    monkeypatch.setattr(service.subprocess, "run",
                        lambda cmd, *a, **k: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""))
    ok, msg = service.install_task(tmp_path / "100%USERNAME%" / "config.yaml")
    assert not ok and "%" in msg and "variable" in msg and calls == []
    ok, _msg = service.install_task(tmp_path / "R&D" / "config.yaml")  # no shell between Task Scheduler and Ionomos
    create = [c for c in calls if c[:2] == ["schtasks", "/Create"]]
    assert ok and len(create) == 1 and str(tmp_path / "R&D" / "config.yaml") in create[0][create[0].index("/TR") + 1]
