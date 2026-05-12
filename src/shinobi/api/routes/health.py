"""Route /health Phase 9.

Sanite minimale du serveur : version, etat du canon, nombre de saves.
"""

from __future__ import annotations

from fastapi import APIRouter

from shinobi import __version__
from shinobi.api.schemas import HealthResponse
from shinobi.persistence import saves as save_module


router = APIRouter(prefix="/health", tags=["health"])


@router.get("", response_model=HealthResponse, summary="Server health")
async def health() -> HealthResponse:
    """Return server health without heavy dependency.

    `canon_loaded=False` if the canonical directory cannot be found.
    `llm_available=False` if local llama-server does not respond (non-blocking).
    """
    canon_loaded = True
    try:
        from shinobi.api.dependencies import get_canon

        get_canon()
    except Exception:
        canon_loaded = False
    saves_count = len(save_module.list_saves())
    llm_available = False
    try:
        from shinobi.llm.client import LLMClient

        async with LLMClient() as client:
            llm_available = bool(await client.health())
    except Exception:
        llm_available = False
    return HealthResponse(
        status="ok",
        version=__version__,
        canon_loaded=canon_loaded,
        saves_count=saves_count,
        llm_available=llm_available,
    )
