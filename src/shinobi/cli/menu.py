"""Menu principal en boucle.

Usage : `shinobi` (sans argument) lance la boucle qui ne quitte que sur demande
explicite du joueur.
"""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from shinobi.cli.display import COLOR_TITLE, banner
from shinobi.persistence import saves as save_module

console = Console()


def main_loop() -> None:
    """Boucle principale du jeu : ne quitte que si l'utilisateur le demande."""
    console.clear()
    console.print(banner("Shinobi no Sho", "Le livre du shinobi"))
    while True:
        if not _menu_iteration():
            break
    console.print("[dim]Au revoir.[/dim]")


def _menu_iteration() -> bool:
    """Une iteration du menu. Retourne False pour quitter."""
    saves = save_module.list_saves()
    last = sorted(saves, key=lambda s: s.last_played, reverse=True)[0] if saves else None

    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan", justify="right")
    table.add_column()
    table.add_row("1", "Nouvelle partie")
    if last:
        table.add_row(
            "2",
            f"Continuer : [yellow]{last.character_name}[/yellow] (an {last.current_year}, {last.rank}, tour {last.total_turns})",
        )
    else:
        table.add_row("2", "[dim]Continuer (aucune partie)[/dim]")
    table.add_row(
        "3", "Gerer les saves [dim](lister, charger, supprimer, exporter, importer)[/dim]"
    )
    table.add_row("4", "Configuration")
    table.add_row("q", "Quitter")
    console.print(Panel(table, title=f"[{COLOR_TITLE}]Menu", border_style="magenta"))

    choice = (
        Prompt.ask("[bold cyan]Choix[/bold cyan]", default="2" if last else "1").strip().lower()
    )

    if choice == "1":
        from shinobi.cli.character_creation import run_character_creation

        save_id = run_character_creation()
        if save_id:
            _maybe_play_now(save_id)
    elif choice == "2":
        if last is None:
            console.print("[yellow]Aucune partie a continuer. Cree d'abord un personnage.[/yellow]")
            return True
        _start_play(last.save_id)
    elif choice == "3":
        _manage_saves_submenu()
    elif choice == "4":
        from shinobi.cli.app import config_cmd

        config_cmd()
    elif choice in ("q", "quit", "quitter", "exit"):
        return False
    else:
        console.print(f"[red]Choix invalide : {choice}[/red]")
    return True


def show_menu() -> None:
    """Compatibilite : lance la boucle complete."""
    main_loop()


def _start_play(save_id: str) -> None:
    """Lance play_session puis revient proprement au menu."""
    from shinobi.cli.play import play_session

    try:
        play_session(save_id)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrompu, retour au menu.[/yellow]")
    except Exception as exc:
        console.print(f"[red]Erreur durant la session : {type(exc).__name__}: {exc}[/red]")


def _maybe_play_now(save_id: str) -> None:
    from rich.prompt import Confirm

    if Confirm.ask("Lancer la partie maintenant ?", default=True):
        _start_play(save_id)


def _pick_save(saves) -> str | None:
    table = Table(title="Saves disponibles", header_style=COLOR_TITLE)
    table.add_column("#", style="bold cyan", justify="right")
    table.add_column("Save id")
    table.add_column("Personnage")
    table.add_column("Annee", justify="right")
    table.add_column("Rang")
    table.add_column("Tours", justify="right")
    sorted_saves = sorted(saves, key=lambda s: s.last_played, reverse=True)
    for i, s in enumerate(sorted_saves, start=1):
        table.add_row(
            str(i),
            s.save_id,
            s.character_name,
            str(s.current_year),
            s.rank,
            str(s.total_turns),
        )
    console.print(table)
    sub = Prompt.ask("[bold cyan]Numero ou id[/bold cyan]", default="1").strip()
    try:
        idx = int(sub) - 1
        if 0 <= idx < len(sorted_saves):
            return sorted_saves[idx].save_id
    except ValueError:
        pass
    if any(s.save_id == sub for s in sorted_saves):
        return sub
    console.print("[red]Selection invalide.[/red]")
    return None


def _manage_saves_submenu() -> None:
    """Sous-menu pour gerer les saves : lister, charger, supprimer, exporter, importer."""
    while True:
        saves = save_module.list_saves()
        table = Table.grid(padding=(0, 2))
        table.add_column(style="bold cyan", justify="right")
        table.add_column()
        table.add_row("1", "Lister toutes les saves")
        table.add_row("2", "Charger une save")
        table.add_row("3", "Creer une nouvelle save (vers nouveau personnage)")
        table.add_row("4", "Supprimer une save")
        table.add_row("5", "Dupliquer une save (point de bifurcation)")
        table.add_row("6", "Exporter une save (.shinosave)")
        table.add_row("7", "Importer une save")
        table.add_row("b", "Retour")
        console.print(Panel(table, title=f"[{COLOR_TITLE}]Gestion des saves", border_style="cyan"))

        choice = Prompt.ask("[bold cyan]Choix[/bold cyan]", default="1").strip().lower()

        if choice == "1":
            _list_saves(saves)
        elif choice == "2":
            if not saves:
                console.print("[yellow]Aucune save.[/yellow]")
                continue
            sid = _pick_save(saves)
            if sid:
                _start_play(sid)
                return  # apres une session de jeu, retour au menu principal
        elif choice == "3":
            from shinobi.cli.character_creation import run_character_creation

            new_id = run_character_creation()
            if new_id:
                _maybe_play_now(new_id)
                return
        elif choice == "4":
            if not saves:
                console.print("[yellow]Aucune save a supprimer.[/yellow]")
                continue
            sid = _pick_save(saves)
            if sid:
                from rich.prompt import Confirm

                if Confirm.ask(f"[red]Supprimer[/red] la save {sid} ?", default=False):
                    save_module.delete_save(sid)
                    console.print(f"[green]Save {sid} supprimee.[/green]")
        elif choice == "5":
            if not saves:
                console.print("[yellow]Aucune save a dupliquer.[/yellow]")
                continue
            sid = _pick_save(saves)
            if sid:
                label = Prompt.ask(
                    "[bold cyan]Label de la nouvelle branche[/bold cyan]", default=f"branche_{sid}"
                )
                new_id = save_module.duplicate_save(sid, label)
                console.print(f"[green]Save dupliquee : {new_id}[/green]")
        elif choice == "6":
            if not saves:
                console.print("[yellow]Aucune save a exporter.[/yellow]")
                continue
            sid = _pick_save(saves)
            if sid:
                from pathlib import Path as _Path

                target = Prompt.ask(
                    "[bold cyan]Chemin du fichier .shinosave[/bold cyan]",
                    default=f".\\{sid}.shinosave",
                )
                final = save_module.export_save(sid, _Path(target))
                console.print(f"[green]Save exportee : {final}[/green]")
        elif choice == "7":
            from pathlib import Path as _Path

            archive = Prompt.ask("[bold cyan]Chemin du .shinosave[/bold cyan]")
            try:
                imported = save_module.import_save(_Path(archive.strip()))
                console.print(f"[green]Save importee : {imported}[/green]")
            except Exception as exc:
                console.print(f"[red]Erreur import : {type(exc).__name__}: {exc}[/red]")
        elif choice in ("b", "back", "retour"):
            return
        else:
            console.print(f"[red]Choix invalide : {choice}[/red]")


def _list_saves(saves) -> None:
    if not saves:
        console.print(Panel("[dim]Aucune save trouvee.[/dim]", title="Saves"))
        return
    table = Table(title=f"Saves ({len(saves)})", header_style=COLOR_TITLE)
    table.add_column("Save id")
    table.add_column("Personnage")
    table.add_column("Age", justify="right")
    table.add_column("Annee", justify="right")
    table.add_column("Village")
    table.add_column("Rang")
    table.add_column("Tours", justify="right")
    for s in sorted(saves, key=lambda s: s.last_played, reverse=True):
        table.add_row(
            s.save_id,
            s.character_name,
            str(s.character_age),
            str(s.current_year),
            s.village,
            s.rank,
            str(s.total_turns),
        )
    console.print(table)
