@echo off
rem Launcher unifie : double-clic depuis l'Explorateur Windows pour jouer.
rem - Si le venv n'existe pas, lance setup.bat automatiquement (install complet).
rem - Sinon, lance directement le jeu.
rem - Le bootstrap RAG (telechargement de l'index pre-build depuis GitHub Releases)
rem   se fait dans l'app au demarrage si necessaire.

setlocal
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

set "VENV_PY=%SCRIPT_DIR%.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
    echo [shinobi] Premiere utilisation : installation complete...
    echo [shinobi] Cela peut prendre 5-10 minutes la premiere fois.
    call "%SCRIPT_DIR%scripts\setup.bat"
    if errorlevel 1 (
        echo [shinobi] L'installation a echoue. Voir messages ci-dessus. 1>&2
        pause
        exit /b 1
    )
)

rem Repare le package si import casse (changement de Python, .venv corrompu, etc.)
"%VENV_PY%" -c "import shinobi" 2>nul
if errorlevel 1 (
    echo [shinobi] Reparation du package...
    "%VENV_PY%" -m pip install -e . --quiet
)

rem Lance l'app : le menu (et le bootstrap RAG) s'affichera automatiquement.
"%VENV_PY%" -m shinobi
set "EXIT_CODE=%errorlevel%"

if %EXIT_CODE% neq 0 (
    echo.
    echo [shinobi] L'app s'est terminee avec un code d'erreur (%EXIT_CODE%).
    pause
)

endlocal
exit /b %EXIT_CODE%
