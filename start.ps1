$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $ProjectDir ".venv\Scripts\python.exe"
Write-Host "Telegram AI Personal Assistant"
if (-not (Test-Path $VenvPython)) {
    Write-Host "[1/4] Tao virtual environment..."
    $Python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $Python) { throw "Can Python 3.12 tro len. Tai tu https://www.python.org/downloads/windows/" }
    $VersionText = & $Python.Source -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    $Parts = $VersionText.Split('.')
    if ([int]$Parts[0] -lt 3 -or ([int]$Parts[0] -eq 3 -and [int]$Parts[1] -lt 12)) { throw "Can Python 3.12 tro len; hien tai $VersionText" }
    & $Python.Source -m venv (Join-Path $ProjectDir ".venv")
}
Write-Host "[2/4] Kiem tra dependency..."
& $VenvPython -c "import tg_assistant, aiogram, alembic, sqlalchemy, telethon, keyring, openai, qdrant_client, fastapi, uvicorn" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[3/4] Cai dependency can thiet (co the mat vai phut)..."
    & $VenvPython -m pip install --upgrade pip
    & $VenvPython -m pip install -e "$ProjectDir"
}
& $VenvPython -m pip check
if ($LASTEXITCODE -ne 0) { throw "Dependency dang xung dot. Xem output pip check o tren." }
Write-Host "[4/4] Khoi dong..."
Push-Location $ProjectDir
try { & $VenvPython -m tg_assistant.cli start }
finally { Pop-Location }
