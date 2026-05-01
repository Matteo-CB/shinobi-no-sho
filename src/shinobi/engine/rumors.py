"""Propagation des rumeurs dans le monde."""

from __future__ import annotations

import uuid
from typing import Literal

from shinobi.canon.models import TimelineEvent
from shinobi.engine.world import Rumor, WorldState

_RADIUS_FIDELITY = {
    "proximity": 0.95,
    "regional": 0.8,
    "international": 0.6,
    "secret": 0.5,
}


def make_rumor_from_event(
    event: TimelineEvent,
    *,
    born_at_year: int,
    radius: Literal["proximity", "regional", "international", "secret"] = "regional",
    fidelity_override: float | None = None,
) -> Rumor:
    """Cree une rumeur basee sur un evenement de timeline."""
    return Rumor(
        id=str(uuid.uuid4()),
        source_event_id=event.id,
        content=event.narrative_summary_fr,
        fidelity=fidelity_override
        if fidelity_override is not None
        else _RADIUS_FIDELITY.get(radius, 0.7),
        diffusion_radius=radius,
        born_at_year=born_at_year,
        expires_at_year=born_at_year + 5,
    )


def propagate_rumors(
    world: WorldState,
    new_rumors: list[Rumor],
) -> WorldState:
    """Insere de nouvelles rumeurs dans le monde."""
    if not new_rumors:
        return world
    return world.model_copy(update={"rumors": [*world.rumors, *new_rumors]})


def player_can_hear(
    rumor: Rumor,
    *,
    player_location: str,
    event_location: str,
    current_year: int,
) -> bool:
    """Determine si une rumeur peut atteindre le joueur."""
    if rumor.expires_at_year is not None and current_year > rumor.expires_at_year:
        return False
    if rumor.diffusion_radius == "secret":
        return False  # le joueur doit avoir un acces particulier
    if rumor.diffusion_radius == "proximity":
        return player_location == event_location
    return True


def receive_rumor(world: WorldState, rumor_id: str, *, year: int) -> WorldState:
    """Marque une rumeur comme recue par le joueur."""
    new_rumors = []
    for r in world.rumors:
        if r.id == rumor_id:
            new_rumors.append(
                r.model_copy(update={"received_by_player": True, "received_at_year": year})
            )
        else:
            new_rumors.append(r)
    return world.model_copy(update={"rumors": new_rumors})
