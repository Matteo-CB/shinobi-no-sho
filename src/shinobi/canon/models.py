"""Modeles pydantic des datasets canoniques.

Tous les modeles sont immuables (frozen=True) et valides strictement au chargement.
Voir docs/04_canonical_data.md pour la specification complete.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from shinobi.types import Canonicity, Gender, TechniqueCategory, TechniqueRank

ImmutableModel: type[BaseModel] = BaseModel


class _Frozen(BaseModel):
    """Base immuable avec champs supplementaires autorises (extensions de schema)."""

    model_config = ConfigDict(frozen=True, extra="allow")


# Rangs et eres ----------------------------------------------------------------


class Rank(_Frozen):
    """Rang ninja."""

    id: str
    name_romaji: str
    name_kanji: str | None = None
    name_fr: str
    level: int
    min_age: int | None = None
    typical_max_age: int | None = None
    description_fr: str
    canonicity: Canonicity
    sources: list[str] = Field(default_factory=list)
    updated_at: str


class Era(_Frozen):
    """Ere chronologique de l'univers."""

    id: str
    name_romaji: str
    name_fr: str
    year_start: int
    year_end: int | None = None
    confidence: Literal["exact", "approximate", "estimated"] | None = None
    description_fr: str
    key_figures: list[str] = Field(default_factory=list)
    canonicity: Canonicity
    sources: list[str] = Field(default_factory=list)
    updated_at: str


# Natures elementaires ---------------------------------------------------------


class Nature(_Frozen):
    """Nature elementaire ou avancee."""

    id: str
    name_romaji: str
    name_kanji: str | None = None
    name_fr: str
    type: Literal["basic", "advanced", "special", "natural"]
    strong_against: list[str] = Field(default_factory=list)
    weak_against: list[str] = Field(default_factory=list)
    common_clans: list[str] = Field(default_factory=list)
    common_villages: list[str] = Field(default_factory=list)
    description_fr: str
    canonicity: Canonicity
    sources: list[str] = Field(default_factory=list)
    updated_at: str


# Regles globales du monde -----------------------------------------------------


class ChakraRules(_Frozen):
    definition: str
    baseline_pools: dict[str, float]
    regeneration: dict[str, float]
    exhaustion_thresholds: dict[str, float]


class LearningRules(_Frozen):
    difficulty_to_hours_baseline: dict[str, int]
    stat_modifiers: dict[str, float]
    mentor_quality_modifiers: dict[str, float]


class CombatRules(_Frozen):
    initiative_formula: str
    hit_formula: str
    damage_formula: str


class SocialRules(_Frozen):
    reputation_decay_per_year: float
    village_loyalty_default: float
    missing_nin_threshold: float
    village_kekkei_genkai_persecution_modifier: float


class EconomyRules(_Frozen):
    ryo_to_jutsu_scroll_multiplier_by_rank: dict[str, int | None]
    mission_pay_by_rank: dict[str, int]


class TimeRules(_Frozen):
    year_one_anchor: str
    month_names_jp: list[str]


class WorldRules(_Frozen):
    """Regles abstraites de l'univers (chakra, apprentissage, combat, etc.)."""

    chakra: ChakraRules
    learning: LearningRules
    combat: CombatRules
    social: SocialRules
    economy: EconomyRules
    time: TimeRules


# Clans ------------------------------------------------------------------------


class ClanStatusEntry(_Frozen):
    from_year: int
    to_year: int | None
    status: str
    notes: str | None = None


class ClanMembersByEra(_Frozen):
    era: str
    members: list[str]


class Clan(_Frozen):
    id: str
    name_romaji: str
    name_kanji: str | None = None
    village_of_origin: str | None = None
    founder: str | None = None
    key_kekkei_genkai: list[str] = Field(default_factory=list)
    key_natures: list[str] = Field(default_factory=list)
    key_techniques: list[str] = Field(default_factory=list)
    exclusive_techniques: list[str] = Field(default_factory=list)
    history_summary_fr: str | None = None
    status_by_era: list[ClanStatusEntry] = Field(default_factory=list)
    notable_members_by_era: list[ClanMembersByEra] = Field(default_factory=list)
    social_structure_fr: str | None = None
    key_advantages_fr: str | None = None
    key_disadvantages_fr: str | None = None
    wiki_sections: dict[str, str] = Field(default_factory=dict)
    canonicity: Canonicity
    sources: list[str] = Field(default_factory=list)
    updated_at: str


# Kekkei genkai et hiden -------------------------------------------------------


class KekkeiStage(_Frozen):
    stage: int
    tomoe: int | None = None
    abilities_fr: str


class KekkeiGenkai(_Frozen):
    id: str
    name_romaji: str
    name_kanji: str | None = None
    type: Literal["dojutsu", "elemental", "non_elemental", "physical"]
    category: Literal["kekkei_genkai", "kekkei_mora"]
    carrier_clans: list[str] = Field(default_factory=list)
    activation_conditions_fr: str
    stages: list[KekkeiStage] = Field(default_factory=list)
    evolution_paths: list[str] = Field(default_factory=list)
    weaknesses_fr: str | None = None
    canonicity: Canonicity
    sources: list[str] = Field(default_factory=list)
    updated_at: str


class HidenTechnique(_Frozen):
    id: str
    name_romaji: str
    name_fr: str
    owning_clan: str | None = None
    owning_village: str | None = None
    shareable_outside_clan: bool
    shareable_with_authorization: bool
    description_fr: str
    canonicity: Canonicity
    sources: list[str] = Field(default_factory=list)
    updated_at: str


# Villages ---------------------------------------------------------------------


class KageEntry(_Frozen):
    order: int
    character_id: str
    from_year: int
    to_year: int | None
    second_term: bool = False


class VillageDistrict(_Frozen):
    id: str
    name_fr: str | None = None
    active_until_year: int | None = None


class Village(_Frozen):
    id: str
    name_romaji: str
    name_kanji: str | None = None
    name_fr: str
    country: str
    country_name_fr: str
    founded_year: int | None = None
    founded_by: list[str] = Field(default_factory=list)
    kage_title: str | None = None
    kage_lineage: list[KageEntry] = Field(default_factory=list)
    main_clans: list[str] = Field(default_factory=list)
    specialties: list[str] = Field(default_factory=list)
    geography_fr: str | None = None
    districts: list[VillageDistrict] = Field(default_factory=list)
    diplomatic_relations_by_era: list[dict[str, Any]] = Field(default_factory=list)
    wiki_sections: dict[str, str] = Field(default_factory=dict)
    canonicity: Canonicity
    sources: list[str] = Field(default_factory=list)
    updated_at: str


# Organisations ----------------------------------------------------------------


class OrganizationActivePhase(_Frozen):
    from_year: int
    to_year: int | None
    phase: str | None = None


class OrganizationLeaderEntry(_Frozen):
    from_year: int
    to_year: int | None
    leader: str


class OrganizationMembersByEra(_Frozen):
    year: int
    members: list[str]


class Organization(_Frozen):
    id: str
    name_romaji: str
    name_fr: str
    active_period: list[OrganizationActivePhase] = Field(default_factory=list)
    founders: list[str] = Field(default_factory=list)
    leaders_by_era: list[OrganizationLeaderEntry] = Field(default_factory=list)
    members_by_era: list[OrganizationMembersByEra] = Field(default_factory=list)
    ideology_fr: str
    headquarters: list[str] = Field(default_factory=list)
    wiki_sections: dict[str, str] = Field(default_factory=dict)
    canonicity: Canonicity
    sources: list[str] = Field(default_factory=list)
    updated_at: str


# Personnages ------------------------------------------------------------------


class CharacterRankEntry(_Frozen):
    year: int
    rank: str
    notes: str | None = None


class CharacterStatsByEra(_Frozen):
    era_label: str
    year: int
    ninjutsu: float = 1.0
    taijutsu: float = 1.0
    genjutsu: float = 1.0
    intelligence: float = 1.0
    strength: float = 1.0
    speed: float = 1.0
    stamina: float = 1.0
    hand_seals: float = 1.0
    chakra_pool: int = 100
    chakra_control: float = 1.0
    total_databook: float | None = None
    social_charisma: float = 1.0
    learning_genius: float = 1.0
    luck: float = 1.0
    beauty: float = 1.0
    lineage_value: float = 1.0
    confidence: Literal["exact", "approximate", "extrapolated"] | None = None


class CharacterTechniquesByEra(_Frozen):
    year: int
    techniques: list[str]


class CharacterRelationship(_Frozen):
    with_character: str = Field(alias="with")
    type: str
    since_year: int


class CharacterLocationByYear(_Frozen):
    year: int
    location: str


class CharacterVillageByEra(_Frozen):
    from_year: int
    to_year: int | None
    village: str


class SpeechPatterns(_Frozen):
    verbal_tic: str | None = None
    tic_frequency: str | None = None
    register_label: str | None = Field(default=None, alias="register")
    vocabulary_traits: list[str] = Field(default_factory=list)


class Character(_Frozen):
    """Personnage canonique."""

    id: str
    name_romaji: str
    name_kanji: str | None = None
    name_fr: str | None = None
    aliases: list[str] = Field(default_factory=list)
    gender: Gender
    birth_year: int | None = None
    birth_date: str | None = None
    death_year: int | None = None
    death_circumstances_fr: str | None = None
    village_of_origin: str
    current_village_by_era: list[CharacterVillageByEra] = Field(default_factory=list)
    clan: str | None = None
    secondary_clan: str | None = None
    kekkei_genkai: list[str] = Field(default_factory=list)
    kekkei_mora: list[str] = Field(default_factory=list)
    tailed_beast: str | None = None
    rank_progression: list[CharacterRankEntry] = Field(default_factory=list)
    stats_by_era: list[CharacterStatsByEra] = Field(default_factory=list)
    techniques_known_by_era: list[CharacterTechniquesByEra] = Field(default_factory=list)
    natures: list[str] = Field(default_factory=list)
    personality_fr: str | None = None
    voice_profile_id: str | None = None
    speech_patterns: SpeechPatterns | None = None
    key_relationships: list[CharacterRelationship] = Field(default_factory=list)
    location_by_year: list[CharacterLocationByYear] = Field(default_factory=list)
    teachable_techniques: list[str] = Field(default_factory=list)
    teaching_conditions_fr: str | None = None
    knowledge_domains: list[str] = Field(default_factory=list)
    # Sections wiki brutes : Background, Personality, Appearance, Abilities,
    # Part I, Part II, Boruto, Trivia, Quotes, etc. Cle = titre de section.
    # Permet d'exposer 100% du contenu Narutopedia au narrator.
    wiki_sections: dict[str, str] = Field(default_factory=dict)
    canonicity: Canonicity
    sources: list[str] = Field(default_factory=list)
    updated_at: str


# Techniques -------------------------------------------------------------------


class TechniquePrerequisites(_Frozen):
    min_chakra_pool: int = 0
    min_chakra_control: float = 0.0
    required_natures: list[str] = Field(default_factory=list)
    required_techniques: list[str] = Field(default_factory=list)
    min_age: int | None = None
    clan_restriction: str | None = None
    kekkei_genkai_restriction: str | None = None
    village_restriction: str | None = None
    rank_restriction: str | None = None
    notes_fr: str | None = None


class TechniqueEffects(_Frozen):
    damage: Literal["none", "minor", "moderate", "high", "extreme"] = "none"
    area_type: str | None = None
    area_size_meters: float | None = None
    duration_turns: int = 1
    side_effects_fr: list[str] = Field(default_factory=list)


class TechniqueFirstAppearance(_Frozen):
    year: int | None = None
    context_fr: str | None = None


class Technique(_Frozen):
    id: str
    name_romaji: str
    name_kanji: str | None = None
    name_fr: str
    category: TechniqueCategory
    subcategory: str | None = None
    natures: list[str] = Field(default_factory=list)
    rank: TechniqueRank
    classification: list[str] = Field(default_factory=list)
    range: str | None = None
    hand_seals: list[str] = Field(default_factory=list)
    chakra_cost: int = 0
    stamina_cost: int = 0
    learning_difficulty: int = 1
    prerequisites: TechniquePrerequisites = Field(default_factory=TechniquePrerequisites)
    effects: TechniqueEffects = Field(default_factory=TechniqueEffects)
    counters: list[str] = Field(default_factory=list)
    synergies: list[str] = Field(default_factory=list)
    canonical_users: list[str] = Field(default_factory=list)
    first_appearance: TechniqueFirstAppearance | None = None
    description_fr: str
    wiki_sections: dict[str, str] = Field(default_factory=dict)
    canonicity: Canonicity
    sources: list[str] = Field(default_factory=list)
    updated_at: str
    creator_id: str | None = None
    forbidden_reason_fr: str | None = None


# Bijuu ------------------------------------------------------------------------


class TailedBeastJinchuurikiEntry(_Frozen):
    from_year: int
    to_year: int | None
    jinchuuriki: str


class TailedBeast(_Frozen):
    id: str
    name_romaji: str
    tails: int
    epithets: list[str] = Field(default_factory=list)
    current_jinchuuriki_by_era: list[TailedBeastJinchuurikiEntry] = Field(default_factory=list)
    personality_fr: str | None = None
    abilities_fr: str | None = None
    chakra_signature_color: str | None = None
    canonicity: Canonicity
    sources: list[str] = Field(default_factory=list)
    updated_at: str


# Timeline events --------------------------------------------------------------


class EventPrecondition(_Frozen):
    type: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class EventOutcome(_Frozen):
    type: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class CancellationStrategy(_Frozen):
    type: str
    substitute_logic: str | None = None
    notes_fr: str | None = None


class TimelineEvent(_Frozen):
    id: str
    name_fr: str
    year: int
    date: str | None = None
    location: str | None = None
    involved_characters: list[str] = Field(default_factory=list)
    preconditions: list[EventPrecondition] = Field(default_factory=list)
    outcomes: list[EventOutcome] = Field(default_factory=list)
    narrative_summary_fr: str
    cancellation_strategy: CancellationStrategy = Field(
        default_factory=lambda: CancellationStrategy(type="hard_cancel"),
    )
    canonicity: Canonicity
    sources: list[str] = Field(default_factory=list)
    updated_at: str


# Armes et lieux ---------------------------------------------------------------


class WeaponTool(_Frozen):
    id: str
    name_romaji: str
    name_fr: str
    type: str
    subcategory: str | None = None
    wielders_canonical: list[str] = Field(default_factory=list)
    abilities_fr: str | None = None
    rarity: Literal["common", "uncommon", "rare", "legendary", "unique"] = "common"
    canonicity: Canonicity
    sources: list[str] = Field(default_factory=list)
    updated_at: str


class Location(_Frozen):
    id: str
    name_romaji: str
    name_fr: str
    country: str | None = None
    near_village: str | None = None
    geography_fr: str | None = None
    canonical_events: list[str] = Field(default_factory=list)
    wiki_sections: dict[str, str] = Field(default_factory=dict)
    canonicity: Canonicity
    sources: list[str] = Field(default_factory=list)
    updated_at: str


# Voice profiles ---------------------------------------------------------------


class VoiceProfile(_Frozen):
    id: str
    character_id: str
    register_fr: str
    verbal_tics: list[str] = Field(default_factory=list)
    vocabulary_themes: list[str] = Field(default_factory=list)
    syntactic_patterns: list[str] = Field(default_factory=list)
    sample_lines: list[str] = Field(default_factory=list)
    do_not_use: list[str] = Field(default_factory=list)
    updated_at: str


# Container global -------------------------------------------------------------


class CanonBundle(_Frozen):
    """Bundle complet d'un dataset canonique apres chargement."""

    world_rules: WorldRules
    natures: dict[str, Nature]
    ranks: dict[str, Rank]
    eras: dict[str, Era]
    villages: dict[str, Village]
    clans: dict[str, Clan]
    organizations: dict[str, Organization]
    characters: dict[str, Character]
    tailed_beasts: dict[str, TailedBeast]
    kekkei_genkai: dict[str, KekkeiGenkai]
    kekkei_mora: dict[str, KekkeiGenkai]
    hiden: dict[str, HidenTechnique]
    techniques: dict[str, Technique]
    weapons_tools: dict[str, WeaponTool]
    locations: dict[str, Location]
    timeline_events: dict[str, TimelineEvent]
    voice_profiles: dict[str, VoiceProfile]
