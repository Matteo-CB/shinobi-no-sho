"""Test d'integration end-to-end : creation perso, plusieurs tours, save/load, continuation.

Couvre les boucles critiques sans LLM (le narrator est skip).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from shinobi.canon.profiles import CanonicityProfile
from shinobi.engine.actions import (
    Action,
    ResolutionInputs,
    apply_action_to_state,
    resolve_action,
)
from shinobi.engine.character import Character
from shinobi.engine.economy import cost_of_living_for_period
from shinobi.engine.events import tick_scheduler
from shinobi.engine.interpreter import interpret
from shinobi.engine.locations import travel_minutes
from shinobi.engine.progression import advance_age
from shinobi.engine.relations import decay_affinities
from shinobi.engine.shop import buy_item, sell_item
from shinobi.engine.stats import CoreStats, ExtendedStats
from shinobi.engine.world import WorldState, create_default_world
from shinobi.goals.completion import check_goal_by_target
from shinobi.goals.declaration import GoalTargetType, complete_goal, declare_goal
from shinobi.persistence import saves as save_module
from shinobi.types import ActionType, Gender


@pytest.fixture()
def isolated_saves_dir(tmp_path: Path, monkeypatch):
    from shinobi.config import settings

    monkeypatch.setattr(settings, "saves_path", str(tmp_path))
    monkeypatch.setattr(type(settings), "saves_dir", property(lambda self: tmp_path))
    return tmp_path


def _make_character() -> Character:
    return Character(
        id="e2e_hero",
        name="E2E Hero",
        gender=Gender.male,
        birth_year=5,
        birth_date="01-01",
        age_years=12,
        village_of_origin="konohagakure",
        current_village="konohagakure",
        current_location="konohagakure",
        rank="genin",
        money=10000,
        stats=CoreStats(taijutsu=2.0, ninjutsu=2.0, intelligence=2.0, stamina=2.0),
        extended_stats=ExtendedStats(learning_genius=2.5, chakra_pool_max=200, chakra_control=2.0),
    )


def _make_world() -> WorldState:
    return create_default_world(profile=CanonicityProfile.default(), starting_year=17)


# Test principal : flux complet ----------------------------------------------


def test_full_session_creation_play_save_reload(isolated_saves_dir) -> None:
    """Cree perso, joue 3 tours d'actions varies, save, reload, verifie etat preserve."""
    character = _make_character()
    world = _make_world()

    # Persistance initiale
    save_id = save_module.create_save(character, world)
    assert save_id

    # Tour 1 : entrainement
    parsed = interpret("je m'entraine au taijutsu pendant 4 heures")
    assert parsed.action_type == ActionType.train_stat
    action = Action(
        action_type=parsed.action_type,
        summary=parsed.summary,
        parameters=parsed.parameters,
    )
    result = resolve_action(
        ResolutionInputs(character=character, world=world, action=action, seed=42)
    )
    character, world, result = apply_action_to_state(character, world, result)
    assert character.stats.taijutsu >= 2.0
    save_module.save_turn(
        save_id,
        turn_number=1,
        action_result=result,
        new_character=character,
        new_world=world,
        seed_state=result.seed_after,
    )

    # Tour 2 : achat d'arme
    from shinobi.engine.shop import ITEM_CATALOG

    kunai = ITEM_CATALOG["kunai"]
    character, msg = buy_item(character, kunai, kunai.base_price_ryos)
    assert "Achete" in msg
    assert any(w.weapon_id == "kunai" for w in character.weapons)

    # Tour 3 : declare goal + verifie completion
    goal = declare_goal(
        description_player="atteindre rang chunin",
        interpretation_canonical="rank chunin",
        declared_at_year=world.current_year,
        declared_at_age=character.age_years,
        target_type=GoalTargetType.achieve_rank,
        target_id="chunin",
    )
    save_module.save_goal(save_id, goal)
    # Le perso n'est pas encore chunin, le goal n'est pas accompli
    assert not check_goal_by_target(goal, character)
    # Promotion manuelle, le goal devrait alors matcher
    promoted = character.model_copy(update={"rank": "chunin"})
    assert check_goal_by_target(goal, promoted)
    closed = complete_goal(goal, world.current_year)
    save_module.save_goal(save_id, closed)

    # Save + reload
    save_module.save_turn(
        save_id,
        turn_number=3,
        action_result=result,
        new_character=character,
        new_world=world,
        seed_state=result.seed_after,
    )
    loaded_char, _loaded_world, _meta = save_module.load_save(save_id)
    assert loaded_char.name == character.name
    assert loaded_char.money == character.money
    assert any(w.weapon_id == "kunai" for w in loaded_char.weapons)
    loaded_goals = save_module.load_goals(save_id)
    assert any(g.id == goal.id and g.status.value == "completed" for g in loaded_goals)


# Tests des sous-systemes interconnectes ------------------------------------


def test_aging_progresses_stats_and_persists(isolated_saves_dir) -> None:
    """Verifie qu'advance_age + persistance preservent age_years."""
    character = _make_character()
    world = _make_world()
    aged = advance_age(character, character.age_years + 5)
    assert aged.age_years == character.age_years + 5
    save_id = save_module.create_save(aged, world)
    loaded, _, _ = save_module.load_save(save_id)
    assert loaded.age_years == aged.age_years


def test_living_cost_consumed_money() -> None:
    character = _make_character()
    cost = cost_of_living_for_period(days=10, inflation_factor=1.0)
    assert cost > 0
    assert character.money > cost  # 10000 ryos couvre 10 jours largement
    poor = character.model_copy(update={"money": cost - 1})
    # Deduction simule
    after = poor.with_money(-(cost - 1))
    assert after.money == 0


def test_travel_minutes_match_known_distance() -> None:
    minutes = travel_minutes("konohagakure", "sunagakure")
    days = minutes // (24 * 60)
    assert 4 <= days <= 6  # 5 jours canonique


def test_decay_then_save_preserves_relation(isolated_saves_dir) -> None:
    from shinobi.engine.character import Relationship, RelationshipEvent

    character = _make_character()
    rel = Relationship(
        with_character_id="naruto",
        type="friend",
        affinity=30,
        history=[RelationshipEvent(year=10, description="vu", affinity_delta=0)],
    )
    character = character.model_copy(update={"relationships": [rel]})
    world = _make_world()
    decayed = decay_affinities(character, current_year=world.current_year)
    save_id = save_module.create_save(decayed, world)
    loaded, _, _ = save_module.load_save(save_id)
    assert loaded.relationships
    assert loaded.relationships[0].with_character_id == "naruto"


def test_event_scheduler_round_trip(isolated_saves_dir) -> None:
    """Les events programmes survivent au save/reload + tick fonctionne."""
    from shinobi.canon.loader import load_canon
    from shinobi.engine.events import initialize_scheduler

    character = _make_character()
    world = _make_world()
    canon = load_canon(optional=("characters", "timeline_events"))
    scheduled = initialize_scheduler(canon, starting_year=world.current_year)
    world = world.model_copy(update={"scheduled_events": scheduled})

    save_id = save_module.create_save(character, world)
    _, loaded_world, _ = save_module.load_save(save_id)
    assert len(loaded_world.scheduled_events) == len(scheduled)

    # Tick : verifie qu'aucune exception n'est levee
    _new_world, fired, cancelled = tick_scheduler(loaded_world, canon, turn_number=1)
    assert isinstance(fired, list)
    assert isinstance(cancelled, list)


def test_shop_sell_back_a_weapon() -> None:
    """L'achat puis la revente d'une arme : flux complet."""
    from shinobi.engine.shop import ITEM_CATALOG

    character = _make_character()
    item = ITEM_CATALOG["fuma_shuriken"]
    character, msg = buy_item(character, item, item.base_price_ryos)
    assert "Achete" in msg
    assert any(w.weapon_id == "fuma_shuriken" for w in character.weapons)
    money_before_sell = character.money
    character, msg2 = sell_item(character, "fuma_shuriken")
    assert "Vendu" in msg2
    assert character.money > money_before_sell
    assert not any(w.weapon_id == "fuma_shuriken" for w in character.weapons)
