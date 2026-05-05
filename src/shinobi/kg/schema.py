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

-- Phase B : sous-KG par PNJ (belief propagator)
-- Chaque ligne = "ce NPC croit que ce fact est vrai", avec une fidelity
-- qui se degrade par chaine de transmission. learned_at_year permet de
-- savoir DEPUIS QUAND le NPC connait le fait (pas avant).
CREATE TABLE IF NOT EXISTS kg_beliefs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fact_id INTEGER NOT NULL REFERENCES kg_facts(id) ON DELETE CASCADE,
    npc_id TEXT NOT NULL,
    fidelity REAL NOT NULL DEFAULT 1.0,
    learned_at_year INTEGER,
    learned_via_npc_id TEXT,           -- NPC source (None = temoin direct ou canon)
    learned_via_channel TEXT,          -- 'witness' / 'rumor' / 'spy' / 'canon_default'
    distortion_notes TEXT,             -- (optionnel) note sur la deformation
    created_at_ts INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
    UNIQUE(fact_id, npc_id)
);
CREATE INDEX IF NOT EXISTS idx_kg_beliefs_npc ON kg_beliefs(npc_id);
CREATE INDEX IF NOT EXISTS idx_kg_beliefs_fact ON kg_beliefs(fact_id);
CREATE INDEX IF NOT EXISTS idx_kg_beliefs_year ON kg_beliefs(learned_at_year);

-- Phase B : reseau social (graphe non oriente avec strength)
-- Une ligne par paire ordonnee (a, b) avec a < b par convention.
-- link_type : family / friend / mentor / student / rival / enemy / acquaintance / ally
-- strength : 0 (etranger) -> 1 (lien tres fort)
CREATE TABLE IF NOT EXISTS kg_social_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    npc_a TEXT NOT NULL,
    npc_b TEXT NOT NULL,
    link_type TEXT NOT NULL DEFAULT 'acquaintance',
    strength REAL NOT NULL DEFAULT 0.5,
    valid_from_year INTEGER,
    valid_to_year INTEGER,
    notes TEXT,
    created_at_ts INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
    UNIQUE(npc_a, npc_b, link_type, valid_from_year)
);
CREATE INDEX IF NOT EXISTS idx_kg_social_a ON kg_social_links(npc_a);
CREATE INDEX IF NOT EXISTS idx_kg_social_b ON kg_social_links(npc_b);
CREATE INDEX IF NOT EXISTS idx_kg_social_strength ON kg_social_links(strength);
"""


CURRENT_SCHEMA_VERSION = 2  # bump : ajout kg_beliefs + kg_social_links


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


def belief_columns() -> tuple[str, ...]:
    """Colonnes de kg_beliefs."""
    return (
        "id", "fact_id", "npc_id", "fidelity", "learned_at_year",
        "learned_via_npc_id", "learned_via_channel", "distortion_notes",
        "created_at_ts",
    )


def social_link_columns() -> tuple[str, ...]:
    """Colonnes de kg_social_links."""
    return (
        "id", "npc_a", "npc_b", "link_type", "strength",
        "valid_from_year", "valid_to_year", "notes", "created_at_ts",
    )


@dataclass
class Belief:
    """Croyance d'un NPC sur un fact, avec fidelity (precision de l'info)."""

    fact_id: int
    npc_id: str
    fidelity: float = 1.0  # 1.0 = certitude/temoin direct, 0 = inconnu
    learned_at_year: int | None = None
    learned_via_npc_id: str | None = None  # None = canon ou temoin direct
    learned_via_channel: str | None = None  # 'witness' | 'rumor' | 'spy' | 'canon_default'
    distortion_notes: str | None = None
    id: int | None = None
    created_at_ts: int | None = None

    def to_row(self) -> dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "npc_id": self.npc_id,
            "fidelity": self.fidelity,
            "learned_at_year": self.learned_at_year,
            "learned_via_npc_id": self.learned_via_npc_id,
            "learned_via_channel": self.learned_via_channel,
            "distortion_notes": self.distortion_notes,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row | dict[str, Any]) -> Belief:
        d = dict(row) if isinstance(row, sqlite3.Row) else row
        return cls(
            id=d.get("id"),
            fact_id=d["fact_id"],
            npc_id=d["npc_id"],
            fidelity=float(d.get("fidelity") or 1.0),
            learned_at_year=d.get("learned_at_year"),
            learned_via_npc_id=d.get("learned_via_npc_id"),
            learned_via_channel=d.get("learned_via_channel"),
            distortion_notes=d.get("distortion_notes"),
            created_at_ts=d.get("created_at_ts"),
        )


@dataclass
class SocialLink:
    """Lien social entre deux NPCs, oriente non (a < b par convention)."""

    npc_a: str
    npc_b: str
    link_type: str = "acquaintance"
    strength: float = 0.5  # 0..1, intensite du lien
    valid_from_year: int | None = None
    valid_to_year: int | None = None
    notes: str | None = None
    id: int | None = None
    created_at_ts: int | None = None

    def __post_init__(self) -> None:
        # Convention : on stocke toujours npc_a < npc_b lexicographiquement
        if self.npc_a > self.npc_b:
            self.npc_a, self.npc_b = self.npc_b, self.npc_a

    def to_row(self) -> dict[str, Any]:
        return {
            "npc_a": self.npc_a,
            "npc_b": self.npc_b,
            "link_type": self.link_type,
            "strength": self.strength,
            "valid_from_year": self.valid_from_year,
            "valid_to_year": self.valid_to_year,
            "notes": self.notes,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row | dict[str, Any]) -> SocialLink:
        d = dict(row) if isinstance(row, sqlite3.Row) else row
        link = cls(
            id=d.get("id"),
            npc_a=d["npc_a"],
            npc_b=d["npc_b"],
            link_type=d.get("link_type") or "acquaintance",
            strength=float(d.get("strength") or 0.5),
            valid_from_year=d.get("valid_from_year"),
            valid_to_year=d.get("valid_to_year"),
            notes=d.get("notes"),
            created_at_ts=d.get("created_at_ts"),
        )
        return link

    def other(self, npc_id: str) -> str:
        """Retourne l'autre NPC du lien."""
        if npc_id == self.npc_a:
            return self.npc_b
        if npc_id == self.npc_b:
            return self.npc_a
        raise ValueError(f"{npc_id} n'est pas dans ce lien ({self.npc_a}, {self.npc_b}).")


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
