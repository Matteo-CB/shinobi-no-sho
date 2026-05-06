"""Types de donnees pour le Tension Detector.

Pydantic v2 strict. Tout est immuable (frozen).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class TensionType(StrEnum):
    """Categories des tensions detectees, alignees avec doc 02 §5.3.

    Chaque type correspond a une famille d'invariants ou a une categorie
    de fil narratif identifie par le LLM analyste.
    """

    # Politique
    power_vacuum = "power_vacuum"               # absence de leader / kage vacant
    border_conflict = "border_conflict"         # tensions geographiques
    succession_dispute = "succession_dispute"    # qui prend la suite
    alliance_breakdown = "alliance_breakdown"    # alliance fragile

    # Reseau / clan
    clan_extinction_threat = "clan_extinction_threat"
    bloodline_unresolved = "bloodline_unresolved"  # liens de sang non resolus
    factional_revenge = "factional_revenge"      # faction lesee

    # Psychologie individuelle
    obsessive_npc_idle = "obsessive_npc_idle"   # perso obsede passif
    lone_survivor_obsessed = "lone_survivor_obsessed"
    student_surpasses_master = "student_surpasses_master"
    cursed_hatred = "cursed_hatred"             # haine cumulative

    # Pouvoir & artefacts
    jinchuuriki_unprotected = "jinchuuriki_unprotected"
    tailed_beast_uncontrolled = "tailed_beast_uncontrolled"
    forbidden_jutsu_threat = "forbidden_jutsu_threat"
    kekkei_carrier_isolated = "kekkei_carrier_isolated"

    # Information / secrets
    hidden_truth_pending = "hidden_truth_pending"
    chekhovs_gun_unfired = "chekhovs_gun_unfired"
    prophecy_unfulfilled = "prophecy_unfulfilled"

    # Anniversaires & rythme narratif
    death_anniversary = "death_anniversary"
    canon_event_pending = "canon_event_pending"  # event canon a sa date

    # Categorie generique pour le LLM analyste
    other = "other"


class TensionSeverity(StrEnum):
    """Niveau d'urgence dramatique. Mappe a un score numerique pour tri."""

    low = "low"          # signal faible, peut attendre
    medium = "medium"    # interessant a explorer
    high = "high"        # configuration narrativement potente
    critical = "critical"  # tension explosive, pousser maintenant


_SEVERITY_SCORE: dict[TensionSeverity, float] = {
    TensionSeverity.low: 0.25,
    TensionSeverity.medium: 0.5,
    TensionSeverity.high: 0.75,
    TensionSeverity.critical: 1.0,
}


class Tension(BaseModel):
    """Une opportunite dramatique detectee. Immuable.

    Le `score` (0-1) combine severity + canon_weight pour le tri par
    le Director. Le `source_rule` identifie l'invariant ou le LLM
    analyste qui a souleve la tension (utile pour debug + rejouabilite).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: TensionType
    description: str = Field(..., min_length=10)
    severity: TensionSeverity = TensionSeverity.medium
    score: float = Field(default=0.5, ge=0.0, le=1.0)
    involved_entities: list[str] = Field(default_factory=list)
    source_rule: str = "unspecified"   # nom de l'invariant ou 'llm_analyst'
    detected_at_year: int | None = None
    notes: str | None = None
    # Suggestion non-prescriptive de canal narratif (le Director decidera).
    suggested_resolution_hint: str | None = None

    @classmethod
    def from_severity(
        cls,
        *,
        type: TensionType,
        description: str,
        severity: TensionSeverity,
        involved_entities: list[str] | None = None,
        source_rule: str = "unspecified",
        detected_at_year: int | None = None,
        notes: str | None = None,
        suggested_resolution_hint: str | None = None,
    ) -> Tension:
        """Construit une Tension en derivant le score depuis la severity."""
        return cls(
            type=type,
            description=description,
            severity=severity,
            score=_SEVERITY_SCORE[severity],
            involved_entities=involved_entities or [],
            source_rule=source_rule,
            detected_at_year=detected_at_year,
            notes=notes,
            suggested_resolution_hint=suggested_resolution_hint,
        )


class TensionList(BaseModel):
    """Resultat agrege du detecteur pour un tour donne. Immuable.

    Triee par score decroissant. Permet aux couches superieures (Director)
    de prendre les top-N tensions a un cout cap.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    tensions: list[Tension] = Field(default_factory=list)
    detected_at_year: int | None = None
    detector_version: str = "phase_c_v1"

    def top(self, n: int) -> list[Tension]:
        return sorted(self.tensions, key=lambda t: -t.score)[:n]

    def by_type(self, t: TensionType) -> list[Tension]:
        return [x for x in self.tensions if x.type == t]

    def total(self) -> int:
        return len(self.tensions)

    def merge(self, other: TensionList) -> TensionList:
        """Concatenation immuable. Pas de dedup automatique pour preserver les
        traces de chaque source (invariant vs LLM peuvent avoir le meme
        type sans etre redondants)."""
        return TensionList(
            tensions=[*self.tensions, *other.tensions],
            detected_at_year=self.detected_at_year or other.detected_at_year,
            detector_version=self.detector_version,
        )


__all__ = ["Tension", "TensionList", "TensionSeverity", "TensionType"]
