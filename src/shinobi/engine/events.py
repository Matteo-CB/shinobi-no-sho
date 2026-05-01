"""Scheduler des evenements canon de la timeline."""

from __future__ import annotations

from shinobi.canon.models import CanonBundle, EventPrecondition
from shinobi.engine.world import (
    CancelledEvent,
    CompletedEvent,
    ScheduledEvent,
    WorldState,
)
from shinobi.types import EventStatus


def initialize_scheduler(
    canon: CanonBundle,
    *,
    starting_year: int,
) -> list[ScheduledEvent]:
    """Liste les evenements canon dont la date est >= starting_year."""
    out: list[ScheduledEvent] = []
    for ev in canon.timeline_events.values():
        if ev.year >= starting_year:
            out.append(ScheduledEvent(event_id=ev.id, year=ev.year, date=ev.date))
    return sorted(out, key=lambda e: (e.year, e.date or ""))


def evaluate_precondition(
    pre: EventPrecondition,
    *,
    world: WorldState,
    canon: CanonBundle,
) -> bool:
    """Evalue une precondition simple sur l'etat courant."""
    params = pre.parameters
    if pre.type == "character_alive":
        cid = params.get("character_id")
        npc = world.npc_states.get(cid)
        if npc is None:
            char = canon.characters.get(cid)
            if char is None:
                return False
            if char.death_year is not None and world.current_year >= char.death_year:
                return False
            return True
        return npc.is_alive
    if pre.type == "no_event_triggered":
        eid = params.get("event_id")
        return all(c.event_id != eid for c in world.completed_events)
    if pre.type == "clan_active":
        clan_id = params.get("clan_id")
        clan = canon.clans.get(clan_id)
        if clan is None:
            return False
        for entry in clan.status_by_era:
            if entry.from_year <= world.current_year and (
                entry.to_year is None or world.current_year < entry.to_year
            ):
                if entry.status == "extinct":
                    return False
        return True
    if pre.type == "jinchuuriki_held_by":
        beast_id = params.get("beast")
        holder = params.get("jinchuuriki_id")
        beast = canon.tailed_beasts.get(beast_id)
        if beast is None:
            return False
        for entry in beast.current_jinchuuriki_by_era:
            if entry.from_year <= world.current_year and (
                entry.to_year is None or world.current_year < entry.to_year
            ):
                return entry.jinchuuriki == holder
        return False
    return True


def tick_scheduler(
    world: WorldState,
    canon: CanonBundle,
    *,
    turn_number: int,
) -> tuple[WorldState, list[CompletedEvent], list[CancelledEvent]]:
    """Avance le scheduler d'evenements pour la date courante du monde.

    Pour chaque evenement scheduled dont la date est passee, evalue les preconditions.
    Si toutes ok, marque triggered. Sinon applique la strategie de cancellation.
    """
    new_scheduled: list[ScheduledEvent] = []
    new_completed: list[CompletedEvent] = list(world.completed_events)
    new_cancelled: list[CancelledEvent] = list(world.cancelled_events)
    fired: list[CompletedEvent] = []
    cancelled: list[CancelledEvent] = []

    for ev in world.scheduled_events:
        if ev.status != EventStatus.scheduled:
            new_scheduled.append(ev)
            continue
        if not _date_reached(ev, world):
            new_scheduled.append(ev)
            continue
        canon_ev = canon.timeline_events.get(ev.event_id)
        if canon_ev is None:
            new_scheduled.append(ev)
            continue
        all_ok = all(
            evaluate_precondition(p, world=world, canon=canon) for p in canon_ev.preconditions
        )
        if all_ok:
            triggered = ev.model_copy(
                update={"status": EventStatus.triggered, "triggered_at_turn": turn_number}
            )
            new_scheduled.append(triggered)
            completed = CompletedEvent(
                event_id=ev.event_id,
                triggered_at_turn=turn_number,
                triggered_at_year=world.current_year,
            )
            new_completed.append(completed)
            fired.append(completed)
        else:
            strategy = canon_ev.cancellation_strategy.type
            if strategy == "delay":
                new_scheduled.append(ev.model_copy(update={"year": ev.year + 1}))
                continue
            cancelled_ev = CancelledEvent(
                event_id=ev.event_id,
                cancelled_at_turn=turn_number,
                cancelled_at_year=world.current_year,
                reason="precondition violated",
            )
            new_cancelled.append(cancelled_ev)
            cancelled.append(cancelled_ev)
            new_scheduled.append(
                ev.model_copy(
                    update={"status": EventStatus.cancelled, "notes": "precondition violated"}
                )
            )

    new_world = world.model_copy(
        update={
            "scheduled_events": new_scheduled,
            "completed_events": new_completed,
            "cancelled_events": new_cancelled,
        }
    )
    return new_world, fired, cancelled


def _date_reached(ev: ScheduledEvent, world: WorldState) -> bool:
    if ev.year > world.current_year:
        return False
    if ev.year < world.current_year:
        return True
    if ev.date is None:
        return True
    return ev.date <= world.current_date
