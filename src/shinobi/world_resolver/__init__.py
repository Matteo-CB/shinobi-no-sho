"""Phase F - Boucle creative fermee WorldResolver.

Spec doc 02 §8 : extension du WorldResolver pour generer des
SubstituteEvent structures (pas juste du texte) quand un event canon
est annule, valider via mode hybride (canon_strict ou alternate_timeline)
et reinjecter dans le scheduler + KG.

Composants :
- types.py     : SubstituteEvent, ValidationMode, ValidationReport, ...
- schema.py    : JSON schema strict pour le LLM
- prompts.py   : SUBSTITUTE_EVENT_SYSTEM_PROMPT
- generator.py : SubstituteEventGenerator (LLM call + Pydantic round-trip)
- validator.py : HybridSubstituteValidator (canon_strict + alternate_timeline)
- injector.py  : SubstituteEventInjector (world.scheduled_events + KG facts)
- pipeline.py  : WorldResolverPipeline (orchestration complete)
"""

from __future__ import annotations

from shinobi.world_resolver.context import (
    DEFAULT_DIVERGENT_THRESHOLD,
    DEFAULT_KG_FACTS_LIMIT,
    build_kg_recent_facts,
    build_world_state_summary,
    select_validation_mode,
)
from shinobi.world_resolver.generator import (
    GenerationFailure,
    SubstituteEventGenerator,
)
from shinobi.world_resolver.injector import (
    InjectionResult,
    SubstituteEventInjector,
)
from shinobi.world_resolver.pipeline import (
    DEFAULT_MAX_REGEN_ATTEMPTS,
    WorldResolverPipeline,
    silent_cancel_resolution,
)
from shinobi.world_resolver.types import (
    SubstituteEvent,
    SubstituteOutcome,
    SubstitutePrecondition,
    SubstituteResolution,
    ValidationMode,
    ValidationOutcome,
    ValidationReport,
)
from shinobi.world_resolver.validator import HybridSubstituteValidator

__all__ = [
    "DEFAULT_DIVERGENT_THRESHOLD",
    "DEFAULT_KG_FACTS_LIMIT",
    "DEFAULT_MAX_REGEN_ATTEMPTS",
    "GenerationFailure",
    "HybridSubstituteValidator",
    "InjectionResult",
    "SubstituteEvent",
    "SubstituteEventGenerator",
    "SubstituteEventInjector",
    "SubstituteOutcome",
    "SubstitutePrecondition",
    "SubstituteResolution",
    "ValidationMode",
    "ValidationOutcome",
    "ValidationReport",
    "WorldResolverPipeline",
    "build_kg_recent_facts",
    "build_world_state_summary",
    "select_validation_mode",
    "silent_cancel_resolution",
]
