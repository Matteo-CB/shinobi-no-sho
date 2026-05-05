#!/usr/bin/env bash
# Bootstrap complet de Shinobi no Sho sur Linux/macOS.
# Idempotent : re-executable en toute securite.
#
# Usage : ./scripts/setup.sh
#         ./scripts/setup.sh --skip-model
#         ./scripts/setup.sh --skip-llama
#         ./scripts/setup.sh --git-remote https://github.com/<user>/shinobi-no-sho.git

set -euo pipefail

SKIP_MODEL=0
SKIP_LLAMA=0
GIT_REMOTE=""
LLAMA_DIR="${HOME}/llama.cpp"
MODEL_URL="https://huggingface.co/unsloth/Qwen3-8B-GGUF/resolve/main/Qwen3-8B-UD-Q5_K_XL.gguf"
MODEL_FILE="Qwen3-8B-UD-Q5_K_XL.gguf"

while [[ $# -gt 0 ]]; do
    case $1 in
        --skip-model) SKIP_MODEL=1; shift ;;
        --skip-llama) SKIP_LLAMA=1; shift ;;
        --git-remote) GIT_REMOTE="$2"; shift 2 ;;
        *) echo "Option inconnue: $1"; exit 1 ;;
    esac
done

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

cyan() { printf "\033[1;36m%s\033[0m\n" "$1"; }
green() { printf "\033[0;32m  [OK] %s\033[0m\n" "$1"; }
yellow() { printf "\033[1;33m  [!!] %s\033[0m\n" "$1"; }
gray() { printf "\033[0;90m  [..] %s\033[0m\n" "$1"; }

step() { echo ""; cyan "==> $1"; }

step "Verification de Python"
if ! command -v python3 >/dev/null 2>&1; then
    yellow "python3 introuvable. Installe Python 3.11+."
    exit 1
fi
green "$(python3 --version)"

step "Environnement virtuel"
if [[ ! -f .venv/bin/python ]]; then
    python3 -m venv .venv
    green ".venv cree"
else
    gray ".venv deja present"
fi
VENV_PY="./.venv/bin/python"

step "Installation des dependances + projet editable"
$VENV_PY -m pip install --upgrade pip --quiet
$VENV_PY -m pip install --quiet \
    "fastapi>=0.115" "uvicorn[standard]>=0.30" "pydantic>=2.8" "pydantic-settings>=2.4" \
    "sqlalchemy>=2.0" "alembic>=1.13" "chromadb>=0.5" "sentence-transformers>=3.0" \
    "httpx>=0.27" "structlog>=24.0" "rich>=13.7" "typer>=0.12" \
    "beautifulsoup4>=4.12" "trafilatura>=1.12" \
    "pytest>=8.3" "pytest-asyncio>=0.24" "pytest-cov>=5.0" "ruff>=0.6" "mypy>=1.11" "hypothesis>=6.100"
$VENV_PY -m pip install --quiet -e .
green "Dependances + commande shinobi disponibles"

if [[ $SKIP_LLAMA -eq 0 ]]; then
    step "llama.cpp"
    if [[ -x "${LLAMA_DIR}/llama-server" ]]; then
        gray "llama-server deja present a ${LLAMA_DIR}"
    else
        yellow "llama.cpp non installe. Installation manuelle recommandee :"
        echo "  - Arch : sudo pacman -S llama.cpp"
        echo "  - Source : https://github.com/ggml-org/llama.cpp"
    fi
fi

if [[ $SKIP_MODEL -eq 0 ]]; then
    step "Modele LLM Qwen3-8B"
    mkdir -p models/llm
    if [[ -f "models/llm/${MODEL_FILE}" ]]; then
        gray "Modele deja present ($(du -h "models/llm/${MODEL_FILE}" | cut -f1))"
    else
        echo "  Telechargement (~5.5 Go)..."
        curl -L --fail --retry 3 -o "models/llm/${MODEL_FILE}" "$MODEL_URL"
        green "Modele telecharge"
    fi
fi

step "Fichier .env"
if [[ ! -f .env && -f .env.example ]]; then
    cp .env.example .env
    green ".env cree depuis .env.example"
else
    gray ".env deja present (ou .env.example absent)"
fi

step "Configuration git"
if ! command -v git >/dev/null 2>&1; then
    yellow "git introuvable, etape sautee"
else
    if [[ ! -d .git ]]; then
        git init -b main >/dev/null 2>&1
        green "git init main"
    else
        gray "repo git deja initialise"
    fi
    if [[ -z "$(git config --global user.name)" ]]; then
        read -p "  Ton nom git [Matteo] : " name
        name="${name:-Matteo}"
        git config --global user.name "$name"
        green "git user.name = $name"
    else
        gray "git user.name = $(git config --global user.name)"
    fi
    if [[ -z "$(git config --global user.email)" ]]; then
        read -p "  Ton email git : " email
        git config --global user.email "$email"
        green "git user.email = $email"
    else
        gray "git user.email = $(git config --global user.email)"
    fi
    if [[ -n "$GIT_REMOTE" ]]; then
        git remote remove origin >/dev/null 2>&1 || true
        git remote add origin "$GIT_REMOTE"
        green "remote origin = $GIT_REMOTE"
    fi
    if ! git rev-parse --verify HEAD >/dev/null 2>&1; then
        git add CLAUDE.md README.md TUTORIAL.md .env.example .gitignore pyproject.toml \
                ruff.toml mypy.ini docs/ scripts/ src/ tests/ data/canonical/ 2>/dev/null || true
        git -c commit.gpgsign=false commit -m "initial bootstrap" >/dev/null
        green "commit initial cree"
    fi
fi

step "Tests de fumee"
$VENV_PY -m pytest tests/ -q | tail -3 || yellow "Tests en echec"

echo ""
echo "==============================================="
echo "  Setup termine. Pour jouer :"
echo "==============================================="
echo ""
echo "  1. Demarrer llama-server (autre terminal) :"
echo "       llama-server -m models/llm/${MODEL_FILE} -ngl 99 -c 16384 --port 8080 --jinja"
echo ""
echo "  2. Activer le venv et lancer :"
echo "       source .venv/bin/activate && shinobi"
