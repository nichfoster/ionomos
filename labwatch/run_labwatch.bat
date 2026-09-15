@echo off
REM labwatch launcher for Windows Task Scheduler / double-click.
REM Uses the venv created per README.md. Edit VENV if you put it elsewhere.
setlocal
set VENV=C:\Fragpipe_Auto\labwatch\.venv
set CONFIG=C:\Fragpipe_Auto\config.yaml
cd /d "%~dp0"
"%VENV%\Scripts\labwatch.exe" run --config "%CONFIG%"
