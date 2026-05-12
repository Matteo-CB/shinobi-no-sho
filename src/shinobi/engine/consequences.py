"""Consequences emergentes : actions modifient indirectement les stats.

Philosophie : un perso qui combat reellement gagne du taijutsu meme s'il ne
s'entraine pas explicitement, un perso qui parle beaucoup gagne du charisme,
un perso qui rate apprend de ses erreurs (willpower + perception).

Anti-abus :
- Petits gains par action (3-8h equivalentes pour les actions concretes)
- Diminishing returns naturels via train_stat (plus la stat est haute, plus
  il faut d'effort)
- Echecs catastrophiques ne donnent pas plus que les succes (pas de farming)
- Consequences plafonnees a max 5 stats par action (pas un buffet)

Immersion :
- Chaque (action, stat) a une justification RP courte affichee au joueur
- Le narrateur LLM peut s'en inspirer pour la narration suivante
"""

from __future__ import annotations

from dataclasses import dataclass

from shinobi.engine.character import Character
from shinobi.engine.missions import Mission
from shinobi.engine.progression import StatChange, train_stat
from shinobi.i18n import t
from shinobi.types import ActionOutcome, ActionType


@dataclass(frozen=True)
class ConsequenceRule:
    """Une consequence : stat, heures equivalentes, cle i18n de justification."""

    stat: str
    hours: int
    why_key: str  # Cle i18n. La traduction est resolue a l'application.

    @property
    def why_fr(self) -> str:
        """Resout la justification localisee (compat retour API)."""
        return t(self.why_key)


def _rule(stat: str, hours: int, action_or_profile: str) -> ConsequenceRule:
    """Helper pour construire une cle i18n coherente : engine.consequences.{scope}.{stat}."""
    return ConsequenceRule(
        stat=stat,
        hours=hours,
        why_key=f"engine.consequences.{action_or_profile}.{stat}",
    )


# Pour chaque type d'action : liste de consequences possibles avec justifications.
# Les heures sont multipliees par OUTCOME_MULTIPLIERS.
CONSEQUENCE_RULES: dict[ActionType, list[ConsequenceRule]] = {
    ActionType.fight: [
        _rule("taijutsu", 8, "action.fight"),
        _rule("strength", 4, "action.fight"),
        _rule("speed", 3, "action.fight"),
        _rule("willpower", 5, "action.fight"),
        _rule("perception", 3, "action.fight"),
    ],
    ActionType.use_technique: [
        _rule("ninjutsu", 5, "action.use_technique"),
        _rule("hand_seals", 3, "action.use_technique"),
        _rule("chakra_control", 3, "action.use_technique"),
    ],
    ActionType.talk: [
        _rule("social_charisma", 3, "action.talk"),
        _rule("intelligence", 1, "action.talk"),
    ],
    ActionType.intimidate: [
        _rule("strength", 1, "action.intimidate"),
        _rule("social_charisma", 2, "action.intimidate"),
        _rule("leadership", 2, "action.intimidate"),
    ],
    ActionType.seduce: [
        _rule("social_charisma", 5, "action.seduce"),
        _rule("perception", 2, "action.seduce"),
    ],
    ActionType.spy: [
        _rule("perception", 6, "action.spy"),
        _rule("speed", 2, "action.spy"),
        _rule("intelligence", 3, "action.spy"),
    ],
    ActionType.steal: [
        _rule("speed", 4, "action.steal"),
        _rule("perception", 3, "action.steal"),
        _rule("intelligence", 2, "action.steal"),
    ],
    ActionType.bribe: [
        _rule("social_charisma", 3, "action.bribe"),
        _rule("intelligence", 1, "action.bribe"),
    ],
    ActionType.research: [
        _rule("intelligence", 6, "action.research"),
        _rule("perception", 1, "action.research"),
    ],
    ActionType.meditate: [
        _rule("willpower", 7, "action.meditate"),
        _rule("chakra_control", 3, "action.meditate"),
        _rule("senjutsu_aptitude", 1, "action.meditate"),
    ],
    ActionType.work: [
        _rule("willpower", 2, "action.work"),
        _rule("strength", 1, "action.work"),
    ],
    ActionType.move: [
        _rule("speed", 2, "action.move"),
        _rule("stamina", 2, "action.move"),
        _rule("perception", 1, "action.move"),
    ],
    ActionType.pray: [
        _rule("willpower", 4, "action.pray"),
    ],
    ActionType.challenge: [
        _rule("taijutsu", 6, "action.challenge"),
        _rule("willpower", 8, "action.challenge"),
        _rule("leadership", 3, "action.challenge"),
        _rule("perception", 2, "action.challenge"),
    ],
    ActionType.accept_mission: [
        _rule("willpower", 1, "action.accept_mission"),
    ],
    ActionType.submit_mission: [
        _rule("willpower", 2, "action.submit_mission"),
        _rule("social_charisma", 1, "action.submit_mission"),
    ],
    ActionType.buy: [
        _rule("intelligence", 1, "action.buy"),
        _rule("social_charisma", 1, "action.buy"),
    ],
    ActionType.sell: [
        _rule("social_charisma", 2, "action.sell"),
        _rule("intelligence", 1, "action.sell"),
    ],
    ActionType.declare_goal: [
        _rule("willpower", 3, "action.declare_goal"),
    ],
    ActionType.request_objective_path: [
        _rule("intelligence", 2, "action.request_objective_path"),
    ],
    ActionType.pay_for_information: [
        _rule("intelligence", 2, "action.pay_for_information"),
        _rule("social_charisma", 1, "action.pay_for_information"),
    ],
}

# Multiplicateur des consequences selon l'outcome.
OUTCOME_MULTIPLIERS: dict[ActionOutcome, float] = {
    ActionOutcome.full_success: 1.0,
    ActionOutcome.partial_success: 0.7,
    ActionOutcome.minor_failure: 0.4,
    ActionOutcome.catastrophic_failure: 0.6,
    ActionOutcome.contextual_impossibility: 0.0,
}

# Plafond de stats gagnees par action (anti-abus : pas un buffet)
MAX_STATS_PER_ACTION = 5


@dataclass(frozen=True)
class AppliedConsequence:
    """Une consequence effectivement appliquee, avec son contexte."""

    change: StatChange
    why_fr: str


def apply_action_consequences(
    character: Character,
    *,
    action_type: ActionType,
    outcome: ActionOutcome,
    duration_hours: int = 1,
) -> tuple[Character, list[StatChange], list[AppliedConsequence]]:
    """Applique les consequences passives d'une action.

    Retourne (character, stat_changes, applied_with_justifications).
    """
    rules = CONSEQUENCE_RULES.get(action_type, [])
    if not rules:
        return character, [], []
    multiplier = OUTCOME_MULTIPLIERS.get(outcome, 0.0)
    if multiplier <= 0:
        return character, [], []

    # Bonus duree : longue session = plus d'apprentissage (mais log scale)
    duration_bonus = 1.0
    if duration_hours >= 8:
        duration_bonus = 1.3
    if duration_hours >= 24:
        duration_bonus = 1.6
    if duration_hours >= 72:
        duration_bonus = 2.0

    changes: list[StatChange] = []
    applied: list[AppliedConsequence] = []
    current = character
    for rule in rules[:MAX_STATS_PER_ACTION]:
        actual_hours = max(1, int(rule.hours * multiplier * duration_bonus))
        new_char, change = train_stat(
            current, rule.stat, hours=actual_hours, quality_modifier=multiplier
        )
        if change:
            changes.append(change)
            applied.append(AppliedConsequence(change=change, why_fr=t(rule.why_key)))
            current = new_char
    return current, changes, applied


# Bonus de stats octroyes a la fin d'une mission selon son rang et son type.
RANK_TO_HOURS = {"D": 12, "C": 35, "B": 80, "A": 180, "S": 400}


# Categorie de mission deduite localement (locale-agnostique) depuis template_id.
_MISSION_PROFILE_BY_TEMPLATE: dict[str, str] = {
    # Combat / elimination
    "eliminate_chunin_deserter": "combat",
    "assassinate_jonin_deserter": "combat",
    "eliminate_renegade_sannin": "combat",
    "destroy_organized_bandits": "combat",
    "dismantle_bandits": "combat",
    "sabotage_invasion": "combat",
    # Infiltration / vol / capture
    "infiltrate_oto": "stealth",
    "recover_stolen_artifact": "stealth",
    "capture_jinchuuriki": "stealth",
    "steal_battle_plans": "stealth",
    # Escorte / protection
    "escort_merchant_local": "escort",
    "escort_merchant_intercountry": "escort",
    "protect_caravan": "escort",
    "escort_lord": "escort",
    "escort_diplomatic": "escort",
    "guard_shrine": "escort",
    # Diplomatique / politique
    "prevent_kage_plot": "diplomatic",
    # Investigation / enquete
    "recover_forbidden_scroll": "investigate",
    # Civil / quotidien
    "tora_cat": "civil",
    "harvest_help": "civil",
    "repair_fence": "civil",
}


def mission_consequences(
    character: Character,
    mission: Mission,
    *,
    success: bool,
) -> tuple[Character, list[AppliedConsequence]]:
    """Bonus de stats apres avoir reussi (ou rate) une mission."""
    base_hours = RANK_TO_HOURS.get(mission.rank, 12)
    if not success:
        rules = [
            _rule("willpower", base_hours // 2, "mission.failed"),
            _rule("perception", base_hours // 3, "mission.failed"),
        ]
        multiplier = 0.6
    else:
        rules = _profile_for_mission(mission, base_hours)
        multiplier = 1.0

    changes: list[AppliedConsequence] = []
    current = character
    for rule in rules:
        actual_hours = max(1, int(rule.hours * multiplier))
        new_char, change = train_stat(
            current, rule.stat, hours=actual_hours, quality_modifier=multiplier
        )
        if change:
            changes.append(AppliedConsequence(change=change, why_fr=t(rule.why_key)))
            current = new_char
    return current, changes


def _profile_for_mission(mission: Mission, base_hours: int) -> list[ConsequenceRule]:
    """Deduit les stats prioritairement gagnees selon le contexte de la mission."""
    profile = _MISSION_PROFILE_BY_TEMPLATE.get(mission.template_id, "civil")

    if profile == "combat":
        return [
            _rule("taijutsu", base_hours, "mission.combat"),
            _rule("willpower", base_hours, "mission.combat"),
            _rule("strength", base_hours // 2, "mission.combat"),
            _rule("ninjutsu", base_hours // 2, "mission.combat"),
            _rule("social_charisma", base_hours // 4, "mission.combat"),
        ]
    if profile == "stealth":
        return [
            _rule("speed", base_hours, "mission.stealth"),
            _rule("perception", base_hours, "mission.stealth"),
            _rule("intelligence", base_hours // 2, "mission.stealth"),
        ]
    if profile == "escort":
        return [
            _rule("willpower", base_hours, "mission.escort"),
            _rule("perception", base_hours, "mission.escort"),
            _rule("taijutsu", base_hours // 2, "mission.escort"),
            _rule("social_charisma", base_hours // 4, "mission.escort"),
        ]
    if profile == "diplomatic":
        return [
            _rule("social_charisma", base_hours, "mission.diplomatic"),
            _rule("leadership", base_hours, "mission.diplomatic"),
            _rule("intelligence", base_hours // 2, "mission.diplomatic"),
        ]
    if profile == "investigate":
        return [
            _rule("perception", base_hours, "mission.investigate"),
            _rule("intelligence", base_hours, "mission.investigate"),
            _rule("speed", base_hours // 2, "mission.investigate"),
        ]
    # Mission civile (chat egare, ferme, sanctuaire)
    return [
        _rule("willpower", base_hours // 2, "mission.civil"),
        _rule("social_charisma", base_hours // 2, "mission.civil"),
        _rule("perception", base_hours // 3, "mission.civil"),
    ]
