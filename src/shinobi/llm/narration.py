"""Orchestrateur de tour narratif : assemble RAG, voice profiles et appelle le LLM."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shinobi.canon.models import CanonBundle
from shinobi.errors import LLMSchemaError, LLMStyleError
from shinobi.llm.client import LLMClient, Message
from shinobi.llm.prompts import NARRATOR_SYSTEM_PROMPT
from shinobi.llm.schema import NARRATOR_SCHEMA
from shinobi.llm.voices import compose_voice_section
from shinobi.rag.contextualize import TurnContextRequest, build_turn_context
from shinobi.rag.retriever import Retriever
from shinobi.utils.text import (
    contains_em_dash,
    contains_emoji,
    contains_forbidden_slang,
    sanitize_narrative,
)


@dataclass
class NarrationRequest:
    """Donnees necessaires pour narrer un tour."""

    turn_summary: str
    action_text: str
    action_result_summary: str
    location_id: str | None
    present_npcs: list[str]
    active_breadcrumb_descriptions: list[str]
    character_state_summary: str
    duration_str: str


@dataclass
class NarrationResponse:
    """Reponse structuree du narrator."""

    narrative: str
    npc_dialogue: list[dict[str, Any]]
    proposed_actions: list[dict[str, Any]]
    world_observations: list[str]
    clarification_request: str | None


class Narrator:
    """Orchestrateur du role NARRATOR."""

    def __init__(self, client: LLMClient, canon: CanonBundle, retriever: Retriever) -> None:
        self.client = client
        self.canon = canon
        self.retriever = retriever

    async def narrate(self, request: NarrationRequest) -> NarrationResponse:
        rag_context = build_turn_context(
            self.retriever,
            TurnContextRequest(
                action_text=request.action_text,
                location_id=request.location_id,
                present_npcs=request.present_npcs,
                active_breadcrumb_descriptions=request.active_breadcrumb_descriptions,
            ),
        )
        voices = compose_voice_section(self.canon, request.present_npcs)

        user_blocks = [
            "[ETAT DU PERSONNAGE]",
            request.character_state_summary,
        ]
        if voices:
            user_blocks.append("\n" + voices)
        user_blocks.append("\n" + rag_context)
        user_blocks.append(
            "\n[ACTION DU JOUEUR]\n"
            f"Texte de l'intention : {request.action_text}\n"
            f"Resultat mecanique : {request.action_result_summary}\n"
            f"Duree ecoulee : {request.duration_str}"
        )
        user_blocks.append(
            "\n[INSTRUCTION]\n"
            "Narre ce tour en respectant strictement les regles. Reponds en JSON conforme."
        )
        user_message = "\n".join(user_blocks)

        response = await self.client.generate(
            messages=[
                Message(role="system", content=NARRATOR_SYSTEM_PROMPT),
                Message(role="user", content=user_message),
            ],
            schema=NARRATOR_SCHEMA,
        )
        if response.parsed_json is None:
            raise LLMSchemaError("Reponse narrator vide")

        data = response.parsed_json
        narrative = data.get("narrative", "")
        if (
            contains_em_dash(narrative)
            or contains_emoji(narrative)
            or contains_forbidden_slang(narrative)
        ):
            cleaned = sanitize_narrative(narrative)
            if contains_forbidden_slang(cleaned):
                raise LLMStyleError("Argot otaku detecte dans la narration")
            narrative = cleaned

        return NarrationResponse(
            narrative=narrative,
            npc_dialogue=data.get("npc_dialogue", []),
            proposed_actions=data.get("proposed_actions", []),
            world_observations=data.get("world_observations", []),
            clarification_request=data.get("clarification_request"),
        )
