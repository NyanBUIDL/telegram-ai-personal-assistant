$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$tokens = $null; $errors = $null
$syntax = [Management.Automation.Language.Parser]::ParseFile("$root/scripts/build-installer.ps1", [ref]$tokens, [ref]$errors)
if ($errors) { throw 'Build wrapper does not parse.' }
# Exercise the actual validation functions without invoking or deleting candidate outputs.
foreach ($name in @('Assert-SafeTree', 'Hash', 'Assert-Inventory', 'Assert-Candidate')) {
    $node = $syntax.Find({ param($part) $part -is [Management.Automation.Language.FunctionDefinitionAst] -and $part.Name -eq $name }, $true)
    . ([scriptblock]::Create($node.Extent.Text))
}
$qa = [IO.Path]::GetFullPath((Join-Path $root ('.test-temp/p02-manifest-' + [Guid]::NewGuid().ToString('N'))))
Assert-SafeTree $qa
New-Item -ItemType Directory -Path "$qa/payload", "$qa/outside" -Force | Out-Null
Set-Content "$qa/payload/one.txt" 'original'
$payload = [IO.Path]::GetFullPath("$qa/payload")
$inventory = [pscustomobject]@{ 'one.txt' = Hash "$qa/payload/one.txt" }
$count = 0
function Refuses([scriptblock]$Action, [string]$Case) {
    $refused = $false
    try { & $Action } catch { $refused = $true }
    if (-not $refused) { throw "Validation did not refuse: $Case" }
    $script:count++
}
Assert-Inventory $payload $inventory
$count++
Set-Content "$qa/payload/one.txt" 'changed'
Refuses { Assert-Inventory $payload $inventory } 'changed content'
Set-Content "$qa/payload/one.txt" 'original'
Set-Content "$qa/payload/extra.txt" 'extra'
Refuses { Assert-Inventory $payload $inventory } 'extra unmanifested file'
Remove-Item -LiteralPath "$qa/payload/extra.txt"
Refuses { Assert-Inventory $payload ([pscustomobject]@{}) } 'empty inventory'
Refuses { Assert-Inventory $payload ([pscustomobject]@{ '../one.txt' = $inventory.'one.txt' }) } 'traversal'
New-Item -ItemType Junction -Path "$qa/payload/redirect" -Target "$qa/outside" | Out-Null
try { Refuses { Assert-Inventory $payload $inventory } 'descendant junction' }
finally { Remove-Item -LiteralPath "$qa/payload/redirect" }
New-Item -ItemType Junction -Path "$qa/redirect" -Target "$qa/outside" | Out-Null
try { Refuses { Assert-SafeTree "$qa/redirect/not-yet-created" } 'linked ancestor of absent output' }
finally { Remove-Item -LiteralPath "$qa/redirect" }
$candidate = [pscustomobject]@{ schema_version = 1; source_dirty = $false; signed = $false; purpose = 'private-preview-candidate'; distribution = 'private-only'; version = '0.1.0'; source_commit = ('a' * 40); diagnostics = [pscustomobject]@{ status = 'ok'; frozen = $true }; executable = 'dist/TelegramAIPersonalAssistant/TelegramAIPersonalAssistant.exe' }
Assert-Candidate $candidate
$count++
foreach ($case in @(
    @{ key = 'source_dirty'; value = $true }, @{ key = 'source_dirty'; value = 'false' },
    @{ key = 'signed'; value = $true }, @{ key = 'signed'; value = $null },
    @{ key = 'purpose'; value = 'dirty-preflight-only' }, @{ key = 'distribution'; value = 'public' },
    @{ key = 'version'; value = '0.1.0-beta' }, @{ key = 'source_commit'; value = 'missing' },
    @{ key = 'executable'; value = '../outside.exe' }
)) {
    $old = $candidate.($case.key)
    $candidate.($case.key) = $case.value
    Refuses { Assert-Candidate $candidate } $case.key
    $candidate.($case.key) = $old
}
$candidate.diagnostics.frozen = 'true'
Refuses { Assert-Candidate $candidate } 'malformed diagnostics status'
@{ purpose = 'source-validation-only'; passed = $count; qa_root = $qa } | ConvertTo-Json
