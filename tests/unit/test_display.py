"""Tests sur le formatage des dialogues."""

from __future__ import annotations

import pytest

from shinobi.canon.loader import load_canon
from shinobi.cli.display import format_speaker


@pytest.fixture(scope="module")
def canon():
    return load_canon(
        optional=(
            "organizations", "tailed_beasts", "kekkei_mora", "hiden",
            "timeline_events", "voice_profiles",
        )
    )


def test_format_unknown_npc_uses_role(canon) -> None:
    label = format_speaker(canon, "marchand_taverne")
    assert "Marchand Taverne" in label


def test_format_known_npc_includes_clan(canon) -> None:
    if "uchiha_itachi" not in canon.characters:
        pytest.skip("uchiha_itachi pas dans le dataset")
    label = format_speaker(canon, "uchiha_itachi")
    assert "Itachi" in label or "uchiha" in label.lower()


def test_format_handles_missing_id_gracefully(canon) -> None:
    label = format_speaker(canon, "garde_porte_konoha")
    assert "Garde" in label
