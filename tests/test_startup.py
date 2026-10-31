"""Exercise the Windows entrypoint offline, intercepting only child processes."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")
pytestmark = pytest.mark.skipif(not POWERSHELL, reason="PowerShell is required")


def run_startup(tmp_path: Path, *, failure: str = "", existing: bool = True):
    project = tmp_path / "project with spaces"
    project.mkdir()
    shutil.copyfile(Path(__file__).parents[1] / "start.ps1", project / "start.ps1")
    venv_python = project / ".venv" / "Scripts" / "python.exe"
    if existing:
        venv_python.parent.mkdir(parents=True)
        venv_python.touch()
    harness = tmp_path / "harness.ps1"
    harness.write_text(
        r"""
param($Project, $Failure, $RealPython)
$ErrorActionPreference = 'Stop'
$global:Calls = [System.Collections.Generic.List[string]]::new()
$global:Failure = $Failure
$global:RealPython = $RealPython
function Invoke-FakePython {
    $command = $args -join ' '
    $global:Calls.Add($command)
    $global:LASTEXITCODE = 0
    if ($command -like '-c *sys*') {
        if ($global:Failure -eq 'interpreter') { $global:LASTEXITCODE = 7; return }
        '3.12'
    } elseif ($command -like '-c *import*') {
        if ($global:Failure -eq 'install') { $global:LASTEXITCODE = 1 }
        if ($global:Failure -eq 'missing-dependency') {
            & $global:RealPython -c 'import tg_assistant_missing_startup_dependency'
        }
    } elseif ($command -like '-m venv*') {
        if ($global:Failure -eq 'venv') { $global:LASTEXITCODE = 8 }
    } elseif ($command -like '-m pip install*') {
        if ($global:Failure -eq 'install') { $global:LASTEXITCODE = 9 }
    } elseif ($command -eq '-m tg_assistant.cli start') {
        if ($global:Failure -eq 'start') { $global:LASTEXITCODE = 10 }
    }
}
Set-Item -Path "Function:\$(Join-Path $Project '.venv\Scripts\python.exe')" -Value ${function:Invoke-FakePython}
Set-Item -Path 'Function:\fake-base-python' -Value ${function:Invoke-FakePython}
function Get-Command {
    param($Name, $ErrorAction)
    [pscustomobject]@{ Source = 'fake-base-python' }
}
try {
    & (Join-Path $Project 'start.ps1')
    $result = 0
} catch {
    Write-Output "STARTUP_ERROR=$($_.Exception.Message)"
    $result = 1
}
Write-Output ('TEST_CALLS=' + (ConvertTo-Json -InputObject @($global:Calls) -Compress))
exit $result
""",
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(harness), str(project), failure, sys.executable],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    calls_lines = [line for line in result.stdout.splitlines() if line.startswith("TEST_CALLS=")]
    assert calls_lines, (result.stdout, result.stderr)
    calls_line = calls_lines[-1]
    return result, json.loads(calls_line.removeprefix("TEST_CALLS=")), venv_python


@pytest.mark.parametrize("failure", ["interpreter", "install", "venv"])
def test_startup_stops_before_launch_after_failed_setup(tmp_path, failure):
    result, calls, venv_python = run_startup(
        tmp_path, failure=failure, existing=failure != "venv"
    )
    assert result.returncode == 1, result.stdout
    assert "-m tg_assistant.cli start" not in calls
    if failure == "interpreter":
        assert not any(call.startswith("-m pip") for call in calls)
        assert venv_python.exists(), "A relocated environment must be preserved for manual recovery"


def test_startup_surfaces_application_exit_failure(tmp_path):
    result, calls, _ = run_startup(tmp_path, failure="start")
    assert "-m tg_assistant.cli start" in calls
    assert result.returncode == 1, result.stdout


def test_startup_reuses_valid_environment_in_path_with_spaces(tmp_path):
    result, calls, _ = run_startup(tmp_path)
    assert result.returncode == 0, result.stdout
    assert calls[-1] == "-m tg_assistant.cli start"
    assert not any(call.startswith("-m pip install") for call in calls)


def test_missing_dependency_stderr_does_not_abort_windows_powershell_setup(tmp_path):
    result, calls, _ = run_startup(tmp_path, failure="missing-dependency")
    assert result.returncode == 0, result.stdout
    assert any(call.startswith("-m pip install") for call in calls)
    assert calls[-1] == "-m tg_assistant.cli start"
