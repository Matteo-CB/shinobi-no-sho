"""Alembic env.py - configure pour les bases SQLite per-save.

Particularite Phase 4 : 1 base SQLite par save (data/saves/<id>/state.sqlite).
On lit la save cible via `-x save_id=<id>` ou par defaut la 1ere save trouvee.

Usage CLI :
    alembic -c src/shinobi/persistence/alembic.ini \\
        -x save_id=<id> upgrade head

Pour scripter un upgrade programmatique (cf saves.upgrade_save_schema), le
helper Python passe par alembic.command.upgrade() en passant un Config
custom-build avec sqlalchemy.url derive du save_id.
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Permet d'importer shinobi.* depuis src/
ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _resolve_sqlite_url() -> str:
    """Determine l'URL SQLite cible.

    Priorite :
    1. `-x save_id=<id>` -> data/saves/<id>/state.sqlite
    2. `-x state_path=<path>` -> path direct
    3. Default sqlalchemy.url du config (placeholder.sqlite : echoue
       proprement plutot que de muter une vraie DB par accident)
    """
    xargs = context.get_x_argument(as_dictionary=True)
    save_id = xargs.get("save_id")
    state_path = xargs.get("state_path")

    if state_path:
        return f"sqlite:///{Path(state_path).resolve()}"
    if save_id:
        from shinobi.persistence import saves as save_module
        path = save_module._state_path(save_id)  # noqa: SLF001
        return f"sqlite:///{path.resolve()}"
    return config.get_main_option("sqlalchemy.url", "sqlite:///placeholder.sqlite")


target_metadata = None  # schema.sql gere les tables, pas SQLAlchemy ORM


def run_migrations_offline() -> None:
    """Run migrations sans connection live (genere SQL only)."""
    url = _resolve_sqlite_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # SQLite : ALTER TABLE limited, batch mode
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations avec connection live."""
    section = config.get_section(config.config_ini_section, {}) or {}
    section["sqlalchemy.url"] = _resolve_sqlite_url()
    connectable = engine_from_config(
        section, prefix="sqlalchemy.", poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
