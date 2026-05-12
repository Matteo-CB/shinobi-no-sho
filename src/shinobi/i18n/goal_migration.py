"""Phase i18n.8 : migration des goals existants vers le schema enrichi.

Logique partagee entre :
- `scripts/migrate_goals_i18n.py` : CLI standalone (dry-run / no-llm).
- `POST /play/{id}/initialize` : migration silencieuse au bootstrap.

API publique :

    from shinobi.i18n.goal_migration import migrate_save_goals

    stats = migrate_save_goals(save_id, target_lang="en")
    # {"migrated": 3, "pending": 0, "skipped": 5, "total": 8}

Le comportement est idempotent : un goal deja migre (champ
`description_player_original_language` non None ET la langue cible deja
presente dans `description_player_translated`) est skip.
"""

from __future__ import annotations

from shinobi.goals.declaration import Goal
from shinobi.i18n.player_translator import (
    PlayerTranslator,
    process_player_input,
)
from shinobi.persistence import saves as save_module


def _needs_update(goal: Goal, target_lang: str) -> bool:
    if goal.description_player_original_language is None:
        return True
    if (
        goal.description_player_original_language != target_lang
        and target_lang not in goal.description_player_translated
    ):
        return True
    return False


def migrate_goal(
    goal: Goal,
    *,
    target_lang: str,
    translator: PlayerTranslator | None = None,
) -> tuple[Goal, str]:
    """Migre un goal individuellement.

    Returns (goal_resultat, status) ou status est :
    - "skipped" : rien a faire
    - "migrated" : champs remplis avec traduction reussie
    - "pending" : detection OK mais traduction echouee (Qwen down)
    """
    if not _needs_update(goal, target_lang):
        return goal, "skipped"

    src_lang, translated, pending = process_player_input(
        goal.description_player,
        target_lang=target_lang,
        fallback_source=goal.description_player_original_language,
        translator=translator,
    )

    if src_lang is None and not translated:
        return goal, "skipped"

    merged = dict(goal.description_player_translated)
    merged.update(translated)
    new_goal = goal.model_copy(
        update={
            "description_player_original_language": (
                src_lang or goal.description_player_original_language
            ),
            "description_player_translated": merged,
        }
    )
    return new_goal, "pending" if pending else "migrated"


def migrate_save_goals(
    save_id: str,
    *,
    target_lang: str,
    translator: PlayerTranslator | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """Migre tous les goals d'une save.

    Returns dict {"migrated": int, "pending": int, "skipped": int, "total": int}.
    """
    goals = save_module.load_goals(save_id)
    stats = {"migrated": 0, "pending": 0, "skipped": 0, "total": len(goals)}
    for g in goals:
        new_g, status = migrate_goal(
            g, target_lang=target_lang, translator=translator,
        )
        stats[status] += 1
        if status in ("migrated", "pending") and not dry_run:
            save_module.save_goal(save_id, new_g)
    return stats


__all__ = ["migrate_goal", "migrate_save_goals"]
