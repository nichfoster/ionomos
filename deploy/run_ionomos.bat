@echo off
REM ionomos launcher — used by the Task Scheduler task and for double-clicking.
REM Installed by deploy\install.ps1 to C:\Fragpipe_Auto\run_ionomos.bat
setlocal
set ROOT=%~dp0
set ROOT=%ROOT:~0,-1%
"%ROOT%\venv\Scripts\ionomos.exe" --config "%ROOT%\config.yaml" run
