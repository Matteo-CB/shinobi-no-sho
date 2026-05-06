"""MajorAgent : orchestrateur d'un PNJ majeur (tick : perceive + reflect + act).

Spec docs/02 §6.1 :
> Chaque PNJ majeur est un agent avec memoire 3-niveaux (obs/refl/plans).
> Quand un PNJ majeur doit agir, Qwen3-4B recoit en contexte sa memoire
> pertinente, son vecteur de personnalite, ses relations, etat du monde local,
> ses plans en cours.

Cycle d'un agent par tick :

1. **Perceive** : convertit les events du monde (KG facts changes, NPCs
   present_npcs, rumeurs entendues) en `Observation`s ajoutees a la memoire.
2. **Reflect** (periodique, tous les N ticks) : `Reflector` synthetise les
   N derniers obs en `Reflection`s.
3. **Select action** : `ActionSelector` produit une `AgentAction` LLM-driven
   (avec cache + fallback deterministe).
4. **Persist** : observations / reflections / actions ecrites dans
   `AgentMemoryStore`.

L'agent ne mute pas le KG directement : il EMET une AgentAction. C'est le
TickEngine qui collecte les actions et applique les consequences au monde.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from shinobi.agents.action_space import AgentAction
from shinobi.agents.memory import AgentMemory
from shinobi.agents.reflector import Reflector
from shinobi.agents.selector import ActionSelector, SelectionContext
from shinobi.agents.store import AgentMemoryStore
from shinobi.agents.types import (
    Observation,
    Plan,
    PlanStatus,
    Reflection,
)
from shinobi.personality.types import NPCPersonality


@dataclass
class AgentTickInputs:
    """Donnees fournies a un agent au moment de son tick."""

    year: int
    tick: int
    location_id: str | None = None
    present_npc_ids: tuple[str, ...] = ()
    new_observations: tuple[Observation, ...] = ()
    world_summary: str = ""
    relations_summary: str = ""
    extras: dict = field(default_factory=dict)


@dataclass
class AgentTickResult:
    """Sortie d'un tick d'agent."""

    action: AgentAction
    new_observations_count: int = 0
    new_reflections_count: int = 0
    cache_hit: bool = False
    used_llm: bool = False


class MajorAgent:
    """Orchestrateur d'un PNJ Major.

    Stocke la `AgentMemory` en memoire pendant le tick et persiste a la fin
    via le `AgentMemoryStore` injecte. L'objet est leger : on en cree un
    par PNJ par tick (ou on le reutilise si TickEngine pin l'instance).
    """

    REFLECTION_PERIOD_TICKS: int = 10  # reflect tous les N ticks

    def __init__(
        self,
        npc_id: str,
        *,
        memory_store: AgentMemoryStore,
        selector: ActionSelector,
        reflector: Reflector,
        personality: NPCPersonality | None = None,
        memory: AgentMemory | None = None,
    ) -> None:
        self._npc_id = npc_id
        self._store = memory_store
        self._selector = selector
        self._reflector = reflector
        self._personality = personality
        self._memory = memory or memory_store.load_memory(npc_id)
        self._ticks_since_reflect = 0

    @property
    def npc_id(self) -> str:
        return self._npc_id

    @property
    def memory(self) -> AgentMemory:
        return self._memory

    @property
    def personality(self) -> NPCPersonality | None:
        return self._personality

    def update_personality(self, personality: NPCPersonality) -> None:
        """Met a jour le vecteur de personnalite (appele apres drift)."""
        self._personality = personality

    # --- perceive ----------------------------------------------------------

    def perceive(self, observations: Iterable[Observation]) -> int:
        """Ajoute des observations a la memoire. Retourne nb ajoutes."""
        n = 0
        for o in observations:
            if o.npc_id != self._npc_id:
                # Skip silencieux : observation pour un autre PNJ
                continue
            self._memory.add_observation(o)
            self._store.insert_observation(o)
            n += 1
        return n

    # --- reflect -----------------------------------------------------------

    async def reflect_if_due(self, year: int) -> list[Reflection]:
        """Si REFLECTION_PERIOD_TICKS atteint, reflechit. Sinon, [].

        Reset le compteur a la fin si reflection produite.
        """
        if self._ticks_since_reflect < self.REFLECTION_PERIOD_TICKS:
            return []
        reflections = await self._reflector.reflect(
            self._npc_id, year, self._memory.observations,
        )
        for r in reflections:
            self._memory.add_reflection(r)
            self._store.insert_reflection(r)
        self._ticks_since_reflect = 0
        return reflections

    # --- act ---------------------------------------------------------------

    async def act(
        self, inputs: AgentTickInputs,
    ) -> AgentTickResult:
        """Execute un tick complet : perceive + reflect + select + log."""
        # 1. Perceive
        added_obs = self.perceive(inputs.new_observations)

        # 2. Reflect (eventuel)
        reflections = await self.reflect_if_due(inputs.year)

        # 3. Select action
        active_plans_text = tuple(p.description for p in self._memory.active_plans())
        ctx = SelectionContext(
            npc_id=self._npc_id,
            year=inputs.year,
            location_id=inputs.location_id,
            present_npc_ids=inputs.present_npc_ids,
            personality=self._personality,
            active_plans_text=active_plans_text,
            world_summary=inputs.world_summary,
            relations_summary=inputs.relations_summary,
            extras=inputs.extras,
        )
        cache_hits_before = self._selector.cache_hits
        action = await self._selector.select(self._memory, ctx)
        cache_hit = self._selector.cache_hits > cache_hits_before

        # 4. Persist action
        self._store.log_action(action, tick=inputs.tick)

        self._ticks_since_reflect += 1

        return AgentTickResult(
            action=action,
            new_observations_count=added_obs,
            new_reflections_count=len(reflections),
            cache_hit=cache_hit,
            used_llm=not cache_hit,
        )

    # --- plans -------------------------------------------------------------

    def add_plan(self, plan: Plan) -> None:
        """Ajoute un plan + persist."""
        if plan.npc_id != self._npc_id:
            raise ValueError(f"plan.npc_id != {self._npc_id}")
        self._memory.add_plan(plan)
        self._store.insert_plan(plan)

    def update_plan_status(self, plan_id: str, status: PlanStatus) -> bool:
        """Met a jour le status d'un plan + persist."""
        ok = self._memory.update_plan_status(plan_id, status)
        if ok:
            self._store.update_plan_status(plan_id, status)
        return ok


__all__ = ["AgentTickInputs", "AgentTickResult", "MajorAgent"]
