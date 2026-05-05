"""Query rewriter : enrichit la query joueur avant retrieval / generation.

Pipeline :
1. classify_intent (guards.intent_classifier)
2. si in-universe / ambiguous : resolve_references (preprocessing.reference_resolver)
3. retourne un EnrichedQuery pret pour la suite du pipeline

Compatible avec un StateView absent (NullStateView par defaut). La couche
appelante peut fournir un StateView reel quand le pilier 4 sera disponible.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from shinobi.guards.intent_classifier import Intent, classify_intent
from shinobi.preprocessing.reference_resolver import (
    NullStateView,
    ResolutionResult,
    StateView,
    resolve_references,
)


@dataclass(frozen=True)
class EnrichedQuery:
    """Query joueur enrichie, prete pour retrieval ou generation."""

    raw: str
    intent: Intent
    intent_confidence: float
    rewritten: str
    blacklist_hits: tuple[str, ...]
    is_ambiguous: bool
    clarification_needed: str | None
    redirect_message: str | None  # message in-character si reject (out_of_universe)
    used_referents: dict[str, str] = field(default_factory=dict)


def rewrite_query(raw: str, state: StateView | None = None) -> EnrichedQuery:
    """Pipeline complet de pre-processing de la query joueur."""
    intent_result = classify_intent(raw)
    state = state or NullStateView()

    if intent_result.intent == Intent.out_of_universe:
        return EnrichedQuery(
            raw=raw,
            intent=intent_result.intent,
            intent_confidence=intent_result.confidence,
            rewritten=raw,
            blacklist_hits=intent_result.blacklist_hits,
            is_ambiguous=False,
            clarification_needed=None,
            redirect_message=intent_result.suggested_redirect,
        )

    if intent_result.intent == Intent.meta_command:
        return EnrichedQuery(
            raw=raw,
            intent=intent_result.intent,
            intent_confidence=intent_result.confidence,
            rewritten=raw,
            blacklist_hits=(),
            is_ambiguous=False,
            clarification_needed=None,
            redirect_message=None,
        )

    resolution: ResolutionResult = resolve_references(raw, state)
    return EnrichedQuery(
        raw=raw,
        intent=intent_result.intent,
        intent_confidence=intent_result.confidence,
        rewritten=resolution.rewritten,
        blacklist_hits=(),
        is_ambiguous=resolution.is_ambiguous,
        clarification_needed=resolution.clarification_needed,
        redirect_message=None,
        used_referents=resolution.used_referents,
    )
