# Security notes: a threat model per component

`SECURITY.md` says what Ionomos is and how to report a problem. This page says,
for each part that faces something it does not control, **what is protected,
against whom, and what is not**, with the code that does it and the test that
attacks it (`ionomos/tests/test_security.py` unless named). Written by the
round-4 security audit (2026-10-15); keep it in step with the code.

## Who can do what

| Who | Controls | Trusted? |
|---|---|---|
| Whoever can edit `config.yaml`, the program folder or the account | which programs run, every folder, every channel | yes: they own the PC (out of scope in SECURITY.md) |
| Whoever can drop into the inbox (often a network share) | folder and file names, `experiment.yaml`, any extra file in the drop (a `.workflow`, a `fragpipe-ui.cache`, a link) | **no** |
| The search engines' output | console logs, result tables, sample names | no: text, never instructions |
| Someone who sends a bundle to the maintainer | every byte of the zip, including `BUNDLE.json` | **no** |
| The local model behind the assistant | its replies and tool calls | no: it only proposes |
| GitHub / UniProt, over HTTPS | release metadata, FASTA, stock workflows | the TLS certificate is the trust anchor |

## The troubleshooting bundle, made on the lab's PC (`bundle.py`, D63)

- **Protected**: the lab's names (users, PC, experiments, samples, raw files, e-mail and IP
  addresses, home folders) against whoever receives the zip; secrets (webhook addresses, the SMTP
  password, from the file, the environment and the last 30 `config-backups/`) against the same.
  `learn()` collects names, `_Scrub` rewrites every line, `verify()` searches the finished zip for
  every original and a hit fails the bundle (`BundleLeak`, no zip left). The key file is written next
  to the zip with `open(..., "x")`, never inside it.
- **How**: `notify.file_secrets` / `redact_config_text` / `scrub` (secrets), `Anonymiser` (names),
  `_write` writes a `.part` file and renames it only after the check (`create`).
- **Not protected**: a name Ionomos never learns (typed only inside a free-text log line by a third
  program) is not replaced: the leak check can only search for names it knows. `--no-anonymise`
  keeps names by design (secrets still go). Protein and gene identifiers are kept on purpose.
  Tests: `test_bundle.py`, `test_bundle_extras.py`, `test_notify.py::test_diagnostics_and_bundle_never_hold_a_secret`.

## The bundle, read by the maintainer (`bundle.inspect`, `bundle.unpack`)

- **Protected**: the maintainer's disk and files against a hostile zip. Every member name goes
  through `_safe_member` (no absolute, drive, UNC, `..`, colon / NTFS stream, Windows device name,
  trailing dot or space, control character); a job's folder name from `BUNDLE.json` must be one plain
  name (`_safe_folder`), else the job is left out whole; two members that meet under Windows'
  case-insensitive names are a note; nothing is written over (`open(..., "xb")`). `_bomb_check`
  refuses, before the first write, a member over 1 GB that deflated more than 100:1 and a total above
  the free disk space less 1 GB; `inspect` refuses a `BUNDLE.json` / `build.json` over 64 MB. A damaged
  member is a `BundleError`, not a traceback. The unpacked `config.yaml` is `yaml.safe_load`ed and
  loses `notify:`, `assistant:`, `library:`, every `*_exe` of a method and FragPipe's tool / Python
  folders: analysing the unpacked bundle can't message, ask, scan or start the sender's programs.
- **Not protected**: `inspect --key` with a key file runs the name check over every member line by
  line; a member with no line break is read whole (the key comes from the lab, so the zip is the lab's
  own). Hash mismatches are reported, not refused (the maintainer decides).
  Tests: `test_unpack_*`, `test_inspect_refuses_*`, `test_unpacked_config_*`.

## Notifications (`notify.py`, D58)

- **Protected**: secrets against the log, `notify-test`'s output and diagnostics: every `Result.detail`
  is `scrub`bed with the full address, its path, query and user part (`_url_parts`); an address with
  `user:password@` is refused when read (urllib's error would quote the password). Where messages go
  against redirection: no redirect is followed (`_NoRedirect`; 301–308 tested against a second local
  server standing for 169.254.169.254); `http://` only to loopback. What is sent is `Message.fields`
  only: names (sanitised at intake to `[A-Za-z0-9._-]`), counts, the report's local path, the PC name;
  `include_names: false` sends the job number and status alone. Slack text escapes `& < >`, so no
  name or reason can `@channel` or make a link. A message kept for want of a connection (D131) is written to
  `<log_dir>/notify_state.json` as those fields only: the address and the password are read from the config
  when it is sent, never kept with it; at most 50 messages, 7 days.
- **Not protected**: an `https://` hook to a private address the config owner chose (they own the
  PC); the system proxy (`HTTPS_PROXY`) is used for hooks; Teams renders a little Markdown in a reason;
  e-mail goes over STARTTLS/SSL as configured (`security: none` refuses a password).
  `config.yaml` and its backups hold the values in plain text (D58).
  Tests: `test_a_webhook_address_with_a_password_*`, `test_a_hook_that_redirects_*`, `test_slack_*`,
  `test_a_config_that_does_not_parse_is_still_redacted`, `test_a_hook_address_replaced_last_week_*`,
  `test_without_internet_every_channel_fails_at_once_in_words_*` (no secret in any detail).

## Diagnostics, fingerprints, catalog, logs (`service.diagnostics`, `fingerprint.py`, `library.py`)

- **Protected**: secrets in `ionomos diagnose`, the report zip and the bundle (above, including a
  `config.yaml` that does not parse: flow style, list items and values on the next lines are redacted
  by text). The run fingerprint holds the command line, the launcher's environment (`JAVA_HOME` only)
  and parsed console lines, never config values; `library.json` holds paths and file facts.
- **Not protected**: names and paths in the plain (not anonymised) diagnostics, the fingerprint and
  the logs: they are the lab's own files; sending them on is the bundle's job.
  Test: `test_planted_secrets_are_in_nothing_ionomos_writes_or_sends` (a real job; the hook's body,
  fingerprint, status file, log folder, catalog, `ionomos check`, every assistant tool result).

## Command lines Ionomos builds (`fragpipe.py`, `preflight.py`, `service.py`, `fastasource.py`)

- **Protected**: no name reaches a shell as code. Every `subprocess` call takes an argument list, never
  `shell=True`. A drop's names are sanitised before they become a folder (`naming.sanitize`,
  `manifest._require_path_safe` for `user:`). Windows runs a `.bat` / `.cmd` (fragpipe.bat) through
  cmd.exe, which reads `& | < > ^ %` and `"` even inside a path: `fragpipe.batch_problem` refuses to
  start one (search and preflight) when any argument or its own path holds one or a line break, and
  the job fails at once saying what to rename. The Desktop shortcut's PowerShell is sent as
  `-EncodedCommand` with every path a `ps_literal` (`'` and the typographic quotes doubled: a profile
  like `C:\Users\O'Brien` stays one string). The startup task (`schtasks /TR`) runs without a shell;
  a command with `%` is refused because Task Scheduler expands `%NAME%`.
- **Not protected**: what a configured program does with its arguments (FragPipe, DIA-NN, MaxQuant,
  Sage, Philosopher are trusted, SECURITY.md); `maxquant.create_template` and `sage.run_job` start
  their programs without the batch check (real installs are `.exe`; the testbed's stand-ins are
  `.bat` under folders Ionomos names).
  Tests: `test_a_batch_launcher_is_never_started_*`, `test_a_search_in_a_folder_with_an_ampersand`,
  `test_preflight_never_starts_*`, `test_the_desktop_shortcut_*`, `test_powershell_reads_*`,
  `test_the_startup_task_*`.

## YAML and JSON

- **Protected**: every YAML file (config.yaml, experiment.yaml, a bundle's config, benchmark files) is
  read with `yaml.safe_load`: a `!!python/...` tag is a parse error, never an object. A test reads the
  source and fails on any other loader. JSON is `json.loads` (no code).
- **Not protected**: a huge YAML or JSON file is read whole (a denial of service on one's own PC).
  Tests: `test_no_yaml_loader_but_safe_load_anywhere`, `test_a_python_tag_*`,
  `test_unpacked_config_with_a_python_tag_runs_nothing`.

## Intake and `experiment.yaml` (`intake.py`, `manifest.py`, `naming.py`)

- **Protected**: intake moves, never deletes; the destination is `users_root / user / safe_name` with
  both parts sanitised; `user:` in experiment.yaml must name an existing user folder and can't be a path.
- **Protected (D105, #168)**: `workflow:` and `fasta:` in experiment.yaml are file names in
  `workflow_dir` / `fasta_dir`: a value with `/`, `\`, `:` or a bare `..` is refused at intake, so a
  drop can't choose a file elsewhere or make Windows sign in to a `//server/share`. `site_annotation:`
  takes a full path by design, but a network path is never opened (the analysis warns instead).
  Tests: `test_a_workflow_or_fasta_outside_the_lab_folders_is_rejected_and_never_used`,
  `test_a_site_annotation_on_a_network_path_is_never_opened`.

## The assistant (`assistant/*`, D49, D57, D75)

- **Protected**: the network: `client.local_problem` refuses anything but `localhost` / a loopback
  address before a byte is sent; no proxy, no redirect. The PC: tools are read-only wrappers with no
  path argument (a job is named by its id, its folder comes from the ledger); arguments are checked
  against the schema; an unknown tool is an error the model reads; a proposal changes nothing until a
  person presses Confirm (`actions.apply` is called only from the proposal window). Injection: every
  tool result is untrusted text, `clean`ed of control, bidi and zero-width characters, capped, and
  passed as a JSON `tool` message, so a log line can't become a system or user turn; citations are
  accepted only for what a tool returned. `log_tail` reads only the job's own run folder, by name, and
  not through a symbolic link.
- **Not protected**: what a person does with an answer; a model that is wrong but cites correctly
  (`ionomos ask-eval` measures it). The help text is trusted (it ships with Ionomos).
  Tests: `test_assistant*.py`, the injection scenarios in `assistant/scenarios/`,
  `test_a_log_line_reaches_the_model_only_as_data_and_changes_nothing`, `test_log_tail_does_not_follow_*`.

## FASTA sources and stock workflows (`fastasource.py`, D90)

- **Protected**: no network use unless a function is called, never with `IONOMOS_OFFLINE`, always
  with a timeout; only `rest.uniprot.org`, GitHub (`api.github.com`, `raw.githubusercontent.com`) and
  the GPM's cRAP are asked; a proteome id must look like `UP000005640` and a workflow name like
  `TMT10-MS3` (no path), so neither can choose where a file lands. Files are written as `.part` and
  renamed, never over an existing file (`-2`, `-3`). Philosopher is started as a program with an
  argument list (no shell).
- **Not protected**: the content is what UniProt / GitHub send over TLS; a downloaded proteome is
  bounded by time (an hour), not by size; the contaminant zip is read whole in memory (50 MB cap on the
  download, none on the deflated member).

## The library scan (`library.py`, D89)

- **Protected**: the scan only reads, at most `max_depth` folders deep and `time_limit_s` seconds;
  a root gets 5 s on a helper thread to say it exists (a share whose server is off took about 20 s
  per root). A network path written *inside* a file the walk finds (a workflow's
  `database.db-path`, a `fragpipe-ui.cache`) is never opened, by the scan, adopt or
  `fragpipe.import_workflow`: opening `\\server\share` makes Windows sign in to that server as the
  account Ionomos runs as, and the file may come from anyone's drop. Adopting copies, never moves.
- **Not protected**: a mapped network drive letter (`Z:`) looks local; `library.roots` that the
  config owner adds are walked as given (UNC included).
  Tests: `test_a_scan_never_opens_a_network_path_*`, `test_adopting_such_a_workflow_*`,
  `test_a_scan_root_on_a_server_that_does_not_answer_*`.

## Updates (`updates.py`, `app.install_update`)

- **Protected**: only an installer whose size and SHA-256 match the digest GitHub publishes runs:
  `download()` refuses a release without a digest, a non-`https://` address (urllib would also open
  `file:` and `ftp:`) and a name other than `Ionomos-Setup-<version>.exe`; the download is a `.part`
  file, renamed after the check and deleted on failure. A Setup file found in Downloads runs only when
  it matches the online release's digest. `check_latest` never raises on an odd answer.
- **Not protected**: the installer is not checked for an Authenticode signature; the digest comes from
  the same HTTPS answer as the address (trust = GitHub's TLS and the repository's owner). Someone who
  can write to the user's Downloads can plant a file, but it runs only if its digest matches.
  Tests: `test_loose_and_updates.py`, `test_an_installer_is_never_fetched_*`, `test_an_odd_answer_*`.
