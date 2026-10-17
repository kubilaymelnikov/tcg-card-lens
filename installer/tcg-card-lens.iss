; Installer für TCG Card Lens (OBS-Plugin + KI-Engine). Bauen: installer\build-installer.ps1
#define AppName "TCG Card Lens"
#define AppVersion "0.1.0"
#define Root ".."

[Setup]
AppId={{EC13D249-C6FC-45AF-8462-349F053579DA}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=TCG Card Lens
DefaultDirName={localappdata}\TCGCardLens
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=Output
OutputBaseFilename=TCGCardLens-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
CloseApplicationsFilter=*.dll
UninstallDisplayName={#AppName}
LicenseFile={#Root}\NOTICE.md
SetupLogging=yes

[Languages]
Name: "de"; MessagesFile: "compiler:Languages\German.isl"

[Messages]
de.WelcomeLabel2=Dieses Programm installiert [name/ver]:%n%n• OBS-Filter „TCG Card Lens“ (erkennt Pokémon- und Magic-Karten und zeigt Cardmarket-Preise im Videobild)%n• KI-Engine (startet automatisch mit OBS)%n%nWährend der Installation werden Python, die KI-Pakete (ca. 3–4 GB) und die Modelle aus dem Internet geladen. Kartenbilder werden danach im Hintergrund aufgebaut.%n%nBitte OBS vor der Installation schließen.

[Tasks]
Name: "setupengine"; Description: "KI-Engine jetzt einrichten (Python, Pakete, Modelle, Grunddaten – empfohlen, ca. 15–30 Minuten)"; Flags: checkedonce

[Files]
; OBS-Plugin (für alle OBS-Installationen dieses Rechners)
Source: "{#Root}\obs-plugin\build_x64\RelWithDebInfo\tcg-card-lens.dll"; DestDir: "{commonappdata}\obs-studio\plugins\tcg-card-lens\bin\64bit"; Flags: ignoreversion
Source: "{#Root}\obs-plugin\build_x64\RelWithDebInfo\tcg-card-lens.pdb"; DestDir: "{commonappdata}\obs-studio\plugins\tcg-card-lens\bin\64bit"; Flags: ignoreversion skipifsourcedoesntexist
Source: "{#Root}\obs-plugin\data\locale\*.ini"; DestDir: "{commonappdata}\obs-studio\plugins\tcg-card-lens\data\locale"; Flags: ignoreversion
; Engine (Python-Quellcode; Pakete, Modelle und Daten richtet setup-engine.ps1 ein)
Source: "{#Root}\engine\cardengine\*"; DestDir: "{app}\engine\cardengine"; Excludes: "__pycache__,*.pyc"; Flags: ignoreversion recursesubdirs
Source: "{#Root}\engine\tools\*.py"; DestDir: "{app}\engine\tools"; Flags: ignoreversion
Source: "{#Root}\engine\server.py"; DestDir: "{app}\engine"; Flags: ignoreversion
Source: "{#Root}\engine\run.py"; DestDir: "{app}\engine"; Flags: ignoreversion
Source: "{#Root}\engine\requirements.txt"; DestDir: "{app}\engine"; Flags: ignoreversion
Source: "{#Root}\engine\setup-engine.ps1"; DestDir: "{app}\engine"; Flags: ignoreversion
Source: "{#Root}\engine\start-engine.cmd"; DestDir: "{app}\engine"; Flags: ignoreversion
Source: "{#Root}\engine\stop-engine.ps1"; DestDir: "{app}\engine"; Flags: ignoreversion
Source: "{#Root}\engine\README.md"; DestDir: "{app}\engine"; Flags: ignoreversion
Source: "{#Root}\NOTICE.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#Root}\docs\*.md"; DestDir: "{app}\docs"; Flags: ignoreversion

[Icons]
Name: "{userprograms}\{#AppName}\Engine starten (mit Konsole)"; Filename: "{app}\engine\start-engine.cmd"; WorkingDir: "{app}\engine"
Name: "{userprograms}\{#AppName}\Engine neu einrichten"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -NoExit -File ""{app}\engine\setup-engine.ps1"""; WorkingDir: "{app}\engine"
Name: "{userprograms}\{#AppName}\Snapshots (Debug)"; Filename: "{app}\engine\data\snapshots"
Name: "{userprograms}\{#AppName}\Engine-Log"; Filename: "{app}\engine\data\engine.log"
Name: "{userprograms}\{#AppName}\Deinstallieren"; Filename: "{uninstallexe}"

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\engine\setup-engine.ps1"""; WorkingDir: "{app}\engine"; StatusMsg: "KI-Engine wird eingerichtet (Fenster mit Fortschritt) …"; Tasks: setupengine; Flags: waituntilterminated

[UninstallRun]
; Laufende Engine beenden, damit die Dateien gelöscht werden können
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\engine\stop-engine.ps1"""; Flags: runhidden; RunOnceId: "StopEngine"

[UninstallDelete]
Type: filesandordirs; Name: "{commonappdata}\obs-studio\plugins\tcg-card-lens"
Type: filesandordirs; Name: "{app}\engine\cardengine"

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    if MsgBox('Auch die heruntergeladenen Daten löschen (Python-Umgebung, Modelle, Kartenbilder, Preisdaten, Snapshots – mehrere GB)?',
              mbConfirmation, MB_YESNO) = IDYES then
      DelTree(ExpandConstant('{app}'), True, True, True);
  end;
end;
