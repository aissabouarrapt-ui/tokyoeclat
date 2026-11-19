@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
if not exist ".venv\Scripts\python.exe" (
  echo [!!] Run setup.bat first.
  if /i not "%~1"=="scheduled" pause
  exit /b 2
)
"%~dp0.venv\Scripts\python.exe" "%~dp0app\clipper.py" accounts
pause
