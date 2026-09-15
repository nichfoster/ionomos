@echo off
REM ====================================================================
REM  Proteomics QC pipeline launcher (watcher + worker).
REM  Double-click this, or point Windows Task Scheduler at it to start
REM  the pipeline automatically on login/boot.
REM
REM  Edit PYTHON below if "python" isn't on your PATH (e.g. point it at
REM  a full path or a venv: C:\proteomics-qc\.venv\Scripts\python.exe).
REM ====================================================================

setlocal
set PYTHON=python

REM Run from the folder this .bat lives in, so relative paths resolve.
cd /d "%~dp0"

echo Starting proteomics QC pipeline...
echo (Press Ctrl-C to stop.)
echo.

%PYTHON% main.py

echo.
echo Pipeline stopped.
pause
