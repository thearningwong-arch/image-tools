; Compile with Inno Setup 6.5 or later. Binaries come from the Windows bundle.
#if Ver < EncodeVer(6, 5, 0)
  #error Inno Setup 6.5 or later is required (compatible Simplified Chinese messages).
#endif
#ifndef AppVersion
  #error Pass /DAppVersion=<version>
#endif
#ifndef BuildRoot
  #define BuildRoot "..\dist\ImageTools"
#endif
#ifndef OutputRoot
  #define OutputRoot "..\dist"
#endif

[Setup]
; Keep this identity stable so new versions replace the existing installation.
AppId={{6BC68160-8131-4515-A3A3-B92332CDCF56}
AppName=图迹
AppVersion={#AppVersion}
AppVerName=图迹 {#AppVersion}
AppPublisher=Image Tools
AppSupportURL=https://github.com/thearningwong-arch/image-tools
VersionInfoVersion={#AppVersion}
VersionInfoDescription=图迹安装程序
DefaultDirName={localappdata}\Programs\ImageTools
DefaultGroupName=图迹
DisableProgramGroupPage=yes
DisableWelcomePage=no
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputRoot}
OutputBaseFilename=ImageTools-{#AppVersion}-win-x64-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
LanguageDetectionMethod=none
ShowLanguageDialog=no
UninstallDisplayName=图迹
UninstallDisplayIcon={app}\ImageTools.exe
CloseApplications=yes
CloseApplicationsFilter=ImageTools.exe
RestartApplications=no
SetupLogging=yes

[Languages]
Name: "chinesesimp"; MessagesFile: "languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："

[Files]
Source: "{#BuildRoot}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autodesktop}\图迹"; Filename: "{app}\ImageTools.exe"; WorkingDir: "{app}"; Tasks: desktopicon
Name: "{group}\图迹"; Filename: "{app}\ImageTools.exe"; WorkingDir: "{app}"
Name: "{group}\卸载图迹"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\ImageTools.exe"; Description: "启动图迹"; Flags: nowait postinstall skipifsilent

[Messages]
WelcomeLabel2=此向导将安装图迹 [name/ver]。%n%n无需安装 Python 或下载运行依赖。%n%n升级和卸载会保留本机索引及缩略图，不会修改素材原文件。%n%n建议先关闭正在运行的图迹，然后继续。
FinishedLabelNoIcons=图迹已安装完成。%n%n索引默认保存在当前用户的 AppData\Local\ImageTools 文件夹，升级和卸载均会保留。
ConfirmUninstall=确实要卸载 %1 及其程序组件吗？%n%n现有索引、缩略图和素材原文件将保留。

[Code]
function IsSameOrInside(const Candidate, Parent: String): Boolean;
var
  CandidatePath, ParentPath: String;
begin
  CandidatePath := AddBackslash(ExpandFileName(Candidate));
  ParentPath := AddBackslash(ExpandFileName(Parent));
  Result := CompareText(Copy(CandidatePath, 1, Length(ParentPath)), ParentPath) = 0;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  InstallPath, IndexPath: String;
begin
  Result := '';
  InstallPath := ExpandConstant('{app}');
  IndexPath := ExpandConstant('{localappdata}\ImageTools');
  { Avoid owning index files even when /DIR is supplied in silent mode. }
  if IsSameOrInside(InstallPath, IndexPath) or IsSameOrInside(IndexPath, InstallPath) then
    Result := '程序安装目录不能与索引目录重叠。请使用默认安装目录。';
end;

{ Intentionally no [UninstallDelete] or custom data cleanup. The application
  writes its index outside the program folder; Inno removes only installed files. }
