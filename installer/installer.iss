; ============================================================================
;  Flowkey installer (Inno Setup 6.x)
;
;  Compile with:
;     iscc installer.iss
;
;  Or via the build script:
;     .\installer\build.ps1 -BundleAhk -BundleFlm
;
;  Produces:
;     out\Flowkey-Setup-<version>.exe
;
;  Layout written to disk:
;     {app}\                            Program Files\FastFlowPrompt (read-only)
;       ffp-daemon.exe                 PyInstaller bundle, flattened into {app}
;       ffp-grammar-fix.exe
;       ffp-first-run.exe
;       _internal\                     shared Python runtime + bundled datas
;       ahk\
;         AutoHotkey64.exe
;         LICENSE.txt
;       scripts\                        AHK source (consumed at runtime)
;         grammarFix.ahk
;         lib\*.ahk
;         ui\*.ahk
;         assets\flowkey.ico
;       setup\defaults\                 seed config (read by paths.py + paths.ahk)
;       LICENSE.txt
;       README.md
;
;     %LOCALAPPDATA%\FastFlowPrompt\    per-user writable state (created on first run)
;       config\
;       data\
;       logs\
;
;  Per-machine, admin-required, x64 only.
; ============================================================================

#define AppName       "Flowkey"
#define AppPublisher  "Flowkey"
#define AppURL        "https://github.com/agr77one/Fastflow"
#define AppExeName    "Flowkey.exe"  ; symbolic — actual launchers below
; Keep in lockstep with scripts\_version.py.
#define AppVersion    "2.3.0"

[Setup]
AppId={{8A4F1E6C-9B3D-4E62-9F7A-FASTFLOW140}}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
DefaultDirName={commonpf}\FastFlowPrompt
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Every Source path below (dist\, vendor\, scripts\, SetupIconFile, LICENSE,
; README.md) is written relative to the REPO ROOT, but Inno Setup resolves
; relative paths against the directory containing this .iss by default — which
; is installer\, not the root. Point SourceDir at the root (this script lives
; in installer\, so "..") so every Source resolves. This also lands OutputDir
; at <root>\out, where build.ps1 and release-installer.yml look for the exe.
SourceDir=..
OutputDir=out
OutputBaseFilename=Flowkey-Setup-{#AppVersion}
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupIconFile=scripts\assets\flowkey.ico
UninstallDisplayIcon={app}\ffp-daemon.exe
UninstallDisplayName={#AppName} {#AppVersion}
CloseApplications=force
RestartApplications=no
; Windows 10 1809+ (build 17763) — NPU drivers need 22H2 anyway. Inno Setup
; treats ';' as a comment ONLY at the start of a line, so this note must sit
; on its own line; a trailing comment would be parsed as part of the value and
; fail with "Value of [Setup] section directive MinVersion is invalid".
MinVersion=10.0.17763

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
; NOTE: autostart is intentionally NOT an install-time task. The daemon owns a
; single per-user HKCU\...\Run\FastFlowPrompt entry (Dashboard -> Config ->
; "Launch Flowkey when I sign in") -- that's the one source of truth. A prior
; machine-wide HKLM task here used a different value name and could run
; alongside the per-user one, double-launching the app at logon. See T10/B6.
Name: "desktopicon";  Description: "Create a desktop shortcut"; \
                      GroupDescription: "Additional options:"; Flags: unchecked

[Files]
; --- PyInstaller bundle ---------------------------------------------------------
; Flatten the PyInstaller bundle straight into {app} (NOT {app}\FastFlowPrompt).
; paths.py's production mode assumes APP_DIR = the dir holding scripts\/ahk\/setup\
; (it computes APP_DIR = parent-of-the-frozen-modules-dir). Nesting the bundle one
; level down made APP_DIR resolve to {app}\FastFlowPrompt, so the daemon looked for
; ahk\/scripts\ and the config seed in the wrong place (empty autostart command,
; unfound seed). Flattening puts ffp-*.exe + _internal\ directly in {app}, beside
; ahk\ and scripts\ — exactly the layout paths.py documents.
Source: "dist\FastFlowPrompt\*"; DestDir: "{app}"; \
  Flags: ignoreversion recursesubdirs createallsubdirs

; --- AHK runtime ---------------------------------------------------------------
Source: "vendor\ahk\AutoHotkey64.exe"; DestDir: "{app}\ahk"; Flags: ignoreversion
Source: "vendor\ahk\LICENSE.txt";      DestDir: "{app}\ahk"; Flags: ignoreversion skipifsourcedoesntexist

; --- AHK source scripts --------------------------------------------------------
Source: "scripts\grammarFix.ahk"; DestDir: "{app}\scripts";        Flags: ignoreversion
Source: "scripts\lib\*";          DestDir: "{app}\scripts\lib";    Flags: ignoreversion recursesubdirs
Source: "scripts\ui\*";           DestDir: "{app}\scripts\ui";     Flags: ignoreversion recursesubdirs
; Tray/window icon — grammarFix.ahk loads {app}\scripts\assets\flowkey.ico at
; runtime (A_ScriptDir "\assets\flowkey.ico"); without this it silently has no
; tray icon. Same .ico is the compile-time SetupIconFile above.
Source: "scripts\assets\*";       DestDir: "{app}\scripts\assets"; Flags: ignoreversion recursesubdirs

; --- Docs ---------------------------------------------------------------------
Source: "LICENSE";   DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist; DestName: "LICENSE.txt"
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion

; --- Seed config (read-only) ---------------------------------------------------
;     Python seeds CONFIG_DIR from here on first run; AHK reads the .example from
;     here. MUST live at {app}\setup\defaults so paths.py (APP_DIR\setup\defaults)
;     and paths.ahk (appDir\setup\defaults) both find it. The PyInstaller `datas`
;     copy lands in _internal\setup\defaults, which is NOT on that lookup path —
;     so ship it loose here too.
Source: "setup\defaults\*"; DestDir: "{app}\setup\defaults"; \
  Flags: ignoreversion recursesubdirs skipifsourcedoesntexist

; --- FLM chained installer (extracted to tmp, run during install, then deleted)
Source: "vendor\flm\flm-setup.exe"; DestDir: "{tmp}"; \
  Flags: deleteafterinstall ignoreversion skipifsourcedoesntexist; Check: NeedsFLM

[Run]
; --- 1. Chain FLM install (skipped if FLM already on this machine) ------------
Filename: "{tmp}\flm-setup.exe"; \
  Parameters: "/VERYSILENT /SUPPRESSMSGBOXES /NOCANCEL /NORESTART /SP- /NOICONS /CLOSEAPPLICATIONS /FORCECLOSEAPPLICATIONS /LANG=english /LOG=""{tmp}\flm-install.log"""; \
  StatusMsg: "Installing FastFlowLM runtime (~170 MB)..."; \
  Check: NeedsFLM; \
  Flags: waituntilterminated

; --- 2. Mark that WE installed FLM (so uninstaller can clean it up later) -----
;     Pascal code drops {app}\.flm_installed_by_us via CurStepChanged.
;     See [Code] section below.

; --- 3. Optional: launch first-run wizard right after install -----------------
Filename: "{app}\ffp-first-run.exe"; \
  Description: "Run the {#AppName} setup wizard"; \
  Flags: postinstall nowait skipifsilent

[Icons]
Name: "{commonprograms}\{#AppName}";          Filename: "{app}\ahk\AutoHotkey64.exe"; \
  Parameters: """{app}\scripts\grammarFix.ahk"""; WorkingDir: "{app}"; \
  IconFilename: "{app}\ffp-daemon.exe"
Name: "{commonprograms}\{#AppName} Dashboard"; Filename: "{app}\ahk\AutoHotkey64.exe"; \
  Parameters: """{app}\scripts\grammarFix.ahk"" /dashboard"; WorkingDir: "{app}"; \
  IconFilename: "{app}\ffp-daemon.exe"
Name: "{commonprograms}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{commondesktop}\{#AppName}";            Filename: "{app}\ahk\AutoHotkey64.exe"; \
  Parameters: """{app}\scripts\grammarFix.ahk"""; WorkingDir: "{app}"; \
  IconFilename: "{app}\ffp-daemon.exe"; Tasks: desktopicon

[UninstallRun]
; --- 1. Stop our processes before removing files -----------------------------
;     CloseApplications=force handles in-use files but a windowless daemon
;     won't always trip the close-apps prompt. Kill explicitly.
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM ffp-daemon.exe /T"; \
  RunOnceId: "KillDaemon"; Flags: runhidden waituntilterminated
Filename: "{sys}\taskkill.exe"; \
  Parameters: "/F /IM AutoHotkey64.exe /FI ""WINDOWTITLE eq grammarFix*"""; \
  RunOnceId: "KillAhk"; Flags: runhidden waituntilterminated

; --- 1b. Remove the per-user autostart entry, if the dashboard toggle or a
;     source install set it (installer itself never sets it — see [Tasks]).
;     reg.exe exits non-zero when the value is absent; that's fine, Inno
;     doesn't treat a nonzero [UninstallRun] exit as fatal.
Filename: "{sys}\reg.exe"; \
  Parameters: "delete ""HKCU\Software\Microsoft\Windows\CurrentVersion\Run"" /v ""FastFlowPrompt"" /f"; \
  RunOnceId: "RemoveAutostart"; Flags: runhidden waituntilterminated

; --- 2. Chain FLM uninstaller — but ONLY if we installed it ------------------
;     We tagged it with {app}\.flm_installed_by_us. Pascal helper reads the
;     QuietUninstallString out of the registry and runs it silently.
Filename: "{cmd}"; Parameters: "/c if exist ""{app}\.flm_installed_by_us"" call ""{code:FlmUninstallCmd}"""; \
  RunOnceId: "FlmUninstallChain"; Flags: runhidden waituntilterminated

[UninstallDelete]
; Files the user can't easily clean by themselves. The user-data wipe (under
; %LOCALAPPDATA%\FastFlowPrompt) is handled by CurUninstallStepChanged below,
; behind an opt-in prompt — never wipe by default.
Type: files;          Name: "{app}\.flm_installed_by_us"
; Bundle now flattens into {app}; _internal\ holds the PyInstaller runtime.
Type: filesandordirs; Name: "{app}\_internal"
Type: dirifempty;     Name: "{app}\ahk"
Type: dirifempty;     Name: "{app}\scripts\lib"
Type: dirifempty;     Name: "{app}\scripts\ui"
Type: dirifempty;     Name: "{app}\scripts\assets"
Type: dirifempty;     Name: "{app}\scripts"
Type: dirifempty;     Name: "{app}\setup\defaults"
Type: dirifempty;     Name: "{app}\setup"
Type: dirifempty;     Name: "{app}"

; ============================================================================
; [Code] — Pascal helpers
; ============================================================================
[Code]

const
  FLM_REG_PREFIX = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\flm version ';

{ True if no FLM uninstall key is found AND no flm.exe exists in PF\FastFlowLM. }
function NeedsFLM(): Boolean;
var
  Names: TArrayOfString;
  i: Integer;
  Dummy: String;
begin
  Result := True;

  { Scan 32-bit uninstall hive for any 'flm version *' subkey. }
  if RegGetSubkeyNames(HKLM, 'Software\Microsoft\Windows\CurrentVersion\Uninstall', Names) then
  begin
    for i := 0 to GetArrayLength(Names) - 1 do
    begin
      if Pos('flm version ', Names[i]) = 1 then
      begin
        Result := False;
        Exit;
      end;
    end;
  end;

  { Fallback: probe the default install path. }
  if FileExists(ExpandConstant('{commonpf}\FastFlowLM\flm.exe')) then
    Result := False;

  { Avoid 'Dummy unused' warning. }
  Dummy := '';
end;

{ Locate the FLM QuietUninstallString from the 32-bit Uninstall hive.
  Returns a cmd-runnable string, or '' if FLM isn't registered. }
function FlmUninstallCmd(Param: String): String;
var
  Names: TArrayOfString;
  i: Integer;
  KeyPath, Quiet: String;
begin
  Result := 'echo FLM not registered';
  if not RegGetSubkeyNames(HKLM, 'Software\Microsoft\Windows\CurrentVersion\Uninstall', Names) then
    Exit;
  for i := 0 to GetArrayLength(Names) - 1 do
  begin
    if Pos('flm version ', Names[i]) = 1 then
    begin
      KeyPath := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\' + Names[i];
      if RegQueryStringValue(HKLM, KeyPath, 'QuietUninstallString', Quiet) then
      begin
        Result := Quiet;
        Exit;
      end;
    end;
  end;
end;

{ After the FLM /VERYSILENT step finishes, drop a marker file so the
  uninstaller knows we're responsible for chaining its removal. }
procedure CurStepChanged(CurStep: TSetupStep);
var
  MarkerPath: String;
begin
  if CurStep = ssPostInstall then
  begin
    if NeedsFLM() then
    begin
      { This branch shouldn't fire — FLM should now BE installed because
        the [Run] step ran. But if NeedsFLM still returns true here, FLM
        install failed silently. Surface that. }
      Log('WARNING: FLM still missing after install step.');
    end
    else
    begin
      MarkerPath := ExpandConstant('{app}\.flm_installed_by_us');
      { Only write the marker if FLM wasn't there before we ran (NeedsFLM
        was true pre-install — that case is captured by [Files] running
        the FLM installer conditionally). If a marker already exists from
        a prior install, leave it. }
      if not FileExists(MarkerPath) then
        SaveStringToFile(MarkerPath, 'Flowkey installed FLM', False);
    end;
  end;
end;

{ Resolve %LOCALAPPDATA%\FastFlowPrompt for the user running the uninstaller. }
function UserDataDir(): String;
begin
  Result := ExpandConstant('{localappdata}') + '\FastFlowPrompt';
end;

{ During uninstall: ask whether to wipe per-user config/data/logs, then act.
  Runs AFTER files in the install dir are removed but BEFORE the uninstaller
  exits, so the prompt isn't competing with file-in-use errors.
  NB: never put an Inno brace-constant in a Pascal comment — these comments
  do not nest, so the constant's closing brace would end the comment early. }
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Wipe: Integer;
  Target: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    Target := UserDataDir();
    if not DirExists(Target) then
      Exit;
    Wipe := MsgBox(
      'Remove your Flowkey config, notes, and logs?' + #13#10 + #13#10 +
      Target + #13#10 + #13#10 +
      'Click Yes to wipe everything. Click No to keep your data — ' +
      'a future install will pick up where you left off.',
      mbConfirmation,
      MB_YESNO or MB_DEFBUTTON2
    );
    if Wipe = IDYES then
    begin
      if DelTree(Target, True, True, True) then
        Log('User data removed: ' + Target)
      else
        Log('Failed to remove some files under: ' + Target);
    end
    else
    begin
      Log('User data kept: ' + Target);
    end;
  end;
end;
