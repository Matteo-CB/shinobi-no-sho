"""Reseau social pour le belief propagator (Phase B roadmap §5.4).

Encapsule la table kg_social_links : graphe non oriente avec strength.

Convention : on stocke toujours (npc_a, npc_b) tel que npc_a < npc_b
lexicographiquement. La classe SocialLink reordonne automatiquement.

Chaque lien a un link_type (family, friend, mentor, student, rival, enemy,
acquaintance, ally) et une strength dans [0, 1] qui guide la propagation
des beliefs (un lien fort transmet l'info avec moins de degradation).

Filtre temporel : un lien actif a year si valid_from_year <= year et
(valid_to_year IS NULL OR valid_to_year >= year).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from shinobi.kg.schema import SocialLink, social_link_columns

# Force par defaut associee a chaque type de lien (peut etre override)
DEFAULT_STRENGTH_BY_TYPE: dict[str, float] = {
    "family": 0.9,
    "mentor": 0.85,
    "student": 0.85,
    "friend": 0.8,
    "ally": 0.7,
    "rival": 0.6,
    "enemy": 0.5,
    "acquaintance": 0.3,
    "stranger": 0.0,
}


def _ensure_ordered(npc_a: str, npc_b: str) -> tuple[str, str]:
    return (npc_a, npc_b) if npc_a < npc_b else (npc_b, npc_a)


class SocialNetwork:
    """Encapsule la table kg_social_links."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn

    # --- create -----------------------------------------------------------

    def add_link(self, link: SocialLink) -> int:
        """Insert (ou remplace si conflit unique). Retourne id."""
        row = link.to_row()
        # Upsert sur (npc_a, npc_b, link_type, valid_from_year)
        cur = self._conn.execute(
            "INSERT INTO kg_social_links "
            "(npc_a, npc_b, link_type, strength, valid_from_year, valid_to_year, notes) "
            "VALUES (:npc_a, :npc_b, :link_type, :strength, :valid_from_year, "
            ":valid_to_year, :notes) "
            "ON CONFLICT(npc_a, npc_b, link_type, valid_from_year) DO UPDATE SET "
            "strength=excluded.strength, valid_to_year=excluded.valid_to_year, "
            "notes=excluded.notes",
            row,
        )
        self._conn.commit()
        return int(cur.lastrowid or 0)

    def add_links_batch(self, links: Iterable[SocialLink]) -> int:
        """Insert un batch dans une transaction. Retourne le nombre."""
        n = 0
        for link in links:
            self.add_link(link)
            n += 1
        return n

    # --- read -------------------------------------------------------------

    def get_link(
        self, npc_a: str, npc_b: str, *, link_type: str | None = None,
        year: int | None = None,
    ) -> SocialLink | None:
        """Retourne le lien actif (le plus recent) entre deux NPCs."""
        a, b = _ensure_ordered(npc_a, npc_b)
        clauses: list[str] = ["npc_a = ?", "npc_b = ?"]
        params: list[object] = [a, b]
        if link_type is not None:
            clauses.append("link_type = ?")
            params.append(link_type)
        if year is not None:
            clauses.append("(valid_from_year IS NULL OR valid_from_year <= ?)")
            params.append(year)
            clauses.append("(valid_to_year IS NULL OR valid_to_year >= ?)")
            params.append(year)
        sql = (
            "SELECT " + ", ".join(social_link_columns())
            + " FROM kg_social_links WHERE " + " AND ".join(clauses)
            + " ORDER BY strength DESC, id DESC LIMIT 1"
        )
        row = self._conn.execute(sql, params).fetchone()
        return SocialLink.from_row(row) if row else None

    def neighbors(
        self, npc_id: str, *, year: int | None = None,
        min_strength: float = 0.0,
    ) -> list[SocialLink]:
        """Tous les liens actifs de ce NPC (ordre aleatoire SQL)."""
        clauses: list[str] = ["(npc_a = ? OR npc_b = ?)"]
        params: list[object] = [npc_id, npc_id]
        if year is not None:
            clauses.append("(valid_from_year IS NULL OR valid_from_year <= ?)")
            params.append(year)
            clauses.append("(valid_to_year IS NULL OR valid_to_year >= ?)")
            params.append(year)
        if min_strength > 0:
            clauses.append("strength >= ?")
            params.append(min_strength)
        sql = (
            "SELECT " + ", ".join(social_link_columns())
            + " FROM kg_social_links WHERE " + " AND ".join(clauses)
            + " ORDER BY strength DESC"
        )
        rows = self._conn.execute(sql, params).fetchall()
        return [SocialLink.from_row(r) for r in rows]

    def strength_between(
        self, npc_a: str, npc_b: str, *, year: int | None = None,
    ) -> float:
        """Strength du lien le plus fort entre deux NPCs (0 si pas de lien)."""
        link = self.get_link(npc_a, npc_b, year=year)
        return link.strength if link else 0.0

    # --- delete -----------------------------------------------------------

    def delete_link(self, link_id: int) -> bool:
        cur = self._conn.execute("DELETE FROM kg_social_links WHERE id = ?", (link_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def clear_all(self) -> None:
        self._conn.execute("DELETE FROM kg_social_links")
        self._conn.commit()

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS c FROM kg_social_links").fetchone()
        return int(row["c"])


__all__ = ["DEFAULT_STRENGTH_BY_TYPE", "SocialNetwork"]
