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
from shinobi.agents.batch_selector import (
    BATCH_ACTIONS_JSON_SCHEMA,
    BATCH_SYSTEM_PROMPT,
    BatchActionSelector,
    build_batch_user_prompt,
)
from shinobi.agents.cache import LLMCache, compute_cache_key
from shinobi.agents.context_builder import (
    auto_fill_selection_context,
    build_relations_summary_for_npc,
    build_world_summary_for_npc,
)
from shinobi.agents.embeddings_index import (
    EmbeddingsIndex,
    cosine_similarity,
)
from shinobi.agents.kg_bridge import (
    SECRET_ACTION_TYPES,
    action_to_fact,
    collect_witness_observations,
    push_action_to_kg,
    push_actions_to_kg_batch,
    witness_observation,
)
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
    is_trivial_state,
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
    "BATCH_ACTIONS_JSON_SCHEMA",
    "BATCH_SYSTEM_PROMPT",
    "DEFAULT_RECENCY_DECAY",
    "DEFAULT_SECONDARY_50",
    "DEFAULT_SYSTEM_PROMPT",
    "DEFAULT_TOP_15",
    "DEFAULT_WEIGHTS",
    "REFLECTOR_SYSTEM_PROMPT",
    "REFLECT_JSON_SCHEMA",
    "SECRET_ACTION_TYPES",
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
    "BatchActionSelector",
    "DigestEntry",
    "EmbeddingsIndex",
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
    "action_to_fact",
    "auto_fill_selection_context",
    "build_batch_user_prompt",
    "build_reflect_prompt",
    "build_relations_summary_for_npc",
    "build_user_prompt",
    "build_world_summary_for_npc",
    "collect_witness_observations",
    "composite_score",
    "compute_cache_key",
    "cosine_similarity",
    "deterministic_fallback_action",
    "deterministic_fallback_reflections",
    "initialize_roster",
    "is_trivial_action",
    "is_trivial_state",
    "jaccard_similarity",
    "push_action_to_kg",
    "push_actions_to_kg_batch",
    "recency_score",
    "relevance_score",
    "witness_observation",
]
