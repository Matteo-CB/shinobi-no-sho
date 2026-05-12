"""Phase 6.7 : tests des commandes slash meta (/status, /inventory, etc.).

Couvre `_handle_meta` dispatcher : verifie qu'il route vers les bonnes
fonctions sans crash sur les 31 commandes principales.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from shinobi.canon.profiles import CanonicityProfile
from shinobi.cli.play import _handle_meta
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
        id="test_id", name="Slash Test", gender=Gender.female,
        birth_year=5, birth_date="06-15", age_years=12,
        village_of_origin="konohagakure",
        current_village="konohagakure",
        current_location="konohagakure",
        rank="genin",
        stats=CoreStats(), extended_stats=ExtendedStats(),
    )


def _setup_save(save_id: str | None = None):
    char = _make_character()
    world = create_default_world(
        profile=CanonicityProfile.default(), starting_year=12,
    )
    sid = save_module.create_save(char, world)
    return char, world, sid


def _fake_canon():
    """Stub canon minimal pour les commandes qui ne le consultent pas."""
    canon = MagicMock()
    canon.characters = {}
    canon.clans = {}
    canon.villages = {}
    canon.organizations = {}
    canon.tailed_beasts = {}
    canon.locations = {}
    canon.summons = {}
    canon.weapons_tools = {}
    canon.timeline_events = {}
    canon.voice_profiles = {}
    canon.rules_books = []
    canon.deep_motivations = {}
    canon.political_forces = {}
    canon.divergence_points = {}
    canon.narrative_patterns = {}
    canon.timeline_events_enriched = {}
    return canon


# === 6.7 slash commands meta =============================================


def test_quit_command_returns_false(isolated_saves_dir) -> None:
    """/quit retourne (False, character, world) pour stopper la boucle."""
    char, world, sid = _setup_save()
    cont, ch, w = _handle_meta(
        "/quit", char, world, sid, _fake_canon(), [],
    )
    assert cont is False


def test_exit_command_returns_false(isolated_saves_dir) -> None:
    """/exit alias de /quit."""
    char, world, sid = _setup_save()
    cont, _, _ = _handle_meta(
        "/exit", char, world, sid, _fake_canon(), [],
    )
    assert cont is False


def test_help_command_continues(isolated_saves_dir) -> None:
    """/help continue la boucle."""
    char, world, sid = _setup_save()
    cont, _, _ = _handle_meta(
        "/help", char, world, sid, _fake_canon(), [],
    )
    assert cont is True


def test_language_command_routes_to_picker(
    isolated_saves_dir, monkeypatch, tmp_path,
) -> None:
    """Phase i18n.2 : /language slash command appelle run_language_reset_menu
    et continue la boucle."""
    monkeypatch.setenv("SHINOBI_PREFERENCES_DIR", str(tmp_path / "prefs"))
    # Mock le picker pour ne pas attendre input
    called_with: list[str] = []

    def fake_reset_menu(**kwargs):
        called_with.append("called")
        return "ja"

    monkeypatch.setattr(
        "shinobi.cli.language_picker.run_language_reset_menu",
        fake_reset_menu,
    )

    char, world, sid = _setup_save()
    cont, _, _ = _handle_meta(
        "/language", char, world, sid, _fake_canon(), [],
    )
    assert cont is True
    assert called_with == ["called"]


def test_status_command(isolated_saves_dir) -> None:
    """/status n'echoue pas et continue."""
    char, world, sid = _setup_save()
    cont, _, _ = _handle_meta(
        "/status", char, world, sid, _fake_canon(), [],
    )
    assert cont is True


def test_techniques_command(isolated_saves_dir) -> None:
    """/techniques liste les techniques (vide ici)."""
    char, world, sid = _setup_save()
    cont, _, _ = _handle_meta(
        "/techniques", char, world, sid, _fake_canon(), [],
    )
    assert cont is True


def test_objectives_command_no_goals(isolated_saves_dir) -> None:
    """/objectives sans goal affiche 'aucun'."""
    char, world, sid = _setup_save()
    cont, _, _ = _handle_meta(
        "/objectives", char, world, sid, _fake_canon(), [],
    )
    assert cont is True


def test_inventory_command(isolated_saves_dir) -> None:
    """/inventory n'echoue pas."""
    char, world, sid = _setup_save()
    cont, _, _ = _handle_meta(
        "/inventory", char, world, sid, _fake_canon(), [],
    )
    assert cont is True


def test_journal_command(isolated_saves_dir) -> None:
    """/journal lit le narrative_log (vide initialement)."""
    char, world, sid = _setup_save()
    cont, _, _ = _handle_meta(
        "/journal", char, world, sid, _fake_canon(), [],
    )
    assert cont is True


def test_breadcrumbs_command(isolated_saves_dir) -> None:
    """/breadcrumbs liste les indices (vide)."""
    char, world, sid = _setup_save()
    cont, _, _ = _handle_meta(
        "/breadcrumbs", char, world, sid, _fake_canon(), [],
    )
    assert cont is True


def test_missions_command(isolated_saves_dir, monkeypatch) -> None:
    """/missions liste les missions disponibles + prompt mockable."""
    # /missions prompte pour selection, on mock pour retourner '0' (skip)
    monkeypatch.setattr(
        "rich.prompt.IntPrompt.ask", lambda *a, **k: 0,
    )
    monkeypatch.setattr(
        "rich.prompt.Prompt.ask", lambda *a, **k: "0",
    )
    char, world, sid = _setup_save()
    cont, _, _ = _handle_meta(
        "/missions", char, world, sid, _fake_canon(), [],
    )
    assert cont is True


def test_active_missions_command(isolated_saves_dir) -> None:
    """/active_missions liste les missions actives."""
    char, world, sid = _setup_save()
    cont, _, _ = _handle_meta(
        "/active_missions", char, world, sid, _fake_canon(), [],
    )
    assert cont is True


def test_reputation_command(isolated_saves_dir) -> None:
    """/reputation affiche les rep par village."""
    char, world, sid = _setup_save()
    cont, _, _ = _handle_meta(
        "/reputation", char, world, sid, _fake_canon(), [],
    )
    assert cont is True


def test_biography_command(isolated_saves_dir) -> None:
    """/biography affiche l'historique du perso."""
    char, world, sid = _setup_save()
    cont, _, _ = _handle_meta(
        "/biography", char, world, sid, _fake_canon(), [],
    )
    assert cont is True


def test_knowledge_command(isolated_saves_dir) -> None:
    """/knowledge liste ce que le perso a appris."""
    char, world, sid = _setup_save()
    cont, _, _ = _handle_meta(
        "/knowledge", char, world, sid, _fake_canon(), [],
    )
    assert cont is True


def test_rumors_command(isolated_saves_dir) -> None:
    """/rumors liste les rumeurs entendues."""
    char, world, sid = _setup_save()
    cont, _, _ = _handle_meta(
        "/rumors", char, world, sid, _fake_canon(), [],
    )
    assert cont is True


def test_weapons_command(isolated_saves_dir) -> None:
    """/weapons liste les armes possedees."""
    char, world, sid = _setup_save()
    cont, _, _ = _handle_meta(
        "/weapons", char, world, sid, _fake_canon(), [],
    )
    assert cont is True


def test_summons_command(isolated_saves_dir) -> None:
    """/summons liste les contrats d'invocation."""
    char, world, sid = _setup_save()
    cont, _, _ = _handle_meta(
        "/summons", char, world, sid, _fake_canon(), [],
    )
    assert cont is True


def test_dialogues_command(isolated_saves_dir) -> None:
    """/dialogues affiche le journal VN."""
    char, world, sid = _setup_save()
    cont, _, _ = _handle_meta(
        "/dialogues", char, world, sid, _fake_canon(), [],
    )
    assert cont is True


def test_declare_command_interactive(isolated_saves_dir, monkeypatch) -> None:
    """/declare (interactif) prompt l'objectif puis sauvegarde."""
    monkeypatch.setattr(
        "rich.prompt.Prompt.ask",
        lambda *a, **k: "apprendre rasengan",
    )
    char, world, sid = _setup_save()
    cont, _, _ = _handle_meta(
        "/declare", char, world, sid, _fake_canon(), [],
    )
    assert cont is True
    # Le goal est sauvegarde
    goals = save_module.load_goals(sid)
    assert len(goals) == 1
    assert "rasengan" in goals[0].description_player.lower()


def test_skip_command_advances_time(isolated_saves_dir) -> None:
    """/skip 7d avance current_date."""
    char, world, sid = _setup_save()
    initial_year = world.current_year
    cont, _, w = _handle_meta(
        "/skip 7d", char, world, sid, _fake_canon(), [],
    )
    assert cont is True
    # current_date a change OU current_year a change
    assert (w.current_date != world.current_date) or (
        w.current_year != initial_year
    )


def test_skip_command_invalid_format(isolated_saves_dir) -> None:
    """/skip avec format invalide ne crash pas."""
    char, world, sid = _setup_save()
    cont, _, _ = _handle_meta(
        "/skip invalid_format", char, world, sid, _fake_canon(), [],
    )
    assert cont is True


def test_personality_command(isolated_saves_dir) -> None:
    """/personality affiche le vecteur de personnalite (vide par defaut)."""
    char, world, sid = _setup_save()
    cont, _, _ = _handle_meta(
        "/personality", char, world, sid, _fake_canon(), [],
    )
    assert cont is True


def test_personality_command_with_npc_id(isolated_saves_dir) -> None:
    """/personality <npc_id> affiche un NPC specifique."""
    char, world, sid = _setup_save()
    cont, _, _ = _handle_meta(
        "/personality uchiha_itachi", char, world, sid, _fake_canon(), [],
    )
    assert cont is True


def test_beliefs_command_no_arg_warns(isolated_saves_dir) -> None:
    """/beliefs sans argument warn 'Usage'."""
    char, world, sid = _setup_save()
    cont, _, _ = _handle_meta(
        "/beliefs", char, world, sid, _fake_canon(), [],
    )
    assert cont is True


def test_beliefs_command_with_npc_id(isolated_saves_dir) -> None:
    """/beliefs <npc_id> affiche les facts connus du NPC."""
    char, world, sid = _setup_save()
    cont, _, _ = _handle_meta(
        "/beliefs uchiha_itachi", char, world, sid, _fake_canon(), [],
    )
    assert cont is True


def test_agents_command(isolated_saves_dir) -> None:
    """/agents affiche le roster d'agents Phase E."""
    char, world, sid = _setup_save()
    cont, _, _ = _handle_meta(
        "/agents", char, world, sid, _fake_canon(), [],
    )
    assert cont is True


def test_agent_command_with_id(isolated_saves_dir) -> None:
    """/agent <npc_id> affiche un agent specifique (memoire + actions)."""
    char, world, sid = _setup_save()
    cont, _, _ = _handle_meta(
        "/agent uchiha_itachi", char, world, sid, _fake_canon(), [],
    )
    assert cont is True


def test_tensions_command(isolated_saves_dir) -> None:
    """/tensions liste les tensions Phase C."""
    # Init le KG via _ensure_kg_initialized en bypassant
    char, world, sid = _setup_save()
    cont, _, _ = _handle_meta(
        "/tensions", char, world, sid, _fake_canon(), [],
    )
    assert cont is True


def test_path_command_unknown_goal(isolated_saves_dir) -> None:
    """/path <unknown_id> warn 'introuvable' sans crash."""
    char, world, sid = _setup_save()
    cont, _, _ = _handle_meta(
        "/path nonexistent_goal_xyz",
        char, world, sid, _fake_canon(), [],
    )
    assert cont is True


def test_path_command_no_goal_arg(isolated_saves_dir) -> None:
    """/path sans argument ne crash pas (aucun goal trouve)."""
    char, world, sid = _setup_save()
    cont, _, _ = _handle_meta(
        "/path", char, world, sid, _fake_canon(), [],
    )
    assert cont is True


def test_path_command_with_goal_llm_offline_graceful(
    isolated_saves_dir, monkeypatch,
) -> None:
    """/path <goal_id> avec LLM offline: fallback gracieux."""
    from shinobi.goals.declaration import declare_goal, GoalTargetType

    char, world, sid = _setup_save()
    # Cree un goal pour avoir un id existant
    goal = declare_goal(
        description_player="apprendre rasengan",
        interpretation_canonical="learn rasengan",
        declared_at_year=12, declared_at_age=12,
        target_type=GoalTargetType.learn_technique,
        target_id="rasengan",
    )
    save_module.save_goal(sid, goal)

    # Le pathfinder_flow utilise asyncio.run + LLMClient ; si LLM offline,
    # exception attrapee dans le handler -> message dim mais continue.
    cont, _, _ = _handle_meta(
        f"/path {goal.id[:8]}",
        char, world, sid, _fake_canon(), [],
    )
    assert cont is True


def test_buy_command_no_shop_at_location(isolated_saves_dir, monkeypatch) -> None:
    """/buy sans shop au lieu courant retourne sans crash."""
    monkeypatch.setattr(
        "rich.prompt.Prompt.ask", lambda *a, **k: "0",
    )
    monkeypatch.setattr(
        "rich.prompt.IntPrompt.ask", lambda *a, **k: 0,
    )
    char, world, sid = _setup_save()
    cont, _, _ = _handle_meta(
        "/buy", char, world, sid, _fake_canon(), [],
    )
    assert cont is True


def test_sell_command(isolated_saves_dir, monkeypatch) -> None:
    """/sell sans inventaire ne crash pas."""
    monkeypatch.setattr(
        "rich.prompt.Prompt.ask", lambda *a, **k: "0",
    )
    monkeypatch.setattr(
        "rich.prompt.IntPrompt.ask", lambda *a, **k: 0,
    )
    char, world, sid = _setup_save()
    cont, _, _ = _handle_meta(
        "/sell", char, world, sid, _fake_canon(), [],
    )
    assert cont is True


def test_use_command_no_args(isolated_saves_dir) -> None:
    """/use sans argument warn ou continue."""
    char, world, sid = _setup_save()
    cont, _, _ = _handle_meta(
        "/use", char, world, sid, _fake_canon(), [],
    )
    assert cont is True


def test_sign_contract_command(isolated_saves_dir) -> None:
    """/sign_contract <name> sans contrat valide warn."""
    char, world, sid = _setup_save()
    cont, _, _ = _handle_meta(
        "/sign_contract toad", char, world, sid, _fake_canon(), [],
    )
    assert cont is True


def test_invoke_command(isolated_saves_dir) -> None:
    """/invoke <name> sans contrat signe warn."""
    char, world, sid = _setup_save()
    cont, _, _ = _handle_meta(
        "/invoke toad", char, world, sid, _fake_canon(), [],
    )
    assert cont is True


def test_export_vn_dialogues_command(isolated_saves_dir, tmp_path) -> None:
    """/export-vn-dialogues genere un payload VN dans tmp."""
    char, world, sid = _setup_save()
    cont, _, _ = _handle_meta(
        f"/export-vn-dialogues {tmp_path / 'vn.json'}",
        char, world, sid, _fake_canon(), [],
    )
    assert cont is True


def test_unknown_command_continues(isolated_saves_dir) -> None:
    """Une commande slash inconnue n'arrete pas la boucle."""
    char, world, sid = _setup_save()
    cont, _, _ = _handle_meta(
        "/totally_unknown_command", char, world, sid, _fake_canon(), [],
    )
    # Implementation tolere les commandes inconnues (pas de match elif)
    # et retourne (True, char, world)
    assert cont is True
