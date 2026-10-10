[CmdletBinding()]
param(
    [string]$Python = 'python',
    [string]$Node = 'node',
    [string]$NpmCli = '',
    [switch]$Preflight
)
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
function Assert-OwnedBuildTree([string]$target) {
    $target = [IO.Path]::GetFullPath($target)
    if (-not $target.StartsWith($root + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Build output containment failed.'
    }
    $probe = $target
    while ($probe) {
        try { $item = Get-Item -LiteralPath $probe -Force -ErrorAction Stop }
        catch [System.Management.Automation.ItemNotFoundException] { $item = $null }
        if ($item -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw 'Reparse point in build output or its ancestors.'
        }
        $probe = Split-Path -Path $probe -Parent
    }
    # With every ancestor non-reparse, the lexical child is physically inside root.
    $pending = [Collections.Generic.Queue[string]]::new()
    if (Test-Path -LiteralPath $target -PathType Container) { $pending.Enqueue($target) }
    while ($pending.Count) {
        foreach ($item in Get-ChildItem -LiteralPath $pending.Dequeue() -Force) {
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw 'Reparse point in build output.'
            }
            if ($item.PSIsContainer) { $pending.Enqueue($item.FullName) }
        }
    }
}
$buildRoot = Join-Path $root 'build/windows'
Assert-OwnedBuildTree $buildRoot
$manifest = Join-Path $buildRoot 'manifest.json'
if (Test-Path -LiteralPath $manifest) { Remove-Item -LiteralPath $manifest -Force }
if ((Get-Location).Path -ne $root) { throw 'Run this script from the checkout root.' }
if (-not $NpmCli) {
    $npmCommand = Get-Command npm.cmd -ErrorAction Stop
    $NpmCli = Join-Path (Split-Path $npmCommand.Source) 'node_modules/npm/bin/npm-cli.js'
}
$environment = Join-Path $buildRoot '.venv'
if (Test-Path -LiteralPath $environment) {
    Assert-OwnedBuildTree $environment
    Remove-Item -LiteralPath $environment -Recurse -Force
}
& $Python -c 'import platform,sys; assert platform.python_version()=="3.12.14" and sys.maxsize>2**32, "Requires CPython 3.12.14 x64"'
if ($LASTEXITCODE -ne 0) { throw 'Python toolchain mismatch.' }
& $Python -m venv $environment
if ($LASTEXITCODE -ne 0) { throw 'Build environment creation failed.' }
$buildPython = Join-Path $environment 'Scripts/python.exe'
& $buildPython -m pip --isolated install --require-hashes --no-deps -r packaging/windows-build.lock
if ($LASTEXITCODE -ne 0) { throw 'Build lock installation failed.' }
& $buildPython -m pip --isolated install --require-hashes --no-deps --no-build-isolation -r packaging/windows-runtime.lock
if ($LASTEXITCODE -ne 0) { throw 'Runtime lock installation failed.' }
& $buildPython -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Dependency validation failed.' }
$arguments = @('packaging/build.py', '--node', $Node, '--npm-cli', $NpmCli)
if ($Preflight) { $arguments += '--preflight' }
& $buildPython @arguments
if ($LASTEXITCODE -ne 0) { throw 'Windows build failed; no candidate accepted.' }
