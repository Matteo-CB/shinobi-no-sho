"""Pipeline de resolution d'actions joueur.

Ne fait jamais d'appel reseau. Toute decision est deterministe a seed connu.
Aucune action n'est refusee ; au pire, elle aboutit a une impossibilite contextuelle.
"""

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
    declared_text: str | None = None  # texte original si action libre


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
    if action.action_type in (
        ActionType.train_stat,
        ActionType.train_technique,
        ActionType.research,
    ):
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
    """Pipeline complet de resolution d'une action."""
    action = inputs.action
    character = inputs.character

    # 1. Faisabilite de base : le joueur est mort ?
    if character.is_dead:
        return ActionResult(
            action=action,
            outcome=ActionOutcome.contextual_impossibility,
            summary_fr="Le personnage est decede. Aucune action possible.",
            seed_after=inputs.seed,
        )

    # 2. Calcul de difficulte et roll
    diff = difficulty_for(action, character)
    stat = relevant_stat(action, character)
    modifier = int(stat * 4)
    r = roll(inputs.seed, "1d20", modifier=modifier)
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

    duration = estimate_duration(
        action.action_type, action.parameters.get("duration_minutes_override")
    )
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
) -> tuple[Character, WorldState]:
    """Applique le resultat sur l'etat. Pure function, retourne nouveaux objets."""
    new_chakra = character.chakra
    if result.chakra_cost > 0:
        new_chakra = character.chakra.model_copy(
            update={"current": max(0, character.chakra.current - result.chakra_cost)}
        )
    new_world = world  # ticking time se fait au niveau superieur
    new_character = character.with_chakra(new_chakra)
    return new_character, new_world
