@echo off
REM labwatch launcher — used by the Task Scheduler task and for double-clicking.
REM Installed by deploy\install.ps1 to C:\Fragpipe_Auto\run_labwatch.bat
setlocal
set ROOT=%~dp0
set ROOT=%ROOT:~0,-1%
"%ROOT%\venv\Scripts\labwatch.exe" --config "%ROOT%\config.yaml" run
