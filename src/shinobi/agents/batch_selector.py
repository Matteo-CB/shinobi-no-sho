"""BatchActionSelector : 1 inference Qwen3-4B pour N agents simultanement.

Spec docs/02 §6.4 + §11.1 :
> Batch d'agents en un seul prompt (5 PNJ -> 1 inference Qwen3-4B
>   multi-output)

Strategie :
- Concatener les contextes de N agents dans UN SEUL prompt structure
- LLM produit un JSON array d'actions (une par agent dans l'ordre)
- Schema JSON contraint -> grammar-constrained generation
- Cache : key = hash de TOUS les contextes (rarement repetable, mais possible
  pour 'tous au repos' / ticks triviaux)
- Fallback deterministe par agent si le LLM echoue ou la batch reponse est
  malformee (chaque agent revient a son ActionSelector individuel).

Usage :

```python
batch = BatchActionSelector(llm_call=llm, cache=cache, batch_size=5)
results = await batch.select_batch([(memory_a, ctx_a), (memory_b, ctx_b), ...])
```

Le batch n'est pas obligatoire : TickEngine peut decider tick par tick s'il
veut batcher selon le nombre d'agents actifs.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from shinobi.agents.action_space import (
    AGENT_ACTION_JSON_SCHEMA,
    AgentAction,
    AgentActionType,
)
from shinobi.agents.cache import LLMCache, compute_cache_key
from shinobi.agents.memory import AgentMemory
from shinobi.agents.selector import (
    SelectionContext,
    build_user_prompt,
    deterministic_fallback_action,
)
from shinobi.i18n import t
from shinobi.logging_setup import get_logger

logger = get_logger(__name__)

# JSON schema batch : array d'actions, une par agent
BATCH_ACTIONS_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "actions": {
            "type": "array",
            "items": AGENT_ACTION_JSON_SCHEMA,
            "minItems": 1,
        },
    },
    "required": ["actions"],
    "additionalProperties": False,
}


def default_batch_system_prompt() -> str:
    """Resolve le system prompt batch localise via i18n."""
    return t("agents.batch_selector.system_prompt")


def __getattr__(name: str) -> str:
    """Compat pour `from shinobi.agents.batch_selector import BATCH_SYSTEM_PROMPT`."""
    if name == "BATCH_SYSTEM_PROMPT":
        return default_batch_system_prompt()
    raise AttributeError(name)


# (system, user, schema, model_id, temperature) -> dict | None
LLMCall = Callable[[str, str, dict, str, float], Awaitable[dict | None]]


def build_batch_user_prompt(
    contexts: list[SelectionContext],
) -> str:
    """Compose un prompt batch : numerote les agents et concatene les contextes."""
    blocks: list[str] = [
        t("agents.batch_selector.batch_size_line", count=len(contexts)),
        "",
    ]
    for i, ctx in enumerate(contexts):
        blocks.append(t("agents.batch_selector.agent_header", index=i + 1, npc_id=ctx.npc_id))
        blocks.append(build_user_prompt(ctx))
        blocks.append("")
    blocks.append(t("agents.batch_selector.instruction", count=len(contexts)))
    return "\n".join(blocks)


class BatchActionSelector:
    """Action selector batch (N agents -> 1 inference).

    Performant pour secondary tier (50 PNJ tous les 10 ticks) : 50/5 = 10
    inferences au lieu de 50 = 5x speedup theorique sur ces ticks.
    """

    def __init__(
        self,
        *,
        llm_call: LLMCall | None = None,
        cache: LLMCache | None = None,
        batch_size: int = 5,
        model_id: str = "qwen3-4b",
        temperature: float = 0.7,
        system_prompt: str | None = None,
    ) -> None:
        self._llm_call = llm_call
        self._cache = cache
        self._batch_size = max(1, batch_size)
        self._model_id = model_id
        self._temperature = temperature
        # None = resoudre le default localise au moment de l'usage.
        self._system_prompt = system_prompt
        self._cache_hits = 0
        self._cache_misses = 0

    @property
    def batch_size(self) -> int:
        return self._batch_size

    @property
    def cache_hits(self) -> int:
        return self._cache_hits

    @property
    def cache_misses(self) -> int:
        return self._cache_misses

    async def select_batch(
        self,
        items: list[tuple[AgentMemory, SelectionContext]],
    ) -> list[AgentAction]:
        """Selectionne N actions pour N agents en 1 (ou ceil(N/batch_size))
        inferences LLM. Retourne list[AgentAction] dans l'ordre des inputs.
        """
        if not items:
            return []

        results: list[AgentAction] = []
        # Auto-fill top_memories si vide
        prepared: list[tuple[AgentMemory, SelectionContext]] = []
        for memory, ctx in items:
            if not ctx.top_memories:
                scored = memory.retrieve(
                    f"{ctx.npc_id} {ctx.location_id or ''} an {ctx.year}",
                    top_k=5,
                )
                ctx = _replace_top_memories(ctx, tuple(e for _s, e in scored))
            prepared.append((memory, ctx))

        # Decoupage en batches
        for i in range(0, len(prepared), self._batch_size):
            batch_slice = prepared[i:i + self._batch_size]
            batch_actions = await self._select_one_batch(batch_slice)
            results.extend(batch_actions)
        return results

    async def _select_one_batch(
        self,
        batch: list[tuple[AgentMemory, SelectionContext]],
    ) -> list[AgentAction]:
        """Une inference batch + parse + fallback per-agent si echec."""
        contexts = [ctx for _m, ctx in batch]
        user_prompt = build_batch_user_prompt(contexts)
        system_prompt = self._system_prompt or default_batch_system_prompt()
        cache_key = compute_cache_key(
            f"{system_prompt}\n###\n{user_prompt}",
            self._model_id,
            self._temperature,
        )

        # Cache check
        if self._cache is not None:
            cached = self._cache.get(cache_key)
            if cached is not None:
                self._cache_hits += 1
                actions = self._parse_batch(cached, contexts)
                if actions is not None and len(actions) == len(contexts):
                    return actions

        self._cache_misses += 1

        # LLM call
        if self._llm_call is not None:
            try:
                raw = await self._llm_call(
                    system_prompt,
                    user_prompt,
                    BATCH_ACTIONS_JSON_SCHEMA,
                    self._model_id,
                    self._temperature,
                )
                if raw is not None:
                    actions = self._parse_batch(raw, contexts)
                    if actions is not None and len(actions) == len(contexts):
                        if self._cache is not None:
                            self._cache.set(
                                cache_key, raw,
                                model_id=self._model_id,
                                temperature=self._temperature,
                                prompt_chars=len(user_prompt),
                            )
                        return actions
            except Exception as exc:  # noqa: BLE001
                # Audit anti-silent : log au lieu de pass nu. Un bug
                # signature LLM ou un parse_batch incorrect retombait
                # silencieusement sur le fallback per-agent.
                logger.warning(
                    "batch_selector_llm_call_failed",
                    batch_size=len(contexts),
                    error=type(exc).__name__, msg=str(exc)[:200],
                )

        # Fallback : per-agent deterministic
        return [deterministic_fallback_action(ctx) for ctx in contexts]

    def _parse_batch(
        self, raw: dict, contexts: list[SelectionContext],
    ) -> list[AgentAction] | None:
        """Parse le JSON batch en list[AgentAction] alignees sur contexts."""
        try:
            items = raw.get("actions") or []
            if not isinstance(items, list) or len(items) != len(contexts):
                return None
            out: list[AgentAction] = []
            for ctx, payload in zip(contexts, items, strict=False):
                if not isinstance(payload, dict):
                    return None
                try:
                    atype = AgentActionType(payload.get("type", "idle"))
                except ValueError:
                    return None
                out.append(AgentAction(
                    npc_id=ctx.npc_id,
                    type=atype,
                    year=ctx.year,
                    target_npc_id=payload.get("target_npc_id"),
                    location_id=payload.get("location_id") or ctx.location_id,
                    content=payload.get("content", ""),
                    importance=float(payload.get("importance", 0.5)),
                    params=payload.get("params") or {},
                ))
            return out
        except (ValueError, TypeError, KeyError):
            return None


def _replace_top_memories(
    ctx: SelectionContext, top_memories: tuple,
) -> SelectionContext:
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
        # Phase H wiring 9.2 : preserver deep_motivations_text. Avant : drop
        # silencieux dans le batch path (~50 secondaries) -> motivations
        # canon disparaissaient des prompts de la moitie des agents simules.
        deep_motivations_text=ctx.deep_motivations_text,
        # Phase G+E wiring : preserver director_nudge_text idem.
        director_nudge_text=ctx.director_nudge_text,
        extras=ctx.extras,
    )


__all__ = [
    "BATCH_ACTIONS_JSON_SCHEMA",
    "BATCH_SYSTEM_PROMPT",
    "BatchActionSelector",
    "build_batch_user_prompt",
]
