"""Schema SQLite du Knowledge Graph dynamique.

Voir docs/02-PROJET-ROADMAP-SUITE.md §5.1 pour le schema cible.

Choix design :
- Une seule table `kg_facts` avec triplets (subject, relation, object)
- valid_from_year / valid_to_year (NULL = pas de borne) pour les facts datés
- source : 'canon' | 'event_<id>' | 'player_action_<id>' | 'inferred'
- canonicity : 'canon_strict' | 'canon_modified' | 'divergent'
- known_by_npc_ids : JSON array (TEXT) pour le belief propagator
- Index sur (subject, relation), (valid_from_year, valid_to_year)

Migrations futures : table `kg_schema_version` pour tracker.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class FactSource(StrEnum):
    """Provenance d'un fact dans le KG."""

    canon = "canon"
    event = "event"  # avec suffixe :<id>
    player_action = "player_action"  # avec suffixe :<id>
    inferred = "inferred"  # deduit par regle de drift / propagation


class Canonicity(StrEnum):
    """Position du fait par rapport au canon Naruto."""

    canon_strict = "canon_strict"  # textuellement attestable
    canon_modified = "canon_modified"  # canon modifie en cours de partie
    divergent = "divergent"  # nouveau fait absent du canon


class ObjectType(StrEnum):
    """Type de l'objet dans le triplet (pour le typage)."""

    entity = "entity"  # un autre id (character, location, etc.)
    value = "value"  # une valeur scalaire (string, int)
    belief = "belief"  # une croyance (peut etre fausse)


@dataclass
class Fact:
    """Triplet du KG avec metadata."""

    subject: str
    relation: str
    object: str | None = None
    object_type: ObjectType = ObjectType.value
    valid_from_year: int | None = None
    valid_to_year: int | None = None
    source: str = FactSource.canon.value
    confidence: float = 1.0
    canonicity: Canonicity = Canonicity.canon_strict
    known_by_npc_ids: list[str] = field(default_factory=list)
    id: int | None = None
    created_at_ts: int | None = None

    def to_row(self) -> dict[str, Any]:
        """Serialise pour insertion SQLite."""
        return {
            "subject": self.subject,
            "relation": self.relation,
            "object": self.object,
            "object_type": self.object_type.value if isinstance(self.object_type, ObjectType) else self.object_type,
            "valid_from_year": self.valid_from_year,
            "valid_to_year": self.valid_to_year,
            "source": self.source,
            "confidence": self.confidence,
            "canonicity": self.canonicity.value if isinstance(self.canonicity, Canonicity) else self.canonicity,
            "known_by_npc_ids": json.dumps(self.known_by_npc_ids),
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row | dict[str, Any]) -> Fact:
        """Reconstruit depuis une ligne SQLite."""
        d = dict(row) if isinstance(row, sqlite3.Row) else row
        known_raw = d.get("known_by_npc_ids") or "[]"
        try:
            known = json.loads(known_raw) if known_raw else []
        except json.JSONDecodeError:
            known = []
        return cls(
            id=d.get("id"),
            subject=d["subject"],
            relation=d["relation"],
            object=d.get("object"),
            object_type=ObjectType(d.get("object_type") or "value"),
            valid_from_year=d.get("valid_from_year"),
            valid_to_year=d.get("valid_to_year"),
            source=d.get("source") or FactSource.canon.value,
            confidence=float(d.get("confidence") or 1.0),
            canonicity=Canonicity(d.get("canonicity") or "canon_strict"),
            known_by_npc_ids=known,
            created_at_ts=d.get("created_at_ts"),
        )


KG_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS kg_schema_version (
    version INTEGER PRIMARY KEY,
    applied_at_ts INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS kg_facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject TEXT NOT NULL,
    relation TEXT NOT NULL,
    object TEXT,
    object_type TEXT NOT NULL DEFAULT 'value',
    valid_from_year INTEGER,
    valid_to_year INTEGER,
    source TEXT NOT NULL DEFAULT 'canon',
    confidence REAL NOT NULL DEFAULT 1.0,
    canonicity TEXT NOT NULL DEFAULT 'canon_strict',
    known_by_npc_ids TEXT NOT NULL DEFAULT '[]',
    created_at_ts INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_kg_subject ON kg_facts(subject, relation);
CREATE INDEX IF NOT EXISTS idx_kg_object ON kg_facts(object);
CREATE INDEX IF NOT EXISTS idx_kg_valid ON kg_facts(valid_from_year, valid_to_year);
CREATE INDEX IF NOT EXISTS idx_kg_canonicity ON kg_facts(canonicity);
"""


CURRENT_SCHEMA_VERSION = 1


def initialize_db(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Cree la base et applique le schema. Retourne la connexion ouverte.

    Si db_path est None, utilise une base in-memory (pour les tests).
    Sinon, cree le repertoire parent si necessaire.
    """
    if db_path is None:
        conn = sqlite3.connect(":memory:")
    else:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(KG_SCHEMA_SQL)
    # Enregistre la version courante si vide
    cur = conn.execute("SELECT MAX(version) AS v FROM kg_schema_version")
    row = cur.fetchone()
    if row is None or row["v"] is None:
        conn.execute(
            "INSERT INTO kg_schema_version (version, applied_at_ts) "
            "VALUES (?, strftime('%s', 'now'))",
            (CURRENT_SCHEMA_VERSION,),
        )
    conn.commit()
    return conn


def schema_version(conn: sqlite3.Connection) -> int:
    """Retourne la version max appliquee dans la base."""
    row = conn.execute("SELECT MAX(version) AS v FROM kg_schema_version").fetchone()
    if row is None or row["v"] is None:
        return 0
    return int(row["v"])


def fact_columns() -> tuple[str, ...]:
    """Liste des colonnes du Fact pour les requetes SELECT."""
    return (
        "id", "subject", "relation", "object", "object_type",
        "valid_from_year", "valid_to_year", "source", "confidence",
        "canonicity", "known_by_npc_ids", "created_at_ts",
    )


def insert_facts_batch(conn: sqlite3.Connection, facts: Iterable[Fact]) -> list[int]:
    """Insert un batch de facts dans une transaction. Retourne les ids inserts."""
    rows = [f.to_row() for f in facts]
    if not rows:
        return []
    sql = (
        "INSERT INTO kg_facts "
        "(subject, relation, object, object_type, valid_from_year, valid_to_year, "
        "source, confidence, canonicity, known_by_npc_ids) "
        "VALUES (:subject, :relation, :object, :object_type, :valid_from_year, "
        ":valid_to_year, :source, :confidence, :canonicity, :known_by_npc_ids)"
    )
    cur = conn.cursor()
    ids: list[int] = []
    for row in rows:
        cur.execute(sql, row)
        ids.append(int(cur.lastrowid))
    conn.commit()
    return ids
