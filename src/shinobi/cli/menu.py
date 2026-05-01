"""Menu principal."""

from __future__ import annotations

import sys

import typer
from rich.console import Console
from rich.panel import Panel

from shinobi import __version__
from shinobi.persistence import saves as save_module

console = Console()


def show_menu() -> None:
    """Affiche le menu principal et boucle jusqu'a une action."""
    saves = save_module.list_saves()
    last = saves[0] if saves else None
    panel_text = "\n".join(
        [
            "1. Nouvelle partie",
            (
                f"2. Continuer la derniere partie : {last.character_name}, an {last.current_year}, {last.rank}"
                if last
                else "2. Continuer (aucune partie disponible)"
            ),
            "3. Choisir une partie",
            "4. Configuration",
            "5. Quitter",
        ]
    )
    console.print(
        Panel.fit(
            panel_text,
            title=f"Shinobi no Sho {__version__}",
            subtitle="Le livre du shinobi",
        )
    )
    choice = typer.prompt("Choix").strip()
    if choice == "1":
        from shinobi.cli.character_creation import run_character_creation

        run_character_creation()
    elif choice == "2":
        if last is None:
            console.print("Aucune partie a continuer.")
            return
        from shinobi.cli.play import play_session

        play_session(last.save_id)
    elif choice == "3":
        if not saves:
            console.print("Aucune save disponible.")
            return
        for i, s in enumerate(saves, start=1):
            console.print(f"{i}. {s.save_id} : {s.character_name} (an {s.current_year})")
        sub = typer.prompt("Numero").strip()
        try:
            idx = int(sub) - 1
            sid = saves[idx].save_id
        except (ValueError, IndexError):
            console.print("Selection invalide.")
            return
        from shinobi.cli.play import play_session

        play_session(sid)
    elif choice == "4":
        from shinobi.cli.app import config_cmd

        config_cmd()
    else:
        console.print("Au revoir.")
        sys.exit(0)
