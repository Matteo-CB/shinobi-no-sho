"""Detection de completion d'un breadcrumb apres une action resolue."""

from __future__ import annotations

from shinobi.engine.actions import ActionResult
from shinobi.engine.character import Character
from shinobi.goals.breadcrumbs import Breadcrumb, CompletionCondition


def check_breadcrumb_completion(
    breadcrumb: Breadcrumb,
    *,
    action_result: ActionResult,
    character: Character,
) -> bool:
    """Retourne True si toutes les conditions du breadcrumb sont satisfaites."""
    if breadcrumb.completed:
        return True
    if not breadcrumb.revealed:
        return False
    return all(
        _check_condition(c, action_result=action_result, character=character)
        for c in breadcrumb.completion_conditions
    )


def _check_condition(
    cond: CompletionCondition,
    *,
    action_result: ActionResult,
    character: Character,
) -> bool:
    params = cond.parameters
    t = cond.type
    if t == "visit_location":
        return character.current_location == params.get("location_id")
    if t == "talk_to_npc":
        target = action_result.action.target_id
        return target == params.get("npc_id")
    if t == "obtain_item":
        item_id = params.get("item_id")
        return item_id in character.inventory.misc or item_id in character.inventory.scrolls
    if t == "learn_technique":
        tech_id = params.get("technique_id")
        return any(t.technique_id == tech_id for t in character.techniques_known)
    if t == "reach_stat_threshold":
        stat_name = params.get("stat")
        threshold = float(params.get("threshold", 0))
        value = getattr(character.stats, stat_name, None)
        if value is None:
            value = getattr(character.extended_stats, stat_name, 0.0)
        return float(value) >= threshold
    if t == "befriend_npc":
        npc_id = params.get("npc_id")
        for rel in character.relationships:
            if rel.with_character_id == npc_id and rel.affinity >= 50:
                return True
        return False
    if t == "accomplish_action":
        action_type = params.get("action_type")
        return (
            action_result.action.action_type.value == action_type
            and action_result.outcome.value
            in (
                "full_success",
                "partial_success",
            )
        )
    if t == "survive_event":
        return not character.is_dead
    return False
