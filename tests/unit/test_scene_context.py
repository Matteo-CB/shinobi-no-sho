"""Tests sur le calculateur de contexte de scene."""

from __future__ import annotations

import pytest

from shinobi.canon.loader import load_canon
from shinobi.canon.profiles import CanonicityProfile
from shinobi.engine.character import Character
from shinobi.engine.scene_context import (
    age_at,
    compute_scene_context,
    filter_proposed_actions,
)
from shinobi.engine.stats import CoreStats, ExtendedStats
from shinobi.engine.world import create_default_world
from shinobi.types import Gender


@pytest.fixture(scope="module")
def canon():
    return load_canon(
        optional=(
            "organizations",
            "tailed_beasts",
            "kekkei_mora",
            "hiden",
            "timeline_events",
            "voice_profiles",
        )
    )


def _make(*, age: int, rank: str = "academy_student") -> Character:
    return Character(
        id="test_player",
        name="Test",
        gender=Gender.male,
        birth_year=1,
        birth_date="01-01",
        age_years=age,
        village_of_origin="konohagakure",
        current_village="konohagakure",
        current_location="konohagakure",
        rank=rank,
        clan="uchiha",
        stats=CoreStats(),
        extended_stats=ExtendedStats(),
    )


def test_baby_cannot_leave_village(canon) -> None:
    char = _make(age=1)
    world = create_default_world(profile=CanonicityProfile.default(), starting_year=1)
    ctx = compute_scene_context(char, world, canon)
    assert not ctx.player_can_leave_village
    assert not ctx.player_combat_capable
    assert any("trop jeune" in c.lower() for c in ctx.constraints_fr)


def test_genin_can_leave_village(canon) -> None:
    char = _make(age=12, rank="genin")
    world = create_default_world(profile=CanonicityProfile.default(), starting_year=12)
    ctx = compute_scene_context(char, world, canon)
    assert ctx.player_can_leave_village
    assert ctx.player_combat_capable


def test_kabuto_not_accessible_to_baby_in_konoha_year_1(canon) -> None:
    """Le bug du joueur : an 1, joueur de 1 an a Konoha, Kabuto ne devrait pas etre propose."""
    char = _make(age=1)
    world = create_default_world(profile=CanonicityProfile.default(), starting_year=1)
    ctx = compute_scene_context(char, world, canon)
    accessible_ids = ctx.npc_ids()
    # Kabuto Yakushi ne doit PAS etre dans les PNJ accessibles a un bebe de 1 an a Konoha
    assert "yakushi_kabuto" not in accessible_ids
    assert "kabuto_yakushi" not in accessible_ids


def test_age_at_helper(canon) -> None:
    if "uchiha_itachi" in canon.characters:
        itachi = canon.characters["uchiha_itachi"]
        if itachi.birth_year is not None:
            assert age_at(itachi, itachi.birth_year + 5) == 5


def test_filter_blocks_inaccessible_npc(canon) -> None:
    char = _make(age=1)
    world = create_default_world(profile=CanonicityProfile.default(), starting_year=1)
    ctx = compute_scene_context(char, world, canon)
    actions = [
        {"label_fr": "Ne rien faire", "action_type": "wait", "parameters": {}},
        {
            "label_fr": "Demander conseil a Kabuto",
            "action_type": "talk",
            "parameters": {"character_id": "yakushi_kabuto"},
        },
        {
            "label_fr": "Parler a maman",
            "action_type": "talk",
            "parameters": {"character_id": "mere_du_perso"},
        },
    ]
    filtered = filter_proposed_actions(actions, ctx)
    labels = [a["label_fr"] for a in filtered]
    assert "Ne rien faire" in labels
    assert "Parler a maman" in labels  # PNJ generique = ok
    assert "Demander conseil a Kabuto" not in labels  # PNJ canon inaccessible = rejette
