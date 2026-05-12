"""Phase 6.8/6.9 : test e2e CLI via subprocess.

Substitue automatique des tests manuels Linux (6.8) + Windows (6.9). Lance
le CLI en sous-processus et verifie :
- `python -m shinobi.cli.app version` retourne 0 + affiche version
- `python -m shinobi.cli.app config` retourne 0 + affiche config
- `python -m shinobi.cli.app list` sans saves retourne 0 + 'Aucune save'
- Round-trip complet : create save (via API) -> list -> export -> delete
  -> import -> list (via subprocess CLI)

Critere de sortie roadmap (6.8) :
> 'creer un perso, jouer 30 tours, sauvegarder, recharger'

Le 'jouer 30 tours' interactif n'est pas testable en subprocess sans LLM
+ stdin scripte ; on couvre la partie CRUD CLI (create implicite via API,
list/export/import/delete via subprocess) + le 30-tours roundtrip est
deja validé en test_persistence.py:test_save_50_turns_then_reload_preserves_state.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from shinobi.canon.profiles import CanonicityProfile
from shinobi.engine.character import Character
from shinobi.engine.stats import CoreStats, ExtendedStats
from shinobi.engine.world import create_default_world
from shinobi.persistence import saves as save_module
from shinobi.types import Gender


def _python_cli(saves_dir: Path) -> list[str]:
    """Construit la commande [python -m shinobi.cli.app ...] avec saves_dir
    isole via env var SAVES_PATH (cf shinobi.config.settings).
    """
    return [sys.executable, "-m", "shinobi.cli.app"]


@pytest.fixture()
def isolated_env(tmp_path: Path):
    """Env subprocess avec SAVES_PATH + SHINOBI_PREFERENCES_DIR isoles.

    Pre-initialise les preferences i18n avec first_launch_completed=True
    afin que le picker interactif Phase 2 ne bloque pas le subprocess.
    """
    import json
    env = os.environ.copy()
    env["SAVES_PATH"] = str(tmp_path)
    prefs_dir = tmp_path / "prefs"
    prefs_dir.mkdir(parents=True, exist_ok=True)
    (prefs_dir / "preferences.json").write_text(
        json.dumps({
            "language": "en",
            "first_launch_completed": True,
            "language_chosen_at": "2026-01-01T00:00:00",
            "schema_version": 1,
        }),
        encoding="utf-8",
    )
    env["SHINOBI_PREFERENCES_DIR"] = str(prefs_dir)
    env["PYTHONIOENCODING"] = "utf-8"
    return env, tmp_path


def _make_character() -> Character:
    return Character(
        id="test_id", name="E2E Test", gender=Gender.female,
        birth_year=5, birth_date="06-15", age_years=12,
        village_of_origin="konohagakure",
        current_village="konohagakure",
        current_location="konohagakure",
        rank="genin",
        stats=CoreStats(), extended_stats=ExtendedStats(),
    )


# === 6.8/6.9 e2e CLI subprocess ==========================================


def test_cli_version_subprocess(isolated_env) -> None:
    """`python -m shinobi.cli.app version` exit code 0 + affiche version."""
    env, _ = isolated_env
    cmd = _python_cli(env["SAVES_PATH"]) + ["version"]
    result = subprocess.run(
        cmd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    assert result.returncode == 0
    assert "Shinobi no Sho" in result.stdout


def test_cli_config_subprocess(isolated_env) -> None:
    """`shinobi config` affiche la config."""
    env, _ = isolated_env
    cmd = _python_cli(env["SAVES_PATH"]) + ["config"]
    result = subprocess.run(
        cmd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    assert result.returncode == 0
    assert "LLM backend" in result.stdout
    assert "Saves dir" in result.stdout


def test_cli_list_empty_subprocess(isolated_env) -> None:
    """`shinobi list` sur saves dir vide affiche 'Aucune save'."""
    env, tmp_path = isolated_env
    cmd = _python_cli(tmp_path) + ["list"]
    result = subprocess.run(
        cmd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    assert result.returncode == 0
    assert "No save" in result.stdout


def test_cli_play_no_saves_exits_subprocess(isolated_env) -> None:
    """`shinobi play` sans save existante quitte avec un message."""
    env, _ = isolated_env
    cmd = _python_cli(env["SAVES_PATH"]) + ["play"]
    result = subprocess.run(
        cmd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    assert result.returncode == 0
    assert "No save" in result.stdout


def test_cli_help_lists_all_commands_subprocess(isolated_env) -> None:
    """`shinobi --help` liste toutes les sous-commandes."""
    env, _ = isolated_env
    cmd = _python_cli(env["SAVES_PATH"]) + ["--help"]
    result = subprocess.run(
        cmd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    assert result.returncode == 0
    for sub in ("version", "config", "list", "delete", "export", "import", "play", "new"):
        assert sub in result.stdout


def test_cli_e2e_create_list_export_delete_import_roundtrip(
    isolated_env, tmp_path,
) -> None:
    """Test e2e complet 6.8/6.9 :
    1. Cree une save via API (subprocess interactif character_creation
       n'est pas scriptable sans LLM mock)
    2. `shinobi list` via subprocess voit la save
    3. `shinobi export` via subprocess cree l'archive
    4. `shinobi delete` (avec confirm 'y') efface la save
    5. `shinobi list` montre vide
    6. `shinobi import` restaure
    7. `shinobi list` voit la save de nouveau
    """
    env, saves_dir = isolated_env

    # 1. Cree la save via API (avec settings pointant le tmp_path)
    from shinobi.config import settings
    original_path = settings.saves_path
    try:
        settings.saves_path = str(saves_dir)
        char = _make_character()
        world = create_default_world(
            profile=CanonicityProfile.default(), starting_year=12,
        )
        sid = save_module.create_save(char, world)
    finally:
        settings.saves_path = original_path

    # 2. list via subprocess voit la save
    cmd = _python_cli(saves_dir) + ["list"]
    result = subprocess.run(
        cmd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    assert result.returncode == 0
    assert sid in result.stdout

    # 3. export via subprocess
    archive = tmp_path / "subproc_export"
    cmd = _python_cli(saves_dir) + ["export", sid, str(archive)]
    result = subprocess.run(
        cmd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    assert result.returncode == 0
    archive_real = archive.with_suffix(".shinosave")
    assert archive_real.exists()

    # 4. delete via subprocess (avec confirm 'y')
    cmd = _python_cli(saves_dir) + ["delete", sid]
    result = subprocess.run(
        cmd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        input="y\n",
    )
    assert result.returncode == 0

    # 5. list montre vide
    cmd = _python_cli(saves_dir) + ["list"]
    result = subprocess.run(
        cmd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    assert result.returncode == 0
    assert "No save" in result.stdout

    # 6. import restaure
    cmd = _python_cli(saves_dir) + ["import", str(archive_real)]
    result = subprocess.run(
        cmd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    assert result.returncode == 0
    assert sid in result.stdout

    # 7. list voit la save de nouveau
    cmd = _python_cli(saves_dir) + ["list"]
    result = subprocess.run(
        cmd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    assert result.returncode == 0
    assert sid in result.stdout
