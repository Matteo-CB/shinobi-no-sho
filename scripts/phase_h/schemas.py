"""Pydantic schemas pour valider les outputs Phase H.

Chaque dataset a son schema. Si l'LLM produit du JSON non-conforme,
Pydantic raise -> retry ou skip cette entry. Le validator refuse
implicitement les hallucinations (champs requis manquants, types
invalides).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# --- 9.1 Timeline events enrichis -------------------------------------------


class StructuredFact(BaseModel):
    """Un fact structure (precondition ou outcome)."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    fact: str = Field(..., min_length=3, max_length=100)
    # Accepte aussi list (LLM produit parfois des arrays pour multi-valued
    # facts, e.g. 'team_7.members': ['naruto', 'sasuke', 'sakura']).
    value: str | int | bool | list | None = None


class EnrichedTimelineEvent(BaseModel):
    """Spec doc 02 §9.1 : event canon enrichi pour Phase F validator."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(..., min_length=3, max_length=80)
    year: int = Field(..., ge=-2000, le=200)
    name_fr: str = Field(..., min_length=5, max_length=120)
    preconditions: list[StructuredFact] = Field(
        default_factory=list, max_length=15,
    )
    outcomes: list[StructuredFact] = Field(..., min_length=1, max_length=15)
    narrative_invariants: list[str] = Field(
        default_factory=list, max_length=10,
    )
    alternative_seeds: list[str] = Field(
        default_factory=list, max_length=10,
    )


# --- 9.2 Motivations top-50 PNJ ---------------------------------------------


class DeepMotivations(BaseModel):
    """Spec doc 02 §9.2 : profil psycho profond."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Bounds elargies apres run pilote : LLM produit parfois des descriptions
    # nuancees > 100 chars. 250 chars laisse marge.
    primary: str = Field(..., min_length=3, max_length=250)
    secondary: str | None = Field(default=None, max_length=250)
    tertiary: str | None = Field(default=None, max_length=250)


class CharacterDeepProfile(BaseModel):
    """Profil deep d'un PNJ. Cite par Phase E agents et Phase D drift."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(..., min_length=3, max_length=80)
    deep_motivations: DeepMotivations
    moral_red_lines: list[str] = Field(
        default_factory=list, max_length=10,
    )
    secret_ambitions: list[str] = Field(
        default_factory=list, max_length=10,
    )
    deepest_fear: str = Field(..., min_length=5, max_length=300)
    self_image: str = Field(..., min_length=3, max_length=250)
    what_others_dont_know: list[str] = Field(
        default_factory=list, max_length=10,
    )


# --- 9.3 Forces politiques ---------------------------------------------------


class PoliticalFaction(BaseModel):
    """Une faction politique avec ses leaders, alliances, tensions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(..., min_length=3, max_length=80)
    name_fr: str = Field(..., min_length=3, max_length=120)
    type: Literal[
        "village", "clan", "organization", "country", "guild", "rebellion",
    ]
    leader_id: str | None = Field(default=None, max_length=80)
    members: list[str] = Field(default_factory=list, max_length=30)
    allies: list[str] = Field(default_factory=list, max_length=20)
    enemies: list[str] = Field(default_factory=list, max_length=20)
    active_year_start: int = Field(..., ge=-2000, le=200)
    active_year_end: int | None = None
    description_fr: str = Field(..., min_length=20, max_length=500)


class PoliticalForcesDataset(BaseModel):
    """Cartographie complete des factions et tensions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    factions: list[PoliticalFaction] = Field(..., min_length=5, max_length=80)


# --- 9.4 Moments charnieres --------------------------------------------------


class DivergencePoint(BaseModel):
    """Un moment canon dont l'altération produit cascade massive."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str = Field(..., min_length=3, max_length=80)
    year: int = Field(..., ge=-2000, le=200)
    name_fr: str = Field(..., min_length=5, max_length=120)
    cascade_severity: Literal["high", "very_high", "fundamental"]
    why_pivotal_fr: str = Field(..., min_length=20, max_length=500)
    if_altered_consequences: list[str] = Field(
        ..., min_length=2, max_length=10,
    )


class DivergencePointsDataset(BaseModel):
    """Index des moments charnieres canon."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    divergence_points: list[DivergencePoint] = Field(
        ..., min_length=10, max_length=40,
    )


# --- 9.5 Patterns Kishimoto --------------------------------------------------


class KishimotoPattern(BaseModel):
    """Un pattern d'ecriture recurrent identifie."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(..., min_length=3, max_length=80)
    title_fr: str = Field(..., min_length=5, max_length=100)
    description_fr: str = Field(..., min_length=50, max_length=600)
    canon_examples: list[str] = Field(..., min_length=2, max_length=8)
    when_to_apply_fr: str = Field(..., min_length=20, max_length=300)


class KishimotoPatternsDataset(BaseModel):
    """Style guide patterns Kishimoto pour le narrator LLM."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    patterns: list[KishimotoPattern] = Field(..., min_length=5, max_length=20)


__all__ = [
    "CharacterDeepProfile",
    "DeepMotivations",
    "DivergencePoint",
    "DivergencePointsDataset",
    "EnrichedTimelineEvent",
    "KishimotoPattern",
    "KishimotoPatternsDataset",
    "PoliticalFaction",
    "PoliticalForcesDataset",
    "StructuredFact",
]
