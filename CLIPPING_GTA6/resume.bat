@echo off
cd /d "%~dp0"
if exist "%~dp0STOP.txt" del "%~dp0STOP.txt"
echo STOP.txt removed: the scheduled task will run again at its next 2-hour slot.
pause
