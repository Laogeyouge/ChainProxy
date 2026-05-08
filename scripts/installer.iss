; Inno Setup script for ChainProxy (Windows).
;
; Build:
;   iscc scripts\installer.iss
;   → dist\ChainProxy-Setup-<version>.exe
;
; The version is read from the AppVersion define below; bump it at release time.

#define MyAppName        "ChainProxy"
#define MyAppVersion     "1.1.7"
#define MyAppPublisher   "Laogeyouge"
#define MyAppURL         "https://github.com/Laogeyouge/ChainProxy"
#define MyAppExeName     "ChainProxy.exe"

[Setup]
AppId={{B8C9D4E2-7F1A-4C3D-9E8B-1A2B3C4D5E6F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
; Allow "anywhere" install — the user picks. autopf = Program Files for admin
; install or %LOCALAPPDATA%\Programs for per-user.
DisableDirPage=no
DisableProgramGroupPage=yes
; Per-user vs system-wide: lowest means installer doesn't insist on admin.
; If installing to Program Files, Windows prompts for elevation as needed.
PrivilegesRequiredOverridesAllowed=dialog
PrivilegesRequired=lowest
OutputBaseFilename=ChainProxy-Setup-{#MyAppVersion}
OutputDir=..\dist
SetupIconFile=..\icon.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\LICENSE
UninstallDisplayIcon={app}\{#MyAppExeName}

; Language: Chinese (Simplified) primary; fall back to English for unsupported locales
[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english";   MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "quicklaunchicon"; Description: "{cm:CreateQuickLaunchIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Whole PyInstaller --onedir output. The {#MyAppExeName} sits at the root,
; everything else (Qt DLLs, _internal, mihomo.exe) goes alongside.
Source: "..\dist\ChainProxy\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\ChainProxy\*"; Excludes: "{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

; Don't wipe the user's runtime config on uninstall — they may want their
; node list / rules to survive across versions. The installer only owns the
; {app} directory; %APPDATA%\ChainProxy\ is user state and stays untouched.
[UninstallDelete]
Type: filesandordirs; Name: "{app}\_internal"
