"""Tests des 4 nouvelles couches de validation rerintegrees :
- ExplicitAgeLayer : 'Naruto, ninja de 10 ans' alors qu'a year=6 il a 6 ans
- AnachronismLayer : 'Tsunade 5e Hokage' en l'an 6 alors que 3e (Hiruzen) en place
- PlayerFriendshipLayer : 'X ami proche d'Endo' alors que joueur OC sans relation
- CoordinationFriendsLayer : 'amis comme X, Y, Z, W' alors que canon contredit
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import pytest

from shinobi.state.world_state import (
    NarrativeTime,
    PlayerCharacterState,
    RuntimeState,
    SceneContextSnapshot,
    WorldStateData,
)
from shinobi.validation import (
    AnachronismLayer,
    CoordinationFriendsLayer,
    ExplicitAgeLayer,
    NarrativeDialogue,
    NarrativeOutput,
    PlayerFriendshipLayer,
)


@dataclass
class FakeCharacter:
    id: str
    birth_year: int | None
    death_year: int | None = None


@dataclass
class FakeCanonBundle:
    characters: Mapping[str, FakeCharacter]


@pytest.fixture
def canon() -> FakeCanonBundle:
    return FakeCanonBundle(
        characters={
            "uzumaki_naruto": FakeCharacter("uzumaki_naruto", 0),
            "uchiha_sasuke": FakeCharacter("uchiha_sasuke", 0),
            "haruno_sakura": FakeCharacter("haruno_sakura", 0),
            "aburame_shino": FakeCharacter("aburame_shino", 0),
            "akimichi_choji": FakeCharacter("akimichi_choji", 0),
            "inuzuka_kiba": FakeCharacter("inuzuka_kiba", 0),
            "tsunade": FakeCharacter("tsunade", -50),
            "sarutobi_hiruzen": FakeCharacter("sarutobi_hiruzen", -45, death_year=12),
        }
    )


def make_state(year: int = 6, player_name: str = "Endo Uchiha", established: list[str] | None = None) -> RuntimeState:
    return RuntimeState(
        narrative_time=NarrativeTime(approximate_year=year),
        player_character=PlayerCharacterState(
            name=player_name,
            established_npc_relationships=established or [],
        ),
        world_state=WorldStateData(),
        scene_context=SceneContextSnapshot(),
    )


# --- ExplicitAgeLayer -------------------------------------------------------


def test_explicit_age_detects_wrong_age(canon: FakeCanonBundle) -> None:
    out = NarrativeOutput(
        narrative="Naruto, jeune ninja de 10 ans, marche dans les rues de Konoha."
    )
    result = ExplicitAgeLayer().validate(narrative_output=out, state=make_state(year=6), canon=canon)
    assert not result.is_valid
    assert any("10 ans" in d for d in result.details)


def test_explicit_age_passes_correct_age(canon: FakeCanonBundle) -> None:
    out = NarrativeOutput(
        narrative="Naruto, jeune ninja de 6 ans, marche dans les rues."
    )
    result = ExplicitAgeLayer().validate(narrative_output=out, state=make_state(year=6), canon=canon)
    assert result.is_valid


def test_explicit_age_tolerance_one_year(canon: FakeCanonBundle) -> None:
    out = NarrativeOutput(
        narrative="Naruto, ninja de 7 ans..."  # tolerance ok (1 an d'ecart)
    )
    result = ExplicitAgeLayer().validate(narrative_output=out, state=make_state(year=6), canon=canon)
    assert result.is_valid


def test_explicit_age_skips_unknown_npc(canon: FakeCanonBundle) -> None:
    out = NarrativeOutput(
        narrative="Endo, jeune garcon de 6 ans, observe la scene."
    )
    # Endo n'est pas dans le canon -> skip silencieux
    result = ExplicitAgeLayer().validate(narrative_output=out, state=make_state(year=6), canon=canon)
    assert result.is_valid


# --- AnachronismLayer -------------------------------------------------------


def test_anachronism_tsunade_5e_hokage_an_6(canon: FakeCanonBundle) -> None:
    out = NarrativeOutput(
        narrative="Tsunade, la cinquieme Hokage, est assise derriere son bureau."
    )
    result = AnachronismLayer().validate(narrative_output=out, state=make_state(year=6), canon=canon)
    assert not result.is_valid
    assert any("Hokage" in d for d in result.details)


def test_anachronism_hiruzen_3e_hokage_an_6_ok(canon: FakeCanonBundle) -> None:
    out = NarrativeOutput(
        narrative="Hiruzen, le troisieme Hokage, surveille le village."
    )
    result = AnachronismLayer().validate(narrative_output=out, state=make_state(year=6), canon=canon)
    assert result.is_valid


def test_anachronism_tsunade_5e_an_13_ok(canon: FakeCanonBundle) -> None:
    out = NarrativeOutput(
        narrative="Tsunade, la cinquieme Hokage, regarde par la fenetre."
    )
    result = AnachronismLayer().validate(narrative_output=out, state=make_state(year=13), canon=canon)
    assert result.is_valid


def test_anachronism_no_rank_no_check(canon: FakeCanonBundle) -> None:
    out = NarrativeOutput(narrative="Tsunade voyage a travers les pays.")
    result = AnachronismLayer().validate(narrative_output=out, state=make_state(year=6), canon=canon)
    assert result.is_valid


# --- PlayerFriendshipLayer --------------------------------------------------


def test_friendship_invented_naruto_endo(canon: FakeCanonBundle) -> None:
    out = NarrativeOutput(
        narrative="Naruto, qui est un ami proche de Endo, le salua chaleureusement."
    )
    result = PlayerFriendshipLayer().validate(
        narrative_output=out,
        state=make_state(year=6, player_name="Endo Uchiha", established=[]),
        canon=canon,
    )
    assert not result.is_valid
    assert any("uzumaki_naruto" in d for d in result.details)


def test_friendship_established_passes(canon: FakeCanonBundle) -> None:
    out = NarrativeOutput(
        narrative="Naruto, qui est un ami proche de Endo, le salua chaleureusement."
    )
    result = PlayerFriendshipLayer().validate(
        narrative_output=out,
        state=make_state(year=6, player_name="Endo Uchiha", established=["uzumaki_naruto"]),
        canon=canon,
    )
    assert result.is_valid


def test_friendship_no_player_name_skips(canon: FakeCanonBundle) -> None:
    out = NarrativeOutput(narrative="Naruto, ami proche de Endo, salue.")
    result = PlayerFriendshipLayer().validate(
        narrative_output=out, state=make_state(player_name=""), canon=canon,
    )
    assert result.is_valid


# --- CoordinationFriendsLayer ----------------------------------------------


def test_coordination_friends_naruto_a_des_amis(canon: FakeCanonBundle, monkeypatch) -> None:
    """Mock psycho_notes pour forcer 'pas d'amis' sur Naruto."""
    import shinobi.canon.fact_sheet as fs

    monkeypatch.setattr(
        fs, "_psycho_entry_at",
        lambda npc_id, age: (
            {"note": "Ostracise par le village. Pas d'amis."}
            if npc_id == "uzumaki_naruto" else None
        ),
    )

    out = NarrativeOutput(
        narrative="Naruto repond qu il a des amis comme Sakura, Shino et Choji."
    )
    result = CoordinationFriendsLayer().validate(
        narrative_output=out, state=make_state(year=6), canon=canon,
    )
    assert not result.is_valid
    assert any("uzumaki_naruto" in d for d in result.details)


def test_coordination_friends_no_match_passes(canon: FakeCanonBundle, monkeypatch) -> None:
    import shinobi.canon.fact_sheet as fs
    monkeypatch.setattr(fs, "_psycho_entry_at", lambda npc_id, age: None)

    out = NarrativeOutput(narrative="Naruto marche dans la rue.")
    result = CoordinationFriendsLayer().validate(
        narrative_output=out, state=make_state(year=6), canon=canon,
    )
    assert result.is_valid


def test_coordination_friends_dialogue_text(canon: FakeCanonBundle, monkeypatch) -> None:
    """La couche scanne aussi les dialogues NPC."""
    import shinobi.canon.fact_sheet as fs

    monkeypatch.setattr(
        fs, "_psycho_entry_at",
        lambda npc_id, age: (
            {"note": "Ostracise."}
            if npc_id == "uzumaki_naruto" else None
        ),
    )

    out = NarrativeOutput(
        narrative="Endo demande.",
        npc_dialogue=[NarrativeDialogue(
            character_id="uzumaki_naruto",
            line="J'ai des amis comme Sakura et Choji.",
        )],
    )
    result = CoordinationFriendsLayer().validate(
        narrative_output=out, state=make_state(year=6), canon=canon,
    )
    assert not result.is_valid
