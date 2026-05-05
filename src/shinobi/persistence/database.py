"""Connexion SQLite par save."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def open_connection(state_path: Path) -> sqlite3.Connection:
    """Ouvre une connexion et applique le schema si necessaire."""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(state_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    apply_schema(conn)
    return conn


def apply_schema(conn: sqlite3.Connection) -> None:
    """Cree les tables si elles n'existent pas."""
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(sql)
    conn.commit()


def close(conn: sqlite3.Connection) -> None:
    conn.close()
