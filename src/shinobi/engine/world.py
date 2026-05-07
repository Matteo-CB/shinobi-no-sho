"""Etat global du monde."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from shinobi.canon.profiles import CanonicityProfile
from shinobi.types import AttentionLevel, EventStatus, KnowledgeLevel


class NPCState(BaseModel):
    model_config = ConfigDict(frozen=True)

    character_id: str
    is_alive: bool = True
    current_location: str
    current_year: int
    current_age: int
    current_rank: str
    current_affiliations: list[str] = Field(default_factory=list)
    psychological_state: str = "stable"
    canonical_arc_progress: str | None = None
    attention_level: AttentionLevel = AttentionLevel.low
    last_updated_year: int | None = None


class VillageState(BaseModel):
    model_config = ConfigDict(frozen=True)

    village_id: str
    current_kage: str | None = None
    political_alignment: str = "neutral"
    population_status: str = "stable"
    recent_incidents: list[str] = Field(default_factory=list)


class OrganizationState(BaseModel):
    model_config = ConfigDict(frozen=True)

    organization_id: str
    is_active: bool = True
    current_leader: str | None = None
    members: list[str] = Field(default_factory=list)
    recent_activities: list[str] = Field(default_factory=list)


class ScheduledEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_id: str
    year: int
    date: str | None = None
    status: EventStatus = EventStatus.scheduled
    triggered_at_turn: int | None = None
    notes: str | None = None


class CompletedEvent(BaseModel):
    model_config = ConfigDict(frozen=True)
    event_id: str
    triggered_at_turn: int
    triggered_at_year: int


class CancelledEvent(BaseModel):
    model_config = ConfigDict(frozen=True)
    event_id: str
    cancelled_at_turn: int
    cancelled_at_year: int
    reason: str


class ModifiedEvent(BaseModel):
    model_config = ConfigDict(frozen=True)
    event_id: str
    modified_at_turn: int
    modified_at_year: int
    description: str


class Rumor(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    source_event_id: str | None = None
    content: str
    fidelity: float = 1.0
    diffusion_radius: Literal["proximity", "regional", "international", "secret"] = "regional"
    born_at_year: int
    expires_at_year: int | None = None
    received_by_player: bool = False
    received_at_year: int | None = None
    knowledge_level: KnowledgeLevel = KnowledgeLevel.rumor


class PoliticalClimate(BaseModel):
    model_config = ConfigDict(frozen=True)
    relations_by_pair: dict[str, str] = Field(default_factory=dict)
    global_tension: int = 0


class EconomyState(BaseModel):
    model_config = ConfigDict(frozen=True)
    inflation_factor: float = 1.0
    notable_shortages: list[str] = Field(default_factory=list)


class WorldState(BaseModel):
    """Etat complet du monde."""

    model_config = ConfigDict(frozen=True)

    current_year: int
    current_date: str  # MM-DD
    current_hour: int = 8
    current_minute: int = 0

    canonicity_profile: list[str] = Field(default_factory=list)
    seed: int = 0xCAFEBABE_DEADBEEF

    npc_states: dict[str, NPCState] = Field(default_factory=dict)
    village_states: dict[str, VillageState] = Field(default_factory=dict)
    organization_states: dict[str, OrganizationState] = Field(default_factory=dict)

    scheduled_events: list[ScheduledEvent] = Field(default_factory=list)
    completed_events: list[CompletedEvent] = Field(default_factory=list)
    cancelled_events: list[CancelledEvent] = Field(default_factory=list)
    modified_events: list[ModifiedEvent] = Field(default_factory=list)

    rumors: list[Rumor] = Field(default_factory=list)
    political_climate: PoliticalClimate = Field(default_factory=PoliticalClimate)
    economy: EconomyState = Field(default_factory=EconomyState)

    # Phase F : registry des SubstituteEvent runtime injectes par
    # WorldResolverPipeline. Stocke comme dict[str, dict] (Pydantic-safe)
    # pour eviter dependance circulaire avec shinobi.world_resolver.
    substitute_events: dict[str, dict] = Field(default_factory=dict)

    def with_seed(self, seed: int) -> WorldState:
        return self.model_copy(update={"seed": seed})

    def with_time(self, year: int, date: str, hour: int, minute: int = 0) -> WorldState:
        return self.model_copy(
            update={
                "current_year": year,
                "current_date": date,
                "current_hour": hour,
                "current_minute": minute,
            }
        )

    def with_event_status(
        self,
        event_id: str,
        status: EventStatus,
        *,
        turn: int | None = None,
        notes: str | None = None,
    ) -> WorldState:
        new_events = []
        found = False
        for ev in self.scheduled_events:
            if ev.event_id == event_id:
                new_events.append(
                    ev.model_copy(
                        update={"status": status, "triggered_at_turn": turn, "notes": notes}
                    )
                )
                found = True
            else:
                new_events.append(ev)
        if not found:
            return self
        return self.model_copy(update={"scheduled_events": new_events})

    def with_npc_state(self, npc_state: NPCState) -> WorldState:
        d = dict(self.npc_states)
        d[npc_state.character_id] = npc_state
        return self.model_copy(update={"npc_states": d})


def create_default_world(
    *,
    profile: CanonicityProfile,
    starting_year: int,
    starting_date: str = "01-01",
    seed: int = 0xCAFEBABEDEADBEEF,
) -> WorldState:
    """Cree un WorldState minimal a la date de depart."""
    return WorldState(
        current_year=starting_year,
        current_date=starting_date,
        canonicity_profile=sorted(profile.sources),
        seed=seed,
    )
