; Ionomos Windows installer (Inno Setup 6). Built by deploy\build_exe.ps1 / GitHub Actions:
;   iscc /DAppVersion=0.5.0 deploy\ionomos.iss      -> dist\Ionomos-Setup-0.5.0.exe
;
; Install:   next, next, finish -> C:\Ionomos, Start menu + Desktop shortcut, opens the app.
; Upgrade:   run a newer Setup over it. The running watcher is stopped gracefully first
;            (its search is re-queued) and started again afterwards; config + data untouched.
; LabWatch:  an old LabWatch install (<= 0.4.0) is retired: its processes, "labwatch" startup
;            task, Desktop shortcut and exes go; its config, ledger and experiment data stay.
; Uninstall: Settings -> Apps -> Ionomos. Removes the program, task and shortcuts only.
;            config.yaml, the job ledger, logs and every experiment folder are kept.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{89AE05EB-2D7C-435C-BE61-5BF87115A88B}
AppName=Ionomos
AppVersion={#AppVersion}
AppVerName=Ionomos {#AppVersion}
AppPublisher=Nomura Lab
AppSupportURL=https://github.com/nichfoster/ionomos
DefaultDirName={sd}\Ionomos
UsePreviousAppDir=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename=Ionomos-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=force
RestartApplications=no
UninstallDisplayIcon={app}\Ionomos.exe
UninstallDisplayName=Ionomos
SetupLogging=yes
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "Put an Ionomos shortcut on the Desktop"; Flags: checkedonce

[Files]
Source: "..\dist\exe\Ionomos.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\exe\ionomos-cli.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\docs\DEPLOY_WINDOWS.md"; DestDir: "{app}\docs"; Flags: ignoreversion
Source: "..\docs\NAMING_CONVENTION.md"; DestDir: "{app}\docs"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Ionomos"; Filename: "{app}\Ionomos.exe"; Comment: "Ionomos setup, jobs and reports"
Name: "{autodesktop}\Ionomos"; Filename: "{app}\Ionomos.exe"; Tasks: desktopicon

[Run]
; restart the watcher if the startup task exists (harmless if it doesn't)
Filename: "schtasks.exe"; Parameters: "/Run /TN Ionomos"; Flags: runhidden skipifdoesntexist; Check: TaskExists
Filename: "{app}\Ionomos.exe"; Description: "Open Ionomos"; Flags: nowait postinstall skipifsilent
; silent updates started from the app reopen it without asking
Filename: "{app}\Ionomos.exe"; Flags: nowait; Check: WizardSilent

[UninstallRun]
Filename: "{app}\ionomos-cli.exe"; Parameters: "stop --wait 60"; Flags: runhidden waituntilterminated skipifdoesntexist; RunOnceId: "StopWatcher"
Filename: "schtasks.exe"; Parameters: "/Delete /TN Ionomos /F"; Flags: runhidden; RunOnceId: "DeleteTask"
Filename: "taskkill.exe"; Parameters: "/IM Ionomos.exe /T /F"; Flags: runhidden; RunOnceId: "KillApp"
Filename: "taskkill.exe"; Parameters: "/IM ionomos-cli.exe /T /F"; Flags: runhidden; RunOnceId: "KillCli"

[Code]
function TaskExists: Boolean;
var Code: Integer;
begin
  Result := Exec('schtasks.exe', '/Query /TN Ionomos', '', SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0);
end;

procedure Run(const Exe, Params: String);
var Code: Integer;
begin
  Exec(Exe, Params, '', SW_HIDE, ewWaitUntilTerminated, Code);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var OldCli: String;
begin
  if CurStep = ssInstall then
  begin
    { 1. an existing Ionomos: stop its watcher gracefully (re-queues a running search), remember to restart it }
    OldCli := ExpandConstant('{app}\ionomos-cli.exe');
    if FileExists(OldCli) then
      Run(OldCli, 'stop --wait 60 --remember');
    Run('taskkill.exe', '/IM Ionomos.exe /T /F');
    Run('taskkill.exe', '/IM ionomos-cli.exe /T /F');
    { 2. retire LabWatch (<= 0.4.0): processes, startup task, shortcut, program files. Data stays. }
    Run('taskkill.exe', '/IM LabWatch.exe /T /F');
    Run('taskkill.exe', '/IM labwatch-cli.exe /T /F');
    Run('schtasks.exe', '/Delete /TN labwatch /F');
    DeleteFile(ExpandConstant('{userdesktop}\LabWatch.lnk'));
    DeleteFile(ExpandConstant('{sd}\Fragpipe_Auto\LabWatch.exe'));
    DeleteFile(ExpandConstant('{sd}\Fragpipe_Auto\labwatch-cli.exe'));
    DeleteFile(ExpandConstant('{sd}\Fragpipe_Auto\labwatch.exe'));
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent then
    MsgBox('Ionomos was removed. Your settings (config.yaml), the job list, logs and all experiment folders '
      + 'were kept - reinstall any time and it picks up where it left off.', mbInformation, MB_OK);
end;
