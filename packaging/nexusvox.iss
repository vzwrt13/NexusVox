; Inno Setup script for the NexusVox per-user installer.
;
; Build with:  python packaging/build_installer.py   (after `python packaging/build.py`)
; or directly: ISCC /DAppVersion=0.7.0 packaging\nexusvox.iss
;
; Per-user, no admin rights: the app goes to %LOCALAPPDATA%\Programs\NexusVox. The file name of the
; installer is stable on purpose (NexusVox-Setup.exe, no version): the website links to
; releases/latest/download/NexusVox-Setup.exe and download reputation is tracked per file name.
; The installer is unsigned for now.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#define AppName "NexusVox"
#define AppExe "NexusVox.exe"

[Setup]
; Never change this GUID: it identifies the installed app for upgrades and for Add/Remove Programs.
AppId={{E4B0785D-ABD0-4965-B617-C058A320AB4B}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=NightShift AI GmbH
AppPublisherURL=https://nightshift-ai.de
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\LICENSE
InfoBeforeFile=..\THIRD_PARTY_LICENSES.md
UninstallDisplayIcon={app}\{#AppExe}
OutputDir=..\dist\installer
OutputBaseFilename=NexusVox-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; A running NexusVox holds its files open. Let the Restart Manager offer to close it, for installs and uninstalls.
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "autostart"; Description: "Start NexusVox when I sign in"; Flags: unchecked

[Files]
Source: "..\dist\NexusVox\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\THIRD_PARTY_LICENSES.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"

[Registry]
; The same per-user Run key a Settings toggle will use later. No scheduled task, no admin rights.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#AppName}"; ValueData: """{app}\{#AppExe}"""; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\{#AppExe}"; Description: "Start {#AppName}"; Flags: nowait postinstall skipifsilent

[Code]
{ The uninstaller removes the program only. The user's own files live outside the install folder:
    config:  %APPDATA%\NexusVox
    data:    %LOCALAPPDATA%\NexusVox\data (history database, stored audio)
    logs:    %LOCALAPPDATA%\NexusVox\logs
    models:  %LOCALAPPDATA%\NexusVox\models (downloaded speech models, several hundred MB)
  Two questions, both defaulting to keep. A silent uninstall keeps everything unless it is told otherwise with
  /DELETEDATA and /DELETEMODELS. }

function HasSwitch(const Name: String): Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), Name) = 0 then
      Result := True;
end;

function WantsDelete(const Switch, Question: String): Boolean;
begin
  if HasSwitch(Switch) then
    Result := True
  else if UninstallSilent then
    Result := False
  else
    Result := MsgBox(Question, mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  LocalRoot: String;
begin
  if CurUninstallStep <> usPostUninstall then
    Exit;

  LocalRoot := ExpandConstant('{localappdata}\NexusVox');

  if WantsDelete('/DELETEDATA',
      'Also delete your NexusVox data?' + #13#10#13#10 +
      'This removes your settings, dictation history, stored audio and logs. It cannot be undone.' + #13#10#13#10 +
      'Choose No to keep them for a later reinstall.') then
  begin
    DelTree(ExpandConstant('{userappdata}\NexusVox'), True, True, True);
    DelTree(LocalRoot + '\data', True, True, True);
    DelTree(LocalRoot + '\logs', True, True, True);
  end;

  if WantsDelete('/DELETEMODELS',
      'Also delete the downloaded speech models?' + #13#10#13#10 +
      'They take several hundred MB and are downloaded again when you reinstall and dictate.' + #13#10#13#10 +
      'Choose No to keep them.') then
    DelTree(LocalRoot + '\models', True, True, True);

  { Removes the folder only when nothing is left in it. }
  RemoveDir(LocalRoot);
end;
