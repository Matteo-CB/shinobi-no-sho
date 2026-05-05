"""Tests pour cout de vie, voyage, missions modulees par tension/inflation, poison."""

from __future__ import annotations

from shinobi.engine.character import (
    Character,
    HealthState,
    Poison,
)
from shinobi.engine.economy import (
    apply_inflation,
    cost_of_living_for_period,
    daily_living_cost,
)
from shinobi.engine.interpreter import interpret
from shinobi.engine.locations import travel_days, travel_minutes
from shinobi.engine.missions import generate_mission, list_available_missions
from shinobi.engine.stats import CoreStats, ExtendedStats
from shinobi.types import ActionType, Gender


def _make_char(money: int = 1000) -> Character:
    return Character(
        id="t",
        name="Test",
        gender=Gender.male,
        birth_year=1,
        birth_date="01-01",
        age_years=12,
        village_of_origin="konohagakure",
        current_village="konohagakure",
        current_location="konohagakure",
        rank="genin",
        money=money,
        stats=CoreStats(taijutsu=2.0),
        extended_stats=ExtendedStats(learning_genius=2.0, chakra_pool_max=200),
    )


# Economy ---------------------------------------------------------------------


def test_daily_living_cost_is_reasonable() -> None:
    cost = daily_living_cost()
    assert 100 <= cost <= 200


def test_apply_inflation_scales_price() -> None:
    assert apply_inflation(100, 1.0) == 100
    assert apply_inflation(100, 1.5) == 150
    assert apply_inflation(100, 0.5) == 50
    # zero ou negatif retourne le prix nu
    assert apply_inflation(100, 0) == 100
    assert apply_inflation(100, -1.0) == 100


def test_cost_of_living_period() -> None:
    daily = daily_living_cost()
    assert cost_of_living_for_period(days=0) == 0
    assert cost_of_living_for_period(days=1, inflation_factor=1.0) == daily
    assert cost_of_living_for_period(days=7, inflation_factor=1.0) == daily * 7
    assert cost_of_living_for_period(days=10, inflation_factor=2.0) == daily * 10 * 2


# Travel ----------------------------------------------------------------------


def test_travel_same_village_is_zero() -> None:
    assert travel_days("konohagakure", "konohagakure") == 0
    assert travel_minutes("konohagakure", "konohagakure") == 0


def test_travel_known_pair_is_symmetric() -> None:
    assert travel_days("konohagakure", "sunagakure") == travel_days("sunagakure", "konohagakure")


def test_travel_minutes_matches_days_x_24_60() -> None:
    days = travel_days("konohagakure", "iwagakure")
    assert travel_minutes("konohagakure", "iwagakure") == days * 24 * 60


def test_travel_unknown_uses_default() -> None:
    # Defaut = 5 jours pour paire inconnue
    assert travel_days("konohagakure", "place_inconnue") == 5


# Missions modulees -----------------------------------------------------------


def test_generate_mission_default_no_tension() -> None:
    mission = generate_mission(player_rank="genin", seed=1, global_tension=0)
    # Genin -> rank D -> DC base 8
    assert mission.difficulty_dc == 8


def test_generate_mission_increases_dc_with_tension() -> None:
    base = generate_mission(player_rank="genin", seed=1, global_tension=0)
    high_tension = generate_mission(player_rank="genin", seed=1, global_tension=50)
    assert high_tension.difficulty_dc > base.difficulty_dc
    # Tension 50 -> bonus 5 sur le DC
    assert high_tension.difficulty_dc == base.difficulty_dc + 5


def test_generate_mission_inflation_scales_reward() -> None:
    base = generate_mission(player_rank="genin", seed=1, inflation_factor=1.0)
    inflated = generate_mission(player_rank="genin", seed=1, inflation_factor=2.0)
    assert inflated.reward_ryos > base.reward_ryos


def test_list_available_missions_returns_count() -> None:
    missions = list_available_missions(player_rank="genin", count=5)
    assert len(missions) == 5


# Interpreter : voyage et desertion ------------------------------------------


def test_interpret_move_extracts_destination() -> None:
    parsed = interpret("je voyage vers sunagakure")
    assert parsed.action_type == ActionType.move
    assert parsed.parameters.get("target_location") == "sunagakure"


def test_interpret_move_short_form() -> None:
    parsed = interpret("je vais a iwa")
    assert parsed.action_type == ActionType.move
    assert parsed.parameters.get("target_location") == "iwagakure"


def test_interpret_desert_sets_flag() -> None:
    parsed = interpret("je deserte mon village")
    assert parsed.parameters.get("_desert") is True


def test_interpret_brise_bandeau() -> None:
    parsed = interpret("je brise mon bandeau et je pars")
    assert parsed.parameters.get("_desert") is True


# Poison structure -----------------------------------------------------------


def test_poison_can_be_applied_to_health() -> None:
    char = _make_char()
    poison = Poison(name="toxine", severity="severe", rounds_remaining=3)
    new_health = char.health.model_copy(
        update={"poison_status": [*char.health.poison_status, poison]}
    )
    poisoned = char.with_health(new_health)
    assert len(poisoned.health.poison_status) == 1
    assert poisoned.health.poison_status[0].severity == "severe"


def test_poison_is_cleared_by_antidote_simulation() -> None:
    """Simule l'effet de antidote (items.py) : vide la poison_status liste."""
    poison = Poison(name="toxine", severity="severe", rounds_remaining=3)
    health = HealthState(poison_status=[poison])
    cleared = health.model_copy(update={"poison_status": []})
    assert cleared.poison_status == []
