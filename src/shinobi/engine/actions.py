"""Pipeline de resolution d'actions joueur avec effets reels sur l'etat."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from shinobi.constants import (
    DIFFICULTY_EASY,
    DIFFICULTY_HARD,
    DIFFICULTY_MODERATE,
    DIFFICULTY_TRIVIAL,
    DIFFICULTY_VERY_HARD,
)
from shinobi.engine.character import Character
from shinobi.engine.missions import Mission
from shinobi.engine.progression import (
    StatChange,
    add_money,
    apply_chakra_cost,
    apply_damage,
    apply_fatigue,
    apply_meditation,
    apply_rest,
    apply_sleep,
    train_stat,
)
from shinobi.engine.rng import roll
from shinobi.engine.stats import average_combat_stat
from shinobi.engine.time import estimate_duration
from shinobi.engine.world import WorldState
from shinobi.types import ActionOutcome, ActionType


class Action(BaseModel):
    """Action declaree par le joueur."""

    model_config = ConfigDict(frozen=True)

    action_type: ActionType
    summary: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    target_id: str | None = None
    declared_text: str | None = None


class ActionResult(BaseModel):
    """Resultat d'une action."""

    model_config = ConfigDict(frozen=True)

    action: Action
    outcome: ActionOutcome
    summary_fr: str
    consequences: list[str] = Field(default_factory=list)
    chakra_cost: int = 0
    duration_minutes: int = 0
    seed_after: int = 0
    stat_changes: list[dict[str, Any]] = Field(default_factory=list)
    money_delta: int = 0
    hp_delta: int = 0
    fatigue_delta: int = 0


@dataclass
class ResolutionInputs:
    """Donnees passees au resolver pour resoudre une action."""

    character: Character
    world: WorldState
    action: Action
    seed: int


def difficulty_for(action: Action, character: Character) -> int:
    """Difficulte par defaut selon le type d'action."""
    if action.action_type in (ActionType.rest, ActionType.meditate, ActionType.wait):
        return DIFFICULTY_TRIVIAL
    if action.action_type in (ActionType.talk, ActionType.move, ActionType.buy, ActionType.sell):
        return DIFFICULTY_EASY
    if action.action_type in (
        ActionType.train_stat,
        ActionType.train_technique,
        ActionType.work,
        ActionType.research,
    ):
        return DIFFICULTY_MODERATE
    if action.action_type in (
        ActionType.spy,
        ActionType.steal,
        ActionType.intimidate,
        ActionType.seduce,
        ActionType.bribe,
        ActionType.fight,
        ActionType.use_technique,
    ):
        return DIFFICULTY_HARD
    if action.action_type == ActionType.challenge:
        return DIFFICULTY_VERY_HARD
    return DIFFICULTY_MODERATE


def relevant_stat(action: Action, character: Character) -> float:
    """Choix de la stat principale selon l'action."""
    s = character.stats
    es = character.extended_stats
    if action.action_type == ActionType.fight:
        return average_combat_stat(s)
    if action.action_type in (ActionType.train_stat, ActionType.train_technique, ActionType.research):
        return es.learning_genius
    if action.action_type in (ActionType.talk, ActionType.seduce, ActionType.bribe):
        return es.social_charisma
    if action.action_type == ActionType.intimidate:
        return s.strength
    if action.action_type in (ActionType.move, ActionType.spy, ActionType.steal):
        return s.speed
    if action.action_type == ActionType.use_technique:
        return s.ninjutsu
    if action.action_type == ActionType.meditate:
        return es.willpower
    if action.action_type == ActionType.work:
        return es.willpower
    return s.intelligence


def resolve_action(inputs: ResolutionInputs) -> ActionResult:
    """Pipeline de resolution. Calcule outcome + duration."""
    action = inputs.action
    character = inputs.character

    if character.is_dead:
        return ActionResult(
            action=action,
            outcome=ActionOutcome.contextual_impossibility,
            summary_fr="Le personnage est decede. Aucune action possible.",
            seed_after=inputs.seed,
        )

    diff = difficulty_for(action, character)
    stat = relevant_stat(action, character)
    modifier = int(stat * 4)

    # Penalite de fatigue : plus tu es fatigue, plus c'est dur.
    fatigue_penalty = -(character.health.fatigue // 25)
    # Penalite de manque de chakra
    chakra_ratio = character.chakra.current / max(1, character.chakra.max)
    chakra_penalty = -3 if chakra_ratio < 0.2 else 0

    r = roll(inputs.seed, "1d20", modifier=modifier + fatigue_penalty + chakra_penalty)
    margin = r.total - diff

    if margin >= 10:
        outcome = ActionOutcome.full_success
        summary = f"Reussite eclatante. {action.summary}"
    elif margin >= 0:
        outcome = ActionOutcome.full_success
        summary = f"Reussite. {action.summary}"
    elif margin >= -5:
        outcome = ActionOutcome.partial_success
        summary = f"Reussite partielle, avec consequences. {action.summary}"
    elif margin >= -10:
        outcome = ActionOutcome.minor_failure
        summary = f"Echec sans degat majeur. {action.summary}"
    else:
        outcome = ActionOutcome.catastrophic_failure
        summary = f"Echec critique. {action.summary}"

    duration_hours = int(action.parameters.get("duration_hours", 0))
    if duration_hours > 0:
        duration = duration_hours * 60
    else:
        duration = estimate_duration(action.action_type, action.parameters.get("duration_minutes_override"))
    chakra_cost = int(action.parameters.get("chakra_cost", 0))

    return ActionResult(
        action=action,
        outcome=outcome,
        summary_fr=summary,
        consequences=[],
        chakra_cost=chakra_cost,
        duration_minutes=duration,
        seed_after=r.seed_after,
    )


def apply_action_to_state(
    character: Character,
    world: WorldState,
    result: ActionResult,
) -> tuple[Character, WorldState, ActionResult]:
    """Applique le resultat sur l'etat du personnage. Retourne (char, world, result enrichi)."""
    action = result.action
    duration_hours = max(1, result.duration_minutes // 60)

    stat_changes: list[StatChange] = []
    money_delta = 0
    hp_delta = 0
    fatigue_delta = 0
    chakra_cost = result.chakra_cost

    success = result.outcome in (ActionOutcome.full_success, ActionOutcome.partial_success)
    quality = 1.0 if result.outcome == ActionOutcome.full_success else (0.6 if result.outcome == ActionOutcome.partial_success else 0.2)

    new_char = character

    if action.action_type == ActionType.train_stat:
        stat_name = str(action.parameters.get("stat", "stamina"))
        new_char, change = train_stat(new_char, stat_name, hours=duration_hours, quality_modifier=quality)
        if change:
            stat_changes.append(change)
        # Cout de fatigue proportionnel
        new_char = apply_fatigue(new_char, duration_hours * 2)
        fatigue_delta = duration_hours * 2

    elif action.action_type == ActionType.train_technique:
        # Sans technique target connue, on entraine intelligence + chakra_control
        new_char, c1 = train_stat(new_char, "intelligence", hours=duration_hours // 2, quality_modifier=quality)
        new_char, c2 = train_stat(new_char, "chakra_control", hours=duration_hours // 2, quality_modifier=quality)
        for c in (c1, c2):
            if c:
                stat_changes.append(c)
        new_char = apply_fatigue(new_char, duration_hours * 3)
        fatigue_delta = duration_hours * 3

    elif action.action_type == ActionType.rest:
        sleep = bool(action.parameters.get("sleep", False))
        new_char = apply_sleep(new_char, hours=duration_hours) if sleep else apply_rest(new_char, hours=duration_hours)
        fatigue_delta = -(character.health.fatigue - new_char.health.fatigue)

    elif action.action_type == ActionType.meditate:
        new_char = apply_meditation(new_char, hours=duration_hours)

    elif action.action_type == ActionType.work:
        # Travail civil : gain de ryos selon duree + qualite
        gain = int(50 * duration_hours * quality) if success else 0
        new_char = add_money(new_char, gain)
        new_char = apply_fatigue(new_char, duration_hours)
        money_delta = gain
        fatigue_delta = duration_hours

    elif action.action_type == ActionType.fight:
        # Si echec critique, le perso prend des degats
        if result.outcome == ActionOutcome.catastrophic_failure:
            damage = 30 + duration_hours * 5
            new_char = apply_damage(new_char, damage, description="blessure de combat")
            hp_delta = -damage
        elif result.outcome == ActionOutcome.minor_failure:
            damage = 10
            new_char = apply_damage(new_char, damage)
            hp_delta = -damage
        new_char = apply_fatigue(new_char, 10)
        fatigue_delta = 10
        if chakra_cost == 0:
            chakra_cost = 20

    elif action.action_type == ActionType.use_technique:
        # Cout de chakra par defaut
        if chakra_cost == 0:
            chakra_cost = 25

    elif action.action_type == ActionType.research:
        new_char, c = train_stat(new_char, "intelligence", hours=duration_hours, quality_modifier=quality * 0.5)
        if c:
            stat_changes.append(c)

    elif action.action_type == ActionType.steal:
        if success:
            gain = int(200 * quality)
            new_char = add_money(new_char, gain)
            money_delta = gain
        elif result.outcome == ActionOutcome.catastrophic_failure:
            new_char = apply_damage(new_char, 5, description="prise au vol")
            hp_delta = -5

    elif action.action_type == ActionType.bribe:
        cost = int(action.parameters.get("amount", 100))
        if character.money >= cost:
            new_char = add_money(new_char, -cost)
            money_delta = -cost

    if chakra_cost > 0:
        new_char = apply_chakra_cost(new_char, chakra_cost)

    # Toute action consomme un peu de chakra et d'energie au minimum
    if action.action_type not in (ActionType.rest, ActionType.meditate, ActionType.wait):
        passive_chakra = max(1, duration_hours // 2)
        new_char = apply_chakra_cost(new_char, passive_chakra)

    new_result = result.model_copy(
        update={
            "stat_changes": [
                {"stat": c.stat_name, "old": c.old, "new": c.new, "delta": c.delta}
                for c in stat_changes
            ],
            "money_delta": money_delta,
            "hp_delta": hp_delta,
            "fatigue_delta": fatigue_delta,
        }
    )
    return new_char, world, new_result


def apply_mission_result(
    character: Character,
    mission: Mission,
    *,
    success: bool,
) -> tuple[Character, int]:
    """Applique le resultat d'une mission. Retourne (char, ryos_gagnes)."""
    if not success:
        new_char = apply_damage(character, 15, description=f"echec de mission {mission.title}")
        new_char = apply_fatigue(new_char, 30)
        return new_char, 0
    new_char = add_money(character, mission.reward_ryos)
    new_char = apply_fatigue(new_char, 20)
    return new_char, mission.reward_ryos
