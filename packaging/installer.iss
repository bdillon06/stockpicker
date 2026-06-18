; Inno Setup script for Swing Finder — produces SwingFinder-Setup-<version>.exe
; Compiled by packaging\build_windows.ps1 (or: iscc packaging\installer.iss).
; The single-file SwingFinder.exe must already exist in dist\ (PyInstaller output).

#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif

#define MyAppName "Swing Finder"
#define MyAppExe "SwingFinder.exe"
#define MyAppPublisher "Swing Finder"

[Setup]
AppId={{B7F3B1C2-9B4E-4C2A-9F4D-SWINGFINDER01}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\SwingFinder
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; Per-user install needs no admin rights — easiest to share.
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename=SwingFinder-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"

[Files]
Source: "..\dist\{#MyAppExe}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "Launch Swing Finder"; Flags: nowait postinstall skipifsilent
