"""Routes /goals Phase 9.

Declaration / liste / abandon d'objectifs joueur (Phase 5).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from shinobi.api.schemas import (
    DeclareGoalRequest,
    GoalsListResponse,
    GoalSummary,
    PathfinderResponse,
)
from shinobi.errors import SaveNotFoundError
from shinobi.goals.declaration import (
    Goal,
    abandon_goal,
    complete_goal,
    declare_goal,
)
from shinobi.i18n import t
from shinobi.i18n.catalog import get_active_language
from shinobi.i18n.player_translator import process_player_input
from shinobi.persistence import saves as save_module

router = APIRouter(prefix="/play/{save_id}/goals", tags=["goals"])


def _to_summary(g: Goal) -> GoalSummary:
    return GoalSummary(
        id=g.id,
        description_player=g.description_player,
        interpretation_canonical=g.interpretation_canonical,
        target_type=g.target_type,
        target_id=g.target_id,
        status=g.status.value,
        declared_at_year=g.declared_at_year,
        declared_at_age=g.declared_at_age,
        completed_at_year=g.completed_at_year,
        abandoned_at_year=g.abandoned_at_year,
        breadcrumbs=list(g.breadcrumbs),
        description_player_original_language=g.description_player_original_language,
        description_player_translated=dict(g.description_player_translated),
    )


def _ensure_save_exists(save_id: str) -> None:
    """Raise 404 if the save has no meta.json (otherwise load_goals would
    silently create an empty DB)."""
    if not save_module._meta_path(save_id).exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=t("api.saves.not_found", save_id=save_id),
        )


@router.get("", response_model=GoalsListResponse, summary="List player goals")
def list_goals(save_id: str) -> GoalsListResponse:
    """List all goals declared for this save."""
    _ensure_save_exists(save_id)
    goals = save_module.load_goals(save_id)
    return GoalsListResponse(
        goals=[_to_summary(g) for g in goals],
        count=len(goals),
    )


@router.post(
    "",
    response_model=GoalSummary,
    status_code=status.HTTP_201_CREATED,
    summary="Declare a new player goal",
)
def post_goal(save_id: str, payload: DeclareGoalRequest) -> GoalSummary:
    """Declare a new goal. Year/age are read from the save.

    Phase i18n.8: detect the language of the player text and, if it differs
    from the current config language, translate + cache in the goal payload.
    If the Qwen backend is down, fall back silently to source=config
    (verbatim, no translation) — no 5xx error.
    """
    try:
        character, world, _meta = save_module.load_save(save_id)
    except SaveNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc),
        ) from exc
    active_lang = get_active_language()
    try:
        src_lang, translated, _pending = process_player_input(
            payload.description_player,
            target_lang=active_lang,
            fallback_source=active_lang,
        )
    except Exception:
        src_lang, translated = active_lang, {}
    goal = declare_goal(
        description_player=payload.description_player,
        interpretation_canonical=(
            payload.interpretation_canonical or payload.description_player
        ),
        declared_at_year=world.current_year,
        declared_at_age=character.age_years,
        target_type=payload.target_type,
        target_id=payload.target_id,
        declared_priority=payload.declared_priority,
        description_player_original_language=src_lang,
        description_player_translated=translated,
    )
    save_module.save_goal(save_id, goal)
    return _to_summary(goal)


@router.post(
    "/{goal_id}/abandon",
    response_model=GoalSummary,
    summary="Abandon a goal",
)
def abandon(save_id: str, goal_id: str) -> GoalSummary:
    """Mark a goal as abandoned."""
    try:
        _, world, _meta = save_module.load_save(save_id)
    except SaveNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc),
        ) from exc
    goals = save_module.load_goals(save_id)
    target = next((g for g in goals if g.id == goal_id), None)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=t("api.goals.not_found", goal_id=goal_id),
        )
    updated = abandon_goal(target, year=world.current_year)
    save_module.save_goal(save_id, updated)
    return _to_summary(updated)


@router.post(
    "/{goal_id}/path",
    response_model=PathfinderResponse,
    summary="Ask the LLM pathfinder for the next step toward a goal",
)
async def pathfinder(save_id: str, goal_id: str) -> PathfinderResponse:
    """Run the LLM pathfinder (Phase 5). If the LLM is down, return
    available=False with an explanatory message (no 5xx, to stay predictable).
    """
    try:
        character, world, _meta = save_module.load_save(save_id)
    except SaveNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc),
        ) from exc
    goals = save_module.load_goals(save_id)
    target = next((g for g in goals if g.id == goal_id), None)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=t("api.goals.not_found", goal_id=goal_id),
        )
    try:
        from shinobi.canon.loader import load_canon
        from shinobi.goals.pathfinder import GoalPathfinder, PathfinderRequest
        from shinobi.llm.client import LLMClient
        from shinobi.rag.retriever import Retriever
        from shinobi.rag.store import ChromaStore

        canon = load_canon(
            optional=(
                "characters", "techniques", "clans", "villages",
                "organizations", "tailed_beasts", "kekkei_genkai",
                "kekkei_mora", "hiden", "weapons_tools", "locations",
                "timeline_events", "voice_profiles",
            ),
        )
        retriever = Retriever(ChromaStore(), canon)
        async with LLMClient() as client:
            if not await client.health():
                return PathfinderResponse(
                    goal_id=goal_id,
                    available=False,
                    error="LLM hors ligne (lance llama-server pour activer le pathfinder).",
                )
            existing = save_module.load_breadcrumbs(
                save_id, parent_goal_id=goal_id,
            )
            seq = len(existing) + 1
            req = PathfinderRequest(
                goal=target,
                character_state_summary=(
                    f"{character.name}, {character.age_years} ans, "
                    f"{character.rank} a {character.current_village}"
                ),
                current_year=world.current_year,
                sequence_index=seq,
            )
            pf = GoalPathfinder(client, canon, retriever)
            response = await pf.find_path(req)
        next_step = (
            response.breadcrumbs[0].description
            if response.breadcrumbs else None
        )
        bc_id = (
            response.breadcrumbs[0].id if response.breadcrumbs else None
        )
        # Persist breadcrumbs + transition declared -> in_progress
        if response.breadcrumbs:
            from shinobi.goals.declaration import mark_goal_in_progress

            updated = mark_goal_in_progress(target)
            if updated.status != target.status:
                save_module.save_goal(save_id, updated)
            for bc in response.breadcrumbs:
                save_module.save_breadcrumb(save_id, bc)
        return PathfinderResponse(
            goal_id=goal_id,
            available=True,
            next_step_fr=next_step,
            breadcrumb_id=bc_id,
        )
    except Exception as exc:
        return PathfinderResponse(
            goal_id=goal_id,
            available=False,
            error=f"{type(exc).__name__}: {exc}",
        )


@router.post(
    "/{goal_id}/complete",
    response_model=GoalSummary,
    summary="Mark a goal as completed",
)
def complete(save_id: str, goal_id: str) -> GoalSummary:
    """Mark a goal as completed (manual transition)."""
    try:
        _, world, _meta = save_module.load_save(save_id)
    except SaveNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc),
        ) from exc
    goals = save_module.load_goals(save_id)
    target = next((g for g in goals if g.id == goal_id), None)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=t("api.goals.not_found", goal_id=goal_id),
        )
    updated = complete_goal(target, year=world.current_year)
    save_module.save_goal(save_id, updated)
    return _to_summary(updated)
