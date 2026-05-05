"""Tests du state tracker runtime (pilier 4).

Couvre :
- get_age_from_birth_year (fonction pure)
- get_age (resolution name/id + canon, avec strict mode)
- is_alive (booleen non-throwing)
- RuntimeState schema (defaults, validation, JSON roundtrip)
- RuntimeState satisfait le Protocol StateView (peut etre passe au resolver)

Les tests utilisent un fake leger (dataclass) au lieu de construire un vrai
`Character` Pydantic qui demande ~30 champs requis. C'est legitime via le
Protocol `CanonView` defini dans age_calculator.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest

from shinobi.errors import (
    CharacterDeadError,
    CharacterNotFoundError,
    CharacterNotYetBornError,
)
from shinobi.preprocessing.reference_resolver import resolve_references
from shinobi.state.age_calculator import (
    get_age,
    get_age_from_birth_year,
    is_alive,
)
from shinobi.state.world_state import (
    DialogueTurn,
    NarrativeTime,
    PlayerCharacterState,
    RuntimeState,
    SceneContextSnapshot,
    WorldStateData,
)

# Fake canon


@dataclass
class FakeCharacter:
    id: str
    birth_year: int | None
    death_year: int | None = None


@dataclass
class FakeCanonBundle:
    characters: Mapping[str, FakeCharacter]


@pytest.fixture
def canon_minimal() -> FakeCanonBundle:
    return FakeCanonBundle(
        characters={
            "uzumaki_naruto": FakeCharacter("uzumaki_naruto", 0),
            "uchiha_sasuke": FakeCharacter("uchiha_sasuke", 0),
            "haruno_sakura": FakeCharacter("haruno_sakura", 0),
            "hatake_kakashi": FakeCharacter("hatake_kakashi", -14),
            "uchiha_itachi": FakeCharacter("uchiha_itachi", -5, death_year=14),
            "konohamaru_sarutobi": FakeCharacter("konohamaru_sarutobi", 8),
            "no_birth_year": FakeCharacter("no_birth_year", None),
        }
    )


# get_age_from_birth_year


class TestGetAgeFromBirthYear:
    def test_naruto_age_in_year_12(self) -> None:
        assert get_age_from_birth_year(0, 12) == 12

    def test_kakashi_age_in_year_0(self) -> None:
        assert get_age_from_birth_year(-14, 0) == 14

    def test_zero_at_birth(self) -> None:
        assert get_age_from_birth_year(0, 0) == 0

    def test_negative_age_raises_when_strict(self) -> None:
        with pytest.raises(CharacterNotYetBornError):
            get_age_from_birth_year(0, -3)

    def test_negative_age_clamps_when_not_strict(self) -> None:
        assert get_age_from_birth_year(0, -3, strict=False) == 0


# get_age


class TestGetAge:
    def test_naruto_in_year_12(self, canon_minimal: FakeCanonBundle) -> None:
        assert get_age("uzumaki_naruto", 12, canon_minimal) == 12

    def test_kakashi_year_0(self, canon_minimal: FakeCanonBundle) -> None:
        assert get_age("hatake_kakashi", 0, canon_minimal) == 14

    def test_kakashi_year_minus_14_is_zero(self, canon_minimal: FakeCanonBundle) -> None:
        assert get_age("hatake_kakashi", -14, canon_minimal) == 0

    def test_kakashi_not_yet_born_raises(self, canon_minimal: FakeCanonBundle) -> None:
        with pytest.raises(CharacterNotYetBornError):
            get_age("hatake_kakashi", -20, canon_minimal)

    def test_kakashi_not_yet_born_clamps_when_not_strict(
        self, canon_minimal: FakeCanonBundle
    ) -> None:
        assert get_age("hatake_kakashi", -20, canon_minimal, strict=False) == 0

    def test_itachi_dead_after_arc_raises(self, canon_minimal: FakeCanonBundle) -> None:
        with pytest.raises(CharacterDeadError):
            get_age("uchiha_itachi", 15, canon_minimal)

    def test_itachi_dead_does_not_raise_when_not_strict(
        self, canon_minimal: FakeCanonBundle
    ) -> None:
        # Mort en l'an 14, en l'an 15 il aurait 20 ans s'il etait toujours vivant.
        assert get_age("uchiha_itachi", 15, canon_minimal, strict=False) == 20

    def test_unknown_character_raises(self, canon_minimal: FakeCanonBundle) -> None:
        with pytest.raises(CharacterNotFoundError):
            get_age("unknown_ninja", 0, canon_minimal)

    def test_empty_name_raises(self, canon_minimal: FakeCanonBundle) -> None:
        with pytest.raises(CharacterNotFoundError):
            get_age("", 0, canon_minimal)

    def test_birth_year_missing_raises(self, canon_minimal: FakeCanonBundle) -> None:
        with pytest.raises(CharacterNotFoundError):
            get_age("no_birth_year", 0, canon_minimal)

    def test_id_lookup_is_case_insensitive(self, canon_minimal: FakeCanonBundle) -> None:
        # Le resolver lowercase la cle.
        assert get_age("UZUMAKI_NARUTO", 12, canon_minimal) == 12


# is_alive


class TestIsAlive:
    def test_naruto_alive_in_year_5(self, canon_minimal: FakeCanonBundle) -> None:
        assert is_alive("uzumaki_naruto", 5, canon_minimal) is True

    def test_naruto_not_yet_born_in_year_minus_1(self, canon_minimal: FakeCanonBundle) -> None:
        assert is_alive("uzumaki_naruto", -1, canon_minimal) is False

    def test_itachi_alive_at_year_13(self, canon_minimal: FakeCanonBundle) -> None:
        # Itachi mort en l'an 14, donc encore vivant en 13.
        assert is_alive("uchiha_itachi", 13, canon_minimal) is True

    def test_itachi_dead_at_year_14(self, canon_minimal: FakeCanonBundle) -> None:
        # death_year=14 signifie mort durant ou avant l'an 14.
        assert is_alive("uchiha_itachi", 14, canon_minimal) is False

    def test_unknown_character_not_alive(self, canon_minimal: FakeCanonBundle) -> None:
        assert is_alive("unknown", 0, canon_minimal) is False

    def test_no_birth_year_not_alive(self, canon_minimal: FakeCanonBundle) -> None:
        assert is_alive("no_birth_year", 0, canon_minimal) is False


# RuntimeState schema


class TestRuntimeStateSchema:
    def test_defaults_with_player_only(self) -> None:
        state = RuntimeState(player_character=PlayerCharacterState(name="Endo"))
        assert state.narrative_time.arc == "(non défini)"
        assert state.narrative_time.approximate_year == 0
        assert not state.narrative_time.post_timeskip
        assert state.scene_context.location is None
        assert state.scene_context.present_characters == []
        assert state.world_state.characters_dead == []
        assert state.dialogue_history == []

    def test_full_construction(self) -> None:
        state = RuntimeState(
            narrative_time=NarrativeTime(
                arc="chunin_exam", approximate_year=12, post_timeskip=False
            ),
            player_character=PlayerCharacterState(
                name="Endo", village="konoha", rank="genin", birth_year=0
            ),
            scene_context=SceneContextSnapshot(
                location="konoha_training_ground_3",
                present_characters=["iruka_umino"],
                last_mentioned_character="iruka_umino",
            ),
        )
        assert state.player_character.name == "Endo"
        assert state.scene_context.present_characters == ["iruka_umino"]
        assert state.narrative_time.arc == "chunin_exam"

    def test_extra_field_forbidden(self) -> None:
        # extra="forbid" doit rejeter les champs inconnus.
        with pytest.raises(Exception):  # noqa: B017
            RuntimeState(
                player_character=PlayerCharacterState(name="Endo"),
                unknown_field="boom",  # type: ignore[call-arg]
            )

    def test_player_character_required(self) -> None:
        with pytest.raises(Exception):  # noqa: B017
            RuntimeState()  # type: ignore[call-arg]


# RuntimeState implements StateView (Protocol satisfaction)


class TestRuntimeStateImplementsStateView:
    def test_last_mentioned_character_property(self) -> None:
        state = RuntimeState(
            player_character=PlayerCharacterState(name="Endo"),
            scene_context=SceneContextSnapshot(last_mentioned_character="naruto"),
        )
        assert state.last_mentioned_character == "naruto"

    def test_present_characters_property(self) -> None:
        state = RuntimeState(
            player_character=PlayerCharacterState(name="Endo"),
            scene_context=SceneContextSnapshot(present_characters=["iruka", "sakura"]),
        )
        assert tuple(state.present_characters) == ("iruka", "sakura")

    def test_current_location_property(self) -> None:
        state = RuntimeState(
            player_character=PlayerCharacterState(name="Endo"),
            scene_context=SceneContextSnapshot(location="academie"),
        )
        assert state.current_location == "academie"

    def test_runtime_state_can_drive_resolver(self) -> None:
        """Le RuntimeState doit pouvoir etre passe directement a resolve_references."""
        state = RuntimeState(
            player_character=PlayerCharacterState(name="Endo"),
            scene_context=SceneContextSnapshot(last_mentioned_character="Sasuke"),
        )
        # type: ignore parce que mypy ne fait pas le lien Protocol/duck typing
        # immediatement, mais pylance et pytest le font correctement au runtime.
        result = resolve_references("je vais le voir", state)  # type: ignore[arg-type]
        assert not result.is_ambiguous
        assert "Sasuke" in result.rewritten

    def test_empty_runtime_state_resolver_asks_clarification(self) -> None:
        state = RuntimeState(player_character=PlayerCharacterState(name="Endo"))
        result = resolve_references("je le vois", state)  # type: ignore[arg-type]
        assert result.is_ambiguous


# Roundtrip JSON


class TestRuntimeStateRoundtrip:
    def test_json_roundtrip_minimal(self, tmp_path: Path) -> None:
        original = RuntimeState(player_character=PlayerCharacterState(name="Endo"))
        path = tmp_path / "state.json"
        original.save(path)
        loaded = RuntimeState.load(path)
        assert loaded == original

    def test_json_roundtrip_full(self, tmp_path: Path) -> None:
        original = RuntimeState(
            narrative_time=NarrativeTime(
                arc="pain_invasion", approximate_year=16, post_timeskip=True
            ),
            player_character=PlayerCharacterState(
                name="Endo",
                village="konoha",
                rank="genin",
                birth_year=0,
                location="konoha_main_gate",
                known_jutsu=["kage_bunshin_no_jutsu"],
            ),
            world_state=WorldStateData(
                key_events_resolved=["chunin_exam", "sasuke_defection"],
                destroyed_locations=["konoha_inner_district_3"],
            ),
            scene_context=SceneContextSnapshot(
                location="konoha_main_gate",
                present_characters=["hatake_kakashi"],
                last_mentioned_character="hatake_kakashi",
                time_of_day="dusk",
                mood="tense",
            ),
            dialogue_history=[
                DialogueTurn(turn=1, speaker="Endo", text="Salut sensei", referents={}),
                DialogueTurn(turn=2, speaker="hatake_kakashi", text="Tu es en retard."),
            ],
        )
        path = tmp_path / "state.json"
        original.save(path)
        loaded = RuntimeState.load(path)
        assert loaded == original

    def test_json_string_roundtrip(self) -> None:
        original = RuntimeState(player_character=PlayerCharacterState(name="Endo"))
        raw = original.to_json()
        loaded = RuntimeState.from_json(raw)
        assert loaded == original
