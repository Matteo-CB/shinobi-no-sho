"""Types Phase F : boucle creative fermee WorldResolver.

Spec doc 02 §8 : quand un event canon est annule (precondition violee),
le WorldResolver genere un `SubstituteEvent` STRUCTURE (pas juste du
texte) qui peut etre injecte dans le scheduler comme un nouvel
evenement runtime.

Distinction avec `TimelineEvent` (canon, immutable, charge depuis JSON) :
- `SubstituteEvent` : runtime, derive d'un cancel canon, valide via
  hybrid validator, source = 'substitute:<canon_event_id>'.
- Le scheduler doit pouvoir le declencher via les memes mecanismes que
  les events canon (preconditions, outcomes, location, etc.).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Round 59 : single source of truth pour les strategies. Avant, valeurs
# duplicates dans types.py (Literal), schema.py (enum), generator.py (set)
# -> desync silencieux possible. Maintenant : tuple ici, schema.py importe,
# generator.py importe (converti en set).
ALLOWED_CANCELLATION_STRATEGIES: tuple[str, ...] = (
    "substitute", "cascade_cancel", "silent_cancel", "delay",
)


class ValidationMode(StrEnum):
    """Modes de validation pour les SubstituteEvent.

    Spec §8.3 :
    - canon_strict : check triplet exact (canonical_users contient l'actor)
    - alternate_timeline : check assoupli, plausibilite contextuelle via
      la chaine d'evenements vecus dans cette branche divergente.
    """

    canon_strict = "canon_strict"
    alternate_timeline = "alternate_timeline"


class ValidationOutcome(StrEnum):
    """Resultat d'une validation hybride."""

    valid = "valid"
    invalid_triplet = "invalid_triplet"  # canon_strict mode
    invalid_plausibility = "invalid_plausibility"  # alternate_timeline mode
    invalid_dead_character = "invalid_dead_character"
    invalid_temporal = "invalid_temporal"
    invalid_schema = "invalid_schema"
    # Round 45 : distinct de invalid_schema pour que le feedback regen LLM
    # ne dise pas "verifie ton JSON" alors que le JSON est valide mais le
    # contenu narratif viole le style guide (em dash, emoji).
    invalid_style = "invalid_style"


class SubstitutePrecondition(BaseModel):
    """Precondition d'un SubstituteEvent. Mirror EventPrecondition canon."""

    model_config = ConfigDict(frozen=True)

    # Round 39 : min_length=1 enforce. Avant, type='' passait Pydantic ;
    # le validator skip la whitelist (`if pre.type and ...`) et l'engine
    # retourne True (fall-through) -> precondition vide silencieusement
    # consideree comme satisfaite.
    type: str = Field(..., min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)


class SubstituteOutcome(BaseModel):
    """Outcome d'un SubstituteEvent. Mirror EventOutcome canon."""

    model_config = ConfigDict(frozen=True)

    # Round 39 : meme rationale que SubstitutePrecondition.type. Un outcome
    # type='' produirait un fact KG `outcome:` mal forme.
    type: str = Field(..., min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)


class SubstituteEvent(BaseModel):
    """Evenement substitut runtime injecte apres cancel canon.

    Spec §8.2 : meme structure que TimelineEvent canon, mais :
    - id prefixe `substitute_` pour distinction
    - source `substitute:<canon_event_id>` -> traceabilite
    - canonicity = 'divergent' (jamais canon_strict)
    - cancellation_strategy.type peut etre 'silent_cancel' / 'substitute' /
      'cascade_cancel' selon la nature
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(..., min_length=1, pattern=r"^substitute_[a-z0-9_]+$")
    # Round 36 : min_length=1 enforce. Avant, '' passait silencieusement et
    # injector emettait `source="substitute:"` (suffixe vide) -> tracabilite
    # KG cassee : impossible de retrouver le canon event d'origine.
    cancelled_canon_event_id: str = Field(..., min_length=1)
    name_fr: str = Field(..., min_length=3)
    year: int
    # Round 40 : format MM-DD strict. L'engine compare comme string contre
    # world.current_date ('MM-DD'). Une ISO 'YYYY-MM-DD' produirait une
    # comparaison lexicographique fausse -> _date_reached retourne False
    # indefiniment, substitute jamais trigger. None autorise (event a la
    # premiere date qui matche year).
    # Round 49 : tighten le pattern - R40 acceptait "13-99" ou "00-00"
    # (regex naive `\d{2}-\d{2}`). world.current_date ne depasse jamais
    # "12-31" donc une date "13-99" n'est jamais atteinte -> substitute
    # scheduled forever. Enforce MM=01-12 et DD=01-31.
    date: str | None = Field(
        default=None,
        pattern=r"^(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])$",
    )
    location: str | None = None
    # Round 66 : bornes superieures basees sur canon norm (involved max=6,
    # outcomes max=5, preconditions max=4) avec marge de securite. Sans cap,
    # un LLM derape pourrait produire 100 outcomes -> bloat KG / token blowup.
    involved_characters: list[str] = Field(default_factory=list, max_length=15)
    preconditions: list[SubstitutePrecondition] = Field(
        default_factory=list, max_length=10,
    )
    # Round 35 : min_length=1 enforce Pydantic, parite avec schema JSON minItems
    # et generator. Avant, construction directe avec outcomes=[] passait
    # silencieusement et produisait un substitute "vide".
    # Round 66 : max_length=10 (canon max=5).
    outcomes: list[SubstituteOutcome] = Field(..., min_length=1, max_length=10)
    # Round 61 : min_length=20 (etait 10) parite avec schema JSON. Canon
    # narratives sont ~99-229 chars ; 10-char narratives (ex 'aaaaaaaaaa')
    # passaient Pydantic mais auraient ete rejetes par le LLM schema, et se
    # propageaient en rumeur sans valeur narrative.
    narrative_summary_fr: str = Field(..., min_length=20)
    # Spec doc 02 §8.2 round 16 : enum constraint pour eviter que le LLM
    # produise une strategy inconnue qui ne serait pas geree par
    # tick_scheduler (delay/hard_cancel/cascade/substitute uniquement).
    cancellation_strategy_type: Literal[
        "substitute", "cascade_cancel", "silent_cancel", "delay",
    ] = "substitute"
    rumor_template: str | None = None

    @field_validator("involved_characters")
    @classmethod
    def _dedupe_involved_characters(cls, v: list[str]) -> list[str]:
        """Round 46 : dedupe avec ordre preserve.

        LLM peut repeter le meme character_id (emphase narrative). Sans
        dedup, l'injector emet N facts `(sub_id, involves, cid)` identiques,
        bloat le KG et duplique les queries.
        """
        return list(dict.fromkeys(v))


class ValidationReport(BaseModel):
    """Rapport detaille d'une validation hybride."""

    model_config = ConfigDict(frozen=True)

    outcome: ValidationOutcome
    mode: ValidationMode
    is_valid: bool
    reason: str | None = None
    failing_facts: list[str] = Field(default_factory=list)


class SubstituteResolution(BaseModel):
    """Resultat final de la pipeline Phase F.

    Distingue 3 etats :
    - 'injected' : SubstituteEvent valide et injecte dans scheduler + KG
    - 'silent_cancel' : aucun substitut viable, l'event canon reste annule
    - 'regen_exhausted' : 2 regens echouees -> fallback silent_cancel
    """

    model_config = ConfigDict(frozen=True)

    cancelled_canon_event_id: str
    # Round 19 : Literal au lieu de str pour bloquer typos. Pydantic enforce
    # les 3 valeurs valides.
    status: Literal["injected", "silent_cancel", "regen_exhausted"]
    substitute: SubstituteEvent | None = None
    validation_attempts: list[ValidationReport] = Field(default_factory=list)
    rumor_template: str | None = None


__all__ = [
    "SubstituteEvent",
    "SubstituteOutcome",
    "SubstitutePrecondition",
    "SubstituteResolution",
    "ValidationMode",
    "ValidationOutcome",
    "ValidationReport",
]
