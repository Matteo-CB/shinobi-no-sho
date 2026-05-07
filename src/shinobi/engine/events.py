"""Scheduler des evenements canon de la timeline."""

from __future__ import annotations

from shinobi.canon.models import CanonBundle, EventPrecondition
from shinobi.engine.rumors import make_rumor_from_event
from shinobi.engine.world import (
    CancelledEvent,
    CompletedEvent,
    Rumor,
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
    Si toutes ok, marque triggered + propage rumeur. Sinon strategie de cancellation.
    """
    new_scheduled: list[ScheduledEvent] = []
    new_completed: list[CompletedEvent] = list(world.completed_events)
    new_cancelled: list[CancelledEvent] = list(world.cancelled_events)
    new_rumors: list[Rumor] = list(world.rumors)
    fired: list[CompletedEvent] = []
    cancelled: list[CancelledEvent] = []
    # Round 29 : GC les substitute_events dont l'event correspondant transitionne
    # vers un etat terminal (triggered/cancelled). Apres terminal, plus aucun
    # lookup ne se fait (on continue ligne 98 si status != scheduled), donc
    # garder le dict en memoire/save n'apporte rien et grossit chaque tick.
    terminal_substitute_ids: set[str] = set()

    for ev in world.scheduled_events:
        if ev.status != EventStatus.scheduled:
            new_scheduled.append(ev)
            continue
        if not _date_reached(ev, world):
            new_scheduled.append(ev)
            continue
        canon_ev = canon.timeline_events.get(ev.event_id)
        # Phase F : si l'event_id est un substitute (prefixe substitute_*)
        # on le lookup dans world.substitute_events au lieu de canon.
        sub_ev_dict = world.substitute_events.get(ev.event_id) if canon_ev is None else None
        if canon_ev is None and sub_ev_dict is None:
            new_scheduled.append(ev)
            continue
        # Construit un objet pseudo-canon avec les fields necessaires au trigger.
        # (preconditions + name_fr utilises par make_rumor_from_event)
        if canon_ev is not None:
            preconditions = canon_ev.preconditions
            name_fr = canon_ev.name_fr or ""
            cancellation_strategy_type = canon_ev.cancellation_strategy.type
            event_for_rumor = canon_ev
        else:
            from shinobi.canon.models import EventPrecondition as _Pre
            # Round 52 : reconstruction defensive. Pydantic enforce dict[str, dict]
            # au niveau WorldState mais l'inner dict est non-structure ;
            # une save corrompue / import externe pourrait produire un type
            # imprevu (preconditions=str, name_fr=dict, ...). On guard chaque
            # field contre crash mid-iteration.
            raw_preconditions = sub_ev_dict.get("preconditions")
            if not isinstance(raw_preconditions, list):
                raw_preconditions = []
            preconditions = [
                _Pre(
                    type=p.get("type", "") if isinstance(p, dict) else "",
                    parameters=(
                        p.get("parameters") or {}
                        if isinstance(p, dict) else {}
                    ),
                )
                for p in raw_preconditions
                if isinstance(p, dict)
            ]
            raw_name = sub_ev_dict.get("name_fr")
            name_fr = raw_name if isinstance(raw_name, str) else ""
            raw_strategy = sub_ev_dict.get(
                "cancellation_strategy_type", "substitute",
            )
            cancellation_strategy_type = (
                raw_strategy if isinstance(raw_strategy, str) else "substitute"
            )
            event_for_rumor = canon_ev  # None ; rumor skip si pas canon
        all_ok = all(
            evaluate_precondition(p, world=world, canon=canon)
            for p in preconditions
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
            # Round 29 : marque le substitute pour GC apres trigger
            if sub_ev_dict is not None:
                terminal_substitute_ids.add(ev.event_id)
            # Phase F : rumeur seulement pour events canon (le SubstituteEvent
            # a deja sa propre rumor via injector). Sinon make_rumor_from_event
            # crash sur object None.
            if canon_ev is not None:
                radius = "international" if any(
                    kw in name_fr.lower()
                    for kw in ("guerre", "kage", "kyuubi", "akatsuki", "uchiha", "konoha")
                ) else "regional"
                rumor = make_rumor_from_event(
                    canon_ev, born_at_year=world.current_year, radius=radius,
                )
                new_rumors.append(rumor)
        else:
            if cancellation_strategy_type == "delay":
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
            # Round 29 : marque le substitute pour GC apres cancel terminal
            if sub_ev_dict is not None:
                terminal_substitute_ids.add(ev.event_id)
            new_scheduled.append(
                ev.model_copy(
                    update={"status": EventStatus.cancelled, "notes": "precondition violated"}
                )
            )

    # Round 29 : GC les entrees substitute_events dont l'event est passe en
    # terminal (triggered/cancelled). Pas touche si rien a GC pour eviter une
    # copie de dict inutile chaque tick.
    update_payload: dict = {
        "scheduled_events": new_scheduled,
        "completed_events": new_completed,
        "cancelled_events": new_cancelled,
        "rumors": new_rumors,
    }
    if terminal_substitute_ids:
        update_payload["substitute_events"] = {
            sid: data
            for sid, data in world.substitute_events.items()
            if sid not in terminal_substitute_ids
        }
    new_world = world.model_copy(update=update_payload)
    return new_world, fired, cancelled


def _date_reached(ev: ScheduledEvent, world: WorldState) -> bool:
    if ev.year > world.current_year:
        return False
    if ev.year < world.current_year:
        return True
    if ev.date is None:
        return True
    return ev.date <= world.current_date
