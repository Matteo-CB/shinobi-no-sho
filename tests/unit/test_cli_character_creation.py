"""Phase 6.3 : tests des helpers character_creation.py.

Couvre les fonctions deterministes (rank_from_age, default_era_index,
year_within_era, detect_clan_from_name) sans declencher le flux interactif
complet.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from shinobi.cli.character_creation import (
    _default_era_index,
    _detect_clan_from_name,
    _rank_from_age,
)


# === _rank_from_age ======================================================


def test_rank_from_age_civilian_under_6() -> None:
    assert _rank_from_age(0) == "civilian"
    assert _rank_from_age(3) == "civilian"
    assert _rank_from_age(5) == "civilian"


def test_rank_from_age_academy_6_to_11() -> None:
    assert _rank_from_age(6) == "academy_student"
    assert _rank_from_age(8) == "academy_student"
    assert _rank_from_age(11) == "academy_student"


def test_rank_from_age_genin_at_12_plus() -> None:
    assert _rank_from_age(12) == "genin"
    assert _rank_from_age(15) == "genin"
    assert _rank_from_age(20) == "genin"


# === _default_era_index ==================================================


def test_default_era_index_naruto_academy_era() -> None:
    """Defaut prefere l'ere de l'academie de Naruto."""
    fake_eras = [
        MagicMock(id="warring_states_era"),
        MagicMock(id="naruto_academy_era"),
        MagicMock(id="boruto_era"),
    ]
    # Index est 1-based selon impl
    assert _default_era_index(fake_eras) == 2


def test_default_era_index_fallback_to_first() -> None:
    """Sans ere matchante, retourne 1 (premier index)."""
    fake_eras = [
        MagicMock(id="random_era_x"),
        MagicMock(id="random_era_y"),
    ]
    assert _default_era_index(fake_eras) == 1


def test_default_era_index_empty_returns_1() -> None:
    """Liste vide retourne 1."""
    assert _default_era_index([]) == 1


# === _detect_clan_from_name ==============================================


def test_detect_clan_from_name_uchiha() -> None:
    """Nom 'Uchiha Itachi' suggere clan uchiha."""
    canon = MagicMock()
    canon.clans = {"uchiha": MagicMock(id="uchiha")}
    assert _detect_clan_from_name(canon, "Uchiha Itachi") == "uchiha"


def test_detect_clan_from_name_lowercase() -> None:
    """Detection insensible a la casse."""
    canon = MagicMock()
    canon.clans = {"uchiha": MagicMock(id="uchiha")}
    assert _detect_clan_from_name(canon, "uchiha sasuke") == "uchiha"


def test_detect_clan_from_name_civil_returns_none() -> None:
    """Nom sans clan canon connu retourne None."""
    canon = MagicMock()
    canon.clans = {"uchiha": MagicMock(id="uchiha")}
    assert _detect_clan_from_name(canon, "Tanaka Civilian") is None


def test_detect_clan_from_name_empty_canon() -> None:
    """canon.clans vide retourne None."""
    canon = MagicMock()
    canon.clans = {}
    assert _detect_clan_from_name(canon, "Anything") is None


# === 6.3 run_character_creation flow complet (mock interactif) ==========


def test_run_character_creation_full_flow_creates_save(
    tmp_path, monkeypatch,
) -> None:
    """Phase 6.3 : run_character_creation orchestrateur complet, tous
    les Prompt.ask mockes pour exercer le flux jusqu'a la save.

    Mock strategy :
    - Prompt.ask retourne values cycliques selon l'ordre des prompts
    - typer.confirm renvoie True (validation finale + family confirms)
    - IntPrompt.ask renvoie 1 (premier choix)
    """
    from shinobi.config import settings
    monkeypatch.setattr(settings, "saves_path", str(tmp_path))
    monkeypatch.setattr(
        type(settings), "saves_dir",
        property(lambda self: tmp_path),
    )

    # Sequence de reponses Prompt.ask :
    # 1. Nom -> "Test Hero"
    # 2. Genre -> "f"
    # 3. Annee de demarrage (mocke la fonction _pick_starting_year)
    # ... beaucoup d'autres prompts qu'on mock par defaut "1" ou "r"
    prompt_responses = iter([
        "1",           # mode = nouveau perso (random)
        "Test Hero",  # name
        "f",           # gender
        # _pick_starting_year est mock separement
    ])

    def fake_prompt_ask(prompt_text, *args, **kwargs):
        try:
            return next(prompt_responses)
        except StopIteration:
            # Default fallback pour les prompts non listes : choix '1' ou 'r'
            default = kwargs.get("default")
            if default is not None:
                return default
            return "1"

    monkeypatch.setattr("rich.prompt.Prompt.ask", fake_prompt_ask)
    monkeypatch.setattr(
        "rich.prompt.IntPrompt.ask", lambda *a, **k: k.get("default", 1),
    )
    monkeypatch.setattr(
        "rich.prompt.Confirm.ask", lambda *a, **k: True,
    )
    # typer.confirm dans le flow
    monkeypatch.setattr("typer.confirm", lambda *a, **k: True)

    # Mock _pick_starting_year pour eviter le menu d'eres
    monkeypatch.setattr(
        "shinobi.cli.character_creation._pick_starting_year",
        lambda canon: 12,
    )
    # Mock _pick_village pour selectionner konohagakure
    monkeypatch.setattr(
        "shinobi.cli.character_creation._pick_village",
        lambda villages: "konohagakure",
    )
    # Mock _pick_clan (return None = civil)
    monkeypatch.setattr(
        "shinobi.cli.character_creation._pick_clan",
        lambda canon, village_id, year, hint=None: None,
    )
    # Mock _pick_kekkei_genkai (none)
    monkeypatch.setattr(
        "shinobi.cli.character_creation._pick_kekkei_genkai",
        lambda clan_id: [],
    )
    # Mock _roll_rare_gifts (no rare gifts)
    monkeypatch.setattr(
        "shinobi.cli.character_creation._roll_rare_gifts",
        lambda canon, name, year: ([], None),
    )
    # Mock _pick_natures (1 nature)
    monkeypatch.setattr(
        "shinobi.cli.character_creation._pick_natures",
        lambda clan_id: ["katon"],
    )
    # Mock _pick_age_years (12 = genin)
    monkeypatch.setattr(
        "shinobi.cli.character_creation._pick_age_years",
        lambda: 12,
    )
    # Mock _pick_family (state minimal)
    from shinobi.engine.character import FamilyState
    monkeypatch.setattr(
        "shinobi.cli.character_creation._pick_family",
        lambda clan_id: FamilyState(),
    )

    # Run le flow complet
    from shinobi.cli.character_creation import run_character_creation
    save_id = run_character_creation()

    assert save_id is not None
    assert save_id.startswith("test_hero_") or save_id.startswith("shinobi_")

    # Verifie que la save existe et est chargeable
    from shinobi.persistence import saves as save_module
    char, world, meta = save_module.load_save(save_id)
    assert char.name == "Test Hero"
    assert char.age_years == 12
    assert char.current_village == "konohagakure"
    assert char.rank == "genin"
    assert "katon" in char.natures


def test_run_character_creation_user_cancels_returns_none(
    tmp_path, monkeypatch,
) -> None:
    """Phase 6.3 : si user repond 'no' a la confirmation finale, no save."""
    from shinobi.config import settings
    monkeypatch.setattr(settings, "saves_path", str(tmp_path))
    monkeypatch.setattr(
        type(settings), "saves_dir",
        property(lambda self: tmp_path),
    )

    # Premier prompt = mode "1" (nouveau perso), puis tous les autres
    # prompts retournent "Cancel Test" ou default.
    cancel_iter = iter(["1"])

    def fake_ask(prompt_text, *args, **kwargs):
        try:
            return next(cancel_iter)
        except StopIteration:
            return kwargs.get("default", "Cancel Test")

    monkeypatch.setattr("rich.prompt.Prompt.ask", fake_ask)
    monkeypatch.setattr(
        "rich.prompt.IntPrompt.ask", lambda *a, **k: 1,
    )
    monkeypatch.setattr("rich.prompt.Confirm.ask", lambda *a, **k: True)
    # typer.confirm sur la validation finale -> False
    monkeypatch.setattr("typer.confirm", lambda *a, **k: False)

    monkeypatch.setattr(
        "shinobi.cli.character_creation._pick_starting_year",
        lambda canon: 12,
    )
    monkeypatch.setattr(
        "shinobi.cli.character_creation._pick_village",
        lambda villages: "konohagakure",
    )
    monkeypatch.setattr(
        "shinobi.cli.character_creation._pick_clan",
        lambda canon, vid, y, hint=None: None,
    )
    monkeypatch.setattr(
        "shinobi.cli.character_creation._pick_kekkei_genkai",
        lambda c: [],
    )
    monkeypatch.setattr(
        "shinobi.cli.character_creation._roll_rare_gifts",
        lambda c, n, y: ([], None),
    )
    monkeypatch.setattr(
        "shinobi.cli.character_creation._pick_natures",
        lambda c: ["katon"],
    )
    monkeypatch.setattr(
        "shinobi.cli.character_creation._pick_age_years",
        lambda: 12,
    )
    from shinobi.engine.character import FamilyState
    monkeypatch.setattr(
        "shinobi.cli.character_creation._pick_family",
        lambda c: FamilyState(),
    )

    from shinobi.cli.character_creation import run_character_creation
    result = run_character_creation()
    assert result is None
    # Aucune save creee
    from shinobi.persistence import saves as save_module
    assert save_module.list_saves() == []


def test_run_character_creation_canon_mode_creates_save(
    tmp_path, monkeypatch,
) -> None:
    """Phase 6.3 (canon mode) : flow complet d'incarnation Itachi @13.

    Mock les prompts (mode=2, village=konohagakure, selection=1, age=13)
    + typer.confirm=True. Verifie que la save creee charge un Character
    avec le profil canon Itachi (clan uchiha, sharingan, jonin).
    """
    from shinobi.config import settings
    monkeypatch.setattr(settings, "saves_path", str(tmp_path))
    monkeypatch.setattr(
        type(settings), "saves_dir",
        property(lambda self: tmp_path),
    )

    # Sequence des prompts dans _run_canon_incarnation_flow :
    # 1. Mode -> "2"
    # 2. Village filter -> "konohagakure"
    # 3. Selection char -> "uchiha_itachi"
    # 4. Age -> "13"
    answers = iter(["2", "konohagakure", "uchiha_itachi", "13"])

    def fake_ask(*args, **kwargs):
        try:
            return next(answers)
        except StopIteration:
            return kwargs.get("default", "")

    monkeypatch.setattr("rich.prompt.Prompt.ask", fake_ask)
    monkeypatch.setattr("typer.confirm", lambda *a, **k: True)

    from shinobi.cli.character_creation import run_character_creation
    save_id = run_character_creation()
    assert save_id is not None

    from shinobi.persistence import saves as save_module
    char, world, meta = save_module.load_save(save_id)
    # Profil canon Itachi
    assert "Itachi" in char.name
    assert char.clan == "uchiha"
    assert char.age_years == 13
    assert char.current_village == "konohagakure"
    assert "sharingan" in char.kekkei_genkai
    # Itachi est prodige -> jonin a 13 ans
    assert char.rank == "jonin"
    # current_year = birth(-7) + age(13) = 6
    assert world.current_year == 6


def test_run_character_creation_canon_mode_user_cancels(
    tmp_path, monkeypatch,
) -> None:
    """Phase 6.3 : si user cancel a la confirmation finale, no save."""
    from shinobi.config import settings
    monkeypatch.setattr(settings, "saves_path", str(tmp_path))
    monkeypatch.setattr(
        type(settings), "saves_dir",
        property(lambda self: tmp_path),
    )

    answers = iter(["2", "", "uchiha_itachi", "13"])

    def fake_ask(*args, **kwargs):
        try:
            return next(answers)
        except StopIteration:
            return kwargs.get("default", "")

    monkeypatch.setattr("rich.prompt.Prompt.ask", fake_ask)
    # User cancel
    monkeypatch.setattr("typer.confirm", lambda *a, **k: False)

    from shinobi.cli.character_creation import run_character_creation
    save_id = run_character_creation()
    assert save_id is None

    from shinobi.persistence import saves as save_module
    assert save_module.list_saves() == []


def test_run_character_creation_canon_mode_unknown_char(
    tmp_path, monkeypatch,
) -> None:
    """Phase 6.3 : si user tape un perso inconnu, retourne None graceful."""
    from shinobi.config import settings
    monkeypatch.setattr(settings, "saves_path", str(tmp_path))
    monkeypatch.setattr(
        type(settings), "saves_dir",
        property(lambda self: tmp_path),
    )

    answers = iter(["2", "", "totally_made_up_xyz_character", "13"])

    def fake_ask(*args, **kwargs):
        try:
            return next(answers)
        except StopIteration:
            return kwargs.get("default", "")

    monkeypatch.setattr("rich.prompt.Prompt.ask", fake_ask)
    monkeypatch.setattr("typer.confirm", lambda *a, **k: True)

    from shinobi.cli.character_creation import run_character_creation
    save_id = run_character_creation()
    assert save_id is None
