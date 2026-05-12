"""Phase i18n.12 : tests des outils de maintenance i18n.

Verifie que :
1. `scripts/i18n_lint.py` retourne 0 (crit. de sortie spec).
2. `scripts/i18n_extract_new_strings.py` retourne 0 (aucune cle orpheline).
3. Le hook pre-commit `.git/hooks/pre-commit` existe + est executable.
4. Le doc `docs/15_i18n_maintenance.md` existe + est non vide.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_i18n_lint_exits_clean() -> None:
    """Critere de sortie spec : `python scripts/i18n_lint.py` retourne 0."""
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "i18n_lint.py"), "--quiet"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, (
        f"i18n_lint failed (exit={result.returncode})\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_i18n_extract_new_strings_finds_no_orphan() -> None:
    """L'extraction ne doit detecter aucune nouvelle cle orpheline (sinon
    quelqu'un a ajoute `t('xxx')` sans le mettre dans en.json)."""
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "i18n_extract_new_strings.py"),
            "--quiet",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, (
        f"i18n_extract_new_strings found orphan keys (exit={result.returncode})\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_i18n_translate_new_dry_run_runs() -> None:
    """Le script translate_new doit etre invocable en --dry-run sans erreur,
    meme sans cle a traiter."""
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "i18n_translate_new.py"),
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, (
        f"i18n_translate_new --dry-run failed: {result.stderr}"
    )


def test_precommit_hook_installed() -> None:
    """Le hook pre-commit est installe et executable (apres run de
    `scripts/install-precommit-hook.sh`).

    Si non installe (env de CI propre), le test skip.
    """
    import pytest

    hook = REPO_ROOT / ".git" / "hooks" / "pre-commit"
    if not hook.exists():
        pytest.skip("pre-commit hook non installe (run scripts/install-precommit-hook.sh)")
    text = hook.read_text(encoding="utf-8")
    assert "i18n_lint.py" in text, "hook pre-commit ne reference pas i18n_lint"
    assert "i18n_extract_new_strings.py" in text, (
        "hook pre-commit ne reference pas i18n_extract_new_strings"
    )


def test_pre_commit_config_yaml_exists() -> None:
    """Le fichier `.pre-commit-config.yaml` decrit les hooks pour
    l'outil pre-commit (alternatif au hook git natif)."""
    cfg = REPO_ROOT / ".pre-commit-config.yaml"
    assert cfg.exists(), "fichier .pre-commit-config.yaml manquant"
    text = cfg.read_text(encoding="utf-8")
    assert "i18n-lint" in text
    assert "i18n-extract-new" in text


def test_i18n_maintenance_doc_exists() -> None:
    """Le doc maintenance est livre et non vide."""
    doc = REPO_ROOT / "docs" / "15_i18n_maintenance.md"
    assert doc.exists()
    text = doc.read_text(encoding="utf-8")
    assert len(text) > 1000, "doc maintenance trop court"
    # Mentionne les 3 scripts livres
    assert "i18n_lint.py" in text
    assert "i18n_extract_new_strings.py" in text
    assert "i18n_translate_new.py" in text
