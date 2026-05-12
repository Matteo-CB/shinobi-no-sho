"""Phase i18n.13 : migration finale des saves existantes vers le schema i18n.

Couvre l'ensemble des migrations i18n par save :

1. **Goals** (Phase 8) : remplit
   `description_player_original_language` + `description_player_translated`
   pour les goals declares avant Phase 8 (idempotent).

2. **Preferences** (Phase 2) : si `~/.config/shinobi-no-sho/preferences.json`
   manque, l'utilisateur sera prompt au prochain lancement (le picker
   gere). Rien a faire ici.

3. **Phase H i18n locale** (Phase 7) : pas de migration save needed : la
   resolution est lazy au runtime via `get_active_language()`.

4. **Wiki cache** (Phase 6) : idem, lazy, pas de pre-warm requis.

Le script est idempotent et orchestre `shinobi.i18n.goal_migration` pour
chaque save listee dans `settings.saves_dir`.

Usage :

    python scripts/migrate_saves_i18n.py               # toutes les saves
    python scripts/migrate_saves_i18n.py <save_id>     # une save specifique
    python scripts/migrate_saves_i18n.py --dry-run     # rapport sans ecriture
    python scripts/migrate_saves_i18n.py --no-llm      # heuristique seule
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

# sys.path bootstrap : permet l'execution directe `python scripts/...`
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
    parser = argparse.ArgumentParser(
        description=(
            "Phase i18n.13 : migration finale des saves existantes vers le "
            "schema i18n (goals + traductions + flags). Idempotent."
        ),
    )
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
            "Desactive Qwen LLM (test/CI). Heuristique seule, pas de "
            "traduction reelle, juste detection langue source."
        ),
    )
    args = parser.parse_args(argv)

    initialize_from_preferences()
    target_lang = args.target_lang or get_active_language()
    print(f"[migrate_saves_i18n] target_lang={target_lang}")

    translator: PlayerTranslator | None = None
    if args.no_llm:
        import httpx

        class _AlwaysFailClient:
            def post(self, *_a: Any, **_k: Any) -> Any:
                raise httpx.HTTPError("disabled by --no-llm")

        translator = PlayerTranslator(http_client=_AlwaysFailClient())  # type: ignore[arg-type]

    save_ids = [args.save_id] if args.save_id else _iter_save_ids()
    if not save_ids:
        print("[migrate_saves_i18n] aucun save trouve.")
        return 0

    grand_total = {"migrated": 0, "pending": 0, "skipped": 0, "total": 0}
    errors = 0
    for sid in save_ids:
        try:
            stats = migrate_save_goals(
                sid,
                target_lang=target_lang,
                translator=translator,
                dry_run=args.dry_run,
            )
        except Exception as exc:
            print(
                f"[migrate_saves_i18n] {sid} : ERROR "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            errors += 1
            continue
        for k in grand_total:
            grand_total[k] += stats[k]
        suffix = " (dry-run)" if args.dry_run else ""
        print(
            f"  {sid}: goals total={stats['total']} "
            f"migrated={stats['migrated']} pending={stats['pending']} "
            f"skipped={stats['skipped']}{suffix}"
        )

    print(
        f"\n[migrate_saves_i18n] DONE - {len(save_ids)} save(s) traites. "
        f"Cumul goals : total={grand_total['total']} "
        f"migrated={grand_total['migrated']} pending={grand_total['pending']} "
        f"skipped={grand_total['skipped']}"
    )
    if errors:
        print(f"[migrate_saves_i18n] {errors} save(s) en erreur.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
