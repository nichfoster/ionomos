# Proteomics PC — facts from the 2026-09-15 inventory

Source: [`reference/pc-inventory/2026-09-15/`](../reference/pc-inventory/2026-09-15/)
(`system_report.txt`, `software_path_candidates.csv`), collected 15:24 on
2026-09-15 with the script now at `tools/inventory/collect_pc_inventory.ps1`.

## Machine

| | |
|---|---|
| Hostname | `DESKTOP-NI5V92B` |
| Account | `DESKTOP-NI5V92B\Daniel Nomura` (local; WORKGROUP, not domain-joined) |
| OS | Windows 11 Pro for Workstations, build 10.0.26200 |
| CPU | Xeon Silver 4216 — 16 cores / 32 threads @ 2.1 GHz |
| RAM | 64 GB |
| PowerShell | 5.1 (no pwsh 7) |
| C: | 953 GB NVMe (RAID), **99 GB free** — OS + FragPipe + `C:\Fragpipe_General` |
| D: | 18.6 TB WD Elements **USB HDD**, 14.5 TB free — per-user archive (`D:\<Name>\`) |
| E: | 30 GB USB flash, "Proteome Discoverer 2.5" installer media |
| Network drives | none mapped at inventory time. `C:\Proteomics_File_Sharing` (the Eclipse share) does not exist yet / wasn't verifiable |

## Software

| Software | Version | Location / notes |
|---|---|---|
| FragPipe | **24.0** | `C:\FragPipe\FragPipe-24.0\` — `javaw.exe` running from `…\jre\bin\`, so it has a bundled JRE. Exact launcher path (`fragpipe\bin\fragpipe.exe`?) **not captured** |
| FragPipe | 23.1 | `C:\FragPipe\FragPipe-23.1\` |
| FragPipe | 22.0 | unzipped in Downloads (`FragPipe-jre-22.0\`), has stock `workflows\isoDTB-ABPP.workflow`, MSFragger 4.1, IonQuant 1.10.27 |
| DIA-NN | 2.3.2 Academia | installed (MSI); path not captured |
| Proteome Discoverer | 2.5.0.400 | `C:\Program Files\Thermo\Proteome Discoverer 2.5` |
| Python | 3.14.5 (user) | `C:\Users\Daniel Nomura\AppData\Local\Python\pythoncore-3.14-64` |
| Python | 3.9.13 | system-wide; `py` launcher at `C:\Windows\py.exe` |
| `python` on PATH | — | resolves to the **WindowsApps store stub** (`0.0.0.0`). Do not rely on `python`; use `py -3.14` or the absolute path |
| Java on PATH | — | **not found** (FragPipe uses its own JRE — fine) |
| R / Rscript | — | **not installed** → the lab's R scripts are currently run… somewhere else? Or R was removed. Either way: port to Python |
| git | — | not installed. Deploy by copying / `pip install` from a wheel, or install git |
| .NET | 10.0 | present (irrelevant) |

## How the lab currently works (inferred)

- Working area appears to be `C:\Fragpipe_General\<user>\<experiment>\` (both
  R scripts and the "representative experiment" answers point there). **Not
  scanned** by the inventory — needs the fixed script.
- `D:\<user>\` holds completed experiments with raws, mzML/mzBIN conversions,
  and FragPipe output — ~15 user folders: Aman, Carolyn, Chris, CMZ, EJQ,
  Isaac, Kosuke, Melissa, Melody, Phillip, Qian, RUI, Sheena, Taylor_Elements,
  Thang, Yun, Zoe.
- Folder naming is ad hoc (see NAMING_CONVENTION.md). Raw naming is consistent:
  `<prefix>_<rep>_<fraction>.raw`.
- FragPipe outputs seen: per-replicate subfolders (`EJQ_2_027_1\`) with
  `*_ion_label_quant.tsv`, `*_modified_peptide_label_quant.tsv`,
  `*_protein_label_quant.tsv`, `interact-*.pep.xml`; top-level
  `combined_*.tsv`, `fragpipe.workflow`, `fragpipe.job`,
  `fragpipe-files.fp-manifest`, `filelist_ionquant.txt`, `modmasses_ionquant.txt`.
- People re-run: `Fragpipe-DIANN_RERUN`, `Redo\`, `Again (see this one)\`
  folders exist. The watcher's "never overwrite" rule matters.
- Lab SOP docs and R scripts live on the Desktop of the shared account.
- The user-entered "planned watcher folder" was `C:\Fragipe_Auto` (sic —
  typo). Proposal: `C:\Fragpipe_Auto`.

## Known defects in the 2026-09-15 inventory

1. **All `PATH:` sections report `DOES NOT EXIST YET`** because the paths were
   entered with surrounding double-quotes and the script passed them verbatim
   to `Test-Path -LiteralPath`. Evidence: `C:\FragPipe\FragPipe-24.0` "doesn't
   exist" yet `javaw.exe` is running from inside it. The `NETWORK PATH
   CONNECTIVITY` table shows the quotes. Fixed in `tools/inventory/`.
2. Because of (1), **no experiment manifests or per-folder file-type summaries
   were produced**, and the FragPipe install tree was not listed.
3. `software_path_candidates.csv` only matched names containing
   `FragPipe|MSFragger|IonQuant|Philosopher|isoDTB|DIA-NN|Spectronaut`, so it is
   biased toward isoDTB experiments; TMT/DIA/other folders are under-represented.
4. Search roots did not include `C:\Fragpipe_General` or `C:\FragPipe`.

## Still needed from the PC (re-run the fixed inventory script)

- [ ] Listing of `C:\FragPipe\FragPipe-24.0\` — launcher name, `workflows\`, `tools\`
- [ ] Listing of `C:\Fragpipe_General\` (users and one full experiment tree)
- [ ] Does `C:\Proteomics_File_Sharing` exist; is it a local folder shared out
      over SMB, or a mapped drive from the Eclipse PC? Which direction is the share?
- [ ] DIA-NN 2.3.2 install path
- [ ] Where FASTA databases live (probably inside a `fragpipe.workflow` as
      `database.db-path=…`; grep an existing one)
- [ ] Whether the account has an unattended-login / Task Scheduler policy
      (the watcher must survive logout/reboot)
- [ ] Power settings: does the PC sleep? (Would kill long runs.)
