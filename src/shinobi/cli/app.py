"""Application Typer racine."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel

from shinobi import __version__
from shinobi.cli.character_creation import run_character_creation
from shinobi.cli.menu import show_menu
from shinobi.cli.play import play_session
from shinobi.config import settings
from shinobi.errors import SaveNotFoundError
from shinobi.persistence import saves as save_module

app = typer.Typer(
    add_completion=False,
    no_args_is_help=False,
    help="Shinobi no Sho. Le livre du shinobi.",
)
console = Console()


@app.callback(invoke_without_command=True)
def root(ctx: typer.Context) -> None:
    """Si appele sans sous-commande, affiche le menu."""
    if ctx.invoked_subcommand is None:
        show_menu()


@app.command()
def play(save_id: str | None = typer.Option(None, help="Save_id a charger.")) -> None:
    """Lance ou reprend une partie."""
    if save_id is None:
        items = save_module.list_saves()
        if not items:
            console.print("Aucune save existante. Lance `shinobi new` pour creer un personnage.")
            raise typer.Exit(0)
        save_id = sorted(items, key=lambda s: s.last_played, reverse=True)[0].save_id
    play_session(save_id)


@app.command(name="new")
def new() -> None:
    """Creation d'un nouveau personnage."""
    run_character_creation()


@app.command(name="list")
def list_cmd() -> None:
    """Liste toutes les saves."""
    items = save_module.list_saves()
    if not items:
        console.print(Panel("Aucune save trouvee.", title="Saves"))
        return
    lines = [
        f"- {s.save_id} : {s.character_name}, {s.character_age} ans, an {s.current_year} {s.current_date} ({s.village}, {s.rank})"
        for s in items
    ]
    console.print(Panel("\n".join(lines), title=f"Saves ({len(items)})"))


@app.command(name="delete")
def delete_cmd(save_id: str) -> None:
    """Supprime une save."""
    if not typer.confirm(f"Supprimer definitivement la save {save_id} ?"):
        raise typer.Exit(0)
    try:
        save_module.delete_save(save_id)
    except SaveNotFoundError:
        console.print(f"[red]Save introuvable : {save_id}[/red]")
        raise typer.Exit(1)
    console.print(f"Save {save_id} supprimee.")


@app.command(name="export")
def export_cmd(save_id: str, out_path: Path = typer.Argument(...)) -> None:
    """Exporte une save vers une archive .shinosave."""
    final = save_module.export_save(save_id, out_path)
    console.print(f"Save exportee vers {final}")


@app.command(name="import")
def import_cmd(archive: Path) -> None:
    """Importe une save .shinosave."""
    sid = save_module.import_save(archive)
    console.print(f"Save importee : {sid}")


@app.command(name="version")
def version_cmd() -> None:
    """Affiche la version."""
    console.print(f"Shinobi no Sho {__version__}")


@app.command(name="config")
def config_cmd() -> None:
    """Affiche la config courante."""
    console.print(
        Panel.fit(
            "\n".join(
                [
                    f"LLM backend : {settings.llm_backend_url}",
                    f"Modele : {settings.llm_model_name}",
                    f"Path GGUF : {settings.llm_model_path}",
                    f"Embeddings : {settings.embeddings_model_name} ({settings.embeddings_device})",
                    f"Saves dir : {settings.saves_dir}",
                    f"Canonical dir : {settings.canonical_data_dir}",
                    f"Profil canonicite : {settings.canonicity_profile_sources}",
                ]
            ),
            title="Configuration",
        )
    )


def main() -> None:
    """Entry point en ligne de commande."""
    app()


if __name__ == "__main__":
    main()
