"""FragPipe as it really behaves (D59): what Ionomos passes it, reads from it and does when it fails.

Nothing here runs FragPipe. The expectations come from FragPipe 24.0's source (checked against 23.1), its
headless tutorial, and logs users attached to FragPipe's issue tracker; the excerpts below are from those
(paths shortened). The testbed's fake FragPipe prints the same shapes, so the run-loop tests further down
exercise the parsers on them.
"""
import json
import os
import re
import subprocess
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from ionomos import fake_fragpipe, fingerprint, fragpipe, names, preflight, testbed
from ionomos import help as helpdoc
from ionomos.cli import main as cli_main
from ionomos.config import load
from ionomos.intake import intake
from ionomos.ledger import Ledger
from ionomos.worker import Worker, request_cancel


@pytest.fixture
def bed(tmp_path, monkeypatch):
    monkeypatch.setenv("IONOMOS_FAKE_FP_SECONDS", "0")
    for var in ("IONOMOS_FAKE_FP_MODE", "IONOMOS_FAKE_FP_STRICT"):
        monkeypatch.delenv(var, raising=False)
    cfg_path = testbed.init(tmp_path / "bed")
    cfg = load(cfg_path)
    return {"root": tmp_path / "bed", "cfg": cfg, "cfg_path": cfg_path, "ledger": Ledger(cfg.database)}


def _queue(bed, sample="iso_good") -> Path:
    folder = testbed.drop(bed["root"], sample)
    assert intake(folder, bed["cfg"], bed["ledger"]).value == "queued"
    return Path(bed["ledger"].list()[-1].dest_dir)


def _status(dest: Path) -> dict:
    return json.loads((dest / "ionomos.json").read_text(encoding="utf-8"))


def _console(dest: Path) -> str:
    return (dest / fragpipe.RUN_DIR / fragpipe.CONSOLE_LOG).read_text(encoding="utf-8")


# ---------------------------------------------------------------- a real log --

# The start of a FragPipe run as it prints it (layout: FragpipeRun.run; lines: a 23.0 log attached to FragPipe
# issue #2509 and a 24.0 log attached to #2602). {CONFIG} is FragPipe's echo of every workflow setting.
REAL_HEAD = r"""System OS: Windows 11, Architecture: AMD64
Java Info: 17.0.10, OpenJDK 64-Bit Server VM, Eclipse Adoptium
.NET Core Info: 6.0.27


Version info:
FragPipe version 24.0
DIA-Umpire version 2.3.3
diaTracer version 2.2.1
MSFragger version 4.4.1
Crystal-C version 1.5.10
MSBooster version 1.4.14
Percolator version 3.7.1
PTMProphet version 6.3.2
Metaproteomics version 1.0.1
Philosopher version 5.1.3-RC9
PTM-Shepherd version 3.0.11
IonQuant version 1.11.20
TMT-Integrator version 6.1.3
FragPipe-SpecLib version 0.1.58
DIA-NN version 1.8.2 beta 8
Skyline version N/A
Pandas version 2.3.3
Numpy version 1.26.4


LCMS files:
  Experiment/Group: EJQ_2_027_1
  (if "spectral library generation" is enabled, all files will be analyzed together)
  - C:\Fragpipe_General\EJQ\x\EJQ_2_027_1_1.raw	DDA


8 commands to execute:
CheckCentroid
C:\FragPipe\FragPipe-24.0\jre\bin\java.exe -Xmx48G -cp C:\FragPipe\FragPipe-24.0\lib/* org.nesvilab.fragpipe.util.CheckCentroid C:\Fragpipe_General\EJQ\x\EJQ_2_027_1_1.raw 28
WorkspaceCleanInit [Work dir: C:\Fragpipe_General\EJQ\x\fragpipe\EJQ_2_027_1]
C:\FragPipe\FragPipe-24.0\tools\Philosopher\philosopher-v5.1.3-RC9.exe workspace --clean --nocheck
MSFragger [Work dir: C:\Fragpipe_General\EJQ\x\fragpipe]
C:\FragPipe\FragPipe-24.0\jre\bin\java.exe -jar -Dfile.encoding=UTF-8 -Xmx48G C:\FragPipe\FragPipe-24.0\tools\MSFragger-4.4.1\MSFragger-4.4.1.jar C:\Fragpipe_General\EJQ\x\fragpipe\fragger.params C:\Fragpipe_General\EJQ\x\EJQ_2_027_1_1.raw
MSFragger move pepxml
C:\FragPipe\FragPipe-24.0\jre\bin\java.exe -cp C:\FragPipe\FragPipe-24.0\lib\fragpipe-24.0.jar org.nesvilab.utils.FileMove --no-err C:\Fragpipe_General\EJQ\x\EJQ_2_027_1_1.pepXML C:\Fragpipe_General\EJQ\x\fragpipe\EJQ_2_027_1\EJQ_2_027_1_1.pepXML
Percolator [Work dir: C:\Fragpipe_General\EJQ\x\fragpipe\EJQ_2_027_1]
C:\FragPipe\FragPipe-24.0\tools\percolator_3_7_1\windows\percolator.exe --only-psms --no-terminate --post-processing-tdc --num-threads 28
Percolator: Convert to pepxml [Work dir: C:\Fragpipe_General\EJQ\x\fragpipe\EJQ_2_027_1]
C:\FragPipe\FragPipe-24.0\jre\bin\java.exe -cp C:\FragPipe\FragPipe-24.0\lib/* org.nesvilab.fragpipe.tools.percolator.PercolatorOutputToPepXML
PhilosopherReport [Work dir: C:\Fragpipe_General\EJQ\x\fragpipe\EJQ_2_027_1]
C:\FragPipe\FragPipe-24.0\tools\Philosopher\philosopher-v5.1.3-RC9.exe report
IonQuant [Work dir: C:\Fragpipe_General\EJQ\x\fragpipe]
C:\FragPipe\FragPipe-24.0\jre\bin\java.exe -Xmx48G -Dlibs.bruker.dir=C:\FragPipe\FragPipe-24.0\tools\MSFragger-4.4.1\ext\bruker -Dlibs.thermo.dir=C:\FragPipe\FragPipe-24.0\tools\MSFragger-4.4.1\ext\thermo -cp C:\FragPipe\FragPipe-24.0\tools\IonQuant-1.11.20.jar ionquant.IonQuant --threads 28 --perform-ms1quant 1 --filelist C:\Fragpipe_General\EJQ\x\fragpipe\filelist_ionquant.txt
~~~~~~~~~~~~~~~~~~~~~~

Execution order:

    Cmd: [START], Work dir: [C:\Fragpipe_General\EJQ\x\fragpipe]
    Cmd: [CheckCentroid], Work dir: [C:\Fragpipe_General\EJQ\x\fragpipe]
    Cmd: [WorkspaceCleanInit], Work dir: [C:\Fragpipe_General\EJQ\x\fragpipe\EJQ_2_027_1]
    Cmd: [MSFragger], Work dir: [C:\Fragpipe_General\EJQ\x\fragpipe]
    Cmd: [Percolator], Work dir: [C:\Fragpipe_General\EJQ\x\fragpipe\EJQ_2_027_1]
    Cmd: [PhilosopherReport], Work dir: [C:\Fragpipe_General\EJQ\x\fragpipe\EJQ_2_027_1]
    Cmd: [IonQuant], Work dir: [C:\Fragpipe_General\EJQ\x\fragpipe]

~~~~~~~~~~~~~~~~~~~~~~

~~~~~~Sample of C:\Fragpipe_Auto\fasta\human.fas~~~~~~~
>rev_sp|A0A024RBG1|NUD4B_HUMAN Diphosphoinositol polyphosphate phosphohydrolase NUDT4B OS=Homo sapiens OX=9606 GN=NUDT4B PE=3 SV=1
>sp|P04406|G3P_HUMAN Glyceraldehyde-3-phosphate dehydrogenase OS=Homo sapiens OX=9606 GN=GAPDH PE=1 SV=3
~~~~~~~~~~~~~~~~~~~~~~

~~~~~~~~~ fragpipe.config ~~~~~~~~~
{CONFIG}
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
"""

# Settings FragPipe echoes on every run: names from the 24.0 stock workflows, plus the ones a run adds.
REAL_CONFIG = r"""# FragPipe (24.0) runtime properties
database.db-path=C\:\\Fragpipe_Auto\\fasta\\human.fas
database.decoy-tag=rev_
diann.library=
diann.run-dia-nn=false
diann.run-specific-protein-q-value=false
diatracer.run-diatracer=false
fragpipe-config.bin-diann=C\:\\FragPipe\\FragPipe-24.0\\tools\\diann\\1.8.2_beta_8\\windows\\DiaNN.exe
fragpipe-config.bin-python=C\:\\FragPipe\\FragPipe-24.0\\python\\python.exe
fragpipe-config.tools-folder=C\:\\FragPipe\\FragPipe-24.0\\tools
ionquant.excludemods=
ionquant.light=C561.3387
ionquant.mbr=1
ionquant.run-ionquant=true
msfragger.allowed_missed_cleavage_1=2
msfragger.misc.fragger.remove-precursor-range-lo=-1.5
msfragger.misc.slice-db=1
msfragger.run-msfragger=true
msfragger.table.fix-mods=0.0,C-Term Peptide,true,-1; 57.02146,C (cysteine),false,-1
percolator.cmd-opts=--only-psms --no-terminate --post-processing-tdc
percolator.run-percolator=true
phi-report.filter=--sequential --prot 0.01
phi-report.run-report=true
quantitation.run-label-free-quant=true
speclibgen.run-speclibgen=false
tmtintegrator.run-tmtintegrator=false
workdir=C\:\\Fragpipe_General\\EJQ\\x\\fragpipe
workflow.description=Perform closed search and MS1-based label quantification with chemically labelled cysteines. Note\: If the dataset has multiple MS files they should be annotated as different experiments.
workflow.ram=48
workflow.saved-with-ver=24.0-build27
workflow.threads=28"""

# What the steps print when all is well (issue #2509's log up to its failing step, #2418, #2808).
REAL_STEPS_OK = r"""CheckCentroid
C:\FragPipe\FragPipe-24.0\jre\bin\java.exe -Xmx48G -cp C:\FragPipe\FragPipe-24.0\lib/* org.nesvilab.fragpipe.util.CheckCentroid C:\Fragpipe_General\EJQ\x\EJQ_2_027_1_1.raw 28
Done in 4.6 s.
Process 'CheckCentroid' finished, exit code: 0
WorkspaceCleanInit [Work dir: C:\Fragpipe_General\EJQ\x\fragpipe\EJQ_2_027_1]
C:\FragPipe\FragPipe-24.0\tools\Philosopher\philosopher-v5.1.3-RC9.exe workspace --clean --nocheck
time="13:34:50" level=info msg="Executing Workspace  v5.1.3-RC9"
time="13:34:50" level=info msg="Removing workspace"
time="13:34:50" level=info msg=Done
Process 'WorkspaceCleanInit' finished, exit code: 0
MSFragger [Work dir: C:\Fragpipe_General\EJQ\x\fragpipe]
C:\FragPipe\FragPipe-24.0\jre\bin\java.exe -jar -Dfile.encoding=UTF-8 -Xmx48G C:\FragPipe\FragPipe-24.0\tools\MSFragger-4.4.1\MSFragger-4.4.1.jar C:\Fragpipe_General\EJQ\x\fragpipe\fragger.params C:\Fragpipe_General\EJQ\x\EJQ_2_027_1_1.raw
MSFragger version MSFragger-4.4.1
Batmass-IO version 1.36.5
(c) University of Michigan
RawFileReader reading tool. Copyright (c) 2016 by Thermo Fisher Scientific, Inc. All rights reserved.
System OS: Windows 11, Architecture: AMD64
JVM started with 48 GB memory
Checking database...
Checking spectral files...
C:\Fragpipe_General\EJQ\x\EJQ_2_027_1_1.raw: Scans = 34000
In total 5138423 peptides.
Generated 8278769 modified peptides.
Number of peptides with more than 5000 modification patterns: 0
Selected fragment index width 0.10 Da.
346779428 fragments to be searched in 1 slices (3.23 GB total)
Operating on slice 1 of 1:
	Fragment index slice generated in 2.78 s
	001. EJQ_2_027_1_1.mzBIN_calibrated 2.1 s | deisotoping 0.8 s
*******************************TOTAL TIME 0.468 MIN*****
Process 'MSFragger' finished, exit code: 0
MSFragger move pepxml
C:\FragPipe\FragPipe-24.0\jre\bin\java.exe -cp C:\FragPipe\FragPipe-24.0\lib\fragpipe-24.0.jar org.nesvilab.utils.FileMove --no-err C:\Fragpipe_General\EJQ\x\EJQ_2_027_1_1.pepXML C:\Fragpipe_General\EJQ\x\fragpipe\EJQ_2_027_1\EJQ_2_027_1_1.pepXML
Process 'MSFragger move pepxml' finished, exit code: 0
Percolator [Work dir: C:\Fragpipe_General\EJQ\x\fragpipe\EJQ_2_027_1]
C:\FragPipe\FragPipe-24.0\tools\percolator_3_7_1\windows\percolator.exe --only-psms --no-terminate --post-processing-tdc --num-threads 28
Processing took 23.2650 cpu seconds or 24 seconds wall clock time.
Process 'Percolator' finished, exit code: 0
Percolator: Convert to pepxml [Work dir: C:\Fragpipe_General\EJQ\x\fragpipe\EJQ_2_027_1]
C:\FragPipe\FragPipe-24.0\jre\bin\java.exe -cp C:\FragPipe\FragPipe-24.0\lib/* org.nesvilab.fragpipe.tools.percolator.PercolatorOutputToPepXML
Process 'Percolator: Convert to pepxml' finished, exit code: 0
PhilosopherReport [Work dir: C:\Fragpipe_General\EJQ\x\fragpipe\EJQ_2_027_1]
C:\FragPipe\FragPipe-24.0\tools\Philosopher\philosopher-v5.1.3-RC9.exe report
time="13:35:46" level=info msg="Executing Report  v5.1.3-RC9"
time="13:35:46" level=info msg="Creating reports"
time="13:35:46" level=info msg=Done
Process 'PhilosopherReport' finished, exit code: 0
IonQuant [Work dir: C:\Fragpipe_General\EJQ\x\fragpipe]
C:\FragPipe\FragPipe-24.0\jre\bin\java.exe -Xmx48G -Dlibs.thermo.dir=C:\FragPipe\FragPipe-24.0\tools\MSFragger-4.4.1\ext\thermo -cp C:\FragPipe\FragPipe-24.0\tools\IonQuant-1.11.20.jar ionquant.IonQuant --threads 28
IonQuant version IonQuant-1.11.20
(c) University of Michigan
JVM started with 48 GB memory
The license (C:\FragPipe\FragPipe-24.0\license.dat) is valid. Customer: Lab. Mode: Temporary. Expiry date: 2027-09-15.
2026-10-01 13:09:13 [INFO] - Collecting variable modifications from all psm.tsv files...
2026-10-01 13:09:14 [INFO] - Loading and indexing all psm.tsv files...
"""

REAL_END_OK = r"""2026-10-01 13:12:14 [INFO] - Done!
Process 'IonQuant' finished, exit code: 0

Please cite:
(Any searches) MSFragger: ultrafast and comprehensive peptide identification in mass spectrometry–based proteomics. Nat Methods. 14:513 (2017)
(FDR filtering and reporting) Philosopher: a versatile toolkit for shotgun proteomics data analysis. Nat Methods. 17:869 (2020)
(Label-free/isotopic-labeling quantification) IonQuant Enables Accurate and Sensitive Label-Free Quantification With FDR-Controlled Match-Between-Runs. Mol Cell Proteomics. 20:100077 (2021)

Task Runtimes:
  CheckCentroid: 0.08 minutes
  MSFragger: 0.47 minutes
  IonQuant: 3.02 minutes
  Finalizer Task: 0.00 minutes

=============================================================ALL JOBS DONE IN 4.1 MINUTES=============================================================
"""

REAL_OK = REAL_HEAD.replace("{CONFIG}", REAL_CONFIG) + REAL_STEPS_OK + REAL_END_OK


def test_a_healthy_fragpipe_log_gets_no_explanation():
    """FragPipe echoes every workflow setting and every command line on each run. A hint that matched such a
    line would be shown for every failure (one did: the bare key database.db-path)."""
    assert fragpipe.explain(REAL_OK) == []
    for rx, msg in fragpipe.EXPLANATIONS:
        assert not re.search(rx, REAL_OK), f"{msg[:50]!r} matches a healthy log"


def test_console_facts_of_a_finished_run():
    facts = fragpipe.console_facts(REAL_OK)
    assert facts["commands"] == 8
    assert [name for name, _code in facts["finished"]] == [
        "CheckCentroid", "WorkspaceCleanInit", "MSFragger", "MSFragger move pepxml", "Percolator",
        "Percolator: Convert to pepxml", "PhilosopherReport", "IonQuant"]
    assert all(code == 0 for _name, code in facts["finished"])
    # the list of commands at the top names every step too: those are not starts
    assert facts["started"] == ["WorkspaceCleanInit", "MSFragger", "Percolator", "Percolator: Convert to pepxml",
                                "PhilosopherReport", "IonQuant"]
    assert facts["all_jobs_done_minutes"] == "4.1" and facts["cancelled_tasks"] is None and not facts["dry_run"]
    assert fragpipe.failed_step(REAL_OK) is None


def test_progress_follows_the_running_step_not_the_list_of_commands(tmp_path):
    log = tmp_path / "c.log"
    head = REAL_HEAD.replace("{CONFIG}", REAL_CONFIG)
    listing = head[:head.index("~~~~~~~~~~~~~~~~~~~~~~")]
    log.write_text("# ionomos job 1  2026-10-01 13:00:00\n# fragpipe.bat --headless\n\n", encoding="utf-8")
    assert fragpipe.progress(log) == "starting"
    log.write_text(listing, encoding="utf-8")  # FragPipe has only listed what it will run
    assert fragpipe.progress(log) == "starting (0 of 8 step(s) done)"
    log.write_text(head, encoding="utf-8")
    assert fragpipe.progress(log) == "starting (0 of 8 step(s) done)"  # used to say "IonQuant": the list's last line
    upto = REAL_STEPS_OK.index("Percolator [Work dir")
    log.write_text(head + REAL_STEPS_OK[:upto], encoding="utf-8")
    assert fragpipe.progress(log) == "MSFragger (4 of 8 step(s) done)"
    log.write_text(REAL_OK, encoding="utf-8")
    assert fragpipe.progress(log) == "IonQuant (8 of 8 step(s) done)"
    # a step whose name has brackets or a colon (FragPipe's "Quant (Isobaric)", "Percolator: Convert to pepxml")
    log.write_text("Quant (Isobaric) [Work dir: C:\\x]\n", encoding="utf-8")
    assert fragpipe.progress(log) == "Quant (Isobaric) (0 step(s) done)"


def test_progress_reads_the_latest_attempt_only(tmp_path):
    log = tmp_path / "c.log"
    log.write_text("\n# ionomos job 1  2026-10-01 13:00:00\n# cmd\n\n" + REAL_OK
                   + "\n# exit code 0 after 4.1 min\n\n# ionomos job 1  2026-10-01 14:00:00\n# cmd\n\n",
                   encoding="utf-8")
    assert fragpipe.progress(log) == "starting"


# ------------------------------------------------------------------ failures --

FAIL = "Process returned non-zero exit code, stopping\n\n~~~~~~~~~~~~~~~~~~~~\nCancelling 4 remaining tasks\n"


def _failed(step: str, said: str, code: int = 1) -> str:
    """A run that got as far as `step`, which printed `said` and failed: the shape FragPipe gives it."""
    return (REAL_HEAD.replace("{CONFIG}", REAL_CONFIG) + f"{step} [Work dir: C:\\Fragpipe_General\\EJQ\\x\\fragpipe]\n"
            + said.rstrip("\n") + f"\nProcess '{step}' finished, exit code: {code}\n" + FAIL)


def _refused(message: str) -> str:
    """FragPipe refusing before any step: one logback ERROR line, then exit code 1."""
    return f"2026-10-01 14:03:11,532 ERROR - {message}\n"


# (what the log holds, a few words of the hint it must get). Each excerpt is as FragPipe or its tool prints it;
# the source is named beside it.
REAL_FAILURES = [
    # Gradle's start script (fragpipe.bat) when it finds no Java
    ("\nERROR: JAVA_HOME is not set and no 'java' command could be found in your PATH.\n\nPlease set the JAVA_HOME "
     "variable in your environment to match the\nlocation of your Java installation.\n", "found no Java"),
    ("ERROR: JAVA_HOME is set to an invalid directory: C:\\FragPipe\\FragPipe-24.0\\jre\n", "found no Java"),
    # FragPipeMain / Fragpipe.main0
    ("Cannot recognize the argument --config-python\n", "doesn't know an option"),
    # FragpipeRun.checkFasta, checkDbConfig (headless)
    (_refused("FASTA file path is empty or the file is corrupted"), "No protein database"),
    (_refused("Could not find fasta file at: C:\\Fragpipe_Auto\\fasta\\human.fas"), "No protein database"),
    (_refused("No decoys found in the FASTA file."), "FASTA's decoys"),
    (_refused("FASTA file contains 33.3% decoys."), "FASTA's decoys"),
    (_refused("All FASTA entries seem to be decoys."), "FASTA's decoys"),
    # FragpipeRun.validateWd; TabConfig's tool checks
    (_refused("Output directory path contains whitespace characters. Some programs in the pipeline might not work "
              "properly in this case. Please change output directory to one without spaces."), "space in it"),
    (_refused('There are spaces in the path: "C:\\Program Files\\FragPipe\\tools\\MSFragger-4.4.1.jar"'), "space in it"),
    # FragpipeRun.configureTaskGraph, after SwingUtils.stripHtml
    (_refused("MSFragger is not valid but it is enabled in the workflow. Please disable MSFragger or fix it in the "
              "Config tab."), "MSFragger isn't installed"),
    (_refused("IonQuant is not valid but it is enabled in the workflow. Please disable IonQuant or fix it in the "
              "Config tab."), "IonQuant isn't installed"),
    (_refused("diaTracer is not valid but it is enabled in the workflow. Please disable diaTracer or fix it in the "
              "Config tab."), "diaTracer isn't installed"),
    # Msfragger.testJar / IonQuant: the commercial builds' licence lines
    ("The license (C:\\FragPipe\\FragPipe-24.0\\license.dat) has expired. Expiry date: 2025-09-15.\n", "licence problem"),
    ("No license file found.\n", "licence problem"),
    # CmdIonquant
    (_refused("When processing .RAW files IonQuant requires native Thermo libraries. Native libraries come with "
              "MSFragger zip download, contained in ext sub-directory."), "'ext' folder"),
    # the .NET host, when a tool's runtime isn't installed
    (_failed("MSFragger", "You must install or update .NET to run this application.\n\nApp: C:\\FragPipe\\FragPipe-24.0"
                          "\\tools\\MSFragger-4.4.1\\ext\\thermo\\BatmassIoThermoServer.exe\nArchitecture: x64\n"
                          "Framework: 'Microsoft.NETCore.App', version '6.0.0' (x64)"), ".NET runtime"),
    # issues #619 / #2843 (MSFragger), #1859 (MSBooster, hs_err), #1478 (Python)
    (_failed("MSFragger", "Not enough memory allocated to MSFragger. Try increasing the memory or the number of "
                          "database splits."), "didn't get enough memory"),
    (_failed("MSBooster", "OpenJDK 64-Bit Server VM warning: INFO: os::commit_memory(0x00000271ac000000, 6442450944, 0) "
                          "failed; error='The paging file is too small for this operation to complete' (DOS error/"
                          "errno=1455)\n#\n# There is insufficient memory for the Java Runtime Environment to continue."),
     "Windows ran out of memory"),
    (_failed("MSFragger", "OSError: [WinError 1455] The paging file is too small for this operation to complete"),
     "Windows ran out of memory"),
    (_failed("IonQuant", 'Exception in thread "main" java.lang.OutOfMemoryError: Java heap space\n\tat '
                         "java.base/java.util.Arrays.copyOf(Unknown Source)"), "ran out of memory"),
    # issues #427, #2602, #247
    (_failed("MSFragger", 'java.io.IOException: Cannot run program "C:\\FragPipe\\jre\\bin\\java.exe": CreateProcess '
                          "error=206, The filename or extension is too long"), "longer than Windows allows"),
    (_failed("SpecLibGen", "FileNotFoundError: [WinError 206] The filename or extension is too long"),
     "longer than Windows allows"),
    # issue #2509 (OneDrive), #2452 (RewritePepxml)
    (_failed("PhilosopherReport", 'time="13:35:46" level=error msg="Cannot write file. cannot create report file, open '
                                  "C:\\\\Users\\\\x\\\\OneDrive\\\\Desktop\\\\r\\\\apo2\\\\psm.tsv: The process cannot access "
                                  'the file because it is being used by another process."'), "open in another program"),
    (_failed("Percolator: Convert to pepxml", 'Exception in thread "main" java.nio.file.FileSystemException: C:\\x\\'
                                              "interact.pep.xml4313989976365597700.temp-rewrite -> C:\\x\\interact.pep.xml"),
     "open in another program"),
    # issue #1762 (Philosopher), #2808 (MSFragger), #498 (Thermo reader)
    (_failed("ProteinProphet", 'time="10:02:11" level=fatal msg="Cannot deploy asset. ProteinProphet"'), "antivirus"),
    (_failed("MSFragger", "Could not load file D:\\x\\027589_F1_R1.mzBIN_calibrated. The file may be corrupted."),
     "antivirus"),
    # issues #2644 / #2364 (headless without --config-python), CmdMsfragger
    (_refused("Spectral Library Generation module was not configured correctly. Please make sure that Python and "
              "FragPipe-SpecLib have been installed."), "FragPipe's Python"),
    (_refused("DbSplit was enabled, but Python was not configured."), "FragPipe's Python"),
    (_failed("SpecLibGen", "Traceback (most recent call last):\n  File \"gen_con_spec_lib.py\", line 12, in <module>\n"
                           "ModuleNotFoundError: No module named 'easypqp'"), "FragPipe's Python"),
    # issue #2364 (Diann.validate), Fragpipe.main0
    ('org.nesvilab.fragpipe.exceptions.ValidationException: Version string not found for DIA-NN from "C:\\DIA-NN\\'
     '2.3.2\\DIA-NN.exe"\n\tat org.nesvilab.fragpipe.tools.diann.Diann.validate(Diann.java:127)\n', "can't start DIA-NN"),
    ("DIA-NN executable file path C:/DIA-NN/2.3.2/DiaNN.exe does not seem right.\n", "can't start DIA-NN"),
    (_failed("DIA-Quant run DIA-NN", "DIA-NN 1.8.2 beta 8 (Data-Independent Acquisition by Neural Networks)\n"
                                     "0 files will be processed"), "DIA quantification step"),
    # CmdTmtIntegrator, TmtiPanel
    (_refused("Number of the samples in the annotation file does not match the number of channels in the 'label type' "
              "of the Quant (Isobaric) tab."), "TMT annotation"),
    (_refused("Duplicate samples found in annotation files. The sample names must be unique among all experiments."),
     "TMT annotation"),
    ('Exception in thread "main" java.lang.RuntimeException: Invalid line in annotation file C:\\x\\annotation.txt: '
     "126 DMSO rep 1\n", "TMT annotation"),
    # FragpipeRun.checkInputLcmsFiles2, run()
    (_refused("Some input LCMS files have the same name, even though located in different folders:"), "file list"),
    (_refused("Percolator was enabled but MSFragger's output formats did not contain pin or pepXML."),
     "settings contradict"),
    # issue #2418
    (_failed("IonQuant", "2025-09-03 13:09:16 [ERROR] - There were errors creating indexes : com.univocity.parsers."
                         "common.DataProcessingException: '\u200b' is not a character"), "character in the FASTA"),
]


@pytest.mark.parametrize(("log", "expect"), REAL_FAILURES, ids=[e + f"-{i}" for i, (_l, e) in enumerate(REAL_FAILURES)])
def test_real_fragpipe_failures_get_their_cause_first(log, expect):
    hints = fragpipe.explain(log)
    assert hints and expect in hints[0], hints


def test_every_explanation_is_exercised_by_a_test():
    """A new entry in EXPLANATIONS needs a realistic excerpt here or in test_failsafes."""
    here = {msg for log, _e in REAL_FAILURES for msg in fragpipe.explain(log)}
    elsewhere = {msg for log in (
        "raw file(s) are empty (0 bytes): a.raw", "raw file(s) missing from C:/x: a.raw",
        "There is not enough space on the disk.", "DIA-NN.exe not found",
        "The process cannot access the file because it is being used by another process",
        "Error reading D:\\x\\DMSO_1_1.raw", "java.lang.UnsupportedClassVersionError: x",
        "Error: Could not find or load main class org.nesvilab.fragpipe.FragPipeMain",
        "'fragpipe.bat' is not recognized as an internal or external command", "Access is denied",
        "output directory is not empty", "philosopher: fatal error", "0 PSMs passed",
        "Exception in thread \"main\" java.lang.IllegalStateException") for msg in fragpipe.explain(log)}
    missing = [msg for _rx, msg in fragpipe.EXPLANATIONS if msg not in here | elsewhere]
    assert missing == []


def test_the_failed_step_is_named_with_what_it_said():
    log = REAL_FAILURES[23][0]  # PhilosopherReport and the locked psm.tsv
    step, code, said = fragpipe.failed_step(log)
    assert (step, code) == ("PhilosopherReport", "1")
    assert said.endswith('used by another process."')  # not FragPipe's "Cancelling 4 remaining tasks"
    assert fragpipe.console_facts(log)["cancelled_tasks"] == 4
    assert fragpipe.console_facts(_refused("No decoys found in the FASTA file."))["error_lines"] == [
        "No decoys found in the FASTA file."]


def test_new_holds_have_help(bed):
    assert helpdoc.hold_topic("FragPipe launcher C:/x/bin/FragPipe-24.0.exe is FragPipe's window program and "
                              "can't run a search unattended; ...") == "search.hold-launcher-window"
    assert helpdoc.hold_topic("FASTA for isoDTB can't be searched: h.fas has no decoys") == "search.hold-decoys"
    assert helpdoc.hold_topic("FASTA for isoDTB missing: C:/x.fas") == "search.hold-fasta"


# ------------------------------------------------------- launcher and command --


def _install(root: Path, exe: bool = True, bat: bool = True, jre: bool = True) -> Path:
    """A FragPipe 24.0 installation as its Windows installer lays it out (empty files)."""
    files = ["lib/fragpipe-24.0.jar", "tools/MSFragger-4.4.1/MSFragger-4.4.1.jar", "tools/IonQuant-1.11.20.jar",
             "tools/diaTracer-2.2.1.jar", "tools/MSFragger-4.4.1/ext/thermo/BatmassIoThermoServer.exe",
             "tools/Philosopher/philosopher-v5.1.3-RC9.exe", "tools/diann/1.8.2_beta_8/windows/DiaNN.exe",
             "python/python.exe", "workflows/LFQ-MBR.workflow"]
    files += ["bin/FragPipe-24.0.exe"] if exe else []
    files += ["bin/fragpipe.bat", "bin/fragpipe"] if bat else []
    files += ["jre/bin/java.exe"] if jre else []
    for f in files:
        (root / f).parent.mkdir(parents=True, exist_ok=True)
        (root / f).write_text("", encoding="utf-8")
    return root / "bin"


def test_the_exe_of_an_install_is_swapped_for_fragpipe_bat_and_gets_fragpipes_java(bed, tmp_path):
    bin_ = _install(tmp_path / "FragPipe-24.0")
    cfg = replace(bed["cfg"], fragpipe_exe=bin_ / "FragPipe-24.0.exe")
    exe = fragpipe.resolve_launcher(cfg)
    assert exe == bin_ / "fragpipe.bat"
    # fragpipe.bat (Gradle's start script) needs JAVA_HOME or java on PATH; the lab PC has neither
    assert fragpipe.launcher_env(exe) == {"JAVA_HOME": str(tmp_path / "FragPipe-24.0" / "jre")}
    _queue(bed)
    spec = fragpipe.prepare(bed["ledger"].get(1), cfg)
    assert spec.exe == exe and spec.env["JAVA_HOME"].endswith("jre")


def test_the_window_exe_without_fragpipe_bat_holds_the_job(bed, tmp_path):
    """FragPipe-24.0.exe is a launch4j window program: it returns at once and prints nothing. Running it would
    look like a search that wrote no output while FragPipe worked on unseen."""
    bin_ = _install(tmp_path / "FragPipe-24.0", bat=False)
    cfg = replace(bed["cfg"], fragpipe_exe=bin_ / "FragPipe-24.0.exe")
    assert fragpipe.is_window_exe(bin_ / "FragPipe-24.0.exe")
    with pytest.raises(fragpipe.Hold, match="window program"):
        fragpipe.resolve_launcher(cfg)
    _queue(bed)
    Worker(cfg, bed["ledger"]).run_once()
    job = bed["ledger"].get(1)
    assert job.status == "queued" and "window program" in job.reason
    rows = fragpipe.install_report(cfg)
    assert rows[0][0] is False and "fragpipe.bat is missing" in rows[0][2]
    # an .exe that isn't inside a FragPipe installation is run as before
    lone = tmp_path / "other" / "fragpipe.exe"
    lone.parent.mkdir()
    lone.write_text("", encoding="utf-8")
    assert not fragpipe.is_window_exe(lone)
    assert fragpipe.resolve_launcher(replace(bed["cfg"], fragpipe_exe=lone)) == lone


def test_launcher_env_without_a_bundled_jre_or_outside_an_install(bed, tmp_path):
    bin_ = _install(tmp_path / "FragPipe-24.0", jre=False)
    assert fragpipe.launcher_env(bin_ / "fragpipe.bat") == {}
    assert fragpipe.launcher_env(bed["cfg"].fragpipe_exe) == {}  # the testbed's fake: not in a bin folder
    # zip builds keep the JRE beside the fragpipe folder (FragPipe-jre-22.0/jre)
    zipped = tmp_path / "FragPipe-jre-22.0"
    for f in ("fragpipe/bin/fragpipe.bat", "jre/bin/java.exe"):
        (zipped / f).parent.mkdir(parents=True, exist_ok=True)
        (zipped / f).write_text("", encoding="utf-8")
    assert fragpipe.launcher_env(zipped / "fragpipe" / "bin" / "fragpipe.bat") == {"JAVA_HOME": str(zipped / "jre")}


def test_command_line_has_only_options_fragpipe_knows(bed):
    """FragPipeMain stops on an option it doesn't know ("Cannot recognize the argument")."""
    known = {"--headless", "--dry-run", "--workflow", "--manifest", "--workdir", "--ram", "--threads",
             "--config-tools-folder", "--config-diann", "--config-python"}
    cfg = replace(bed["cfg"], config_tools_folder="C:/FragPipe/tools", config_diann="C:/DIA-NN/DiaNN.exe",
                  config_python="C:/FragPipe/FragPipe-24.0/python")
    _queue(bed, "dia_good")
    cmd = fragpipe.prepare(bed["ledger"].get(1), cfg).command()
    assert {a for a in cmd if a.startswith("--")} == known - {"--dry-run"}
    assert cmd[cmd.index("--config-python") + 1] == "C:/FragPipe/FragPipe-24.0/python"
    assert all(" " not in a and "\\" not in a for a in cmd[1:])
    # --config-diann only goes to a DIA search; --ram / --threads are whole numbers
    bed["ledger"].set_status(1, "failed", "x")
    _queue(bed, "iso_good")
    iso = fragpipe.prepare(bed["ledger"].get(2), cfg).command()
    assert "--config-diann" not in iso and iso[iso.index("--ram") + 1].isdigit()


def test_the_launcher_gets_java_home_in_its_environment(bed, tmp_path):
    """run() hands RunSpec.env to the process, and writes it into the console log's header."""
    out = tmp_path / "seen.txt"
    if os.name == "nt":
        launcher = tmp_path / "echo.bat"
        launcher.write_text(f'@echo off\r\necho %JAVA_HOME%> "{out}"\r\necho ok> "%7\\made.txt"\r\n', encoding="utf-8")
    else:
        launcher = tmp_path / "echo.sh"
        launcher.write_text(f'#!/bin/sh\necho "$JAVA_HOME" > "{out}"\necho ok > "$7/made.txt"\n', encoding="utf-8")
        launcher.chmod(0o755)
    _queue(bed)
    spec = fragpipe.prepare(bed["ledger"].get(1), replace(bed["cfg"], fragpipe_exe=launcher))
    spec.env = {"JAVA_HOME": "C:/FragPipe/FragPipe-24.0/jre"}
    fragpipe.write_inputs(spec)
    res = fragpipe.run(spec, poll=0.1)
    assert res.code == 0 and out.read_text(encoding="utf-8").strip() == "C:/FragPipe/FragPipe-24.0/jre"
    assert "# JAVA_HOME=C:/FragPipe/FragPipe-24.0/jre" in spec.console_log.read_text(encoding="utf-8")
    assert any("no 'ALL JOBS DONE' line" in w for w in res.warnings)  # this launcher isn't FragPipe


def test_install_report_reads_a_24_installer_layout(bed, tmp_path):
    bin_ = _install(tmp_path / "FragPipe-24.0")
    cfg = replace(bed["cfg"], fragpipe_exe=bin_ / "FragPipe-24.0.exe")
    rows = {label: (ok, detail) for ok, label, detail in fragpipe.install_report(cfg)}
    assert rows["launcher"] == (True, str(bin_ / "fragpipe.bat"))
    assert rows["FragPipe"][1].startswith("version 24.0")
    assert rows["bundled Java"][0] is True and rows["MSFragger"] == (True, "MSFragger-4.4.1.jar")
    assert rows["Thermo .raw reader"][0] is True and rows["Philosopher"] == (True, "philosopher-v5.1.3-RC9.exe")
    assert rows["DIA-NN"][0] is True and rows["Python for FragPipe"][0] is True
    assert "licence file" not in rows
    # FragPipe's window was never opened with this installation: a headless run has no tools folder to read
    assert rows["FragPipe settings"][0] is None and "open FragPipe once" in rows["FragPipe settings"][1]
    (tmp_path / "FragPipe-24.0" / "cache").mkdir()
    (tmp_path / "FragPipe-24.0" / "cache" / "fragpipe-ui.cache").write_text("", encoding="utf-8")
    assert dict((label, ok) for ok, label, _d in fragpipe.install_report(cfg))["FragPipe settings"] is True
    (tmp_path / "FragPipe-24.0" / "license.dat").write_text("", encoding="utf-8")
    assert "licence file" in {label for _ok, label, _d in fragpipe.install_report(cfg)}


def test_workflow_needs_follow_fragpipes_run_switches():
    read = fragpipe.read_properties
    assert fragpipe.workflow_needs(read(testbed.workflow_text("isoDTB"))) == {
        "MSFragger": True, "IonQuant": True, "diaTracer": False, "DIA-NN": False, "Python": False}
    # the stock DIA workflow has ionquant.run-ionquant=true and never runs IonQuant
    assert fragpipe.workflow_needs(read(testbed.workflow_text("DIA"))) == {
        "MSFragger": True, "IonQuant": False, "diaTracer": False, "DIA-NN": True, "Python": True}
    assert fragpipe.workflow_needs(read(testbed.workflow_text("TMT")))["IonQuant"] is True  # intensity extraction
    assert fragpipe.workflow_needs({"msfragger.run-msfragger": "true", "msfragger.misc.slice-db": "4"})["Python"]


def test_experiment_names_as_fragpipe_keeps_them():
    assert fragpipe.fp_experiment("EJQ-2-027") == "EJQ_2_027"
    assert fragpipe.fp_experiment("DMSO.rep 1") == "DMSO_rep_1"
    assert fragpipe.fp_experiment("Drug_4h") == "Drug_4h"


# -------------------------------------------------------------------- decoys --


def _fasta(path: Path, targets: int, decoys: int, tag: str = "rev_") -> None:
    path.write_text("".join(f">sp|T{i}|T{i}_HUMAN\nMK\n" for i in range(targets))
                    + "".join(f">{tag}sp|T{i}|T{i}_HUMAN\nKM\n" for i in range(decoys)), encoding="utf-8")


@pytest.mark.parametrize(("targets", "decoys", "expect"), [
    (10, 0, "has no decoys"), (10, 2, "16.7 % of the entries"), (2, 10, "83.3 % of the entries"), (10, 10, ""),
    (10, 7, ""), (0, 0, "no protein entries")])
def test_decoy_problem_is_fragpipes_own_rule(tmp_path, targets, decoys, expect):
    fa = tmp_path / "db.fas"
    _fasta(fa, targets, decoys)
    props = {"percolator.run-percolator": "true", "database.decoy-tag": "rev_"}
    assert expect in fragpipe.decoy_problem(props, fa) and bool(fragpipe.decoy_problem(props, fa)) == bool(expect)
    # a workflow that doesn't say it validates PSMs: FragPipe's check can't be predicted, so nothing is claimed
    assert fragpipe.decoy_problem({"database.decoy-tag": "rev_"}, fa) == ""


def test_a_fasta_without_decoys_holds_the_job_until_it_is_replaced(bed):
    fa = bed["cfg"].fasta_dir / "human_reviewed_decoys.fas"
    good = fa.read_text(encoding="utf-8")
    _fasta(fa, 4, 0)
    dest = _queue(bed)
    w = Worker(bed["cfg"], bed["ledger"])
    assert not w.run_once()
    job = bed["ledger"].get(1)
    assert job.status == "queued" and "can't be searched" in job.reason and "has no decoys" in job.reason
    assert not (dest / "fragpipe").exists()  # FragPipe was not started
    lines = fragpipe.describe_method(bed["cfg"], "isoDTB")
    assert lines[1][0] is False and "has no decoys" in lines[1][1]
    fa.write_text(good, encoding="utf-8")
    assert w.run_once() and bed["ledger"].get(1).status == "done"


def test_the_fake_refuses_what_fragpipe_refuses(bed, monkeypatch):
    """The same FASTA handed straight to the (fake) FragPipe: its ERROR line and exit code 1."""
    fa = bed["cfg"].fasta_dir / "human_reviewed_decoys.fas"
    dest = _queue(bed)
    spec = fragpipe.prepare(bed["ledger"].get(1), bed["cfg"])
    fragpipe.write_inputs(spec)
    _fasta(fa, 4, 1)
    res = fragpipe.run(spec, poll=0.1)
    assert res.code == 1 and "FASTA's decoys" in res.reason and "ERROR - FASTA file contains 20.0% decoys." in res.reason
    assert not any((dest / "fragpipe").iterdir())


# ------------------------------------------------------------------ run loop --


def test_a_complete_run_leaves_fragpipes_files(bed):
    dest = _queue(bed)
    Worker(bed["cfg"], bed["ledger"]).run_once()
    assert bed["ledger"].get(1).status == "done", bed["ledger"].get(1).reason
    wd = dest / "fragpipe"
    have = {p.relative_to(wd).as_posix() for p in wd.rglob("*") if p.is_file()}
    group = "EJQ_PK_EJQ_2_027_isoDTB_1uM_3h_1"  # <experiment>_<bioreplicate>, - turned into _
    for f in ("fragpipe.workflow", "fragpipe-files.fp-manifest", "fragpipe.job", "fragger.params", "combined.prot.xml",
              "filelist_ionquant.txt", "modmasses_ionquant.txt", "experiment_annotation.tsv",
              "combined_modified_peptide_label_quant.tsv", f"{group}/psm.tsv", f"{group}/protein.fas",
              f"{group}/interact-EJQ_PK_EJQ-2-027_isoDTB_1uM_3h_1_1.pep.xml"):
        assert f in have, f
    assert any(re.fullmatch(r"log_\d{4}-\d\d-\d\d_\d\d-\d\d-\d\d\.txt", f) for f in have)
    assert "sdrf.tsv" not in have  # off in the testbed's workflows; see the xfail below
    console = _console(dest)
    facts = fragpipe.console_facts(fragpipe.attempt_text(console))
    assert facts["commands"] == len(facts["finished"]) and facts["all_jobs_done_minutes"] is not None
    assert fragpipe.explain(console) == []  # the fake's healthy output gets no hint either
    assert "database.db-path=" in console and "~~~~~~~~~ fragpipe.config ~~~~~~~~~" in console
    assert _status(dest)["run"]["warnings"] == [w for w in _status(dest)["run"]["warnings"] if "ALL JOBS DONE" not in w]


def test_dia_run_writes_dia_quant_output_and_one_flat_folder(bed):
    dest = _queue(bed, "dia_good")
    Worker(bed["cfg"], bed["ledger"]).run_once()
    assert bed["ledger"].get(1).status == "done", bed["ledger"].get(1).reason
    wd = dest / "fragpipe"
    assert (wd / "dia-quant-output" / "report.pg_matrix.tsv").is_file() and not (wd / "diann-output").exists()
    assert not [p for p in wd.iterdir() if p.is_dir() and p.name.startswith(("DMSO", "Drug"))]
    assert "DIA-Quant run DIA-NN [Work dir: " in _console(dest)


def test_tmt_run_uses_the_annotation_file_and_names_fragpipes_way_without_one(bed):
    dest = _queue(bed, "tmt_good")
    Worker(bed["cfg"], bed["ledger"]).run_once()
    assert bed["ledger"].get(1).status == "done", bed["ledger"].get(1).reason
    console = _console(dest)
    assert "~~~~~~annotation files~~~~~~~" in console and "126\tDMSO_1_126" in console
    head = (dest / "fragpipe" / "tmt-report" / "abundance_gene_MD.tsv").read_text(encoding="utf-8").splitlines()[0]
    assert "DMSO_1_126" in head and "NA" not in head.split("\t")  # NA = a channel left out
    ann = (dest / "fragpipe" / "experiment_annotation.tsv").read_text(encoding="utf-8").splitlines()
    assert ann[0] == "plex\tchannel\tsample\tsample_name\tcondition\treplicate" and len(ann) == 7


@pytest.mark.parametrize(("mode", "status", "expect"), [
    ("step-fail-exit0", "failed", "step MSFragger failed (exit code 137) although FragPipe exited 0"),
    ("step-fail-neg-exit0", "failed", "step MSFragger failed (exit code -11) although FragPipe exited 0"),
    ("cancel-exit0", "failed", "FragPipe stopped early (cancelled"),
    ("silent-exit0", "failed", "exited 0 but wrote nothing"),
    ("oom", "failed", "step MSFragger failed (exit code 1); it said:"),
    ("locked", "failed", "open in another program"),
    ("speclib", "failed", "FragPipe's Python"),
    ("no-java", "failed", "found no Java"),
    ("msfragger", "failed", "MSFragger isn't installed"),
])
def test_ways_a_run_fails(bed, monkeypatch, mode, status, expect):
    monkeypatch.setenv("IONOMOS_FAKE_FP_MODE", mode)
    dest = _queue(bed)
    Worker(bed["cfg"], bed["ledger"]).run_once()
    job = bed["ledger"].get(1)
    assert job.status == status and expect in job.reason, job.reason
    assert (dest / "FAILED.txt").is_file() and not (dest / "DONE.txt").exists()
    assert not (dest / "results").exists()  # no analysis of a search that didn't finish


def test_dia_nn_step_failure_is_named(bed, monkeypatch):
    monkeypatch.setenv("IONOMOS_FAKE_FP_MODE", "diann")
    _queue(bed, "dia_good")
    Worker(bed["cfg"], bed["ledger"]).run_once()
    reason = bed["ledger"].get(1).reason
    assert "DIA quantification step" in reason and "step DIA-Quant run DIA-NN failed (exit code 1)" in reason


def test_a_log_without_the_done_line_is_a_warning_not_a_failure(bed, monkeypatch):
    monkeypatch.setenv("IONOMOS_FAKE_FP_MODE", "no-done-line")
    dest = _queue(bed)
    Worker(bed["cfg"], bed["ledger"]).run_once()
    job = bed["ledger"].get(1)
    assert job.status == "done" and "no 'ALL JOBS DONE' line" in job.reason
    assert "Note: FragPipe's log has no 'ALL JOBS DONE' line" in (dest / "DONE.txt").read_text(encoding="utf-8")


def test_a_dry_run_is_never_taken_for_a_search(bed, tmp_path):
    """If --dry-run ever reached a job's command line, FragPipe would exit 0 having searched nothing."""
    dest = _queue(bed)
    spec = fragpipe.prepare(bed["ledger"].get(1), bed["cfg"])
    fragpipe.write_inputs(spec)
    (spec.workdir / "left_over.txt").write_text("x", encoding="utf-8")
    spec.command = lambda: [*fragpipe.RunSpec.command(spec)[:2], "--dry-run", *fragpipe.RunSpec.command(spec)[2:]]
    res = fragpipe.run(spec, poll=0.1)
    assert not res.ok and "only did a dry run" in res.reason
    assert sorted(p.name for p in (dest / "fragpipe").iterdir()) == ["left_over.txt"]  # a dry run writes nothing


def test_a_retry_that_succeeds_is_not_blamed_for_the_earlier_attempt(bed, monkeypatch):
    """The console log keeps every attempt. The failed step of attempt 1 used to fail attempt 2 as well,
    whenever the second run's output fitted in the part of the log that is read back."""
    monkeypatch.setenv("IONOMOS_FAKE_FP_MODE", "step-fail-exit0")
    dest = _queue(bed)
    w = Worker(bed["cfg"], bed["ledger"])
    w.run_once()
    assert bed["ledger"].get(1).status == "failed"
    monkeypatch.delenv("IONOMOS_FAKE_FP_MODE")
    bed["ledger"].requeue(1, "retry requested", reset_attempts=True)
    w.run_once()
    job = bed["ledger"].get(1)
    assert job.status == "done", job.reason
    console = _console(dest)
    assert console.count("# ionomos job 1 ") == 2 and "exit code: 137" in console
    assert "exit code: 137" not in fragpipe.attempt_text(console)
    # the first attempt's output was moved aside, not deleted
    prev = list(dest.glob("fragpipe_previous_*"))
    assert len(prev) == 1 and (prev[0] / "partial.txt").is_file()


def test_every_rerun_keeps_the_output_before_it(bed):
    dest = _queue(bed)
    w = Worker(bed["cfg"], bed["ledger"])
    for n in range(3):
        if n:
            time.sleep(1.1)  # fragpipe_previous_<time> is named to the second
            bed["ledger"].requeue(1)
        w.run_once()
        assert bed["ledger"].get(1).status == "done"
    prev = sorted(dest.glob("fragpipe_previous_*"))
    assert len(prev) == 2
    for p in (*prev, dest / "fragpipe"):
        assert (p / "combined_modified_peptide_label_quant.tsv").stat().st_size > 0


def _alive(pid: int) -> bool:
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:  # a child that was killed but not yet reaped still answers kill(0)
        done, _status_ = os.waitpid(pid, os.WNOHANG)
        return done == 0
    except ChildProcessError:
        state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
        return bool(state) and not state.startswith("Z")


def _wait_for(path: Path, seconds: float = 30) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end and not path.exists():
        time.sleep(0.1)
    assert path.exists(), f"{path} did not appear"


def _gone(pid: int, seconds: float = 15) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not _alive(pid):
            return True
        time.sleep(0.2)
    return False


@pytest.mark.parametrize("how", ["stop", "cancel", "timeout"])
def test_ending_a_search_kills_everything_fragpipe_started(bed, monkeypatch, how):
    """FragPipe starts Java, which starts MSFragger, IonQuant, DIA-NN. A stop, a cancel or the time limit has to
    end all of them, or the next search finds its files held by a leftover process."""
    monkeypatch.setenv("IONOMOS_FAKE_FP_SECONDS", "120")
    monkeypatch.setenv("IONOMOS_FAKE_FP_MODE", "child")
    cfg = replace(bed["cfg"], timeout_minutes=0.1) if how == "timeout" else bed["cfg"]
    dest = _queue(bed)
    w = Worker(cfg, bed["ledger"], poll_seconds=0.1)
    t = threading.Thread(target=w.run_once, daemon=True)
    t.start()
    pid_file = dest / "fragpipe" / "child.pid"
    _wait_for(pid_file)
    child = int(pid_file.read_text(encoding="utf-8"))
    launcher = _status(dest)["run"]["pid"]
    assert _alive(child)
    if how == "stop":
        w.stop()
    elif how == "cancel":
        assert "stopped within a few seconds" in request_cancel(Ledger(cfg.database), 1)
    t.join(timeout=60)
    assert not t.is_alive()
    assert _gone(child), "a process FragPipe started is still running"
    assert _gone(launcher)
    job = bed["ledger"].get(1)
    expect = {"stop": ("queued", "interrupted"), "cancel": ("failed", "cancelled by user"),
              "timeout": ("failed", "timed out")}[how]
    assert job.status == expect[0] and expect[1] in job.reason
    marker = {"stop": "# stopped by ionomos", "cancel": "# cancelled by user", "timeout": "# TIMEOUT"}[how]
    assert marker in _console(dest)
    # what it had written stays where it is until the next attempt moves it aside
    assert (dest / "fragpipe" / "fragpipe.workflow").is_file()


def test_a_launcher_that_cannot_be_started_fails_the_job_with_the_reason(bed, tmp_path):
    missing = tmp_path / "bin" / "fragpipe.sh"
    missing.parent.mkdir()
    missing.write_text("not a program\n", encoding="utf-8")  # no exec bit / not a valid Win32 application
    _queue(bed)
    spec = fragpipe.prepare(bed["ledger"].get(1), replace(bed["cfg"], fragpipe_exe=missing))
    fragpipe.write_inputs(spec)
    res = fragpipe.run(spec, poll=0.1)
    assert not res.ok and (res.code is None and "could not start FragPipe" in res.reason or res.code not in (0, None))


# ------------------------------------------------------------ the fake itself --


def _fake(bed, *args: str, env: dict | None = None) -> tuple[int, str]:
    """Run the testbed's launcher; output through a file, as everywhere (a Windows pipe holds 4 KB)."""
    out = bed["root"] / "fake_out.txt"
    with open(out, "wb") as fh:
        code = subprocess.run([str(bed["cfg"].fragpipe_exe), *args], stdout=fh, stderr=subprocess.STDOUT,
                              stdin=subprocess.DEVNULL, env={**os.environ, **(env or {})}, timeout=120).returncode
    return code, out.read_text(encoding="utf-8", errors="replace")


def test_fake_help_and_unknown_options_answer_as_fragpipe_does(bed):
    code, text = _fake(bed, "--help")
    assert code == 1  # FragPipeMain: prints the help, then System.exit(1)
    assert text.startswith("FragPipe v24.0\n") and "--config-python <string>" in text and "Java Info: " in text
    code, text = _fake(bed, "--headless", "--version")
    assert code == 1 and "Cannot recognize the argument --version" in text
    code, text = _fake(bed, "--workflow", "x.workflow")
    assert code == 1 and "you did not add --headless flag" in text
    code, text = _fake(bed, "--headless", "--manifest", "nope")
    assert code == 1 and "Please provide --workflow <path to workflow file> in the headless mode." in text
    assert fake_fragpipe.help_text().count("--") >= 12


def test_fake_dry_run_checks_and_lists_but_writes_nothing(bed):
    _queue(bed)
    spec = fragpipe.prepare(bed["ledger"].get(1), bed["cfg"])
    fragpipe.write_inputs(spec)
    cmd = spec.command()
    code, text = _fake(bed, *cmd[1:], "--dry-run")
    assert code == 0 and "It's a dry-run, not running the commands." in text
    facts = fragpipe.console_facts(text)
    assert facts["dry_run"] and facts["commands"] and facts["finished"] == [] and facts["started"] == []
    assert list(spec.workdir.iterdir()) == []


def test_fake_refuses_spaces_as_on_windows(bed):
    _queue(bed)
    spec = fragpipe.prepare(bed["ledger"].get(1), bed["cfg"])
    fragpipe.write_inputs(spec)
    cmd = spec.command()
    cmd[cmd.index("--workdir") + 1] = str(bed["root"] / "out put")
    code, text = _fake(bed, *cmd[1:], env={"IONOMOS_FAKE_FP_STRICT": "1"})
    assert code == 1 and "ERROR - Output directory path contains whitespace characters." in text
    assert "space in it" in fragpipe.explain(text)[0]


def test_fake_skips_manifest_files_that_do_not_exist_like_fragpipe(bed):
    """FragPipe drops missing files from the manifest without a word (TabWorkflow.manifestLoad): Ionomos checks
    the raws itself before every run for that reason."""
    dest = _queue(bed)
    spec = fragpipe.prepare(bed["ledger"].get(1), bed["cfg"])
    fragpipe.write_inputs(spec)
    gone = next(dest.glob("*_1_1.raw"))
    gone.rename(gone.with_suffix(".away"))
    code, text = _fake(bed, *spec.command()[1:])
    assert code == 0 and gone.name not in text.split("LCMS files:")[1].split("commands to execute")[0]
    with pytest.raises(fragpipe.JobError, match="raw file\\(s\\) missing"):
        fragpipe.prepare(bed["ledger"].get(1), bed["cfg"])


@pytest.mark.xfail(reason="FragPipe 24's stock workflows write <workdir>/sdrf.tsv (workflow.misc.save-sdrf=true); "
                          "downstream/sdrfdesign.py reads it as the experiment's own design and raises "
                          "SDRF_UNMATCHED_RUNS. Open in docs/ROADMAP.md; the testbed's workflows switch it off.",
                   strict=False)
def test_fragpipes_own_sdrf_is_not_taken_for_the_users_design(bed):
    wf = bed["cfg"].workflow_dir / "isoDTB.workflow"
    wf.write_text(wf.read_text(encoding="utf-8").replace("workflow.misc.save-sdrf=false", "workflow.misc.save-sdrf=true"),
                  encoding="utf-8")
    dest = _queue(bed)
    Worker(bed["cfg"], bed["ledger"]).run_once()
    assert (dest / "fragpipe" / "sdrf.tsv").read_text(encoding="utf-8").startswith("source name\tcharacteristics[organism]")
    analysis = json.loads((dest / "results" / "analysis.json").read_text(encoding="utf-8"))
    assert "SDRF_UNMATCHED_RUNS" not in json.dumps(analysis)


# --------------------------------------------------------------- fingerprint --


def test_fingerprint_of_a_finished_search(bed):
    dest = _queue(bed)
    Worker(bed["cfg"], bed["ledger"]).run_once()
    path = names.fingerprint_path(dest)
    assert path == dest / "ionomos_run" / "run_fingerprint.json" and path.is_file()
    assert _status(dest)["run"]["fingerprint"] == str(path)
    fp = json.loads(path.read_text(encoding="utf-8"))
    assert fp["fingerprint"] == fingerprint.FORMAT and fp["job"]["method"] == "isoDTB" and fp["job"]["attempt"] == 1
    assert fp["engine"]["name"] == "FragPipe" and fp["engine"]["version"] == "24.0"
    assert fp["engine"]["tool_versions"]["MSFragger"] == "4.4.1" and fp["engine"]["system"]["Java Info"].startswith("17")
    assert fp["engine"]["command"][1] == "--headless" and fp["engine"]["launcher"] == str(bed["cfg"].fragpipe_exe)
    assert fp["result"]["ok"] is True and fp["result"]["exit_code"] == 0 and fp["timing"]["seconds"] >= 0
    assert fp["timing"]["task_runtimes_minutes"]["MSFragger"] and "Finalizer Task" in fp["timing"]["task_runtimes_minutes"]
    assert fp["manifest"] == {"files": 9, "data_types": {"DDA": 9}, "extensions": {".raw": 9},
                              "raw_bytes": 9 * 4096, "experiments": 3}
    assert fp["workflow"]["settings"]["ionquant.mbr"] == "1" and fp["workflow"]["needs"]["IonQuant"] is True
    assert fp["workflow"]["database"] == "human_reviewed_decoys.fas" and fp["fasta"] == {
        "file": "human_reviewed_decoys.fas", "entries": 4, "decoys": 2}
    assert fp["workflow_as_run"]["settings"]["workflow.threads"] == "4"
    facts = fp["console"]["facts"]
    assert facts["commands"] == len(facts["finished"]) and facts["all_jobs_done_minutes"]
    assert fp["console"]["head"][0].startswith("# ionomos job 1") and any("ALL JOBS DONE" in ln for ln in fp["console"]["tail"])
    listed = dict(map(tuple, fp["outputs"]["listing"]))
    assert listed["combined_modified_peptide_label_quant.tsv"] > 0 and fp["outputs"]["files"] == len(listed)
    assert fp["parsed_progress"].startswith("IonQuant (")
    assert path.stat().st_size < 200_000  # a few tens of kB: text only, no tables


def test_fingerprint_of_a_failed_search_and_the_earlier_one_is_kept(bed, monkeypatch):
    monkeypatch.setenv("IONOMOS_FAKE_FP_MODE", "locked")
    dest = _queue(bed)
    w = Worker(bed["cfg"], bed["ledger"])
    w.run_once()
    fp = json.loads(names.fingerprint_path(dest).read_text(encoding="utf-8"))
    assert fp["result"]["ok"] is False and fp["result"]["exit_code"] == 1
    assert "open in another program" in fp["result"]["hints"][0]
    assert ["PhilosopherReport", 1] in fp["console"]["facts"]["finished"] and fp["console"]["facts"]["cancelled_tasks"]
    assert any("Cannot write file" in ln for ln in fp["console"]["tail"])
    monkeypatch.delenv("IONOMOS_FAKE_FP_MODE")
    time.sleep(1.1)
    bed["ledger"].requeue(1, "retry requested", reset_attempts=True)
    w.run_once()
    kept = sorted(p.name for p in (dest / "ionomos_run").glob("run_fingerprint*.json"))
    assert len(kept) == 2 and kept[0] == "run_fingerprint.json"
    assert json.loads(names.fingerprint_path(dest).read_text(encoding="utf-8"))["result"]["ok"] is True
    older = json.loads((dest / "ionomos_run" / kept[1]).read_text(encoding="utf-8"))
    assert older["result"]["ok"] is False
    # only this attempt's console is in the new one
    new = json.loads(names.fingerprint_path(dest).read_text(encoding="utf-8"))
    assert not any("Cannot write file" in ln for ln in new["console"]["head"] + new["console"]["tail"])


def test_fingerprint_never_gets_in_the_jobs_way(bed, monkeypatch):
    monkeypatch.setattr(fingerprint, "build", lambda *a, **k: 1 / 0)
    dest = _queue(bed)
    Worker(bed["cfg"], bed["ledger"]).run_once()
    assert bed["ledger"].get(1).status == "done" and not names.fingerprint_path(dest).exists()


def test_fingerprint_keeps_long_logs_small(tmp_path):
    log = tmp_path / "c.log"
    with open(log, "w", encoding="utf-8") as fh:
        fh.write(REAL_HEAD.replace("{CONFIG}", REAL_CONFIG) + REAL_STEPS_OK)
        for i in range(20_000):
            fh.write(f"2026-10-01 13:10:{i % 60:02d} [INFO] - Quantifying run {i} " + "x" * 400 + "\n")
        fh.write(REAL_END_OK)
    scan = fingerprint.scan_console(log)
    assert scan["lines"] > 20_000 and len(scan["head"]) == fingerprint.HEAD_LINES and len(scan["tail"]) == fingerprint.TAIL_LINES
    assert max(len(ln) for ln in scan["tail"]) < fingerprint.LINE_CHARS + 40
    assert scan["facts"]["commands"] == 8 and len(scan["facts"]["finished"]) == 8
    assert scan["versions"]["IonQuant"] == "1.11.20" and scan["task_runtimes_minutes"]["IonQuant"] == "3.02"
    assert len(json.dumps(scan)) < 120_000


# ----------------------------------------------------------------- preflight --


def _by_key(checks) -> dict:
    return {c.key: c for c in checks}


def test_preflight_on_a_working_setup(bed):
    checks = preflight.run(bed["cfg"])
    by = _by_key(checks)
    assert [c.key for c in checks if c.status == "fail"] == []
    assert by["starts"].status == "ok" and "FragPipe 24.0 answered --help (exit code 1" in by["starts"].detail
    assert by["java-version"].status == "ok" and by["dotnet"].status == "info"
    for m in ("isoDTB", "TMT", "DIA"):
        assert by[f"method:{m}:workflow"].status == "ok" and by[f"method:{m}:fasta"].status == "ok"
        dry = by[f"method:{m}:dry"]
        assert dry.status == "ok" and "would run" in dry.detail and "MSFragger 4.4.1" in dry.detail
    # its files are its own, under the log folder; nothing lands in the users folder or the inbox
    scratch = Path(by["files"].detail)
    assert scratch.parent == bed["cfg"].log_dir / names.PREFLIGHT_DIR
    assert (scratch / "launcher_help.log").is_file() and (scratch / "isoDTB" / "ionomos_run" / "dry_run_console.log").is_file()
    assert list((scratch / "isoDTB" / "fragpipe").iterdir()) == []
    assert not any(p.is_file() for u in bed["cfg"].users_root.iterdir() for p in u.rglob("*"))
    assert "ready for a search" in preflight.text(checks) or "Nothing blocks a search" in preflight.text(checks)
    again = Path(_by_key(preflight.run(bed["cfg"], dry=False))["files"].detail)
    assert again != scratch and scratch.is_dir()  # a new folder each time; the earlier one is left alone


def test_preflight_names_what_blocks_a_search(bed):
    _fasta(bed["cfg"].fasta_dir / "human_reviewed_decoys.fas", 4, 0)
    (bed["cfg"].workflow_dir / "DIA.workflow").write_text("not=a workflow\n", encoding="utf-8")
    checks = preflight.run(bed["cfg"])
    by = _by_key(checks)
    assert by["method:isoDTB:fasta"].status == "fail" and "has no decoys" in by["method:isoDTB:fasta"].detail
    assert by["method:isoDTB:dry"].status == "info" and "skipped" in by["method:isoDTB:dry"].detail
    assert by["method:DIA:workflow"].status == "fail" and "doesn't look like a workflow" in by["method:DIA:workflow"].detail
    out = preflight.text(checks)
    assert "to fix before a search can work" in out and "→" in out


def test_preflight_dry_run_reports_what_fragpipe_refuses(bed, monkeypatch):
    monkeypatch.setenv("IONOMOS_FAKE_FP_MODE", "speclib")
    dry = _by_key(preflight.run(bed["cfg"], methods=["DIA"]))["method:DIA:dry"]
    assert dry.status == "fail" and "Spectral Library Generation module was not configured correctly" in dry.detail
    assert "FragPipe's Python" in dry.fix


def test_preflight_static_starts_nothing(bed, monkeypatch):
    monkeypatch.setattr(preflight.subprocess, "Popen", lambda *a, **k: 1 / 0)
    checks = preflight.run(bed["cfg"], start=False)
    keys = {c.key for c in checks}
    assert "starts" not in keys and not any(k.endswith(":dry") for k in keys) and "method:isoDTB:fasta" in keys
    assert not (bed["cfg"].log_dir / names.PREFLIGHT_DIR).exists()


def test_preflight_with_the_window_exe_and_with_a_missing_tool(bed, tmp_path):
    bin_ = _install(tmp_path / "FragPipe-24.0", bat=False)
    by = _by_key(preflight.run(replace(bed["cfg"], fragpipe_exe=bin_ / "FragPipe-24.0.exe")))
    assert by["launcher"].status == "fail" and "window program" in by["launcher"].detail
    assert not any(k.endswith(":dry") for k in by)
    # an install whose tools folder lacks what a workflow runs (nothing is started: these are empty files)
    bin_ = _install(tmp_path / "FragPipe-24.1")
    (tmp_path / "FragPipe-24.1" / "tools" / "IonQuant-1.11.20.jar").unlink()
    (tmp_path / "FragPipe-24.1" / "python" / "python.exe").unlink()
    by = _by_key(preflight.run(replace(bed["cfg"], fragpipe_exe=bin_ / "fragpipe.bat"), start=False))
    assert by["install:IonQuant"].status == "warn"
    assert by["method:isoDTB:tools"].status == "fail" and "not installed: IonQuant" in by["method:isoDTB:tools"].detail
    assert by["method:DIA:tools"].status == "fail" and "Python" in by["method:DIA:tools"].detail
    assert by["method:TMT:tools"].status == "fail"


def test_preflight_pc_checks(bed, monkeypatch):
    from ionomos import health

    monkeypatch.setattr(health, "memory_gb", lambda: (64.0, 40.0))
    monkeypatch.setattr(health, "disk_free_gb", lambda p: 5.0)
    monkeypatch.setattr(preflight.os, "cpu_count", lambda: 8)
    cfg = replace(bed["cfg"], ram_gb=96, threads=28, min_free_gb=20)
    by = _by_key(preflight._pc_checks(cfg, cfg.fragpipe_exe))
    assert by["ram"].status == "fail" and "PC has 64 GB" in by["ram"].detail
    assert by["threads"].status == "warn" and by["disk"].status == "warn"
    assert _by_key(preflight._pc_checks(replace(cfg, ram_gb=48, threads=6), cfg.fragpipe_exe))["ram"].status == "ok"
    spaced = replace(cfg, config_tools_folder="C:/Program Files/FragPipe/tools")
    assert "tools folder (C:/Program Files/FragPipe/tools)" in _by_key(preflight._pc_checks(spaced, None))["spaces"].detail
    deep = replace(cfg, users_root=bed["root"] / ("d" * 70))
    assert _by_key(preflight._pc_checks(deep, None))["longpaths"].status == "warn"


def test_cli_preflight(bed, capsys):
    assert cli_main(["--config", str(bed["cfg_path"]), "preflight", "--method", "isoDTB"]) == 0
    out = capsys.readouterr().out
    assert "isoDTB: FragPipe dry run" in out and "TMT: " not in out
    _fasta(bed["cfg"].fasta_dir / "human_reviewed_decoys.fas", 4, 0)
    assert cli_main(["--config", str(bed["cfg_path"]), "preflight", "--static"]) == 1
    assert "has no decoys" in capsys.readouterr().out


def test_names_go_through_names_py():
    assert names.FINGERPRINT_FILE == "run_fingerprint.json" and names.PREFLIGHT_DIR == "preflight"
    src = Path(fragpipe.__file__).parent
    for mod in ("fingerprint.py", "preflight.py", "worker.py", "fragpipe.py"):
        assert '"run_fingerprint' not in (src / mod).read_text(encoding="utf-8"), mod
    assert "names.PREFLIGHT_DIR" in (src / "preflight.py").read_text(encoding="utf-8")
    assert "names.FINGERPRINT_FILE" in (src / "fingerprint.py").read_text(encoding="utf-8")
