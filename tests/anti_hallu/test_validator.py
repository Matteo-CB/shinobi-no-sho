"""Tests du validator central + couches A (sherlock) et C (age coherence).

Branchement state -> validator :
- RuntimeState fournit le contexte (year, runtime dead, destroyed locations)
- Le validator combine ce contexte avec les sorties du LLM
- Les couches retournent ValidationResult, le Validator orchestre
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import pytest

from shinobi.state.age_calculator import CanonStatus, get_canon_status
from shinobi.state.world_state import (
    CharacterDeath,
    NarrativeTime,
    PlayerCharacterState,
    RuntimeState,
    SceneContextSnapshot,
    WorldStateData,
)
from shinobi.validation import (
    AgeCoherenceLayer,
    NarrativeAction,
    NarrativeDialogue,
    NarrativeOutput,
    SherlockRulesLayer,
    ValidationResult,
    Validator,
    format_violations_for_regen,
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
def canon_minimal() -> FakeCanonBundle:
    return FakeCanonBundle(
        characters={
            "uzumaki_naruto": FakeCharacter("uzumaki_naruto", 0),
            "uchiha_sasuke": FakeCharacter("uchiha_sasuke", 0),
            "haruno_sakura": FakeCharacter("haruno_sakura", 0),
            "hatake_kakashi": FakeCharacter("hatake_kakashi", -14),
            "umino_iruka": FakeCharacter("umino_iruka", -10),
            "jiraiya": FakeCharacter("jiraiya", -50, death_year=14),
            "sarutobi_hiruzen": FakeCharacter("sarutobi_hiruzen", -45, death_year=12),
        }
    )


def make_state(
    *,
    year: int = 12,
    location: str = "konoha_main_gate",
    dead: list[CharacterDeath] | None = None,
    destroyed: list[str] | None = None,
) -> RuntimeState:
    return RuntimeState(
        narrative_time=NarrativeTime(arc="(test)", approximate_year=year),
        player_character=PlayerCharacterState(name="Endo"),
        world_state=WorldStateData(
            characters_dead=dead or [],
            destroyed_locations=destroyed or [],
        ),
        scene_context=SceneContextSnapshot(location=location),
    )


# get_canon_status (helper utilise par la couche A)


class TestCanonStatus:
    def test_alive_in_arc(self, canon_minimal: FakeCanonBundle) -> None:
        assert get_canon_status("uzumaki_naruto", 12, canon_minimal) == CanonStatus.alive

    def test_not_yet_born(self, canon_minimal: FakeCanonBundle) -> None:
        assert get_canon_status("hatake_kakashi", -20, canon_minimal) == CanonStatus.not_yet_born

    def test_dead_after_arc(self, canon_minimal: FakeCanonBundle) -> None:
        assert get_canon_status("jiraiya", 16, canon_minimal) == CanonStatus.dead

    def test_unknown_character(self, canon_minimal: FakeCanonBundle) -> None:
        assert get_canon_status("marchand_taverne", 12, canon_minimal) == CanonStatus.unknown


# Couche A : sherlock rules


class TestSherlockRulesDeadActors:
    def test_runtime_dead_speaker_rejected(self, canon_minimal: FakeCanonBundle) -> None:
        layer = SherlockRulesLayer()
        state = make_state(year=10, dead=[CharacterDeath(name="jiraiya", death_arc="custom_run")])
        output = NarrativeOutput(
            npc_dialogue=[NarrativeDialogue(character_id="jiraiya", line="Bonjour")],
        )
        r = layer.validate(narrative_output=output, state=state, canon=canon_minimal)
        assert not r.is_valid
        assert "jiraiya" in r.details[0].lower()
        assert "runtime" in r.details[0].lower()

    def test_canon_dead_speaker_rejected(self, canon_minimal: FakeCanonBundle) -> None:
        # Jiraiya canon mort en l'an 14, on est en l'an 16.
        layer = SherlockRulesLayer()
        state = make_state(year=16)
        output = NarrativeOutput(
            npc_dialogue=[NarrativeDialogue(character_id="jiraiya", line="Salut")],
        )
        r = layer.validate(narrative_output=output, state=state, canon=canon_minimal)
        assert not r.is_valid
        assert "canoniquement" in r.details[0].lower() or "canonic" in r.details[0].lower()

    def test_not_yet_born_speaker_rejected(self, canon_minimal: FakeCanonBundle) -> None:
        # Kakashi pas encore ne en l'an -20.
        layer = SherlockRulesLayer()
        state = make_state(year=-20)
        output = NarrativeOutput(
            npc_dialogue=[NarrativeDialogue(character_id="hatake_kakashi", line="...")],
        )
        r = layer.validate(narrative_output=output, state=state, canon=canon_minimal)
        assert not r.is_valid
        assert "encore" in r.details[0].lower() or "né" in r.details[0].lower()

    def test_unknown_pnj_passes(self, canon_minimal: FakeCanonBundle) -> None:
        # PNJ generique non connu du canon : ne doit pas etre rejete.
        layer = SherlockRulesLayer()
        state = make_state(year=10)
        output = NarrativeOutput(
            npc_dialogue=[
                NarrativeDialogue(character_id="marchand_taverne", line="Bienvenue"),
            ],
        )
        r = layer.validate(narrative_output=output, state=state, canon=canon_minimal)
        assert r.is_valid

    def test_alive_actor_passes(self, canon_minimal: FakeCanonBundle) -> None:
        layer = SherlockRulesLayer()
        state = make_state(year=12)
        output = NarrativeOutput(
            npc_dialogue=[
                NarrativeDialogue(character_id="hatake_kakashi", line="Tu es en retard"),
            ],
        )
        r = layer.validate(narrative_output=output, state=state, canon=canon_minimal)
        assert r.is_valid

    def test_dead_actor_in_action_rejected(self, canon_minimal: FakeCanonBundle) -> None:
        layer = SherlockRulesLayer()
        state = make_state(year=16)
        output = NarrativeOutput(
            actions=[
                NarrativeAction(actor="jiraiya", type="movement", location="konoha_main_gate"),
            ],
        )
        r = layer.validate(narrative_output=output, state=state, canon=canon_minimal)
        assert not r.is_valid


class TestSherlockRulesDestroyedLocations:
    def test_scene_in_destroyed_location_rejected(
        self, canon_minimal: FakeCanonBundle
    ) -> None:
        layer = SherlockRulesLayer()
        state = make_state(
            year=16,
            location="konoha_district_3",
            destroyed=["konoha_district_3"],
        )
        output = NarrativeOutput(narrative="...")
        r = layer.validate(narrative_output=output, state=state, canon=canon_minimal)
        assert not r.is_valid

    def test_action_in_destroyed_location_rejected(
        self, canon_minimal: FakeCanonBundle
    ) -> None:
        layer = SherlockRulesLayer()
        state = make_state(year=16, destroyed=["konoha_district_3"])
        output = NarrativeOutput(
            actions=[
                NarrativeAction(
                    actor="uzumaki_naruto", type="movement", location="konoha_district_3"
                ),
            ],
        )
        r = layer.validate(narrative_output=output, state=state, canon=canon_minimal)
        assert not r.is_valid

    def test_intact_location_passes(self, canon_minimal: FakeCanonBundle) -> None:
        layer = SherlockRulesLayer()
        state = make_state(year=12, location="konoha_main_gate")
        output = NarrativeOutput(narrative="...")
        r = layer.validate(narrative_output=output, state=state, canon=canon_minimal)
        assert r.is_valid


class TestSherlockRulesUbiquity:
    def test_actor_in_two_locations_rejected(self, canon_minimal: FakeCanonBundle) -> None:
        layer = SherlockRulesLayer()
        state = make_state(year=12)
        output = NarrativeOutput(
            actions=[
                NarrativeAction(
                    actor="uzumaki_naruto", type="dialogue", location="konoha_academy"
                ),
                NarrativeAction(
                    actor="uzumaki_naruto", type="movement", location="konoha_main_gate"
                ),
            ],
        )
        r = layer.validate(narrative_output=output, state=state, canon=canon_minimal)
        assert not r.is_valid
        assert "plusieurs lieux" in r.details[0].lower() or "ubiquit" in r.details[0].lower()

    def test_actor_in_same_location_passes(self, canon_minimal: FakeCanonBundle) -> None:
        layer = SherlockRulesLayer()
        state = make_state(year=12)
        output = NarrativeOutput(
            actions=[
                NarrativeAction(
                    actor="uzumaki_naruto", type="dialogue", location="konoha_academy"
                ),
                NarrativeAction(
                    actor="uzumaki_naruto", type="movement", location="konoha_academy"
                ),
            ],
        )
        r = layer.validate(narrative_output=output, state=state, canon=canon_minimal)
        assert r.is_valid


# Couche C : age coherence


class TestAgeCoherenceChild:
    def test_strategic_vocab_in_child_rejected(self, canon_minimal: FakeCanonBundle) -> None:
        # Naruto a 5 ans avec vocabulaire complexe.
        layer = AgeCoherenceLayer()
        state = make_state(year=5)
        output = NarrativeOutput(
            npc_dialogue=[
                NarrativeDialogue(
                    character_id="uzumaki_naruto",
                    line=(
                        "J'ai analysé la stratégie diplomatique du Hokage et j'ai conclu que "
                        "la conséquence politique sera désastreuse."
                    ),
                ),
            ],
        )
        r = layer.validate(narrative_output=output, state=state, canon=canon_minimal)
        assert not r.is_valid
        assert "5 ans" in r.details[0]

    def test_normal_child_speech_passes(self, canon_minimal: FakeCanonBundle) -> None:
        layer = AgeCoherenceLayer()
        state = make_state(year=5)
        output = NarrativeOutput(
            npc_dialogue=[
                NarrativeDialogue(
                    character_id="uzumaki_naruto", line="Je veux des ramens, dattebayo !"
                ),
            ],
        )
        r = layer.validate(narrative_output=output, state=state, canon=canon_minimal)
        assert r.is_valid


class TestAgeCoherenceAdult:
    def test_baby_talk_in_adult_rejected(self, canon_minimal: FakeCanonBundle) -> None:
        # Kakashi a 26 ans en l'an 12.
        layer = AgeCoherenceLayer()
        state = make_state(year=12)
        output = NarrativeOutput(
            npc_dialogue=[
                NarrativeDialogue(
                    character_id="hatake_kakashi",
                    line="Areu areu, maman m'a dit que je suis un grand garçon.",
                ),
            ],
        )
        r = layer.validate(narrative_output=output, state=state, canon=canon_minimal)
        assert not r.is_valid

    def test_normal_adult_speech_passes(self, canon_minimal: FakeCanonBundle) -> None:
        layer = AgeCoherenceLayer()
        state = make_state(year=12)
        output = NarrativeOutput(
            npc_dialogue=[
                NarrativeDialogue(character_id="hatake_kakashi", line="Tu es en retard."),
            ],
        )
        r = layer.validate(narrative_output=output, state=state, canon=canon_minimal)
        assert r.is_valid


class TestAgeCoherenceSkipsUnknown:
    def test_unknown_pnj_skipped(self, canon_minimal: FakeCanonBundle) -> None:
        layer = AgeCoherenceLayer()
        state = make_state(year=12)
        output = NarrativeOutput(
            npc_dialogue=[
                NarrativeDialogue(
                    character_id="marchand_inconnu",
                    line="J'ai analysé la stratégie diplomatique de mon village.",
                ),
            ],
        )
        r = layer.validate(narrative_output=output, state=state, canon=canon_minimal)
        assert r.is_valid


class TestAgeCoherenceMidAgeNotChecked:
    def test_genin_age_in_middle_zone_passes(self, canon_minimal: FakeCanonBundle) -> None:
        # Naruto a 12 ans : entre les deux seuils, on n'applique pas le check.
        # Les nuances genin/jonin viendront via overlay behavior_profiles plus tard.
        layer = AgeCoherenceLayer()
        state = make_state(year=12)
        output = NarrativeOutput(
            npc_dialogue=[
                NarrativeDialogue(
                    character_id="uzumaki_naruto",
                    line="Bof, tactique, j'analyse la situation politique de l'ennemi.",
                ),
            ],
        )
        r = layer.validate(narrative_output=output, state=state, canon=canon_minimal)
        assert r.is_valid


# Validator orchestration


class TestValidatorOrchestration:
    def test_short_circuit_stops_at_first_reject(self, canon_minimal: FakeCanonBundle) -> None:
        validator = Validator([SherlockRulesLayer(), AgeCoherenceLayer()])
        state = make_state(year=5, dead=[CharacterDeath(name="jiraiya", death_arc="run")])
        output = NarrativeOutput(
            npc_dialogue=[
                NarrativeDialogue(character_id="jiraiya", line="Bonjour"),
                NarrativeDialogue(
                    character_id="uzumaki_naruto",
                    line="J'ai analysé la stratégie diplomatique de l'ennemi.",
                ),
            ],
        )
        results = validator.validate(narrative_output=output, state=state, canon=canon_minimal)
        assert len(results) == 1  # short-circuit applied
        assert results[0].layer == "sherlock_rules"
        assert not Validator.is_valid(results)

    def test_cumulative_runs_all_layers(self, canon_minimal: FakeCanonBundle) -> None:
        validator = Validator([SherlockRulesLayer(), AgeCoherenceLayer()])
        state = make_state(year=5, dead=[CharacterDeath(name="jiraiya", death_arc="run")])
        output = NarrativeOutput(
            npc_dialogue=[
                NarrativeDialogue(character_id="jiraiya", line="Bonjour"),
                NarrativeDialogue(
                    character_id="uzumaki_naruto",
                    line=(
                        "J'ai analysé la stratégie diplomatique du Hokage et j'ai conclu que "
                        "les conséquences politiques sont graves."
                    ),
                ),
            ],
        )
        results = validator.validate(
            narrative_output=output, state=state, canon=canon_minimal, short_circuit=False
        )
        assert len(results) == 2
        assert not Validator.is_valid(results)

    def test_all_layers_pass(self, canon_minimal: FakeCanonBundle) -> None:
        validator = Validator([SherlockRulesLayer(), AgeCoherenceLayer()])
        state = make_state(year=12)
        output = NarrativeOutput(
            npc_dialogue=[
                NarrativeDialogue(character_id="hatake_kakashi", line="Tu es en retard."),
            ],
        )
        results = validator.validate(narrative_output=output, state=state, canon=canon_minimal)
        assert Validator.is_valid(results)


# Regen formatter


class TestRegenFormatter:
    def test_format_with_violations(self) -> None:
        results = [
            ValidationResult(
                is_valid=False,
                layer="sherlock_rules",
                reason="2 violations détectées",
                details=["jiraiya est mort", "lieu détruit"],
            ),
            ValidationResult(is_valid=True, layer="age_coherence"),
        ]
        text = format_violations_for_regen(results)
        assert "sherlock_rules" in text
        assert "jiraiya" in text
        assert "lieu détruit" in text
        # La couche valide ne doit pas apparaitre dans le feedback.
        assert "[Couche age_coherence]" not in text

    def test_format_empty_when_all_valid(self) -> None:
        results = [
            ValidationResult(is_valid=True, layer="sherlock_rules"),
            ValidationResult(is_valid=True, layer="age_coherence"),
        ]
        assert format_violations_for_regen(results) == ""

    def test_format_includes_details(self) -> None:
        results = [
            ValidationResult(
                is_valid=False,
                layer="sherlock_rules",
                reason="3 violations détectées",
                details=["actor mort", "lieu détruit", "ubiquité"],
            ),
        ]
        text = format_violations_for_regen(results)
        assert "actor mort" in text
        assert "lieu détruit" in text
        assert "ubiquité" in text
