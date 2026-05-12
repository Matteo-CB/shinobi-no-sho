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
from shinobi.i18n import t
from shinobi.persistence import saves as save_module

console = Console()


def main_loop() -> None:
    """Boucle principale du jeu : ne quitte que si l'utilisateur le demande."""
    console.clear()
    console.print(banner(t("cli.app.banner.title"), t("cli.app.banner.subtitle")))
    while True:
        if not _menu_iteration():
            break
    console.print(f"[dim]{t('cli.app.bye')}[/dim]")


def _menu_iteration() -> bool:
    """Une iteration du menu. Retourne False pour quitter."""
    saves = save_module.list_saves()
    last = sorted(saves, key=lambda s: s.last_played, reverse=True)[0] if saves else None

    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan", justify="right")
    table.add_column()
    table.add_row("1", t("cli.menu.new_game"))
    if last:
        table.add_row(
            "2",
            t(
                "cli.menu.continue_with_save",
                name=last.character_name,
                year=last.current_year,
                rank=last.rank,
                turns=last.total_turns,
            ),
        )
    else:
        table.add_row("2", t("cli.menu.continue_disabled"))
    table.add_row("3", t("cli.menu.manage_saves"))
    table.add_row("4", t("cli.menu.config_label"))
    table.add_row("q", t("cli.menu.quit_label"))
    console.print(Panel(table, title=f"[{COLOR_TITLE}]{t('cli.menu.title')}", border_style="magenta"))

    choice = (
        Prompt.ask(
            f"[bold cyan]{t('cli.menu.choice_prompt')}[/bold cyan]",
            default="2" if last else "1",
        ).strip().lower()
    )

    if choice == "1":
        from shinobi.cli.character_creation import run_character_creation

        save_id = run_character_creation()
        if save_id:
            _maybe_play_now(save_id)
    elif choice == "2":
        if last is None:
            console.print(f"[yellow]{t('cli.menu.no_save_to_continue')}[/yellow]")
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
        console.print(f"[red]{t('cli.menu.invalid_choice', choice=choice)}[/red]")
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
        console.print(f"[yellow]{t('cli.menu.interrupted')}[/yellow]")
    except Exception as exc:
        console.print(
            f"[red]{t('cli.menu.session_error', error_type=type(exc).__name__, error=str(exc))}[/red]"
        )


def _maybe_play_now(save_id: str) -> None:
    from rich.prompt import Confirm

    if Confirm.ask(t("cli.menu.confirm_play_now"), default=True):
        _start_play(save_id)


def _pick_save(saves) -> str | None:
    table = Table(title=t("cli.menu.saves_pick.title"), header_style=COLOR_TITLE)
    table.add_column(t("cli.menu.saves_pick.col_num"), style="bold cyan", justify="right")
    table.add_column(t("cli.menu.saves_pick.col_save_id"))
    table.add_column(t("cli.menu.saves_pick.col_character"))
    table.add_column(t("cli.menu.saves_pick.col_year"), justify="right")
    table.add_column(t("cli.menu.saves_pick.col_rank"))
    table.add_column(t("cli.menu.saves_pick.col_turns"), justify="right")
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
    sub = Prompt.ask(
        f"[bold cyan]{t('cli.menu.saves_pick.prompt_num_or_id')}[/bold cyan]",
        default="1",
    ).strip()
    try:
        idx = int(sub) - 1
        if 0 <= idx < len(sorted_saves):
            return sorted_saves[idx].save_id
    except ValueError:
        pass
    if any(s.save_id == sub for s in sorted_saves):
        return sub
    console.print(f"[red]{t('cli.menu.saves_pick.invalid')}[/red]")
    return None


def _manage_saves_submenu() -> None:
    """Sous-menu pour gerer les saves : lister, charger, supprimer, exporter, importer."""
    while True:
        saves = save_module.list_saves()
        table = Table.grid(padding=(0, 2))
        table.add_column(style="bold cyan", justify="right")
        table.add_column()
        table.add_row("1", t("cli.menu.manage.list_all"))
        table.add_row("2", t("cli.menu.manage.load"))
        table.add_row("3", t("cli.menu.manage.create"))
        table.add_row("4", t("cli.menu.manage.delete"))
        table.add_row("5", t("cli.menu.manage.duplicate"))
        table.add_row("6", t("cli.menu.manage.export"))
        table.add_row("7", t("cli.menu.manage.import"))
        table.add_row("b", t("cli.menu.manage.back"))
        console.print(
            Panel(
                table,
                title=f"[{COLOR_TITLE}]{t('cli.menu.manage.title')}",
                border_style="cyan",
            )
        )

        choice = Prompt.ask(
            f"[bold cyan]{t('cli.menu.choice_prompt')}[/bold cyan]",
            default="1",
        ).strip().lower()

        if choice == "1":
            _list_saves(saves)
        elif choice == "2":
            if not saves:
                console.print(f"[yellow]{t('cli.menu.no_save')}[/yellow]")
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
                console.print(f"[yellow]{t('cli.menu.no_save_to_delete')}[/yellow]")
                continue
            sid = _pick_save(saves)
            if sid:
                from rich.prompt import Confirm

                if Confirm.ask(
                    t("cli.menu.confirm_delete_red", save_id=sid),
                    default=False,
                ):
                    save_module.delete_save(sid)
                    console.print(
                        f"[green]{t('cli.app.save_deleted', save_id=sid)}[/green]"
                    )
        elif choice == "5":
            if not saves:
                console.print(f"[yellow]{t('cli.menu.no_save_to_duplicate')}[/yellow]")
                continue
            sid = _pick_save(saves)
            if sid:
                label = Prompt.ask(
                    f"[bold cyan]{t('cli.menu.duplicate.label_prompt')}[/bold cyan]",
                    default=f"branche_{sid}",
                )
                new_id = save_module.duplicate_save(sid, label)
                console.print(
                    f"[green]{t('cli.menu.duplicate.success', save_id=new_id)}[/green]"
                )
        elif choice == "6":
            if not saves:
                console.print(f"[yellow]{t('cli.menu.no_save_to_export')}[/yellow]")
                continue
            sid = _pick_save(saves)
            if sid:
                from pathlib import Path as _Path

                target = Prompt.ask(
                    f"[bold cyan]{t('cli.menu.export.path_prompt')}[/bold cyan]",
                    default=f".\\{sid}.shinosave",
                )
                final = save_module.export_save(sid, _Path(target))
                console.print(
                    f"[green]{t('cli.menu.export.success', path=final)}[/green]"
                )
        elif choice == "7":
            from pathlib import Path as _Path

            archive = Prompt.ask(
                f"[bold cyan]{t('cli.menu.import.path_prompt')}[/bold cyan]"
            )
            try:
                imported = save_module.import_save(_Path(archive.strip()))
                console.print(
                    f"[green]{t('cli.menu.import.success', save_id=imported)}[/green]"
                )
            except Exception as exc:
                console.print(
                    f"[red]{t('cli.menu.import.error', error_type=type(exc).__name__, error=str(exc))}[/red]"
                )
        elif choice in ("b", "back", "retour"):
            return
        else:
            console.print(f"[red]{t('cli.menu.invalid_choice', choice=choice)}[/red]")


def _list_saves(saves) -> None:
    if not saves:
        console.print(
            Panel(
                t("cli.menu.list.empty"),
                title=t("cli.menu.list.title"),
            )
        )
        return
    table = Table(
        title=t("cli.menu.list.title_count", count=len(saves)),
        header_style=COLOR_TITLE,
    )
    table.add_column(t("cli.menu.saves_pick.col_save_id"))
    table.add_column(t("cli.menu.saves_pick.col_character"))
    table.add_column(t("cli.menu.saves_pick.col_age"), justify="right")
    table.add_column(t("cli.menu.saves_pick.col_year"), justify="right")
    table.add_column(t("cli.menu.saves_pick.col_village"))
    table.add_column(t("cli.menu.saves_pick.col_rank"))
    table.add_column(t("cli.menu.saves_pick.col_turns"), justify="right")
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
