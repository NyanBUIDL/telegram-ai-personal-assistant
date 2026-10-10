[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Compiler,
    [string]$Manifest = 'build/windows/manifest.json'
)
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if ((Get-Location).Path -ne $root) { throw 'Run from the checkout root.' }

function Assert-SafeTree([string]$Path) {
    $probe = [IO.Path]::GetFullPath($Path)
    while ($probe) {
        if (Test-Path -LiteralPath $probe) {
            if ((Get-Item -LiteralPath $probe -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Linked path refused.' }
        }
        $probe = Split-Path $probe -Parent
    }
    $pending = [Collections.Generic.Queue[string]]::new()
    if (Test-Path -LiteralPath $Path -PathType Container) { $pending.Enqueue($Path) }
    while ($pending.Count) {
        foreach ($item in Get-ChildItem -LiteralPath $pending.Dequeue() -Force) {
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Linked tree entry refused.' }
            if ($item.PSIsContainer) { $pending.Enqueue($item.FullName) }
        }
    }
}
function Hash([string]$Path) { (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }
function Git-Read([string[]]$Arguments) {
    $result = & git @Arguments
    if ($LASTEXITCODE -ne 0) { throw 'Cannot verify source identity.' }
    return ($result -join "`n").Trim()
}
function Assert-Inventory([string]$Directory, $Inventory) {
    Assert-SafeTree $Directory
    $names = @($Inventory.PSObject.Properties.Name)
    if ($names.Count -eq 0) { throw 'Empty artifact inventory.' }
    $actual = @(Get-ChildItem -LiteralPath $Directory -File -Recurse -Force | ForEach-Object { $_.FullName.Substring($Directory.Length + 1).Replace('\', '/') })
    if (Compare-Object $names $actual -CaseSensitive) { throw 'Artifact inventory membership mismatch.' }
    foreach ($name in $names) {
        if ($name -match '(^/|\\|:|(^|/)\.\.?(/|$))') { throw 'Unsafe inventory path.' }
        if ((Hash (Join-Path $Directory $name)) -cne $Inventory.$name) { throw 'Artifact inventory hash mismatch.' }
    }
}
function Assert-Candidate($Candidate) {
    if ($Candidate.schema_version -ne 1 -or $Candidate.source_dirty -isnot [bool] -or $Candidate.source_dirty -or
        $Candidate.signed -isnot [bool] -or $Candidate.signed -or $Candidate.purpose -cne 'private-preview-candidate' -or
        $Candidate.distribution -cne 'private-only' -or $Candidate.version -isnot [string] -or $Candidate.version -notmatch '^\d+\.\d+\.\d+(\.\d+)?$' -or
        $Candidate.source_commit -isnot [string] -or $Candidate.source_commit -notmatch '^[0-9a-f]{40}$' -or $Candidate.diagnostics.status -cne 'ok' -or
        $Candidate.diagnostics.frozen -isnot [bool] -or -not $Candidate.diagnostics.frozen -or $Candidate.executable -cne 'dist/TelegramAIPersonalAssistant/TelegramAIPersonalAssistant.exe') {
        throw 'Only a complete clean unsigned private P01 candidate is accepted.'
    }
}

$output = Join-Path $root 'build/windows-installer'
Assert-SafeTree $output
if (Test-Path "$output/manifest.json") { Remove-Item -LiteralPath "$output/manifest.json" -Force }
$Manifest = [IO.Path]::GetFullPath($Manifest)
Assert-SafeTree $Manifest
$manifestHash = Hash $Manifest
$candidate = Get-Content -LiteralPath $Manifest -Raw | ConvertFrom-Json
Assert-Candidate $candidate
function Assert-Source {
    if ((Git-Read @('status', '--porcelain')) -or (Git-Read @('rev-parse', 'HEAD')) -cne $candidate.source_commit) { throw 'Source is dirty or differs from P01.' }
    $names = (Git-Read @('-c', 'core.quotepath=false', 'ls-files')).Split("`n")
    $expected = @($candidate.source_sha256.PSObject.Properties.Name)
    if (Compare-Object $names $expected -CaseSensitive) { throw 'Source inventory membership mismatch.' }
    foreach ($name in $names) {
        if ($name -match '(^/|\\|:|(^|/)\.\.?(/|$))') { throw 'Unsafe source inventory path.' }
        $path = Join-Path $root $name
        Assert-SafeTree $path
        if ((Hash $path) -cne $candidate.source_sha256.$name) { throw 'Source changed since P01.' }
    }
    $project = Get-Content "$root/pyproject.toml" -Raw
    if ($project -notmatch ('(?m)^version = "' + [regex]::Escape($candidate.version) + '"\r?$')) { throw 'Version differs from source.' }
}
Assert-Source
$payload = [IO.Path]::GetFullPath((Join-Path (Split-Path $Manifest) 'dist/TelegramAIPersonalAssistant'))
Assert-Inventory $payload $candidate.files_sha256
if (Test-Path -LiteralPath (Join-Path $payload 'p01-manifest.json')) { throw 'Payload uses reserved installer manifest filename.' }
if ((Get-AuthenticodeSignature -LiteralPath (Join-Path $payload 'TelegramAIPersonalAssistant.exe')).Status -ne 'NotSigned') { throw 'P01 executable signing state differs from the unsigned preview manifest.' }
$Compiler = [IO.Path]::GetFullPath($Compiler)
Assert-SafeTree (Split-Path $Compiler)
if ((Hash $Compiler) -ne '0a8757031b33777e4c9cbffee40f11a5062b36d25cbe144c1db73b6102b80ad7') { throw 'Compiler differs from verified Inno Setup 6.7.3.' }
$compilerFiles = [ordered]@{}
$compilerDirectory = Split-Path $Compiler
foreach ($file in Get-ChildItem $compilerDirectory -Recurse -File | Sort-Object FullName) {
    $compilerFiles[$file.FullName.Substring($compilerDirectory.Length + 1).Replace('\', '/')] = Hash $file.FullName
}
New-Item -ItemType Directory -Path $output -Force | Out-Null
$payloadId = "$($candidate.version)-$($manifestHash.Substring(0,12))"
$name = "TelegramAIPersonalAssistant-$payloadId-private-unsigned"
& $Compiler '/Qp' "/DSourceDir=$payload" "/DSourceManifest=$Manifest" "/DAppVersion=$($candidate.version)" "/DPayloadId=$payloadId" "/O$output" "/F$name" "$root/packaging/windows-installer.iss"
if ($LASTEXITCODE -ne 0) { throw 'Installer compile failed; no candidate accepted.' }
Assert-Source
Assert-Inventory $payload $candidate.files_sha256
Assert-Inventory $compilerDirectory ([pscustomobject]$compilerFiles)
if ((Hash $Manifest) -cne $manifestHash) { throw 'P01 manifest changed during compile.' }
$installer = Join-Path $output "$name.exe"
$signature = Get-AuthenticodeSignature -LiteralPath $installer
if ($signature.Status -ne 'NotSigned') { throw 'Unexpected installer signature state; manual review required.' }
$record = [ordered]@{
    schema_version = 1; purpose = 'private-preview-candidate'; distribution = 'private-only'; signed = $false
    version = $candidate.version; source_commit = $candidate.source_commit; source_dirty = $false
    p01_manifest_sha256 = $manifestHash; payload_id = $payloadId
    installer = "$name.exe"; installer_sha256 = Hash $installer
    signature_observation = $signature.Status.ToString(); install_scope = 'current-user; fixed local app path; data preserved'
    compiler = @{ version = '6.7.3'; executable_sha256 = Hash $Compiler; files_sha256 = $compilerFiles
        official_asset_sha256 = '9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732'
        official_release = 'https://github.com/jrsoftware/issrc/releases/tag/is-6_7_3' }
    acceptance = 'Pending exact installed candidate QA and independent review; unsigned private preview only'
}
$record | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath "$output/manifest.json" -Encoding utf8
$record | ConvertTo-Json -Depth 8
