"""TickEngine : orchestrateur multi-agent + tick autonome + fast-forward.

Spec docs/02 §6.4 + §6.5 + §11.1 :
- Top-15 simulation active a chaque tick
- PNJ secondaires : par lot toutes les 10 ticks
- Mode fast-forward : tick N mois sans le joueur, digest a la fin
- Strategie latence §11.1 : Sampling top-K agents (5 sur 15) actifs ce tick

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

import random
from collections.abc import Callable

from shinobi.agents.agent import (
    AgentTickInputs,
    AgentTickResult,
    MajorAgent,
)
from shinobi.agents.batch_selector import BatchActionSelector
from shinobi.agents.cache import LLMCache
from shinobi.agents.context_builder import (
    build_relations_summary_for_npc,
    build_world_summary_for_npc,
)
from shinobi.agents.kg_bridge import (
    collect_witness_observations,
    push_actions_to_kg_batch,
)
from shinobi.agents.reflector import Reflector
from shinobi.agents.roster import AgentRoster
from shinobi.agents.selector import ActionSelector, SelectionContext
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
        sample_majors_k: int | None = None,
        sampling_seed: int = 0,
        embeddings_index=None,
        batch_selector: BatchActionSelector | None = None,
        kg_store=None,  # type: KnowledgeGraphStore | None
        social_network=None,  # type: SocialNetwork | None
    ) -> None:
        self._roster = roster
        self._store = memory_store
        self._selector = selector
        self._reflector = reflector
        self._cache = cache
        self._personality_store = personality_store
        self._secondary_period_ticks = secondary_period_ticks
        self._ticks_per_month = ticks_per_month
        # Spec §11.1 : Sampling top-K agents actifs ce tick (default = simulate all)
        # Si sample_majors_k est defini, on tire K majors par tick (deterministe avec seed).
        self._sample_majors_k = sample_majors_k
        self._sampling_seed = sampling_seed
        # Spec §6.1 : embeddings BGE-M3 propage a chaque MajorAgent
        self._embeddings_index = embeddings_index
        # Spec §6.4 : 'PNJ secondaires (~50) : simulation par lot toutes les
        # 10 ticks (1 inference batchee pour le groupe via prompt batched)'.
        # Si fourni, le tier secondary utilise BatchActionSelector au lieu
        # du selector individuel.
        self._batch_selector = batch_selector
        # Spec §6.3 : KG (filtre par known_by_npc_ids) + SocialNetwork pour
        # auto-build world_summary + relations_summary sur chaque tick.
        self._kg_store = kg_store
        self._social_network = social_network
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
            embeddings_index=self._embeddings_index,
        )
        self._agents[npc_id] = agent
        return agent

    def _select_npcs_for_tick(self, tick: int) -> list[str]:
        """Liste plate des PNJ a simuler ce tick (compat backward).

        Pour le batch path, voir _select_npcs_partitioned.
        """
        majors, secondaries = self._select_npcs_partitioned(tick)
        return [*majors, *secondaries]

    def _select_npcs_partitioned(self, tick: int) -> tuple[list[str], list[str]]:
        """Retourne (majors_a_simuler, secondaries_a_simuler) pour ce tick.

        Spec §11.1 : sample_majors_k applique aux majors.
        Spec §6.4 : secondary actifs uniquement si tick % 10 == 0.
        """
        majors: list[str] = []
        secondary_active: list[str] = []
        for entry in self._roster.all_entries:
            if entry.tier == AgentTier.background:
                continue
            if entry.tier == AgentTier.major:
                majors.append(entry.npc_id)
            elif self._roster.should_simulate_this_tick(
                entry.npc_id, tick=tick,
                secondary_period=self._secondary_period_ticks,
            ):
                secondary_active.append(entry.npc_id)

        # Sampling top-K majors ce tick (deterministe par seed+tick)
        if self._sample_majors_k is not None and len(majors) > self._sample_majors_k:
            rng = random.Random(self._sampling_seed + tick)
            majors = rng.sample(sorted(majors), self._sample_majors_k)

        return sorted(majors), sorted(secondary_active)

    # --- single tick -------------------------------------------------------

    async def tick(
        self,
        *,
        year: int,
        tick: int,
        context_provider: TickContextProvider | None = None,
        observations_per_npc: dict[str, list[Observation]] | None = None,
    ) -> list[AgentTickResult]:
        """Lance un tick : simulate top-15 individuels + secondary batches.

        Spec §6.4 : secondary tier utilise BatchActionSelector si fourni
        (1 inference par lot de batch_size). Sinon, fallback selector individuel.

        `context_provider` : callable optionnel pour fournir le AgentTickInputs
        de chaque PNJ. Si None, un input minimal est genere.
        `observations_per_npc` : permet d'injecter des observations exterieures.
        """
        majors, secondaries = self._select_npcs_partitioned(tick)
        results: list[AgentTickResult] = []

        # 1. Top-15 majors : selector individuel (1 inference / agent)
        for npc_id in majors:
            inputs = self._build_inputs(
                npc_id, year, tick, context_provider, observations_per_npc,
            )
            agent = self._get_or_create_agent(npc_id)
            result = await agent.act(inputs)
            self._roster.mark_active(npc_id, year=year, tick=tick)
            results.append(result)

        # 2. Secondary tier (~50) : batch via BatchActionSelector si fourni
        # Spec §6.4 : '1 inference batchee pour le groupe via prompt batched'
        if secondaries:
            if self._batch_selector is not None:
                results.extend(
                    await self._batch_act_secondaries(
                        secondaries, year, tick,
                        context_provider, observations_per_npc,
                    )
                )
            else:
                # Fallback : selector individuel par agent
                for npc_id in secondaries:
                    inputs = self._build_inputs(
                        npc_id, year, tick,
                        context_provider, observations_per_npc,
                    )
                    agent = self._get_or_create_agent(npc_id)
                    result = await agent.act(inputs)
                    self._roster.mark_active(npc_id, year=year, tick=tick)
                    results.append(result)

        # Spec §6.3 : 'Ces actions modifient le KG, qui a son tour change ce
        # que les autres PNJ peuvent observer'.
        if results:
            self._propagate_actions_to_kg_and_witnesses(
                [r.action for r in results], year=year, tick=tick,
            )
        return results

    def _propagate_actions_to_kg_and_witnesses(
        self, actions, *, year: int, tick: int,
    ) -> None:
        """Spec §6.3 : KG mutation + witness observations.

        1. Insere chaque action comme Fact dans le KG (kg_store).
        2. Genere observations pour les temoins (target + bystanders meme
           location). Les observations sont ajoutees aux memoires des agents
           concernes pour le PROCHAIN tick.
        """
        # 1. KG facts
        if self._kg_store is not None:
            try:
                push_actions_to_kg_batch(actions, kg_store=self._kg_store)
            except Exception:
                pass

        # 2. Witness observations : groupe par location
        scene_npcs: dict[str, set[str]] = {}
        for action in actions:
            if action.location_id:
                scene_npcs.setdefault(
                    action.location_id, set(),
                ).add(action.npc_id)
        # Ajoute tous les agents actifs comme potentiels temoins par location
        for entry in self._roster.all_entries:
            if entry.tier == AgentTier.background:
                continue
            for loc_set in scene_npcs.values():
                loc_set.add(entry.npc_id)

        try:
            obs_per_witness = collect_witness_observations(
                actions, npcs_in_scene_per_location=scene_npcs,
            )
        except Exception:
            return

        # Inject les observations dans les memoires des agents temoins
        for witness_id, observations in obs_per_witness.items():
            if witness_id not in self._agents:
                continue
            agent = self._agents[witness_id]
            for obs in observations:
                if obs.npc_id != witness_id:
                    continue
                try:
                    agent.memory.add_observation(obs)
                    self._store.insert_observation(obs)
                except Exception:
                    pass

    def _build_inputs(
        self, npc_id: str, year: int, tick: int,
        context_provider, observations_per_npc,
    ) -> AgentTickInputs:
        """Construit le AgentTickInputs pour un agent.

        Spec §6.3 : auto-fill world_summary (KG known_by) + relations_summary
        (SocialNetwork) si non fournis par context_provider et si KG/social
        sont configures sur le TickEngine.
        """
        if context_provider is not None:
            inputs = context_provider(npc_id, year, tick)
        else:
            inputs = AgentTickInputs(year=year, tick=tick)

        # Spec §6.3 : auto-fill summaries depuis KG + SocialNetwork
        world_summary = inputs.world_summary
        relations_summary = inputs.relations_summary
        if not world_summary and self._kg_store is not None:
            try:
                world_summary = build_world_summary_for_npc(
                    kg_store=self._kg_store, npc_id=npc_id, year=year,
                )
            except Exception:
                world_summary = ""
        if not relations_summary and self._social_network is not None:
            try:
                relations_summary = build_relations_summary_for_npc(
                    social_network=self._social_network,
                    npc_id=npc_id,
                    present_npc_ids=inputs.present_npc_ids,
                )
            except Exception:
                relations_summary = ""

        new_obs = inputs.new_observations
        if observations_per_npc and npc_id in observations_per_npc:
            new_obs = tuple(observations_per_npc[npc_id])

        # Re-build inputs avec auto-filled fields
        if (
            new_obs is not inputs.new_observations
            or world_summary != inputs.world_summary
            or relations_summary != inputs.relations_summary
        ):
            inputs = AgentTickInputs(
                year=inputs.year, tick=inputs.tick,
                location_id=inputs.location_id,
                present_npc_ids=inputs.present_npc_ids,
                new_observations=new_obs,
                world_summary=world_summary,
                relations_summary=relations_summary,
                extras=inputs.extras,
            )
        return inputs

    async def _batch_act_secondaries(
        self,
        npc_ids: list[str],
        year: int,
        tick: int,
        context_provider,
        observations_per_npc,
    ) -> list[AgentTickResult]:
        """Spec §6.4 : 1 inference batchee pour le groupe secondary.

        Pour chaque agent secondary :
        1. perceive (observations injectees)
        2. reflect_if_due (sequentiel, peu frequent)
        3. SelectionContext build
        Puis BatchActionSelector.select_batch -> N actions en
        ceil(N/batch_size) inferences. Distribution des actions aux agents.
        """
        items: list[tuple] = []
        agents: list[MajorAgent] = []
        observations_added: list[int] = []
        reflections_added: list[int] = []

        for npc_id in npc_ids:
            agent = self._get_or_create_agent(npc_id)
            inputs = self._build_inputs(
                npc_id, year, tick, context_provider, observations_per_npc,
            )
            # 1. Perceive
            n_obs = agent.perceive(inputs.new_observations)
            # 2. Reflect periodique
            reflections = await agent.reflect_if_due(year)
            # 3. Build context
            active_plans_text = tuple(
                p.description for p in agent.memory.active_plans()
            )
            ctx = SelectionContext(
                npc_id=npc_id, year=year,
                location_id=inputs.location_id,
                present_npc_ids=inputs.present_npc_ids,
                personality=agent.personality,
                active_plans_text=active_plans_text,
                world_summary=inputs.world_summary,
                relations_summary=inputs.relations_summary,
                extras=inputs.extras,
            )
            items.append((agent.memory, ctx))
            agents.append(agent)
            observations_added.append(n_obs)
            reflections_added.append(len(reflections))

        # Batch inference (1 call par batch_size agents)
        actions = await self._batch_selector.select_batch(items)

        # Distribute + persist
        results: list[AgentTickResult] = []
        for agent, action, n_obs, n_refl in zip(
            agents, actions, observations_added, reflections_added, strict=False,
        ):
            self._store.log_action(action, tick=tick)
            self._roster.mark_active(agent.npc_id, year=year, tick=tick)
            results.append(AgentTickResult(
                action=action,
                new_observations_count=n_obs,
                new_reflections_count=n_refl,
                cache_hit=False,  # batch path : approxime
                used_llm=True,
            ))
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
                    # Spec §6.5 : on peut passer les actions du tick courant
                    # au scheduler pour qu'il puisse muter le world avant
                    # d'evaluer les preconditions canon.
                    tick_actions = [r.action for r in results]
                    import inspect
                    sig = inspect.signature(canon_scheduler_fn)
                    is_async = inspect.iscoroutinefunction(canon_scheduler_fn)
                    if "actions" in sig.parameters:
                        if is_async:
                            canon_state, fired, cancelled = await canon_scheduler_fn(
                                canon_state, cur_year, cur_tick,
                                actions=tick_actions,
                            )
                        else:
                            canon_state, fired, cancelled = canon_scheduler_fn(
                                canon_state, cur_year, cur_tick,
                                actions=tick_actions,
                            )
                    else:
                        if is_async:
                            canon_state, fired, cancelled = await canon_scheduler_fn(
                                canon_state, cur_year, cur_tick,
                            )
                        else:
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
                        # Spec §6.4 : auto-promote NPCs impactes par un event
                        # majeur. Le caller peut fournir `involved_characters`
                        # via attribut sur l'event.
                        involved = getattr(ev, "involved_characters", None) \
                            or getattr(ev, "npc_ids", None) or []
                        if involved:
                            self._roster.on_event_impact(
                                involved, year=cur_year, tick=cur_tick,
                            )
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
