; Installer für TCG Card Lens (OBS-Plugin + KI-Engine). Bauen: installer\build-installer.ps1
#define AppName "TCG Card Lens"
#define AppVersion "0.1.0"
#define Root ".."
; Auswahlseite: Spiele und Namenssprachen als "kürzel=Anzeigename;…" (neue Spiele hier ergänzen,
; Kürzel wie in engine/cardengine/config.py GAMES). Vorauswahl: alle Spiele, Sprachen laut LangDefault.
#define GameList "pokemon=Pokémon;mtg=Magic: The Gathering;ygo=Yu-Gi-Oh!"
#define LangList "de=Deutsch;en=Englisch;fr=Französisch;it=Italienisch;es=Spanisch;pt=Portugiesisch;ja=Japanisch"
#define LangDefault ",de,en,"

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
de.WelcomeLabel2=Dieses Programm installiert [name/ver]:%n%n• OBS-Filter „TCG Card Lens“ (erkennt Pokémon- und Magic-Karten und zeigt Cardmarket-Preise im Videobild)%n• KI-Engine (startet automatisch mit OBS)%n%nSie wählen gleich die Spiele und Namenssprachen. Während der Installation werden Python, die KI-Pakete (ca. 3–4 GB), die Modelle und je Spiel ein Datenpaket (Kartenkatalog + Erkennungs-Index, ohne Bilder) aus dem Internet geladen. Liegt neben dieser Setup-Datei ein Ordner „packs“, werden die Pakete von dort installiert (offline).%n%nBitte OBS vor der Installation schließen.

[Tasks]
Name: "setupengine"; Description: "KI-Engine jetzt einrichten (Python, Pakete, Modelle, Kartendaten – empfohlen, ca. 10–30 Minuten)"; Flags: checkedonce

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
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\engine\setup-engine.ps1"" {code:SetupArgs}"; WorkingDir: "{app}\engine"; StatusMsg: "KI-Engine wird eingerichtet (Fenster mit Fortschritt) …"; Tasks: setupengine; Flags: waituntilterminated

[UninstallRun]
; Laufende Engine beenden, damit die Dateien gelöscht werden können
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\engine\stop-engine.ps1"""; Flags: runhidden; RunOnceId: "StopEngine"

[UninstallDelete]
Type: filesandordirs; Name: "{commonappdata}\obs-studio\plugins\tcg-card-lens"
Type: filesandordirs; Name: "{app}\engine\cardengine"

[Code]
var
  SelPage: TWizardPage;
  SelList: TNewCheckListBox;
  GameIds, LangIds: TArrayOfString;
  GameItems, LangItems: array of Integer;

{ "a=Name A;b=Name B" -> Kürzel und Anzeigenamen }
procedure SplitList(S: String; var Ids, Names: TArrayOfString);
var
  Part: String;
  P, N: Integer;
begin
  N := 0;
  SetArrayLength(Ids, 0);
  SetArrayLength(Names, 0);
  while Length(S) > 0 do
  begin
    P := Pos(';', S);
    if P = 0 then P := Length(S) + 1;
    Part := Copy(S, 1, P - 1);
    Delete(S, 1, P);
    P := Pos('=', Part);
    if P > 0 then
    begin
      N := N + 1;
      SetArrayLength(Ids, N);
      SetArrayLength(Names, N);
      Ids[N - 1] := Trim(Copy(Part, 1, P - 1));
      Names[N - 1] := Trim(Copy(Part, P + 1, Length(Part)));
    end;
  end;
end;

procedure InitializeWizard;
var
  Names: TArrayOfString;
  I: Integer;
begin
  SelPage := CreateCustomPage(wpSelectTasks, 'Spiele und Sprachen',
    'Für welche Spiele soll die Engine Kartendaten installieren, und in welchen Sprachen sollen Kartennamen angezeigt werden?');
  SelList := TNewCheckListBox.Create(SelPage);
  SelList.Parent := SelPage.Surface;
  SelList.Left := 0;
  SelList.Top := 0;
  SelList.Width := SelPage.SurfaceWidth;
  SelList.Height := SelPage.SurfaceHeight;

  SplitList('{#GameList}', GameIds, Names);
  SetArrayLength(GameItems, GetArrayLength(GameIds));
  SelList.AddGroup('Spiele (je Spiel ein Datenpaket: Kartenkatalog + Erkennungs-Index)', '', 0, nil);
  for I := 0 to GetArrayLength(GameIds) - 1 do
    GameItems[I] := SelList.AddCheckBox(Names[I], '', 1, True, True, False, True, nil);

  SplitList('{#LangList}', LangIds, Names);
  SetArrayLength(LangItems, GetArrayLength(LangIds));
  SelList.AddGroup('Namenssprachen (Kartennamen im Overlay; Englisch ist immer Rückfall)', '', 0, nil);
  for I := 0 to GetArrayLength(LangIds) - 1 do
    LangItems[I] := SelList.AddCheckBox(Names[I], '', 1, Pos(',' + LangIds[I] + ',', '{#LangDefault}') > 0,
      True, False, True, nil);
end;

function SelectedIds(const Ids: TArrayOfString; const Items: array of Integer): String;
var
  I: Integer;
begin
  Result := '';
  for I := 0 to GetArrayLength(Ids) - 1 do
    if SelList.Checked[Items[I]] then
    begin
      if Result <> '' then Result := Result + ',';
      Result := Result + Ids[I];
    end;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  { Auswahl nur, wenn die Engine jetzt eingerichtet wird }
  Result := (SelPage <> nil) and (PageID = SelPage.ID) and not WizardIsTaskSelected('setupengine');
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (SelPage <> nil) and (CurPageID = SelPage.ID) then
  begin
    if SelectedIds(GameIds, GameItems) = '' then
    begin
      MsgBox('Bitte mindestens ein Spiel auswählen.', mbError, MB_OK);
      Result := False;
    end
    else if SelectedIds(LangIds, LangItems) = '' then
    begin
      MsgBox('Bitte mindestens eine Namenssprache auswählen.', mbError, MB_OK);
      Result := False;
    end;
  end;
end;

{ Parameter für setup-engine.ps1: Auswahl und ggf. Offline-Pakete neben der Setup-Datei }
function SetupArgs(Param: String): String;
var
  PackDir: String;
begin
  Result := '-Games ' + SelectedIds(GameIds, GameItems) + ' -Languages ' + SelectedIds(LangIds, LangItems);
  PackDir := ExpandConstant('{src}\packs');
  if DirExists(PackDir) then
    Result := Result + ' -PackDir "' + PackDir + '"';
  Log('setup-engine.ps1 ' + Result);
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo, MemoComponentsInfo,
  MemoGroupInfo, MemoTasksInfo: String): String;
begin
  Result := MemoTasksInfo;
  if WizardIsTaskSelected('setupengine') then
  begin
    Result := Result + NewLine + NewLine + 'Spiele und Sprachen:' + NewLine + Space + 'Spiele: ' +
      SelectedIds(GameIds, GameItems) + NewLine + Space + 'Namenssprachen: ' + SelectedIds(LangIds, LangItems);
    if DirExists(ExpandConstant('{src}\packs')) then
      Result := Result + NewLine + Space + 'Datenpakete aus: ' + ExpandConstant('{src}\packs');
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    if MsgBox('Auch die heruntergeladenen Daten löschen (Python-Umgebung, Modelle, Kartenbilder, Preisdaten, Snapshots – mehrere GB)?',
              mbConfirmation, MB_YESNO) = IDYES then
      DelTree(ExpandConstant('{app}'), True, True, True);
  end;
end;
