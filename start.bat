@echo off
REM Windows: bam dup file nay (hoac go: start.bat) de chay ca llama-server + web.
REM Them tuy chon neu can, vi du:  start.bat --mock     (AI gia lap, khong can model)
chcp 65001 >nul
cd /d "%~dp0"
set PY=python
if exist ".venv\Scripts\python.exe" set PY=.venv\Scripts\python.exe
%PY% start.py %*
if errorlevel 1 pause
