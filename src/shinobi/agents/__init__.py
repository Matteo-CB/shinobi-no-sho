"""Phase E : Multi-agent simulation top-15 (Generative Agents Park et al).

Spec docs/02 §6 + §13 Phase E :
- Memoire 3-niveaux (observations, reflections, plans)
- Action selection LLM-driven sous contraintes JSON
- Tick autonome + fast-forward N mois -> digest
- Caching agressif (LLMCache disque) - spec §11.2
- Top-15 actifs / tick + secondary 50 / 10 ticks + background dynamique

Modules :
- types.py        : Pydantic frozen (Observation, Reflection, Plan,
                    AgentTier, RosterEntry, FastForwardDigest)
- memory.py       : AgentMemory + retrieval Park (recency+importance+relevance)
- action_space.py : 7 types + JSON schema constrained
- selector.py     : ActionSelector LLM-driven (cache + fallback deterministe)
- reflector.py    : Reflector synthese N obs -> reflections
- cache.py        : LLMCache disk-backed SQLite (compute_cache_key SHA-256)
- store.py        : AgentMemoryStore SQLite per-save (5 tables)
- roster.py       : top-15 + secondary-50 + dynamique (promote/demote)
- agent.py        : MajorAgent (perceive + reflect + act async)
- tick.py         : TickEngine (tick + fast_forward + digest)
"""

from __future__ import annotations

from shinobi.agents.action_space import (
    AGENT_ACTION_JSON_SCHEMA,
    TRIVIAL_ACTION_TYPES,
    AgentAction,
    AgentActionType,
    is_trivial_action,
)
from shinobi.agents.agent import (
    AgentTickInputs,
    AgentTickResult,
    MajorAgent,
)
from shinobi.agents.cache import LLMCache, compute_cache_key
from shinobi.agents.memory import (
    DEFAULT_RECENCY_DECAY,
    DEFAULT_WEIGHTS,
    AgentMemory,
    RetrievalConfig,
    composite_score,
    jaccard_similarity,
    recency_score,
    relevance_score,
)
from shinobi.agents.reflector import (
    REFLECT_JSON_SCHEMA,
    REFLECTOR_SYSTEM_PROMPT,
    Reflector,
    build_reflect_prompt,
    deterministic_fallback_reflections,
)
from shinobi.agents.roster import (
    DEFAULT_SECONDARY_50,
    DEFAULT_TOP_15,
    AgentRoster,
    initialize_roster,
)
from shinobi.agents.selector import (
    DEFAULT_SYSTEM_PROMPT,
    ActionSelector,
    LLMCall,
    SelectionContext,
    build_user_prompt,
    deterministic_fallback_action,
)
from shinobi.agents.store import AgentMemoryStore
from shinobi.agents.tick import TickContextProvider, TickEngine
from shinobi.agents.types import (
    AgentTier,
    DigestEntry,
    FastForwardDigest,
    MemoryEntry,
    Observation,
    Plan,
    PlanStatus,
    Reflection,
    RosterEntry,
)

__all__ = [
    "AGENT_ACTION_JSON_SCHEMA",
    "DEFAULT_RECENCY_DECAY",
    "DEFAULT_SECONDARY_50",
    "DEFAULT_SYSTEM_PROMPT",
    "DEFAULT_TOP_15",
    "DEFAULT_WEIGHTS",
    "REFLECTOR_SYSTEM_PROMPT",
    "REFLECT_JSON_SCHEMA",
    "TRIVIAL_ACTION_TYPES",
    "ActionSelector",
    "AgentAction",
    "AgentActionType",
    "AgentMemory",
    "AgentMemoryStore",
    "AgentRoster",
    "AgentTickInputs",
    "AgentTickResult",
    "AgentTier",
    "DigestEntry",
    "FastForwardDigest",
    "LLMCache",
    "LLMCall",
    "MajorAgent",
    "MemoryEntry",
    "Observation",
    "Plan",
    "PlanStatus",
    "Reflection",
    "Reflector",
    "RetrievalConfig",
    "RosterEntry",
    "SelectionContext",
    "TickContextProvider",
    "TickEngine",
    "build_reflect_prompt",
    "build_user_prompt",
    "composite_score",
    "compute_cache_key",
    "deterministic_fallback_action",
    "deterministic_fallback_reflections",
    "initialize_roster",
    "is_trivial_action",
    "jaccard_similarity",
    "recency_score",
    "relevance_score",
]
