"""Routes /missions Phase 9.

Liste / accept / submit / list active. Le seed de generation est
deterministe (turn_number + village) : le client recoit la meme liste
tant qu'il n'a pas avance d'un tour.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from shinobi.api.schemas import (
    AcceptMissionRequest,
    ActiveMissionsListResponse,
    ActiveMissionSummary,
    MissionsListResponse,
    MissionSummary,
    SubmitMissionRequest,
    SubmitMissionResponse,
)
from shinobi.engine.actions import apply_mission_result
from shinobi.engine.missions import Mission, list_available_missions
from shinobi.errors import SaveNotFoundError
from shinobi.i18n import t
from shinobi.persistence import saves as save_module

router = APIRouter(prefix="/play/{save_id}/missions", tags=["missions"])


def _mission_seed(world_seed: int, turn: int, village: str) -> int:
    """Deterministic seed for the mission list shown on a given turn."""
    return (world_seed ^ (turn * 1009) ^ hash(village)) & 0x7FFFFFFFFFFFFFFF


def _to_summary(m: Mission) -> MissionSummary:
    # Phase i18n.9 : `description` resolu via la chaine de fallback i18n.
    # Pour le moment, le moteur ne genere que description_fr ; le champ
    # `description` retombe dessus jusqu'a ce qu'on stocke des traductions.
    from shinobi.api.i18n_helpers import localize_description

    description = (
        localize_description(m) or getattr(m, "description_fr", "") or ""
    )
    return MissionSummary(
        id=m.id,
        rank=m.rank,
        title=m.title,
        description=description,
        description_fr=m.description_fr,
        duration_hours=m.duration_hours,
        difficulty_dc=m.difficulty_dc,
        reward_ryos=m.reward_ryos,
        reputation_delta=m.reputation_delta,
    )


@router.get(
    "/available",
    response_model=MissionsListResponse,
    summary="Missions available at the current village",
)
def list_available(save_id: str) -> MissionsListResponse:
    """List 4 missions generated for the player's rank and village."""
    try:
        character, world, meta = save_module.load_save(save_id)
    except SaveNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc),
        ) from exc
    seed = _mission_seed(world.seed, meta.total_turns, character.current_village)
    missions = list_available_missions(
        player_rank=character.rank, count=4, seed=seed,
    )
    return MissionsListResponse(
        missions=[_to_summary(m) for m in missions],
        count=len(missions),
    )


@router.post(
    "/accept",
    response_model=MissionSummary,
    summary="Accept an available mission",
)
def accept(save_id: str, payload: AcceptMissionRequest) -> MissionSummary:
    """Accept a mission by id (regeneratable from the available list)."""
    try:
        character, world, meta = save_module.load_save(save_id)
    except SaveNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc),
        ) from exc
    seed = _mission_seed(world.seed, meta.total_turns, character.current_village)
    missions = list_available_missions(
        player_rank=character.rank, count=4, seed=seed,
    )
    target = next((m for m in missions if m.id == payload.mission_id), None)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=t(
                "api.missions.unavailable",
                mission_id=payload.mission_id,
            ),
        )
    save_module.save_active_mission(save_id, target, year=world.current_year)
    return _to_summary(target)


@router.get(
    "/active",
    response_model=ActiveMissionsListResponse,
    summary="Active or completed missions",
)
def list_active(save_id: str) -> ActiveMissionsListResponse:
    """List accepted missions (in progress + completed)."""
    try:
        # Verifie l'existence de la save
        save_module.load_save(save_id)
    except SaveNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc),
        ) from exc
    missions = save_module.load_active_missions(save_id)
    return ActiveMissionsListResponse(
        missions=[
            ActiveMissionSummary(
                id=m["id"],
                rank=m["rank"],
                title=m["title"],
                accepted_at_year=m["accepted_at_year"],
                completed_at_year=m["completed_at_year"],
                success=m["success"],
            )
            for m in missions
        ],
        count=len(missions),
    )


@router.post(
    "/submit",
    response_model=SubmitMissionResponse,
    summary="Submit a finished mission (success or failure)",
)
def submit(save_id: str, payload: SubmitMissionRequest) -> SubmitMissionResponse:
    """Mark an active mission as finished, applying rewards/damage."""
    try:
        character, world, _meta = save_module.load_save(save_id)
    except SaveNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc),
        ) from exc
    actives = save_module.load_active_missions(save_id)
    target = next(
        (m for m in actives if m["id"] == payload.mission_id and m["completed_at_year"] is None),
        None,
    )
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=t(
                "api.missions.active_not_found",
                mission_id=payload.mission_id,
            ),
        )
    payload_dict = target["payload"]
    mission = Mission(**payload_dict)
    new_char, ryos_gained, stat_changes = apply_mission_result(
        character, mission, success=payload.success,
    )
    # Persist le character update via UPDATE direct sur le snapshot courant
    from shinobi.persistence.database import close, open_connection
    from shinobi.persistence.serialize import encode_payload

    state_path = save_module._state_path(save_id)
    conn = open_connection(state_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE character SET payload = ? WHERE is_current = 1",
            (encode_payload(new_char),),
        )
        conn.commit()
    finally:
        close(conn)
    save_module.mark_mission_completed(
        save_id,
        payload.mission_id,
        year=world.current_year,
        success=payload.success,
    )
    return SubmitMissionResponse(
        mission_id=payload.mission_id,
        success=payload.success,
        ryos_gained=ryos_gained,
        new_money=getattr(new_char, "money", 0),
        stat_changes=[
            {
                "stat": c.change.stat_name,
                "old": c.change.old,
                "new": c.change.new,
                "delta": c.change.delta,
                "why_fr": c.why_fr,
            }
            for c in stat_changes
        ],
    )
