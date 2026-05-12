"""Schemas Pydantic d'echange API Phase 9.

Wrappers minces autour des modeles internes : on n'expose pas les payloads
SQLite bruts mais des structures stables versionnees.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    """Response of /health."""

    model_config = ConfigDict(frozen=True)

    status: str = Field(description="'ok' if the API is responsive.")
    version: str = Field(description="shinobi package version.")
    canon_loaded: bool = Field(
        description="True if the canon is loadable from disk.",
    )
    saves_count: int = Field(description="Number of saves present on disk.")
    llm_available: bool = Field(
        default=False,
        description=(
            "True if local llama-server is responsive. Required for "
            "pathfinder, narration, and Phase C/D/G inferences."
        ),
    )


class SaveSummary(BaseModel):
    """Lightweight save view (without payload)."""

    model_config = ConfigDict(frozen=True)

    save_id: str
    schema_version: int
    character_name: str
    character_age: int
    current_year: int
    current_date: str
    village: str
    rank: str
    canonicity_profile: str
    playtime_hours: float
    total_turns: int
    last_played: str
    created_at: str
    thumbnail_summary: str
    warnings: list[str] = Field(default_factory=list)


class SavesListResponse(BaseModel):
    """List of available saves."""

    model_config = ConfigDict(frozen=True)

    saves: list[SaveSummary]
    count: int


class CreateSaveRequest(BaseModel):
    """Save creation: random or canon-incarnated."""

    model_config = ConfigDict(frozen=True)

    mode: str = Field(
        description="'random' (free character) or 'canon' (incarnate a canon character).",
    )
    name: str | None = Field(
        default=None,
        description="Character name (random mode only).",
    )
    gender: str | None = Field(
        default=None,
        description="'male'|'female'|'non_binary' (random mode).",
    )
    village: str | None = Field(
        default=None,
        description="Village of origin (random mode).",
    )
    clan: str | None = Field(
        default=None,
        description="Optional clan (random mode).",
    )
    starting_year: int | None = Field(
        default=None,
        description="Canon starting year (random mode).",
    )
    starting_age: int | None = Field(
        default=None,
        description="Starting age (random mode; default 12).",
    )
    canon_id: str | None = Field(
        default=None,
        description="Canon id to incarnate (canon mode).",
    )
    canon_query: str | None = Field(
        default=None,
        description="Fuzzy lookup of canon_id if not provided (canon mode).",
    )
    age_at_start: int | None = Field(
        default=None,
        description="Incarnation age (canon mode).",
    )
    canonicity_profile: str = Field(
        default="default",
        description="Canonicity profile label.",
    )
    # Champs optionnels mode random : permettent a une UI de produire un
    # personnage complet (parite avec le wizard CLI _run_original_creation_flow).
    kekkei_genkai: list[str] | None = Field(
        default=None,
        description="e.g. ['sharingan'] for an Uchiha (random mode).",
    )
    kekkei_mora: list[str] | None = Field(
        default=None,
        description="e.g. ['karma'] (Otsutsuki, random mode).",
    )
    tailed_beast: str | None = Field(
        default=None,
        description="e.g. 'kyuubi' for a jinchuuriki (random mode).",
    )
    natures: list[str] | None = Field(
        default=None,
        description="Chakra natures e.g. ['katon', 'fuuton'] (random mode).",
    )
    rank: str | None = Field(
        default=None,
        description="Explicit rank (random mode). Otherwise derived from age.",
    )
    family_status: str | None = Field(
        default=None,
        description=(
            "'typical' (default) | 'orphan' | 'lineage'. Determines the "
            "generated FamilyState. Parity with CLI wizard _pick_family."
        ),
    )
    roll_stats: bool = Field(
        default=True,
        description=(
            "If True (default), roll stats with clan/kekkei/nature biases "
            "via _roll_stats. If False, use the CoreStats() defaults "
            "(average stats at 1.0)."
        ),
    )


class CreateSaveResponse(BaseModel):
    """Response to save creation."""

    model_config = ConfigDict(frozen=True)

    save_id: str
    character_name: str
    current_year: int


class TurnRequest(BaseModel):
    """Turn execution request."""

    model_config = ConfigDict(frozen=True)

    intent_text: str = Field(
        description="Free text describing the player's intent.",
    )
    duration_hours: int | None = Field(
        default=None,
        description=(
            "Explicit duration in hours for actions with configurable "
            "duration (train_stat, train_technique, work, ...). If None, "
            "use the interpreter's default value."
        ),
    )
    present_npc_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Canon NPCs present during the action. The server will touch "
            "their relationships and update their NPCState. Automatic "
            "detection (LLM-based in the CLI) is out of scope here — the "
            "client supplies the list."
        ),
    )


class TurnResponse(BaseModel):
    """Turn response: resolved action + new state summary."""

    model_config = ConfigDict(frozen=True)

    turn_number: int
    action_type: str
    outcome: str
    summary_fr: str
    duration_minutes: int
    chakra_cost: int
    money_delta: int
    hp_delta: int
    fatigue_delta: int
    stat_changes: list[dict[str, Any]] = Field(default_factory=list)
    consequences: list[dict[str, Any]] = Field(default_factory=list)
    current_year: int
    current_date: str
    current_hour: int
    character_age: int
    character_hp: int
    character_chakra: int
    # Auto-detection (parite CLI)
    fired_event_ids: list[str] = Field(
        default_factory=list,
        description="Canon events that fired during this turn (tick_scheduler).",
    )
    cancelled_event_ids: list[str] = Field(default_factory=list)
    completed_goal_descriptions: list[str] = Field(
        default_factory=list,
        description="Goals auto-completed this turn (target reached).",
    )
    failed_goal_descriptions: list[str] = Field(
        default_factory=list,
        description="Goals auto-failed this turn (target dead, player dead).",
    )
    completed_breadcrumb_descriptions: list[str] = Field(default_factory=list)
    aged: bool = Field(
        default=False,
        description="True if the character aged during this turn.",
    )
    rumors_received_ids: list[str] = Field(
        default_factory=list,
        description="Rumors marked as heard this turn.",
    )
    living_cost_charged: int = Field(
        default=0,
        description="Cost of living deducted this turn (in ryos).",
    )
    new_money: int = Field(
        default=0,
        description="Ryos balance after this turn.",
    )


class StatusResponse(BaseModel):
    """Current state of a save (read-only)."""

    model_config = ConfigDict(frozen=True)

    save_id: str
    character_name: str
    character_id: str
    age_years: int
    rank: str
    village: str
    current_location: str
    hp_current: int
    hp_max: int
    chakra_current: int
    chakra_max: int
    fatigue: int
    money_ryos: int
    current_year: int
    current_date: str
    current_hour: int
    total_turns: int
    techniques_known: list[str] = Field(default_factory=list)
    natures: list[str] = Field(default_factory=list)
    kekkei_genkai: list[str] = Field(default_factory=list)


class CanonCharacterSummary(BaseModel):
    """Canon character summary view.

    Phase i18n.9: `name` is resolved per the active language
    (Accept-Language or preferences). `name_romaji` remains the romaji
    Japanese name (never translated). `name_fr` is kept for backward
    compatibility. `description` is resolved from personality/wiki per
    the active language.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    name: str | None = None
    name_romaji: str | None = None
    name_fr: str | None = None
    description: str | None = None
    village_of_origin: str | None = None
    clan: str | None = None
    birth_year: int | None = None
    death_year: int | None = None
    rank: str | None = None
    natures: list[str] = Field(default_factory=list)
    kekkei_genkai: list[str] = Field(default_factory=list)


class CanonCharactersResponse(BaseModel):
    """Paginated list of canon characters."""

    model_config = ConfigDict(frozen=True)

    characters: list[CanonCharacterSummary]
    total: int
    offset: int
    limit: int


class CanonTechniqueSummary(BaseModel):
    """Canon technique summary view.

    Phase i18n.9: `name` is resolved per the active language. `name_romaji`
    and `name_fr` are kept for backward compatibility.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    name: str | None = None
    name_romaji: str | None = None
    name_fr: str | None = None
    rank: str | None = None
    natures: list[str] = Field(default_factory=list)
    classification: list[str] = Field(default_factory=list)


class CanonTechniquesResponse(BaseModel):
    """Paginated list of canon techniques."""

    model_config = ConfigDict(frozen=True)

    techniques: list[CanonTechniqueSummary]
    total: int
    offset: int
    limit: int


class CanonVillageSummary(BaseModel):
    """Canon village summary view.

    Phase i18n.9: `name` is resolved per the active language.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    name: str | None = None
    name_romaji: str | None = None
    name_fr: str | None = None
    country: str | None = None


class CanonVillagesResponse(BaseModel):
    """List of canon villages."""

    model_config = ConfigDict(frozen=True)

    villages: list[CanonVillageSummary]
    count: int


class ResolveCanonRequest(BaseModel):
    """Fuzzy lookup of a canon_id from free text."""

    model_config = ConfigDict(frozen=True)

    query: str


class ResolveCanonResponse(BaseModel):
    """Canon resolution result: unique id or candidates."""

    model_config = ConfigDict(frozen=True)

    canon_id: str | None
    candidates: list[str]


class ErrorResponse(BaseModel):
    """Uniform error response."""

    model_config = ConfigDict(frozen=True)

    error: str
    detail: str | None = None


# === Preferences (Phase i18n.2) =======================================


class PreferencesResponse(BaseModel):
    """User preferences read (GET /preferences)."""

    model_config = ConfigDict(frozen=True)

    language: str = Field(
        description="ISO code of the active language (en, fr, ja, zh, ko, pt-BR, de, es).",
    )
    first_launch_completed: bool = Field(
        description="True if the language picker has already been shown.",
    )
    language_chosen_at: str | None = Field(
        default=None,
        description="ISO 8601 UTC timestamp of the last language choice.",
    )
    available_languages: list[str] = Field(
        description="List of language codes supported by the server.",
    )
    native_names: dict[str, str] = Field(
        description="Code -> native name (e.g. 'ja' -> '日本語').",
    )


class SetLanguageRequest(BaseModel):
    """PUT /preferences/language : changer la langue active."""

    model_config = ConfigDict(frozen=True)

    language: str = Field(
        description="ISO code of the target language. Must be in available_languages.",
    )


class SetLanguageResponse(BaseModel):
    """Response to PUT /preferences/language."""

    model_config = ConfigDict(frozen=True)

    language: str
    first_launch_completed: bool
    language_chosen_at: str | None = None


# === Goals ============================================================


class GoalSummary(BaseModel):
    """Goal summary view.

    Phase i18n.8: `description_player_original_language` is the ISO code
    detected from the player text. `description_player_translated` is the
    cache of translations to other config languages (filled at goal
    creation via PlayerTranslator).
    """

    model_config = ConfigDict(frozen=True)

    id: str
    description_player: str
    interpretation_canonical: str
    target_type: str
    target_id: str | None = None
    status: str
    declared_at_year: int
    declared_at_age: int
    completed_at_year: int | None = None
    abandoned_at_year: int | None = None
    breadcrumbs: list[str] = Field(default_factory=list)
    description_player_original_language: str | None = None
    description_player_translated: dict[str, str] = Field(default_factory=dict)


class GoalsListResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    goals: list[GoalSummary]
    count: int


class DeclareGoalRequest(BaseModel):
    """Declaration of a new goal."""

    model_config = ConfigDict(frozen=True)

    description_player: str
    interpretation_canonical: str | None = None
    target_type: str = "free_form"
    target_id: str | None = None
    declared_priority: int = 5


# === Missions =========================================================


class MissionSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    rank: str
    title: str
    description: str = ""  # Phase i18n.9 : lang-resolu (alias description_fr quand pas de variante)
    description_fr: str
    duration_hours: int
    difficulty_dc: int
    reward_ryos: int
    reputation_delta: int


class MissionsListResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    missions: list[MissionSummary]
    count: int


class ActiveMissionSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    rank: str
    title: str
    accepted_at_year: int
    completed_at_year: int | None = None
    success: bool | None = None


class ActiveMissionsListResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    missions: list[ActiveMissionSummary]
    count: int


class AcceptMissionRequest(BaseModel):
    """Accept a mission generated by the API (the server regenerates it from seed)."""

    model_config = ConfigDict(frozen=True)

    mission_id: str = Field(
        description="Mission id returned by GET /missions/available.",
    )


class SubmitMissionRequest(BaseModel):
    """Submit a finished mission."""

    model_config = ConfigDict(frozen=True)

    mission_id: str
    success: bool


class SubmitMissionResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    mission_id: str
    success: bool
    ryos_gained: int
    new_money: int
    stat_changes: list[dict[str, Any]] = Field(default_factory=list)


# === Inventory / Shop =================================================


class InventoryItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    item_id: str
    name: str | None = None  # Phase i18n.9 : lang-resolu (fallback name_fr)
    name_fr: str | None = None
    quantity: int
    category: str  # "weapon", "scroll", "consumable", "tool", ...


class InventoryResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    save_id: str
    money_ryos: int
    items: list[InventoryItem]


class ShopItemSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str = ""  # Phase i18n.9 : lang-resolu (fallback name_fr)
    name_fr: str
    category: str
    price_ryos: int
    description: str = ""  # Phase i18n.9 : lang-resolu (fallback description_fr)
    description_fr: str


class ShopInventoryResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    village_id: str
    items: list[ShopItemSummary]


class BuyItemRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    item_id: str


class SellItemRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    item_id: str


class UseItemRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    item_id: str


class ItemActionResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    item_id: str
    message: str
    new_money: int


# === Skip / Fast-forward ==============================================


class SkipTimeRequest(BaseModel):
    """Skip N days, weeks, or months of game time."""

    model_config = ConfigDict(frozen=True)

    days: int = Field(default=0, ge=0)
    weeks: int = Field(default=0, ge=0)
    months: int = Field(default=0, ge=0)


class SkipTimeResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    new_year: int
    new_date: str
    new_hour: int
    days_skipped: int
    fired_event_ids: list[str] = Field(default_factory=list)


class FastForwardRequest(BaseModel):
    """Advance N months in passive mode (the world ticks without the player)."""

    model_config = ConfigDict(frozen=True)

    months: int = Field(default=1, ge=1, le=60)


class InitializeResponse(BaseModel):
    """Response to /play/{id}/initialize: bootstrap state Phase A/B/D/E + RAG + Director."""

    model_config = ConfigDict(frozen=True)

    save_id: str
    kg_initialized: bool = Field(
        description="True if KG canon+missions was populated or already present.",
    )
    kg_facts_count: int = 0
    personality_initialized: bool = Field(
        description="True if Phase D baselines were extracted.",
    )
    personality_baselines_count: int = 0
    agents_initialized: bool = Field(
        description="True if the Phase E roster (top-15 + secondary 50) was populated.",
    )
    agents_count: int = 0
    rag_index_status: str = Field(
        default="skipped",
        description="'ready' / 'building' / 'failed' / 'skipped' (RAG Phase 3).",
    )
    director_state_initialized: bool = Field(
        default=False,
        description="True if DirectorState (Phase G) is on disk (loaded or created).",
    )
    goals_i18n_migrated: int = Field(
        default=0,
        description=(
            "Phase i18n.8: number of goals migrated (language detection + "
            "translation cache) during initialize. 0 if nothing to migrate."
        ),
    )
    goals_i18n_pending: int = Field(
        default=0,
        description=(
            "Phase i18n.8: number of goals whose translation remains "
            "pending (detected but Qwen down)."
        ),
    )
    errors: list[str] = Field(
        default_factory=list,
        description="Non-blocking errors per subsystem.",
    )


class FastForwardResponse(BaseModel):
    """Fast-forward response: events digest + final character age."""

    model_config = ConfigDict(frozen=True)

    months_simulated: int
    new_year: int
    new_date: str
    new_age: int
    fired_event_ids: list[str] = Field(default_factory=list)
    cancelled_event_ids: list[str] = Field(default_factory=list)
    substitute_injected: list[str] = Field(default_factory=list)
    llm_used: bool = False


# === Status views (read-only) =========================================


class BiographyEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    year: int
    age: int
    summary: str
    category: str


class RumorEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    summary: str
    born_at_year: int
    expires_at_year: int
    fidelity: float
    received_by_player: bool


class BreadcrumbEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    parent_goal_id: str
    sequence_index: int
    revealed: bool
    completed: bool


class ReputationEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    village_id: str
    score: int


class ReputationResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    save_id: str
    bingo_book_entry: bool
    reputation: list[ReputationEntry]


# === Canon (extension) =================================================


class CanonClanSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str | None = None
    name_romaji: str | None = None
    name_fr: str | None = None
    home_village: str | None = None
    kekkei_genkai: list[str] = Field(default_factory=list)


class CanonClansResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    clans: list[CanonClanSummary]
    count: int


class CanonOrganizationSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str | None = None
    name_romaji: str | None = None
    name_fr: str | None = None


class CanonOrganizationsResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    organizations: list[CanonOrganizationSummary]
    count: int


class CanonEraSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str | None = None
    name_fr: str | None = None
    year_start: int | None = None
    year_end: int | None = None
    key_figures: list[str] = Field(default_factory=list)


class CanonErasResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    eras: list[CanonEraSummary]
    count: int


class CanonKekkeiGenkaiSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str | None = None
    name_romaji: str | None = None
    name_fr: str | None = None
    associated_clans: list[str] = Field(default_factory=list)


class CanonKekkeiGenkaiResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    kekkei_genkai: list[CanonKekkeiGenkaiSummary]
    count: int


# === Weapons / Summons / Dialogues ====================================


class WeaponEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    weapon_id: str
    quantity: int
    quality: str


class WeaponsResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    save_id: str
    weapons: list[WeaponEntry]
    count: int


class SummonContractEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    description: str | None = None  # Phase i18n.9 : lang-resolu (fallback description_fr)
    description_fr: str | None = None


class SummonsResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    save_id: str
    contracts: list[SummonContractEntry]
    available_contracts: list[SummonContractEntry] = Field(
        description="List of all canonical contracts that can be signed.",
    )


class SignContractRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    contract_name: str = Field(
        description="e.g. toad, snake, slug, hawk, monkey, ninken, ...",
    )


class InvokeRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    contract_name: str


class InvokeResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    contract_name: str
    success: bool
    tier: str  # "minor", "major", "failed"
    message_fr: str
    chakra_after: int


class DialogueLine(BaseModel):
    model_config = ConfigDict(frozen=True)

    in_game_year: int
    in_game_date: str
    turn_number: int
    speaker: str
    text: str
    style: str | None = None


class DialoguesResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    save_id: str
    lines: list[DialogueLine]
    count: int


# === Phase A-H inspectors =============================================


class PersonalityResponse(BaseModel):
    """Snapshot of an NPC's personality vector + drift (Phase D)."""

    model_config = ConfigDict(frozen=True)

    npc_id: str
    available: bool
    baseline: dict[str, Any] | None = None
    drift: dict[str, Any] | None = None


class BeliefFact(BaseModel):
    model_config = ConfigDict(frozen=True)

    fact_id: str
    subject: str
    predicate: str
    object: str
    fidelity: float


class BeliefsResponse(BaseModel):
    """NPC sub-KG (Phase B §5.4: facts they know + fidelity)."""

    model_config = ConfigDict(frozen=True)

    npc_id: str
    available: bool
    facts: list[BeliefFact] = Field(default_factory=list)
    count: int = 0


class TensionEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    invariant_id: str
    severity: str
    summary_fr: str


class TensionsResponse(BaseModel):
    """Tensions detected by the Phase C invariants."""

    model_config = ConfigDict(frozen=True)

    save_id: str
    tensions: list[TensionEntry]
    count: int


class AgentSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    npc_id: str
    tier: str
    included_since_year: int | None = None
    last_simulated_turn: int | None = None


class AgentsRosterResponse(BaseModel):
    """List of Phase E agents (top-15 + secondary 50)."""

    model_config = ConfigDict(frozen=True)

    save_id: str
    agents: list[AgentSummary]
    count: int


class AgentDetailResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    npc_id: str
    available: bool
    tier: str | None = None
    recent_actions: list[dict[str, Any]] = Field(default_factory=list)
    memory_snippets: list[dict[str, Any]] = Field(default_factory=list)


# === Canon (extension complete) =======================================


class CanonLocationSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str | None = None
    name_romaji: str | None = None
    name_fr: str | None = None
    location_type: str | None = None
    parent_location: str | None = None


class CanonLocationsResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    locations: list[CanonLocationSummary]
    count: int


class CanonTailedBeastSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str | None = None
    name_romaji: str | None = None
    name_fr: str | None = None
    tails: int | None = None


class CanonTailedBeastsResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    tailed_beasts: list[CanonTailedBeastSummary]
    count: int


class CanonHidenSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str | None = None
    name_romaji: str | None = None
    name_fr: str | None = None
    associated_clan: str | None = None


class CanonHidenResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    hiden: list[CanonHidenSummary]
    count: int


class CanonWeaponToolSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str | None = None
    name_romaji: str | None = None
    name_fr: str | None = None
    category: str | None = None


class CanonWeaponsToolsResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    weapons_tools: list[CanonWeaponToolSummary]
    count: int


class CanonNatureSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str | None = None
    name_romaji: str | None = None
    name_fr: str | None = None
    type: str | None = None


class CanonNaturesResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    natures: list[CanonNatureSummary]
    count: int


class CanonTimelineEventSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str | None = None
    name_fr: str | None = None
    year: int | None = None
    arc: str | None = None
    involves: list[str] = Field(default_factory=list)


class CanonTimelineEventsResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    events: list[CanonTimelineEventSummary]
    total: int
    offset: int
    limit: int


class CanonVoiceProfileSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    speaker_id: str | None = None
    style_fr: str | None = None


class CanonVoiceProfilesResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    voice_profiles: list[CanonVoiceProfileSummary]
    count: int


# === Player views (techniques, relationships, pathfinder) =============


class TechniqueKnownEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    technique_id: str
    mastery_level: float
    learned_year: int
    learned_from: str | None = None
    times_used: int


class TechniqueInProgressEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    technique_id: str
    progress_hours: int
    progress_required: int
    started_year: int
    teacher_id: str | None = None


class TechniquesResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    save_id: str
    known: list[TechniqueKnownEntry]
    in_progress: list[TechniqueInProgressEntry]


class RelationshipEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    with_character_id: str
    type: str
    affinity: int
    trust: int


class RelationshipsResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    save_id: str
    relationships: list[RelationshipEntry]
    count: int


class PathStepEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    description: str = ""  # Phase i18n.9 : lang-resolu (fallback description_fr)
    description_fr: str
    sequence_index: int


class PathfinderResponse(BaseModel):
    """LLM pathfinder response, or degraded if the LLM is unavailable."""

    model_config = ConfigDict(frozen=True)

    goal_id: str
    available: bool
    next_step_fr: str | None = None
    breadcrumb_id: str | None = None
    error: str | None = None


# === Canon (datasets restants + Phase H) ==============================


class CanonRankSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str | None = None
    name_romaji: str | None = None
    name_fr: str | None = None
    level: int | None = None
    min_age: int | None = None
    typical_max_age: int | None = None


class CanonRanksResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    ranks: list[CanonRankSummary]
    count: int


class CanonWorldRulesResponse(BaseModel):
    """Abstract world rules (chakra, learning, combat, social, ...)."""

    model_config = ConfigDict(frozen=True)

    chakra: dict[str, Any]
    learning: dict[str, Any]
    combat: dict[str, Any]
    social: dict[str, Any]
    economy: dict[str, Any]
    time: dict[str, Any]


class CanonKekkeiMoraResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    kekkei_mora: list[CanonKekkeiGenkaiSummary]
    count: int


class CanonPhaseHDatasetResponse(BaseModel):
    """Generic wrapper for the 5 Phase H datasets (LLM-enriched)."""

    model_config = ConfigDict(frozen=True)

    dataset_id: str
    available: bool
    payload: dict[str, Any] | list[Any] | None = None
    count: int | None = None


class CanonCharacterWikiResponse(BaseModel):
    """Phase i18n.9: canon character wiki sections in the active language.

    The 3 sections `Background`, `Personality`, `Abilities` are returned
    in the language resolved from Accept-Language or preferences.
    `language` echoes the code used for translation. `pending` is True
    if the translation could not be performed (Qwen down) and the EN
    source is served instead with a marker.
    """

    model_config = ConfigDict(frozen=True)

    canon_id: str
    language: str
    Background: str = ""
    Personality: str = ""
    Abilities: str = ""
    pending: bool = False
