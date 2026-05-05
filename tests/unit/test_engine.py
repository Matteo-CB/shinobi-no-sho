"""Tests unitaires du moteur deterministe."""

from __future__ import annotations

from shinobi.canon.profiles import CanonicityProfile
from shinobi.engine.actions import Action, ResolutionInputs, resolve_action
from shinobi.engine.character import Character
from shinobi.engine.rng import roll
from shinobi.engine.stats import CoreStats, ExtendedStats, databook_total
from shinobi.engine.world import create_default_world
from shinobi.types import ActionOutcome, ActionType, Gender


def _make_character() -> Character:
    return Character(
        id="test_character",
        name="Test",
        gender=Gender.male,
        birth_year=1,
        birth_date="01-01",
        age_years=12,
        village_of_origin="konohagakure",
        current_village="konohagakure",
        current_location="konohagakure",
        rank="genin",
        stats=CoreStats(ninjutsu=2.0, taijutsu=2.0, intelligence=2.5, speed=2.0),
        extended_stats=ExtendedStats(chakra_pool_max=200, chakra_control=2.0, willpower=2.0),
    )


def test_rng_deterministic() -> None:
    seed = 12345
    r1 = roll(seed, "1d20", modifier=3)
    r2 = roll(seed, "1d20", modifier=3)
    assert r1.total == r2.total
    assert r1.seed_after == r2.seed_after


def test_rng_advances() -> None:
    seed = 12345
    r1 = roll(seed)
    r2 = roll(r1.seed_after)
    assert r1.total != r2.total or r1.seed_after != r2.seed_after


def test_resolve_action_basic() -> None:
    char = _make_character()
    world = create_default_world(profile=CanonicityProfile.default(), starting_year=12)
    action = Action(action_type=ActionType.train_stat, summary="entrainement intensif")
    result = resolve_action(ResolutionInputs(character=char, world=world, action=action, seed=42))
    assert result.outcome != ActionOutcome.contextual_impossibility
    assert result.duration_minutes > 0
    assert result.seed_after != 42


def test_resolve_action_dead_character() -> None:
    char = _make_character().model_copy(update={"is_dead": True})
    world = create_default_world(profile=CanonicityProfile.default(), starting_year=12)
    action = Action(action_type=ActionType.train_stat, summary="impossible")
    result = resolve_action(ResolutionInputs(character=char, world=world, action=action, seed=42))
    assert result.outcome == ActionOutcome.contextual_impossibility


def test_databook_total() -> None:
    stats = CoreStats(
        ninjutsu=4.0,
        taijutsu=3.0,
        genjutsu=2.0,
        intelligence=4.0,
        strength=2.5,
        speed=3.5,
        stamina=4.0,
        hand_seals=3.0,
    )
    assert databook_total(stats) == 26.0
