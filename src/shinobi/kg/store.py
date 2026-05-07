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

    _savepoint_counter: int = 0

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Transaction explicite atomique. Supporte la nesting via SAVEPOINT.

        Spec Phase A : sans BEGIN explicite ici, chaque INSERT/UPDATE/DELETE
        sous-jacent ouvre/ferme sa propre transaction -> aucune atomicite. Le
        BEGIN explicite force tout le bloc dans une transaction unique.

        Round 34 : nesting supporte. Si on est deja dans une transaction
        parent (out OR in), on utilise SAVEPOINT/RELEASE/ROLLBACK TO. Sinon,
        BEGIN/COMMIT/ROLLBACK normal. SQLite ne supporte pas les vraies nested
        transactions mais SAVEPOINT donne la meme semantique.

        Tous les helpers CRUD (add_fact, update_fact, delete_fact, ...)
        detectent `conn.in_transaction` et skip leur propre commit -> ils
        delegent l'atomicite a la transaction (ou savepoint) parente.
        """
        conn = self.conn
        cur = conn.cursor()
        if conn.in_transaction:
            # Nested : utiliser SAVEPOINT
            self._savepoint_counter += 1
            sp_name = f"sp_{self._savepoint_counter}"
            cur.execute(f"SAVEPOINT {sp_name}")
            try:
                yield conn
                cur.execute(f"RELEASE SAVEPOINT {sp_name}")
            except Exception:
                cur.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
                cur.execute(f"RELEASE SAVEPOINT {sp_name}")
                raise
        else:
            # Top-level : BEGIN/COMMIT/ROLLBACK
            cur.execute("BEGIN")
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def _execute_dml(self, sql: str, params: list | tuple = ()) -> sqlite3.Cursor:
        """Execute un INSERT/UPDATE/DELETE et commit uniquement si on n'est
        pas dans une transaction utilisateur.

        Detection : on capture `conn.in_transaction` AVANT l'execution. Si
        deja True, l'utilisateur a un BEGIN explicite (via transaction()) ;
        on n'ose pas commit (prematurerait son rollback). Sinon, Python
        sqlite3 auto-begins notre DML -> on commit pour fermer.
        """
        in_user_tx = self.conn.in_transaction
        cur = self.conn.execute(sql, params)
        if not in_user_tx:
            self.conn.commit()
        return cur

    # --- create -------------------------------------------------------------

    def add_fact(self, fact: Fact) -> int:
        """Insert un fact, retourne son id. Mute fact.id en place.

        Spec Phase A round 37 : populer fact.id apres insert pour eviter
        que le caller ait a faire `fid = store.add_fact(fact); fact.id = fid`
        manuellement quand il veut enchainer (add_known_by, update_fact, etc).
        """
        ids = insert_facts_batch(self.conn, [fact])
        fact.id = ids[0]
        return ids[0]

    def add_facts_batch(self, facts: Iterable[Fact]) -> list[int]:
        """Insert plusieurs facts en transaction. Retourne la liste d'ids.

        Mute chaque fact.id en place (round 37) - meme rationale que add_fact.
        """
        facts_list = list(facts)
        ids = insert_facts_batch(self.conn, facts_list)
        for f, fid in zip(facts_list, ids):
            f.id = fid
        return ids

    # --- read ---------------------------------------------------------------

    def get_fact(self, fact_id: int) -> Fact | None:
        """Recupere un fact par id."""
        row = self.conn.execute(
            "SELECT " + ", ".join(fact_columns()) + " FROM kg_facts WHERE id = ?",
            (fact_id,),
        ).fetchone()
        return Fact.from_row(row) if row else None

    @staticmethod
    def _build_filter_clauses(
        *,
        subject: str | None = None,
        relation: str | None = None,
        relation_prefix: str | None = None,
        object_value: str | None = None,
        object_type: ObjectType | str | None = None,
        year: int | None = None,
        year_range: tuple[int, int] | None = None,
        canonicity: Canonicity | str | None = None,
        source: str | None = None,
        source_prefix: str | None = None,
        min_confidence: float | None = None,
    ) -> tuple[list[str], list[object]]:
        """Construit clauses WHERE et parametres pour get_facts/count/etc.

        Spec Phase A : filtres temporels et categoriels symetriques entre
        toutes les operations CRUD. Year/year_range sont mutuellement exclusifs.
        """
        if year is not None and year_range is not None:
            raise ValueError("year et year_range sont mutuellement exclusifs")
        clauses: list[str] = []
        params: list[object] = []
        if subject is not None:
            clauses.append("subject = ?")
            params.append(subject)
        if relation is not None:
            clauses.append("relation = ?")
            params.append(relation)
        if relation_prefix is not None:
            clauses.append("relation LIKE ?")
            params.append(f"{relation_prefix}%")
        if object_value is not None:
            clauses.append("object = ?")
            params.append(object_value)
        if object_type is not None:
            otype = object_type.value if isinstance(object_type, ObjectType) else object_type
            clauses.append("object_type = ?")
            params.append(otype)
        if year is not None:
            clauses.append("(valid_from_year IS NULL OR valid_from_year <= ?)")
            params.append(year)
            clauses.append("(valid_to_year IS NULL OR valid_to_year >= ?)")
            params.append(year)
        if year_range is not None:
            yr_from, yr_to = year_range
            if yr_from is None or yr_to is None:
                raise ValueError(
                    f"year_range doit etre (int, int), recu ({yr_from}, {yr_to}); "
                    "utiliser year=X pour borne unique"
                )
            if yr_from > yr_to:
                raise ValueError(f"year_range invalide: from={yr_from} > to={yr_to}")
            clauses.append("(valid_from_year IS NULL OR valid_from_year <= ?)")
            params.append(yr_to)
            clauses.append("(valid_to_year IS NULL OR valid_to_year >= ?)")
            params.append(yr_from)
        if canonicity is not None:
            cval = canonicity.value if isinstance(canonicity, Canonicity) else canonicity
            clauses.append("canonicity = ?")
            params.append(cval)
        if source is not None:
            clauses.append("source = ?")
            params.append(source)
        if source_prefix is not None:
            clauses.append("source LIKE ?")
            params.append(f"{source_prefix}%")
        if min_confidence is not None:
            clauses.append("confidence >= ?")
            params.append(min_confidence)
        return clauses, params

    def get_facts(
        self,
        *,
        subject: str | None = None,
        relation: str | None = None,
        relation_prefix: str | None = None,
        object_value: str | None = None,
        object_type: ObjectType | str | None = None,
        year: int | None = None,
        year_range: tuple[int, int] | None = None,
        canonicity: Canonicity | str | None = None,
        source: str | None = None,
        source_prefix: str | None = None,
        min_confidence: float | None = None,
        limit: int | None = None,
    ) -> list[Fact]:
        """Requete generique du KG avec filtres composables.

        Spec Phase A doc 02 : "API CRUD avec filtres temporels".

        - subject / relation / object_value : matchs exacts
        - relation_prefix : LIKE 'prefix%' (ex: 'outcome:', 'requires:')
        - year : filtre temporel point (fact actif a cette annee)
        - year_range : (from, to) -> fact dont la validite chevauche [from, to]
        - canonicity : 'canon_strict' / 'canon_modified' / 'divergent'
        - source_prefix : prefixe (ex: 'event_', 'player_action_')
        - min_confidence : seuil bas
        - limit : LIMIT SQL
        """
        clauses, params = self._build_filter_clauses(
            subject=subject, relation=relation,
            relation_prefix=relation_prefix,
            object_value=object_value, object_type=object_type,
            year=year, year_range=year_range,
            canonicity=canonicity, source=source,
            source_prefix=source_prefix,
            min_confidence=min_confidence,
        )
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
        subject: str | None = None,
        relation: str | None = None,
        relation_prefix: str | None = None,
        object_value: str | None = None,
        object_type: ObjectType | str | None = None,
        year: int | None = None,
        year_range: tuple[int, int] | None = None,
        canonicity: Canonicity | str | None = None,
        source: str | None = None,
        source_prefix: str | None = None,
        min_confidence: float | None = None,
    ) -> int:
        """Compte les facts (avec filtres composables symetriques a get_facts).

        Spec Phase A : count() doit accepter les memes filtres que get_facts
        pour permettre des comparaisons / monitoring sans charger les rows.
        """
        clauses, params = self._build_filter_clauses(
            subject=subject, relation=relation,
            relation_prefix=relation_prefix,
            object_value=object_value, object_type=object_type,
            year=year, year_range=year_range,
            canonicity=canonicity, source=source,
            source_prefix=source_prefix,
            min_confidence=min_confidence,
        )
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
        object_type: ObjectType | str | None = None,
        valid_from_year: int | None = None,
        valid_to_year: int | None = None,
        source: str | None = None,
        confidence: float | None = None,
        canonicity: Canonicity | str | None = None,
        known_by_npc_ids: list[str] | None = None,
    ) -> Fact | None:
        """Met a jour un fact existant. Champs None ignores (pas de mise a 0).

        Spec Phase A : "API CRUD avec filtres temporels". Toutes les colonnes
        non-clef-primaire (id, subject, relation, created_at_ts) sont
        updateables symetriquement.

        Cas d'usage `source` : promouvoir un fact 'inferred' en 'canon' apres
        verification, ou attribuer un fact a un event runtime ('event_42').
        Cas d'usage `object_type` : corriger une mauvaise classification.
        """
        sets: list[str] = []
        params: list[object] = []
        if object_value is not None:
            sets.append("object = ?")
            params.append(object_value)
        if object_type is not None:
            otype = object_type.value if isinstance(object_type, ObjectType) else object_type
            sets.append("object_type = ?")
            params.append(otype)
        if valid_from_year is not None:
            sets.append("valid_from_year = ?")
            params.append(valid_from_year)
        if valid_to_year is not None:
            sets.append("valid_to_year = ?")
            params.append(valid_to_year)
        if source is not None:
            sets.append("source = ?")
            params.append(source)
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
        self._execute_dml(
            f"UPDATE kg_facts SET {', '.join(sets)} WHERE id = ?",
            params,
        )
        return self.get_fact(fact_id)

    def delete_facts(
        self,
        *,
        subject: str | None = None,
        relation: str | None = None,
        relation_prefix: str | None = None,
        object_value: str | None = None,
        object_type: ObjectType | str | None = None,
        year: int | None = None,
        year_range: tuple[int, int] | None = None,
        canonicity: Canonicity | str | None = None,
        source: str | None = None,
        source_prefix: str | None = None,
        min_confidence: float | None = None,
    ) -> int:
        """Suppression bulk par filtre. Retourne le nombre de facts supprimes.

        Spec Phase A : filtres symetriques avec get_facts() et count(). Au
        moins UN filtre doit etre fourni (refus du DELETE FROM nu pour
        eviter clear_all() accidentel).
        """
        clauses, params = self._build_filter_clauses(
            subject=subject, relation=relation,
            relation_prefix=relation_prefix,
            object_value=object_value, object_type=object_type,
            year=year, year_range=year_range,
            canonicity=canonicity, source=source,
            source_prefix=source_prefix,
            min_confidence=min_confidence,
        )
        if not clauses:
            raise ValueError(
                "delete_facts requiert au moins un filtre (utiliser clear_all() "
                "explicitement pour wipe complet)"
            )
        sql = f"DELETE FROM kg_facts WHERE {' AND '.join(clauses)}"
        cur = self._execute_dml(sql, params)
        return int(cur.rowcount)

    def close_fact(self, fact_id: int, valid_to_year: int) -> Fact | None:
        """Ferme la validite d'un fact (ex: perso meurt)."""
        return self.update_fact(fact_id, valid_to_year=valid_to_year)

    # --- delete -------------------------------------------------------------

    def delete_fact(self, fact_id: int) -> bool:
        """Supprime un fact. Retourne True si supprime."""
        cur = self._execute_dml("DELETE FROM kg_facts WHERE id = ?", (fact_id,))
        return cur.rowcount > 0

    def clear_all(self) -> None:
        """Vide toute la table (tests + reset)."""
        self._execute_dml("DELETE FROM kg_facts")

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

        Spec Phase A round 31 : utilise SQLite JSON1 (json_each) pour pousser
        le filtrage cote SQL au lieu de scanner la table entiere puis filtrer
        en Python. Sur 41k facts c'est O(n_NPC) au lieu de O(n_facts) pour
        chaque appel known_to.
        """
        sql = (
            "SELECT " + ", ".join(fact_columns())
            + " FROM kg_facts WHERE EXISTS ("
            "  SELECT 1 FROM json_each(kg_facts.known_by_npc_ids) "
            "  WHERE value = ?"
            ")"
        )
        params: list[object] = [npc_id]
        if year is not None:
            sql += " AND (valid_from_year IS NULL OR valid_from_year <= ?)"
            params.append(year)
            sql += " AND (valid_to_year IS NULL OR valid_to_year >= ?)"
            params.append(year)
        rows = self.conn.execute(sql, params).fetchall()
        return [Fact.from_row(r) for r in rows]


__all__ = [
    "Canonicity",
    "Fact",
    "FactSource",
    "KnowledgeGraphStore",
    "ObjectType",
]
