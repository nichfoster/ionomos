# Security policy

## Reporting a vulnerability

Please report it privately, not in a public issue: on GitHub, open the
repository's **Security** tab and choose **Report a vulnerability** (GitHub's
private vulnerability reporting). Only the maintainer (@nichfoster) sees the
report. Say which version (`ionomos --version`), how you ran it (the Windows
installer, `pip install`, or a git checkout) and how to reproduce it. Leave
out lab data you may not share; a demo folder (`ionomos demo`) or a made-up
table is enough for most reports.

There is one maintainer and no guaranteed response time. You will get an
answer when the report has been read, and a fix goes into the next release
with a CHANGELOG line that credits you unless you ask not to be named.

## Supported versions

Only the latest minor release (the newest `0.x` on the
[Releases page](https://github.com/nichfoster/ionomos/releases)) gets security
fixes. The app offers its own updates. The pip package is not on PyPI yet
(checked 2026-10-28); install or update it from GitHub with
`pip install -U "git+https://github.com/nichfoster/ionomos#subdirectory=ionomos"`.

## What Ionomos is, for judging a report

- **No server.** Ionomos is a command line, a Tk app and a watcher that runs
  as a Windows logon task; it listens on no network port. (The one exception,
  `ionomos ask-eval --scripted`, serves a scripted model on 127.0.0.1 while
  that command runs.)
- **The watcher runs on a shared lab PC, often as a shared account.** It reads
  `config.yaml` (whoever can write that file decides which programs the
  watcher starts: FragPipe, DIA-NN and the other engines are paths in it) and
  moves folders that anyone with access to the inbox drops there.
- **Folder, file and sample names come from users** and end up in logs, in
  the commands Ionomos builds for the search engines, and in the HTML report.
- **The assistant only proposes.** It talks only to a model on the same PC
  (localhost or a loopback address; anything else is refused before a byte is
  sent, D57), it cannot change anything itself, and only a person's Confirm
  button in the app applies what it proposes (D75).
- **Network use.** The installed app asks GitHub's public API for the newest
  release (a version string goes out, nothing else) and checks the installer's
  size and SHA-256 before running it. Enrichment downloads public gene-set
  libraries once. FASTA downloads from UniProt happen only when asked, and
  messages go only to the webhooks and mail server set in `config.yaml`
  (`notify:`). Your data is never uploaded. The environment variable
  `IONOMOS_OFFLINE=1` switches off the update check and the downloads.

In scope, for example:

- a name (sample, condition, file, folder) that runs script in the report
  (`report.html`) or reaches a shell or an engine's command line as more than
  a name;
- a drop in the inbox that makes Ionomos delete, overwrite or move something
  outside the folders it owns, or a path that escapes them;
- the troubleshooting bundle (D63): a lab name, user name or path that
  survives anonymisation, or a leak its check before the zip is written misses;
- the assistant reaching a non-local address, or a change applied without a
  person's Confirm;
- the update path running an installer whose digest does not match.

Out of scope: someone who can already edit `config.yaml` or the program's
folder (they control what runs, by design); the search engines themselves
(FragPipe, DIA-NN, MaxQuant, Sage: report those upstream); denial of service
from a huge or malformed table on your own PC.

What each part protects against, and what it does not, is in
[docs/SECURITY_NOTES.md](docs/SECURITY_NOTES.md) (with the code and the test
that attacks it).

## The project's own rules that matter here

These are tested on every push (`help/safety.md`, `ionomos help safety`):
nothing destructive (intake moves, never deletes; nothing is overwritten; a
failure leaves everything in place); a bundle is saved on the PC, never sent,
and its names are replaced; no network use beyond the list above. A report
that breaks one of these rules is a security report even without an attacker.
