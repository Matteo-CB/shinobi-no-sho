"""Export du DialogueLog au format JSON canonique pour application VN.

Le format genere est independant de Pydantic interne : on produit un payload
plat, idempotent, versione, importable par n'importe quel runtime VN
(Ren'Py, custom, web, etc.).

Schema VN_PAYLOAD_VERSION_1 :
{
  "version": 1,
  "exported_at_ts": float,
  "in_game_metadata": {
      "year_min": int|null, "year_max": int|null,
      "turn_min": int|null, "turn_max": int|null,
      "speakers": list[str],
  },
  "speakers_index": {
      "<speaker_id>": {
          "name_display": str,        // alias humain si dispo
          "voice_profile_id": str|null,
          "is_canon_npc": bool,
          "is_player": bool,
          "is_narrator": bool,
      }
  },
  "scenes": [                          // grouping logique
      {
          "id": str,
          "year": int|null,
          "date": str|null,
          "location_id": str|null,
          "mood": str|null,
          "lines": [DialogueLineDict, ...]
      }
  ],
  "raw_lines": [DialogueLineDict, ...] // chronologique brut, alternative
}
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from shinobi.dialogue.types import DialogueLine

VN_PAYLOAD_VERSION = 1


@dataclass(frozen=True)
class VNExportConfig:
    """Parametres d'export."""

    group_into_scenes: bool = True
    include_thoughts: bool = True
    include_system_lines: bool = True
    speaker_display_name_resolver: Any = None  # Callable[[str], str] ou None


def _line_to_dict(line: DialogueLine) -> dict[str, Any]:
    """Serialise une DialogueLine au format VN payload."""
    return {
        "id": line.id,
        "speaker_id": line.speaker_id,
        "text": line.text,
        "emotion": line.emotion.value,
        "expression": line.expression.value,
        "tone": line.tone.value,
        "is_thought": line.is_thought,
        "in_game_year": line.in_game_year,
        "in_game_date": line.in_game_date,
        "location_id": line.location_id,
        "scene_mood": line.scene_mood,
        "turn_number": line.turn_number,
        "related_event_id": line.related_event_id,
        "related_mission_id": line.related_mission_id,
        "addressed_to_id": line.addressed_to_id,
        "voice_profile_id": line.voice_profile_id,
        "stage_directions": line.stage_directions,
        "real_time_ts": line.real_time_ts,
    }


def _scene_key(line: DialogueLine) -> tuple:
    """Cle de regroupement de scene : (year, date, location, mood)."""
    return (
        line.in_game_year,
        line.in_game_date,
        line.location_id,
        line.scene_mood,
    )


def _resolve_display_name(
    speaker_id: str,
    resolver,
) -> str:
    """Resout un id en nom affichable. Si resolver fourni, l'utilise."""
    if resolver is not None:
        try:
            return resolver(speaker_id) or speaker_id
        except Exception:
            return speaker_id
    # Fallback : id verbatim
    if speaker_id == "narrator":
        return "Narrateur"
    if speaker_id == "player":
        return "Joueur"
    if speaker_id == "system":
        return "Systeme"
    return speaker_id


def _build_speakers_index(
    lines: list[DialogueLine], resolver,
) -> dict[str, dict[str, Any]]:
    """Index des speakers pour facilite l'app VN (charger sprites, voix, etc.)."""
    out: dict[str, dict[str, Any]] = {}
    for line in lines:
        sid = line.speaker_id
        if sid in out:
            continue
        out[sid] = {
            "name_display": _resolve_display_name(sid, resolver),
            "voice_profile_id": line.voice_profile_id,
            "is_canon_npc": line.is_canon_npc(),
            "is_player": line.is_player(),
            "is_narrator": line.is_narrator(),
        }
    return out


def _group_scenes(
    lines: list[DialogueLine],
) -> list[dict[str, Any]]:
    """Regroupe les lines en scenes par (year, date, location, mood)."""
    if not lines:
        return []
    scenes: list[dict[str, Any]] = []
    current_key: tuple | None = None
    current_lines: list[dict[str, Any]] = []
    scene_idx = 0
    for line in lines:
        key = _scene_key(line)
        if current_key is None or key != current_key:
            # Flush previous
            if current_lines:
                scenes.append(_make_scene(current_key, current_lines, scene_idx))
                scene_idx += 1
            current_key = key
            current_lines = []
        current_lines.append(_line_to_dict(line))
    # Flush trailing
    if current_lines:
        scenes.append(_make_scene(current_key, current_lines, scene_idx))
    return scenes


def _make_scene(
    key: tuple,
    lines_dicts: list[dict[str, Any]],
    idx: int,
) -> dict[str, Any]:
    year, date, location, mood = key if key else (None, None, None, None)
    return {
        "id": f"scene_{idx:04d}",
        "year": year,
        "date": date,
        "location_id": location,
        "mood": mood,
        "lines": lines_dicts,
    }


def export_to_vn_payload(
    log_or_lines: Iterable[DialogueLine],
    *,
    config: VNExportConfig | None = None,
) -> dict[str, Any]:
    """Convertit un DialogueLog (ou n'importe quel iterable de DialogueLine)
    en payload VN canonique. Pas d'I/O disque."""
    cfg = config or VNExportConfig()
    all_lines = list(log_or_lines)

    # Filtrage
    filtered: list[DialogueLine] = []
    for line in all_lines:
        if not cfg.include_thoughts and line.is_thought:
            continue
        if not cfg.include_system_lines and line.is_system():
            continue
        filtered.append(line)

    # Metadata aggreges
    years = [line.in_game_year for line in filtered if line.in_game_year is not None]
    turns = [line.turn_number for line in filtered if line.turn_number is not None]
    speakers = sorted({line.speaker_id for line in filtered})

    payload: dict[str, Any] = {
        "version": VN_PAYLOAD_VERSION,
        "exported_at_ts": datetime.now(UTC).timestamp(),
        "in_game_metadata": {
            "year_min": min(years) if years else None,
            "year_max": max(years) if years else None,
            "turn_min": min(turns) if turns else None,
            "turn_max": max(turns) if turns else None,
            "speakers": speakers,
            "total_lines": len(filtered),
        },
        "speakers_index": _build_speakers_index(filtered, cfg.speaker_display_name_resolver),
        "raw_lines": [_line_to_dict(line) for line in filtered],
    }
    if cfg.group_into_scenes:
        payload["scenes"] = _group_scenes(filtered)
    else:
        payload["scenes"] = []
    return payload


def export_to_vn_json(
    log_or_lines: Iterable[DialogueLine],
    path: Path | str,
    *,
    config: VNExportConfig | None = None,
    indent: int = 2,
) -> int:
    """Ecrit le payload VN sur disque. Retourne le nombre de lignes exportees."""
    payload = export_to_vn_payload(log_or_lines, config=config)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(payload, ensure_ascii=False, indent=indent),
        encoding="utf-8",
    )
    return int(payload["in_game_metadata"]["total_lines"])


__all__ = [
    "VN_PAYLOAD_VERSION",
    "VNExportConfig",
    "export_to_vn_json",
    "export_to_vn_payload",
]
