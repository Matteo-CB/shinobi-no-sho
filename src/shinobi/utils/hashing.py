"""Hashes deterministes pour cache et integrite."""

from __future__ import annotations

import hashlib
from pathlib import Path


def sha256_text(text: str) -> str:
    """Hash SHA-256 hex d'une chaine UTF-8."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: str | Path) -> str:
    """Hash SHA-256 hex du contenu d'un fichier."""
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def short_hash(text: str, length: int = 12) -> str:
    """Hash court pour clefs de cache."""
    return sha256_text(text)[:length]
