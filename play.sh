#!/usr/bin/env bash
# Launcher unifie Linux/macOS : ./play.sh pour jouer.
# - Si le venv n'existe pas, lance setup.sh automatiquement.
# - Sinon, lance directement le jeu.
# - Le bootstrap RAG (telechargement de l'index depuis GitHub Releases) se fait
#   dans l'app au demarrage si necessaire.

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV_PY=".venv/bin/python"

if [ ! -x "$VENV_PY" ]; then
    echo "[shinobi] Premiere utilisation : installation complete..."
    echo "[shinobi] Cela peut prendre 5-10 minutes la premiere fois."
    bash "scripts/setup.sh"
fi

# Repare le package si import casse
if ! "$VENV_PY" -c "import shinobi" 2>/dev/null; then
    echo "[shinobi] Reparation du package..."
    "$VENV_PY" -m pip install -e . --quiet
fi

# Lance l'app
"$VENV_PY" -m shinobi
