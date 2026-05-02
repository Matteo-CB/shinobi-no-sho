"""Orchestrateurs LLM : narrator, character interpreter, world resolver."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shinobi.canon.models import CanonBundle
from shinobi.engine.scene_context import (
    SceneContext,
    filter_proposed_actions,
    format_scene_context_for_prompt,
    looks_like_generic_role,
)
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

# Estimations heuristiques pour combler les "?" dans le tableau d'actions proposees.
# Le LLM ne fournit pas systematiquement difficulty/duration ; on les derive du label.
_DIFFICULTY_KEYWORDS_FR: dict[str, str] = {
    "combat": "difficile", "attaque": "difficile", "tuer": "tres difficile",
    "voler": "difficile", "espionner": "difficile",
    "intimider": "difficile", "seduire": "difficile",
    "entrain": "modere", "entraine": "modere", "pratique": "modere",
    "etudier": "facile", "lire": "facile", "ecouter": "facile",
    "suivre les cours": "facile", "discuter": "facile", "parler": "facile",
    "demander": "facile", "se reposer": "trivial", "dormir": "trivial",
    "mediter": "trivial", "manger": "trivial",
    "voyager": "modere", "se rendre": "modere", "aller": "facile",
}
_DURATION_KEYWORDS_FR: dict[str, str] = {
    "dormir": "8h", "se reposer": "1h", "mediter": "1h",
    "entrain": "4h", "pratique": "4h",
    "etudier": "2h", "lire": "2h", "ecouter": "1h",
    "suivre les cours": "2h",
    "voyager": "1j+", "se rendre": "1j+",
    "discuter": "30min", "parler": "30min", "demander": "15min",
    "manger": "30min",
    "combat": "1h", "attaque": "30min",
}


def _guess_difficulty(label: str) -> str:
    low = label.lower()
    for kw, diff in _DIFFICULTY_KEYWORDS_FR.items():
        if kw in low:
            return diff
    return "modere"


def _guess_duration(label: str) -> str:
    low = label.lower()
    for kw, dur in _DURATION_KEYWORDS_FR.items():
        if kw in low:
            return dur
    return "1h"


def _enrich_proposed_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remplit difficulty/duration cote Python si le LLM les a omises."""
    out: list[dict[str, Any]] = []
    for a in actions:
        label = a.get("label_fr", "") or a.get("label", "")
        new = dict(a)
        if not new.get("difficulty_fr") and not new.get("difficulty"):
            new["difficulty_fr"] = _guess_difficulty(label)
        if not new.get("duration_fr") and not new.get("duration"):
            new["duration_fr"] = _guess_duration(label)
        out.append(new)
    return out


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
    scene_context: SceneContext | None = None


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

        user_blocks = []
        if request.scene_context is not None:
            user_blocks.append(format_scene_context_for_prompt(request.scene_context))
            user_blocks.append("")
        user_blocks.append("[ETAT DU PERSONNAGE]")
        user_blocks.append(request.character_state_summary)
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
            "Narre ce tour en respectant strictement les regles ET le CONTEXTE FACTUEL "
            "DE LA SCENE. Reponds en JSON conforme."
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

        proposed_actions = data.get("proposed_actions", [])
        if request.scene_context is not None:
            proposed_actions = filter_proposed_actions(
                proposed_actions, request.scene_context, canon=self.canon
            )
        npc_dialogue = data.get("npc_dialogue", [])
        if request.scene_context is not None:
            allowed = request.scene_context.npc_ids()
            # Garde les dialogues des PNJ accessibles + des PNJ generiques (id role-based)
            npc_dialogue = [
                d
                for d in npc_dialogue
                if d.get("character_id", "") in allowed
                or looks_like_generic_role(d.get("character_id", ""))
            ]
        # Post-filter : rejette tout dialogue de PNJ canon non vivant a l'annee courante.
        # Capte les cas ou le LLM invente "Kabuto" / "Itachi" / etc. en pleine ere prehistorique.
        if request.scene_context is not None:
            current_year = request.scene_context.current_year
            filtered = []
            for d in npc_dialogue:
                cid = d.get("character_id", "")
                # Generic role (sensei_academie, marchand_taverne...) toujours OK
                if looks_like_generic_role(cid):
                    filtered.append(d)
                    continue
                canon_npc = self.canon.characters.get(cid)
                if canon_npc is None:
                    # Id inconnu, on le garde (peut etre un OC mais le LLM doit l'avoir invente)
                    filtered.append(d)
                    continue
                if canon_npc.birth_year is not None and current_year < canon_npc.birth_year:
                    continue  # pas encore ne
                if canon_npc.death_year is not None and current_year > canon_npc.death_year:
                    continue  # deja mort
                filtered.append(d)
            npc_dialogue = filtered

        # Enrichit les proposed_actions avec difficulty + duration calcules cote Python
        # (le LLM ne fournit que le label, le moteur calcule le reste pour eviter les "?").
        proposed_actions = _enrich_proposed_actions(proposed_actions)

        return NarrationResponse(
            narrative=narrative,
            npc_dialogue=npc_dialogue,
            proposed_actions=proposed_actions,
            world_observations=data.get("world_observations", []),
            clarification_request=data.get("clarification_request"),
        )


# ---------------------------------------------------------------------------
# Character Interpreter LLM (fallback de l'heuristique engine.interpreter)
# ---------------------------------------------------------------------------


@dataclass
class InterpretedIntent:
    """Resultat de l'interpretation LLM d'une action libre."""

    action_type: str
    summary: str
    parameters: dict[str, Any]
    target_id: str | None
    clarification_questions: list[str]


class CharacterInterpreter:
    """LLM-driven interpretation des actions joueur ambigues."""

    def __init__(self, client: LLMClient) -> None:
        self.client = client

    async def interpret(self, free_text: str, *, context_summary: str = "") -> InterpretedIntent:
        from shinobi.llm.prompts import CHARACTER_INTERPRETER_SYSTEM_PROMPT
        from shinobi.llm.schema import CHARACTER_INTERPRETER_SCHEMA

        user_msg = (
            f"[CONTEXTE]\n{context_summary}\n\n"
            f"[ACTION DU JOUEUR]\n{free_text}\n\n"
            f"[INSTRUCTION]\nClassifie cette action et reponds en JSON conforme."
        )
        response = await self.client.generate(
            messages=[
                Message(role="system", content=CHARACTER_INTERPRETER_SYSTEM_PROMPT),
                Message(role="user", content=user_msg),
            ],
            schema=CHARACTER_INTERPRETER_SCHEMA,
        )
        if response.parsed_json is None:
            return InterpretedIntent(
                action_type="custom",
                summary=free_text,
                parameters={},
                target_id=None,
                clarification_questions=[],
            )
        intent = response.parsed_json.get("intention", {})
        return InterpretedIntent(
            action_type=intent.get("action_type", "custom"),
            summary=intent.get("summary", free_text),
            parameters=intent.get("parameters", {}),
            target_id=intent.get("target_id"),
            clarification_questions=response.parsed_json.get("clarification_questions", []),
        )


# ---------------------------------------------------------------------------
# World Resolver LLM (resout les divergences canoniques complexes)
# ---------------------------------------------------------------------------


@dataclass
class WorldResolution:
    """Resultat du WorldResolver pour un evenement annule."""

    substitute_event_summary: str
    consequences: list[dict[str, Any]]
    rumor_template: str | None


class WorldResolver:
    """LLM qui propose des consequences narratives quand un event canon est annule."""

    def __init__(self, client: LLMClient, canon: CanonBundle) -> None:
        self.client = client
        self.canon = canon

    async def resolve_cancelled_event(
        self,
        *,
        event_id: str,
        cancellation_reason: str,
        current_year: int,
    ) -> WorldResolution:
        from shinobi.llm.prompts import WORLD_RESOLVER_SYSTEM_PROMPT
        from shinobi.llm.schema import WORLD_RESOLVER_SCHEMA

        ev = self.canon.timeline_events.get(event_id)
        if ev is None:
            return WorldResolution(
                substitute_event_summary="Evenement inconnu, aucun substitut.",
                consequences=[],
                rumor_template=None,
            )
        user_msg = (
            f"[EVENEMENT ANNULE]\n"
            f"Id : {event_id}\n"
            f"Nom : {ev.name_fr}\n"
            f"Date prevue : an {ev.year}{', ' + ev.date if ev.date else ''}\n"
            f"Resume canon : {ev.narrative_summary_fr}\n"
            f"Raison annulation : {cancellation_reason}\n"
            f"Annee courante in-game : {current_year}\n\n"
            f"[INSTRUCTION]\nProduis un substitut narratif et liste les consequences en cascade."
        )
        response = await self.client.generate(
            messages=[
                Message(role="system", content=WORLD_RESOLVER_SYSTEM_PROMPT),
                Message(role="user", content=user_msg),
            ],
            schema=WORLD_RESOLVER_SCHEMA,
        )
        if response.parsed_json is None:
            return WorldResolution(
                substitute_event_summary="Le canon est devie, mais aucune narration n'a pu etre generee.",
                consequences=[],
                rumor_template=None,
            )
        data = response.parsed_json
        return WorldResolution(
            substitute_event_summary=data.get("substitute_event_summary", ""),
            consequences=data.get("consequences", []),
            rumor_template=data.get("rumor_template"),
        )


