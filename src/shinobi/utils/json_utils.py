"""Chargement JSON strict avec messages d'erreur clairs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from shinobi.errors import CanonLoadError


def load_json(path: str | Path) -> Any:
    """Charge un fichier JSON UTF-8 et retourne sa structure."""
    p = Path(path)
    if not p.exists():
        raise CanonLoadError(f"Fichier introuvable: {p}")
    try:
        with p.open(encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        raise CanonLoadError(f"JSON invalide dans {p}: {exc}") from exc


def dump_json(data: Any, path: str | Path, *, indent: int = 2, sort_keys: bool = True) -> None:
    """Ecrit un fichier JSON UTF-8 indente."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent, sort_keys=sort_keys)
        f.write("\n")
