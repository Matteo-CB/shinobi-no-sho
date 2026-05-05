"""Knowledge Graph Store : API CRUD avec filtres temporels et requetes typees.

Wrapper autour de la base SQLite kg_facts. Expose :

- add_fact() / add_facts_batch()
- get_facts(subject?, relation?, object?, year?, canonicity?, source?, min_confidence?)
- update_fact(id, **fields) -> Fact
- delete_fact(id)
- close_fact(id, valid_to_year) : ferme la validite (utile pour les morts)
- count() : metrics
- known_to(npc_id) : sous-KG du belief propagator (futur §5.4 roadmap)

Filtre temporel par defaut : un fact est 'actif' a year si
  valid_from_year is null OR valid_from_year <= year
  AND
  valid_to_year is null OR valid_to_year >= year
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

from shinobi.kg.schema import (
    Canonicity,
    Fact,
    FactSource,
    ObjectType,
    fact_columns,
    initialize_db,
    insert_facts_batch,
)


class KnowledgeGraphStore:
    """Acces principal au KG dynamique. Encapsule la connexion SQLite."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        """Ouvre une base au chemin donne (ou in-memory si None).

        Le fichier est cree si manquant, le schema applique si nouveau.
        """
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = initialize_db(db_path)

    # --- lifecycle ----------------------------------------------------------

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> KnowledgeGraphStore:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("KnowledgeGraphStore is closed.")
        return self._conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Transaction explicite. SQLite est en autocommit par defaut."""
        conn = self.conn
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    # --- create -------------------------------------------------------------

    def add_fact(self, fact: Fact) -> int:
        """Insert un fact, retourne son id."""
        ids = insert_facts_batch(self.conn, [fact])
        return ids[0]

    def add_facts_batch(self, facts: Iterable[Fact]) -> list[int]:
        """Insert plusieurs facts en transaction. Retourne la liste d'ids."""
        return insert_facts_batch(self.conn, list(facts))

    # --- read ---------------------------------------------------------------

    def get_fact(self, fact_id: int) -> Fact | None:
        """Recupere un fact par id."""
        row = self.conn.execute(
            "SELECT " + ", ".join(fact_columns()) + " FROM kg_facts WHERE id = ?",
            (fact_id,),
        ).fetchone()
        return Fact.from_row(row) if row else None

    def get_facts(
        self,
        *,
        subject: str | None = None,
        relation: str | None = None,
        object_value: str | None = None,
        year: int | None = None,
        canonicity: Canonicity | str | None = None,
        source_prefix: str | None = None,
        min_confidence: float | None = None,
        limit: int | None = None,
    ) -> list[Fact]:
        """Requete generique du KG avec filtres composables.

        - subject / relation / object_value : matchs exacts
        - year : filtre temporel (fact actif a cette annee, voir docstring module)
        - canonicity : 'canon_strict' / 'canon_modified' / 'divergent'
        - source_prefix : prefixe (ex: 'event_', 'player_action_')
        - min_confidence : seuil bas
        - limit : LIMIT SQL
        """
        clauses: list[str] = []
        params: list[object] = []
        if subject is not None:
            clauses.append("subject = ?")
            params.append(subject)
        if relation is not None:
            clauses.append("relation = ?")
            params.append(relation)
        if object_value is not None:
            clauses.append("object = ?")
            params.append(object_value)
        if year is not None:
            clauses.append("(valid_from_year IS NULL OR valid_from_year <= ?)")
            params.append(year)
            clauses.append("(valid_to_year IS NULL OR valid_to_year >= ?)")
            params.append(year)
        if canonicity is not None:
            cval = canonicity.value if isinstance(canonicity, Canonicity) else canonicity
            clauses.append("canonicity = ?")
            params.append(cval)
        if source_prefix is not None:
            clauses.append("source LIKE ?")
            params.append(f"{source_prefix}%")
        if min_confidence is not None:
            clauses.append("confidence >= ?")
            params.append(min_confidence)

        sql = "SELECT " + ", ".join(fact_columns()) + " FROM kg_facts"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"

        rows = self.conn.execute(sql, params).fetchall()
        return [Fact.from_row(r) for r in rows]

    def count(
        self,
        *,
        canonicity: Canonicity | str | None = None,
        source_prefix: str | None = None,
    ) -> int:
        """Compte les facts (avec filtres optionnels)."""
        clauses: list[str] = []
        params: list[object] = []
        if canonicity is not None:
            cval = canonicity.value if isinstance(canonicity, Canonicity) else canonicity
            clauses.append("canonicity = ?")
            params.append(cval)
        if source_prefix is not None:
            clauses.append("source LIKE ?")
            params.append(f"{source_prefix}%")
        sql = "SELECT COUNT(*) AS c FROM kg_facts"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        row = self.conn.execute(sql, params).fetchone()
        return int(row["c"])

    # --- update -------------------------------------------------------------

    def update_fact(
        self,
        fact_id: int,
        *,
        object_value: str | None = None,
        valid_to_year: int | None = None,
        confidence: float | None = None,
        canonicity: Canonicity | str | None = None,
        known_by_npc_ids: list[str] | None = None,
    ) -> Fact | None:
        """Met a jour un fact existant. Champs None ignores (pas de mise a 0)."""
        sets: list[str] = []
        params: list[object] = []
        if object_value is not None:
            sets.append("object = ?")
            params.append(object_value)
        if valid_to_year is not None:
            sets.append("valid_to_year = ?")
            params.append(valid_to_year)
        if confidence is not None:
            sets.append("confidence = ?")
            params.append(confidence)
        if canonicity is not None:
            cval = canonicity.value if isinstance(canonicity, Canonicity) else canonicity
            sets.append("canonicity = ?")
            params.append(cval)
        if known_by_npc_ids is not None:
            sets.append("known_by_npc_ids = ?")
            params.append(json.dumps(known_by_npc_ids))
        if not sets:
            return self.get_fact(fact_id)
        params.append(fact_id)
        self.conn.execute(
            f"UPDATE kg_facts SET {', '.join(sets)} WHERE id = ?",
            params,
        )
        self.conn.commit()
        return self.get_fact(fact_id)

    def close_fact(self, fact_id: int, valid_to_year: int) -> Fact | None:
        """Ferme la validite d'un fact (ex: perso meurt)."""
        return self.update_fact(fact_id, valid_to_year=valid_to_year)

    # --- delete -------------------------------------------------------------

    def delete_fact(self, fact_id: int) -> bool:
        """Supprime un fact. Retourne True si supprime."""
        cur = self.conn.execute("DELETE FROM kg_facts WHERE id = ?", (fact_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def clear_all(self) -> None:
        """Vide toute la table (tests + reset)."""
        self.conn.execute("DELETE FROM kg_facts")
        self.conn.commit()

    # --- belief propagator (Phase B future, helpers de base maintenant) ----

    def add_known_by(self, fact_id: int, npc_ids: Iterable[str]) -> None:
        """Etend la liste known_by_npc_ids d'un fact existant."""
        fact = self.get_fact(fact_id)
        if fact is None:
            return
        new_set = set(fact.known_by_npc_ids) | set(npc_ids)
        self.update_fact(fact_id, known_by_npc_ids=sorted(new_set))

    def known_to(
        self, npc_id: str, *, year: int | None = None
    ) -> list[Fact]:
        """Sous-KG : facts connus par un NPC.

        Approche legere : SELECT puis filtrage Python via JSON. Pour scale,
        on pourra ajouter une table de jointure plus tard.
        """
        sql = "SELECT " + ", ".join(fact_columns()) + " FROM kg_facts"
        clauses: list[str] = []
        params: list[object] = []
        if year is not None:
            clauses.append("(valid_from_year IS NULL OR valid_from_year <= ?)")
            params.append(year)
            clauses.append("(valid_to_year IS NULL OR valid_to_year >= ?)")
            params.append(year)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        rows = self.conn.execute(sql, params).fetchall()
        out: list[Fact] = []
        for row in rows:
            f = Fact.from_row(row)
            if npc_id in f.known_by_npc_ids:
                out.append(f)
        return out


__all__ = [
    "Canonicity",
    "Fact",
    "FactSource",
    "KnowledgeGraphStore",
    "ObjectType",
]
