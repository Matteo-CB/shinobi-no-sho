#!/usr/bin/env bash
# Phase i18n.12 : installe le hook git pre-commit qui lance i18n_lint.
#
# Usage :
#     bash scripts/install-precommit-hook.sh
#
# Apres l'install, chaque `git commit` lancera automatiquement
# `python scripts/i18n_lint.py --quiet`. Si le lint echoue, le commit
# est refuse. Override possible (a eviter) via `git commit --no-verify`.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOOK_PATH="${REPO_ROOT}/.git/hooks/pre-commit"

cat > "${HOOK_PATH}" << 'EOF'
#!/usr/bin/env bash
# Pre-commit hook : i18n catalog parity check.
# Genere par scripts/install-precommit-hook.sh (Phase i18n.12).
set -e

REPO_ROOT="$(git rev-parse --show-toplevel)"
PY="${REPO_ROOT}/.venv/Scripts/python.exe"
if [ ! -x "${PY}" ]; then
    PY="${REPO_ROOT}/.venv/bin/python"
fi
if [ ! -x "${PY}" ]; then
    PY="python"
fi

# 1. i18n_lint : parite catalog
"${PY}" "${REPO_ROOT}/scripts/i18n_lint.py" --quiet
if [ $? -ne 0 ]; then
    echo ""
    echo "[pre-commit] i18n_lint a echoue. Run :"
    echo "    python scripts/i18n_lint.py"
    echo "  pour voir les divergences. Pour ignorer (a eviter) :"
    echo "    git commit --no-verify"
    exit 1
fi

# 2. i18n_extract_new : detecter nouvelles cles `t("...")` orphelines
"${PY}" "${REPO_ROOT}/scripts/i18n_extract_new_strings.py" --quiet
if [ $? -ne 0 ]; then
    echo ""
    echo "[pre-commit] nouvelles cles i18n detectees. Run :"
    echo "    python scripts/i18n_extract_new_strings.py"
    echo "  pour voir la liste, puis :"
    echo "    python scripts/i18n_translate_new.py"
    echo "  pour les traduire."
    exit 1
fi

exit 0
EOF

chmod +x "${HOOK_PATH}"
echo "[install-precommit] Hook installe : ${HOOK_PATH}"
echo "[install-precommit] Test : bash ${HOOK_PATH}"
