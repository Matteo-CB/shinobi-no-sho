"""Tests pour goal completion auto-check, decay des relations, aging continu."""

from __future__ import annotations

from shinobi.engine.character import (
    Character,
    Relationship,
    RelationshipEvent,
)
from shinobi.engine.progression import advance_age
from shinobi.engine.relations import (
    DECAY_DAYS_THRESHOLD,
    decay_affinities,
    touch_relationship,
)
from shinobi.engine.stats import CoreStats, ExtendedStats
from shinobi.goals.breadcrumbs import (
    Breadcrumb,
    CompletionCondition,
)
from shinobi.goals.completion import check_goal_by_target, check_goal_completion
from shinobi.goals.declaration import GoalTargetType, declare_goal
from shinobi.types import Gender


def _make_char(age: int = 12, money: int = 100) -> Character:
    return Character(
        id="t",
        name="Test",
        gender=Gender.male,
        birth_year=1,
        birth_date="01-01",
        age_years=age,
        village_of_origin="konohagakure",
        current_village="konohagakure",
        current_location="konohagakure",
        rank="genin",
        money=money,
        stats=CoreStats(taijutsu=2.0),
        extended_stats=ExtendedStats(learning_genius=2.0, chakra_pool_max=200),
    )


# Goal completion --------------------------------------------------------------


def test_goal_with_no_breadcrumbs_is_not_completed() -> None:
    goal = declare_goal(
        description_player="devenir Hokage",
        interpretation_canonical="atteindre le rang Hokage",
        declared_at_year=10,
        declared_at_age=12,
        target_type=GoalTargetType.achieve_rank,
        target_id="hokage",
    )
    assert check_goal_completion(goal, []) is False


def test_goal_completed_when_all_required_breadcrumbs_done() -> None:
    goal = declare_goal(
        description_player="objectif test",
        interpretation_canonical="test",
        declared_at_year=10,
        declared_at_age=12,
    )
    bc1 = Breadcrumb(
        id="b1",
        parent_goal_id=goal.id,
        sequence_index=1,
        description="step 1",
        canonical_basis="test",
        completion_conditions=[],
        revealed=True,
        completed=True,
    )
    bc2 = Breadcrumb(
        id="b2",
        parent_goal_id=goal.id,
        sequence_index=2,
        description="step 2",
        canonical_basis="test",
        completion_conditions=[],
        revealed=True,
        completed=True,
    )
    assert check_goal_completion(goal, [bc1, bc2]) is True


def test_goal_not_completed_when_one_breadcrumb_pending() -> None:
    goal = declare_goal(
        description_player="objectif test",
        interpretation_canonical="test",
        declared_at_year=10,
        declared_at_age=12,
    )
    bc1 = Breadcrumb(
        id="b1",
        parent_goal_id=goal.id,
        sequence_index=1,
        description="step 1",
        canonical_basis="test",
        completion_conditions=[CompletionCondition(type="visit_location")],
        revealed=True,
        completed=True,
    )
    bc2 = Breadcrumb(
        id="b2",
        parent_goal_id=goal.id,
        sequence_index=2,
        description="step 2",
        canonical_basis="test",
        completion_conditions=[CompletionCondition(type="visit_location")],
        revealed=True,
        completed=False,
    )
    assert check_goal_completion(goal, [bc1, bc2]) is False


def test_goal_by_target_achieve_rank() -> None:
    char = _make_char()
    char = char.model_copy(update={"rank": "hokage"})
    goal = declare_goal(
        description_player="devenir Hokage",
        interpretation_canonical="rang hokage",
        declared_at_year=10,
        declared_at_age=12,
        target_type=GoalTargetType.achieve_rank,
        target_id="hokage",
    )
    assert check_goal_by_target(goal, char) is True


def test_goal_by_target_befriend_requires_high_affinity() -> None:
    char = _make_char()
    char = char.model_copy(
        update={
            "relationships": [
                Relationship(with_character_id="naruto", type="ally", affinity=80)
            ]
        }
    )
    goal = declare_goal(
        description_player="devenir ami avec Naruto",
        interpretation_canonical="befriend",
        declared_at_year=10,
        declared_at_age=12,
        target_type=GoalTargetType.befriend_character,
        target_id="naruto",
    )
    assert check_goal_by_target(goal, char) is True


# Affinity decay ---------------------------------------------------------------


def test_decay_affinity_after_long_silence() -> None:
    char = _make_char()
    rel = Relationship(
        with_character_id="kiba",
        type="friend",
        affinity=20,
        history=[RelationshipEvent(year=10, description="rencontre", affinity_delta=20)],
    )
    char = char.model_copy(update={"relationships": [rel]})
    # 1 an de silence = 365 jours = 4 periodes de 90 jours = -4
    decayed = decay_affinities(char, current_year=11)
    assert decayed.relationships[0].affinity == 16


def test_decay_does_not_apply_to_recent_relationships() -> None:
    char = _make_char()
    rel = Relationship(
        with_character_id="kiba",
        type="friend",
        affinity=20,
        history=[RelationshipEvent(year=10, description="vu", affinity_delta=0)],
    )
    char = char.model_copy(update={"relationships": [rel]})
    # Meme annee : pas de decay
    decayed = decay_affinities(char, current_year=10)
    assert decayed.relationships[0].affinity == 20


def test_touch_relationship_resets_decay_clock() -> None:
    char = _make_char()
    rel = Relationship(
        with_character_id="kiba",
        type="friend",
        affinity=20,
        history=[RelationshipEvent(year=5, description="ancienne", affinity_delta=0)],
    )
    char = char.model_copy(update={"relationships": [rel]})
    # On le voit en l'an 11
    char = touch_relationship(char, with_id="kiba", year=11)
    # Maintenant le decay doit ignorer la vieille interaction
    decayed = decay_affinities(char, current_year=11)
    assert decayed.relationships[0].affinity == 20


def test_decay_threshold_constant_is_reasonable() -> None:
    # Garde-fou : si quelqu'un baisse drastiquement le seuil, le test casse.
    assert 30 <= DECAY_DAYS_THRESHOLD <= 365


# Aging continu ----------------------------------------------------------------


def test_advance_age_applies_growth_before_18() -> None:
    char = _make_char(age=10)
    aged = advance_age(char, 14)
    assert aged.age_years == 14
    # Stat de base 2.0 → growth doit l'avoir poussee vers la cible 3.0
    assert aged.stats.taijutsu >= char.stats.taijutsu


def test_advance_age_applies_decay_after_30() -> None:
    char = _make_char(age=29)
    char = char.model_copy(update={"stats": CoreStats(speed=4.0, taijutsu=4.0, strength=4.0, stamina=4.0)})
    aged = advance_age(char, 50)
    assert aged.age_years == 50
    # Decay doit avoir reduit les stats physiques
    assert aged.stats.speed < 4.0
    assert aged.stats.strength < 4.0


def test_advance_age_noop_if_same_or_lower() -> None:
    char = _make_char(age=20)
    same = advance_age(char, 20)
    assert same is char
    same2 = advance_age(char, 15)
    assert same2 is char
