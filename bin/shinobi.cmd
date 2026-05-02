@echo off
rem Launcher Windows pour shinobi : marche dans cmd, PowerShell, Windows Terminal,
rem sans necessiter l'activation prealable du venv. Auto-repare si le package
rem n'est pas installe (pip install -e . a la volee).
setlocal
set "SCRIPT_DIR=%~dp0"
set "PROJECT_ROOT=%SCRIPT_DIR%.."
set "VENV_PY=%PROJECT_ROOT%\.venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
    echo [shinobi] venv introuvable. Lance .\scripts\setup.bat d'abord. 1>&2
    exit /b 1
)
"%VENV_PY%" -c "import shinobi" 2>nul
if errorlevel 1 (
    echo [shinobi] Package non installe, reparation automatique... 1>&2
    pushd "%PROJECT_ROOT%"
    "%VENV_PY%" -m pip install -e . --quiet
    popd
)
"%VENV_PY%" -m shinobi %*
