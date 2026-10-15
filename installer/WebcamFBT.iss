; WebcamFBT installer (Inno Setup 6). Built by installer\package.ps1, which passes:
;   AppVersion  from the VERSION file
;   SourceDir   build\dist (the folder laid out like the installation)
;   OutputDir   build\installer
;
; Per-user install into %LOCALAPPDATA%\Programs\WebcamFBT: no administrator rights, and the app
; can write its settings, calibration and logs next to itself.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\build\dist"
#endif
#ifndef OutputDir
  #define OutputDir "..\build\installer"
#endif

[Setup]
AppId={{47034B22-F4D6-4207-BDD4-1C1C9FE7F447}
AppName=WebcamFBT
AppVersion={#AppVersion}
AppVerName=WebcamFBT {#AppVersion}
AppPublisher=Onurada
DefaultDirName={localappdata}\Programs\WebcamFBT
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
OutputDir={#OutputDir}
OutputBaseFilename=WebcamFBT-Setup-{#AppVersion}
SetupIconFile=..\ui\Assets\app.ico
UninstallDisplayIcon={app}\WebcamFBT.exe
UninstallDisplayName=WebcamFBT {#AppVersion}
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; The app holds this mutex while running: setup and uninstall ask to close it first.
AppMutex=WebcamFBT.App
CloseApplications=yes
WizardStyle=modern
ShowLanguageDialog=auto
MinVersion=10.0

[Languages]
Name: "tr"; MessagesFile: "compiler:Languages\Turkish.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
tr.RegisterDriver=WebcamFBT sürücüsünü SteamVR'a kaydet (SteamVR kapalı olmalı)
en.RegisterDriver=Register the WebcamFBT driver with SteamVR (SteamVR must be closed)
tr.RegisteringDriver=SteamVR sürücüsü kaydediliyor...
en.RegisteringDriver=Registering the SteamVR driver...

[Tasks]
Name: "registerdriver"; Description: "{cm:RegisterDriver}"
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; User data (settings, calibration, logs) is never part of the package, so updates keep it.
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion; \
    Excludes: "\logs\*,\tracker\config.json,\tracker\calibration.json"

[Icons]
Name: "{autoprograms}\WebcamFBT"; Filename: "{app}\WebcamFBT.exe"
Name: "{autodesktop}\WebcamFBT"; Filename: "{app}\WebcamFBT.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\WebcamFBT.exe"; Parameters: "--install-driver"; Tasks: registerdriver; \
    StatusMsg: "{cm:RegisteringDriver}"; Flags: waituntilterminated
Filename: "{app}\WebcamFBT.exe"; Description: "{cm:LaunchProgram,WebcamFBT}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Removes the driver from SteamVR and the tracker roles (kept if another WebcamFBT driver is still registered).
Filename: "{app}\WebcamFBT.exe"; Parameters: "--uninstall-driver"; RunOnceId: "RemoveDriver"; Flags: waituntilterminated

[UninstallDelete]
; Everything the app created at runtime: settings, calibration, logs, Python caches.
Type: filesandordirs; Name: "{app}"
