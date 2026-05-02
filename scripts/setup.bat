@echo off
rem Wrapper Windows : double-clic ou .\scripts\setup.bat depuis cmd.exe
rem Contourne la policy d'execution PowerShell automatiquement.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
