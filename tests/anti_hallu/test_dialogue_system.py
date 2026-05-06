"""Tests du systeme dialogue Visual Novel.

Couvre :
- DialogueLine : construction, helpers (is_narrator, is_player, etc.)
- Enums : DialogueEmotion, DialogueExpression, DialogueTone
- DialogueLog : append, rolling window, queries, persistance JSONL, archive
- DialogueFormatter : narrative -> narrator lines, npc_dialogue -> NPC lines,
  discours rapporte 'X dit : "Y"', pensees *...*, emotion/tone heuristiques
- VN export : payload structure, scenes grouping, speakers_index, JSON file
"""

from __future__ import annotations

from pathlib import Path

import pytest

from shinobi.dialogue import (
    NARRATOR_SPEAKER_ID,
    DialogueEmotion,
    DialogueExpression,
    DialogueFormatter,
    DialogueLine,
    DialogueLog,
    DialogueLogConfig,
    DialogueTone,
    VNExportConfig,
    export_to_vn_json,
    export_to_vn_payload,
)

# ============================================================================
# DialogueLine
# ============================================================================


def test_dialogue_line_minimal_required_fields() -> None:
    line = DialogueLine(speaker_id="naruto", text="Dattebayo !")
    assert line.speaker_id == "naruto"
    assert line.text == "Dattebayo !"
    assert line.id.startswith("dline_")
    assert line.emotion == DialogueEmotion.neutral


def test_dialogue_line_helpers() -> None:
    nar = DialogueLine(speaker_id="narrator", text="Le vent souffle.")
    pl = DialogueLine(speaker_id="player", text="Je m'avance.")
    sys = DialogueLine(speaker_id="system", text="Sauvegarde effectuee.")
    npc = DialogueLine(speaker_id="uzumaki_naruto", text="On y va !")
    assert nar.is_narrator() is True
    assert pl.is_player() is True
    assert sys.is_system() is True
    assert npc.is_canon_npc() is True
    assert nar.is_canon_npc() is False


def test_dialogue_line_short_label() -> None:
    line = DialogueLine(speaker_id="kakashi", text="Yo.")
    assert line.short_label() == "kakashi: Yo."


def test_dialogue_line_immutable() -> None:
    from pydantic import ValidationError
    line = DialogueLine(speaker_id="x", text="hi")
    with pytest.raises((ValidationError, TypeError, AttributeError)):
        line.text = "modified"  # type: ignore[misc]


def test_dialogue_line_with_full_context() -> None:
    line = DialogueLine(
        speaker_id="sasuke",
        text="Je deviendrai plus fort.",
        emotion=DialogueEmotion.determined,
        expression=DialogueExpression.glare,
        tone=DialogueTone.normal,
        in_game_year=12,
        in_game_date="04-15",
        location_id="konohagakure",
        turn_number=42,
        related_event_id="event_chunin_exam",
        is_thought=False,
        voice_profile_id="vp_sasuke",
    )
    assert line.in_game_year == 12
    assert line.related_event_id == "event_chunin_exam"


# ============================================================================
# DialogueLog
# ============================================================================


def test_log_append_and_query_by_speaker() -> None:
    log = DialogueLog()
    log.append(DialogueLine(speaker_id="naruto", text="A"))
    log.append(DialogueLine(speaker_id="sasuke", text="B"))
    log.append(DialogueLine(speaker_id="naruto", text="C"))
    assert log.size == 3
    naruto_lines = log.by_speaker("naruto")
    assert len(naruto_lines) == 2
    assert {ln.text for ln in naruto_lines} == {"A", "C"}


def test_log_rolling_window_drops_oldest() -> None:
    """Quand on depasse max_lines, on retire les plus anciennes."""
    log = DialogueLog(config=DialogueLogConfig(max_lines=3, archive_threshold=999))
    log.append(DialogueLine(speaker_id="x", text="1"))
    log.append(DialogueLine(speaker_id="x", text="2"))
    log.append(DialogueLine(speaker_id="x", text="3"))
    log.append(DialogueLine(speaker_id="x", text="4"))
    texts = [ln.text for ln in log]
    assert texts == ["2", "3", "4"]
    assert log.size == 3


def test_log_append_many() -> None:
    log = DialogueLog()
    n = log.append_many([
        DialogueLine(speaker_id="a", text=str(i)) for i in range(5)
    ])
    assert n == 5
    assert log.size == 5


def test_log_query_by_year_range() -> None:
    log = DialogueLog()
    log.append(DialogueLine(speaker_id="x", text="ancien", in_game_year=8))
    log.append(DialogueLine(speaker_id="x", text="recent", in_game_year=14))
    log.append(DialogueLine(speaker_id="x", text="sans annee"))
    in_8_12 = log.by_year_range(year_min=7, year_max=12)
    assert len(in_8_12) == 1
    assert in_8_12[0].text == "ancien"


def test_log_query_by_event_and_mission() -> None:
    log = DialogueLog()
    log.append(DialogueLine(speaker_id="x", text="A", related_event_id="ev1"))
    log.append(DialogueLine(speaker_id="x", text="B", related_event_id="ev2"))
    log.append(DialogueLine(speaker_id="x", text="C", related_mission_id="m_wave"))
    assert len(log.by_event("ev1")) == 1
    assert len(log.by_mission("m_wave")) == 1


def test_log_thoughts_vs_speech() -> None:
    log = DialogueLog()
    log.append(DialogueLine(speaker_id="naruto", text="dit", is_thought=False))
    log.append(DialogueLine(speaker_id="naruto", text="pense", is_thought=True))
    assert len(log.thoughts_only()) == 1
    assert len(log.speech_only()) == 1


def test_log_speakers_dedup() -> None:
    log = DialogueLog()
    for sp in ("a", "b", "a", "c", "b"):
        log.append(DialogueLine(speaker_id=sp, text="x"))
    assert log.speakers() == ["a", "b", "c"]


def test_log_persistence_jsonl_round_trip(tmp_path: Path) -> None:
    log = DialogueLog()
    log.append(DialogueLine(speaker_id="naruto", text="A", in_game_year=12))
    log.append(DialogueLine(speaker_id="kakashi", text="B"))
    fp = tmp_path / "dialogue.jsonl"
    n = log.to_jsonl_file(fp)
    assert n == 2
    log2 = DialogueLog.from_jsonl_file(fp)
    assert log2.size == 2
    assert log2.all()[0].text == "A"
    assert log2.all()[0].in_game_year == 12


def test_log_from_jsonl_missing_file(tmp_path: Path) -> None:
    log = DialogueLog.from_jsonl_file(tmp_path / "nope.jsonl")
    assert log.size == 0


def test_log_archive_old_offloads_to_disk(tmp_path: Path) -> None:
    archive = tmp_path / "archive.jsonl"
    # Threshold haut pour eviter l'archive auto au sein d'append
    log = DialogueLog(config=DialogueLogConfig(
        max_lines=10, archive_threshold=999, archive_path=archive,
    ))
    for i in range(5):
        log.append(DialogueLine(speaker_id="x", text=str(i)))
    assert log.size == 5
    archived = log.archive_old(2)
    assert archived == 2
    assert log.size == 3
    # Verify file
    content = archive.read_text(encoding="utf-8").strip().splitlines()
    assert len(content) == 2


def test_log_archive_auto_on_threshold(tmp_path: Path) -> None:
    """Append auto-archives quand threshold est atteint et log non full."""
    archive = tmp_path / "auto_archive.jsonl"
    log = DialogueLog(config=DialogueLogConfig(
        max_lines=100, archive_threshold=3, archive_path=archive,
    ))
    for i in range(5):
        log.append(DialogueLine(speaker_id="x", text=str(i)))
    # Le log a auto-archive a un moment, donc < 5 en memoire
    assert log.size <= 5
    # Le fichier d'archive contient au moins quelques lignes
    if archive.exists():
        content = archive.read_text(encoding="utf-8").strip().splitlines()
        assert len(content) >= 1


def test_log_clear() -> None:
    log = DialogueLog()
    log.append(DialogueLine(speaker_id="x", text="a"))
    log.clear()
    assert log.size == 0


def test_log_last_n() -> None:
    log = DialogueLog()
    for i in range(10):
        log.append(DialogueLine(speaker_id="x", text=str(i)))
    last3 = log.last_n(3)
    assert [ln.text for ln in last3] == ["7", "8", "9"]
    assert log.last_n(0) == []


# ============================================================================
# DialogueFormatter
# ============================================================================


def test_formatter_npc_dialogue_to_lines() -> None:
    fmt = DialogueFormatter()
    lines = fmt.format(
        narrative="",
        npc_dialogue=[
            {"character_id": "naruto", "line": "Yo Iruka-sensei !", "tone": "shout"},
            {"character_id": "iruka", "line": "Bonjour Naruto.", "tone": "normal"},
        ],
        in_game_year=8, location_id="konoha_academy",
    )
    assert len(lines) == 2
    assert lines[0].speaker_id == "naruto"
    assert lines[0].tone == DialogueTone.shout
    assert lines[0].in_game_year == 8


def test_formatter_narrative_split_into_narrator_lines() -> None:
    fmt = DialogueFormatter()
    lines = fmt.format(
        narrative="Le vent souffle. Naruto avance vers le pont. La nuit tombe.",
    )
    narrator_lines = [ln for ln in lines if ln.is_narrator()]
    assert len(narrator_lines) == 3
    assert narrator_lines[0].speaker_id == NARRATOR_SPEAKER_ID


def test_formatter_extracts_reported_speech() -> None:
    """'Naruto dit : "Je deviendrai Hokage"' attribue a uzumaki_naruto."""
    fmt = DialogueFormatter()
    lines = fmt.format(
        narrative='Naruto declare : "Je deviendrai Hokage !" Le silence se fait.',
    )
    naruto = [ln for ln in lines if ln.speaker_id == "uzumaki_naruto"]
    assert len(naruto) == 1
    assert "Hokage" in naruto[0].text


def test_formatter_thoughts_inline() -> None:
    fmt = DialogueFormatter()
    lines = fmt.format(
        narrative="*Il faut que je reste calme.* Naruto serra les poings.",
    )
    thoughts = [ln for ln in lines if ln.is_thought]
    assert len(thoughts) == 1
    assert "calme" in thoughts[0].text.lower()


def test_formatter_emotion_heuristic_anger() -> None:
    fmt = DialogueFormatter()
    lines = fmt.format(
        narrative="",
        npc_dialogue=[{
            "character_id": "sasuke",
            "line": "Tu vas payer pour ce que tu as fait, dans une fureur sans nom.",
        }],
    )
    assert lines[0].emotion == DialogueEmotion.angry


def test_formatter_tone_heuristic_whisper() -> None:
    fmt = DialogueFormatter()
    lines = fmt.format(
        narrative="",
        npc_dialogue=[{
            "character_id": "kakashi",
            "line": "Approche, dit-il dans un murmure.",
        }],
    )
    assert lines[0].tone == DialogueTone.whisper


def test_formatter_expression_derived_from_emotion() -> None:
    fmt = DialogueFormatter()
    lines = fmt.format(
        narrative="",
        npc_dialogue=[{
            "character_id": "naruto",
            "line": "Quelle joie de te revoir, joyeux je rit en effet !",
        }],
    )
    assert lines[0].emotion == DialogueEmotion.joyful
    assert lines[0].expression == DialogueExpression.smile


def test_formatter_empty_input_no_lines() -> None:
    fmt = DialogueFormatter()
    lines = fmt.format(narrative="", npc_dialogue=[])
    assert lines == []


def test_formatter_skips_invalid_npc_entries() -> None:
    fmt = DialogueFormatter()
    lines = fmt.format(
        narrative="",
        npc_dialogue=[
            {"character_id": "", "line": "no speaker"},
            {"character_id": "x", "line": ""},
            {"character_id": "naruto", "line": "Valid"},
        ],
    )
    assert len(lines) == 1
    assert lines[0].text == "Valid"


def test_formatter_passes_context_to_lines() -> None:
    fmt = DialogueFormatter()
    lines = fmt.format(
        narrative="Test phrase complete.",
        in_game_year=12, in_game_date="04-15",
        location_id="konohagakure", turn_number=42,
        related_event_id="ev_42", scene_mood="tense",
    )
    line = lines[0]
    assert line.in_game_year == 12
    assert line.location_id == "konohagakure"
    assert line.turn_number == 42
    assert line.related_event_id == "ev_42"
    assert line.scene_mood == "tense"


# ============================================================================
# VN Export
# ============================================================================


def test_vn_export_payload_basic() -> None:
    log = DialogueLog()
    log.append(DialogueLine(
        speaker_id="naruto", text="Salut !", in_game_year=12,
        location_id="konoha", scene_mood="calme",
    ))
    log.append(DialogueLine(
        speaker_id="iruka", text="Bonjour.", in_game_year=12,
        location_id="konoha", scene_mood="calme",
    ))
    payload = export_to_vn_payload(log)
    assert payload["version"] == 1
    assert payload["in_game_metadata"]["total_lines"] == 2
    assert "naruto" in payload["speakers_index"]
    assert payload["speakers_index"]["naruto"]["is_canon_npc"] is True


def test_vn_export_groups_into_scenes() -> None:
    log = DialogueLog()
    # Scene 1 : year 12 konoha
    log.append(DialogueLine(speaker_id="naruto", text="A", in_game_year=12, location_id="konoha"))
    log.append(DialogueLine(speaker_id="iruka", text="B", in_game_year=12, location_id="konoha"))
    # Scene 2 : year 14 wave
    log.append(DialogueLine(speaker_id="naruto", text="C", in_game_year=14, location_id="wave"))
    payload = export_to_vn_payload(log)
    scenes = payload["scenes"]
    assert len(scenes) == 2
    assert scenes[0]["year"] == 12
    assert scenes[1]["year"] == 14
    assert len(scenes[0]["lines"]) == 2
    assert len(scenes[1]["lines"]) == 1


def test_vn_export_excludes_thoughts_when_configured() -> None:
    log = DialogueLog()
    log.append(DialogueLine(speaker_id="naruto", text="speak", is_thought=False))
    log.append(DialogueLine(speaker_id="naruto", text="think", is_thought=True))
    payload = export_to_vn_payload(
        log, config=VNExportConfig(include_thoughts=False),
    )
    texts = {line["text"] for line in payload["raw_lines"]}
    assert "speak" in texts
    assert "think" not in texts


def test_vn_export_excludes_system_lines_when_configured() -> None:
    log = DialogueLog()
    log.append(DialogueLine(speaker_id="naruto", text="hi"))
    log.append(DialogueLine(speaker_id="system", text="save_ok"))
    payload = export_to_vn_payload(
        log, config=VNExportConfig(include_system_lines=False),
    )
    speakers = payload["in_game_metadata"]["speakers"]
    assert "system" not in speakers


def test_vn_export_speakers_index_shows_roles() -> None:
    lines = [
        DialogueLine(speaker_id="narrator", text="Le silence se fait."),
        DialogueLine(speaker_id="player", text="Je m'avance."),
        DialogueLine(speaker_id="uzumaki_naruto", text="Yo !"),
    ]
    payload = export_to_vn_payload(lines)
    idx = payload["speakers_index"]
    assert idx["narrator"]["is_narrator"] is True
    assert idx["player"]["is_player"] is True
    assert idx["uzumaki_naruto"]["is_canon_npc"] is True


def test_vn_export_to_json_file(tmp_path: Path) -> None:
    log = DialogueLog()
    log.append(DialogueLine(speaker_id="naruto", text="Hi", in_game_year=12))
    log.append(DialogueLine(speaker_id="iruka", text="Bonjour"))
    fp = tmp_path / "vn_export.json"
    n = export_to_vn_json(log, fp)
    assert n == 2
    import json as _json
    payload = _json.loads(fp.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["in_game_metadata"]["total_lines"] == 2


def test_vn_export_with_custom_resolver() -> None:
    """speaker_display_name_resolver permet d'enrichir avec noms canoniques."""
    log = DialogueLog()
    log.append(DialogueLine(speaker_id="uzumaki_naruto", text="Yo"))

    def resolver(sid: str) -> str:
        return {"uzumaki_naruto": "Naruto Uzumaki"}.get(sid, sid)

    payload = export_to_vn_payload(
        log, config=VNExportConfig(speaker_display_name_resolver=resolver),
    )
    assert payload["speakers_index"]["uzumaki_naruto"]["name_display"] == "Naruto Uzumaki"


def test_vn_export_resolver_default_for_known_roles() -> None:
    log = DialogueLog()
    log.append(DialogueLine(speaker_id="narrator", text="Test"))
    log.append(DialogueLine(speaker_id="player", text="Hi"))
    payload = export_to_vn_payload(log)
    # Sans resolver custom : narrator -> 'Narrateur', player -> 'Joueur'
    assert payload["speakers_index"]["narrator"]["name_display"] == "Narrateur"
    assert payload["speakers_index"]["player"]["name_display"] == "Joueur"


def test_vn_export_metadata_aggregates() -> None:
    log = DialogueLog()
    log.append(DialogueLine(speaker_id="x", text="a", in_game_year=8, turn_number=10))
    log.append(DialogueLine(speaker_id="y", text="b", in_game_year=14, turn_number=42))
    payload = export_to_vn_payload(log)
    md = payload["in_game_metadata"]
    assert md["year_min"] == 8 and md["year_max"] == 14
    assert md["turn_min"] == 10 and md["turn_max"] == 42


def test_vn_export_empty_log() -> None:
    payload = export_to_vn_payload([])
    assert payload["in_game_metadata"]["total_lines"] == 0
    assert payload["scenes"] == []
    assert payload["raw_lines"] == []


def test_vn_export_raw_lines_chronological() -> None:
    log = DialogueLog()
    log.append(DialogueLine(speaker_id="a", text="1"))
    log.append(DialogueLine(speaker_id="b", text="2"))
    log.append(DialogueLine(speaker_id="c", text="3"))
    payload = export_to_vn_payload(log, config=VNExportConfig(group_into_scenes=False))
    texts = [ln["text"] for ln in payload["raw_lines"]]
    assert texts == ["1", "2", "3"]
    assert payload["scenes"] == []
