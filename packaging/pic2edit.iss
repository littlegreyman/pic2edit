; pic2edit 安装包脚本（Inno Setup）
; 用法（需安装 Inno Setup 6，命令行 ISCC 或 GUI 打开本文件编译）：
;   ISCC.exe packaging\pic2edit.iss
; 产物：installer_out\install.exe
;
; 设计要点
; - 允许安装到任意盘（含非系统盘）：DisableDirPage=no，默认 {localappdata}\pic2edit
; - 模型（~2GB GGUF）与 llama.cpp 引擎不随安装包；首次运行由程序自动下载到
;   安装目录（跟随所选盘符，见 ocr_app/utils/app_home.data_root）。
; - 含 MIT LICENSE 页面（合规：PySide6 为 LGPL，需随附第三方声明）。
; - 开始菜单 + 桌面快捷方式；自动卸载；自定义图标。

#define MyAppName "pic2edit"
#define MyAppVersion "0.18.0"
#define MyAppPublisher "pic2edit"
#define MyAppURL "https://github.com/littlegreyman/pic2edit"
#define MyAppExeName "pic2edit.exe"

[Setup]
; 基本信息
AppId={{71F3A9C2-3B6E-4D2A-9C1B-2E8F5A7D0C33}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
VersionInfoVersion={#MyAppVersion}
VersionInfoDescription=图片转可编辑文档（DeepSeek-OCR 本地离线）
VersionInfoCopyright=Copyright (C) pic2edit (MIT)

; 安装位置：默认用户级（可写、免管理员），用户可在目录页改到任意盘
DefaultDirName={localappdata}\{#MyAppName}
; 允许选择目录（含非系统盘）；该目录不存在时允许创建
DisableDirPage=no
AllowNoIcons=no
; 用户级安装：不写 HKLM，普通用户即可，无需管理员
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

; 输出
OutputDir=..\installer_out
OutputBaseFilename=install
SetupIconFile=pic2edit-logo.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}

; 压缩 / 语言
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
LanguageDetectionMethod=uilanguage
Languages=chinese_simplified

; 单实例 + 重启后清理
CloseApplications=yes
RestartApplications=no

; LGPL/第三方合规：随附 LICENSE 与第三方声明页面
LicenseFile=..\LICENSE
InfoBeforeFile=

[Languages]
Name: "chinese_simplified"; MessagesFile: "compiler:ChineseSimplified.isl"

[Files]
; 整个冻结目录（dist/pic2edit/*）安装到 {app}
; 注意：dist/pic2edit 由 PyInstaller 产出（先运行 python packaging/build_exe.py）
Source: "..\dist\pic2edit\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; 第三方许可声明（PySide6/LGPL 要求随附，且说明可替换/源码可得）
; 若项目根有 THIRD_PARTY_NOTICES 则一并打包；没有则跳过（Flags: skipifsourceless）
Source: "..\THIRD_PARTY_NOTICES"; DestDir: "{app}"; Flags: ignoreversion skipifsourceless

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; \
  WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; \
  WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "额外任务"; Flags: unchecked

[Run]
; 完成页可选“启动 pic2edit”
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; \
  Flags: nowait postinstall skipifsilent
