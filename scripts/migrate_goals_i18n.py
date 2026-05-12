"""Phase i18n.8 : migration des goals existants (CLI standalone).

Pour chaque save sur disque, parcourt les goals declares avant Phase 8
(donc avec `description_player_original_language = None` et/ou
`description_player_translated = {}`) et :

1. Detecte la langue de `description_player` via PlayerTranslator (heuristique
   fallback si LLM indispo).
2. Si la langue detectee != lang config courante, traduit `description_player`
   vers la lang config et stocke dans `description_player_translated`.
3. Re-serialize le goal et fait un INSERT OR REPLACE.

Usage :

    python scripts/migrate_goals_i18n.py            # tous les saves
    python scripts/migrate_goals_i18n.py <save_id>  # un save specifique
    python scripts/migrate_goals_i18n.py --dry-run  # rapport, pas d'ecriture

La logique de migration est partagee avec `POST /play/{id}/initialize`
via `shinobi.i18n.goal_migration.migrate_save_goals`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

# Permet d'executer le script depuis n'importe ou via `python scripts/...`
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from shinobi.config import settings  # noqa: E402
from shinobi.i18n.catalog import (  # noqa: E402
    get_active_language,
    initialize_from_preferences,
)
from shinobi.i18n.goal_migration import migrate_save_goals  # noqa: E402
from shinobi.i18n.player_translator import PlayerTranslator  # noqa: E402


def _iter_save_ids() -> list[str]:
    if not settings.saves_dir.exists():
        return []
    return sorted(
        p.name for p in settings.saves_dir.iterdir()
        if (p / "meta.json").exists()
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migration i18n.8 des goals.")
    parser.add_argument(
        "save_id", nargs="?", default=None,
        help="ID du save a migrer. Si omis, migre tous les saves.",
    )
    parser.add_argument(
        "--target-lang", default=None,
        help="Langue cible (defaut : langue config courante).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Affiche le rapport sans ecrire.",
    )
    parser.add_argument(
        "--no-llm", action="store_true",
        help=(
            "Desactive l'appel Qwen (test/CI). Utilise uniquement l'heuristique "
            "pour la detection ; pas de traduction reelle."
        ),
    )
    args = parser.parse_args(argv)

    # Charge la langue config (depuis preferences.json) si pas override.
    initialize_from_preferences()
    target_lang = args.target_lang or get_active_language()
    print(f"[migrate_goals_i18n] target_lang={target_lang}")

    translator: PlayerTranslator | None = None
    if args.no_llm:
        # Force le heuristique : on injecte un client httpx-like qui leve
        # systematiquement (pour court-circuiter le LLM).
        import httpx

        class _AlwaysFailClient:
            def post(self, *_a: Any, **_k: Any) -> Any:
                raise httpx.HTTPError("disabled")

        translator = PlayerTranslator(http_client=_AlwaysFailClient())  # type: ignore[arg-type]

    save_ids = [args.save_id] if args.save_id else _iter_save_ids()
    if not save_ids:
        print("[migrate_goals_i18n] aucun save trouve.")
        return 0

    grand_total = {"migrated": 0, "pending": 0, "skipped": 0, "total": 0}
    for sid in save_ids:
        try:
            stats = migrate_save_goals(
                sid,
                target_lang=target_lang,
                translator=translator,
                dry_run=args.dry_run,
            )
        except Exception as exc:
            print(f"[migrate_goals_i18n] {sid} : ERROR {type(exc).__name__}: {exc}")
            continue
        for k in grand_total:
            grand_total[k] += stats[k]
        suffix = " (dry-run)" if args.dry_run else ""
        print(
            f"  {sid}: total={stats['total']} migrated={stats['migrated']} "
            f"pending={stats['pending']} skipped={stats['skipped']}{suffix}"
        )
    print(
        f"\n[migrate_goals_i18n] DONE - total={grand_total['total']} "
        f"migrated={grand_total['migrated']} pending={grand_total['pending']} "
        f"skipped={grand_total['skipped']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
