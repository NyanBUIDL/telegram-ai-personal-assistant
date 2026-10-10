; Unsigned private owner preview. Compile production artifacts only through build-installer.ps1.
#ifndef SourceDir
  #error SourceDir required
#endif
#ifndef SourceManifest
  #error SourceManifest required
#endif
#ifndef AppVersion
  #error AppVersion required
#endif
#ifndef PayloadId
  #error PayloadId required
#endif
#define HelperHash GetSHA256OfFile(SourceDir + "\TelegramAIPersonalAssistant.exe")
#ifdef SyntheticRoot
  #ifndef SyntheticId
    #error SyntheticId required
  #endif
  #define ProductId "TelegramAIPersonalAssistant.P02.Synthetic." + SyntheticId
  #define ProductName "Telegram Assistant P02 - BẢN KIỂM THỬ GIẢ LẬP, CHƯA ĐƯỢC DUYỆT"
  #define InstallRoot SyntheticRoot + "\app"
  #define DataRoot SyntheticRoot + "\data"
  #define MenuRoot SyntheticRoot + "\shortcuts"
  #define StartupRoot SyntheticRoot + "\startup"
#else
  #define ProductId "TelegramAIPersonalAssistant.OwnerPreview"
  #define ProductName "Telegram AI Personal Assistant (Bản trải nghiệm riêng, chưa ký số)"
  #define InstallRoot "{localappdata}\Programs\TelegramAIPersonalAssistant"
  #define DataRoot "{localappdata}\TelegramAIPersonalAssistant"
  #define MenuRoot "{userprograms}\Telegram AI Personal Assistant"
  #define StartupRoot "{userstartup}"
#endif

[Setup]
AppId={#ProductId}
AppName={#ProductName}
AppVersion={#AppVersion}
DefaultDirName={#InstallRoot}
DisableDirPage=yes
DisableProgramGroupPage=yes
UsePreviousAppDir=no
PrivilegesRequired=lowest
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0.22000
UninstallDisplayName={#ProductName}
UninstallDisplayIcon={app}\versions\{#PayloadId}\TelegramAIPersonalAssistant.exe
CloseApplications=no
RestartApplications=no
AllowCancelDuringInstall=yes
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
OutputBaseFilename=private-unsigned

[Languages]
Name: "vietnamese"; MessagesFile: "compiler:Default.isl,windows-installer-vi.isl"

[Tasks]
Name: autostart; Description: "Mở ứng dụng khi tôi đăng nhập vào Windows"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}\versions\{#PayloadId}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourceManifest}"; DestDir: "{app}\versions\{#PayloadId}"; DestName: "p01-manifest.json"; Flags: ignoreversion; AfterInstall: ValidateInstalled

[Icons]
Name: "{#MenuRoot}\Telegram AI Personal Assistant"; Filename: "{app}\versions\{#PayloadId}\TelegramAIPersonalAssistant.exe"; WorkingDir: "{app}\versions\{#PayloadId}"
Name: "{#StartupRoot}\Telegram AI Personal Assistant"; Filename: "{app}\versions\{#PayloadId}\TelegramAIPersonalAssistant.exe"; WorkingDir: "{app}\versions\{#PayloadId}"; Tasks: autostart

[Code]
const INVALID_HANDLE_VALUE = -1;
var
  RuntimeHandle: THandle;
  Helper: String;

function CreateFileW(Name: String; Access, Share: Cardinal; Security: LongWord;
  Creation, Flags: Cardinal; Template: THandle): THandle;
  external 'CreateFileW@kernel32.dll stdcall';
function LockFile(Handle: THandle; Low, High, CountLow, CountHigh: Cardinal): Boolean;
  external 'LockFile@kernel32.dll stdcall';
function CloseHandle(Handle: THandle): Boolean;
  external 'CloseHandle@kernel32.dll stdcall';
function GetFileAttributesW(Name: String): Cardinal;
  external 'GetFileAttributesW@kernel32.dll stdcall';
function LastError: Cardinal;
  external 'GetLastError@kernel32.dll stdcall';

function SafeAncestors(Path: String): Boolean;
var Attributes: Cardinal; Parent: String;
begin
  Result := False;
  while Path <> '' do begin
    Attributes := GetFileAttributesW(Path);
    if Attributes = $FFFFFFFF then begin
      if (LastError <> 2) and (LastError <> 3) then exit;
    end else if (Attributes and $400) <> 0 then exit;
    Parent := ExtractFileDir(Path);
    if Parent = Path then break;
    Path := Parent;
  end;
  Result := True;
end;

function SafeTree(Path: String): Boolean;
var Found: TFindRec;
begin
  Result := False;
  if not SafeAncestors(Path) then exit;
  if DirExists(Path) then begin
    if FindFirst(Path + '\*', Found) then begin
      try
        repeat
          if (Found.Name <> '.') and (Found.Name <> '..') then begin
            if (Found.Attributes and $400) <> 0 then exit;
            if (Found.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
              if not SafeTree(Path + '\' + Found.Name) then exit;
          end;
        until not FindNext(Found);
        if (LastError <> 18) and (LastError <> 0) then exit;
      finally
        FindClose(Found);
      end;
    end else begin
      if (LastError <> 2) and (LastError <> 18) then exit;
    end;
  end;
  Result := True;
end;

function FixedPathsSafe: Boolean;
begin
  Result := (CompareText(RemoveBackslashUnlessRoot(ExpandConstant('{app}')),
    RemoveBackslashUnlessRoot(ExpandConstant('{#InstallRoot}'))) = 0)
    and SafeTree(ExpandConstant('{app}')) and SafeTree(ExpandConstant('{#DataRoot}'))
    and SafeTree(ExpandConstant('{#MenuRoot}')) and SafeAncestors(ExpandConstant('{#StartupRoot}'));
end;

function CheckHelper(Mode: String): String;
var Code: Integer;
begin
  Result := '';
  if not FileExists(Helper) then begin Result := 'Thiếu thành phần kiểm tra cài đặt. Hãy cài lại đúng bộ cài đã được duyệt để khôi phục.'; exit; end;
  if CompareText(GetSHA256OfFile(Helper), '{#HelperHash}') <> 0 then begin Result := 'Thành phần kiểm tra cài đặt đã bị thay đổi. Hãy cài lại đúng bộ cài đã được duyệt để khôi phục.'; exit; end;
  if not Exec(Helper, Mode, ExtractFileDir(Helper), SW_HIDE, ewWaitUntilTerminated, Code) then
    Result := 'Không thể kiểm tra bản cài đặt một cách an toàn. Hãy cài lại đúng bộ cài đã được duyệt để khôi phục.'
  else if Code = 22 then
    Result := 'Trợ lý đang chạy. Hãy chọn Thoát từ biểu tượng ở khay hệ thống, đợi các tác vụ dừng rồi thử lại.'
  else if Code = 21 then
    Result := 'Bản trải nghiệm chỉ hỗ trợ hồ sơ SQLite mặc định. Hồ sơ MySQL, hồ sơ tùy chỉnh hoặc không hợp lệ được giữ nguyên và không thể nâng cấp tại đây. Hãy sao lưu riêng hồ sơ cũ; không đổi loại cơ sở dữ liệu hoặc xóa hồ sơ.'
  else if Code <> 0 then
    Result := 'Không xác minh được quyền sở hữu an toàn của người dùng hiện tại. Đường dẫn ứng dụng hoặc hồ sơ dạng liên kết hay không truy cập được bị từ chối; dữ liệu hồ sơ chưa bị thay đổi.';
end;

procedure ValidateInstalled;
var Reason: String;
begin
  Helper := ExpandConstant('{app}\versions\{#PayloadId}\TelegramAIPersonalAssistant.exe');
  Reason := CheckHelper('--installer-check');
  if Reason <> '' then RaiseException(Reason);
end;

procedure ReleaseRuntime;
begin
  if (RuntimeHandle <> 0) and (RuntimeHandle <> INVALID_HANDLE_VALUE) then CloseHandle(RuntimeHandle);
  RuntimeHandle := 0;
end;

function FenceRuntime: String;
begin
  Result := CheckHelper('--installer-prepare');
  if Result <> '' then exit;
  if not FixedPathsSafe then begin Result := 'Đường dẫn cài đặt không an toàn; các tệp ứng dụng chưa bị thay đổi.'; exit; end;
  RuntimeHandle := CreateFileW(ExpandConstant('{#DataRoot}\.desktop-control\runtime.lock'),
    $C0000000, 3, 0, 3, $00200000, 0);
  if RuntimeHandle = INVALID_HANDLE_VALUE then begin
    Result := 'Không mở được khóa kiểm soát ứng dụng. Hãy thoát trợ lý rồi thử lại.'; exit;
  end;
  if not LockFile(RuntimeHandle, 0, 0, 1, 0) then begin
    ReleaseRuntime;
    Result := 'Trợ lý đang chạy. Hãy chọn Thoát từ biểu tượng ở khay hệ thống, đợi các tác vụ dừng rồi thử lại.'; exit;
  end;
  Result := CheckHelper('--installer-check');
  if Result <> '' then ReleaseRuntime;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  ReleaseRuntime;
  Result := '';
  if not FixedPathsSafe then begin
    Result := 'Chỉ hỗ trợ thư mục cài đặt mặc định của người dùng hiện tại. Đường dẫn ứng dụng hoặc hồ sơ dạng liên kết bị từ chối.'; exit;
  end;
  ExtractTemporaryFiles('{app}\versions\{#PayloadId}\*');
  Helper := ExpandConstant('{tmp}\') + '{app}\versions\{#PayloadId}\TelegramAIPersonalAssistant.exe';
  Result := FenceRuntime;
end;

function InitializeUninstall: Boolean;
var Reason: String;
begin
  Result := False;
  if not FixedPathsSafe then begin MsgBox('Đường dẫn cài đặt không an toàn. Đã từ chối gỡ cài đặt.', mbError, MB_OK); exit; end;
  Helper := ExpandConstant('{app}\versions\{#PayloadId}\TelegramAIPersonalAssistant.exe');
  Reason := FenceRuntime;
  if Reason <> '' then begin MsgBox(Reason, mbError, MB_OK); exit; end;
  Result := True;
end;

procedure DeinitializeSetup;
begin
  ReleaseRuntime;
end;

procedure CurStepChanged(Step: TSetupStep);
begin
  if (Step = ssPostInstall) and not WizardIsTaskSelected('autostart') then
    DeleteFile(ExpandConstant('{#StartupRoot}\Telegram AI Personal Assistant.lnk'));
end;

procedure DeinitializeUninstall;
begin
  ReleaseRuntime;
end;
