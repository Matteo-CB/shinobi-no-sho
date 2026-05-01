"""Selection du contexte pertinent pour un tour de jeu."""

from __future__ import annotations

from dataclasses import dataclass

from shinobi.rag.formatter import format_context
from shinobi.rag.retriever import Retriever


@dataclass
class TurnContextRequest:
    """Donnees minimales pour calculer le contexte d'un tour."""

    action_text: str
    location_id: str | None
    present_npcs: list[str]
    active_breadcrumb_descriptions: list[str]


def build_turn_context(retriever: Retriever, request: TurnContextRequest) -> str:
    """Construit la chaine de contexte canonique formatee pour le prompt."""
    ctx = retriever.query_for_turn(
        action_text=request.action_text,
        location_id=request.location_id,
        present_npcs=request.present_npcs,
        active_breadcrumb_descriptions=request.active_breadcrumb_descriptions,
    )
    return format_context(ctx)
