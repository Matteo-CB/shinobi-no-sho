"""Types Pydantic pour les missions canon enrichies."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class MissionRank(StrEnum):
    """Rang officiel d'une mission ninja."""

    d = "D"
    c = "C"
    b = "B"
    a = "A"
    s = "S"
    unranked = "unranked"  # missions internes / informelles
    forbidden = "forbidden"  # kinjutsu missions exceptionnelles


class MissionType(StrEnum):
    """Categorie principale de la mission."""

    escort = "escort"
    assassination = "assassination"
    investigation = "investigation"
    retrieval = "retrieval"  # ramener un objet ou une cible
    capture = "capture"
    rescue = "rescue"
    protection = "protection"  # garde rapprochee
    sabotage = "sabotage"
    spy = "spy"
    delivery = "delivery"
    extermination = "extermination"  # bandits / monstres
    survey = "survey"  # exploration / reconnaissance
    diplomatic = "diplomatic"
    chunin_exam = "chunin_exam"
    training = "training"
    special_operation = "special_operation"  # operations Anbu / sceau
    other = "other"


class MissionOutcome(StrEnum):
    """Resultat de la mission."""

    success = "success"
    partial_success = "partial_success"
    failure = "failure"
    abandoned = "abandoned"
    in_progress = "in_progress"
    canceled = "canceled"
    unknown = "unknown"


class MissionParticipant(BaseModel):
    """Un membre du squad assigne a la mission."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    character_id: str = Field(..., min_length=1)
    role: str = "operative"  # leader / medic / scout / operative / sensei / observer
    notes: str | None = None


class Mission(BaseModel):
    """Une mission canon enrichie. Immutable.

    Le `id` est le slug canonique (ex: 'mission_wave_country_zabuza_arc').
    Les dates sont au format ISO partiel : year requis, month/day optionnels.

    L'ancrage canon (`canonical_arc`) permet de mapper avec les arcs scrapes
    Narutopedia (cf docs/02 §5 Phase H pour extraction massive future).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(..., min_length=1, pattern=r"^[a-z0-9_]+$")
    name_fr: str = Field(..., min_length=1)
    name_romaji: str | None = None
    rank: MissionRank
    type: MissionType
    outcome: MissionOutcome = MissionOutcome.unknown

    # Ancrage temporel
    year: int  # an X (convention : an 0 = naissance Naruto)
    month: int | None = Field(default=None, ge=1, le=12)
    day: int | None = Field(default=None, ge=1, le=31)
    duration_days: int | None = Field(default=None, ge=0)
    canonical_arc: str | None = None  # ex: 'wave_country', 'chunin_exam_arc'

    # Acteurs
    participants: list[MissionParticipant] = Field(default_factory=list)
    assigning_authority: str | None = None  # ex: 'sarutobi_hiruzen', 'tazuna_client'
    target_subject: str | None = None  # cible ou objectif central
    location_id: str | None = None
    starting_village: str | None = None

    # Description
    summary_fr: str = Field(..., min_length=10)
    objectives: list[str] = Field(default_factory=list)
    consequences: list[str] = Field(default_factory=list)
    canonicity: str = "manga"  # canon source : manga / databook / anime_canon / filler / boruto

    # Metadata
    sources: list[str] = Field(default_factory=list)  # ex: 'narutopedia:Wave_Country_Mission'
    related_event_ids: list[str] = Field(default_factory=list)
    related_mission_ids: list[str] = Field(default_factory=list)

    def participant_ids(self) -> list[str]:
        return [p.character_id for p in self.participants]

    def has_participant(self, character_id: str) -> bool:
        return any(p.character_id == character_id for p in self.participants)

    def date_iso(self) -> str:
        """Format YYYY[-MM[-DD]] selon les composants disponibles."""
        s = f"{self.year:+d}"
        if self.month is not None:
            s += f"-{self.month:02d}"
            if self.day is not None:
                s += f"-{self.day:02d}"
        return s


__all__ = [
    "Mission",
    "MissionOutcome",
    "MissionParticipant",
    "MissionRank",
    "MissionType",
]
