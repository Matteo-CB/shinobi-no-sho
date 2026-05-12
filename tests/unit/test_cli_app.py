"""Phase 6.1 : tests Typer app.py via CliRunner.

Couvre les sous-commandes : version, list, config, delete, export, import.
Les commandes interactives (play, new, root) sont testees indirectement
via les autres modules CLI.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from shinobi.canon.profiles import CanonicityProfile
from shinobi.cli.app import app
from shinobi.engine.character import Character
from shinobi.engine.stats import CoreStats, ExtendedStats
from shinobi.engine.world import create_default_world
from shinobi.persistence import saves as save_module
from shinobi.types import Gender


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture()
def isolated_saves_dir(tmp_path: Path, monkeypatch):
    """Isole les saves dans un repertoire temporaire."""
    from shinobi.config import settings

    monkeypatch.setattr(settings, "saves_path", str(tmp_path))
    monkeypatch.setattr(
        type(settings), "saves_dir", property(lambda self: tmp_path),
    )
    return tmp_path


def _make_character() -> Character:
    return Character(
        id="test_id",
        name="Test CLI Character",
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


def _create_test_save() -> str:
    char = _make_character()
    world = create_default_world(
        profile=CanonicityProfile.default(), starting_year=12,
    )
    return save_module.create_save(char, world)


# === 6.1 commandes Typer =================================================


def test_version_command_outputs_version(runner: CliRunner) -> None:
    """`shinobi version` affiche le numero de version."""
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "Shinobi no Sho" in result.stdout


def test_config_command_lists_config(runner: CliRunner) -> None:
    """`shinobi config` affiche la config courante."""
    result = runner.invoke(app, ["config"])
    assert result.exit_code == 0
    assert "LLM backend" in result.stdout
    assert "Saves dir" in result.stdout


def test_list_command_empty_saves(runner: CliRunner, isolated_saves_dir) -> None:
    """`shinobi list` sur saves dir vide affiche le message approprie."""
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "No save" in result.stdout


def test_list_command_shows_existing_saves(
    runner: CliRunner, isolated_saves_dir,
) -> None:
    """`shinobi list` affiche les saves crees."""
    sid = _create_test_save()
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert sid in result.stdout
    assert "Test CLI Character" in result.stdout


def test_delete_command_with_confirm(
    runner: CliRunner, isolated_saves_dir,
) -> None:
    """`shinobi delete <id>` apres confirmation supprime la save."""
    sid = _create_test_save()
    # Repond 'y' a la confirmation
    result = runner.invoke(app, ["delete", sid], input="y\n")
    assert result.exit_code == 0
    assert "deleted" in result.stdout.lower()
    assert save_module.list_saves() == []


def test_delete_command_unknown_save_returns_error(
    runner: CliRunner, isolated_saves_dir,
) -> None:
    """`shinobi delete <unknown>` retourne exit_code != 0."""
    result = runner.invoke(app, ["delete", "non_existent_id"], input="y\n")
    assert result.exit_code == 1
    assert "not found" in result.stdout.lower()


def test_delete_command_abort_on_no_confirm(
    runner: CliRunner, isolated_saves_dir,
) -> None:
    """`shinobi delete <id>` sans confirmation n'efface pas."""
    sid = _create_test_save()
    result = runner.invoke(app, ["delete", sid], input="n\n")
    # User abort -> exit code 0 ou 1 selon Typer mais save preserve
    assert sid in {s.save_id for s in save_module.list_saves()}


def test_export_command_creates_archive(
    runner: CliRunner, isolated_saves_dir, tmp_path,
) -> None:
    """`shinobi export <id> <path>` cree une archive .shinosave."""
    sid = _create_test_save()
    out = tmp_path / "exported"
    result = runner.invoke(app, ["export", sid, str(out)])
    assert result.exit_code == 0
    expected = out.with_suffix(".shinosave")
    assert expected.exists()
    assert "exported" in result.stdout.lower()


def test_import_command_restores_save(
    runner: CliRunner, isolated_saves_dir, tmp_path,
) -> None:
    """`shinobi import <archive>` restaure une save exportee."""
    sid = _create_test_save()
    out_path = tmp_path / "round_trip"
    archive = save_module.export_save(sid, out_path)
    save_module.delete_save(sid)
    assert sid not in {s.save_id for s in save_module.list_saves()}

    result = runner.invoke(app, ["import", str(archive)])
    assert result.exit_code == 0
    assert sid in result.stdout


def test_play_command_no_saves_exits_gracefully(
    runner: CliRunner, isolated_saves_dir,
) -> None:
    """`shinobi play` sans save existante quitte avec un message."""
    result = runner.invoke(app, ["play"])
    assert result.exit_code == 0
    assert "No save" in result.stdout


def test_app_help_lists_commands(runner: CliRunner) -> None:
    """`shinobi --help` liste les sous-commandes."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    # Toutes les commandes principales doivent apparaitre dans l'aide
    for cmd in ("version", "config", "list", "delete", "export", "import", "play", "new", "serve"):
        assert cmd in result.stdout


def test_serve_command_help_lists_options(runner: CliRunner) -> None:
    """`shinobi serve --help` documente host, port, reload, log-level (Phase 9)."""
    result = runner.invoke(app, ["serve", "--help"])
    assert result.exit_code == 0
    for opt in ("--host", "--port", "--reload", "--log-level"):
        assert opt in result.stdout
