"""Belief Propagator (Phase B roadmap §5.4).

Fonctions clefs :

- record_witness(npc_id, fact_id, year) : NPC apprend un fact en direct,
  fidelity 1.0
- propagate_to(source_npc, target_npc, fact_id, year, channel) : un NPC
  transmet un fact a un autre. La fidelity du target est calculee comme
  fidelity_source * social_link_strength * channel_decay.
- propagate_cascade(witness_npc, fact_id, year, max_depth=3) : BFS dans
  le reseau social a partir du temoin, propage avec decay multiplicatif.

La distorsion peut etre stockee dans `distortion_notes` (texte libre) pour
les usages avances (rumeurs deformees). Pour Phase B simple, on ne distord
pas l'object lui-meme.

Decay channel-dependant :
  - witness (temoin direct) : pas de decay, fidelity preservee
  - rumor (rumeur) : *0.7 (perte par chaine de transmission)
  - spy (espionnage) : *0.85 (info de meilleure qualite que rumeur)
  - canon_default : *1.0 (fait connu par tous depuis toujours)

Le `min_fidelity_threshold` (default 0.1) coupe la cascade : une info trop
degradee n'est plus retenue. Empeche les explosions infinies.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from shinobi.kg.schema import Belief, belief_columns
from shinobi.kg.social import SocialNetwork

# Coefficient de decay par canal de transmission
CHANNEL_DECAY: dict[str, float] = {
    "witness": 1.0,
    "rumor": 0.7,
    "spy": 0.85,
    "canon_default": 1.0,
    "report": 0.9,  # rapport officiel d'un subordonne
}

DEFAULT_MIN_FIDELITY = 0.1
DEFAULT_MAX_DEPTH = 3


class BeliefPropagator:
    """Gestion des beliefs (sous-KG par NPC) et propagation."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        social_network: SocialNetwork | None = None,
    ) -> None:
        self._conn = conn
        self._social = social_network or SocialNetwork(conn)

    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn

    @property
    def social(self) -> SocialNetwork:
        return self._social

    # --- single belief CRUD -----------------------------------------------

    def add_belief(self, belief: Belief) -> int:
        """Insert (ou upgrade) un belief. Si une entree existe pour (fact, npc),
        on garde la fidelity la plus haute (un NPC ne peut qu'apprendre mieux,
        pas oublier - sauf operation explicite).

        Spec §5.4 'sous-KG par PNJ' : l'ajout d'un belief synchronise aussi
        Fact.known_by_npc_ids pour que `known_to(npc)` (sub-KG view) reste
        coherent avec kg_beliefs (storage). Sans ce sync, les 2 vues du
        sub-KG divergent.
        """
        row = belief.to_row()
        cur = self._conn.execute(
            "INSERT INTO kg_beliefs "
            "(fact_id, npc_id, fidelity, learned_at_year, learned_via_npc_id, "
            "learned_via_channel, distortion_notes) "
            "VALUES (:fact_id, :npc_id, :fidelity, :learned_at_year, "
            ":learned_via_npc_id, :learned_via_channel, :distortion_notes) "
            "ON CONFLICT(fact_id, npc_id) DO UPDATE SET "
            "fidelity = MAX(fidelity, excluded.fidelity), "
            "learned_at_year = COALESCE(MIN(learned_at_year, excluded.learned_at_year), "
            "                          excluded.learned_at_year), "
            "learned_via_npc_id = CASE "
            "  WHEN fidelity < excluded.fidelity THEN excluded.learned_via_npc_id "
            "  ELSE learned_via_npc_id END, "
            "learned_via_channel = CASE "
            "  WHEN fidelity < excluded.fidelity THEN excluded.learned_via_channel "
            "  ELSE learned_via_channel END",
            row,
        )
        # Spec §5.4 : sync known_by_npc_ids du Fact (sub-KG coherent)
        try:
            row_kf = self._conn.execute(
                "SELECT known_by_npc_ids FROM kg_facts WHERE id = ?",
                (belief.fact_id,),
            ).fetchone()
            if row_kf is not None:
                import json as _json
                raw = row_kf["known_by_npc_ids"] or "[]"
                try:
                    known = set(_json.loads(raw))
                except (_json.JSONDecodeError, TypeError):
                    known = set()
                if belief.npc_id not in known:
                    known.add(belief.npc_id)
                    self._conn.execute(
                        "UPDATE kg_facts SET known_by_npc_ids = ? WHERE id = ?",
                        (_json.dumps(sorted(known)), belief.fact_id),
                    )
        except Exception:
            pass
        self._conn.commit()
        return int(cur.lastrowid or 0)

    def get_belief(self, fact_id: int, npc_id: str) -> Belief | None:
        sql = (
            "SELECT " + ", ".join(belief_columns())
            + " FROM kg_beliefs WHERE fact_id = ? AND npc_id = ?"
        )
        row = self._conn.execute(sql, (fact_id, npc_id)).fetchone()
        return Belief.from_row(row) if row else None

    def beliefs_of(
        self,
        npc_id: str,
        *,
        min_fidelity: float = 0.0,
        before_year: int | None = None,
    ) -> list[Belief]:
        """Tous les beliefs d'un NPC. Filtre fidelity et anteriorite optionnels."""
        clauses: list[str] = ["npc_id = ?"]
        params: list[object] = [npc_id]
        if min_fidelity > 0:
            clauses.append("fidelity >= ?")
            params.append(min_fidelity)
        if before_year is not None:
            clauses.append(
                "(learned_at_year IS NULL OR learned_at_year <= ?)"
            )
            params.append(before_year)
        sql = (
            "SELECT " + ", ".join(belief_columns())
            + " FROM kg_beliefs WHERE " + " AND ".join(clauses)
            + " ORDER BY fidelity DESC"
        )
        rows = self._conn.execute(sql, params).fetchall()
        return [Belief.from_row(r) for r in rows]

    def npcs_who_know(self, fact_id: int) -> list[str]:
        """Liste des NPCs qui connaissent ce fact (toute fidelity)."""
        rows = self._conn.execute(
            "SELECT npc_id FROM kg_beliefs WHERE fact_id = ?",
            (fact_id,),
        ).fetchall()
        return [r["npc_id"] for r in rows]

    # --- propagation ------------------------------------------------------

    def record_witness(
        self, npc_id: str, fact_id: int, year: int | None = None
    ) -> int:
        """NPC observe un fact en direct. Fidelity = 1.0."""
        return self.add_belief(Belief(
            fact_id=fact_id, npc_id=npc_id,
            fidelity=1.0,
            learned_at_year=year,
            learned_via_npc_id=None,
            learned_via_channel="witness",
        ))

    def propagate_to(
        self,
        source_npc: str,
        target_npc: str,
        fact_id: int,
        *,
        year: int | None = None,
        channel: str = "rumor",
    ) -> Belief | None:
        """Le source transmet un fact au target. Calcule la fidelity et insert.

        Retourne le belief cree si propagation effective, None si :
        - source_npc ne connait pas le fact
        - aucun lien social entre source et target
        - fidelity finale < min_threshold
        """
        source_belief = self.get_belief(fact_id, source_npc)
        if source_belief is None:
            return None
        link_strength = self._social.strength_between(source_npc, target_npc, year=year)
        if link_strength <= 0:
            return None
        decay = CHANNEL_DECAY.get(channel, 0.5)
        new_fidelity = source_belief.fidelity * link_strength * decay
        if new_fidelity < DEFAULT_MIN_FIDELITY:
            return None
        new_belief = Belief(
            fact_id=fact_id,
            npc_id=target_npc,
            fidelity=new_fidelity,
            learned_at_year=year,
            learned_via_npc_id=source_npc,
            learned_via_channel=channel,
        )
        self.add_belief(new_belief)
        return new_belief

    def propagate_cascade(
        self,
        witness_npc: str,
        fact_id: int,
        *,
        year: int | None = None,
        max_depth: int = DEFAULT_MAX_DEPTH,
        channel: str = "rumor",
        min_fidelity: float = DEFAULT_MIN_FIDELITY,
        initial_fidelity: float = 1.0,
        year_offset_per_hop: int = 0,
    ) -> dict[str, float]:
        """BFS dans le reseau social a partir du temoin, propage avec decay.

        Spec §5.4 : 'Sasuke ne sait peut-etre pas en year 9, Madara apprend
        en year 12, Pain en year 14'. Pour modeler la propagation TEMPORELLE,
        utiliser `year_offset_per_hop > 0` : chaque hop ajoute N annees au
        learned_at_year. Default 0 = propagation instantanee (back-compat).

        Retourne un dict {npc_id: fidelity_finale} de tous les NPCs qui ont
        appris le fact (incluant le temoin a `initial_fidelity`, default 1.0).

        Algorithme : BFS niveau par niveau, fidelity courante = base * (link *
        decay)^depth. Si on revisite un NPC avec une fidelity superieure, on
        ne propage pas plus loin (deja connu mieux).

        `initial_fidelity` : fidelity du temoin de depart. 1.0 = temoin direct
        d'un event. < 1.0 = temoin d'une rumeur (ex: rumor regional 0.8).
        """
        # Cas special : si initial_fidelity = 1.0, on enregistre comme witness
        # (channel='witness'). Sinon comme rumor avec la fidelity passee.
        if initial_fidelity >= 1.0:
            self.record_witness(witness_npc, fact_id, year=year)
        else:
            self.add_belief(Belief(
                fact_id=fact_id, npc_id=witness_npc,
                fidelity=initial_fidelity,
                learned_at_year=year,
                learned_via_channel=channel,
            ))
        propagated: dict[str, float] = {witness_npc: initial_fidelity}

        # BFS niveau par niveau, en partant de la fidelity initiale.
        # Spec §5.4 : `year_offset_per_hop` ajoute N annees au learned_at
        # pour chaque hop, modelisant la propagation temporelle ('Sasuke
        # year 9, Madara year 12, Pain year 14').
        frontier: list[tuple[str, float]] = [(witness_npc, initial_fidelity)]
        for depth in range(max_depth):
            next_frontier: list[tuple[str, float]] = []
            # Year offset pour ce depth (depth=0 = 1er hop)
            hop_year = year
            if year is not None and year_offset_per_hop > 0:
                hop_year = year + (depth + 1) * year_offset_per_hop
            for src, src_fid in frontier:
                for link in self._social.neighbors(src, year=year):
                    target = link.other(src)
                    decay = CHANNEL_DECAY.get(channel, 0.5)
                    new_fid = src_fid * link.strength * decay
                    if new_fid < min_fidelity:
                        continue
                    # Si target deja propage avec fidelity superieure ou egale, skip
                    existing = propagated.get(target, 0.0)
                    if new_fid <= existing:
                        continue
                    propagated[target] = new_fid
                    self.add_belief(Belief(
                        fact_id=fact_id,
                        npc_id=target,
                        fidelity=new_fid,
                        learned_at_year=hop_year,
                        learned_via_npc_id=src,
                        learned_via_channel=channel,
                    ))
                    next_frontier.append((target, new_fid))
            frontier = next_frontier
            if not frontier:
                break
        return propagated

    # --- aggregate / clear -----------------------------------------------

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS c FROM kg_beliefs").fetchone()
        return int(row["c"])

    def clear_all(self) -> None:
        self._conn.execute("DELETE FROM kg_beliefs")
        self._conn.commit()

    def add_beliefs_batch(self, beliefs: Iterable[Belief]) -> int:
        n = 0
        for b in beliefs:
            self.add_belief(b)
            n += 1
        return n

    def belief_view_for_npc(
        self,
        npc_id: str,
        *,
        year: int | None = None,
        min_fidelity: float = 0.0,
    ) -> list[tuple[int, str, str, str | None, float]]:
        """Sous-KG d'un NPC : liste de tuples (fact_id, subject, relation, object, fidelity).

        Joint kg_beliefs et kg_facts, applique le filtre temporel sur le fact
        (actif a year) et le seuil min_fidelity sur le belief.

        Optimise via un seul SELECT JOIN. Pour des usages plus complexes, cf
        `beliefs_of` + `get_fact` separes.
        """
        clauses: list[str] = ["b.npc_id = ?"]
        params: list[object] = [npc_id]
        if min_fidelity > 0:
            clauses.append("b.fidelity >= ?")
            params.append(min_fidelity)
        if year is not None:
            clauses.append("(f.valid_from_year IS NULL OR f.valid_from_year <= ?)")
            params.append(year)
            clauses.append("(f.valid_to_year IS NULL OR f.valid_to_year >= ?)")
            params.append(year)
            clauses.append("(b.learned_at_year IS NULL OR b.learned_at_year <= ?)")
            params.append(year)
        sql = (
            "SELECT f.id AS fact_id, f.subject, f.relation, f.object, b.fidelity "
            "FROM kg_beliefs b JOIN kg_facts f ON b.fact_id = f.id "
            "WHERE " + " AND ".join(clauses) + " ORDER BY b.fidelity DESC"
        )
        rows = self._conn.execute(sql, params).fetchall()
        return [
            (int(r["fact_id"]), r["subject"], r["relation"], r["object"], float(r["fidelity"]))
            for r in rows
        ]


__all__ = ["CHANNEL_DECAY", "DEFAULT_MAX_DEPTH", "DEFAULT_MIN_FIDELITY", "BeliefPropagator"]
