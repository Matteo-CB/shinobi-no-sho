"""Phase D : Personnalite vectorielle evolutive + drift par events vecus.

Modules :
- dimensions.py     : 20 dimensions canoniques (PersonalityDimension)
- types.py          : NPCPersonality, ExperiencedEvent, PersonalityDrift, EventCategory
- drift_rules.py    : 30 regles de drift deterministes + saturation sigmoid
- engine.py         : PersonalityEngine - apply_event, divergence
- baseline.py       : extraction baseline depuis psycho_notes.json
- store.py          : PersonalityStore - persistance SQLite per-save

Voir docs/02-PROJET-ROADMAP-SUITE.md §6.2 et Phase D.
"""

from __future__ import annotations

from shinobi.personality.baseline import (
    BaselineExtractionResult,
    extract_baseline_for_npc,
    extract_baseline_from_character,
    extract_baseline_from_text,
    extract_baselines_combined,
    extract_baselines_from_file,
)
from shinobi.personality.dimensions import (
    ALL_DIMENSIONS,
    DEFAULT_NEUTRAL_VALUE,
    PersonalityDimension,
    dimension_index,
)
from shinobi.personality.drift_rules import (
    DRIFT_RULES,
    DriftRule,
    apply_delta_with_saturation,
    compose_drift_for_event,
    get_rule_for_category,
)
from shinobi.personality.engine import (
    PersonalityEngine,
    PersonalityEngineError,
)
from shinobi.personality.event_bridge import (
    CanonEventLike,
    MissionLike,
    collect_experienced_events,
    detect_category_from_text,
    experienced_events_from_mission,
    experienced_events_from_timeline_event,
)
from shinobi.personality.store import PersonalityStore
from shinobi.personality.types import (
    EventCategory,
    ExperiencedEvent,
    NPCPersonality,
    PersonalityDrift,
)

__all__ = [
    "ALL_DIMENSIONS",
    "DEFAULT_NEUTRAL_VALUE",
    "DRIFT_RULES",
    "BaselineExtractionResult",
    "CanonEventLike",
    "DriftRule",
    "EventCategory",
    "ExperiencedEvent",
    "MissionLike",
    "NPCPersonality",
    "PersonalityDimension",
    "PersonalityDrift",
    "PersonalityEngine",
    "PersonalityEngineError",
    "PersonalityStore",
    "apply_delta_with_saturation",
    "collect_experienced_events",
    "compose_drift_for_event",
    "detect_category_from_text",
    "dimension_index",
    "experienced_events_from_mission",
    "experienced_events_from_timeline_event",
    "extract_baseline_for_npc",
    "extract_baseline_from_character",
    "extract_baseline_from_text",
    "extract_baselines_combined",
    "extract_baselines_from_file",
    "get_rule_for_category",
]
