; Project-maintained Vietnamese translation for the private owner-preview installer.
; Modified message resource, not an official Inno Setup translation.
; Keys/placeholders: Inno Setup 6.7.3 Default.isl, SHA-256
; 42a5f6f7dbbddf26cc278f67db5d894235ce1d126a6856702e31bd02023a1316.
; https://github.com/jrsoftware/issrc/releases/tag/is-6_7_3
;
; Inno Setup License
; ==================
;
; Except where otherwise noted, all of the documentation and software included in the Inno
; Setup package is copyrighted by Jordan Russell.
;
; Copyright (C) 1997-2026 Jordan Russell. All rights reserved.
; Portions Copyright (C) 2000-2026 Martijn Laan. All rights reserved.
;
; This software is provided "as-is," without any express or implied warranty. In no event shall
; the author be held liable for any damages arising from the use of this software.
;
; Permission is granted to anyone to use this software for any purpose, including commercial
; applications, and to alter and redistribute it, provided that the following conditions are met:
;
; 1. All redistributions of source code files must retain all copyright notices that are currently
;    in place, and this list of conditions without modification.
;
; 2. All redistributions in binary form must retain all occurrences of the above copyright notice
;    and web site addresses that are currently in place (for example, in the About boxes).
;
; 3. The origin of this software must not be misrepresented; you must not claim that you wrote
;    the original software. If you use this software to distribute a product, an acknowledgment
;    in the product documentation would be appreciated but is not required.
;
; 4. Modified versions in source or binary form must be plainly marked as such, and must not
;    be misrepresented as being the original software.
;
; Jordan Russell
; jr-2020 AT jrsoftware.org
; https://jrsoftware.org/

[LangOptions]
LanguageName=Tiếng Việt
LanguageID=$042A
LanguageCodePage=1258

[Messages]
SetupAppTitle=Cài đặt
SetupWindowTitle=Cài đặt - %1
UninstallAppTitle=Gỡ cài đặt
UninstallAppFullTitle=Gỡ cài đặt %1
InformationTitle=Thông tin
ConfirmTitle=Xác nhận
ErrorTitle=Lỗi
SetupLdrStartupMessage=Thao tác này sẽ cài đặt %1. Bạn có muốn tiếp tục không?
LdrCannotCreateTemp=Không tạo được tệp tạm. Đã dừng cài đặt
LdrCannotExecTemp=Không chạy được tệp trong thư mục tạm. Đã dừng cài đặt
HelpTextNote=
LastErrorMessage=%1.%n%nLỗi %2: %3
SetupFileMissing=Thiếu tệp %1 trong thư mục cài đặt. Hãy khắc phục lỗi hoặc lấy lại bộ cài đặt.
SetupFileCorrupt=Các tệp cài đặt bị hỏng. Hãy lấy lại bộ cài đặt.
SetupFileCorruptOrWrongVer=Các tệp cài đặt bị hỏng hoặc không tương thích với phiên bản trình cài đặt này. Hãy khắc phục lỗi hoặc lấy lại bộ cài đặt.
InvalidParameter=Tham số dòng lệnh không hợp lệ:%n%n%1
SetupAlreadyRunning=Trình cài đặt đang chạy.
WindowsVersionNotSupported=Chương trình không hỗ trợ phiên bản Windows đang chạy trên máy này.
WindowsServicePackRequired=Chương trình yêu cầu %1 Service Pack %2 trở lên.
NotOnThisPlatform=Chương trình không chạy trên %1.
OnlyOnThisPlatform=Chương trình phải chạy trên %1.
OnlyOnTheseArchitectures=Chỉ có thể cài chương trình trên Windows dành cho các kiến trúc bộ xử lý sau:%n%n%1
WinVersionTooLowError=Chương trình yêu cầu %1 phiên bản %2 trở lên.
WinVersionTooHighError=Không thể cài chương trình trên %1 phiên bản %2 trở lên.
AdminPrivilegesRequired=Bạn phải đăng nhập bằng tài khoản quản trị viên để cài chương trình này.
PowerUserPrivilegesRequired=Bạn phải đăng nhập bằng tài khoản quản trị viên hoặc thành viên nhóm Power Users để cài chương trình này.
SetupAppRunningError=%1 đang chạy.%n%nHãy đóng tất cả phiên đang chạy, rồi chọn Đồng ý để tiếp tục hoặc Hủy để thoát.
UninstallAppRunningError=%1 đang chạy.%n%nHãy đóng tất cả phiên đang chạy, rồi chọn Đồng ý để tiếp tục hoặc Hủy để thoát.
PrivilegesRequiredOverrideTitle=Chọn phạm vi cài đặt
PrivilegesRequiredOverrideInstruction=Chọn phạm vi cài đặt
PrivilegesRequiredOverrideText1=Có thể cài %1 cho tất cả người dùng (cần quyền quản trị viên) hoặc chỉ cho bạn.
PrivilegesRequiredOverrideText2=Có thể cài %1 chỉ cho bạn hoặc cho tất cả người dùng (cần quyền quản trị viên).
PrivilegesRequiredOverrideAllUsers=Cài cho &tất cả người dùng
PrivilegesRequiredOverrideAllUsersRecommended=Cài cho &tất cả người dùng (khuyên dùng)
PrivilegesRequiredOverrideCurrentUser=Cài &chỉ cho tôi
PrivilegesRequiredOverrideCurrentUserRecommended=Cài &chỉ cho tôi (khuyên dùng)
ErrorCreatingDir=Không tạo được thư mục "%1"
ErrorTooManyFilesInDir=Không tạo được tệp trong thư mục "%1" vì thư mục có quá nhiều tệp
ExitSetupTitle=Thoát trình cài đặt
ExitSetupMessage=Cài đặt chưa hoàn tất. Nếu thoát bây giờ, chương trình sẽ chưa được cài đặt.%n%nBạn có thể chạy lại bộ cài đặt sau để hoàn tất.%n%nBạn có muốn thoát không?
AboutSetupMenuItem=&Giới thiệu trình cài đặt...
AboutSetupTitle=Giới thiệu trình cài đặt
AboutSetupMessage=%1 phiên bản %2%n%3%n%nTrang chủ %1:%n%4
AboutSetupNote=
TranslatorNote=
ButtonBack=< &Quay lại
ButtonNext=&Tiếp tục >
ButtonInstall=&Cài đặt
ButtonOK=Đồng ý
ButtonCancel=Hủy
ButtonYes=&Có
ButtonYesToAll=Có cho &tất cả
ButtonNo=&Không
ButtonNoToAll=Khô&ng cho tất cả
ButtonFinish=&Hoàn tất
ButtonBrowse=&Duyệt...
ButtonWizardBrowse=&Duyệt...
ButtonNewFolder=&Tạo thư mục
SelectLanguageTitle=Chọn ngôn ngữ cài đặt
SelectLanguageLabel=Chọn ngôn ngữ sử dụng trong quá trình cài đặt.
ClickNext=Chọn Tiếp tục để tiếp tục hoặc Hủy để thoát trình cài đặt.
BeveledLabel=
BrowseDialogTitle=Chọn thư mục
BrowseDialogLabel=Chọn một thư mục bên dưới rồi chọn Đồng ý.
NewFolderName=Thư mục mới
WelcomeLabel1=Chào mừng đến với trình cài đặt [name]
WelcomeLabel2=Trình cài đặt sẽ cài [name/ver] trên máy của bạn.%n%nBạn nên đóng các ứng dụng khác trước khi tiếp tục.
WizardPassword=Mật khẩu
PasswordLabel1=Bộ cài đặt này được bảo vệ bằng mật khẩu.
PasswordLabel3=Nhập mật khẩu rồi chọn Tiếp tục. Mật khẩu phân biệt chữ hoa và chữ thường.
PasswordEditLabel=&Mật khẩu:
IncorrectPassword=Mật khẩu không đúng. Hãy thử lại.
WizardLicense=Thỏa thuận cấp phép
LicenseLabel=Đọc thông tin quan trọng sau trước khi tiếp tục.
LicenseLabel3=Đọc thỏa thuận cấp phép sau. Bạn phải chấp nhận các điều khoản trước khi tiếp tục cài đặt.
LicenseAccepted=Tôi &chấp nhận thỏa thuận
LicenseNotAccepted=Tôi &không chấp nhận thỏa thuận
WizardInfoBefore=Thông tin
InfoBeforeLabel=Đọc thông tin quan trọng sau trước khi tiếp tục.
InfoBeforeClickLabel=Khi đã sẵn sàng, chọn Tiếp tục.
WizardInfoAfter=Thông tin
InfoAfterLabel=Đọc thông tin quan trọng sau trước khi tiếp tục.
InfoAfterClickLabel=Khi đã sẵn sàng, chọn Tiếp tục.
WizardUserInfo=Thông tin người dùng
UserInfoDesc=Nhập thông tin của bạn.
UserInfoName=&Tên người dùng:
UserInfoOrg=&Tổ chức:
UserInfoSerial=&Số sê-ri:
UserInfoNameRequired=Bạn phải nhập tên.
WizardSelectDir=Chọn thư mục cài đặt
SelectDirDesc=Bạn muốn cài [name] vào đâu?
SelectDirLabel3=[name] sẽ được cài vào thư mục sau.
SelectDirBrowseLabel=Chọn Tiếp tục để tiếp tục. Chọn Duyệt nếu muốn dùng thư mục khác.
DiskSpaceGBLabel=Cần ít nhất [gb] GB dung lượng trống.
DiskSpaceMBLabel=Cần ít nhất [mb] MB dung lượng trống.
CannotInstallToNetworkDrive=Không thể cài vào ổ đĩa mạng.
CannotInstallToUNCPath=Không thể cài vào đường dẫn UNC.
InvalidPath=Bạn phải nhập đường dẫn đầy đủ có ký tự ổ đĩa, ví dụ:%n%nC:\APP%n%nhoặc đường dẫn UNC dạng:%n%n\\server\share
InvalidDrive=Ổ đĩa hoặc thư mục chia sẻ UNC đã chọn không tồn tại hoặc không truy cập được. Hãy chọn nơi khác.
DiskSpaceWarningTitle=Không đủ dung lượng trống
DiskSpaceWarning=Cần ít nhất %1 KB dung lượng trống, nhưng ổ đĩa đã chọn chỉ còn %2 KB.%n%nBạn vẫn muốn tiếp tục không?
DirNameTooLong=Tên thư mục hoặc đường dẫn quá dài.
InvalidDirName=Tên thư mục không hợp lệ.
BadDirName32=Tên thư mục không được chứa các ký tự sau:%n%n%1
DirExistsTitle=Thư mục đã tồn tại
DirExists=Thư mục:%n%n%1%n%nđã tồn tại. Bạn vẫn muốn cài vào thư mục này không?
DirDoesntExistTitle=Thư mục chưa tồn tại
DirDoesntExist=Thư mục:%n%n%1%n%nchưa tồn tại. Bạn có muốn tạo thư mục này không?
WizardSelectComponents=Chọn thành phần
SelectComponentsDesc=Bạn muốn cài những thành phần nào?
SelectComponentsLabel2=Chọn các thành phần cần cài và bỏ chọn những thành phần không cần. Chọn Tiếp tục khi đã sẵn sàng.
FullInstallation=Cài đặt đầy đủ
CompactInstallation=Cài đặt gọn
CustomInstallation=Cài đặt tùy chọn
NoUninstallWarningTitle=Thành phần đã tồn tại
NoUninstallWarning=Các thành phần sau đã được cài trên máy:%n%n%1%n%nBỏ chọn không gỡ các thành phần này.%n%nBạn vẫn muốn tiếp tục không?
ComponentSize1=%1 KB
ComponentSize2=%1 MB
ComponentsDiskSpaceGBLabel=Lựa chọn hiện tại cần ít nhất [gb] GB dung lượng đĩa.
ComponentsDiskSpaceMBLabel=Lựa chọn hiện tại cần ít nhất [mb] MB dung lượng đĩa.
WizardSelectTasks=Chọn tác vụ bổ sung
SelectTasksDesc=Bạn muốn thực hiện thêm tác vụ nào?
SelectTasksLabel2=Chọn các tác vụ muốn thực hiện khi cài [name], rồi chọn Tiếp tục.
WizardSelectProgramGroup=Chọn thư mục menu Bắt đầu
SelectStartMenuFolderDesc=Bạn muốn đặt lối tắt chương trình ở đâu?
SelectStartMenuFolderLabel3=Lối tắt chương trình sẽ được tạo trong thư mục menu Bắt đầu sau.
SelectStartMenuFolderBrowseLabel=Chọn Tiếp tục để tiếp tục. Chọn Duyệt nếu muốn dùng thư mục khác.
MustEnterGroupName=Bạn phải nhập tên thư mục.
GroupNameTooLong=Tên thư mục hoặc đường dẫn quá dài.
InvalidGroupName=Tên thư mục không hợp lệ.
BadGroupName=Tên thư mục không được chứa các ký tự sau:%n%n%1
NoProgramGroupCheck2=&Không tạo thư mục trong menu Bắt đầu
WizardReady=Sẵn sàng cài đặt
ReadyLabel1=Đã sẵn sàng cài [name] trên máy của bạn.
ReadyLabel2a=Chọn Cài đặt để tiếp tục hoặc Quay lại để xem hay đổi lựa chọn.
ReadyLabel2b=Chọn Cài đặt để tiếp tục.
ReadyMemoUserInfo=Thông tin người dùng:
ReadyMemoDir=Thư mục cài đặt:
ReadyMemoType=Kiểu cài đặt:
ReadyMemoComponents=Thành phần đã chọn:
ReadyMemoGroup=Thư mục menu Bắt đầu:
ReadyMemoTasks=Tác vụ bổ sung:
DownloadingLabel2=Đang tải tệp...
ButtonStopDownload=&Dừng tải
StopDownload=Bạn có chắc muốn dừng tải không?
ErrorDownloadAborted=Đã dừng tải
ErrorDownloadFailed=Tải thất bại: %1 %2
ErrorDownloadSizeFailed=Không lấy được kích thước: %1 %2
ErrorProgress=Tiến độ không hợp lệ: %1 trên %2
ErrorFileSize=Kích thước tệp không hợp lệ: dự kiến %1, thực tế %2
ExtractingLabel=Đang giải nén tệp...
ButtonStopExtraction=&Dừng giải nén
StopExtraction=Bạn có chắc muốn dừng giải nén không?
ErrorExtractionAborted=Đã dừng giải nén
ErrorExtractionFailed=Giải nén thất bại: %1
ArchiveIncorrectPassword=Mật khẩu không đúng
ArchiveIsCorrupted=Tệp nén bị hỏng
ArchiveUnsupportedFormat=Định dạng tệp nén không được hỗ trợ
WizardPreparing=Đang chuẩn bị cài đặt
PreparingDesc=Đang chuẩn bị cài [name] trên máy của bạn.
PreviousInstallNotCompleted=Lần cài đặt hoặc gỡ bỏ trước chưa hoàn tất. Bạn cần khởi động lại máy để hoàn tất thao tác đó.%n%nSau khi khởi động lại, chạy lại bộ cài đặt để hoàn tất cài [name].
CannotContinue=Không thể tiếp tục cài đặt. Chọn Hủy để thoát.
ApplicationsFound=Các ứng dụng sau đang dùng tệp cần cập nhật. Bạn nên cho phép trình cài đặt tự đóng các ứng dụng này.
ApplicationsFound2=Các ứng dụng sau đang dùng tệp cần cập nhật. Bạn nên cho phép trình cài đặt tự đóng các ứng dụng này. Sau khi cài xong, trình cài đặt sẽ thử khởi động lại chúng.
CloseApplications=&Tự đóng các ứng dụng
DontCloseApplications=&Không đóng các ứng dụng
ErrorCloseApplications=Không tự đóng được tất cả ứng dụng. Bạn nên đóng những ứng dụng đang dùng tệp cần cập nhật trước khi tiếp tục.
PrepareToInstallNeedsRestart=Cần khởi động lại máy. Sau khi khởi động lại, chạy lại bộ cài đặt để hoàn tất cài [name].%n%nBạn có muốn khởi động lại ngay không?
WizardInstalling=Đang cài đặt
InstallingLabel=Vui lòng đợi trong khi cài [name] trên máy của bạn.
FinishedHeadingLabel=Hoàn tất cài đặt [name]
FinishedLabelNoIcons=Đã cài xong [name] trên máy của bạn.
FinishedLabel=Đã cài xong [name] trên máy của bạn. Bạn có thể mở ứng dụng bằng lối tắt đã tạo.
ClickFinish=Chọn Hoàn tất để thoát trình cài đặt.
FinishedRestartLabel=Cần khởi động lại máy để hoàn tất cài [name]. Bạn có muốn khởi động lại ngay không?
FinishedRestartMessage=Cần khởi động lại máy để hoàn tất cài [name].%n%nBạn có muốn khởi động lại ngay không?
ShowReadmeCheck=Có, tôi muốn xem tệp README
YesRadio=&Có, khởi động lại ngay
NoRadio=&Không, tôi sẽ khởi động lại sau
RunEntryExec=Mở %1
RunEntryShellExec=Xem %1
ChangeDiskTitle=Cần đĩa cài đặt tiếp theo
SelectDiskLabel2=Đưa đĩa %1 vào rồi chọn Đồng ý.%n%nNếu các tệp nằm trong thư mục khác, nhập đường dẫn đúng hoặc chọn Duyệt.
PathLabel=&Đường dẫn:
FileNotInDir2=Không tìm thấy tệp "%1" trong "%2". Hãy đưa đúng đĩa vào hoặc chọn thư mục khác.
SelectDirectoryLabel=Chỉ định vị trí của đĩa tiếp theo.
SetupAborted=Cài đặt chưa hoàn tất.%n%nHãy khắc phục lỗi rồi chạy lại bộ cài đặt.
AbortRetryIgnoreSelectAction=Chọn thao tác
AbortRetryIgnoreRetry=&Thử lại
AbortRetryIgnoreIgnore=&Bỏ qua lỗi và tiếp tục
AbortRetryIgnoreCancel=Hủy cài đặt
RetryCancelSelectAction=Chọn thao tác
RetryCancelRetry=&Thử lại
RetryCancelCancel=Hủy
StatusClosingApplications=Đang đóng ứng dụng...
StatusCreateDirs=Đang tạo thư mục...
StatusExtractFiles=Đang giải nén tệp...
StatusDownloadFiles=Đang tải tệp...
StatusCreateIcons=Đang tạo lối tắt...
StatusCreateIniEntries=Đang tạo mục INI...
StatusCreateRegistryEntries=Đang tạo mục trong sổ đăng ký...
StatusRegisterFiles=Đang đăng ký tệp...
StatusSavingUninstall=Đang lưu thông tin gỡ cài đặt...
StatusRunProgram=Đang hoàn tất cài đặt...
StatusRestartingApplications=Đang khởi động lại ứng dụng...
StatusRollback=Đang hoàn tác thay đổi...
ErrorInternal2=Lỗi nội bộ: %1
ErrorFunctionFailedNoCode=%1 thất bại
ErrorFunctionFailed=%1 thất bại; mã %2
ErrorFunctionFailedWithMessage=%1 thất bại; mã %2.%n%3
ErrorExecutingProgram=Không chạy được tệp:%n%1
ErrorRegOpenKey=Lỗi mở khóa trong sổ đăng ký:%n%1\%2
ErrorRegCreateKey=Lỗi tạo khóa trong sổ đăng ký:%n%1\%2
ErrorRegWriteKey=Lỗi ghi khóa trong sổ đăng ký:%n%1\%2
ErrorIniEntry=Lỗi tạo mục INI trong tệp "%1".
FileAbortRetryIgnoreSkipNotRecommended=&Bỏ qua tệp này (không khuyến nghị)
FileAbortRetryIgnoreIgnoreNotRecommended=&Bỏ qua lỗi và tiếp tục (không khuyến nghị)
SourceIsCorrupted=Tệp nguồn bị hỏng
SourceDoesntExist=Tệp nguồn "%1" không tồn tại
SourceVerificationFailed=Không xác minh được tệp nguồn: %1
VerificationSignatureDoesntExist=Tệp chữ ký "%1" không tồn tại
VerificationSignatureInvalid=Tệp chữ ký "%1" không hợp lệ
VerificationKeyNotFound=Tệp chữ ký "%1" dùng khóa không xác định
VerificationFileNameIncorrect=Tên tệp không đúng
VerificationFileTagIncorrect=Nhãn tệp không đúng
VerificationFileSizeIncorrect=Kích thước tệp không đúng
VerificationFileHashIncorrect=Mã băm tệp không đúng
ExistingFileReadOnly2=Không thay được tệp hiện có vì tệp được đặt ở chế độ chỉ đọc.
ExistingFileReadOnlyRetry=&Bỏ thuộc tính chỉ đọc rồi thử lại
ExistingFileReadOnlyKeepExisting=&Giữ tệp hiện có
ErrorReadingExistingDest=Đã xảy ra lỗi khi đọc tệp hiện có:
FileExistsSelectAction=Chọn thao tác
FileExists2=Tệp đã tồn tại.
FileExistsOverwriteExisting=&Ghi đè tệp hiện có
FileExistsKeepExisting=&Giữ tệp hiện có
FileExistsOverwriteOrKeepAll=&Áp dụng cho các xung đột tiếp theo
ExistingFileNewerSelectAction=Chọn thao tác
ExistingFileNewer2=Tệp hiện có mới hơn tệp mà trình cài đặt đang định cài.
ExistingFileNewerOverwriteExisting=&Ghi đè tệp hiện có
ExistingFileNewerKeepExisting=&Giữ tệp hiện có (khuyên dùng)
ExistingFileNewerOverwriteOrKeepAll=&Áp dụng cho các xung đột tiếp theo
ErrorChangingAttr=Đã xảy ra lỗi khi đổi thuộc tính của tệp hiện có:
ErrorCreatingTemp=Đã xảy ra lỗi khi tạo tệp trong thư mục đích:
ErrorReadingSource=Đã xảy ra lỗi khi đọc tệp nguồn:
ErrorCopying=Đã xảy ra lỗi khi sao chép tệp:
ErrorDownloading=Đã xảy ra lỗi khi tải tệp:
ErrorExtracting=Đã xảy ra lỗi khi giải nén:
ErrorReplacingExistingFile=Đã xảy ra lỗi khi thay tệp hiện có:
ErrorRestartReplace=Thao tác RestartReplace thất bại:
ErrorRenamingTemp=Đã xảy ra lỗi khi đổi tên tệp trong thư mục đích:
ErrorRegisterServer=Không đăng ký được DLL/OCX: %1
ErrorRegSvr32Failed=RegSvr32 thất bại với mã thoát %1
ErrorRegisterTypeLib=Không đăng ký được thư viện kiểu: %1
UninstallDisplayNameMark=%1 (%2)
UninstallDisplayNameMarks=%1 (%2, %3)
UninstallDisplayNameMark32Bit=32-bit
UninstallDisplayNameMark64Bit=64-bit
UninstallDisplayNameMarkAllUsers=Tất cả người dùng
UninstallDisplayNameMarkCurrentUser=Người dùng hiện tại
ErrorOpeningReadme=Đã xảy ra lỗi khi mở tệp README.
ErrorRestartingComputer=Không khởi động lại được máy. Hãy tự khởi động lại.
UninstallNotFound=Tệp "%1" không tồn tại. Không thể gỡ cài đặt.
UninstallOpenError=Không mở được tệp "%1". Không thể gỡ cài đặt
UninstallUnsupportedVer=Định dạng nhật ký gỡ cài đặt "%1" không được phiên bản này nhận diện. Không thể gỡ cài đặt
UninstallUnknownEntry=Phát hiện mục không xác định (%1) trong nhật ký gỡ cài đặt
ConfirmUninstall=Bạn có chắc muốn gỡ %1 và các thành phần chương trình không?%n%nCấu hình, dữ liệu và thông tin đăng nhập đã lưu của bạn được giữ lại.
UninstallOnlyOnWin64=Chỉ có thể gỡ bản cài đặt này trên Windows 64-bit.
OnlyAdminCanUninstall=Chỉ người dùng có quyền quản trị viên mới gỡ được bản cài đặt này.
UninstallStatusLabel=Vui lòng đợi trong khi gỡ %1 khỏi máy của bạn.
UninstalledAll=Đã gỡ %1 khỏi máy của bạn.
UninstalledMost=Đã hoàn tất gỡ %1.%n%nMột số thành phần chưa gỡ được. Bạn có thể gỡ chúng thủ công.
UninstalledAndNeedsRestart=Cần khởi động lại máy để hoàn tất gỡ %1.%n%nBạn có muốn khởi động lại ngay không?
UninstallDataCorrupted=Tệp "%1" bị hỏng. Không thể gỡ cài đặt
ConfirmDeleteSharedFileTitle=Gỡ tệp dùng chung?
ConfirmDeleteSharedFile2=Hệ thống cho biết tệp dùng chung sau không còn được chương trình nào sử dụng. Bạn có muốn gỡ tệp này không?%n%nNếu vẫn có chương trình dùng tệp, gỡ tệp có thể khiến chương trình đó hoạt động sai. Nếu không chắc, chọn Không. Giữ tệp trên máy không gây hại.
SharedFileNameLabel=Tên tệp:
SharedFileLocationLabel=Vị trí:
WizardUninstalling=Trạng thái gỡ cài đặt
StatusUninstalling=Đang gỡ %1...
ShutdownBlockReasonInstallingApp=Đang cài %1.
ShutdownBlockReasonUninstallingApp=Đang gỡ %1.
