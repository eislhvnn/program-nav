; 程序导航 安装程序（Inno Setup 6）
; 编译：运行 build_installer.bat，或用 ISCC.exe 编译本文件
; 装到当前用户的 AppData\Local\Programs 下：不需要管理员权限，程序也能在自己旁边保存数据

#define AppName "程序导航"
#define AppVersion "1.0.4"
#define AppExe "程序导航.exe"

[Setup]
AppId={{6F1C2B7E-4D3A-4B8E-9C61-2E7A5D0F9B13}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppName}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=build\installer
OutputBaseFilename={#AppName}-安装程序-{#AppVersion}
SetupIconFile=app.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "chs"; MessagesFile: "installer\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "在桌面创建快捷方式"; GroupDescription: "快捷方式："
Name: "autostart"; Description: "开机自动启动（只在右下角托盘里，不弹窗口）"; GroupDescription: "启动："
; 电脑上已经有 Everything 就不显示这一项
Name: "everything"; Description: "同时安装 Everything（全盘搜索需要；voidtools 出品的免费软件，安装时会弹一次管理员确认）"; GroupDescription: "附带软件："; Check: not EverythingInstalled

[Files]
; 只装程序本身，不带任何个人数据（程序列表、设置等第一次运行时自动创建）
Source: "build\release\{#AppName}\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "build\release\{#AppName}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
; Everything 官方原版安装包（MIT 许可证，分发时要附上许可证文本）。安装时没勾也照样带上，
; 以后在程序里「一键安装」直接用它，不需要联网
Source: "redist\Everything-Setup.exe"; DestDir: "{app}\redist"; Flags: ignoreversion
Source: "licenses\*"; DestDir: "{app}\licenses"; Flags: ignoreversion

[Icons]
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExe}"; Parameters: "--background"; WorkingDir: "{app}"; Tasks: autostart
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"

[Registry]
; 程序托盘菜单里勾「开机自动启动」会写这一项；卸载时一起删掉（安装时不创建）
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "ProgramNav"; ValueType: none; Flags: dontcreatekey uninsdeletevalue

[Run]
Filename: "{app}\{#AppExe}"; Description: "立即运行 {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 只删缓存；程序列表、分类、设置、自定义图标这些个人数据保留
Type: filesandordirs; Name: "{app}\.webview"
Type: files; Name: "{app}\app.log"
Type: files; Name: "{app}\app.ico"

[Code]
// 安装/卸载前先关掉正在运行的程序，否则文件被占用替换不了
procedure CloseRunningApp();
var
  Code: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/IM "{#AppExe}" /T /F', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Sleep(800);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  CloseRunningApp();
  Result := '';
end;

function EverythingPath(): String;
begin
  Result := ExpandConstant('{commonpf64}\Everything\Everything.exe');
  if not FileExists(Result) then Result := ExpandConstant('{commonpf32}\Everything\Everything.exe');
  if not FileExists(Result) then Result := ExpandConstant('{localappdata}\Programs\Everything\Everything.exe');
  if not FileExists(Result) then Result := '';
end;

function EverythingInstalled(): Boolean;
begin
  Result := EverythingPath() <> '';
end;

// 用自带的安装包静默安装 Everything（它要管理员权限，所以用 runas 弹一次确认），装好后启动
// 点了「否」不算错误，只提示以后可以在程序里装
procedure InstallEverything();
var
  Code: Integer;
begin
  WizardForm.StatusLabel.Caption := '正在安装 Everything，请在弹出的窗口中点「是」…';
  if ShellExec('runas', ExpandConstant('{app}\redist\Everything-Setup.exe'), '/S', '',
               SW_SHOW, ewWaitUntilTerminated, Code) and EverythingInstalled() then
    ShellExec('', EverythingPath(), '-startup', '', SW_HIDE, ewNoWait, Code)
  else
    SuppressibleMsgBox('Everything 没有装上（可能是在确认窗口里点了「否」）。' + #13#10 +
      '不影响程序导航的使用，以后可以在「＋ 添加程序」的全盘搜索里一键安装，不需要联网。',
      mbInformation, MB_OK, IDOK);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and WizardIsTaskSelected('everything') then
    InstallEverything();
end;

function InitializeUninstall(): Boolean;
begin
  CloseRunningApp();
  Result := True;
end;
