"""Tests cross-cutting Sprint Integration : Narrator + DialogueLog + Missions + KG.

Verifie que les composants des phases A/B/C cohabitent avec les sprints VN
Dialogue + Missions :

- Le Narrator accepte un DialogueFormatter + DialogueLog optionnels et appende
  automatiquement les sorties LLM converties en DialogueLines.
- Les missions importees dans le KG produisent les facts attendus pour les
  invariants Phase C (involves, occurs_in_year, participated_in_mission).
- L'helper `_ensure_kg_initialized` (mode CLI play_session) est idempotent :
  re-appel ne duplique pas les facts.
- Le format VN payload est correct apres une session simulee de plusieurs
  tours (multi-scenes, year shift, mission related).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from shinobi.dialogue import (
    DialogueFormatter,
    DialogueLog,
    export_to_vn_payload,
)
from shinobi.kg.store import KnowledgeGraphStore
from shinobi.llm.narration import NarrationRequest, NarrationResponse, Narrator
from shinobi.missions.catalog import MissionCatalog
from shinobi.missions.kg_integration import import_missions_to_kg

# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------


def _make_canon_minimal():
    """CanonBundle stub minimal (suffisant pour tester capture VN sans validation)."""
    canon = MagicMock()
    canon.characters = {}
    canon.techniques = {}
    canon.clans = {}
    canon.villages = {}
    canon.organizations = {}
    canon.tailed_beasts = {}
    canon.kekkei_genkai = {}
    canon.kekkei_mora = {}
    canon.hiden = {}
    canon.weapons_tools = {}
    canon.locations = {}
    canon.timeline_events = {}
    canon.voice_profiles = {}
    return canon


def _make_narrator_no_validation() -> Narrator:
    """Narrator avec mocks pour client+retriever, sans validation anti-hallu."""
    client = MagicMock()
    retriever = MagicMock()
    canon = _make_canon_minimal()
    formatter = DialogueFormatter()
    log = DialogueLog()
    return Narrator(
        client, canon, retriever,
        enable_anti_hallu_validation=False,
        dialogue_formatter=formatter,
        dialogue_log=log,
    )


# ----------------------------------------------------------------------------
# Capture VN par le Narrator
# ----------------------------------------------------------------------------


def test_narrator_capture_dialogues_when_wired() -> None:
    """Quand DialogueFormatter+DialogueLog sont fournis, capture auto."""
    narrator = _make_narrator_no_validation()
    response = NarrationResponse(
        narrative="Naruto regarde le ciel. Le vent souffle.",
        npc_dialogue=[
            {"character_id": "uzumaki_naruto", "line": "On va y arriver.", "tone": "determined"},
        ],
        proposed_actions=[],
        world_observations=[],
        clarification_request=None,
    )
    request = NarrationRequest(
        turn_summary="t",
        action_text="t",
        action_result_summary="t",
        location_id="konohagakure",
        present_npcs=["uzumaki_naruto"],
        active_breadcrumb_descriptions=[],
        character_state_summary="",
        duration_str="1h",
        turn_number=42,
        in_game_year=12,
        in_game_date="04-15",
    )
    n = narrator._capture_dialogues(response, request)
    assert n >= 2
    log = narrator.dialogue_log
    assert log is not None
    # narrator + npc lines doivent etre presents
    speakers = log.speakers()
    assert "uzumaki_naruto" in speakers


def test_narrator_capture_no_op_when_unwired() -> None:
    """Sans formatter/log, _capture_dialogues retourne 0."""
    client = MagicMock()
    retriever = MagicMock()
    canon = _make_canon_minimal()
    narrator = Narrator(client, canon, retriever, enable_anti_hallu_validation=False)
    response = NarrationResponse(
        narrative="x", npc_dialogue=[], proposed_actions=[],
        world_observations=[], clarification_request=None,
    )
    request = NarrationRequest(
        turn_summary="", action_text="", action_result_summary="",
        location_id=None, present_npcs=[],
        active_breadcrumb_descriptions=[],
        character_state_summary="", duration_str="",
    )
    assert narrator._capture_dialogues(response, request) == 0


def test_narrator_capture_uses_request_year_over_scene_context() -> None:
    """Si request.in_game_year est fourni, prevaut sur scene_context."""
    narrator = _make_narrator_no_validation()
    response = NarrationResponse(
        narrative="Texte.", npc_dialogue=[],
        proposed_actions=[], world_observations=[],
        clarification_request=None,
    )
    request = NarrationRequest(
        turn_summary="", action_text="", action_result_summary="",
        location_id=None, present_npcs=[],
        active_breadcrumb_descriptions=[],
        character_state_summary="", duration_str="",
        in_game_year=15, turn_number=7,
    )
    narrator._capture_dialogues(response, request)
    lines = narrator.dialogue_log.all()
    assert len(lines) >= 1
    assert lines[0].in_game_year == 15
    assert lines[0].turn_number == 7


# ----------------------------------------------------------------------------
# Missions -> KG facts (verification que tout est bien injecte)
# ----------------------------------------------------------------------------


def test_missions_kg_integration_idempotent() -> None:
    """Re-appel de import_missions_to_kg avec clear_first ne duplique pas."""
    catalog_path = Path("data/canonical/missions.json")
    if not catalog_path.exists():
        pytest.skip("missions.json absent")
    catalog = MissionCatalog.from_json_file(catalog_path)
    if catalog.count == 0:
        pytest.skip("missions.json vide")

    with KnowledgeGraphStore(":memory:") as store:
        s1 = import_missions_to_kg(store, catalog.all(), clear_first=True)
        c1 = store.count(source_prefix="mission:")
        s2 = import_missions_to_kg(store, catalog.all(), clear_first=True)
        c2 = store.count(source_prefix="mission:")
        assert s1["facts_inserted"] == s2["facts_inserted"]
        assert c1 == c2


def test_missions_kg_double_direction_facts_present() -> None:
    """Pour chaque participant : (mid, involves, npc) ET (npc, participated_in_mission, mid)."""
    catalog_path = Path("data/canonical/missions.json")
    if not catalog_path.exists():
        pytest.skip("missions.json absent")
    catalog = MissionCatalog.from_json_file(catalog_path)
    if catalog.count == 0:
        pytest.skip("missions.json vide")

    with KnowledgeGraphStore(":memory:") as store:
        import_missions_to_kg(store, catalog.all(), clear_first=True)
        # Sample : prend la premiere mission avec >=1 participant
        target = next((m for m in catalog.all() if m.participants), None)
        assert target is not None
        npc_id = target.participants[0].character_id

        forward = store.get_facts(
            subject=target.id, relation="involves", object_value=npc_id,
        )
        backward = store.get_facts(
            subject=npc_id, relation="participated_in_mission",
            object_value=target.id,
        )
        assert len(forward) == 1
        assert len(backward) == 1


# ----------------------------------------------------------------------------
# Cross-cutting : VN payload apres scenario multi-tour multi-mission
# ----------------------------------------------------------------------------


def test_vn_payload_after_multi_turn_session() -> None:
    """Simule plusieurs tours, change d'annee/lieu, verifie le payload final."""
    formatter = DialogueFormatter()
    log = DialogueLog()
    canon = _make_canon_minimal()
    client = MagicMock()
    retriever = MagicMock()
    narrator = Narrator(
        client, canon, retriever,
        enable_anti_hallu_validation=False,
        dialogue_formatter=formatter,
        dialogue_log=log,
    )

    # Scene 1 : an 12, konohagakure, mission_wave
    r1 = NarrationResponse(
        narrative="Le pont fume. Naruto serre les poings.",
        npc_dialogue=[{"character_id": "uzumaki_naruto", "line": "On rentre."}],
        proposed_actions=[], world_observations=[], clarification_request=None,
    )
    q1 = NarrationRequest(
        turn_summary="", action_text="", action_result_summary="",
        location_id="wave_country", present_npcs=["uzumaki_naruto"],
        active_breadcrumb_descriptions=[],
        character_state_summary="", duration_str="",
        in_game_year=12, in_game_date="07-01", turn_number=10,
        related_mission_id="mission_wave_country_zabuza",
    )
    narrator._capture_dialogues(r1, q1)

    # Scene 2 : an 13, training ground, sans mission
    r2 = NarrationResponse(
        narrative="Sasuke s'entraine au lance-shuriken.",
        npc_dialogue=[{"character_id": "uchiha_sasuke", "line": "Je deviendrai plus fort."}],
        proposed_actions=[], world_observations=[], clarification_request=None,
    )
    q2 = NarrationRequest(
        turn_summary="", action_text="", action_result_summary="",
        location_id="konoha_training_ground", present_npcs=["uchiha_sasuke"],
        active_breadcrumb_descriptions=[],
        character_state_summary="", duration_str="",
        in_game_year=13, in_game_date="01-15", turn_number=20,
        scene_mood="serious",
    )
    narrator._capture_dialogues(r2, q2)

    # Verification du payload
    payload = export_to_vn_payload(log.all())
    assert payload["version"] == 1
    meta = payload["in_game_metadata"]
    assert meta["year_min"] == 12
    assert meta["year_max"] == 13
    assert meta["turn_min"] == 10
    assert meta["turn_max"] == 20
    assert meta["total_lines"] == len(log.all())
    # Au moins 2 scenes (year/location different)
    assert len(payload["scenes"]) >= 2
    # speakers_index doit contenir au moins les 2 NPCs + narrator
    assert "uzumaki_naruto" in payload["speakers_index"]
    assert "uchiha_sasuke" in payload["speakers_index"]


def test_vn_payload_jsonl_roundtrip(tmp_path: Path) -> None:
    """Persiste le log en JSONL, recharge, verifie que les lignes survivent."""
    formatter = DialogueFormatter()
    log = DialogueLog()
    lines = formatter.format(
        narrative="Le ciel tonne.",
        npc_dialogue=[{"character_id": "uzumaki_naruto", "line": "Allons-y."}],
        in_game_year=12, in_game_date="01-01",
        location_id="konohagakure", turn_number=1,
    )
    log.append_many(lines)
    p = tmp_path / "dialogues.jsonl"
    n = log.to_jsonl_file(p)
    assert n == log.size

    reloaded = DialogueLog.from_jsonl_file(p)
    assert reloaded.size == log.size
    assert reloaded.all()[0].speaker_id == log.all()[0].speaker_id


# ----------------------------------------------------------------------------
# Phase C invariants compatibles avec missions
# ----------------------------------------------------------------------------


def test_phase_c_compatible_with_missions_in_kg() -> None:
    """Avec missions importees, la table kg_facts contient leurs facts a cote
    des facts canon. Une requete year-filter renvoie les missions de l'annee."""
    catalog_path = Path("data/canonical/missions.json")
    if not catalog_path.exists():
        pytest.skip("missions.json absent")
    catalog = MissionCatalog.from_json_file(catalog_path)
    if catalog.count == 0:
        pytest.skip("missions.json vide")

    with KnowledgeGraphStore(":memory:") as store:
        import_missions_to_kg(store, catalog.all(), clear_first=True)

        # Toutes les missions de l'an 12 doivent apparaitre via filtre temporel
        year_12_missions = [m for m in catalog.all() if m.year == 12]
        if not year_12_missions:
            pytest.skip("Aucune mission canon en l'an 12")
        for m in year_12_missions:
            facts = store.get_facts(subject=m.id, relation="occurs_in_year")
            assert any(f.object == "12" for f in facts), (
                f"Mission {m.id} : occurs_in_year=12 manquant"
            )
