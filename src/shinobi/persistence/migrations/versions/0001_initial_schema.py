"""Initial schema (Phase 4 baseline).

Cree toutes les tables present dans schema.sql au moment ou Alembic est
introduit (Phase 4.2). Pour les saves existantes pre-Alembic, ce script
fait un `CREATE TABLE IF NOT EXISTS` -> idempotent, peut etre stamp.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-05-07
"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0001_initial_schema"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_SCHEMA_PATH = (
    Path(__file__).resolve().parents[3] / "schema.sql"
)


def upgrade() -> None:
    """Applique le schema.sql complet sur la save cible.

    Idempotent grace aux `CREATE TABLE IF NOT EXISTS`. Permet aux saves
    pre-Alembic d'etre stampees a HEAD sans rien casser.
    """
    sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    # Execute statement par statement pour eviter les pbm de transactions
    # batch dans sqlite + alembic.
    for statement in sql.split(";"):
        stmt = statement.strip()
        if not stmt:
            continue
        op.execute(stmt)


def downgrade() -> None:
    """Drop toutes les tables (irreversible : on perd la save).

    Defensive : on n'execute pas ce downgrade en production. Reserve aux
    tests de migrations / dev local.
    """
    tables = (
        "active_missions",
        "save_meta",
        "techniques_in_progress",
        "techniques_known",
        "knowledge",
        "rumors",
        "scheduled_events",
        "npc_states",
        "relationships",
        "breadcrumbs",
        "goals",
        "turns",
        "world",
        "character",
    )
    for table in tables:
        op.execute(f"DROP TABLE IF EXISTS {table}")
