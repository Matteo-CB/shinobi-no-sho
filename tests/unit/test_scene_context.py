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
    filtered = filter_proposed_actions(actions, ctx, canon=canon)
    labels = [a["label_fr"] for a in filtered]
    assert "Ne rien faire" in labels
    assert "Parler a maman" in labels
    assert "Demander conseil a Kabuto" not in labels


def test_filter_general_blocks_label_only_references(canon) -> None:
    """Le filtre doit detecter le nom canon dans le label meme sans character_id."""
    char = _make(age=1)
    world = create_default_world(profile=CanonicityProfile.default(), starting_year=1)
    ctx = compute_scene_context(char, world, canon)
    # Cas reels d'incoherences typiques pour un bebe a Konoha en l'an 1 :
    actions = [
        {"label_fr": "Aller voir Orochimaru pour des conseils", "action_type": "talk", "parameters": {}},
        {"label_fr": "Demander a Madara comment maitriser le Sharingan", "action_type": "talk", "parameters": {}},
        {"label_fr": "Suivre l'entrainement de Hashirama", "action_type": "talk", "parameters": {}},
        {"label_fr": "Parler avec Kakashi qui passe par hasard", "action_type": "talk", "parameters": {}},
        {"label_fr": "M'entrainer avec papa au quartier Uchiha", "action_type": "train_stat", "parameters": {}},
        {"label_fr": "Apprendre a marcher", "action_type": "train_stat", "parameters": {}},
    ]
    filtered = filter_proposed_actions(actions, ctx, canon=canon)
    labels = [a["label_fr"] for a in filtered]
    # Tous les noms canon de persos morts/absents/inaccessibles a l'an 1 doivent etre rejetes
    assert not any("Orochimaru" in lb for lb in labels), labels
    assert not any("Madara" in lb for lb in labels), labels
    assert not any("Hashirama" in lb for lb in labels), labels
    assert not any("Kakashi" in lb for lb in labels), labels
    # Les actions generiques sans nom de canon doivent passer
    assert any(lb == "Apprendre a marcher" for lb in labels)
    assert any("M'entrainer avec papa" in lb for lb in labels)


def test_filter_keeps_accessible_canon_npcs(canon) -> None:
    """Si un perso canon est dans accessible_npcs, son nom dans label ne doit PAS etre rejete."""
    char = _make(age=12, rank="genin")
    world = create_default_world(profile=CanonicityProfile.default(), starting_year=12)
    ctx = compute_scene_context(char, world, canon)
    accessible_names = [n.name for n in ctx.accessible_npcs]
    if not accessible_names:
        return  # nothing to test
    target_name = accessible_names[0]
    actions = [
        {"label_fr": f"Saluer {target_name}", "action_type": "talk", "parameters": {}},
    ]
    filtered = filter_proposed_actions(actions, ctx, canon=canon)
    assert any(target_name in a["label_fr"] for a in filtered), \
        f"PNJ accessible {target_name} a ete rejete a tort"
