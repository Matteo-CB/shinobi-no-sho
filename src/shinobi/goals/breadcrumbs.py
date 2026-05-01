"""Modele Breadcrumb (sous-objectif a chemin canonique)."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class CompletionCondition(BaseModel):
    """Condition concrete pour valider un breadcrumb."""

    model_config = ConfigDict(frozen=True)

    type: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class BreadcrumbPrice(BaseModel):
    """Prix paye par le joueur pour debloquer un breadcrumb."""

    model_config = ConfigDict(frozen=True)

    type: Literal[
        "money",
        "favor",
        "sub_mission",
        "reputation",
        "secret",
        "physical",
        "moral",
        "political",
        "none",
    ] = "none"
    description: str = ""
    amount: float | None = None
    paid: bool = False
    paid_at_year: int | None = None


class Breadcrumb(BaseModel):
    """Sous-objectif concret deduit du chemin canonique."""

    model_config = ConfigDict(frozen=True)

    id: str
    parent_goal_id: str
    sequence_index: int
    description: str
    canonical_basis: str
    completion_conditions: list[CompletionCondition] = Field(default_factory=list)
    optional: bool = False
    revealed: bool = False
    revealed_at_year: int | None = None
    revealed_by_npc_id: str | None = None
    price_paid: BreadcrumbPrice | None = None
    completed: bool = False
    completed_at_year: int | None = None
    next_breadcrumbs: list[str] = Field(default_factory=list)


def make_breadcrumb(
    *,
    parent_goal_id: str,
    sequence_index: int,
    description: str,
    canonical_basis: str,
    completion_conditions: list[CompletionCondition],
    price: BreadcrumbPrice | None = None,
    revealed: bool = False,
    revealed_at_year: int | None = None,
    revealed_by_npc_id: str | None = None,
) -> Breadcrumb:
    return Breadcrumb(
        id=str(uuid.uuid4()),
        parent_goal_id=parent_goal_id,
        sequence_index=sequence_index,
        description=description,
        canonical_basis=canonical_basis,
        completion_conditions=completion_conditions,
        revealed=revealed,
        revealed_at_year=revealed_at_year,
        revealed_by_npc_id=revealed_by_npc_id,
        price_paid=price,
    )


def mark_completed(breadcrumb: Breadcrumb, year: int) -> Breadcrumb:
    return breadcrumb.model_copy(update={"completed": True, "completed_at_year": year})


def mark_revealed(
    breadcrumb: Breadcrumb,
    *,
    year: int,
    revealed_by_npc_id: str | None = None,
    price_paid: BreadcrumbPrice | None = None,
) -> Breadcrumb:
    return breadcrumb.model_copy(
        update={
            "revealed": True,
            "revealed_at_year": year,
            "revealed_by_npc_id": revealed_by_npc_id,
            "price_paid": price_paid,
        }
    )
