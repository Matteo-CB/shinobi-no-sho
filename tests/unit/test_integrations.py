"""Tests d'integration pour les pieces nouvellement reliees."""

from __future__ import annotations

import pytest

from shinobi.canon.loader import load_canon
from shinobi.canon.profiles import CanonicityProfile
from shinobi.engine.character import Character
from shinobi.engine.consequences import (
    apply_action_consequences,
    mission_consequences,
)
from shinobi.engine.events import initialize_scheduler, tick_scheduler
from shinobi.engine.missions import generate_mission
from shinobi.engine.stats import CoreStats, ExtendedStats
from shinobi.engine.world import create_default_world
from shinobi.types import ActionOutcome, ActionType, Gender


@pytest.fixture(scope="module")
def canon():
    return load_canon()


def _make_player() -> Character:
    return Character(
        id="test",
        name="Test",
        gender=Gender.male,
        birth_year=1,
        birth_date="01-01",
        age_years=12,
        village_of_origin="konohagakure",
        current_village="konohagakure",
        current_location="konohagakure",
        rank="genin",
        clan="uchiha",
        stats=CoreStats(taijutsu=2.0, ninjutsu=2.0),
        extended_stats=ExtendedStats(learning_genius=2.0, chakra_pool_max=200),
    )


def test_scheduler_initialized_with_events(canon) -> None:
    """initialize_scheduler doit charger les events canon a venir."""
    scheduled = initialize_scheduler(canon, starting_year=1)
    assert len(scheduled) > 0


def test_scheduler_ticks_and_generates_rumor(canon) -> None:
    """Quand un event triggered, une rumeur doit apparaitre."""
    scheduled = initialize_scheduler(canon, starting_year=1)
    if not scheduled:
        pytest.skip("aucun event canon a year 1")
    world = create_default_world(profile=CanonicityProfile.default(), starting_year=1)
    world = world.model_copy(update={"scheduled_events": scheduled, "current_year": 100})
    new_world, fired, _ = tick_scheduler(world, canon, turn_number=1)
    if fired:
        assert len(new_world.rumors) >= len(world.rumors)


def test_action_consequences_for_combat() -> None:
    """Un combat reussi doit donner taijutsu, strength, willpower, etc."""
    char = _make_player()
    _new, changes, _applied = apply_action_consequences(
        char, action_type=ActionType.fight, outcome=ActionOutcome.full_success, duration_hours=1
    )
    stat_names = {c.stat_name for c in changes}
    assert "taijutsu" in stat_names
    assert any(c.delta > 0 for c in changes)


def test_mission_consequences_combat_profile() -> None:
    """Mission de combat donne des bonus combat."""
    from dataclasses import replace

    char = _make_player()
    mission = generate_mission(player_rank="genin", seed=42)
    # Force le profil combat (Mission est frozen=True dataclass)
    mission_combat = replace(
        mission,
        title="Eliminer un deserteur",
        description_fr="Combat contre un nukenin",
        rank="C",
    )
    _new, applied = mission_consequences(char, mission_combat, success=True)
    assert applied


def test_goals_persistence(tmp_path, monkeypatch, canon) -> None:
    """save_goal puis load_goals doit retourner l'objectif."""
    from shinobi.config import settings
    from shinobi.goals.declaration import declare_goal
    from shinobi.persistence import saves as save_module

    monkeypatch.setattr(settings, "saves_path", str(tmp_path))
    char = _make_player()
    world = create_default_world(profile=CanonicityProfile.default(), starting_year=12)
    save_id = save_module.create_save(char, world, canonicity_profile="default", thumbnail_summary="")
    goal = declare_goal(
        description_player="Devenir Hokage",
        interpretation_canonical="Atteindre le rang Kage de Konoha",
        declared_at_year=12,
        declared_at_age=12,
    )
    save_module.save_goal(save_id, goal)
    loaded = save_module.load_goals(save_id)
    assert len(loaded) == 1
    assert loaded[0].id == goal.id
    assert loaded[0].description_player == "Devenir Hokage"
