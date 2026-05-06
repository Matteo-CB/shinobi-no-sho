"""TickEngine : orchestrateur multi-agent + tick autonome + fast-forward.

Spec docs/02 §6.4 + §6.5 :
- Top-15 simulation active a chaque tick
- PNJ secondaires : par lot toutes les 10 ticks
- Mode fast-forward : tick N mois sans le joueur, digest a la fin

L'engine consomme :
- AgentRoster (qui simuler ce tick)
- AgentMemoryStore (charger memoire, persister)
- ActionSelector + Reflector (LLM-driven, mockable)
- LLMCache (caching agressif)
- Optionnel : PersonalityStore (pour vector courant) + KG (world summary)

Output :
- list[AgentTickResult] sur un tick simple
- FastForwardDigest sur fast_forward(months=N)
"""

from __future__ import annotations

from collections.abc import Callable

from shinobi.agents.agent import (
    AgentTickInputs,
    AgentTickResult,
    MajorAgent,
)
from shinobi.agents.cache import LLMCache
from shinobi.agents.reflector import Reflector
from shinobi.agents.roster import AgentRoster
from shinobi.agents.selector import ActionSelector
from shinobi.agents.store import AgentMemoryStore
from shinobi.agents.types import (
    AgentTier,
    DigestEntry,
    FastForwardDigest,
    Observation,
)
from shinobi.personality.store import PersonalityStore
from shinobi.personality.types import NPCPersonality

# Hook pour deriver le contexte d'un agent au moment de son tick
# (npc_id, year, tick) -> AgentTickInputs
TickContextProvider = Callable[[str, int, int], AgentTickInputs]


class TickEngine:
    """Engine du tick multi-agent.

    Un tick :
    1. Decide quels agents simuler (roster.should_simulate_this_tick)
    2. Pour chaque agent : load memory, recoit AgentTickInputs, act() async
    3. Aggregate les actions

    Fast-forward :
    - Tick N mois (~ N*4 ticks d'1 semaine in-game)
    - Pas d'input joueur entre les ticks
    - Digest aggrege les events importants (importance >= threshold)
    """

    DEFAULT_TICKS_PER_MONTH: int = 4  # 1 semaine par tick
    DIGEST_IMPORTANCE_THRESHOLD: float = 0.6

    def __init__(
        self,
        *,
        roster: AgentRoster,
        memory_store: AgentMemoryStore,
        selector: ActionSelector,
        reflector: Reflector,
        cache: LLMCache | None = None,
        personality_store: PersonalityStore | None = None,
        secondary_period_ticks: int = 10,
        ticks_per_month: int = DEFAULT_TICKS_PER_MONTH,
    ) -> None:
        self._roster = roster
        self._store = memory_store
        self._selector = selector
        self._reflector = reflector
        self._cache = cache
        self._personality_store = personality_store
        self._secondary_period_ticks = secondary_period_ticks
        self._ticks_per_month = ticks_per_month
        # Cache d'instances MajorAgent par npc_id (eviter re-load memory)
        self._agents: dict[str, MajorAgent] = {}

    @property
    def roster(self) -> AgentRoster:
        return self._roster

    @property
    def selector(self) -> ActionSelector:
        return self._selector

    @property
    def reflector(self) -> Reflector:
        return self._reflector

    @property
    def cache(self) -> LLMCache | None:
        return self._cache

    # --- internal helpers --------------------------------------------------

    def _get_or_create_agent(self, npc_id: str) -> MajorAgent:
        if npc_id in self._agents:
            return self._agents[npc_id]
        personality: NPCPersonality | None = None
        if self._personality_store is not None:
            personality = self._personality_store.get_personality(npc_id)
        agent = MajorAgent(
            npc_id,
            memory_store=self._store,
            selector=self._selector,
            reflector=self._reflector,
            personality=personality,
        )
        self._agents[npc_id] = agent
        return agent

    def _select_npcs_for_tick(self, tick: int) -> list[str]:
        """Liste des PNJ a simuler ce tick selon roster.tier + tick number."""
        out: list[str] = []
        for entry in self._roster.all_entries:
            if entry.tier == AgentTier.background:
                continue
            if self._roster.should_simulate_this_tick(
                entry.npc_id, tick=tick,
                secondary_period=self._secondary_period_ticks,
            ):
                out.append(entry.npc_id)
        return out

    # --- single tick -------------------------------------------------------

    async def tick(
        self,
        *,
        year: int,
        tick: int,
        context_provider: TickContextProvider | None = None,
        observations_per_npc: dict[str, list[Observation]] | None = None,
    ) -> list[AgentTickResult]:
        """Lance un tick : simulate top-15 + secondary (si tick%10==0).

        `context_provider` : callable optionnel pour fournir le AgentTickInputs
        de chaque PNJ. Si None, un input minimal est genere.
        `observations_per_npc` : permet d'injecter des observations exterieures
        (ex: le bridge qui detecte qu'un PNJ a perceived un event).
        """
        active_npcs = self._select_npcs_for_tick(tick)
        results: list[AgentTickResult] = []
        for npc_id in active_npcs:
            agent = self._get_or_create_agent(npc_id)
            if context_provider is not None:
                inputs = context_provider(npc_id, year, tick)
            else:
                inputs = AgentTickInputs(year=year, tick=tick)
            # Inject pre-built observations
            if observations_per_npc and npc_id in observations_per_npc:
                inputs = AgentTickInputs(
                    year=inputs.year,
                    tick=inputs.tick,
                    location_id=inputs.location_id,
                    present_npc_ids=inputs.present_npc_ids,
                    new_observations=tuple(observations_per_npc[npc_id]),
                    world_summary=inputs.world_summary,
                    relations_summary=inputs.relations_summary,
                    extras=inputs.extras,
                )
            result = await agent.act(inputs)
            self._roster.mark_active(npc_id, year=year, tick=tick)
            results.append(result)
        return results

    # --- fast-forward ------------------------------------------------------

    async def fast_forward(
        self,
        *,
        from_year: int,
        months: int,
        starting_tick: int = 0,
        context_provider: TickContextProvider | None = None,
        digest_importance_threshold: float | None = None,
        canon_scheduler_fn=None,
        canon_scheduler_state=None,
    ) -> FastForwardDigest:
        """Tick N mois sans joueur. Aggrege un digest des events importants.

        Logique :
        - 1 mois = ticks_per_month ticks
        - On itere `months * ticks_per_month` ticks
        - Year increments tous les 12 mois (loop sur n)
        - On accumule les actions importance >= threshold dans le digest

        Spec docs/02 §6.5 : 'events canon se declenchent ou s'annulent
        selon les actions agents'. On supporte ce wiring via deux callables :
        - `canon_scheduler_fn(state, year, tick) -> (new_state, fired, cancelled)`
        - `canon_scheduler_state` : etat opaque passe a chaque tick

        Si `canon_scheduler_fn` est fourni, on tick le canon scheduler a chaque
        tick agent et on ajoute les events fired/cancelled au digest.
        """
        threshold = (
            digest_importance_threshold
            if digest_importance_threshold is not None
            else self.DIGEST_IMPORTANCE_THRESHOLD
        )
        total_ticks = months * self._ticks_per_month
        digest_entries: list[DigestEntry] = []
        active_npcs_seen: set[str] = set()
        actions_total = 0
        cache_hits = 0
        cache_misses = 0
        canon_state = canon_scheduler_state

        for offset in range(total_ticks):
            cur_tick = starting_tick + offset
            cur_year = from_year + (offset // (12 * self._ticks_per_month))
            results = await self.tick(
                year=cur_year, tick=cur_tick,
                context_provider=context_provider,
            )
            actions_total += len(results)
            for r in results:
                active_npcs_seen.add(r.action.npc_id)
                if r.cache_hit:
                    cache_hits += 1
                else:
                    cache_misses += 1
                if r.action.importance >= threshold:
                    digest_entries.append(DigestEntry(
                        year=r.action.year,
                        headline=(
                            f"{r.action.npc_id} : {r.action.type.value} "
                            f"({r.action.content[:80]})"
                        ),
                        npc_ids=(r.action.npc_id,) + (
                            (r.action.target_npc_id,)
                            if r.action.target_npc_id else ()
                        ),
                        importance=r.action.importance,
                        location_id=r.action.location_id,
                    ))

            # Tick canon scheduler : permet aux events canon de fire/cancel
            # selon les actions agents accumulees dans le KG (§6.5).
            if canon_scheduler_fn is not None and canon_state is not None:
                try:
                    canon_state, fired, cancelled = canon_scheduler_fn(
                        canon_state, cur_year, cur_tick,
                    )
                    for ev in fired:
                        digest_entries.append(DigestEntry(
                            year=cur_year,
                            headline=f"Canon event declenche : {ev.event_id}",
                            npc_ids=(),
                            importance=0.9,
                            related_event_id=ev.event_id,
                        ))
                    for ev in cancelled:
                        digest_entries.append(DigestEntry(
                            year=cur_year,
                            headline=f"Canon event annule : {ev.event_id}",
                            npc_ids=(),
                            importance=0.85,
                            related_event_id=ev.event_id,
                        ))
                except Exception:
                    # Defensive : on n'interrompt pas la simulation si scheduler echoue
                    pass

        total_calls = cache_hits + cache_misses
        cache_hit_rate = cache_hits / total_calls if total_calls > 0 else 0.0

        return FastForwardDigest(
            from_year=from_year,
            to_year=from_year + (months // 12),
            months_simulated=months,
            ticks_simulated=total_ticks,
            entries=tuple(digest_entries),
            npcs_active=tuple(sorted(active_npcs_seen)),
            actions_total=actions_total,
            cache_hit_rate=cache_hit_rate,
        )


__all__ = ["TickContextProvider", "TickEngine"]
