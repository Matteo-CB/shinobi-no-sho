"""Espace d'actions des agents Phase E + JSON schema LLM-constrained.

docs/02 §6.3 :
> Il genere une action structuree parmi un espace contraint mais ouvert :
>   declarer une intention, parler, voyager, attaquer, chercher information,
>   mediter, comploter

7 categories explicites + 2 utilitaires (idle, custom). On laisse `params`
libre dict pour autoriser de la flexibilite, mais le `type` est enum strict.

Le JSON schema est utilise par llama.cpp grammar pour forcer le LLM a
produire UNIQUEMENT du JSON valide (anti-derive).
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AgentActionType(StrEnum):
    """7 types canon (spec §6.3) + 2 utilitaires."""

    declare_intention = "declare_intention"
    speak = "speak"
    travel = "travel"
    attack = "attack"
    search_information = "search_information"
    meditate = "meditate"
    plot = "plot"

    # Utilitaires : reduit la latence en evitant LLM pour cas triviaux
    idle = "idle"
    custom = "custom"


class AgentAction(BaseModel):
    """Action structuree produite par un agent.

    `target_npc_id` : pour speak/attack/plot
    `location_id` : pour travel
    `content` : libre (texte de la chose dite, pensee declaree, plan)
    `params` : extra dict (intent_type, jutsu_id, target_subject, etc.)
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(default_factory=lambda: f"act_{uuid.uuid4().hex[:12]}")
    npc_id: str = Field(min_length=1)
    type: AgentActionType
    year: int
    target_npc_id: str | None = None
    location_id: str | None = None
    content: str = ""
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    params: dict[str, Any] = Field(default_factory=dict)


# JSON schema pour grammar-constrained generation (llama.cpp / openai-compat)
AGENT_ACTION_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "type": {
            "type": "string",
            "enum": [t.value for t in AgentActionType],
        },
        "target_npc_id": {"type": ["string", "null"]},
        "location_id": {"type": ["string", "null"]},
        "content": {"type": "string", "maxLength": 500},
        "importance": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "params": {"type": "object"},
    },
    "required": ["type", "content"],
    "additionalProperties": False,
}


# Heuristique : actions "triviales" qui ne necessitent pas le LLM
TRIVIAL_ACTION_TYPES: frozenset[AgentActionType] = frozenset({
    AgentActionType.idle,
    AgentActionType.meditate,
    AgentActionType.travel,  # si destination connue dans plan
})


def is_trivial_action(action: AgentAction) -> bool:
    """True si l'action est triviale (cache hit possible / LLM skippable)."""
    return action.type in TRIVIAL_ACTION_TYPES


__all__ = [
    "AGENT_ACTION_JSON_SCHEMA",
    "TRIVIAL_ACTION_TYPES",
    "AgentAction",
    "AgentActionType",
    "is_trivial_action",
]
