"""AgentMemory : memoire 3-niveaux + retrieval Park et al.

docs/02 §6.1 :
> retrieve(query, top_k=5) :
>   Recupere les memories selon recency + importance + relevance
>   (similaire au pattern Generative Agents)

Implementation :
- recency = exp(-decay * (now - created_at_ts))
  decay tel que une memoire de 30 jours soit a ~0.5 (demi-vie)
- importance = entry.importance (deja dans [0,1])
- relevance = similarite text query <-> entry.text
  Approche : embeddings cosinus si embedding fourni, sinon overlap mots
  (deterministe, fonctionne sans BGE-M3 dispo)
- score = alpha*recency + beta*importance + gamma*relevance

L'agent peut choisir de ne pas appeler le LLM mais juste retrieve
deterministe : essentiel pour la frugalite (spec §11).
"""

from __future__ import annotations

import math
import re
import time
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

from shinobi.agents.types import (
    MemoryEntry,
    Observation,
    Plan,
    PlanStatus,
    Reflection,
)

# Decay : 30 jours -> recency ~0.5
# exp(-decay * 30*86400) = 0.5  =>  decay = ln(2) / (30*86400)
DEFAULT_RECENCY_DECAY: float = math.log(2.0) / (30.0 * 86400.0)

DEFAULT_WEIGHTS: tuple[float, float, float] = (1.0, 1.0, 1.0)
"""Poids (recency, importance, relevance) du score retrieve."""


def _normalize(text: str) -> str:
    """Lower + strip accents."""
    if not text:
        return ""
    s = unicodedata.normalize("NFD", text)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.lower()


_TOKEN_RE = re.compile(r"[a-z0-9_]+")


def _tokenize(text: str) -> set[str]:
    """Tokenise un texte (mots >=3 caracteres)."""
    if not text:
        return set()
    norm = _normalize(text)
    return {t for t in _TOKEN_RE.findall(norm) if len(t) >= 3}


def jaccard_similarity(a: str, b: str) -> float:
    """Similarite Jaccard entre deux textes (mots)."""
    sa, sb = _tokenize(a), _tokenize(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def recency_score(
    entry_ts: float, *, now_ts: float | None = None,
    decay: float = DEFAULT_RECENCY_DECAY,
) -> float:
    """Score de recence : exp(-decay * delta_seconds).

    Une memoire vieille de 30 jours -> 0.5 ; 1 jour -> ~0.98 ; jamais 0.
    """
    if now_ts is None:
        now_ts = time.time()
    delta = max(0.0, now_ts - entry_ts)
    return math.exp(-decay * delta)


def relevance_score(query: str, entry_text: str) -> float:
    """Relevance fallback : Jaccard sur mots. Deterministe, sans embeddings.

    Pour passer a BGE-M3, le caller doit utiliser `relevance_score_emb`
    (pas implemente ici par defaut, branchable).
    """
    return jaccard_similarity(query, entry_text)


def composite_score(
    entry: MemoryEntry,
    query: str,
    *,
    now_ts: float | None = None,
    weights: tuple[float, float, float] = DEFAULT_WEIGHTS,
    decay: float = DEFAULT_RECENCY_DECAY,
) -> float:
    """Score composite Park et al : alpha*recency + beta*importance + gamma*relevance."""
    a, b, g = weights
    rec = recency_score(entry.created_at_ts, now_ts=now_ts, decay=decay)
    imp = entry.importance
    rel = relevance_score(query, entry.text) if query else 0.0
    return a * rec + b * imp + g * rel


@dataclass(frozen=True)
class RetrievalConfig:
    """Configuration du retrieval (poids et decay)."""

    weights: tuple[float, float, float] = DEFAULT_WEIGHTS
    decay: float = DEFAULT_RECENCY_DECAY


class AgentMemory:
    """Memoire 3-niveaux d'un PNJ : observations + reflections + plans.

    L'objet est in-memory (la persistance est le job de `store.py`). Le caller
    charge l'agent au demarrage du tick et persiste au save.

    Usage :

    ```python
    mem = AgentMemory(npc_id='uchiha_sasuke')
    mem.add_observation(Observation(...))
    top5 = mem.retrieve('massacre clan', top_k=5)
    ```
    """

    def __init__(
        self,
        *,
        npc_id: str,
        observations: Iterable[Observation] = (),
        reflections: Iterable[Reflection] = (),
        plans: Iterable[Plan] = (),
        config: RetrievalConfig | None = None,
    ) -> None:
        self._npc_id = npc_id
        self._obs: list[Observation] = list(observations)
        self._refl: list[Reflection] = list(reflections)
        self._plans: list[Plan] = list(plans)
        self._config = config or RetrievalConfig()

    @property
    def npc_id(self) -> str:
        return self._npc_id

    @property
    def config(self) -> RetrievalConfig:
        return self._config

    @property
    def observations(self) -> tuple[Observation, ...]:
        return tuple(self._obs)

    @property
    def reflections(self) -> tuple[Reflection, ...]:
        return tuple(self._refl)

    @property
    def plans(self) -> tuple[Plan, ...]:
        return tuple(self._plans)

    @property
    def size(self) -> int:
        return len(self._obs) + len(self._refl) + len(self._plans)

    # --- write -------------------------------------------------------------

    def add_observation(self, obs: Observation) -> None:
        if obs.npc_id != self._npc_id:
            raise ValueError(
                f"obs.npc_id {obs.npc_id} != memory.npc_id {self._npc_id}",
            )
        self._obs.append(obs)

    def add_reflection(self, refl: Reflection) -> None:
        if refl.npc_id != self._npc_id:
            raise ValueError(
                f"refl.npc_id {refl.npc_id} != memory.npc_id {self._npc_id}",
            )
        self._refl.append(refl)

    def add_plan(self, plan: Plan) -> None:
        if plan.npc_id != self._npc_id:
            raise ValueError(
                f"plan.npc_id {plan.npc_id} != memory.npc_id {self._npc_id}",
            )
        self._plans.append(plan)

    def update_plan_status(self, plan_id: str, status: PlanStatus) -> bool:
        """Met a jour le status d'un plan. Retourne True si trouve+modifie."""
        for i, p in enumerate(self._plans):
            if p.id == plan_id:
                self._plans[i] = p.model_copy(update={"status": status})
                return True
        return False

    def all_entries(self) -> list[MemoryEntry]:
        """Tous les types confondus, ordre de creation."""
        return [*self._obs, *self._refl, *self._plans]

    # --- retrieve ----------------------------------------------------------

    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
        now_ts: float | None = None,
        include_kinds: tuple[str, ...] = ("observation", "reflection", "plan"),
    ) -> list[tuple[float, MemoryEntry]]:
        """Top-k memories selon recency + importance + relevance.

        Retourne liste de tuples (score, entry) triee desc.
        """
        candidates: list[MemoryEntry] = []
        if "observation" in include_kinds:
            candidates.extend(self._obs)
        if "reflection" in include_kinds:
            candidates.extend(self._refl)
        if "plan" in include_kinds:
            candidates.extend(self._plans)

        if not candidates or top_k <= 0:
            return []

        scored: list[tuple[float, MemoryEntry]] = []
        for entry in candidates:
            s = composite_score(
                entry, query,
                now_ts=now_ts,
                weights=self._config.weights,
                decay=self._config.decay,
            )
            scored.append((s, entry))
        scored.sort(key=lambda t: t[0], reverse=True)
        return scored[:top_k]

    def retrieve_top_texts(
        self,
        query: str,
        *,
        top_k: int = 5,
        now_ts: float | None = None,
    ) -> list[str]:
        """Helper : top-k texts uniquement (pour prompt LLM)."""
        scored = self.retrieve(query, top_k=top_k, now_ts=now_ts)
        return [e.text for _s, e in scored]

    # --- helpers ------------------------------------------------------------

    def active_plans(self) -> list[Plan]:
        """Plans pas encore completes ni abandonnes."""
        return [
            p for p in self._plans
            if p.status in (PlanStatus.pending, PlanStatus.in_progress)
        ]

    def filter_by_year(
        self, *, year_min: int | None = None, year_max: int | None = None,
    ) -> list[MemoryEntry]:
        out: list[MemoryEntry] = []
        for entry in self.all_entries():
            year = getattr(entry, "year", None)
            if year is None:
                # Pour un Plan, year_started fait foi
                year = getattr(entry, "year_started", None)
            if year is None:
                continue
            if year_min is not None and year < year_min:
                continue
            if year_max is not None and year > year_max:
                continue
            out.append(entry)
        return out


__all__ = [
    "DEFAULT_RECENCY_DECAY",
    "DEFAULT_WEIGHTS",
    "AgentMemory",
    "RetrievalConfig",
    "composite_score",
    "jaccard_similarity",
    "recency_score",
    "relevance_score",
]
