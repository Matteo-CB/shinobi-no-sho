"""Phase i18n.2 : tests language_picker (first-launch + reset menu).

Couvre :
- _resolve_choice : numeros, codes, invalides
- _build_panel_title et _build_table : multi-langue
- show_picker : choix valide, retry sur invalide, persist on/off
- maybe_show_first_launch_picker : skip si already done, lance sinon
- run_language_reset_menu : toujours persiste
- Comportement runtime (set_active_language) + persistance disque
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from rich.console import Console

from shinobi.cli.language_picker import (
    PICKER_CONFIRM_BY_LANGUAGE,
    PICKER_PROMPT_BY_LANGUAGE,
    PICKER_TITLE_BY_LANGUAGE,
    _build_panel_title,
    _build_table,
    _resolve_choice,
    maybe_show_first_launch_picker,
    run_language_reset_menu,
    show_picker,
)
from shinobi.i18n import (
    NATIVE_NAMES,
    SUPPORTED_LANGUAGES,
    get_active_language,
    load_preferences,
)
from shinobi.i18n.catalog import reset_for_tests as reset_catalog


@pytest.fixture(autouse=True)
def reset_state():
    """Reset le runtime i18n entre chaque test."""
    reset_catalog()
    yield
    reset_catalog()


@pytest.fixture()
def isolated_prefs(tmp_path: Path, monkeypatch) -> Path:
    """Isole preferences.json dans tmp_path."""
    monkeypatch.setenv("SHINOBI_PREFERENCES_DIR", str(tmp_path))
    return tmp_path


def _scripted_prompt(answers: list[str]):
    """Retourne une fn de prompt qui consomme `answers` une a une."""
    iterator = iter(answers)

    def _prompt(*_args, **_kwargs) -> str:
        return next(iterator)

    return _prompt


# === _resolve_choice =====================================================


def test_resolve_choice_accepts_numeric_indices() -> None:
    """Numeros 1-8 -> codes correspondants."""
    for i, code in enumerate(SUPPORTED_LANGUAGES, start=1):
        assert _resolve_choice(str(i)) == code


def test_resolve_choice_accepts_iso_codes_case_insensitive() -> None:
    """Codes ISO case-insensitive."""
    assert _resolve_choice("en") == "en"
    assert _resolve_choice("EN") == "en"
    assert _resolve_choice("Ja") == "ja"
    assert _resolve_choice("pt-BR") == "pt-BR"
    assert _resolve_choice("PT-br") == "pt-BR"


def test_resolve_choice_rejects_invalid_inputs() -> None:
    """Numeros hors plage, codes inconnus, vide -> None."""
    assert _resolve_choice("0") is None
    assert _resolve_choice("9") is None
    assert _resolve_choice("99") is None
    assert _resolve_choice("xx") is None
    assert _resolve_choice("klingon") is None
    assert _resolve_choice("") is None
    assert _resolve_choice("   ") is None


# === _build_panel_title et _build_table ==================================


def test_build_panel_title_combines_all_8_languages() -> None:
    """Le titre du panel contient les 8 traductions du picker title."""
    title = _build_panel_title()
    for code in SUPPORTED_LANGUAGES:
        assert PICKER_TITLE_BY_LANGUAGE[code] in title


def test_picker_title_dict_covers_all_languages() -> None:
    """Toutes les langues supportees ont une entree dans les 3 dicts."""
    for code in SUPPORTED_LANGUAGES:
        assert code in PICKER_TITLE_BY_LANGUAGE
        assert code in PICKER_PROMPT_BY_LANGUAGE
        assert code in PICKER_CONFIRM_BY_LANGUAGE


def test_build_table_has_8_rows() -> None:
    """La table affiche les 8 langues avec idx + code + nom natif."""
    table = _build_table()
    # Table.grid expose .rows comme liste interne
    assert len(table.rows) == len(SUPPORTED_LANGUAGES)


# === show_picker =========================================================


def test_show_picker_returns_chosen_language(
    isolated_prefs: Path,
) -> None:
    """show_picker(prompt='3') retourne le 3e code (ja) + persiste."""
    console = Console(record=True)
    prompt = _scripted_prompt(["3"])
    chosen = show_picker(console=console, prompt_fn=prompt)
    assert chosen == SUPPORTED_LANGUAGES[2]
    assert get_active_language() == chosen
    # Persiste sur disque
    prefs = load_preferences()
    assert prefs.language == chosen
    assert prefs.first_launch_completed is True


def test_show_picker_retries_on_invalid_then_accepts(
    isolated_prefs: Path,
) -> None:
    """show_picker boucle tant qu'input invalide, puis accepte."""
    console = Console(record=True)
    prompt = _scripted_prompt(["xx", "99", "  ", "ja"])
    chosen = show_picker(console=console, prompt_fn=prompt)
    assert chosen == "ja"
    assert get_active_language() == "ja"


def test_show_picker_persist_false_does_not_write_file(
    isolated_prefs: Path,
) -> None:
    """persist=False : runtime applied mais preferences.json non ecrit."""
    console = Console(record=True)
    prompt = _scripted_prompt(["fr"])
    chosen = show_picker(console=console, prompt_fn=prompt, persist=False)
    assert chosen == "fr"
    assert get_active_language() == "fr"
    # Pas de fichier preferences.json cree
    assert not (isolated_prefs / "preferences.json").exists()


def test_show_picker_uses_default_console_when_none(
    isolated_prefs: Path, capsys,
) -> None:
    """show_picker(console=None) cree une Console par defaut sans crash."""
    prompt = _scripted_prompt(["en"])
    chosen = show_picker(console=None, prompt_fn=prompt)
    assert chosen == "en"


def test_show_picker_uses_default_prompt_fn_when_none(
    isolated_prefs: Path, monkeypatch,
) -> None:
    """show_picker(prompt_fn=None) tombe sur Prompt.ask (couvre la branche)."""
    from rich.prompt import Prompt

    answers = iter(["fr"])
    monkeypatch.setattr(Prompt, "ask", lambda *a, **k: next(answers))
    console = Console(record=True)
    chosen = show_picker(console=console, prompt_fn=None)
    assert chosen == "fr"


def test_show_picker_displays_confirm_message_in_chosen_language(
    isolated_prefs: Path,
) -> None:
    """Le message de confirmation est dans la langue choisie."""
    console = Console(record=True)
    prompt = _scripted_prompt(["ja"])
    show_picker(console=console, prompt_fn=prompt)
    output = console.export_text()
    expected_substring = "言語を" + NATIVE_NAMES["ja"] + "に設定"
    assert expected_substring in output


# === maybe_show_first_launch_picker =====================================


def test_maybe_show_first_launch_skips_when_already_completed(
    isolated_prefs: Path,
) -> None:
    """Si first_launch_completed=True, le picker est skip."""
    # Pre-cree des preferences valides
    (isolated_prefs / "preferences.json").write_text(
        json.dumps({
            "schema_version": 1,
            "language": "zh",
            "first_launch_completed": True,
        }),
        encoding="utf-8",
    )
    # Le prompt ne doit JAMAIS etre appele
    not_called = _scripted_prompt([])
    result = maybe_show_first_launch_picker(prompt_fn=not_called)
    assert result is None
    # Mais la langue runtime est initialisee depuis preferences
    assert get_active_language() == "zh"


def test_maybe_show_first_launch_shows_picker_when_needed(
    isolated_prefs: Path,
) -> None:
    """Sans preferences.json, le picker est affiche."""
    console = Console(record=True)
    prompt = _scripted_prompt(["ko"])
    result = maybe_show_first_launch_picker(
        console=console, prompt_fn=prompt,
    )
    assert result == "ko"
    assert get_active_language() == "ko"
    assert load_preferences().first_launch_completed is True


# === run_language_reset_menu =============================================


def test_run_language_reset_menu_persists(isolated_prefs: Path) -> None:
    """/language reset persiste toujours (meme si first_launch deja done)."""
    # Pre-set
    (isolated_prefs / "preferences.json").write_text(
        json.dumps({
            "schema_version": 1,
            "language": "en",
            "first_launch_completed": True,
        }),
        encoding="utf-8",
    )
    console = Console(record=True)
    prompt = _scripted_prompt(["de"])
    chosen = run_language_reset_menu(console=console, prompt_fn=prompt)
    assert chosen == "de"
    assert get_active_language() == "de"
    prefs = load_preferences()
    assert prefs.language == "de"
