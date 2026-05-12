"""Tests sur le formatage des dialogues."""

from __future__ import annotations

import pytest

from shinobi.canon.loader import load_canon
from shinobi.cli.display import format_speaker


@pytest.fixture(scope="module")
def canon():
    return load_canon(
        optional=(
            "organizations",
            "tailed_beasts",
            "kekkei_mora",
            "hiden",
            "timeline_events",
            "voice_profiles",
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


# === 6.4 display.py : panels + helpers ==================================


def test_outcome_color_full_success_green() -> None:
    from shinobi.cli.display import COLOR_OK, outcome_color
    assert outcome_color("full_success") == COLOR_OK


def test_outcome_color_partial_yellow() -> None:
    from shinobi.cli.display import COLOR_WARN, outcome_color
    assert outcome_color("partial_success") == COLOR_WARN


def test_outcome_color_catastrophic_red() -> None:
    from shinobi.cli.display import COLOR_BAD, outcome_color
    assert outcome_color("catastrophic_failure") == COLOR_BAD


def test_outcome_color_unknown_white() -> None:
    from shinobi.cli.display import outcome_color
    assert outcome_color("unknown_outcome") == "white"


def test_banner_returns_panel() -> None:
    from rich.panel import Panel

    from shinobi.cli.display import banner

    p = banner("Test Title", "subtitle")
    assert isinstance(p, Panel)


def test_status_panel_returns_panel(canon) -> None:
    """status_panel produit un Panel rich sans crash sur Character + WorldState."""
    from rich.panel import Panel

    from shinobi.canon.profiles import CanonicityProfile
    from shinobi.cli.display import status_panel
    from shinobi.engine.character import Character
    from shinobi.engine.stats import CoreStats, ExtendedStats
    from shinobi.engine.world import create_default_world
    from shinobi.types import Gender

    char = Character(
        id="x", name="Test", gender=Gender.male,
        birth_year=5, birth_date="06-15", age_years=12,
        village_of_origin="konohagakure",
        current_village="konohagakure",
        current_location="konohagakure",
        rank="genin",
        stats=CoreStats(), extended_stats=ExtendedStats(),
    )
    world = create_default_world(
        profile=CanonicityProfile.default(), starting_year=12,
    )
    panel = status_panel(char, world)
    assert isinstance(panel, Panel)


def test_print_objectives_no_goals(capsys) -> None:
    """print_objectives sans goals affiche le message vide."""
    from rich.console import Console

    from shinobi.cli.display import print_objectives

    console = Console()
    print_objectives(console, [])
    # Pas de crash, l'output rich est testable mais ici on verifie no-throw


def test_print_objectives_with_goals(capsys) -> None:
    """print_objectives avec goals enumere les descriptions."""
    from rich.console import Console

    from shinobi.cli.display import print_objectives

    console = Console()
    print_objectives(console, ["apprendre rasengan", "devenir hokage"])
    # Pas de crash


def test_action_menu_empty_options_noop() -> None:
    """action_menu sur liste vide ne crash pas."""
    from rich.console import Console

    from shinobi.cli.display import action_menu

    console = Console()
    action_menu(console, [])


def test_action_menu_with_options() -> None:
    """action_menu rend une table avec les options proposees."""
    from rich.console import Console

    from shinobi.cli.display import action_menu

    console = Console()
    action_menu(console, [
        {
            "label_fr": "Saluer Naruto",
            "difficulty_fr": "facile",
            "duration_fr": "5min",
        },
        {"label_fr": "Defier Sasuke"},  # field manquants -> backfill
    ])


def test_print_dialogue_handles_unknown_speaker(canon) -> None:
    """print_dialogue avec speaker non-canon utilise le role-based label."""
    from rich.console import Console

    from shinobi.cli.display import print_dialogue

    console = Console()
    print_dialogue(console, canon, [
        {"character_id": "marchand_taverne", "line": "Bienvenue !", "tone": "warm"},
    ])  # Pas de crash


def test_print_techniques_empty(canon) -> None:
    """print_techniques sans technique known affiche le message vide."""
    from rich.console import Console

    from shinobi.cli.display import print_techniques
    from shinobi.engine.character import Character
    from shinobi.engine.stats import CoreStats, ExtendedStats
    from shinobi.types import Gender

    char = Character(
        id="x", name="Test", gender=Gender.female,
        birth_year=5, birth_date="06-15", age_years=12,
        village_of_origin="konohagakure",
        current_village="konohagakure",
        current_location="konohagakure",
        rank="genin",
        stats=CoreStats(), extended_stats=ExtendedStats(),
    )
    print_techniques(Console(), char)
