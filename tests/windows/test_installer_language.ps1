param([string]$Compiler = 'E:/ChatGPT-Temp/p02-compiler-20261010/tools/ISCC.exe')
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
function Assert($Value, [string]$Reason) { if (-not $Value) { throw $Reason } }
$resource = Join-Path $root 'packaging/windows-installer-vi.isl'
Assert (Test-Path -LiteralPath $resource) 'Vietnamese wizard resource is missing.'
$default = Join-Path (Split-Path $Compiler) 'Default.isl'
Assert ((Get-FileHash -LiteralPath $default).Hash -eq '42A5F6F7DBBDDF26CC278F67DB5D894235CE1D126A6856702E31BD02023A1316') 'Message baseline differs from pinned Inno 6.7.3.'
function Messages([string]$Path) {
    $result = @{}; $section = ''
    foreach ($line in Get-Content -LiteralPath $Path -Encoding utf8) {
        if ($line -match '^\[(.+)\]$') { $section = $Matches[1] }
        if ($section -eq 'Messages' -and $line -match '^([^;=]+)=(.*)$') {
            Assert (-not $result.ContainsKey($Matches[1])) 'Duplicate message key.'
            $result[$Matches[1]] = $Matches[2]
        }
    }
    return $result
}
$english = Messages $default
$vietnamese = Messages $resource
Assert (-not (Compare-Object @($english.Keys) @($vietnamese.Keys))) 'Vietnamese message coverage differs from compiler keys.'
$neutral = @('HelpTextNote','AboutSetupNote','TranslatorNote','BeveledLabel','ComponentSize1','ComponentSize2','UninstallDisplayNameMark','UninstallDisplayNameMarks','UninstallDisplayNameMark32Bit','UninstallDisplayNameMark64Bit')
foreach ($key in $english.Keys) {
    $pattern = '%[0-9]+|\[(?:name(?:/ver)?|gb|mb)\]'
    $expected = @([regex]::Matches($english[$key], $pattern).Value | Sort-Object)
    $actual = @([regex]::Matches($vietnamese[$key], $pattern).Value | Sort-Object)
    Assert (($expected -join '|') -ceq ($actual -join '|')) "Placeholder mismatch: $key"
    if ($key -notin $neutral) { Assert ($english[$key] -cne $vietnamese[$key]) "Untranslated message: $key" }
}
$script = Get-Content "$root/packaging/windows-installer.iss" -Raw -Encoding utf8
Assert ($script -match 'MessagesFile: "compiler:Default.isl,windows-installer-vi.isl"') 'Vietnamese wizard is not configured.'
Assert ($script -match 'Name: autostart; Description: "[^"\r\n]*[\u0080-\uFFFF][^"\r\n]*"; Flags: unchecked') 'Autostart consent is not Vietnamese and unchecked.'
Assert ($script -match 'ProductName "Telegram AI Personal Assistant \([^"\r\n]*chưa ký số\)"') 'Unsigned private product label is missing.'
$refusals = [regex]::Matches($script, "(?:Result := |MsgBox\()'([^']+)'" )
Assert ($refusals.Count -eq 11) 'Installer refusal coverage changed; review new messages.'
foreach ($message in $refusals) { Assert ($message.Groups[1].Value -match '[\u0080-\uFFFF]') 'An installer refusal remains English.' }
foreach ($path in @($resource, "$root/packaging/windows-installer.iss")) {
    $bytes = [IO.File]::ReadAllBytes($path)
    Assert ($bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) 'Compiler source must declare UTF-8 with BOM.'
}
@{ status = 'source-only'; translated_wizard_messages = $vietnamese.Count; app_refusals = $refusals.Count; placeholders = 'preserved'; installer_execution = 'Not performed' } | ConvertTo-Json
