"""Connexion SQLite par save."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from shinobi.logging_setup import get_logger

logger = get_logger(__name__)

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def open_connection(state_path: Path) -> sqlite3.Connection:
    """Ouvre une connexion et applique le schema si necessaire.

    Phase 4.2 : applique aussi les migrations Alembic pendantes pour les
    saves existantes (idempotent : si deja a HEAD, no-op). Les nouvelles
    saves sont stampees a HEAD pour eviter qu'Alembic re-applique la
    baseline (qui est deja-applique via schema.sql executescript).
    """
    state_path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not state_path.exists()
    conn = sqlite3.connect(state_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    apply_schema(conn)
    # Phase 4.2 : ensure alembic_version table exists et est a HEAD pour les
    # nouvelles saves. Pour les anciennes saves, upgrade tente de jouer les
    # migrations pendantes (no-op si deja a HEAD).
    try:
        # Defensive : import lazy pour eviter cyclic dep + permettre l'usage
        # de open_connection meme si Alembic non installe (legacy fallback).
        from shinobi.persistence.migrations_helper import (
            current_revision,
            stamp_save,
            upgrade_save,
        )
        rev = current_revision(state_path=state_path)
        if rev is None:
            # Save sans alembic_version table : on stamp HEAD (le schema
            # vient d'etre applique via schema.sql, qui est equivalent a la
            # baseline 0001_initial_schema).
            stamp_save(state_path=state_path, revision="head")
        elif not is_new:
            # Save existante stampee : tente upgrade -> head. Idempotent.
            upgrade_save(state_path=state_path, revision="head")
    except Exception as exc:  # noqa: BLE001
        # Defensive : si Alembic crash (ex absence du package en dev), on
        # log mais on continue - schema.sql a deja applique les tables.
        logger.warning(
            "alembic_stamp_or_upgrade_failed",
            state_path=str(state_path),
            error=type(exc).__name__,
            msg=str(exc)[:200],
        )
    return conn


def apply_schema(conn: sqlite3.Connection) -> None:
    """Cree les tables si elles n'existent pas."""
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(sql)
    conn.commit()


def close(conn: sqlite3.Connection) -> None:
    conn.close()
