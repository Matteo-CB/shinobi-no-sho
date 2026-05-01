"""Modele complet du Character joueur."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from shinobi.engine.stats import CoreStats, ExtendedStats
from shinobi.types import Gender, GoalStatus


class ChakraState(BaseModel):
    model_config = ConfigDict(frozen=True)

    current: int = 100
    max: int = 100
    natures_unlocked: list[str] = Field(default_factory=list)
    natures_partial: list[str] = Field(default_factory=list)
    has_yin_yang_release: bool = False
    senjutsu_charged: int = 0


class Injury(BaseModel):
    model_config = ConfigDict(frozen=True)
    description: str
    severity: Literal["minor", "moderate", "major", "critical"] = "minor"
    healing_progress: float = 0.0


class Poison(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    severity: Literal["mild", "severe", "lethal"] = "mild"
    rounds_remaining: int = 1


class HealthState(BaseModel):
    model_config = ConfigDict(frozen=True)

    hp_current: int = 100
    hp_max: int = 100
    fatigue: int = 0
    injuries: list[Injury] = Field(default_factory=list)
    permanent_disabilities: list[str] = Field(default_factory=list)
    mental_state: str = "stable"
    poison_status: list[Poison] = Field(default_factory=list)


class KnownTechnique(BaseModel):
    model_config = ConfigDict(frozen=True)

    technique_id: str
    mastery_level: float = 1.0
    learned_year: int
    learned_from: str | None = None
    times_used: int = 0


class LearningTechnique(BaseModel):
    model_config = ConfigDict(frozen=True)

    technique_id: str
    progress_hours: int = 0
    progress_required: int = 100
    started_year: int
    teacher_id: str | None = None
    quality_modifier: float = 1.0


class OwnedWeapon(BaseModel):
    model_config = ConfigDict(frozen=True)

    weapon_id: str
    quantity: int = 1
    quality: Literal["poor", "standard", "fine", "exceptional"] = "standard"


class Inventory(BaseModel):
    model_config = ConfigDict(frozen=True)

    scrolls: list[str] = Field(default_factory=list)
    consumables: dict[str, int] = Field(default_factory=dict)
    misc: dict[str, int] = Field(default_factory=dict)


class FamilyMember(BaseModel):
    model_config = ConfigDict(frozen=True)
    relationship_label: str
    character_id: str
    is_alive: bool = True


class FamilyState(BaseModel):
    model_config = ConfigDict(frozen=True)
    members: list[FamilyMember] = Field(default_factory=list)


class RelationshipEvent(BaseModel):
    model_config = ConfigDict(frozen=True)
    year: int
    description: str
    affinity_delta: int = 0


class Debt(BaseModel):
    model_config = ConfigDict(frozen=True)
    description: str
    counterparty_id: str
    amount: int = 0


class Relationship(BaseModel):
    model_config = ConfigDict(frozen=True)

    with_character_id: str
    type: str = "acquaintance"
    affinity: int = 0
    trust: int = 0
    history: list[RelationshipEvent] = Field(default_factory=list)
    secrets_shared: list[str] = Field(default_factory=list)
    debts_owed: list[Debt] = Field(default_factory=list)


class ReputationEntry(BaseModel):
    model_config = ConfigDict(frozen=True)
    village_id: str
    score: int = 0


class ReputationState(BaseModel):
    model_config = ConfigDict(frozen=True)
    by_village: list[ReputationEntry] = Field(default_factory=list)
    bingo_book_entry: bool = False


class CharacterKnowledgeEntry(BaseModel):
    model_config = ConfigDict(frozen=True)
    character_id: str
    notes_fr: str | None = None


class KnowledgeState(BaseModel):
    model_config = ConfigDict(frozen=True)

    known_events: dict[str, str] = Field(default_factory=dict)
    known_techniques_existence: list[str] = Field(default_factory=list)
    known_characters: list[CharacterKnowledgeEntry] = Field(default_factory=list)
    known_locations: list[str] = Field(default_factory=list)
    secrets_uncovered: list[str] = Field(default_factory=list)


class GoalRef(BaseModel):
    model_config = ConfigDict(frozen=True)
    goal_id: str
    status: GoalStatus = GoalStatus.declared


class BreadcrumbRef(BaseModel):
    model_config = ConfigDict(frozen=True)
    breadcrumb_id: str
    parent_goal_id: str
    completed: bool = False


class BiographyEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    year: int
    age: int
    summary: str
    category: Literal[
        "birth",
        "rank_promotion",
        "technique_learned",
        "key_relationship",
        "trauma",
        "achievement",
        "encounter",
        "other",
    ] = "other"


class Character(BaseModel):
    """Etat complet du personnage joueur."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    gender: Gender
    birth_year: int
    birth_date: str  # MM-DD
    age_years: int
    village_of_origin: str
    current_village: str
    current_location: str
    clan: str | None = None
    secondary_clan: str | None = None
    family: FamilyState = Field(default_factory=FamilyState)
    rank: str = "academy_student"
    affiliations: list[str] = Field(default_factory=list)
    is_missing_nin: bool = False
    is_dead: bool = False
    death_circumstances: str | None = None

    stats: CoreStats = Field(default_factory=CoreStats)
    extended_stats: ExtendedStats = Field(default_factory=ExtendedStats)
    chakra: ChakraState = Field(default_factory=ChakraState)
    health: HealthState = Field(default_factory=HealthState)
    natures: list[str] = Field(default_factory=list)
    kekkei_genkai: list[str] = Field(default_factory=list)
    kekkei_mora: list[str] = Field(default_factory=list)
    tailed_beast: str | None = None

    techniques_known: list[KnownTechnique] = Field(default_factory=list)
    techniques_in_progress: list[LearningTechnique] = Field(default_factory=list)
    weapons: list[OwnedWeapon] = Field(default_factory=list)
    summons: list[str] = Field(default_factory=list)
    inventory: Inventory = Field(default_factory=Inventory)
    money: int = 0

    relationships: list[Relationship] = Field(default_factory=list)
    reputation: ReputationState = Field(default_factory=ReputationState)
    knowledge: KnowledgeState = Field(default_factory=KnowledgeState)

    declared_goals: list[GoalRef] = Field(default_factory=list)
    active_breadcrumbs: list[BreadcrumbRef] = Field(default_factory=list)
    completed_breadcrumbs: list[str] = Field(default_factory=list)

    biography_log: list[BiographyEvent] = Field(default_factory=list)

    def with_age(self, new_age: int) -> Character:
        return self.model_copy(update={"age_years": new_age})

    def with_money(self, delta: int) -> Character:
        return self.model_copy(update={"money": max(0, self.money + delta)})

    def with_chakra(self, new_chakra: ChakraState) -> Character:
        return self.model_copy(update={"chakra": new_chakra})

    def with_health(self, new_health: HealthState) -> Character:
        return self.model_copy(update={"health": new_health})

    def add_known_technique(self, tech: KnownTechnique) -> Character:
        techs = [*self.techniques_known, tech]
        return self.model_copy(update={"techniques_known": techs})

    def add_biography_event(self, event: BiographyEvent) -> Character:
        log = [*self.biography_log, event]
        return self.model_copy(update={"biography_log": log})
