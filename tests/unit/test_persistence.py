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


# --- Phase 4.6 : CRUD complet --------------------------------------------


def test_list_saves_returns_created_save(isolated_saves_dir) -> None:
    """list_saves retourne les saves crees."""
    character = _make_character()
    world = create_default_world(profile=CanonicityProfile.default(), starting_year=12)
    sid = save_module.create_save(character, world)
    items = save_module.list_saves()
    assert len(items) == 1
    assert items[0].save_id == sid
    assert items[0].character_name == character.name


def test_create_save_uniqueness_by_timestamp(isolated_saves_dir) -> None:
    """create_save sur le meme character produit 2 save_ids differents (timestamp)."""
    import time

    character = _make_character()
    world = create_default_world(profile=CanonicityProfile.default(), starting_year=12)
    sid1 = save_module.create_save(character, world)
    time.sleep(1.1)  # garantir timestamp different (granularite seconde)
    sid2 = save_module.create_save(character, world)
    assert sid1 != sid2


def test_delete_save_removes_directory(isolated_saves_dir) -> None:
    """delete_save efface tout le dossier."""
    character = _make_character()
    world = create_default_world(profile=CanonicityProfile.default(), starting_year=12)
    sid = save_module.create_save(character, world)
    save_dir = isolated_saves_dir / sid
    assert save_dir.exists()
    save_module.delete_save(sid)
    assert not save_dir.exists()


def test_delete_save_unknown_raises(isolated_saves_dir) -> None:
    """delete_save sur save inexistante leve SaveNotFoundError."""
    from shinobi.errors import SaveNotFoundError

    with pytest.raises(SaveNotFoundError):
        save_module.delete_save("non_existent_save_id_xyz")


def test_load_save_unknown_raises(isolated_saves_dir) -> None:
    """load_save sur save inexistante leve SaveNotFoundError."""
    from shinobi.errors import SaveNotFoundError

    with pytest.raises(SaveNotFoundError):
        save_module.load_save("non_existent_save_id_xyz")


def test_duplicate_save_creates_independent_copy(isolated_saves_dir) -> None:
    """duplicate_save copie tout le state, modifs ulterieures isolees."""
    character = _make_character()
    world = create_default_world(profile=CanonicityProfile.default(), starting_year=12)
    sid_orig = save_module.create_save(character, world)
    sid_dup = save_module.duplicate_save(sid_orig, "backup_test")
    assert sid_dup != sid_orig
    # Les 2 saves existent independamment
    items = save_module.list_saves()
    assert {s.save_id for s in items} == {sid_orig, sid_dup}
    # Suppression de l'orig n'affecte pas dup
    save_module.delete_save(sid_orig)
    items_after = save_module.list_saves()
    assert {s.save_id for s in items_after} == {sid_dup}


def test_duplicate_save_unknown_raises(isolated_saves_dir) -> None:
    """duplicate_save sur save inexistante leve SaveNotFoundError."""
    from shinobi.errors import SaveNotFoundError

    with pytest.raises(SaveNotFoundError):
        save_module.duplicate_save("non_existent", "label")


def test_save_passive_state_updates_meta(isolated_saves_dir) -> None:
    """save_passive_state persiste les changements et met a jour meta."""
    character = _make_character()
    world = create_default_world(profile=CanonicityProfile.default(), starting_year=12)
    sid = save_module.create_save(character, world)

    # Mute le world : year 13
    new_world = world.model_copy(update={"current_year": 13})
    save_module.save_passive_state(
        sid, new_character=character, new_world=new_world,
        turn_number=10, seed_state=0,
    )
    _, loaded_world, meta = save_module.load_save(sid)
    assert loaded_world.current_year == 13
    assert meta.current_year == 13
    assert meta.total_turns == 10


# --- Phase 4.7 : 50 turns roundtrip integration --------------------------


def test_save_50_turns_then_reload_preserves_state(isolated_saves_dir) -> None:
    """Spec 4.7 : sauvegarder 50 tours, recharger, comparer etat.

    Verifie que :
    - 50 saves successifs ne corrompent pas la base
    - Reload retourne le state final
    - meta.total_turns reflete les 50 tours
    """
    character = _make_character()
    world = create_default_world(
        profile=CanonicityProfile.default(), starting_year=12,
    )
    sid = save_module.create_save(character, world)
    for turn in range(1, 51):
        new_world = world.model_copy(update={
            "current_year": 12 + (turn // 12),
        })
        save_module.save_passive_state(
            sid, new_character=character, new_world=new_world,
            turn_number=turn, seed_state=0,
        )
    # Reload final
    loaded_char, loaded_world, meta = save_module.load_save(sid)
    assert meta.total_turns == 50
    assert loaded_char.name == character.name
    # year a evolue selon la formule turn // 12
    assert loaded_world.current_year == 12 + (50 // 12)


# --- Phase 4.9 : export / import roundtrip identite ----------------------


def test_export_then_import_preserves_save_identity(isolated_saves_dir, tmp_path) -> None:
    """Spec 4.9 : export -> delete -> import -> verifier identite."""
    character = _make_character()
    world = create_default_world(
        profile=CanonicityProfile.default(), starting_year=12,
    )
    sid = save_module.create_save(character, world)
    # Mute pour avoir du contenu non-trivial
    new_world = world.model_copy(update={"current_year": 14})
    save_module.save_passive_state(
        sid, new_character=character, new_world=new_world,
        turn_number=5, seed_state=0,
    )
    # Snapshot avant export
    _, world_before, meta_before = save_module.load_save(sid)

    # Export
    archive = save_module.export_save(sid, tmp_path / "test_export")
    assert archive.exists()
    assert archive.suffix == ".shinosave"

    # Delete
    save_module.delete_save(sid)
    assert sid not in {s.save_id for s in save_module.list_saves()}

    # Import
    sid_imported = save_module.import_save(archive)
    assert sid_imported == sid  # save_id preserve

    # Reload
    char_after, world_after, meta_after = save_module.load_save(sid_imported)
    assert char_after.name == character.name
    assert world_after.current_year == world_before.current_year
    assert meta_after.total_turns == meta_before.total_turns
    assert meta_after.character_name == meta_before.character_name


def test_import_save_rejects_collision(isolated_saves_dir, tmp_path) -> None:
    """import_save sur save_id deja present leve SaveCorruptError."""
    from shinobi.errors import SaveCorruptError

    character = _make_character()
    world = create_default_world(
        profile=CanonicityProfile.default(), starting_year=12,
    )
    sid = save_module.create_save(character, world)
    archive = save_module.export_save(sid, tmp_path / "exp")
    # save existe encore : import doit echouer
    with pytest.raises(SaveCorruptError):
        save_module.import_save(archive)


# --- Phase 4.2 : Alembic migrations roundtrip ----------------------------


def test_alembic_stamps_new_save_at_head(isolated_saves_dir) -> None:
    """Phase 4.2 : open_connection stamp HEAD pour nouvelle save."""
    from shinobi.persistence.migrations_helper import current_revision

    character = _make_character()
    world = create_default_world(
        profile=CanonicityProfile.default(), starting_year=12,
    )
    sid = save_module.create_save(character, world)
    state_path = save_module._state_path(sid)
    rev = current_revision(state_path=state_path)
    assert rev is not None
    assert rev == "0001_initial_schema"


def test_alembic_idempotent_upgrade_on_existing_save(isolated_saves_dir) -> None:
    """Phase 4.2 : upgrade_save sur save deja a HEAD est no-op."""
    from shinobi.persistence.migrations_helper import (
        current_revision,
        upgrade_save,
    )

    character = _make_character()
    world = create_default_world(
        profile=CanonicityProfile.default(), starting_year=12,
    )
    sid = save_module.create_save(character, world)
    state_path = save_module._state_path(sid)
    rev_before = current_revision(state_path=state_path)
    upgrade_save(state_path=state_path, revision="head")
    rev_after = current_revision(state_path=state_path)
    assert rev_before == rev_after
