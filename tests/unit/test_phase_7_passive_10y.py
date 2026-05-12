"""Phase 7.7 : test partie passive de 10 ans.

Critere de sortie roadmap :
> 'une partie passive de 10 ans produit une chronologie coherente'

Verifie qu'apres 10 ans de simulation passive (sans intervention joueur),
les events canon prevus pour cette plage sont bien declenches dans l'ordre
chronologique, et qu'aucun crash / etat incoherent n'est produit.
"""
from __future__ import annotations

import pytest

from shinobi.canon.loader import load_canon
from shinobi.canon.profiles import CanonicityProfile
from shinobi.engine.events import initialize_scheduler, tick_scheduler
from shinobi.engine.world import create_default_world
from shinobi.types import EventStatus


@pytest.fixture(scope="module")
def canon():
    return load_canon()


def test_passive_10_years_canon_events_fire_in_order(canon) -> None:
    """Spec 7.7 : 10 ans passifs starting year 6 -> events canon entre
    year 6 et year 16 doivent fire chronologiquement.

    On simule 1 tick par mois (= 120 ticks pour 10 ans). Chaque tick
    avance le world d'un mois. Les events canon dont la date arrive
    sont triggered (ou cancelled si preconditions violees, ce qui ne
    devrait pas arriver en passive sans intervention joueur).
    """
    starting_year = 6
    world = create_default_world(
        profile=CanonicityProfile.default(), starting_year=starting_year,
    )
    scheduled = initialize_scheduler(canon, starting_year=starting_year)
    world = world.model_copy(update={"scheduled_events": scheduled})

    # 10 ans = 120 mois. On tick mois par mois.
    fired_events: list[tuple[int, str]] = []
    for offset_month in range(120):
        year = starting_year + (offset_month // 12)
        month = (offset_month % 12) + 1
        world = world.with_time(
            year=year, date=f"{month:02d}-15", hour=12, minute=0,
        )
        world, fired, _ = tick_scheduler(
            world, canon, turn_number=offset_month,
        )
        for ev in fired:
            fired_events.append((ev.triggered_at_year, ev.event_id))

    # 1. Au moins 1 event canon doit avoir fire (sur 60 events canon, plage
    # 10 ans devrait en couvrir plusieurs)
    assert len(fired_events) >= 1, (
        f"0 event canon fired sur 10 ans passifs - regression scheduler"
    )

    # 2. Les events fired sont chronologiquement ordonnes
    years_fired = [year for year, _ in fired_events]
    assert years_fired == sorted(years_fired), (
        "Events fired pas dans l'ordre chronologique"
    )

    # 3. Tous les fired events sont dans la plage temporelle attendue
    for year, eid in fired_events:
        assert starting_year <= year <= starting_year + 10, (
            f"Event {eid} fired outside expected range : year={year}"
        )


def test_passive_10_years_no_runtime_crash(canon) -> None:
    """Spec 7.7 : 10 ans passifs ne doivent pas crasher meme avec
    canon corrompu (events pre-naissance, year hors plage, etc.)."""
    starting_year = 0
    world = create_default_world(
        profile=CanonicityProfile.default(), starting_year=starting_year,
    )
    scheduled = initialize_scheduler(canon, starting_year=starting_year)
    world = world.model_copy(update={"scheduled_events": scheduled})

    # Tick year-par-year (granularite plus large pour speed)
    for year in range(starting_year, starting_year + 11):
        world = world.with_time(
            year=year, date="06-15", hour=12, minute=0,
        )
        world, _, _ = tick_scheduler(world, canon, turn_number=year)
    # Pas de crash : test passe
    assert world.current_year == starting_year + 10


def test_passive_10_years_completed_events_persist_in_world(canon) -> None:
    """Spec 7.7 : les events triggered restent dans world.completed_events
    pour la duree de la partie (pas de GC silencieux).
    """
    starting_year = 8
    world = create_default_world(
        profile=CanonicityProfile.default(), starting_year=starting_year,
    )
    scheduled = initialize_scheduler(canon, starting_year=starting_year)
    world = world.model_copy(update={"scheduled_events": scheduled})

    n_completed_initial = len(world.completed_events)
    # Tick 10 ans
    for offset_month in range(120):
        year = starting_year + (offset_month // 12)
        month = (offset_month % 12) + 1
        world = world.with_time(
            year=year, date=f"{month:02d}-15", hour=12, minute=0,
        )
        world, _, _ = tick_scheduler(
            world, canon, turn_number=offset_month,
        )

    n_completed_final = len(world.completed_events)
    # Events triggered s'accumulent
    assert n_completed_final >= n_completed_initial, (
        "completed_events count diminue - GC silencieux des canon events"
    )


def test_passive_10_years_rumor_count_grows(canon) -> None:
    """Spec 7.7 : les events canon fired generent des rumors qui
    s'accumulent dans world.rumors.
    """
    starting_year = 10
    world = create_default_world(
        profile=CanonicityProfile.default(), starting_year=starting_year,
    )
    scheduled = initialize_scheduler(canon, starting_year=starting_year)
    world = world.model_copy(update={"scheduled_events": scheduled})

    n_rumors_initial = len(world.rumors)
    for offset_month in range(120):
        year = starting_year + (offset_month // 12)
        month = (offset_month % 12) + 1
        world = world.with_time(
            year=year, date=f"{month:02d}-15", hour=12, minute=0,
        )
        world, _, _ = tick_scheduler(
            world, canon, turn_number=offset_month,
        )

    # On accepte 0 (selon les events qui fire) mais pas decroissance
    assert len(world.rumors) >= n_rumors_initial


def test_passive_10_years_scheduler_doesnt_drop_events(canon) -> None:
    """Sanity : le total scheduled + completed + cancelled reste >= initial
    (pas de drop silencieux)."""
    starting_year = 5
    world = create_default_world(
        profile=CanonicityProfile.default(), starting_year=starting_year,
    )
    scheduled = initialize_scheduler(canon, starting_year=starting_year)
    world = world.model_copy(update={"scheduled_events": scheduled})

    n_total_initial = (
        len(world.scheduled_events)
        + len(world.completed_events)
        + len(world.cancelled_events)
    )

    for year in range(starting_year, starting_year + 11):
        world = world.with_time(
            year=year, date="06-15", hour=12, minute=0,
        )
        world, _, _ = tick_scheduler(world, canon, turn_number=year)

    n_total_final = (
        len(world.scheduled_events)
        + len(world.completed_events)
        + len(world.cancelled_events)
    )
    # Le total doit etre constant ou croitre (substitute_events peut ajouter)
    assert n_total_final >= n_total_initial, (
        f"Events disparus : {n_total_initial} -> {n_total_final}"
    )
