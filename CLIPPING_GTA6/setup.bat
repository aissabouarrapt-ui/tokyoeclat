@echo off
cd /d "%~dp0"
chcp 65001 >nul
echo Installing CLIPPING_GTA6 (Node, ffmpeg, Python, heistclips, faster-whisper)...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0app\setup.ps1"
pause
