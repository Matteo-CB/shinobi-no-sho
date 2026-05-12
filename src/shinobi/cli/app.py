"""Application Typer racine."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel

from shinobi import __version__
from shinobi.cli.menu import main_loop
from shinobi.config import settings
from shinobi.errors import SaveNotFoundError
from shinobi.i18n import t
from shinobi.persistence import saves as save_module

app = typer.Typer(
    add_completion=False,
    no_args_is_help=False,
    invoke_without_command=True,
    help="Shinobi no Sho. Le livre du shinobi.",
)
console = Console()


@app.callback(invoke_without_command=True)
def root(ctx: typer.Context) -> None:
    """Si appele sans sous-commande, lance la boucle de menu.

    Phase i18n.2 :
    - Sur TOUTES les invocations : init silencieuse du runtime depuis
      preferences.json (les subcommands non-interactifs comme delete/list/
      version/config/serve heritent de la bonne langue sans bloquer).
    - Sur l'entree interactive (shinobi sans sous-commande, shinobi new,
      shinobi play) : si first_launch_completed=False, affiche le picker.
    """
    # Init runtime silencieuse pour toutes les invocations
    try:
        from shinobi.i18n import initialize_from_preferences

        initialize_from_preferences()
    except Exception as exc:
        console.print(
            f"[dim]{t('cli.app.bootstrap.i18n_skipped', error=type(exc).__name__)}[/dim]",
        )

    # Picker interactif uniquement pour shinobi/play/new (sessions humaines
    # devant un terminal). Skip pour serve (serveur potentiellement daemon),
    # delete/list/version/config/export/import (scriptables) : ces commandes
    # ne doivent jamais bloquer sur un prompt.
    interactive_entries = {None, "play", "new"}
    if ctx.invoked_subcommand in interactive_entries:
        try:
            from shinobi.cli.language_picker import (
                maybe_show_first_launch_picker,
            )

            maybe_show_first_launch_picker(console=console)
        except Exception as exc:
            console.print(
                f"[dim]{t('cli.app.bootstrap.picker_skipped', error=type(exc).__name__)}[/dim]",
            )

    if ctx.invoked_subcommand is None:
        # Bootstrap RAG : si l'index est manquant ou desynchronise du canon, telecharge
        # depuis GitHub Releases (fallback build local). No-op si l'index est deja OK.
        try:
            from shinobi.rag.bootstrap import bootstrap_index

            bootstrap_index(console=console)
        except Exception as exc:
            console.print(
                f"[dim]{t('cli.app.bootstrap.rag_skipped', error=type(exc).__name__)}[/dim]"
            )
        # Bootstrap LLM : lance llama-server en background s'il n'est pas deja up.
        # No-op si le serveur repond deja. Fallback silencieux si llama-server ou
        # le modele est introuvable (le jeu marche en mode mecanique).
        try:
            from shinobi.llm.server_bootstrap import ensure_llm_server

            ensure_llm_server(console=console)
        except Exception as exc:
            console.print(
                f"[dim]{t('cli.app.bootstrap.llm_skipped', error=type(exc).__name__)}[/dim]"
            )
        main_loop()


@app.command()
def play(save_id: str | None = typer.Option(None, help="Save_id a charger.")) -> None:
    """Reprend la derniere partie ou un save specifique."""
    from shinobi.cli.play import play_session

    if save_id is None:
        items = save_module.list_saves()
        if not items:
            console.print(t("cli.app.no_save_exists"))
            raise typer.Exit(0)
        save_id = sorted(items, key=lambda s: s.last_played, reverse=True)[0].save_id
    play_session(save_id)


@app.command(name="new")
def new() -> None:
    """Creation d'un nouveau personnage."""
    from shinobi.cli.character_creation import run_character_creation

    run_character_creation()


@app.command(name="list")
def list_cmd() -> None:
    """Liste toutes les saves."""
    items = save_module.list_saves()
    if not items:
        console.print(Panel(t("cli.app.list.empty"), title=t("cli.app.list.title")))
        return
    lines = [
        t(
            "cli.app.list.row",
            save_id=s.save_id,
            character_name=s.character_name,
            age=s.character_age,
            year=s.current_year,
            date=s.current_date,
            village=s.village,
            rank=s.rank,
        )
        for s in items
    ]
    console.print(
        Panel(
            "\n".join(lines),
            title=t("cli.app.list.title_count", count=len(items)),
        )
    )


@app.command(name="delete")
def delete_cmd(save_id: str) -> None:
    """Supprime une save."""
    if not typer.confirm(t("cli.app.confirm_delete_save", save_id=save_id)):
        raise typer.Exit(0)
    try:
        save_module.delete_save(save_id)
    except SaveNotFoundError:
        console.print(f"[red]{t('cli.app.save_not_found', save_id=save_id)}[/red]")
        raise typer.Exit(1)
    console.print(t("cli.app.save_deleted", save_id=save_id))


@app.command(name="export")
def export_cmd(save_id: str, out_path: Path = typer.Argument(...)) -> None:
    """Exporte une save vers une archive .shinosave."""
    final = save_module.export_save(save_id, out_path)
    console.print(t("cli.app.save_exported", path=final))


@app.command(name="import")
def import_cmd(archive: Path) -> None:
    """Importe une save .shinosave."""
    sid = save_module.import_save(archive)
    console.print(t("cli.app.save_imported", save_id=sid))


@app.command(name="version")
def version_cmd() -> None:
    """Affiche la version."""
    console.print(t("cli.app.version_line", version=__version__))


@app.command(name="serve")
def serve_cmd(
    host: str = typer.Option("127.0.0.1", help="Adresse d'ecoute du serveur."),
    port: int = typer.Option(8000, help="Port d'ecoute."),
    reload: bool = typer.Option(False, help="Auto-reload (dev)."),
    log_level: str = typer.Option("info", help="Niveau de log uvicorn."),
) -> None:
    """Lance le serveur FastAPI Phase 9 (API HTTP locale).

    Le moteur reste local, sans authentification. L'API expose saves, play,
    canon, health. Documentation OpenAPI sur /docs.
    """
    import uvicorn

    console.print(
        Panel.fit(
            t("cli.app.serve.panel", host=host, port=port),
            title=t("cli.app.serve.title"),
            border_style="cyan",
        )
    )
    uvicorn.run(
        "shinobi.api:app",
        host=host,
        port=port,
        reload=reload,
        log_level=log_level,
    )


@app.command(name="config")
def config_cmd() -> None:
    """Affiche la config courante."""
    console.print(
        Panel.fit(
            "\n".join(
                [
                    t("cli.app.config.llm_backend", url=settings.llm_backend_url),
                    t("cli.app.config.llm_model", name=settings.llm_model_name),
                    t("cli.app.config.llm_path", path=settings.llm_model_path),
                    t(
                        "cli.app.config.embeddings",
                        name=settings.embeddings_model_name,
                        device=settings.embeddings_device,
                    ),
                    t("cli.app.config.saves_dir", path=settings.saves_dir),
                    t(
                        "cli.app.config.canonical_dir",
                        path=settings.canonical_data_dir,
                    ),
                    t(
                        "cli.app.config.profile",
                        profile=settings.canonicity_profile_sources,
                    ),
                ]
            ),
            title=t("cli.app.config.title"),
            border_style="cyan",
        )
    )


def main() -> None:
    """Entry point en ligne de commande."""
    app()


if __name__ == "__main__":
    main()
