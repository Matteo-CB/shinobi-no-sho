"""Temps in-game et progression du temps avec actions."""

from __future__ import annotations

from shinobi.types import ActionType
from shinobi.utils.time_utils import MINUTES_PER_HOUR, GameDate

# Duree typique en minutes pour chaque type d'action.
DEFAULT_DURATION_MINUTES: dict[ActionType, int] = {
    ActionType.move: 60,
    ActionType.talk: 5,
    ActionType.train_stat: 4 * MINUTES_PER_HOUR,
    ActionType.train_technique: 8 * MINUTES_PER_HOUR,
    ActionType.use_technique: 5,
    ActionType.fight: 30,
    ActionType.spy: 2 * MINUTES_PER_HOUR,
    ActionType.steal: 15,
    ActionType.buy: 30,
    ActionType.sell: 30,
    ActionType.work: 6 * MINUTES_PER_HOUR,
    ActionType.rest: 8 * MINUTES_PER_HOUR,
    ActionType.meditate: MINUTES_PER_HOUR,
    ActionType.research: 2 * MINUTES_PER_HOUR,
    ActionType.declare_goal: 1,
    ActionType.request_objective_path: 1,
    ActionType.pay_for_information: 30,
    ActionType.accept_mission: 15,
    ActionType.submit_mission: 30,
    ActionType.challenge: 30,
    ActionType.seduce: 30,
    ActionType.intimidate: 10,
    ActionType.bribe: 10,
    ActionType.pray: 30,
    ActionType.wait: MINUTES_PER_HOUR,
    ActionType.custom: 30,
}


def estimate_duration(action_type: ActionType, override: int | None = None) -> int:
    """Estimation en minutes d'une action."""
    if override is not None:
        return max(1, override)
    return DEFAULT_DURATION_MINUTES.get(action_type, 30)


def advance_time(date: GameDate, minutes: int) -> GameDate:
    """Avance la date in-game de N minutes."""
    return date.add_minutes(minutes)
