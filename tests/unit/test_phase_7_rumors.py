"""Phase 7.1 : tests propagation rumors.

Couvre :
- make_rumor_from_event : creation depuis TimelineEvent + radius/fidelite
- propagate_rumors : insertion dans WorldState
- player_can_hear : matrice radius x player_location x event_location +
  expiration year
- receive_rumor : marquage joueur
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from shinobi.canon.profiles import CanonicityProfile
from shinobi.engine.rumors import (
    _RADIUS_FIDELITY,
    make_rumor_from_event,
    player_can_hear,
    propagate_rumors,
    receive_rumor,
)
from shinobi.engine.world import Rumor, create_default_world


def _fake_event(eid="ev_test", summary="Un evenement secret a Konoha"):
    e = MagicMock()
    e.id = eid
    e.narrative_summary_fr = summary
    return e


def test_make_rumor_default_radius_regional() -> None:
    """make_rumor utilise radius regional par defaut."""
    ev = _fake_event()
    r = make_rumor_from_event(ev, born_at_year=10)
    assert r.diffusion_radius == "regional"
    assert r.fidelity == _RADIUS_FIDELITY["regional"]
    assert r.born_at_year == 10
    assert r.expires_at_year == 15  # +5 ans


def test_make_rumor_radius_proximity_higher_fidelity() -> None:
    """proximity rumor a fidelite > regional > international > secret."""
    ev = _fake_event()
    prox = make_rumor_from_event(ev, born_at_year=10, radius="proximity")
    reg = make_rumor_from_event(ev, born_at_year=10, radius="regional")
    intl = make_rumor_from_event(ev, born_at_year=10, radius="international")
    sec = make_rumor_from_event(ev, born_at_year=10, radius="secret")
    assert prox.fidelity > reg.fidelity > intl.fidelity > sec.fidelity


def test_make_rumor_fidelity_override() -> None:
    """fidelity_override remplace la valeur par defaut."""
    ev = _fake_event()
    r = make_rumor_from_event(
        ev, born_at_year=10, radius="proximity", fidelity_override=0.42,
    )
    assert r.fidelity == 0.42


def test_make_rumor_links_event_id() -> None:
    """source_event_id est preserve depuis TimelineEvent."""
    ev = _fake_event(eid="uchiha_clan_massacre")
    r = make_rumor_from_event(ev, born_at_year=9)
    assert r.source_event_id == "uchiha_clan_massacre"


def test_propagate_rumors_appends_to_world() -> None:
    """propagate_rumors ajoute aux rumors existantes du world."""
    world = create_default_world(
        profile=CanonicityProfile.default(), starting_year=10,
    )
    n_before = len(world.rumors)
    new_rumors = [
        make_rumor_from_event(_fake_event("e1"), born_at_year=10),
        make_rumor_from_event(_fake_event("e2"), born_at_year=10),
    ]
    new_world = propagate_rumors(world, new_rumors)
    assert len(new_world.rumors) == n_before + 2


def test_propagate_rumors_empty_returns_world_unchanged() -> None:
    """Si liste vide, world inchange."""
    world = create_default_world(
        profile=CanonicityProfile.default(), starting_year=10,
    )
    new_world = propagate_rumors(world, [])
    assert new_world is world or len(new_world.rumors) == len(world.rumors)


def test_player_can_hear_proximity_same_location() -> None:
    """proximity rumor : entendue si joueur sur meme location."""
    ev = _fake_event()
    r = make_rumor_from_event(ev, born_at_year=10, radius="proximity")
    assert player_can_hear(
        r, player_location="konoha_market", event_location="konoha_market",
        current_year=11,
    )


def test_player_can_hear_proximity_different_location() -> None:
    """proximity rumor : NON entendue si joueur ailleurs."""
    ev = _fake_event()
    r = make_rumor_from_event(ev, born_at_year=10, radius="proximity")
    assert not player_can_hear(
        r, player_location="suna_market", event_location="konoha_market",
        current_year=11,
    )


def test_player_can_hear_regional_anywhere() -> None:
    """regional rumor : entendue partout (default)."""
    ev = _fake_event()
    r = make_rumor_from_event(ev, born_at_year=10, radius="regional")
    assert player_can_hear(
        r, player_location="suna_market", event_location="konoha_market",
        current_year=11,
    )


def test_player_can_hear_secret_never_default() -> None:
    """secret rumor : jamais entendue par le default check."""
    ev = _fake_event()
    r = make_rumor_from_event(ev, born_at_year=10, radius="secret")
    assert not player_can_hear(
        r, player_location="konoha_market", event_location="konoha_market",
        current_year=11,
    )


def test_player_can_hear_expired_returns_false() -> None:
    """Rumor expiree (current_year > expires_at_year) : pas entendue."""
    ev = _fake_event()
    r = make_rumor_from_event(ev, born_at_year=10, radius="regional")
    # expires_at_year = 15, current_year = 20 -> expiree
    assert not player_can_hear(
        r, player_location="konoha", event_location="konoha", current_year=20,
    )


def test_player_can_hear_just_before_expiration() -> None:
    """Rumor a year=expires_at_year est encore entendue (cap inclusif)."""
    ev = _fake_event()
    r = make_rumor_from_event(ev, born_at_year=10, radius="regional")
    # expires_at_year = 15, current_year = 15 -> juste a la limite
    assert player_can_hear(
        r, player_location="konoha", event_location="konoha", current_year=15,
    )


def test_receive_rumor_marks_received() -> None:
    """receive_rumor passe received_by_player a True."""
    ev = _fake_event()
    r = make_rumor_from_event(ev, born_at_year=10, radius="regional")
    world = create_default_world(
        profile=CanonicityProfile.default(), starting_year=10,
    )
    world = world.model_copy(update={"rumors": [r]})
    new_world = receive_rumor(world, r.id, year=11)
    assert new_world.rumors[0].received_by_player is True


def test_receive_rumor_unknown_id_no_op() -> None:
    """receive_rumor sur rumor_id inconnu : world inchange."""
    ev = _fake_event()
    r = make_rumor_from_event(ev, born_at_year=10)
    world = create_default_world(
        profile=CanonicityProfile.default(), starting_year=10,
    )
    world = world.model_copy(update={"rumors": [r]})
    new_world = receive_rumor(world, "unknown_id", year=11)
    assert new_world.rumors[0].received_by_player is False


def test_radius_fidelity_table_canonical_order() -> None:
    """_RADIUS_FIDELITY suit ordre fidelite : prox > reg > intl > secret."""
    assert (
        _RADIUS_FIDELITY["proximity"]
        > _RADIUS_FIDELITY["regional"]
        > _RADIUS_FIDELITY["international"]
        > _RADIUS_FIDELITY["secret"]
    )
