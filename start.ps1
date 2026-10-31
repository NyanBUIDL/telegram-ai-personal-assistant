$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $ProjectDir ".venv\Scripts\python.exe"
Write-Host "Telegram AI Personal Assistant"
function Test-PythonVersion($Executable) {
    try {
        $VersionText = & $Executable -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($LASTEXITCODE -ne 0) { return $false }
        $Version = [version]($VersionText | Select-Object -Last 1)
        return $Version -ge [version]"3.12"
    } catch { return $false }
}

if ((Test-Path $VenvPython) -and -not (Test-PythonVersion $VenvPython)) {
    throw "Python trong .venv khong chay duoc hoac cu hon 3.12 (co the da copy tu may khac). Giu nguyen .venv, doi ten thanh .venv.old roi chay lai start.bat. Xem TROUBLESHOOTING.md."
}
if (-not (Test-Path $VenvPython)) {
    Write-Host "[1/4] Tao virtual environment..."
    $Python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $Python) { throw "Can Python 3.12 tro len. Tai tu https://www.python.org/downloads/windows/" }
    if (-not (Test-PythonVersion $Python.Source)) { throw "Can Python 3.12 tro len chay duoc trong terminal. Kiem tra python --version; xem TROUBLESHOOTING.md." }
    & $Python.Source -m venv (Join-Path $ProjectDir ".venv")
    if ($LASTEXITCODE -ne 0) { throw "Khong tao duoc .venv. Kiem tra quyen ghi va Python; xem TROUBLESHOOTING.md." }
}
Write-Host "[2/4] Kiem tra dependency..."
$DependenciesReady = $false
try {
    & $VenvPython -c "import tg_assistant, aiogram, alembic, sqlalchemy, telethon, keyring, openai, qdrant_client, fastapi, uvicorn" 2>$null
    $DependenciesReady = $LASTEXITCODE -eq 0
} catch {
    # Windows PowerShell 5.1 treats native stderr as an exception with Stop.
    $DependenciesReady = $false
}
if (-not $DependenciesReady) {
    Write-Host "[3/4] Cai dependency can thiet (co the mat vai phut)..."
    & $VenvPython -m pip install -e "$ProjectDir"
    if ($LASTEXITCODE -ne 0) { throw "Cai dependency that bai. Kiem tra mang va loi pip o tren, roi chay lai start.bat." }
}
& $VenvPython -m pip check
if ($LASTEXITCODE -ne 0) { throw "Dependency dang xung dot. Xem output pip check o tren." }
Write-Host "[4/4] Khoi dong..."
Push-Location $ProjectDir
try {
    & $VenvPython -m tg_assistant.cli start
    if ($LASTEXITCODE -ne 0) { throw "Khoi dong that bai. Xem loi o tren va TROUBLESHOOTING.md; ung dung chua san sang." }
}
finally { Pop-Location }
