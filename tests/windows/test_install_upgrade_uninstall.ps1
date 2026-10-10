[CmdletBinding()]
param(
    [string]$Compiler = 'E:/ChatGPT-Temp/p02-compiler-20261010/tools/ISCC.exe',
    [string]$Python = 'E:/ChatGPT Project/Telegram/repository/.venv-q01/Scripts/python.exe'
)
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
function Assert($Condition, [string]$Message) { if (-not $Condition) { throw $Message } }
Assert (Test-Path "$root/packaging/windows-installer.iss") 'P02 missing: no per-user installer exists to compile or exercise.'
Assert (Test-Path "$root/scripts/build-installer.ps1") 'P02 missing: no manifest-bound installer build exists.'
Assert ((Get-FileHash $Compiler).Hash -eq '0A8757031B33777E4C9CBFFEE40F11A5062B36D25CBE144C1DB73B6102B80AD7') 'Compiler must match verified 6.7.3.'

# Every write belongs to a fresh fixture. Production paths/registry IDs are never used.
$id = [Guid]::NewGuid().ToString('N')
$qa = [IO.Path]::GetFullPath((Join-Path $root ".test-temp/p02-$id"))
$probe = $qa
while ($probe) {
    if (Test-Path -LiteralPath $probe) { Assert (-not ((Get-Item -LiteralPath $probe -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) 'QA ancestor is linked.' }
    $probe = Split-Path $probe -Parent
}
New-Item -ItemType Directory -Path "$qa/payload", "$qa/data/.desktop-control", "$qa/data/config", "$qa/startup", "$qa/out" -Force | Out-Null
Set-Content -LiteralPath "$qa/data/.desktop-control/runtime.lock" -Value '' -NoNewline
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
foreach ($path in @("$qa/data", "$qa/data/.desktop-control", "$qa/data/.desktop-control/runtime.lock")) {
    $acl = Get-Acl -LiteralPath $path
    $acl.SetOwner($sid)
    Set-Acl -LiteralPath $path -AclObject $acl
}
$source = @'
using System;
using System.IO;
using System.Threading;
class Synthetic {
    static int Main(string[] args) {
        string root = @"__ROOT__";
        if (args.Length == 1 && (args[0] == "--installer-check" || args[0] == "--installer-prepare")) {
            string config = Path.Combine(root, "data/config/settings.json");
            if (File.Exists(config) && File.ReadAllText(config).Contains("mysql")) return 21;
            if (args[0] == "--installer-check" && File.Exists(Path.Combine(root, "pause"))) {
                File.WriteAllText(Path.Combine(root, "ready"), "synthetic helper under installer lock");
                for (int i = 0; i < 600 && !File.Exists(Path.Combine(root, "continue")); i++) Thread.Sleep(100);
                if (!File.Exists(Path.Combine(root, "continue"))) return 20;
            }
            return File.Exists(Path.Combine(root, "data/.desktop-control/runtime.lock")) ? 0 : 20;
        }
        if (args.Length == 2 && args[0] == "--diagnostics") { File.WriteAllText(args[1], "SYNTHETIC OFFLINE ONLY"); return 0; }
        return 0;
    }
}
'@
$source.Replace('__ROOT__', $qa) | Set-Content -LiteralPath "$qa/fixture.cs"
$csc = Join-Path $env:SystemRoot 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
& $csc /nologo /target:winexe "/out:$qa\payload\TelegramAIPersonalAssistant.exe" "$qa\fixture.cs"
Assert ($LASTEXITCODE -eq 0) 'Could not compile inert synthetic fixture.'
$records = [Collections.Generic.List[object]]::new()
function Record([string]$Name, [string]$Status, [string]$Detail) { $records.Add(@{ test = $Name; status = $Status; detail = $Detail }) }
function Run-Setup([string]$Executable, [string]$Label, [string]$Extra = '') {
    try {
        $process = Start-Process -FilePath $Executable -ArgumentList "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /LOG=`"$qa/$Label.log`" $Extra" -WindowStyle Hidden -Wait -PassThru
    } catch {
        @{ purpose = 'SYNTHETIC-NO-CANDIDATE'; status = 'Blocked'; operation = $Label; reason = $_.Exception.Message; qa_root = $qa
            compiler_sha256 = (Get-FileHash $Compiler).Hash; installer_sha256 = (Get-FileHash $Executable).Hash
            fixture_exe_sha256 = (Get-FileHash "$qa/payload/TelegramAIPersonalAssistant.exe").Hash
            observed_results = $records; remaining_install_tests = 'Pending; process launch failed, no application-control changes authorized'
        } | ConvertTo-Json -Depth 8 | Set-Content "$qa/results.json"
        throw
    }
    return $process.ExitCode
}
function Compile([string]$Version, [string]$Payload) {
    Set-Content "$qa/payload/version.txt" $Version
    @{ purpose = 'SYNTHETIC-NO-CANDIDATE'; version = $Version } | ConvertTo-Json | Set-Content "$qa/manifest.json"
    & $Compiler '/Qp' "/DSourceDir=$qa/payload" "/DSourceManifest=$qa/manifest.json" "/DAppVersion=$Version" "/DPayloadId=$Payload" "/DSyntheticRoot=$qa" "/DSyntheticId=$id" "/O$qa/out" "/F$Payload" "$root/packaging/windows-installer.iss" | Out-Host
    Assert ($LASTEXITCODE -eq 0) 'Synthetic installer compile failed.'
    return "$qa/out/$Payload.exe"
}
$first = Compile '0.1.0' 'synthetic-v1'
$second = Compile '0.1.1' 'synthetic-v2'
foreach ($item in @('config', 'db', 'sessions', 'qdrant', 'downloads', 'backups', 'logs')) {
    New-Item -ItemType Directory -Path "$qa/data/$item" -Force | Out-Null
    Set-Content -LiteralPath "$qa/data/$item/sentinel" -Value "owned synthetic $item"
}
$sentinels = @{}
Get-ChildItem "$qa/data" -Filter sentinel -File -Recurse | ForEach-Object { $sentinels[$_.FullName] = (Get-FileHash $_.FullName).Hash }
Assert ((Run-Setup $first 'install') -eq 0) 'Synthetic clean install failed.'
Assert (Test-Path "$qa/app/versions/synthetic-v1/TelegramAIPersonalAssistant.exe") 'Payload missing.'
Assert (-not (Test-Path "$qa/startup/Telegram AI Personal Assistant.lnk")) 'Autostart unexpectedly enabled.'
Record 'synthetic_install' 'Pass' 'Real compiled installer, fixed synthetic per-user paths, default startup absent.'
Record 'clean_standard_user' 'Pending' 'Host is an existing developer machine; no standard-user or runtime-free clean VM claim.'

Set-Content "$qa/data/config/settings.json" '{"version":1,"settings":{"storage_backend":"mysql"}}'
$old = (Get-FileHash "$qa/app/versions/synthetic-v1/TelegramAIPersonalAssistant.exe").Hash
Assert ((Run-Setup $second 'mysql-refusal') -ne 0) 'Legacy synthetic MySQL fixture was accepted.'
Assert (-not (Test-Path "$qa/app/versions/synthetic-v2")) 'Refused upgrade wrote payload.'
Assert ((Get-FileHash "$qa/app/versions/synthetic-v1/TelegramAIPersonalAssistant.exe").Hash -eq $old) 'Refused upgrade changed old payload.'
Record 'upgrade_previous_mysql' 'SyntheticPass' 'Fixture helper refuses before copy; real Python preflight tested separately. Final packaged helper integration Pending.'
Set-Content "$qa/data/config/settings.json" '{"version":1,"settings":{"storage_backend":"sqlite"}}'
$held = [IO.File]::Open("$qa/data/.desktop-control/runtime.lock", [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::ReadWrite)
try {
    $held.Lock(0, 1)
    Assert ((Run-Setup $second 'busy-refusal') -ne 0) 'Installer ignored existing byte-zero lock.'
    Assert (-not (Test-Path "$qa/app/versions/synthetic-v2")) 'Busy upgrade wrote payload.'
} finally { $held.Dispose() }
Record 'runtime_busy_refusal' 'Pass' 'Actual compiled installer refuses held byte-zero runtime lock, preserving previous payload.'

# Pause the inert helper after the Pascal installer owns the lock; the real guard must refuse.
Set-Content "$qa/pause" '1'
$install = Start-Process -FilePath $second -ArgumentList "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /TASKS=autostart /LOG=`"$qa/upgrade.log`"" -WindowStyle Hidden -PassThru
$deadline = [DateTime]::UtcNow.AddSeconds(30)
while (-not (Test-Path "$qa/ready") -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 100 }
Assert (Test-Path "$qa/ready") 'Installer did not reach held-lock check.'
$env:PYTHONPATH = "$root/src"
$env:QT_QPA_PLATFORM = 'offscreen'
$guardCheck = @'
import sys
from pathlib import Path
from tg_assistant.desktop.instance import InstanceGuard, AlreadyRunning
try:
    guard = InstanceGuard(Path(sys.argv[1])).acquire()
except AlreadyRunning:
    sys.exit(0)
else:
    guard.close()
    sys.exit(1)
'@
try {
    & $Python -c $guardCheck "$qa/data/.desktop-control"
    Assert ($LASTEXITCODE -eq 0) 'Real native InstanceGuard acquired while installer held the lock.'
} finally { Set-Content "$qa/continue" '1' }
$install.WaitForExit()
Assert ($install.ExitCode -eq 0) 'Synthetic upgrade failed.'
Assert (Test-Path "$qa/app/versions/synthetic-v1/TelegramAIPersonalAssistant.exe") 'Previous payload was removed.'
Assert (Test-Path "$qa/app/versions/synthetic-v2/TelegramAIPersonalAssistant.exe") 'Upgrade payload missing.'
Assert (Test-Path "$qa/startup/Telegram AI Personal Assistant.lnk") 'Explicit autostart did not create shortcut.'
Record 'upgrade_sqlite' 'SyntheticPass' 'Versioned real installer upgrade and old payload retention; real SQLite migration/backup acceptance Pending.'
Record 'runtime_start_fenced' 'Pass' 'Real InstanceGuard cannot acquire while actual installer holds native byte zero.'
Record 'reboot_autostart_optin' 'Pending' 'Unchecked default and explicitly selected shortcut verified; actual logon/reboot unobserved.'

Assert ((Run-Setup $second 'custom-target' "/DIR=`"$qa/forbidden`"") -ne 0) 'Custom target accepted.'
Assert (-not (Test-Path "$qa/forbidden")) 'Refused custom target was created.'
New-Item -ItemType Directory -Path "$qa/outside" | Out-Null
Set-Content "$qa/outside/sentinel" 'outside'
New-Item -ItemType Junction -Path "$qa/app/linked" -Target "$qa/outside" | Out-Null
try { Assert ((Run-Setup $second 'linked-target') -ne 0) 'Linked target accepted.' }
finally { Remove-Item -LiteralPath "$qa/app/linked" }
Assert ((Get-Content "$qa/outside/sentinel") -eq 'outside') 'Linked outside sentinel changed.'
Record 'unsafe_targets' 'Pass' 'Custom install and descendant junction refused without modifying outside sentinel.'
$app = "$qa/app/versions/synthetic-v2/TelegramAIPersonalAssistant.exe"
$diagnostic = Start-Process -FilePath $app -ArgumentList "--diagnostics `"$qa/offline.txt`"" -WindowStyle Hidden -Wait -PassThru
Assert ($diagnostic.ExitCode -eq 0 -and (Get-Content "$qa/offline.txt") -eq 'SYNTHETIC OFFLINE ONLY') 'Installed fixture offline startup failed.'
Record 'offline_start' 'SyntheticPass' 'Inert installed fixture executes offline; real frozen launcher/Qt diagnostics Pending.'
Assert ((Run-Setup "$qa/app/unins000.exe" 'uninstall') -eq 0) 'Synthetic uninstall failed.'
foreach ($path in $sentinels.Keys) { Assert ((Get-FileHash -LiteralPath $path).Hash -eq $sentinels[$path]) 'Uninstall changed data sentinel.' }
Assert (-not (Test-Path "$qa/app/versions/synthetic-v2/TelegramAIPersonalAssistant.exe")) 'Uninstall left current payload.'
Assert (-not (Test-Path "$qa/startup/Telegram AI Personal Assistant.lnk")) 'Uninstall left startup shortcut.'
Record 'uninstall_preserves_data' 'Pass' 'Compiled uninstaller preserves all seven owned data categories; actual credential store intentionally untouched.'
$iss = Get-Content "$root/packaging/windows-installer.iss" -Raw
Assert ($iss -notmatch '(?im)^\[UninstallDelete\]|purge|DelTree') 'Unexpected purge mechanism.'
Record 'opt_in_purge_only' 'Pass' 'No automated purge path exists, per private-preview scope ruling.'
Record 'interrupted_copy_recovery' 'Pending' 'Pre-copy refusal preserves previous payload; power-loss/disk-full during file/shortcut commit not exercised.'
$report = @{ purpose = 'SYNTHETIC-NO-CANDIDATE'; qa_root = $qa; host = [Environment]::OSVersion.VersionString; elevated = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator); results = $records
    source_sha256 = (Get-FileHash "$root/packaging/windows-installer.iss").Hash
    compiler_sha256 = (Get-FileHash $Compiler).Hash; fixture_source_sha256 = (Get-FileHash "$qa/fixture.cs").Hash; fixture_exe_sha256 = (Get-FileHash "$qa/payload/TelegramAIPersonalAssistant.exe").Hash
    installers = @($first, $second) | ForEach-Object { @{ path = $_; sha256 = (Get-FileHash $_).Hash; signature = (Get-AuthenticodeSignature $_).Status.ToString() } }
}
$report | ConvertTo-Json -Depth 8 | Set-Content "$qa/results.json"
$report | ConvertTo-Json -Depth 8
