"""Goal pathfinder : appelle le LLM pour generer un breadcrumb canonique.

Utilise le RAG pour ancrer le chemin propose dans le canon. Le pathfinder
ne propose jamais le succes complet : juste la prochaine etape concrete.
"""

from __future__ import annotations

from dataclasses import dataclass

from shinobi.canon.models import CanonBundle
from shinobi.errors import LLMSchemaError
from shinobi.goals.breadcrumbs import (
    Breadcrumb,
    BreadcrumbPrice,
    CompletionCondition,
    make_breadcrumb,
)
from shinobi.goals.declaration import Goal
from shinobi.i18n.prompts_loader import load_prompt
from shinobi.llm.client import LLMClient, Message
from shinobi.llm.schema import GOAL_PATHFINDER_SCHEMA
from shinobi.rag.contextualize import TurnContextRequest, build_turn_context
from shinobi.rag.retriever import Retriever


@dataclass
class PathfinderRequest:
    goal: Goal
    character_state_summary: str
    current_year: int
    sequence_index: int


@dataclass
class PathfinderResponse:
    interpretation: str
    breadcrumbs: list[Breadcrumb]


class GoalPathfinder:
    """Orchestrateur du role GOAL_PATHFINDER."""

    def __init__(self, client: LLMClient, canon: CanonBundle, retriever: Retriever) -> None:
        self.client = client
        self.canon = canon
        self.retriever = retriever

    async def find_path(self, req: PathfinderRequest) -> PathfinderResponse:
        rag = build_turn_context(
            self.retriever,
            TurnContextRequest(
                action_text=req.goal.description_player,
                location_id=None,
                present_npcs=[],
                active_breadcrumb_descriptions=[],
            ),
        )
        user_message = "\n".join(
            [
                "[OBJECTIF DECLARE]",
                f"Description : {req.goal.description_player}",
                f"Interpretation canonique : {req.goal.interpretation_canonical}",
                f"Annee in-game courante : {req.current_year}",
                "",
                "[ETAT DU PERSONNAGE]",
                req.character_state_summary,
                "",
                rag,
                "",
                "[INSTRUCTION]",
                "Propose 1 a 3 sources d'information distinctes pour le prochain pas.",
                "Chacune avec son prix et son indice deverrouille.",
                "Reponds en JSON conforme au schema.",
            ]
        )
        response = await self.client.generate(
            messages=[
                Message(role="system", content=load_prompt("goal_pathfinder")),
                Message(role="user", content=user_message),
            ],
            schema=GOAL_PATHFINDER_SCHEMA,
        )
        if response.parsed_json is None:
            raise LLMSchemaError("Reponse pathfinder vide")
        data = response.parsed_json
        breadcrumbs: list[Breadcrumb] = []
        for src in data.get("sources_of_information", []):
            indice = src.get("indice_unlocked", {})
            price = src.get("price", {})
            cc = [
                CompletionCondition(
                    type=c.get("type", "accomplish_action"),
                    parameters=c.get("parameters", {}),
                )
                for c in indice.get("completion_conditions", [])
            ]
            bc_price = BreadcrumbPrice(
                type=price.get("type", "none"),
                description=price.get("description", ""),
                amount=price.get("amount"),
            )
            breadcrumbs.append(
                make_breadcrumb(
                    parent_goal_id=req.goal.id,
                    sequence_index=req.sequence_index,
                    description=indice.get("description", ""),
                    canonical_basis=src.get("source_description", ""),
                    completion_conditions=cc,
                    price=bc_price,
                    revealed=False,
                )
            )
        return PathfinderResponse(
            interpretation=data.get("interpretation", req.goal.interpretation_canonical),
            breadcrumbs=breadcrumbs,
        )
