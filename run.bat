@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo Chua co moi truong ao. Hay chay start.bat truoc.
  exit /b 1
)
"%~dp0.venv\Scripts\python.exe" -m tg_assistant.cli run
exit /b %ERRORLEVEL%
