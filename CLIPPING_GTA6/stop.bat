@echo off
cd /d "%~dp0"
echo stop> "%~dp0STOP.txt"
echo STOP.txt created: the system stops now (a running cycle is killed within 1-2 seconds)
echo and will not start again until you run resume.bat (or delete STOP.txt).
pause
