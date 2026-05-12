"""Routes /play/{id}/<view> Phase 9 lecture seule.

biography, rumors, breadcrumbs, reputation, knowledge.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status

from shinobi.api.schemas import (
    BiographyEntry,
    BreadcrumbEntry,
    RelationshipEntry,
    RelationshipsResponse,
    ReputationEntry,
    ReputationResponse,
    RumorEntry,
    TechniqueInProgressEntry,
    TechniqueKnownEntry,
    TechniquesResponse,
)
from shinobi.errors import SaveNotFoundError
from shinobi.persistence import saves as save_module


router = APIRouter(prefix="/play/{save_id}", tags=["status"])


@router.get(
    "/biography",
    response_model=list[BiographyEntry],
    summary="Character biography journal",
)
def biography(save_id: str) -> list[BiographyEntry]:
    """List BiographyEvents (rank-ups, learned techniques, traumas, ...)."""
    try:
        character, _, _ = save_module.load_save(save_id)
    except SaveNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc),
        ) from exc
    return [
        BiographyEntry(
            year=ev.year,
            age=ev.age,
            summary=ev.summary,
            category=ev.category,
        )
        for ev in character.biography_log
    ]


@router.get(
    "/rumors",
    response_model=list[RumorEntry],
    summary="Rumors (heard / unheard)",
)
def rumors(save_id: str) -> list[RumorEntry]:
    """List all rumors currently in the world (Phase 7.1)."""
    try:
        _, world, _ = save_module.load_save(save_id)
    except SaveNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc),
        ) from exc
    return [
        RumorEntry(
            id=r.id,
            summary=r.content,
            born_at_year=r.born_at_year,
            expires_at_year=r.expires_at_year if r.expires_at_year is not None else 9999,
            fidelity=r.fidelity,
            received_by_player=r.received_by_player,
        )
        for r in world.rumors
    ]


@router.get(
    "/breadcrumbs",
    response_model=list[BreadcrumbEntry],
    summary="Breadcrumbs (sub-goals)",
)
def breadcrumbs(save_id: str) -> list[BreadcrumbEntry]:
    """List persisted breadcrumbs (revealed or not)."""
    try:
        save_module.load_save(save_id)
    except SaveNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc),
        ) from exc
    bcs = save_module.load_breadcrumbs(save_id)
    return [
        BreadcrumbEntry(
            id=b.id,
            parent_goal_id=b.parent_goal_id,
            sequence_index=b.sequence_index,
            revealed=b.revealed,
            completed=b.completed,
        )
        for b in bcs
    ]


@router.get(
    "/reputation",
    response_model=ReputationResponse,
    summary="Reputation per village",
)
def reputation(save_id: str) -> ReputationResponse:
    """Player reputation per village + bingo book status."""
    try:
        character, _, _ = save_module.load_save(save_id)
    except SaveNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc),
        ) from exc
    return ReputationResponse(
        save_id=save_id,
        bingo_book_entry=character.reputation.bingo_book_entry,
        reputation=[
            ReputationEntry(village_id=e.village_id, score=e.score)
            for e in character.reputation.by_village
        ],
    )


@router.get(
    "/techniques",
    response_model=TechniquesResponse,
    summary="Known techniques + ones being learned",
)
def techniques(save_id: str) -> TechniquesResponse:
    """List known techniques + those currently being learned."""
    try:
        character, _, _ = save_module.load_save(save_id)
    except SaveNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc),
        ) from exc
    return TechniquesResponse(
        save_id=save_id,
        known=[
            TechniqueKnownEntry(
                technique_id=t.technique_id,
                mastery_level=t.mastery_level,
                learned_year=t.learned_year,
                learned_from=t.learned_from,
                times_used=t.times_used,
            )
            for t in character.techniques_known
        ],
        in_progress=[
            TechniqueInProgressEntry(
                technique_id=t.technique_id,
                progress_hours=t.progress_hours,
                progress_required=t.progress_required,
                started_year=t.started_year,
                teacher_id=t.teacher_id,
            )
            for t in character.techniques_in_progress
        ],
    )


@router.get(
    "/relationships",
    response_model=RelationshipsResponse,
    summary="Social relationships (affinity, trust)",
)
def relationships(save_id: str) -> RelationshipsResponse:
    """List all character relationships."""
    try:
        character, _, _ = save_module.load_save(save_id)
    except SaveNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc),
        ) from exc
    return RelationshipsResponse(
        save_id=save_id,
        relationships=[
            RelationshipEntry(
                with_character_id=r.with_character_id,
                type=r.type,
                affinity=r.affinity,
                trust=r.trust,
            )
            for r in character.relationships
        ],
        count=len(character.relationships),
    )


@router.get(
    "/knowledge",
    response_model=dict[str, Any],
    summary="Character knowledge (events, techniques, secrets)",
)
def knowledge(save_id: str) -> dict[str, Any]:
    """Snapshot of KnowledgeState: what the character knows about the world."""
    try:
        character, _, _ = save_module.load_save(save_id)
    except SaveNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc),
        ) from exc
    k = character.knowledge
    return {
        "save_id": save_id,
        "known_events": dict(k.known_events),
        "known_techniques_existence": list(k.known_techniques_existence),
        "known_locations": list(k.known_locations),
        "secrets_uncovered": list(k.secrets_uncovered),
        "known_characters_count": len(k.known_characters),
    }
