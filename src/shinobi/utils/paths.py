"""Resolution de chemins relatifs au projet."""

from __future__ import annotations

from pathlib import Path

from shinobi.config import PROJECT_ROOT


def project_path(relative: str | Path) -> Path:
    """Retourne un chemin absolu depuis la racine du projet."""
    p = Path(relative)
    if p.is_absolute():
        return p
    return (PROJECT_ROOT / p).resolve()


def ensure_dir(path: str | Path) -> Path:
    """Cree le repertoire si necessaire et retourne son chemin absolu."""
    p = project_path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p
