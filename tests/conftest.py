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
