"""ActionSelector : choix d'action LLM-driven sous contraintes JSON.

docs/02 §6.3 : le LLM recoit en contexte :
- Memoire pertinente top-5 retrieved
- Vecteur de personnalite actuel
- Relations avec les PNJ presents
- Etat du monde local (KG filtre sur ce que le PNJ sait)
- Plans en cours

Et il genere une AgentAction structuree (JSON schema-constrained).

Le selector est :
- async (compatible LLMClient existant)
- mockable via `llm_call` injecte
- cache-friendly : le prompt complet est hashe avec model_id + temperature
- deterministe en mode 'trivial' : si l'agent est dans un etat trivial
  (just-finished-action / sleeping / training routine), retourne idle
  sans appeler le LLM (spec §11.1 'Decision deterministe simplifiee')

L'engine ne fait pas d'I/O reseau lui-meme : il delegue a un callable.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from shinobi.agents.action_space import (
    AGENT_ACTION_JSON_SCHEMA,
    AgentAction,
    AgentActionType,
)
from shinobi.agents.cache import LLMCache, compute_cache_key
from shinobi.agents.memory import AgentMemory
from shinobi.agents.types import MemoryEntry
from shinobi.personality.types import NPCPersonality

# Type pour un appel LLM (mockable) :
# (system_prompt, user_prompt, schema, model_id, temperature) -> dict ou None
LLMCall = Callable[
    [str, str, dict, str, float],
    Awaitable[dict | None],
]


@dataclass(frozen=True)
class SelectionContext:
    """Contexte d'inference pour un agent au moment de selectionner une action."""

    npc_id: str
    year: int
    location_id: str | None = None
    present_npc_ids: tuple[str, ...] = ()
    personality: NPCPersonality | None = None
    top_memories: tuple[MemoryEntry, ...] = ()
    active_plans_text: tuple[str, ...] = ()
    world_summary: str = ""
    relations_summary: str = ""
    extras: dict[str, Any] = field(default_factory=dict)


# Default system prompt - Naruto-tonal, contraint
DEFAULT_SYSTEM_PROMPT = """Tu es un PNJ canonique de l'univers Naruto. Tu prends UNE seule action ce tour.
Tu agis selon ta personnalite, ta memoire pertinente, tes plans, et ce que tu sais du monde.
Tu reponds STRICTEMENT en JSON conforme au schema fourni. Pas de markdown.
Choix d'action : declare_intention, speak, travel, attack, search_information, meditate, plot, idle, custom.
Reste sobre. Pas de tirets cadratins. Pas d'emoji. Pas d'argot otaku."""


def build_user_prompt(ctx: SelectionContext) -> str:
    """Compose le user prompt depuis le SelectionContext."""
    blocks: list[str] = []
    blocks.append(f"[IDENTITE]\nTu es {ctx.npc_id}. An in-game : {ctx.year}.")
    if ctx.location_id:
        blocks.append(f"Tu te trouves a : {ctx.location_id}")
    if ctx.present_npc_ids:
        blocks.append(f"Presents : {', '.join(ctx.present_npc_ids)}")
    if ctx.personality is not None:
        # Top 5 dimensions par valeur absolue (dimensions saillantes)
        dims = sorted(
            ctx.personality.vector.items(),
            key=lambda kv: abs(kv[1] - 0.5), reverse=True,
        )[:5]
        traits = ", ".join(f"{d.value}={v:.2f}" for d, v in dims)
        blocks.append(f"[PERSONNALITE saillante]\n{traits}")
    if ctx.top_memories:
        mem_lines = []
        for m in ctx.top_memories:
            text = m.text
            if len(text) > 200:
                text = text[:197] + "..."
            mem_lines.append(f"- {text}")
        blocks.append("[MEMOIRE pertinente]\n" + "\n".join(mem_lines))
    if ctx.active_plans_text:
        blocks.append(
            "[PLANS en cours]\n"
            + "\n".join(f"- {p}" for p in ctx.active_plans_text)
        )
    if ctx.relations_summary:
        blocks.append(f"[RELATIONS]\n{ctx.relations_summary}")
    if ctx.world_summary:
        blocks.append(f"[ETAT DU MONDE LOCAL]\n{ctx.world_summary}")
    blocks.append(
        "[INSTRUCTION]\nChoisis UNE action JSON conforme au schema. "
        "Reste sobre, en accord avec ta personnalite et tes plans."
    )
    return "\n\n".join(blocks)


def is_trivial_state(ctx: SelectionContext) -> bool:
    """Spec §11.1 : 'Decision deterministe simplifiee si le PNJ est dans un
    etat trivial (sleeping, traveling, training routine)'.

    Heuristique deterministe :
    - aucun PNJ present (pas d'interaction sociale a gerer)
    - aucun memoire pertinente recuperee (pas de stimulus)
    - aucun plan actif OU plan trivial (texte 'meditate', 'rest', 'sleep',
      'travel', 'train' sans target_npc)
    - pas de personality fortement saillante
    -> retourne True : on peut court-circuiter le LLM.
    """
    if ctx.present_npc_ids:
        return False
    if ctx.top_memories:
        # Si les memoires recuperees ont importance > 0.6 -> non trivial
        if any(getattr(m, "importance", 0.0) > 0.6 for m in ctx.top_memories):
            return False
    plans_text = " ".join(ctx.active_plans_text).lower() if ctx.active_plans_text else ""
    if plans_text:
        trivial_keywords = ("medit", "repos", "dormir", "sommeil", "voyag",
                            "entrain", "routine", "train")
        if not any(k in plans_text for k in trivial_keywords):
            return False
    return True


def deterministic_fallback_action(ctx: SelectionContext) -> AgentAction:
    """Action deterministe quand le LLM n'est pas dispo / cache miss + fallback.

    Heuristique :
    - si plan actif -> declare_intention (texte du plan)
    - sinon si presents_npc non vide -> idle (presence)
    - sinon -> meditate (etat trivial)
    """
    if ctx.active_plans_text:
        return AgentAction(
            npc_id=ctx.npc_id,
            type=AgentActionType.declare_intention,
            year=ctx.year,
            location_id=ctx.location_id,
            content=ctx.active_plans_text[0],
            importance=0.4,
        )
    if ctx.present_npc_ids:
        return AgentAction(
            npc_id=ctx.npc_id,
            type=AgentActionType.idle,
            year=ctx.year,
            location_id=ctx.location_id,
            content="observe en silence",
            importance=0.2,
        )
    return AgentAction(
        npc_id=ctx.npc_id,
        type=AgentActionType.meditate,
        year=ctx.year,
        location_id=ctx.location_id,
        content="medite seul",
        importance=0.2,
    )


class ActionSelector:
    """Selectionne une AgentAction pour un agent donne, en deleguant le LLM.

    Strategie :
    1. Build prompt -> compute cache key -> check LLMCache
    2. Cache hit : parse + return
    3. Cache miss : appel `llm_call` (mockable) -> parse + cache.set + return
    4. Si LLM echoue : `deterministic_fallback_action`
    """

    def __init__(
        self,
        *,
        llm_call: LLMCall | None = None,
        cache: LLMCache | None = None,
        model_id: str = "qwen3-4b",
        temperature: float = 0.7,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        trivial_state_shortcut: bool = True,
    ) -> None:
        self._llm_call = llm_call
        self._cache = cache
        self._model_id = model_id
        self._temperature = temperature
        self._system_prompt = system_prompt
        # Spec §11.1 : si True, court-circuite le LLM pour les etats triviaux
        self._trivial_state_shortcut = trivial_state_shortcut
        self._cache_hits = 0
        self._cache_misses = 0
        self._trivial_shortcuts = 0

    @property
    def trivial_shortcuts(self) -> int:
        """Compteur d'inferences LLM evitees grace au trivial state shortcut."""
        return self._trivial_shortcuts

    @property
    def cache_hits(self) -> int:
        return self._cache_hits

    @property
    def cache_misses(self) -> int:
        return self._cache_misses

    @property
    def model_id(self) -> str:
        return self._model_id

    async def select(
        self,
        memory: AgentMemory,
        ctx: SelectionContext,
    ) -> AgentAction:
        """Selectionne UNE action pour cet agent.

        Le `memory` permet de retrieve si `ctx.top_memories` est vide.
        """
        # Auto-fill top_memories si non fourni : query naturel = action_text
        if not ctx.top_memories:
            scored = memory.retrieve(
                f"{ctx.npc_id} {ctx.location_id or ''} an {ctx.year}",
                top_k=5,
            )
            ctx = _replace_top_memories(ctx, tuple(e for _s, e in scored))

        # Spec §11.1 : trivial state shortcut. Skip LLM si l'etat ne necessite
        # pas de decision creative (pas de presents, pas de stimulus, plan
        # routinier). Reduit la latence pour les ticks de masse.
        if self._trivial_state_shortcut and is_trivial_state(ctx):
            self._trivial_shortcuts += 1
            return deterministic_fallback_action(ctx)

        user_prompt = build_user_prompt(ctx)
        cache_key = compute_cache_key(
            f"{self._system_prompt}\n###\n{user_prompt}",
            self._model_id,
            self._temperature,
        )

        # 1. Try cache
        if self._cache is not None:
            cached = self._cache.get(cache_key)
            if cached is not None:
                self._cache_hits += 1
                action = self._parse_action(cached, ctx)
                if action is not None:
                    return action

        self._cache_misses += 1

        # 2. Call LLM if available
        if self._llm_call is not None:
            try:
                raw = await self._llm_call(
                    self._system_prompt,
                    user_prompt,
                    AGENT_ACTION_JSON_SCHEMA,
                    self._model_id,
                    self._temperature,
                )
                if raw is not None:
                    action = self._parse_action(raw, ctx)
                    if action is not None:
                        if self._cache is not None:
                            self._cache.set(
                                cache_key, raw,
                                model_id=self._model_id,
                                temperature=self._temperature,
                                prompt_chars=len(user_prompt),
                            )
                        return action
            except Exception:
                # LLM down ou parse error : fallback deterministe
                pass

        # 3. Deterministic fallback
        return deterministic_fallback_action(ctx)

    def _parse_action(
        self, raw: dict, ctx: SelectionContext,
    ) -> AgentAction | None:
        """Parse le payload LLM en AgentAction. Retourne None si invalide."""
        try:
            atype = raw.get("type", "idle")
            return AgentAction(
                npc_id=ctx.npc_id,
                type=AgentActionType(atype),
                year=ctx.year,
                target_npc_id=raw.get("target_npc_id"),
                location_id=raw.get("location_id") or ctx.location_id,
                content=raw.get("content", ""),
                importance=float(raw.get("importance", 0.5)),
                params=raw.get("params") or {},
            )
        except (ValueError, TypeError, KeyError):
            return None


def _replace_top_memories(
    ctx: SelectionContext, top_memories: tuple,
) -> SelectionContext:
    """Helper : ctx est frozen dataclass, on re-cree."""
    return SelectionContext(
        npc_id=ctx.npc_id,
        year=ctx.year,
        location_id=ctx.location_id,
        present_npc_ids=ctx.present_npc_ids,
        personality=ctx.personality,
        top_memories=top_memories,
        active_plans_text=ctx.active_plans_text,
        world_summary=ctx.world_summary,
        relations_summary=ctx.relations_summary,
        extras=ctx.extras,
    )


__all__ = [
    "DEFAULT_SYSTEM_PROMPT",
    "ActionSelector",
    "LLMCall",
    "SelectionContext",
    "build_user_prompt",
    "deterministic_fallback_action",
    "is_trivial_state",
]
