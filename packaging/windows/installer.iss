; Inno Setup script for the Windows installer. Built by CI:
;   iscc /DMyAppVersion=1.1.0 packaging\windows\installer.iss
; Installs per user (no admin rights needed). Running the installer of a newer
; version upgrades in place and keeps zt-remote-config.json (password, server).

#define MyAppName "ZT Remote Desktop"
#define MyAppExe "ZT-Remote-Desktop.exe"
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif

[Setup]
AppId={{5F0C6C1E-7B2D-4C8E-9E7A-3D1B2A6F4C90}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher=ZT Remote Desktop
AppPublisherURL=https://github.com/utkarshpatel-up/zt-remote-desktop
AppUpdatesURL=https://github.com/utkarshpatel-up/zt-remote-desktop/releases/latest
DefaultDirName={localappdata}\Programs\ZT Remote Desktop
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
OutputDir=..\..\out
OutputBaseFilename=ZT-Remote-Desktop-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
UninstallDisplayIcon={app}\{#MyAppExe}

[Tasks]
Name: "startup"; Description: "Start ZT Remote Desktop when I sign in to Windows"; Flags: unchecked
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "..\..\dist\{#MyAppExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: startup

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "Start {#MyAppName} now"; Flags: nowait postinstall skipifsilent
