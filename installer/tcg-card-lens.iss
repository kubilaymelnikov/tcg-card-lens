; Installer für TCG Card Lens (OBS-Plugin + KI-Engine). Bauen: installer\build-installer.ps1
#define AppName "TCG Card Lens"
#define AppVersion "0.2.0"
#define Root ".."
; Auswahlseite: Spiele und Namenssprachen als "kürzel=Anzeigename;…" (neue Spiele hier ergänzen,
; Kürzel wie in engine/cardengine/config.py GAMES). Vorauswahl: alle Spiele, Sprachen laut LangDefault.
#define GameList "pokemon=Pokémon;mtg=Magic: The Gathering;ygo=Yu-Gi-Oh!"
; Namenssprachen: Kürzel=Anzeigename je Setup-Sprache – siehe CustomMessages "LangList" unten
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
SetupLogging=yes
; Sprache des Setups automatisch nach der Windows-Oberfläche; nur wenn keine passt, fragt das Setup
ShowLanguageDialog=auto
LanguageDetectionMethod=uilanguage

[Languages]
; Englisch zuerst: Rückfall für alle Windows-Sprachen außer Deutsch
Name: "en"; MessagesFile: "compiler:Default.isl"; LicenseFile: "{#Root}\NOTICE.en.md"
Name: "de"; MessagesFile: "compiler:Languages\German.isl"; LicenseFile: "{#Root}\NOTICE.md"

[Messages]
en.WelcomeLabel2=This will install [name/ver]:%n%n• OBS filter "TCG Card Lens" (recognises Pokémon, Magic and Yu-Gi-Oh! cards and shows Cardmarket prices in the video)%n• AI engine (starts automatically with OBS)%n%nNext you choose the games and card-name languages. During installation Python, the AI packages (about 3–4 GB), the models and one data pack per game (card catalogue + recognition index, no images) are downloaded. If a folder "packs" sits next to this setup file, the packs are installed from there (offline).%n%nPlease close OBS before installing.
de.WelcomeLabel2=Dieses Programm installiert [name/ver]:%n%n• OBS-Filter „TCG Card Lens“ (erkennt Pokémon-, Magic- und Yu-Gi-Oh!-Karten und zeigt Cardmarket-Preise im Videobild)%n• KI-Engine (startet automatisch mit OBS)%n%nSie wählen gleich die Spiele und Namenssprachen. Während der Installation werden Python, die KI-Pakete (ca. 3–4 GB), die Modelle und je Spiel ein Datenpaket (Kartenkatalog + Erkennungs-Index, ohne Bilder) aus dem Internet geladen. Liegt neben dieser Setup-Datei ein Ordner „packs“, werden die Pakete von dort installiert (offline).%n%nBitte OBS vor der Installation schließen.

[CustomMessages]
en.TaskSetupEngine=Set up the AI engine now (Python, packages, models, card data – recommended, about 10–30 minutes)
de.TaskSetupEngine=KI-Engine jetzt einrichten (Python, Pakete, Modelle, Kartendaten – empfohlen, ca. 10–30 Minuten)
en.SetupEngineStatus=Setting up the AI engine (progress window) …
de.SetupEngineStatus=KI-Engine wird eingerichtet (Fenster mit Fortschritt) …
en.PageTitle=Games and languages
de.PageTitle=Spiele und Sprachen
en.PageDesc=Which games should the engine install card data for, and in which languages should card names be shown?
de.PageDesc=Für welche Spiele soll die Engine Kartendaten installieren, und in welchen Sprachen sollen Kartennamen angezeigt werden?
en.GroupGames=Games (one data pack per game: card catalogue + recognition index)
de.GroupGames=Spiele (je Spiel ein Datenpaket: Kartenkatalog + Erkennungs-Index)
en.GroupLangs=Card-name languages (names in the overlay; English is always the fallback)
de.GroupLangs=Namenssprachen (Kartennamen im Overlay; Englisch ist immer Rückfall)
en.LangList=de=German;en=English;fr=French;it=Italian;es=Spanish;pt=Portuguese;ja=Japanese
de.LangList=de=Deutsch;en=Englisch;fr=Französisch;it=Italienisch;es=Spanisch;pt=Portugiesisch;ja=Japanisch
en.NeedGame=Please select at least one game.
de.NeedGame=Bitte mindestens ein Spiel auswählen.
en.NeedLang=Please select at least one card-name language.
de.NeedLang=Bitte mindestens eine Namenssprache auswählen.
en.MemoTitle=Games and languages:
de.MemoTitle=Spiele und Sprachen:
en.MemoGames=Games:
de.MemoGames=Spiele:
en.MemoLangs=Card-name languages:
de.MemoLangs=Namenssprachen:
en.MemoPacks=Data packs from:
de.MemoPacks=Datenpakete aus:
en.AskDeleteData=Also delete the downloaded data (Python environment, models, card images, price data, snapshots – several GB)?
de.AskDeleteData=Auch die heruntergeladenen Daten löschen (Python-Umgebung, Modelle, Kartenbilder, Preisdaten, Snapshots – mehrere GB)?
en.IconStartEngine=Start engine (with console)
de.IconStartEngine=Engine starten (mit Konsole)
en.IconSetupEngine=Set up engine again
de.IconSetupEngine=Engine neu einrichten
en.IconSnapshots=Snapshots (debug)
de.IconSnapshots=Snapshots (Debug)
en.IconLog=Engine log
de.IconLog=Engine-Log
en.IconUninstall=Uninstall
de.IconUninstall=Deinstallieren

[Tasks]
Name: "setupengine"; Description: "{cm:TaskSetupEngine}"; Flags: checkedonce

[Files]
; OBS-Plugin (für alle OBS-Installationen dieses Rechners)
Source: "{#Root}\obs-plugin\build_x64\RelWithDebInfo\tcg-card-lens.dll"; DestDir: "{commonappdata}\obs-studio\plugins\tcg-card-lens\bin\64bit"; Flags: ignoreversion
Source: "{#Root}\obs-plugin\build_x64\RelWithDebInfo\tcg-card-lens.pdb"; DestDir: "{commonappdata}\obs-studio\plugins\tcg-card-lens\bin\64bit"; Flags: ignoreversion skipifsourcedoesntexist
Source: "{#Root}\obs-plugin\data\locale\*.ini"; DestDir: "{commonappdata}\obs-studio\plugins\tcg-card-lens\data\locale"; Flags: ignoreversion
; Engine (Python-Quellcode; Pakete, Modelle und Daten richtet setup-engine.ps1 ein)
Source: "{#Root}\engine\cardengine\*"; DestDir: "{app}\engine\cardengine"; Excludes: "__pycache__,*.pyc"; Flags: ignoreversion recursesubdirs
Source: "{#Root}\engine\tools\*.py"; DestDir: "{app}\engine\tools"; Flags: ignoreversion
Source: "{#Root}\engine\server.py"; DestDir: "{app}\engine"; Flags: ignoreversion
Source: "{#Root}\engine\dock_server.py"; DestDir: "{app}\engine"; Flags: ignoreversion
Source: "{#Root}\engine\dock\*"; DestDir: "{app}\engine\dock"; Flags: ignoreversion recursesubdirs
Source: "{#Root}\engine\run.py"; DestDir: "{app}\engine"; Flags: ignoreversion
Source: "{#Root}\engine\requirements.txt"; DestDir: "{app}\engine"; Flags: ignoreversion
Source: "{#Root}\engine\setup-engine.ps1"; DestDir: "{app}\engine"; Flags: ignoreversion
Source: "{#Root}\engine\start-engine.cmd"; DestDir: "{app}\engine"; Flags: ignoreversion
Source: "{#Root}\engine\stop-engine.ps1"; DestDir: "{app}\engine"; Flags: ignoreversion
Source: "{#Root}\engine\README.md"; DestDir: "{app}\engine"; Flags: ignoreversion
Source: "{#Root}\NOTICE.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#Root}\NOTICE.en.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#Root}\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#Root}\docs\*.md"; DestDir: "{app}\docs"; Flags: ignoreversion

[Icons]
Name: "{userprograms}\{#AppName}\{cm:IconStartEngine}"; Filename: "{app}\engine\start-engine.cmd"; WorkingDir: "{app}\engine"
Name: "{userprograms}\{#AppName}\{cm:IconSetupEngine}"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -NoExit -File ""{app}\engine\setup-engine.ps1"" -UiLang {language}"; WorkingDir: "{app}\engine"
Name: "{userprograms}\{#AppName}\{cm:IconSnapshots}"; Filename: "{app}\engine\data\snapshots"
Name: "{userprograms}\{#AppName}\{cm:IconLog}"; Filename: "{app}\engine\data\engine.log"
Name: "{userprograms}\{#AppName}\{cm:IconUninstall}"; Filename: "{uninstallexe}"

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\engine\setup-engine.ps1"" {code:SetupArgs}"; WorkingDir: "{app}\engine"; StatusMsg: "{cm:SetupEngineStatus}"; Tasks: setupengine; Flags: waituntilterminated

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
  SelPage := CreateCustomPage(wpSelectTasks, CustomMessage('PageTitle'), CustomMessage('PageDesc'));
  SelList := TNewCheckListBox.Create(SelPage);
  SelList.Parent := SelPage.Surface;
  SelList.Left := 0;
  SelList.Top := 0;
  SelList.Width := SelPage.SurfaceWidth;
  SelList.Height := SelPage.SurfaceHeight;

  SplitList('{#GameList}', GameIds, Names);
  SetArrayLength(GameItems, GetArrayLength(GameIds));
  SelList.AddGroup(CustomMessage('GroupGames'), '', 0, nil);
  for I := 0 to GetArrayLength(GameIds) - 1 do
    GameItems[I] := SelList.AddCheckBox(Names[I], '', 1, True, True, False, True, nil);

  SplitList(CustomMessage('LangList'), LangIds, Names);
  SetArrayLength(LangItems, GetArrayLength(LangIds));
  SelList.AddGroup(CustomMessage('GroupLangs'), '', 0, nil);
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
      MsgBox(CustomMessage('NeedGame'), mbError, MB_OK);
      Result := False;
    end
    else if SelectedIds(LangIds, LangItems) = '' then
    begin
      MsgBox(CustomMessage('NeedLang'), mbError, MB_OK);
      Result := False;
    end;
  end;
end;

{ Parameter für setup-engine.ps1: Auswahl und ggf. Offline-Pakete neben der Setup-Datei }
function SetupArgs(Param: String): String;
var
  PackDir: String;
begin
  Result := '-Games ' + SelectedIds(GameIds, GameItems) + ' -Languages ' + SelectedIds(LangIds, LangItems) +
    ' -UiLang ' + ActiveLanguage;
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
    Result := Result + NewLine + NewLine + CustomMessage('MemoTitle') + NewLine + Space + CustomMessage('MemoGames') + ' ' +
      SelectedIds(GameIds, GameItems) + NewLine + Space + CustomMessage('MemoLangs') + ' ' + SelectedIds(LangIds, LangItems);
    if DirExists(ExpandConstant('{src}\packs')) then
      Result := Result + NewLine + Space + CustomMessage('MemoPacks') + ' ' + ExpandConstant('{src}\packs');
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    if MsgBox(CustomMessage('AskDeleteData'), mbConfirmation, MB_YESNO) = IDYES then
      DelTree(ExpandConstant('{app}'), True, True, True);
  end;
end;
