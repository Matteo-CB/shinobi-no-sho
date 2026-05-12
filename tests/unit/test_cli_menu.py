"""Phase 6.2 : tests menu.py.

Couvre _menu_iteration, _pick_save, _start_play (mocked).
Le main_loop infini n'est pas testable directement, mais ses iterations
le sont.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from shinobi.canon.profiles import CanonicityProfile
from shinobi.cli import menu as menu_module
from shinobi.engine.character import Character
from shinobi.engine.stats import CoreStats, ExtendedStats
from shinobi.engine.world import create_default_world
from shinobi.persistence import saves as save_module
from shinobi.types import Gender


@pytest.fixture()
def isolated_saves_dir(tmp_path: Path, monkeypatch):
    from shinobi.config import settings
    monkeypatch.setattr(settings, "saves_path", str(tmp_path))
    monkeypatch.setattr(
        type(settings), "saves_dir", property(lambda self: tmp_path),
    )
    return tmp_path


def _make_character() -> Character:
    return Character(
        id="test_id", name="Menu Test", gender=Gender.female,
        birth_year=5, birth_date="06-15", age_years=12,
        village_of_origin="konohagakure",
        current_village="konohagakure",
        current_location="konohagakure",
        rank="genin",
        stats=CoreStats(), extended_stats=ExtendedStats(),
    )


def _create_test_save() -> str:
    char = _make_character()
    world = create_default_world(
        profile=CanonicityProfile.default(), starting_year=12,
    )
    return save_module.create_save(char, world)


# === 6.2 menu.py =========================================================


def test_menu_iteration_quit_returns_false(
    isolated_saves_dir, monkeypatch,
) -> None:
    """L'iteration menu retourne False quand l'utilisateur tape 'q'."""
    monkeypatch.setattr(
        "shinobi.cli.menu.Prompt.ask",
        lambda *args, **kwargs: "q",
    )
    assert menu_module._menu_iteration() is False


def test_menu_iteration_quit_aliases(isolated_saves_dir, monkeypatch) -> None:
    """Aliases 'quit', 'quitter', 'exit' fonctionnent."""
    for alias in ("quit", "quitter", "exit", "Q", "Quit"):
        monkeypatch.setattr(
            "shinobi.cli.menu.Prompt.ask",
            lambda *a, **k: alias,
        )
        assert menu_module._menu_iteration() is False


def test_menu_iteration_invalid_choice_continues(
    isolated_saves_dir, monkeypatch, capsys,
) -> None:
    """Choix invalide renvoie True (continue boucle) avec warning."""
    monkeypatch.setattr(
        "shinobi.cli.menu.Prompt.ask", lambda *a, **k: "999",
    )
    assert menu_module._menu_iteration() is True


def test_menu_iteration_continue_no_saves_warns(
    isolated_saves_dir, monkeypatch,
) -> None:
    """Choix 2 sans aucune save : warn 'Aucune partie a continuer'."""
    monkeypatch.setattr(
        "shinobi.cli.menu.Prompt.ask", lambda *a, **k: "2",
    )
    # Aucun save -> doit retourner True (continue) sans crash
    assert menu_module._menu_iteration() is True


def test_menu_iteration_choice_2_with_save_calls_play(
    isolated_saves_dir, monkeypatch,
) -> None:
    """Choix 2 avec save -> _start_play(save_id)."""
    sid = _create_test_save()
    monkeypatch.setattr(
        "shinobi.cli.menu.Prompt.ask", lambda *a, **k: "2",
    )
    called = {"sid": None}

    def fake_start(save_id: str):
        called["sid"] = save_id

    monkeypatch.setattr("shinobi.cli.menu._start_play", fake_start)
    assert menu_module._menu_iteration() is True
    assert called["sid"] == sid


def test_menu_iteration_choice_4_calls_config(
    isolated_saves_dir, monkeypatch,
) -> None:
    """Choix 4 -> config_cmd."""
    monkeypatch.setattr(
        "shinobi.cli.menu.Prompt.ask", lambda *a, **k: "4",
    )
    called = {"v": False}

    def fake_config():
        called["v"] = True

    # Patch the import target ; menu imports lazily
    import shinobi.cli.app as app_mod
    monkeypatch.setattr(app_mod, "config_cmd", fake_config)
    assert menu_module._menu_iteration() is True
    assert called["v"]


def test_pick_save_returns_id_by_index(
    isolated_saves_dir, monkeypatch,
) -> None:
    """_pick_save par index numerique retourne le save_id."""
    sid = _create_test_save()
    saves = save_module.list_saves()
    monkeypatch.setattr(
        "shinobi.cli.menu.Prompt.ask", lambda *a, **k: "1",
    )
    chosen = menu_module._pick_save(saves)
    assert chosen == sid


def test_pick_save_returns_id_by_full_id(
    isolated_saves_dir, monkeypatch,
) -> None:
    """_pick_save accepte le save_id complet."""
    sid = _create_test_save()
    saves = save_module.list_saves()
    monkeypatch.setattr(
        "shinobi.cli.menu.Prompt.ask", lambda *a, **k: sid,
    )
    chosen = menu_module._pick_save(saves)
    assert chosen == sid


def test_pick_save_returns_none_on_invalid(
    isolated_saves_dir, monkeypatch,
) -> None:
    """_pick_save sur input invalide retourne None."""
    _create_test_save()
    saves = save_module.list_saves()
    monkeypatch.setattr(
        "shinobi.cli.menu.Prompt.ask", lambda *a, **k: "99",
    )
    assert menu_module._pick_save(saves) is None


def test_start_play_swallows_keyboard_interrupt(
    isolated_saves_dir, monkeypatch,
) -> None:
    """_start_play attrape KeyboardInterrupt et retourne au menu."""
    def fake_session(sid):
        raise KeyboardInterrupt()

    monkeypatch.setattr(
        "shinobi.cli.play.play_session", fake_session,
    )
    # Ne lance pas d'exception
    menu_module._start_play("any_id")


def test_start_play_swallows_generic_exception(
    isolated_saves_dir, monkeypatch,
) -> None:
    """_start_play attrape n'importe quelle exception et continue."""
    def fake_session(sid):
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(
        "shinobi.cli.play.play_session", fake_session,
    )
    # Ne lance pas d'exception
    menu_module._start_play("any_id")
