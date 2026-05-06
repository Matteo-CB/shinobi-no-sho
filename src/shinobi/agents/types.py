"""Types Pydantic frozen pour la simulation multi-agent Phase E.

Spec docs/02 §6.1 (Generative Agents Park et al, Stanford 2023) :

> Memoire a 3 niveaux :
>   observations : tous les faits percus
>   reflections : syntheses periodiques
>   plans : intentions court/long terme

Chaque memoire entry a :
- text : description en langage naturel
- year (in-game)
- importance : 0.0 - 1.0 (utilise par retrieval scoring)
- created_at_ts : timestamp reel (pour recency decay)
- npc_id : a qui appartient le souvenir

Le retrieval pattern (Park et al) : score = recency * importance * relevance
ou recency = exp(-decay * (now - created_at_ts)).
"""

from __future__ import annotations

import time
import uuid
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AgentTier(StrEnum):
    """Niveau de simulation d'un PNJ.

    docs/02 §6.4 :
    - major : top-15, simulation active a chaque tick (1 inference / agent)
    - secondary : ~50 PNJ, simulation par lot toutes les 10 ticks
    - background : tous les autres, comportement canon par defaut, eleves au
      statut d'agent uniquement si interaction joueur ou impact event majeur
    """

    major = "major"
    secondary = "secondary"
    background = "background"


# -- Memory entries -----------------------------------------------------------


class Observation(BaseModel):
    """Fait percu par un agent (niveau 1 de la memoire 3-tiers).

    Source : action joueur, action d'un autre agent, fact KG nouvellement
    visible, evenement canon firing, rumeur recue.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(default_factory=lambda: f"obs_{uuid.uuid4().hex[:12]}")
    npc_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    year: int
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    created_at_ts: float = Field(default_factory=time.time)
    source_npc_id: str | None = None
    related_event_id: str | None = None
    related_mission_id: str | None = None
    related_fact_id: int | None = None
    location_id: str | None = None
    kind: Literal["observation"] = "observation"


class Reflection(BaseModel):
    """Synthese periodique (niveau 2 de la memoire 3-tiers).

    Produite par le `Reflector` LLM toutes les N observations. Distillation
    d'une serie d'obs en une insight de plus haut niveau.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(default_factory=lambda: f"refl_{uuid.uuid4().hex[:12]}")
    npc_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    year: int
    importance: float = Field(default=0.7, ge=0.0, le=1.0)
    created_at_ts: float = Field(default_factory=time.time)
    source_observation_ids: tuple[str, ...] = Field(default_factory=tuple)
    gist: str = ""  # 1-line summary
    kind: Literal["reflection"] = "reflection"


class PlanStatus(StrEnum):
    """Etat d'un plan."""

    pending = "pending"
    in_progress = "in_progress"
    completed = "completed"
    abandoned = "abandoned"


class Plan(BaseModel):
    """Intention court/long terme (niveau 3 de la memoire 3-tiers)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(default_factory=lambda: f"plan_{uuid.uuid4().hex[:12]}")
    npc_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    year_started: int
    year_target: int | None = None
    priority: float = Field(default=0.5, ge=0.0, le=1.0)
    status: PlanStatus = PlanStatus.pending
    importance: float = Field(default=0.6, ge=0.0, le=1.0)
    created_at_ts: float = Field(default_factory=time.time)
    related_npc_ids: tuple[str, ...] = Field(default_factory=tuple)
    kind: Literal["plan"] = "plan"


# Type alias pour memoire generique
MemoryEntry = Observation | Reflection | Plan


# -- Roster -------------------------------------------------------------------


class RosterEntry(BaseModel):
    """Une entree dans le roster d'agents."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    npc_id: str = Field(min_length=1)
    tier: AgentTier
    included_since_year: int | None = None
    last_active_year: int | None = None
    last_active_tick: int | None = None
    notes: str = ""


# -- Digest fast-forward ------------------------------------------------------


class DigestEntry(BaseModel):
    """Une entree du digest produit par fast-forward."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    year: int
    headline: str = Field(min_length=1)
    npc_ids: tuple[str, ...] = Field(default_factory=tuple)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    related_event_id: str | None = None
    location_id: str | None = None


class FastForwardDigest(BaseModel):
    """Digest produit par TickEngine.fast_forward()."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    from_year: int
    to_year: int
    months_simulated: int
    ticks_simulated: int
    entries: tuple[DigestEntry, ...] = Field(default_factory=tuple)
    npcs_active: tuple[str, ...] = Field(default_factory=tuple)
    actions_total: int = 0
    cache_hit_rate: float = 0.0


__all__ = [
    "AgentTier",
    "DigestEntry",
    "FastForwardDigest",
    "MemoryEntry",
    "Observation",
    "Plan",
    "PlanStatus",
    "Reflection",
    "RosterEntry",
]
