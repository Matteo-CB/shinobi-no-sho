"""Fixtures pytest partagees."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture(scope="session")
def project_root() -> Path:
    """Racine du projet."""
    return ROOT


@pytest.fixture()
def temp_project_dir(tmp_path: Path) -> Iterator[Path]:
    """Repertoire temporaire pour tests d'IO."""
    yield tmp_path


# Phase i18n.11 : fixture parametrisee sur les 8 langues supportees.
# Tout test qui consomme `lang` est automatiquement instancie 8 fois (une
# par langue) et set la langue active du processus pour la duree du test.
# Reset a EN apres chaque test (idempotence inter-test).
@pytest.fixture(params=["en", "fr", "es", "ja", "zh", "ko", "pt-BR", "de"])
def lang(request: pytest.FixtureRequest) -> Iterator[str]:
    """Parametrise un test sur les 8 langues supportees.

    Set `_ACTIVE_LANGUAGE` (catalog.py) au code lang pendant le test.
    Apres le test : retour a EN (le defaut serveur).
    """
    from shinobi.i18n.catalog import (
        get_active_language,
        set_active_language,
    )

    previous = get_active_language()
    set_active_language(request.param)
    try:
        yield request.param
    finally:
        set_active_language(previous if previous else "en")
