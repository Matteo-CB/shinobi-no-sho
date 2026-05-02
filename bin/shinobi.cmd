@echo off
rem Launcher Windows pour shinobi : marche dans cmd, PowerShell, Windows Terminal,
rem sans necessiter l'activation prealable du venv.
setlocal
set "SCRIPT_DIR=%~dp0"
set "PROJECT_ROOT=%SCRIPT_DIR%.."
"%PROJECT_ROOT%\.venv\Scripts\python.exe" -m shinobi %*
