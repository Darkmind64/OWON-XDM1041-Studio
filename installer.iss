#define AppName "OWON XDM1041 Studio"
#define AppVer "3.0"
[Setup]
AppId={{6B2E6F3A-1D41-4E5B-9C3A-5D1041A7C0DE}
AppName={#AppName}
AppVersion={#AppVer}
AppPublisher=Davy
DefaultDirName={autopf}\XDM1041 Studio
DefaultGroupName={#AppName}
OutputDir=installer
OutputBaseFilename=XDM1041_Studio_Setup_{#AppVer}
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\XDM1041_Studio.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=force
[Languages]
Name: "fr"; MessagesFile: "compiler:Languages\French.isl"
[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
[Files]
Source: "dist\XDM1041_Studio\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\XDM1041_Studio.exe"
Name: "{group}\Désinstaller {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\XDM1041_Studio.exe"; Tasks: desktopicon
[Run]
Filename: "{app}\XDM1041_Studio.exe"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
Filename: "{app}\XDM1041_Studio.exe"; Flags: nowait; Check: WizardSilent
