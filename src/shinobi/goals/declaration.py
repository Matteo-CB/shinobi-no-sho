"""Declaration d'objectifs par le joueur."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field

from shinobi.types import GoalStatus


class GoalTargetType:
    """Constantes pour les types d'objectif."""

    learn_technique = "learn_technique"
    achieve_rank = "achieve_rank"
    kill_character = "kill_character"
    befriend_character = "befriend_character"
    marry_character = "marry_character"
    join_organization = "join_organization"
    leave_village = "leave_village"
    found_organization = "found_organization"
    obtain_object = "obtain_object"
    survive_event = "survive_event"
    prevent_event = "prevent_event"
    cause_event = "cause_event"
    master_kekkei_genkai = "master_kekkei_genkai"
    master_nature = "master_nature"
    revive_character = "revive_character"
    transcend_humanity = "transcend_humanity"
    free_form = "free_form"


class Goal(BaseModel):
    """Objectif declare."""

    model_config = ConfigDict(frozen=True)

    id: str
    declared_at_year: int
    declared_at_age: int
    description_player: str
    interpretation_canonical: str
    target_type: str = GoalTargetType.free_form
    target_id: str | None = None
    status: GoalStatus = GoalStatus.declared
    declared_priority: int = 5
    breadcrumbs: list[str] = Field(default_factory=list)
    completed_at_year: int | None = None
    abandoned_at_year: int | None = None


def declare_goal(
    *,
    description_player: str,
    interpretation_canonical: str,
    declared_at_year: int,
    declared_at_age: int,
    target_type: str = GoalTargetType.free_form,
    target_id: str | None = None,
    declared_priority: int = 5,
) -> Goal:
    """Cree un nouveau Goal avec un id unique."""
    return Goal(
        id=str(uuid.uuid4()),
        declared_at_year=declared_at_year,
        declared_at_age=declared_at_age,
        description_player=description_player,
        interpretation_canonical=interpretation_canonical,
        target_type=target_type,
        target_id=target_id,
        declared_priority=declared_priority,
    )


def abandon_goal(goal: Goal, year: int) -> Goal:
    return goal.model_copy(update={"status": GoalStatus.abandoned, "abandoned_at_year": year})


def complete_goal(goal: Goal, year: int) -> Goal:
    return goal.model_copy(update={"status": GoalStatus.completed, "completed_at_year": year})
