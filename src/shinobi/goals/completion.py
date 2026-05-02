"""Detection de completion d'un breadcrumb apres une action resolue."""

from __future__ import annotations

from shinobi.engine.actions import ActionResult
from shinobi.engine.character import Character
from shinobi.goals.breadcrumbs import Breadcrumb, CompletionCondition
from shinobi.goals.declaration import Goal


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


def check_goal_completion(goal: Goal, breadcrumbs: list[Breadcrumb]) -> bool:
    """Un Goal est accompli si tous ses breadcrumbs reveles non-optionnels sont completes.

    Si aucun breadcrumb n'a ete revele, le Goal n'est pas considere accompli :
    le joueur n'a pas encore explore le chemin.
    """
    own = [b for b in breadcrumbs if b.parent_goal_id == goal.id]
    if not own:
        return False
    required = [b for b in own if not b.optional and b.revealed]
    if not required:
        return False
    return all(b.completed for b in required)


def check_goal_by_target(goal: Goal, character: Character) -> bool:
    """Verification heuristique selon target_type, sans breadcrumbs.

    Permet de fermer un Goal meme si le pathfinder n'a jamais ete invoque,
    quand l'objectif est mecaniquement verifiable (ex: apprendre une technique
    dont l'id est connu).
    """
    if goal.target_type == "learn_technique" and goal.target_id:
        return any(t.technique_id == goal.target_id for t in character.techniques_known)
    if goal.target_type == "achieve_rank" and goal.target_id:
        return character.rank == goal.target_id
    if goal.target_type == "befriend_character" and goal.target_id:
        for rel in character.relationships:
            if rel.with_character_id == goal.target_id and rel.affinity >= 60:
                return True
        return False
    if goal.target_type == "join_organization" and goal.target_id:
        return goal.target_id in character.affiliations
    if goal.target_type == "leave_village":
        return character.is_missing_nin or character.current_village != character.village_of_origin
    if goal.target_type == "obtain_object" and goal.target_id:
        return (
            goal.target_id in character.inventory.misc
            or goal.target_id in character.inventory.scrolls
        )
    if goal.target_type == "master_nature" and goal.target_id:
        return goal.target_id in character.chakra.natures_unlocked
    return False
