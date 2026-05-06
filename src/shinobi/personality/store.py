"""Persistence SQLite des `NPCPersonality` per-save.

Schema :

```
CREATE TABLE npc_personalities (
    npc_id TEXT PRIMARY KEY,
    vector_json TEXT NOT NULL,            -- JSON {dim: float}
    canon_baseline_json TEXT NOT NULL,    -- JSON {dim: float}
    baseline_year INTEGER,
    updated_at_ts REAL NOT NULL
);

CREATE TABLE personality_drift_history (
    id TEXT PRIMARY KEY,                   -- drift_<uuid>
    npc_id TEXT NOT NULL,
    rule_name TEXT NOT NULL,
    event_category TEXT NOT NULL,
    year INTEGER NOT NULL,
    delta_json TEXT NOT NULL,
    applied_delta_json TEXT NOT NULL,
    related_npc_id TEXT,
    related_event_id TEXT,
    related_mission_id TEXT,
    applied_at_ts REAL NOT NULL,
    notes TEXT,
    FOREIGN KEY (npc_id) REFERENCES npc_personalities(npc_id)
);

CREATE INDEX idx_drift_npc ON personality_drift_history(npc_id, year);
```

Choix : per-save (pas global) parce qu'un univers divergent peut faire que
Sasuke a des baselines extremement differents en branche A vs B.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

from shinobi.personality.dimensions import (
    ALL_DIMENSIONS,
    PersonalityDimension,
)
from shinobi.personality.types import (
    EventCategory,
    NPCPersonality,
    PersonalityDrift,
)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS npc_personalities (
    npc_id TEXT PRIMARY KEY,
    vector_json TEXT NOT NULL,
    canon_baseline_json TEXT NOT NULL,
    baseline_year INTEGER,
    updated_at_ts REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS personality_drift_history (
    id TEXT PRIMARY KEY,
    npc_id TEXT NOT NULL,
    rule_name TEXT NOT NULL,
    event_category TEXT NOT NULL,
    year INTEGER NOT NULL,
    delta_json TEXT NOT NULL,
    applied_delta_json TEXT NOT NULL,
    related_npc_id TEXT,
    related_event_id TEXT,
    related_mission_id TEXT,
    applied_at_ts REAL NOT NULL,
    notes TEXT,
    FOREIGN KEY (npc_id) REFERENCES npc_personalities(npc_id)
);

CREATE INDEX IF NOT EXISTS idx_drift_npc
ON personality_drift_history(npc_id, year);
"""


def _initialize_db(db_path: Path | str | None) -> sqlite3.Connection:
    """Ouvre la connexion et applique le schema si necessaire."""
    if db_path is None:
        conn = sqlite3.connect(":memory:")
    else:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA_SQL)
    conn.commit()
    return conn


def _vector_to_json(vector: dict[PersonalityDimension, float]) -> str:
    """Serialise vector en JSON deterministe (cle = nom string)."""
    return json.dumps(
        {dim.value: vector[dim] for dim in ALL_DIMENSIONS},
        ensure_ascii=False, sort_keys=True,
    )


def _vector_from_json(payload: str) -> dict[PersonalityDimension, float]:
    """Deserialise vector. Si une dimension manque, fallback a 0.5."""
    raw = json.loads(payload)
    out: dict[PersonalityDimension, float] = {}
    for dim in ALL_DIMENSIONS:
        v = raw.get(dim.value, 0.5)
        out[dim] = float(v)
    return out


class PersonalityStore:
    """CRUD des `NPCPersonality` et `PersonalityDrift` sur une SQLite par save.

    Usage :

    ```python
    with PersonalityStore("path/to/save/personality.sqlite") as store:
        store.upsert_personality(personality)
        loaded = store.get_personality("uchiha_sasuke")
    ```

    Le store NE consomme PAS l'engine : il ne fait que persister/restaurer.
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = _initialize_db(db_path)

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("PersonalityStore is closed.")
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> PersonalityStore:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self.conn
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    # --- personality CRUD --------------------------------------------------

    def upsert_personality(self, personality: NPCPersonality) -> None:
        """Insert ou replace le vecteur courant. NE persiste PAS l'history."""
        self.conn.execute(
            """
            INSERT INTO npc_personalities (
                npc_id, vector_json, canon_baseline_json, baseline_year, updated_at_ts
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(npc_id) DO UPDATE SET
                vector_json = excluded.vector_json,
                canon_baseline_json = excluded.canon_baseline_json,
                baseline_year = excluded.baseline_year,
                updated_at_ts = excluded.updated_at_ts
            """,
            (
                personality.npc_id,
                _vector_to_json(personality.vector),
                _vector_to_json(personality.canon_baseline),
                personality.baseline_year,
                time.time(),
            ),
        )
        self.conn.commit()

    def upsert_personalities(self, personalities: Iterable[NPCPersonality]) -> int:
        """Bulk upsert. Retourne nb d'entries traitees."""
        n = 0
        for p in personalities:
            self.upsert_personality(p)
            n += 1
        return n

    def get_personality(self, npc_id: str) -> NPCPersonality | None:
        """Charge le vecteur + baseline + history pour un npc_id."""
        row = self.conn.execute(
            "SELECT * FROM npc_personalities WHERE npc_id = ?", (npc_id,),
        ).fetchone()
        if row is None:
            return None
        history = self.list_drift_history(npc_id)
        return NPCPersonality(
            npc_id=row["npc_id"],
            vector=_vector_from_json(row["vector_json"]),
            canon_baseline=_vector_from_json(row["canon_baseline_json"]),
            drift_history=tuple(history),
            baseline_year=row["baseline_year"],
        )

    def list_personalities(self) -> list[NPCPersonality]:
        """Tous les NPCs ayant une entree (avec leurs histories)."""
        rows = self.conn.execute(
            "SELECT npc_id FROM npc_personalities ORDER BY npc_id",
        ).fetchall()
        out: list[NPCPersonality] = []
        for row in rows:
            p = self.get_personality(row["npc_id"])
            if p is not None:
                out.append(p)
        return out

    def delete_personality(self, npc_id: str) -> bool:
        """Supprime un NPC + son history. Retourne True si supprime."""
        cur = self.conn.execute(
            "DELETE FROM personality_drift_history WHERE npc_id = ?", (npc_id,),
        )
        cur2 = self.conn.execute(
            "DELETE FROM npc_personalities WHERE npc_id = ?", (npc_id,),
        )
        self.conn.commit()
        return cur2.rowcount > 0 or cur.rowcount > 0

    # --- drift history -----------------------------------------------------

    def insert_drift(self, drift: PersonalityDrift) -> None:
        """Append un drift dans l'historique."""
        self.conn.execute(
            """
            INSERT INTO personality_drift_history (
                id, npc_id, rule_name, event_category, year,
                delta_json, applied_delta_json,
                related_npc_id, related_event_id, related_mission_id,
                applied_at_ts, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                drift.id,
                drift.npc_id,
                drift.rule_name,
                drift.event_category.value,
                drift.year,
                json.dumps(
                    {d.value: v for d, v in drift.delta.items()},
                    ensure_ascii=False, sort_keys=True,
                ),
                json.dumps(
                    {d.value: v for d, v in drift.applied_delta.items()},
                    ensure_ascii=False, sort_keys=True,
                ),
                drift.related_npc_id,
                drift.related_event_id,
                drift.related_mission_id,
                drift.applied_at_ts,
                drift.notes,
            ),
        )
        self.conn.commit()

    def insert_drifts(self, drifts: Iterable[PersonalityDrift]) -> int:
        """Bulk insert. Retourne nb d'entries traitees."""
        n = 0
        for d in drifts:
            self.insert_drift(d)
            n += 1
        return n

    def list_drift_history(self, npc_id: str) -> list[PersonalityDrift]:
        """Recupere l'historique trie par year asc."""
        rows = self.conn.execute(
            """
            SELECT * FROM personality_drift_history
            WHERE npc_id = ?
            ORDER BY year ASC, applied_at_ts ASC
            """,
            (npc_id,),
        ).fetchall()
        out: list[PersonalityDrift] = []
        for row in rows:
            delta_raw = json.loads(row["delta_json"])
            applied_raw = json.loads(row["applied_delta_json"])
            out.append(PersonalityDrift(
                id=row["id"],
                npc_id=row["npc_id"],
                rule_name=row["rule_name"],
                event_category=EventCategory(row["event_category"]),
                year=row["year"],
                delta={PersonalityDimension(k): float(v) for k, v in delta_raw.items()},
                applied_delta={
                    PersonalityDimension(k): float(v) for k, v in applied_raw.items()
                },
                related_npc_id=row["related_npc_id"],
                related_event_id=row["related_event_id"],
                related_mission_id=row["related_mission_id"],
                applied_at_ts=row["applied_at_ts"],
                notes=row["notes"] or "",
            ))
        return out

    def save_personality_with_history(self, personality: NPCPersonality) -> None:
        """Persiste vector + baseline + tout l'historique (replace).

        Utile apres un apply_events() : le vecteur a change, l'historique a
        N nouveaux drifts. On replace pour eviter doublons.
        """
        # Replace les drifts (suppression + reinsert plus simple que diff)
        self.conn.execute(
            "DELETE FROM personality_drift_history WHERE npc_id = ?",
            (personality.npc_id,),
        )
        for d in personality.drift_history:
            self.insert_drift(d)
        self.upsert_personality(personality)


__all__ = ["PersonalityStore"]
