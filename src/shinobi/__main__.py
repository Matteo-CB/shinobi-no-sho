"""Point d'entree CLI principal."""

from __future__ import annotations

from shinobi.cli.app import app


def main() -> None:
    """Lance l'application Typer racine."""
    app()


if __name__ == "__main__":
    main()
