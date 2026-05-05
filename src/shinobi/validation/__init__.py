"""Validator central du pipeline narrateur (pilier 3 du plan anti-hallucination).

Orchestre des couches de validation enchainees, short-circuit dès qu'une
couche reject. Couches du MVP : sherlock_rules (A), age_coherence (C).

Couches reportees :
- triplet_check (B) : pilier 6 (enums canon + structured generation)
- nli_check (D) : pilier 7 (verification selective)
- llm_judge (E) : pilier 7

Le Validator est l'endroit ou `shinobi.guards.output_filter.log_leakage_if_any`
sera appele une fois branche au pipeline narrateur (cf. regen_loop.py).
"""

from __future__ import annotations

from shinobi.validation.age_coherence import AgeCoherenceLayer
from shinobi.validation.regen_loop import format_violations_for_regen
from shinobi.validation.risk_tagger import (
    RiskLevel,
    RiskSegment,
    SegmentType,
    max_risk_in,
    required_layers_for_risk,
    tag_narrative_output,
)
from shinobi.validation.sherlock_rules import SherlockRulesLayer
from shinobi.validation.triplet_check import TripletCheckLayer
from shinobi.validation.validator import (
    NarrativeAction,
    NarrativeDialogue,
    NarrativeOutput,
    ValidationLayer,
    ValidationResult,
    Validator,
)

__all__ = [
    "AgeCoherenceLayer",
    "NarrativeAction",
    "NarrativeDialogue",
    "NarrativeOutput",
    "RiskLevel",
    "RiskSegment",
    "SegmentType",
    "SherlockRulesLayer",
    "TripletCheckLayer",
    "ValidationLayer",
    "ValidationResult",
    "Validator",
    "format_violations_for_regen",
    "max_risk_in",
    "required_layers_for_risk",
    "tag_narrative_output",
]
