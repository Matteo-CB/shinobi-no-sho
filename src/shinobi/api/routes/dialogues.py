"""Routes /dialogues Phase 9.

DialogueLog read + export VN. Le log est persiste en JSONL dans la save.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import JSONResponse

from shinobi.api.schemas import DialogueLine as APIDialogueLine, DialoguesResponse
from shinobi.dialogue.types import DialogueLine
from shinobi.dialogue.vn_export import export_to_vn_payload
from shinobi.i18n import t
from shinobi.persistence import saves as save_module


router = APIRouter(prefix="/play/{save_id}/dialogues", tags=["status"])


def _load_lines(save_id: str) -> list[DialogueLine]:
    """Read DialogueLines from the persisted JSONL."""
    log_path = save_module.dialogue_log_path(save_id)
    if not log_path.exists():
        return []
    out: list[DialogueLine] = []
    with log_path.open("r", encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                data = json.loads(raw)
                out.append(DialogueLine(**data))
            except (json.JSONDecodeError, ValueError):
                continue
    return out


@router.get(
    "",
    response_model=DialoguesResponse,
    summary="Last N dialogue lines (VN style)",
)
def list_dialogues(
    save_id: str,
    limit: int = Query(50, ge=1, le=2000),
) -> DialoguesResponse:
    """Return the last N lines from the persisted DialogueLog."""
    if not save_module._meta_path(save_id).exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=t("api.saves.not_found", save_id=save_id),
        )
    lines = _load_lines(save_id)
    sliced = lines[-limit:]
    return DialoguesResponse(
        save_id=save_id,
        lines=[
            APIDialogueLine(
                in_game_year=line.in_game_year or 0,
                in_game_date=line.in_game_date or "",
                turn_number=line.turn_number or 0,
                speaker=line.speaker_id,
                text=line.text,
                style=getattr(line.tone, "value", None),
            )
            for line in sliced
        ],
        count=len(sliced),
    )


@router.get(
    "/export",
    summary="Export VN payload (JSON) of the full log",
)
def export_vn(save_id: str) -> JSONResponse:
    """Return the JSON payload in Visual Novel style (export_to_vn_payload)."""
    if not save_module._meta_path(save_id).exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=t("api.saves.not_found", save_id=save_id),
        )
    lines = _load_lines(save_id)
    payload = export_to_vn_payload(lines)
    return JSONResponse(content=payload)
