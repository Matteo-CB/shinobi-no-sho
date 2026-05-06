"""Persistence SQLite per-save des AgentMemory + Roster.

Spec docs/02 §6.1 : 'Stockage : SQLite par PNJ + embeddings BGE-M3'.
Implementation : 1 SQLite global pour la save (toutes les memoires de
tous les agents), index par npc_id.

Tables :
- agent_observations
- agent_reflections
- agent_plans
- agent_roster
- agent_actions_log (audit complet des actions selectionnees)
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

from shinobi.agents.action_space import AgentAction, AgentActionType
from shinobi.agents.memory import AgentMemory
from shinobi.agents.types import (
    AgentTier,
    Observation,
    Plan,
    PlanStatus,
    Reflection,
    RosterEntry,
)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS agent_observations (
    id TEXT PRIMARY KEY,
    npc_id TEXT NOT NULL,
    text TEXT NOT NULL,
    year INTEGER NOT NULL,
    importance REAL NOT NULL,
    created_at_ts REAL NOT NULL,
    source_npc_id TEXT,
    related_event_id TEXT,
    related_mission_id TEXT,
    related_fact_id INTEGER,
    location_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_obs_npc ON agent_observations(npc_id, year);

CREATE TABLE IF NOT EXISTS agent_reflections (
    id TEXT PRIMARY KEY,
    npc_id TEXT NOT NULL,
    text TEXT NOT NULL,
    year INTEGER NOT NULL,
    importance REAL NOT NULL,
    created_at_ts REAL NOT NULL,
    source_observation_ids TEXT NOT NULL,
    gist TEXT
);
CREATE INDEX IF NOT EXISTS idx_refl_npc ON agent_reflections(npc_id, year);

CREATE TABLE IF NOT EXISTS agent_plans (
    id TEXT PRIMARY KEY,
    npc_id TEXT NOT NULL,
    description TEXT NOT NULL,
    year_started INTEGER NOT NULL,
    year_target INTEGER,
    priority REAL NOT NULL,
    status TEXT NOT NULL,
    importance REAL NOT NULL,
    created_at_ts REAL NOT NULL,
    related_npc_ids TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_plan_npc ON agent_plans(npc_id, status);

CREATE TABLE IF NOT EXISTS agent_roster (
    npc_id TEXT PRIMARY KEY,
    tier TEXT NOT NULL,
    included_since_year INTEGER,
    last_active_year INTEGER,
    last_active_tick INTEGER,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_roster_tier ON agent_roster(tier);

CREATE TABLE IF NOT EXISTS agent_actions_log (
    id TEXT PRIMARY KEY,
    npc_id TEXT NOT NULL,
    type TEXT NOT NULL,
    year INTEGER NOT NULL,
    target_npc_id TEXT,
    location_id TEXT,
    content TEXT,
    importance REAL NOT NULL,
    params_json TEXT,
    tick INTEGER,
    created_at_ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_action_npc ON agent_actions_log(npc_id, year);
"""


def _initialize_db(db_path: Path | str | None) -> sqlite3.Connection:
    if db_path is None:
        conn = sqlite3.connect(":memory:")
    else:
        p = Path(db_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA_SQL)
    conn.commit()
    return conn


class AgentMemoryStore:
    """CRUD persistant des memories d'agents + roster + action log."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = _initialize_db(db_path)

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("AgentMemoryStore is closed.")
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> AgentMemoryStore:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        c = self.conn
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise

    # --- observations ------------------------------------------------------

    def insert_observation(self, obs: Observation) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO agent_observations (
                id, npc_id, text, year, importance, created_at_ts,
                source_npc_id, related_event_id, related_mission_id,
                related_fact_id, location_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                obs.id, obs.npc_id, obs.text, obs.year, obs.importance,
                obs.created_at_ts, obs.source_npc_id, obs.related_event_id,
                obs.related_mission_id, obs.related_fact_id, obs.location_id,
            ),
        )
        self.conn.commit()

    def insert_observations(self, obs_iter: Iterable[Observation]) -> int:
        n = 0
        for o in obs_iter:
            self.insert_observation(o)
            n += 1
        return n

    def list_observations(
        self,
        npc_id: str,
        *,
        year_min: int | None = None,
        year_max: int | None = None,
    ) -> list[Observation]:
        query = "SELECT * FROM agent_observations WHERE npc_id = ?"
        params: list[object] = [npc_id]
        if year_min is not None:
            query += " AND year >= ?"
            params.append(year_min)
        if year_max is not None:
            query += " AND year <= ?"
            params.append(year_max)
        query += " ORDER BY year ASC, created_at_ts ASC"
        rows = self.conn.execute(query, params).fetchall()
        return [
            Observation(
                id=r["id"], npc_id=r["npc_id"], text=r["text"], year=r["year"],
                importance=r["importance"], created_at_ts=r["created_at_ts"],
                source_npc_id=r["source_npc_id"],
                related_event_id=r["related_event_id"],
                related_mission_id=r["related_mission_id"],
                related_fact_id=r["related_fact_id"],
                location_id=r["location_id"],
            )
            for r in rows
        ]

    # --- reflections -------------------------------------------------------

    def insert_reflection(self, refl: Reflection) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO agent_reflections (
                id, npc_id, text, year, importance, created_at_ts,
                source_observation_ids, gist
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                refl.id, refl.npc_id, refl.text, refl.year, refl.importance,
                refl.created_at_ts, json.dumps(list(refl.source_observation_ids)),
                refl.gist,
            ),
        )
        self.conn.commit()

    def insert_reflections(self, refl_iter: Iterable[Reflection]) -> int:
        n = 0
        for r in refl_iter:
            self.insert_reflection(r)
            n += 1
        return n

    def list_reflections(self, npc_id: str) -> list[Reflection]:
        rows = self.conn.execute(
            "SELECT * FROM agent_reflections WHERE npc_id = ? "
            "ORDER BY year ASC, created_at_ts ASC",
            (npc_id,),
        ).fetchall()
        return [
            Reflection(
                id=r["id"], npc_id=r["npc_id"], text=r["text"], year=r["year"],
                importance=r["importance"], created_at_ts=r["created_at_ts"],
                source_observation_ids=tuple(
                    json.loads(r["source_observation_ids"]),
                ),
                gist=r["gist"] or "",
            )
            for r in rows
        ]

    # --- plans -------------------------------------------------------------

    def insert_plan(self, plan: Plan) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO agent_plans (
                id, npc_id, description, year_started, year_target,
                priority, status, importance, created_at_ts, related_npc_ids
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                plan.id, plan.npc_id, plan.description, plan.year_started,
                plan.year_target, plan.priority, plan.status.value,
                plan.importance, plan.created_at_ts,
                json.dumps(list(plan.related_npc_ids)),
            ),
        )
        self.conn.commit()

    def update_plan_status(self, plan_id: str, status: PlanStatus) -> bool:
        cur = self.conn.execute(
            "UPDATE agent_plans SET status = ? WHERE id = ?",
            (status.value, plan_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def list_plans(self, npc_id: str) -> list[Plan]:
        rows = self.conn.execute(
            "SELECT * FROM agent_plans WHERE npc_id = ? "
            "ORDER BY year_started ASC, created_at_ts ASC",
            (npc_id,),
        ).fetchall()
        return [
            Plan(
                id=r["id"], npc_id=r["npc_id"], description=r["description"],
                year_started=r["year_started"], year_target=r["year_target"],
                priority=r["priority"], status=PlanStatus(r["status"]),
                importance=r["importance"], created_at_ts=r["created_at_ts"],
                related_npc_ids=tuple(json.loads(r["related_npc_ids"])),
            )
            for r in rows
        ]

    # --- compose memory ----------------------------------------------------

    def load_memory(self, npc_id: str) -> AgentMemory:
        """Charge AgentMemory complete pour un NPC."""
        return AgentMemory(
            npc_id=npc_id,
            observations=self.list_observations(npc_id),
            reflections=self.list_reflections(npc_id),
            plans=self.list_plans(npc_id),
        )

    def save_memory(self, memory: AgentMemory) -> None:
        """Persiste tout : obs + refl + plans (overwrite via PRIMARY KEY)."""
        for o in memory.observations:
            self.insert_observation(o)
        for r in memory.reflections:
            self.insert_reflection(r)
        for p in memory.plans:
            self.insert_plan(p)

    # --- roster ------------------------------------------------------------

    def upsert_roster(self, entry: RosterEntry) -> None:
        self.conn.execute(
            """
            INSERT INTO agent_roster (
                npc_id, tier, included_since_year, last_active_year,
                last_active_tick, notes
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(npc_id) DO UPDATE SET
                tier = excluded.tier,
                last_active_year = excluded.last_active_year,
                last_active_tick = excluded.last_active_tick,
                notes = excluded.notes
            """,
            (
                entry.npc_id, entry.tier.value, entry.included_since_year,
                entry.last_active_year, entry.last_active_tick, entry.notes,
            ),
        )
        self.conn.commit()

    def list_roster(self, *, tier: AgentTier | None = None) -> list[RosterEntry]:
        if tier is None:
            rows = self.conn.execute(
                "SELECT * FROM agent_roster ORDER BY npc_id",
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM agent_roster WHERE tier = ? ORDER BY npc_id",
                (tier.value,),
            ).fetchall()
        return [
            RosterEntry(
                npc_id=r["npc_id"], tier=AgentTier(r["tier"]),
                included_since_year=r["included_since_year"],
                last_active_year=r["last_active_year"],
                last_active_tick=r["last_active_tick"],
                notes=r["notes"] or "",
            )
            for r in rows
        ]

    def get_roster_entry(self, npc_id: str) -> RosterEntry | None:
        row = self.conn.execute(
            "SELECT * FROM agent_roster WHERE npc_id = ?", (npc_id,),
        ).fetchone()
        if row is None:
            return None
        return RosterEntry(
            npc_id=row["npc_id"], tier=AgentTier(row["tier"]),
            included_since_year=row["included_since_year"],
            last_active_year=row["last_active_year"],
            last_active_tick=row["last_active_tick"],
            notes=row["notes"] or "",
        )

    def delete_roster_entry(self, npc_id: str) -> bool:
        cur = self.conn.execute(
            "DELETE FROM agent_roster WHERE npc_id = ?", (npc_id,),
        )
        self.conn.commit()
        return cur.rowcount > 0

    # --- action log --------------------------------------------------------

    def log_action(self, action: AgentAction, *, tick: int | None = None) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO agent_actions_log (
                id, npc_id, type, year, target_npc_id, location_id,
                content, importance, params_json, tick, created_at_ts
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                action.id, action.npc_id, action.type.value, action.year,
                action.target_npc_id, action.location_id, action.content,
                action.importance,
                json.dumps(action.params, default=str),
                tick, time.time(),
            ),
        )
        self.conn.commit()

    def list_actions(
        self,
        npc_id: str | None = None,
        *,
        year_min: int | None = None,
        year_max: int | None = None,
        limit: int | None = None,
    ) -> list[AgentAction]:
        query = "SELECT * FROM agent_actions_log WHERE 1=1"
        params: list[object] = []
        if npc_id is not None:
            query += " AND npc_id = ?"
            params.append(npc_id)
        if year_min is not None:
            query += " AND year >= ?"
            params.append(year_min)
        if year_max is not None:
            query += " AND year <= ?"
            params.append(year_max)
        query += " ORDER BY year ASC, created_at_ts ASC"
        if limit is not None:
            query += f" LIMIT {int(limit)}"
        rows = self.conn.execute(query, params).fetchall()
        return [
            AgentAction(
                id=r["id"], npc_id=r["npc_id"],
                type=AgentActionType(r["type"]),
                year=r["year"],
                target_npc_id=r["target_npc_id"],
                location_id=r["location_id"],
                content=r["content"] or "",
                importance=r["importance"],
                params=json.loads(r["params_json"] or "{}"),
            )
            for r in rows
        ]


__all__ = ["AgentMemoryStore"]
