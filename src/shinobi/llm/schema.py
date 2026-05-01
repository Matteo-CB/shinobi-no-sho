"""Schemas JSON pour les sorties structurees du LLM."""

from __future__ import annotations

from typing import Any

NARRATOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["narrative", "proposed_actions"],
    "additionalProperties": False,
    "properties": {
        "narrative": {"type": "string", "minLength": 30},
        "npc_dialogue": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["character_id", "line"],
                "additionalProperties": False,
                "properties": {
                    "character_id": {"type": "string"},
                    "line": {"type": "string"},
                    "tone": {"type": "string"},
                },
            },
        },
        "proposed_actions": {
            "type": "array",
            "minItems": 3,
            "maxItems": 7,
            "items": {
                "type": "object",
                "required": ["label_fr", "action_type"],
                "additionalProperties": False,
                "properties": {
                    "label_fr": {"type": "string"},
                    "action_type": {"type": "string"},
                    "parameters": {"type": "object"},
                    "estimated_difficulty": {"type": "string"},
                    "estimated_duration": {"type": "string"},
                },
            },
        },
        "world_observations": {
            "type": "array",
            "items": {"type": "string"},
        },
        "clarification_request": {"type": "string"},
    },
}


GOAL_PATHFINDER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["sources_of_information"],
    "properties": {
        "interpretation": {"type": "string"},
        "sources_of_information": {
            "type": "array",
            "minItems": 1,
            "maxItems": 3,
            "items": {
                "type": "object",
                "required": [
                    "source_type",
                    "source_description",
                    "price",
                    "indice_unlocked",
                ],
                "properties": {
                    "source_type": {
                        "enum": ["npc", "location", "scroll", "rumor_mill", "self_research"],
                    },
                    "source_id": {"type": "string"},
                    "source_description": {"type": "string"},
                    "price": {
                        "type": "object",
                        "required": ["type", "description"],
                        "properties": {
                            "type": {
                                "enum": [
                                    "money",
                                    "favor",
                                    "sub_mission",
                                    "reputation",
                                    "secret",
                                    "physical",
                                    "moral",
                                    "political",
                                    "none",
                                ],
                            },
                            "amount": {"type": "number"},
                            "description": {"type": "string"},
                        },
                    },
                    "indice_unlocked": {
                        "type": "object",
                        "required": ["description", "completion_conditions"],
                        "properties": {
                            "description": {"type": "string"},
                            "completion_conditions": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "required": ["type", "parameters"],
                                    "properties": {
                                        "type": {"type": "string"},
                                        "parameters": {"type": "object"},
                                    },
                                },
                            },
                        },
                    },
                },
            },
        },
    },
}


CHARACTER_INTERPRETER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["intention"],
    "properties": {
        "intention": {
            "type": "object",
            "required": ["action_type", "summary"],
            "properties": {
                "action_type": {"type": "string"},
                "summary": {"type": "string"},
                "parameters": {"type": "object"},
                "target_id": {"type": "string"},
            },
        },
        "clarification_questions": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
}


WORLD_RESOLVER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["substitute_event_summary", "consequences"],
    "properties": {
        "substitute_event_summary": {"type": "string"},
        "consequences": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["type", "description"],
                "properties": {
                    "type": {"type": "string"},
                    "description": {"type": "string"},
                    "subject_id": {"type": "string"},
                },
            },
        },
        "rumor_template": {"type": "string"},
    },
}
