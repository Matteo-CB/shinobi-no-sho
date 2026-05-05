"""Tests sur la persistance des saves."""

from __future__ import annotations

from pathlib import Path

import pytest

from shinobi.canon.profiles import CanonicityProfile
from shinobi.engine.character import Character
from shinobi.engine.stats import CoreStats, ExtendedStats
from shinobi.engine.world import create_default_world
from shinobi.persistence import saves as save_module
from shinobi.types import Gender


@pytest.fixture()
def isolated_saves_dir(tmp_path: Path, monkeypatch):
    """Isole les saves dans un repertoire temporaire."""
    from shinobi.config import settings

    monkeypatch.setattr(settings, "saves_path", str(tmp_path))
    # Patch property
    monkeypatch.setattr(type(settings), "saves_dir", property(lambda self: tmp_path))
    return tmp_path


def _make_character() -> Character:
    return Character(
        id="test_id",
        name="Test Save Character",
        gender=Gender.female,
        birth_year=5,
        birth_date="06-15",
        age_years=12,
        village_of_origin="konohagakure",
        current_village="konohagakure",
        current_location="konohagakure",
        rank="genin",
        stats=CoreStats(),
        extended_stats=ExtendedStats(),
    )


def test_create_and_load_save(isolated_saves_dir) -> None:
    character = _make_character()
    world = create_default_world(profile=CanonicityProfile.default(), starting_year=12)
    sid = save_module.create_save(character, world)
    loaded_char, loaded_world, meta = save_module.load_save(sid)
    assert loaded_char.name == character.name
    assert loaded_world.current_year == 12
    assert meta.character_name == character.name
    save_module.delete_save(sid)


def test_list_saves_empty(isolated_saves_dir) -> None:
    items = save_module.list_saves()
    assert items == []
