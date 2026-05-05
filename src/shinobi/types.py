"""Type aliases et enums communs."""

from __future__ import annotations

from enum import StrEnum

# Type aliases
CharacterId = str
TechniqueId = str
ClanId = str
VillageId = str
LocationId = str
EventId = str
GoalId = str
BreadcrumbId = str
SaveId = str


class Canonicity(StrEnum):
    """Hierarchie de canonicite (ordre du plus fiable au moins fiable)."""

    manga = "manga"
    boruto_manga = "boruto_manga"
    tbv = "tbv"
    databook = "databook"
    movie_canon = "movie_canon"
    movie_filler = "movie_filler"
    anime_filler = "anime_filler"
    novel = "novel"
    game = "game"


class Gender(StrEnum):
    """Identite de genre."""

    male = "male"
    female = "female"
    non_binary = "non_binary"


class AttentionLevel(StrEnum):
    """Niveau d'attention du moteur sur un PNJ."""

    high = "HIGH"
    medium = "MEDIUM"
    low = "LOW"
    dormant = "DORMANT"


class TechniqueRank(StrEnum):
    """Rang d'une technique ninja."""

    e = "E"
    d = "D"
    c = "C"
    b = "B"
    a = "A"
    s = "S"
    forbidden = "forbidden"


class TechniqueCategory(StrEnum):
    """Categorie de technique."""

    ninjutsu = "ninjutsu"
    taijutsu = "taijutsu"
    genjutsu = "genjutsu"
    kenjutsu = "kenjutsu"
    bukijutsu = "bukijutsu"
    fuinjutsu = "fuinjutsu"
    juinjutsu = "juinjutsu"
    senjutsu = "senjutsu"
    iryo_ninjutsu = "iryo_ninjutsu"
    kinjutsu = "kinjutsu"
    hijutsu = "hijutsu"
    kekkei_genkai = "kekkei_genkai"
    kekkei_mora = "kekkei_mora"
    dojutsu_ability = "dojutsu_ability"
    unique_ability = "unique_ability"
    summoning = "summoning"
    barrier = "barrier"


class ChunkType(StrEnum):
    """Type de chunk dans le RAG."""

    character = "character"
    technique = "technique"
    clan = "clan"
    village = "village"
    event = "event"
    lore = "lore"
    dialogue = "dialogue"


class GoalStatus(StrEnum):
    """Statut d'un objectif declare."""

    declared = "declared"
    in_progress = "in_progress"
    completed = "completed"
    abandoned = "abandoned"
    failed = "failed"


class ActionType(StrEnum):
    """Type d'action joueur."""

    move = "move"
    talk = "talk"
    train_stat = "train_stat"
    train_technique = "train_technique"
    use_technique = "use_technique"
    fight = "fight"
    spy = "spy"
    steal = "steal"
    buy = "buy"
    sell = "sell"
    work = "work"
    rest = "rest"
    meditate = "meditate"
    research = "research"
    declare_goal = "declare_goal"
    request_objective_path = "request_objective_path"
    pay_for_information = "pay_for_information"
    accept_mission = "accept_mission"
    submit_mission = "submit_mission"
    challenge = "challenge"
    seduce = "seduce"
    intimidate = "intimidate"
    bribe = "bribe"
    pray = "pray"
    wait = "wait"
    custom = "custom"


class ActionOutcome(StrEnum):
    """Resultat d'une action resolue."""

    full_success = "full_success"
    partial_success = "partial_success"
    minor_failure = "minor_failure"
    catastrophic_failure = "catastrophic_failure"
    contextual_impossibility = "contextual_impossibility"


class KnowledgeLevel(StrEnum):
    """Niveau de connaissance d'un fait par le joueur."""

    rumor = "rumor"
    confirmed = "confirmed"
    witnessed = "witnessed"


class EventStatus(StrEnum):
    """Statut d'un evenement de timeline."""

    scheduled = "scheduled"
    triggered = "triggered"
    cancelled = "cancelled"
    modified = "modified"
    delayed = "delayed"


class CancellationStrategy(StrEnum):
    """Strategie a appliquer quand un evenement de timeline ne peut plus avoir lieu."""

    hard_cancel = "hard_cancel"
    substitute = "substitute"
    delay = "delay"
    cascade_cancel = "cascade_cancel"
    narrative_resolution = "narrative_resolution"
