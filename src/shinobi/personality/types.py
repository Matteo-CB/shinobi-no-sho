"""Types Pydantic frozen pour la personnalite vectorielle Phase D.

Trois objets cles :

- `NPCPersonality` : vecteur 20 dim courant + canon_baseline + drift_history.
  Le baseline est snapshote au temps T0 du personnage (extrait de psycho_notes).
  Le vecteur courant evolue par application d'evenements.

- `PersonalityDrift` : enregistrement immuable d'un drift applique. Permet
  de tracer la chaine causale ('a cet event, ces dimensions ont bouge').

- `ExperiencedEvent` : input des drift rules. Decrit ce qu'a vecu le PNJ.
  Pas le meme objet que TimelineEvent (canon) ni Mission (canon) : ici c'est
  une experience subjective vecue, parfois dynamique (combat gagne contre X,
  trahison de Y), parfois mappee depuis le canon (mort canon de Y).

Tous les objets sont Pydantic v2 frozen (immuabilite stricte) avec
ConfigDict(extra='forbid').
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shinobi.personality.dimensions import (
    ALL_DIMENSIONS,
    DEFAULT_NEUTRAL_VALUE,
    PersonalityDimension,
)


class EventCategory(StrEnum):
    """Categories d'evenements vecus reconnues par les drift rules.

    Chaque categorie est mappee a UNE rule (ou parfois deux). Les rules
    sont 'generic psycho' selon docs/02 §6.2 : pas 'massacre Uchiha' mais
    'witnessed_atrocity_against_self_clan'.
    """

    trauma_event = "trauma_event"
    betrayal_witnessed = "betrayal_witnessed"
    long_term_companionship = "long_term_companionship"
    violent_combat_won = "violent_combat_won"
    violent_combat_lost = "violent_combat_lost"
    mentor_lost = "mentor_lost"
    lover_lost = "lover_lost"
    parent_lost = "parent_lost"
    sibling_lost = "sibling_lost"
    rescued_by = "rescued_by"
    witnessed_atrocity = "witnessed_atrocity"
    achieved_goal = "achieved_goal"
    failed_goal = "failed_goal"
    rank_promotion = "rank_promotion"
    rank_demotion = "rank_demotion"
    mass_killing_committed = "mass_killing_committed"
    protected_innocent = "protected_innocent"
    massacre_against_self_clan = "massacre_against_self_clan"
    long_isolation = "long_isolation"
    reconciliation = "reconciliation"
    leadership_burden_taken = "leadership_burden_taken"
    lover_gained = "lover_gained"
    friendship_deepened = "friendship_deepened"
    prophecy_received = "prophecy_received"
    jutsu_mastery_milestone = "jutsu_mastery_milestone"
    clan_destroyed = "clan_destroyed"
    secret_revealed_about_self = "secret_revealed_about_self"
    secret_kept_long_term = "secret_kept_long_term"
    daily_routine_long = "daily_routine_long"
    peer_outpaced = "peer_outpaced"
    public_humiliation = "public_humiliation"


class ExperiencedEvent(BaseModel):
    """Un evenement vecu subjectivement par un PNJ, input des drift rules.

    Champs :
    - npc_id : pour qui ?
    - category : type d'evenement (mappe une drift rule)
    - year : an in-game ou l'evenement s'est produit
    - intensity : 0.0 - 1.0, scale generique du drift (default 1.0).
      Permet d'avoir 'trauma leger' (0.3) vs 'trauma traumatique' (1.0).
    - related_npc_id : autre PNJ implique (betrayer, rescuer, mentor, etc.)
    - related_event_id : event canon associe (mass_killing -> event_id)
    - related_mission_id : mission associee
    - duration_years : pour les rules cumulatives (long_term_companionship)
    - notes : libre, log uniquement
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    npc_id: str = Field(min_length=1)
    category: EventCategory
    year: int
    intensity: float = Field(default=1.0, ge=0.0, le=1.0)
    related_npc_id: str | None = None
    related_event_id: str | None = None
    related_mission_id: str | None = None
    duration_years: int | None = Field(default=None, ge=0)
    notes: str = ""


class PersonalityDrift(BaseModel):
    """Enregistrement immuable d'un drift applique (audit trail).

    On y trouve :
    - delta : variations brutes par dimension AVANT saturation sigmoid
    - applied_delta : variations effectivement appliquees APRES saturation
    - rule_name : quelle drift rule a ete declenchee
    - event_id : reference a l'ExperiencedEvent traitant
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(default_factory=lambda: f"drift_{uuid.uuid4().hex[:12]}")
    npc_id: str = Field(min_length=1)
    rule_name: str = Field(min_length=1)
    year: int
    delta: dict[PersonalityDimension, float] = Field(default_factory=dict)
    applied_delta: dict[PersonalityDimension, float] = Field(default_factory=dict)
    event_category: EventCategory
    related_npc_id: str | None = None
    related_event_id: str | None = None
    related_mission_id: str | None = None
    applied_at_ts: float = Field(default_factory=lambda: datetime.now().timestamp())
    notes: str = ""


def _default_canon_baseline() -> dict[PersonalityDimension, float]:
    """Vecteur baseline neutre (toutes dimensions a 0.5)."""
    return dict.fromkeys(ALL_DIMENSIONS, DEFAULT_NEUTRAL_VALUE)


class NPCPersonality(BaseModel):
    """Vecteur de personnalite courant d'un PNJ + son baseline canon + son
    historique de drift.

    Invariants :
    - vector contient EXACTEMENT les 20 dimensions
    - chaque valeur est dans [0.0, 1.0]
    - canon_baseline a la meme structure
    - drift_history est append-only (pas modifie en place)
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    npc_id: str = Field(min_length=1)
    vector: dict[PersonalityDimension, float] = Field(
        default_factory=_default_canon_baseline,
    )
    canon_baseline: dict[PersonalityDimension, float] = Field(
        default_factory=_default_canon_baseline,
    )
    drift_history: tuple[PersonalityDrift, ...] = Field(default_factory=tuple)
    baseline_year: int | None = None

    @model_validator(mode="after")
    def _check_dimensions_complete(self) -> NPCPersonality:
        """Garantit que vector et canon_baseline contiennent les 20 dimensions
        et que toutes les valeurs sont dans [0.0, 1.0]."""
        for label, mapping in (("vector", self.vector), ("canon_baseline", self.canon_baseline)):
            missing = set(ALL_DIMENSIONS) - set(mapping.keys())
            if missing:
                raise ValueError(
                    f"{label} : dimensions manquantes : {sorted(d.value for d in missing)}",
                )
            for dim, val in mapping.items():
                if not (0.0 <= val <= 1.0):
                    raise ValueError(
                        f"{label}[{dim}]={val} hors [0.0, 1.0]",
                    )
        return self

    def divergence_from_canon(self) -> float:
        """Distance euclidienne L2 entre vector courant et canon_baseline.

        Un PNJ qui a beaucoup drifte aura une divergence elevee. Permet
        au Director (Phase G) de detecter les PNJ qui s'eloignent du canon.
        """
        total = 0.0
        for dim in ALL_DIMENSIONS:
            d = self.vector[dim] - self.canon_baseline[dim]
            total += d * d
        return total ** 0.5

    def value(self, dim: PersonalityDimension) -> float:
        """Lecture rapide d'une dimension du vecteur courant."""
        return self.vector[dim]

    def baseline(self, dim: PersonalityDimension) -> float:
        """Lecture rapide d'une dimension du baseline canon."""
        return self.canon_baseline[dim]


__all__ = [
    "EventCategory",
    "ExperiencedEvent",
    "NPCPersonality",
    "PersonalityDrift",
]
