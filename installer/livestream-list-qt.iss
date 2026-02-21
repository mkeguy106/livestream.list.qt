; Inno Setup script for Livestream List Qt
; Packages the PyInstaller --onedir output into a Windows installer.
;
; Expected layout before running iscc:
;   dist\LivestreamListQt\   - PyInstaller output folder
;   data\icon.ico            - Application icon
;
; The version is injected via /D on the iscc command line:
;   iscc /DAppVersion=1.0.25 installer\livestream-list-qt.iss

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppName=Livestream List Qt
AppVersion={#AppVersion}
AppPublisher=mkeguy106
AppPublisherURL=https://github.com/mkeguy106/livestream.list.qt
DefaultDirName={autopf}\LivestreamListQt
DefaultGroupName=Livestream List Qt
OutputDir=..\dist
OutputBaseFilename=LivestreamListQt-v{#AppVersion}-Setup
SetupIconFile=..\data\icon.ico
UninstallDisplayIcon={app}\LivestreamListQt.exe
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
MinVersion=10.0

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"
Name: "addtopath"; Description: "Add to &PATH (enables CLI command)"; GroupDescription: "System integration:"; Flags: unchecked

[Files]
; PyInstaller output directory
Source: "..\dist\LivestreamListQt\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Livestream List Qt"; Filename: "{app}\LivestreamListQt.exe"; IconFilename: "{app}\LivestreamListQt.exe"
Name: "{group}\Uninstall Livestream List Qt"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Livestream List Qt"; Filename: "{app}\LivestreamListQt.exe"; Tasks: desktopicon

[Registry]
; Add to user PATH if selected
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; ValueData: "{olddata};{app}"; Tasks: addtopath; Check: NeedsAddPath(ExpandConstant('{app}'))

[Run]
Filename: "{app}\LivestreamListQt.exe"; Description: "Launch Livestream List Qt"; Flags: nowait postinstall skipifsilent

[Code]
function NeedsAddPath(Param: string): Boolean;
var
  OrigPath: string;
begin
  if not RegQueryStringValue(HKEY_CURRENT_USER,
    'Environment', 'Path', OrigPath) then
  begin
    Result := True;
    exit;
  end;
  Result := Pos(';' + Uppercase(Param) + ';',
    ';' + Uppercase(OrigPath) + ';') = 0;
end;
