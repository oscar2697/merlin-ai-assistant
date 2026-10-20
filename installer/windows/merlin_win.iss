; Inno Setup script for MERLIN (Windows).
; Prerequisite: run `installer\build_windows.ps1` first so dist\Merlin\ exists.
; Compile with Inno Setup (ISCC.exe):  ISCC installer\windows\merlin_win.iss

[Setup]
AppId={{8E7A2C1F-MERLIN-42A0-9C6D-000000000001}
AppName=MERLIN
AppVersion=1.0.0
AppPublisher=MERLIN Local
DefaultDirName={autopf}\MERLIN
DefaultGroupName=MERLIN
OutputDir=..\dist
OutputBaseFilename=MERLIN-Setup
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin

[Files]
Source: "..\..\dist\Merlin\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "..\windows\setup_autostart.ps1"; DestDir: "{app}\installer"; Flags: ignoreversion
Source: "..\windows\start_ollama.ps1"; DestDir: "{app}\installer"; Flags: ignoreversion

[Tasks]
Name: "autostart"; Description: "Start MERLIN and Ollama when I log on"; Flags: unchecked
Name: "desktopicon"; Description: "Create a &desktop icon"; Flags: unchecked

[Icons]
Name: "{group}\MERLIN"; Filename: "{app}\Merlin.exe"
Name: "{autodesktop}\MERLIN"; Filename: "{app}\Merlin.exe"; Tasks: desktopicon

[Run]
Filename: "powershell.exe"; \
  Parameters: "-ExecutionPolicy Bypass -File ""{app}\installer\setup_autostart.ps1"" -InstallDir ""{app}"""; \
  WorkingDir: "{app}"; Tasks: autostart; Flags: runhidden
Filename: "{app}\Merlin.exe"; Description: "Launch MERLIN"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "powershell.exe"; \
  Parameters: "-ExecutionPolicy Bypass -Command ""Unregister-ScheduledTask -TaskName 'MERLIN Assistant' -Confirm:`$false; Unregister-ScheduledTask -TaskName 'MERLIN Ollama' -Confirm:`$false"""; \
  Flags: runhidden; RunOnceId: "RemoveAutostart"
