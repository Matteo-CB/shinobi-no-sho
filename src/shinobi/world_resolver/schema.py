"""JSON Schema strict pour la generation LLM Phase F.

Spec doc 02 §8.2 : le LLM doit produire un SubstituteEvent structure,
pas juste du texte. Schema force la coherence via grammaire JSON
constrained generation cote llama.cpp.
"""

from __future__ import annotations

from typing import Any

from shinobi.world_resolver.types import ALLOWED_CANCELLATION_STRATEGIES

# Spec doc 02 §8 : meme structure que TimelineEvent + cancelled_canon_event_id.
# Note : `id` doit commencer par 'substitute_' (regex enforced cote Pydantic
# round-trip). Le LLM peut ne pas respecter le regex -> on le force lors du
# parsing en prefixant manuellement.
SUBSTITUTE_EVENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "id_suffix", "name_fr", "year",
        "narrative_summary_fr", "outcomes",
    ],
    "properties": {
        "id_suffix": {
            "type": "string",
            "description": (
                "Suffixe id court (ex: 'fugaku_negociation_year9'). "
                "Le prefixe 'substitute_' est ajoute par le code."
            ),
            "minLength": 3,
            "maxLength": 60,
        },
        "name_fr": {"type": "string", "minLength": 3},
        # Round 22 : bornes alignees avec validator._check_year (HybridSubstituteValidator).
        # Avant, schema acceptait tout integer -> LLM pouvait produire year=9999,
        # validator rejetait, on brulait une regen pour une contrainte connue
        # cote schema.
        "year": {"type": "integer", "minimum": -1000, "maximum": 200},
        # Round 40 : format MM-DD strict (parite avec Pydantic + engine
        # _date_reached qui compare comme string contre world.current_date).
        # Round 49 : MM in [01-12] et DD in [01-31] (tighten le naive
        # \d{2}-\d{2} qui acceptait '13-99' jamais atteignable).
        "date": {
            "type": ["string", "null"],
            "pattern": r"^(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])$",
        },
        "location": {"type": ["string", "null"]},
        "involved_characters": {
            "type": "array",
            # Round 66 : maxItems aligne avec Pydantic (canon max=6, marge x2.5).
            "maxItems": 15,
            "items": {"type": "string"},
        },
        "preconditions": {
            "type": "array",
            # Round 66 : maxItems=10 (canon max=4).
            "maxItems": 10,
            "items": {
                "type": "object",
                "required": ["type"],
                "properties": {
                    # Round 37 : enum aligne avec validator round 34 et engine
                    # evaluate_precondition. Avant : `string` libre, le LLM
                    # pouvait produire un type inconnu qui passait le schema
                    # mais que le validator rejetait apres -> regen gachee.
                    "type": {
                        "type": "string",
                        "enum": [
                            "character_alive",
                            "no_event_triggered",
                            "clan_active",
                            "jinchuuriki_held_by",
                        ],
                    },
                    "parameters": {"type": "object"},
                },
            },
        },
        "outcomes": {
            "type": "array",
            "minItems": 1,
            # Round 66 : maxItems=10 aligne avec Pydantic (canon max=5).
            "maxItems": 10,
            "items": {
                "type": "object",
                "required": ["type"],
                "properties": {
                    # Round 57 : minLength=1 (parite Pydantic R39). Avant,
                    # type='' passait le schema (key present satisfait
                    # `required`), generator filtrait apres -> outcomes
                    # vide apres parsing -> regen brulee.
                    "type": {"type": "string", "minLength": 1},
                    "parameters": {"type": "object"},
                },
            },
        },
        "narrative_summary_fr": {"type": "string", "minLength": 20},
        "cancellation_strategy_type": {
            "type": "string",
            # Round 59 : single source of truth depuis types.py
            "enum": list(ALLOWED_CANCELLATION_STRATEGIES),
        },
        "rumor_template": {"type": ["string", "null"]},
    },
}


__all__ = ["SUBSTITUTE_EVENT_SCHEMA"]
