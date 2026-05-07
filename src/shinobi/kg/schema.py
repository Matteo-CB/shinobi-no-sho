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
        """Reconstruit depuis une ligne SQLite.

        Spec Phase A : confidence=0.0 est une valeur LEGITIME (rumeur
        totalement incertaine, ou information douteuse). On utilise
        d.get(key, default) au lieu de `d.get(key) or default` pour ne pas
        transformer 0.0 en 1.0 (bug de serialization round 18).
        """
        d = dict(row) if isinstance(row, sqlite3.Row) else row
        known_raw = d.get("known_by_npc_ids") or "[]"
        try:
            known = json.loads(known_raw) if known_raw else []
        except json.JSONDecodeError:
            known = []
        # Defensive : known_by_npc_ids DOIT etre list[str]. Si la DB contient
        # un dict/scalar/nombre (corruption ou import externe), forcer list
        # vide plutot que de propager un mauvais type.
        if not isinstance(known, list):
            known = []
        else:
            # Filtre toute entree non-string (defensive)
            known = [str(x) for x in known if isinstance(x, str)]
        confidence_raw = d.get("confidence")
        if confidence_raw is None:
            confidence_raw = 1.0
        return cls(
            id=d.get("id"),
            subject=d["subject"],
            relation=d["relation"],
            object=d.get("object"),
            object_type=ObjectType(d.get("object_type") or "value"),
            valid_from_year=d.get("valid_from_year"),
            valid_to_year=d.get("valid_to_year"),
            source=d.get("source") or FactSource.canon.value,
            confidence=float(confidence_raw),
            canonicity=Canonicity(d.get("canonicity") or "canon_strict"),
            known_by_npc_ids=known,
            created_at_ts=d.get("created_at_ts"),
        )


# --- Migration v1 : Phase A pure (kg_facts uniquement) -----------------------
_KG_SCHEMA_V1_PHASE_A = """
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

# --- Migration v2 : Phase B (kg_beliefs + kg_social_links) ------------------
_KG_SCHEMA_V2_PHASE_B = """
-- Phase B : sous-KG par PNJ (belief propagator)
CREATE TABLE IF NOT EXISTS kg_beliefs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fact_id INTEGER NOT NULL REFERENCES kg_facts(id) ON DELETE CASCADE,
    npc_id TEXT NOT NULL,
    fidelity REAL NOT NULL DEFAULT 1.0,
    learned_at_year INTEGER,
    learned_via_npc_id TEXT,
    learned_via_channel TEXT,
    distortion_notes TEXT,
    created_at_ts INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
    UNIQUE(fact_id, npc_id)
);
CREATE INDEX IF NOT EXISTS idx_kg_beliefs_npc ON kg_beliefs(npc_id);
CREATE INDEX IF NOT EXISTS idx_kg_beliefs_fact ON kg_beliefs(fact_id);
CREATE INDEX IF NOT EXISTS idx_kg_beliefs_year ON kg_beliefs(learned_at_year);

-- Phase B : reseau social (graphe non oriente avec strength)
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

# Backward-compat : KG_SCHEMA_SQL = somme de toutes les migrations (pour
# code legacy qui ferait `conn.executescript(KG_SCHEMA_SQL)` directement).
KG_SCHEMA_SQL = _KG_SCHEMA_V1_PHASE_A + "\n" + _KG_SCHEMA_V2_PHASE_B + """
-- Spec doc 02 §5.1 : index sur known_by_npc_ids pour requetes sub-KG (v3)
CREATE INDEX IF NOT EXISTS idx_kg_known_by ON kg_facts(known_by_npc_ids);
-- Index sur source pour queries source_prefix (v4)
CREATE INDEX IF NOT EXISTS idx_kg_source ON kg_facts(source);
"""


# Migrations incrementales : (version, sql). Appliquees dans l'ordre si la
# version courante de la base est strictement inferieure. Spec Phase A :
# "Schema SQLite, migrations". Toute modif schema future = nouvelle entree ici.
_MIGRATIONS: list[tuple[int, str]] = [
    # v1 : Phase A pure (kg_facts + indexes spec §5.1)
    (1, _KG_SCHEMA_V1_PHASE_A),
    # v2 : Phase B (kg_beliefs + kg_social_links)
    (2, _KG_SCHEMA_V2_PHASE_B),
    # v3 : index sur known_by_npc_ids (spec doc 02 §5.1 ligne 179)
    (3, "CREATE INDEX IF NOT EXISTS idx_kg_known_by ON kg_facts(known_by_npc_ids);"),
    # v4 : index sur source pour queries source_prefix
    # (canon, mission:<id>, event:<id>, player_action:<id>, inferred)
    (4, "CREATE INDEX IF NOT EXISTS idx_kg_source ON kg_facts(source);"),
]

CURRENT_SCHEMA_VERSION = 4  # v4 : ajout idx_kg_source pour source_prefix queries


def initialize_db(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Cree la base et applique le schema. Retourne la connexion ouverte.

    Si db_path est None, utilise une base in-memory (pour les tests).
    Sinon, cree le repertoire parent si necessaire.

    Applique les migrations en attente (version courante -> CURRENT_SCHEMA_VERSION)
    en mode incremental : seules les migrations strictement superieures a la
    version stockee sont executees.

    Spec Phase A : active PRAGMA foreign_keys=ON (sinon ON DELETE CASCADE
    declare sur kg_beliefs ne fonctionne pas - bug silencieux SQLite).
    """
    if db_path is None:
        conn = sqlite3.connect(":memory:")
    else:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    # PRAGMA OFF par defaut sur SQLite. Sans cette ligne, ON DELETE CASCADE
    # declare dans le schema ne fait rien (orphelins en kg_beliefs).
    conn.execute("PRAGMA foreign_keys = ON")
    # Mode WAL : permet readers concurrents pendant que le writer ecrit.
    # Critique pour la simulation multi-agent (Phase E) qui aura plusieurs
    # threads de lecture du KG. WAL ne fonctionne pas en :memory: -> skip.
    if db_path is not None:
        conn.execute("PRAGMA journal_mode = WAL")
        # synchronous=NORMAL est sur en WAL et nettement plus rapide que FULL.
        conn.execute("PRAGMA synchronous = NORMAL")
    apply_migrations(conn)
    return conn


def apply_migrations(conn: sqlite3.Connection) -> list[int]:
    """Applique les migrations en attente. Retourne la liste des versions appliquees.

    Approche : on s'assure d'abord que kg_schema_version existe (bootstrap),
    on lit la version max, on applique chaque migration > version stockee.

    Spec Phase A round 33 : detecte le scenario "DB plus recente que code"
    (downgrade) et warn -> evite les bugs silencieux quand on lit une DB
    avec colonnes/tables inconnues du code courant.
    """
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS kg_schema_version ("
        "version INTEGER PRIMARY KEY, "
        "applied_at_ts INTEGER NOT NULL"
        ");"
    )
    current = schema_version(conn)
    if current > CURRENT_SCHEMA_VERSION:
        # DB plus recente que code -> probable downgrade. Pas d'erreur dure
        # (le code doit pouvoir lire les anciennes colonnes), mais log clair
        # pour faciliter le debug.
        from shinobi.logging_setup import get_logger
        get_logger(__name__).warning(
            "kg_schema_db_newer_than_code",
            db_version=current,
            code_version=CURRENT_SCHEMA_VERSION,
            note=(
                "La DB a une version plus recente que le code. Verifier "
                "que toutes les colonnes/tables attendues sont accessibles. "
                "Possible scenario : code revert apres migration."
            ),
        )
    applied: list[int] = []
    for version, sql in _MIGRATIONS:
        if version > current:
            conn.executescript(sql)
            conn.execute(
                "INSERT OR IGNORE INTO kg_schema_version "
                "(version, applied_at_ts) VALUES (?, strftime('%s', 'now'))",
                (version,),
            )
            applied.append(version)
    conn.commit()
    return applied


def schema_version(conn: sqlite3.Connection) -> int:
    """Retourne la version max appliquee dans la base."""
    try:
        row = conn.execute(
            "SELECT MAX(version) AS v FROM kg_schema_version"
        ).fetchone()
    except sqlite3.OperationalError:
        return 0
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
        """Spec Phase A : fidelity=0.0 LEGITIME (info totalement deformee)."""
        d = dict(row) if isinstance(row, sqlite3.Row) else row
        fidelity_raw = d.get("fidelity")
        if fidelity_raw is None:
            fidelity_raw = 1.0
        return cls(
            id=d.get("id"),
            fact_id=d["fact_id"],
            npc_id=d["npc_id"],
            fidelity=float(fidelity_raw),
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
        """Spec Phase A : strength=0.0 LEGITIME (lien rompu)."""
        d = dict(row) if isinstance(row, sqlite3.Row) else row
        strength_raw = d.get("strength")
        if strength_raw is None:
            strength_raw = 0.5
        link = cls(
            id=d.get("id"),
            npc_a=d["npc_a"],
            npc_b=d["npc_b"],
            link_type=d.get("link_type") or "acquaintance",
            strength=float(strength_raw),
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


def execute_dml(
    conn: sqlite3.Connection, sql: str, params: list | tuple = (),
) -> sqlite3.Cursor:
    """Helper module-niveau : execute DML et commit uniquement si pas dans
    une transaction utilisateur.

    Spec Phase A : permet aux classes BeliefPropagator / SocialNetwork (qui
    partagent la connection avec KnowledgeGraphStore) de respecter les
    transactions atomiques utilisateur. Sans ce helper, leur conn.commit()
    direct casserait le rollback dans `with store.transaction():`.
    """
    in_user_tx = conn.in_transaction
    cur = conn.execute(sql, params)
    if not in_user_tx:
        conn.commit()
    return cur


_CANONICITY_RUNTIME_MAP: dict[str, Canonicity] = {
    "manga": Canonicity.canon_strict,
    "boruto_manga": Canonicity.canon_strict,
    "boruto": Canonicity.canon_strict,
    "anime_canon": Canonicity.canon_strict,
    "movie_canon": Canonicity.canon_strict,
    "databook": Canonicity.canon_strict,
    "filler": Canonicity.canon_modified,
    "game": Canonicity.canon_modified,
    "tbv": Canonicity.canon_modified,
}


def map_source_canonicity(raw: Any) -> Canonicity:
    """Mappe une valeur source canon (manga/filler/boruto/...) -> Canonicity.

    Spec Phase A : helper public partage entre tous les pipelines d'import
    (canon standard via _import_list, Sprint MISSIONS, etc.) pour garantir
    la coherence du Fact.canonicity attribute.
    """
    if raw is None:
        return Canonicity.canon_strict
    return _CANONICITY_RUNTIME_MAP.get(str(raw).lower(), Canonicity.canon_strict)


def insert_facts_batch(conn: sqlite3.Connection, facts: Iterable[Fact]) -> list[int]:
    """Insert un batch de facts dans une transaction. Retourne les ids inserts.

    Spec Phase A : transaction explicite pour atomicite + perf. Sans BEGIN
    explicite, chaque INSERT est son propre commit (~41000 commits a l'import
    canon = lent + non-atomique).

    Si conn.in_transaction (deja dans un transaction context appele par
    l'utilisateur via store.transaction()), on n'ouvre PAS une nouvelle
    transaction : on insere dans la transaction parente. C'est la transaction
    parente qui decide commit/rollback. Sinon, on gere notre propre BEGIN/
    COMMIT/ROLLBACK pour atomicite et perf.
    """
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
    own_transaction = not conn.in_transaction
    try:
        if own_transaction:
            cur.execute("BEGIN")
        for row in rows:
            cur.execute(sql, row)
            ids.append(int(cur.lastrowid))
        if own_transaction:
            conn.commit()
    except Exception:
        if own_transaction:
            conn.rollback()
        raise
    return ids
