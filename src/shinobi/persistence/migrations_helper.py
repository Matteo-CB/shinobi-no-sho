"""Helpers pour gerer les migrations Alembic des saves Phase 4.2.

Usage typique programmatique :
    from shinobi.persistence import migrations_helper as M
    M.upgrade_save(save_id="my_save")  # bring it to HEAD
    M.current_revision(save_id="my_save")  # quel niveau ?
    M.stamp_save(save_id="my_save", revision="head")  # marque sans appliquer

Spec doc 02 §11 (4.2) : Alembic configure pour gerer les revisions de
schema. Comme on a 1 SQLite par save, le wrapper construit un Config
Alembic dynamique avec sqlalchemy.url derive de save_id.
"""
from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine

_PERSISTENCE_DIR = Path(__file__).resolve().parent
_ALEMBIC_INI = _PERSISTENCE_DIR / "alembic.ini"
_MIGRATIONS_DIR = _PERSISTENCE_DIR / "migrations"


def _build_config(save_id: str | None = None, state_path: Path | None = None) -> Config:
    """Construit un Config Alembic ciblant la save donnee."""
    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))

    # On determine le state_path :
    # - si state_path fourni : direct
    # - sinon si save_id : derive via saves._state_path
    # - sinon : URL placeholder (echec safe, ne mute aucune vraie save)
    if state_path is not None:
        url = f"sqlite:///{Path(state_path).resolve()}"
    elif save_id is not None:
        # Import lazy pour eviter cyclic dependency saves <-> migrations_helper
        from shinobi.persistence import saves as save_module
        url = f"sqlite:///{save_module._state_path(save_id).resolve()}"  # noqa: SLF001
    else:
        url = "sqlite:///placeholder.sqlite"

    cfg.set_main_option("sqlalchemy.url", url)
    if save_id is not None:
        cfg.cmd_opts = type("X", (), {"x": [f"save_id={save_id}"]})()
    elif state_path is not None:
        cfg.cmd_opts = type("X", (), {"x": [f"state_path={state_path}"]})()
    return cfg


def upgrade_save(save_id: str | None = None, *, state_path: Path | None = None,
                 revision: str = "head") -> None:
    """Upgrade le schema d'une save a la revision donnee (default : head).

    Idempotent : si la save est deja a HEAD, ne fait rien.
    """
    cfg = _build_config(save_id=save_id, state_path=state_path)
    command.upgrade(cfg, revision)


def downgrade_save(save_id: str | None = None, *, state_path: Path | None = None,
                   revision: str = "-1") -> None:
    """Downgrade le schema d'une save (par default : 1 step en arriere).

    Defensive : downgrade peut perdre des donnees. Utiliser avec precaution.
    """
    cfg = _build_config(save_id=save_id, state_path=state_path)
    command.downgrade(cfg, revision)


def stamp_save(save_id: str | None = None, *, state_path: Path | None = None,
               revision: str = "head") -> None:
    """Stampe la save a la revision sans appliquer les migrations.

    Cas d'usage : saves pre-Alembic dont le schema correspond deja a une
    version donnee (creation par schema.sql direct). On les marque a HEAD
    pour qu'Alembic ne tente pas de re-appliquer la baseline.
    """
    cfg = _build_config(save_id=save_id, state_path=state_path)
    command.stamp(cfg, revision)


def current_revision(save_id: str | None = None, *,
                     state_path: Path | None = None) -> str | None:
    """Retourne la revision Alembic courante de la save (None si non stampee).

    Lit la table `alembic_version` directement pour ne pas declencher les
    migrations.
    """
    if state_path is not None:
        url = f"sqlite:///{Path(state_path).resolve()}"
    elif save_id is not None:
        from shinobi.persistence import saves as save_module
        url = f"sqlite:///{save_module._state_path(save_id).resolve()}"  # noqa: SLF001
    else:
        return None
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            return ctx.get_current_revision()
    finally:
        engine.dispose()


__all__ = [
    "current_revision",
    "downgrade_save",
    "stamp_save",
    "upgrade_save",
]
