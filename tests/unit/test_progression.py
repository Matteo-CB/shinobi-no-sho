"""Tests sur la progression des stats et la gestion des ressources."""

from __future__ import annotations

from shinobi.engine.character import Character
from shinobi.engine.progression import (
    apply_chakra_cost,
    apply_meditation,
    apply_rest,
    apply_sleep,
    train_stat,
)
from shinobi.engine.stats import CoreStats, ExtendedStats
from shinobi.types import Gender


def _make() -> Character:
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
        stats=CoreStats(taijutsu=2.0),
        extended_stats=ExtendedStats(learning_genius=2.0, chakra_pool_max=200),
    )


def test_train_stat_progresses() -> None:
    char = _make()
    new, change = train_stat(char, "taijutsu", hours=100)
    assert change is not None
    assert new.stats.taijutsu > 2.0
    # Gain plausible : autour de 0.05 - 0.15 a stat 2.0 sur 100h
    assert 0.01 < change.delta < 0.5


def test_train_stat_diminishing_returns() -> None:
    char = _make().model_copy(update={"stats": CoreStats(taijutsu=4.5)})
    _, change_high = train_stat(char, "taijutsu", hours=100)
    char_low = _make().model_copy(update={"stats": CoreStats(taijutsu=1.0)})
    _, change_low = train_stat(char_low, "taijutsu", hours=100)
    assert change_high is not None and change_low is not None
    assert change_low.delta > change_high.delta * 3


def test_train_stat_caps_at_5() -> None:
    char = _make().model_copy(update={"stats": CoreStats(taijutsu=5.0)})
    new, change = train_stat(char, "taijutsu", hours=10000)
    assert change is None or new.stats.taijutsu <= 5.0


def test_train_non_trainable_returns_none() -> None:
    """lineage_value et chakra_reserves sont strictement non entrainables."""
    char = _make()
    new, change = train_stat(char, "lineage_value", hours=1000)
    assert change is None
    assert new.extended_stats.lineage_value == char.extended_stats.lineage_value


def test_train_intangible_progresses_slowly() -> None:
    """Beauty et luck sont entrainables mais ~5x plus lentement."""
    char = _make()
    _, fast = train_stat(char, "stamina", hours=200)
    _, slow = train_stat(char, "beauty", hours=200)
    assert fast is not None and slow is not None
    assert fast.delta > slow.delta * 3


def test_rest_recovers_chakra() -> None:
    char = _make().model_copy(
        update={"chakra": _make().chakra.model_copy(update={"current": 50, "max": 200})}
    )
    new = apply_rest(char, hours=4)
    assert new.chakra.current > char.chakra.current


def test_sleep_more_efficient_than_rest() -> None:
    char = _make().model_copy(
        update={
            "chakra": _make().chakra.model_copy(update={"current": 0, "max": 200}),
            "health": _make().health.model_copy(update={"fatigue": 80}),
        }
    )
    rested = apply_rest(char, hours=8)
    slept = apply_sleep(char, hours=8)
    assert slept.health.fatigue <= rested.health.fatigue
    assert slept.chakra.current >= rested.chakra.current


def test_meditation_recovers_chakra_efficiently() -> None:
    char = _make().model_copy(
        update={"chakra": _make().chakra.model_copy(update={"current": 0, "max": 200})}
    )
    new = apply_meditation(char, hours=2)
    assert new.chakra.current >= 50  # 25 chakra/h


def test_chakra_cost_clamps_at_zero() -> None:
    char = _make().model_copy(
        update={"chakra": _make().chakra.model_copy(update={"current": 10, "max": 200})}
    )
    new = apply_chakra_cost(char, 50)
    assert new.chakra.current == 0
