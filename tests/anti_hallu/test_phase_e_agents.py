"""Tests Phase E : multi-agent simulation top-15, memoire 3-niveaux, tick.

Couvre :
- types.py (frozen, immuables, ranges)
- memory.py (retrieve scoring : recency, importance, relevance)
- action_space.py (Pydantic, 7 categories spec, JSON schema valide)
- cache.py (compute_cache_key deterministe + hit/miss)
- store.py (SQLite roundtrip obs/refl/plans/roster/actions)
- roster.py (top-15 + secondary 50 + promote/demote/should_simulate)
- selector.py (deterministic fallback + cache hit + LLM call mocked)
- reflector.py (deterministic fallback + LLM call mocked)
- agent.py (MajorAgent.act perceive+reflect+select)
- tick.py (TickEngine tick + fast_forward 30 jours -> digest coherent)

Spec validation :
- 'Tests : 30 jours simulation passive, output coherent' :
  test_phase_e_30days_simulation_passive_output_coherent
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from shinobi.agents import (
    AGENT_ACTION_JSON_SCHEMA,
    DEFAULT_SECONDARY_50,
    DEFAULT_TOP_15,
    AgentAction,
    AgentActionType,
    AgentMemory,
    AgentMemoryStore,
    AgentTickInputs,
    AgentTier,
    LLMCache,
    MajorAgent,
    Observation,
    Plan,
    PlanStatus,
    Reflection,
    Reflector,
    SelectionContext,
    TickEngine,
    composite_score,
    compute_cache_key,
    deterministic_fallback_action,
    deterministic_fallback_reflections,
    initialize_roster,
    is_trivial_action,
    jaccard_similarity,
    recency_score,
)
from shinobi.agents.selector import ActionSelector

# ============================================================================
# 1. Types Pydantic
# ============================================================================


class TestTypes:
    def test_observation_required_fields(self) -> None:
        o = Observation(npc_id="x", text="something", year=12)
        assert o.id.startswith("obs_")
        assert o.importance == 0.5
        assert o.kind == "observation"

    def test_observation_immutable(self) -> None:
        o = Observation(npc_id="x", text="t", year=12)
        with pytest.raises((ValidationError, TypeError, AttributeError)):
            o.year = 13  # type: ignore[misc]

    def test_observation_importance_range(self) -> None:
        with pytest.raises(ValidationError):
            Observation(npc_id="x", text="t", year=12, importance=1.5)

    def test_reflection_default_importance(self) -> None:
        r = Reflection(npc_id="x", text="insight", year=12)
        assert r.importance == 0.7  # spec : reflections plus important par default
        assert r.kind == "reflection"

    def test_plan_status_default(self) -> None:
        p = Plan(npc_id="x", description="train", year_started=12)
        assert p.status == PlanStatus.pending


# ============================================================================
# 2. Memory + retrieval scoring (Park et al)
# ============================================================================


class TestMemoryRetrieval:
    def test_jaccard_similarity_basic(self) -> None:
        assert jaccard_similarity("hello world", "hello world") > 0.5
        assert jaccard_similarity("foo bar", "totally different") == 0.0

    def test_recency_score_now(self) -> None:
        now = time.time()
        assert recency_score(now, now_ts=now) == 1.0

    def test_recency_score_30_days_ago(self) -> None:
        now = time.time()
        thirty_days_ago = now - (30 * 86400)
        # decay = ln(2)/30d -> 30 jours = 0.5 (demi-vie)
        assert abs(recency_score(thirty_days_ago, now_ts=now) - 0.5) < 0.01

    def test_recency_score_decay_monotonic(self) -> None:
        now = time.time()
        a = recency_score(now - 1000, now_ts=now)
        b = recency_score(now - 10000, now_ts=now)
        assert a > b

    def test_composite_score_uses_three_components(self) -> None:
        now = time.time()
        # Tres recent + tres important + parfait match
        o1 = Observation(
            npc_id="x", text="massacre clan uchiha", year=8, importance=0.9,
            created_at_ts=now,
        )
        # Vieux + faible + pas pertinent
        o2 = Observation(
            npc_id="x", text="petit dejeuner ramen", year=10, importance=0.1,
            created_at_ts=now - 100 * 86400,
        )
        s1 = composite_score(o1, "massacre clan", now_ts=now)
        s2 = composite_score(o2, "massacre clan", now_ts=now)
        assert s1 > s2

    def test_memory_add_and_retrieve(self) -> None:
        m = AgentMemory(npc_id="sasuke")
        for i in range(5):
            m.add_observation(Observation(
                npc_id="sasuke",
                text=f"fact {i} about training",
                year=12, importance=0.3 + i * 0.1,
            ))
        m.add_observation(Observation(
            npc_id="sasuke", text="massacre clan uchiha",
            year=8, importance=1.0,
        ))
        top3 = m.retrieve("massacre", top_k=3)
        assert len(top3) == 3
        # Le top doit etre le massacre (importance 1.0 + relevance match)
        assert "massacre" in top3[0][1].text

    def test_memory_npc_id_validation(self) -> None:
        m = AgentMemory(npc_id="sasuke")
        with pytest.raises(ValueError):
            m.add_observation(Observation(npc_id="naruto", text="t", year=12))

    def test_memory_size_property(self) -> None:
        m = AgentMemory(npc_id="x")
        assert m.size == 0
        m.add_observation(Observation(npc_id="x", text="t", year=12))
        m.add_reflection(Reflection(npc_id="x", text="r", year=12))
        m.add_plan(Plan(npc_id="x", description="d", year_started=12))
        assert m.size == 3

    def test_memory_active_plans(self) -> None:
        m = AgentMemory(npc_id="x")
        m.add_plan(Plan(npc_id="x", description="p1", year_started=12))
        m.add_plan(Plan(
            npc_id="x", description="p2", year_started=12,
            status=PlanStatus.completed,
        ))
        active = m.active_plans()
        assert len(active) == 1
        assert active[0].description == "p1"

    def test_memory_filter_by_year(self) -> None:
        m = AgentMemory(npc_id="x")
        m.add_observation(Observation(npc_id="x", text="t1", year=10))
        m.add_observation(Observation(npc_id="x", text="t2", year=15))
        result = m.filter_by_year(year_min=12)
        assert len(result) == 1
        assert result[0].year == 15

    def test_memory_retrieve_top_texts_only(self) -> None:
        m = AgentMemory(npc_id="x")
        m.add_observation(Observation(npc_id="x", text="hello", year=12, importance=0.9))
        m.add_observation(Observation(npc_id="x", text="goodbye", year=12, importance=0.1))
        texts = m.retrieve_top_texts("hello", top_k=2)
        assert texts[0] == "hello"


# ============================================================================
# 3. Action space
# ============================================================================


class TestActionSpace:
    def test_seven_canonical_types_present(self) -> None:
        # Spec §6.3 : declarer intention, parler, voyager, attaquer, chercher info, mediter, comploter
        for name in (
            "declare_intention", "speak", "travel", "attack",
            "search_information", "meditate", "plot",
        ):
            assert name in {t.value for t in AgentActionType}

    def test_action_immutable(self) -> None:
        a = AgentAction(npc_id="x", type=AgentActionType.idle, year=12)
        with pytest.raises((ValidationError, TypeError, AttributeError)):
            a.year = 13  # type: ignore[misc]

    def test_json_schema_keys(self) -> None:
        assert "type" in AGENT_ACTION_JSON_SCHEMA["properties"]
        assert "content" in AGENT_ACTION_JSON_SCHEMA["properties"]
        assert "type" in AGENT_ACTION_JSON_SCHEMA["required"]

    def test_json_schema_enum_matches(self) -> None:
        spec_types = set(
            AGENT_ACTION_JSON_SCHEMA["properties"]["type"]["enum"]
        )
        assert spec_types == {t.value for t in AgentActionType}

    def test_is_trivial_action(self) -> None:
        meditate = AgentAction(
            npc_id="x", type=AgentActionType.meditate, year=12,
        )
        speak = AgentAction(
            npc_id="x", type=AgentActionType.speak, year=12,
            target_npc_id="y", content="bonjour",
        )
        assert is_trivial_action(meditate) is True
        assert is_trivial_action(speak) is False


# ============================================================================
# 4. LLMCache
# ============================================================================


class TestLLMCache:
    def test_compute_cache_key_deterministic(self) -> None:
        k1 = compute_cache_key("hello", "qwen3-4b", 0.7)
        k2 = compute_cache_key("hello", "qwen3-4b", 0.7)
        assert k1 == k2

    def test_compute_cache_key_different_for_different_inputs(self) -> None:
        k1 = compute_cache_key("hello", "qwen3-4b", 0.7)
        k2 = compute_cache_key("world", "qwen3-4b", 0.7)
        k3 = compute_cache_key("hello", "qwen3-4b", 0.8)
        assert k1 != k2
        assert k1 != k3

    def test_cache_set_and_get(self) -> None:
        with LLMCache(None) as cache:
            cache.set("k", {"action": "speak"}, model_id="qwen3-4b")
            assert cache.get("k") == {"action": "speak"}
            assert cache.has("k") is True

    def test_cache_miss(self) -> None:
        with LLMCache(None) as cache:
            assert cache.get("ghost") is None

    def test_cache_hit_rate(self) -> None:
        with LLMCache(None) as cache:
            cache.set("k", {"x": 1}, model_id="m")
            cache.get("ghost")  # miss
            cache.get("k")  # hit
            cache.get("k")  # hit
            assert cache.hit_rate == pytest.approx(2/3, abs=0.01)

    def test_cache_persisted_to_disk(self, tmp_path: Path) -> None:
        path = tmp_path / "c.sqlite"
        with LLMCache(path) as cache:
            cache.set("k", {"v": "x"})
        with LLMCache(path) as cache2:
            assert cache2.get("k") == {"v": "x"}


# ============================================================================
# 5. AgentMemoryStore
# ============================================================================


class TestAgentMemoryStore:
    def test_observation_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "a.sqlite"
        with AgentMemoryStore(path) as store:
            o = Observation(npc_id="x", text="t", year=12, importance=0.6)
            store.insert_observation(o)
        with AgentMemoryStore(path) as store2:
            loaded = store2.list_observations("x")
            assert len(loaded) == 1
            assert loaded[0].text == "t"
            assert loaded[0].importance == 0.6

    def test_reflection_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "a.sqlite"
        with AgentMemoryStore(path) as store:
            r = Reflection(
                npc_id="x", text="insight", year=12,
                source_observation_ids=("obs_aaa", "obs_bbb"),
            )
            store.insert_reflection(r)
        with AgentMemoryStore(path) as store2:
            loaded = store2.list_reflections("x")
            assert len(loaded) == 1
            assert loaded[0].source_observation_ids == ("obs_aaa", "obs_bbb")

    def test_plan_status_update(self, tmp_path: Path) -> None:
        with AgentMemoryStore(tmp_path / "a.sqlite") as store:
            p = Plan(npc_id="x", description="d", year_started=12)
            store.insert_plan(p)
            assert store.update_plan_status(p.id, PlanStatus.completed) is True
            loaded = store.list_plans("x")
            assert loaded[0].status == PlanStatus.completed

    def test_load_memory_aggregates_all(self) -> None:
        with AgentMemoryStore(None) as store:
            store.insert_observation(Observation(npc_id="x", text="o", year=12))
            store.insert_reflection(Reflection(npc_id="x", text="r", year=12))
            store.insert_plan(Plan(npc_id="x", description="p", year_started=12))
            mem = store.load_memory("x")
            assert mem.size == 3

    def test_action_log_roundtrip(self) -> None:
        with AgentMemoryStore(None) as store:
            a = AgentAction(
                npc_id="x", type=AgentActionType.speak, year=12,
                target_npc_id="y", content="bonjour",
            )
            store.log_action(a, tick=5)
            actions = store.list_actions("x")
            assert len(actions) == 1
            assert actions[0].content == "bonjour"


# ============================================================================
# 6. AgentRoster
# ============================================================================


class TestAgentRoster:
    def test_top_15_initialized(self) -> None:
        with AgentMemoryStore(None) as store:
            roster = initialize_roster(store)
            assert roster.major_count == 15
            # Verifier que tous les top-15 spec sont presents
            for npc_id in DEFAULT_TOP_15:
                assert roster.tier_for(npc_id) == AgentTier.major

    def test_secondary_50_initialized(self) -> None:
        with AgentMemoryStore(None) as store:
            roster = initialize_roster(store)
            # >= 50 (on a 52)
            assert roster.secondary_count >= 50

    def test_promote_demote(self) -> None:
        with AgentMemoryStore(None) as store:
            roster = initialize_roster(store)
            new_id = "test_unknown_npc"
            assert roster.tier_for(new_id) == AgentTier.background
            # background -> secondary
            roster.promote(new_id)
            assert roster.tier_for(new_id) == AgentTier.secondary
            # secondary -> major
            roster.promote(new_id)
            assert roster.tier_for(new_id) == AgentTier.major
            # demote major -> secondary
            roster.demote(new_id)
            assert roster.tier_for(new_id) == AgentTier.secondary
            # demote secondary -> background
            roster.demote(new_id)
            assert roster.tier_for(new_id) == AgentTier.background

    def test_should_simulate_major_every_tick(self) -> None:
        with AgentMemoryStore(None) as store:
            roster = initialize_roster(store)
            for tick in range(5):
                assert roster.should_simulate_this_tick(
                    DEFAULT_TOP_15[0], tick=tick,
                ) is True

    def test_should_simulate_secondary_every_10_ticks(self) -> None:
        with AgentMemoryStore(None) as store:
            roster = initialize_roster(store)
            sec_id = DEFAULT_SECONDARY_50[0]
            assert roster.should_simulate_this_tick(sec_id, tick=10) is True
            assert roster.should_simulate_this_tick(sec_id, tick=11) is False
            assert roster.should_simulate_this_tick(sec_id, tick=20) is True

    def test_should_simulate_background_never(self) -> None:
        with AgentMemoryStore(None) as store:
            roster = initialize_roster(store)
            for tick in (0, 1, 10, 100):
                assert roster.should_simulate_this_tick(
                    "ghost_npc", tick=tick,
                ) is False

    def test_mark_active_persists(self) -> None:
        with AgentMemoryStore(None) as store:
            roster = initialize_roster(store)
            roster.mark_active(DEFAULT_TOP_15[0], year=15, tick=42)
            entry = roster.get(DEFAULT_TOP_15[0])
            assert entry is not None
            assert entry.last_active_year == 15
            assert entry.last_active_tick == 42


# ============================================================================
# 7. ActionSelector
# ============================================================================


class TestActionSelector:
    def test_deterministic_fallback_with_plan(self) -> None:
        ctx = SelectionContext(
            npc_id="x", year=12,
            active_plans_text=("entrainer son taijutsu",),
        )
        action = deterministic_fallback_action(ctx)
        assert action.type == AgentActionType.declare_intention
        assert "taijutsu" in action.content

    def test_deterministic_fallback_with_present_npcs(self) -> None:
        ctx = SelectionContext(
            npc_id="x", year=12,
            present_npc_ids=("y", "z"),
        )
        action = deterministic_fallback_action(ctx)
        assert action.type == AgentActionType.idle

    def test_deterministic_fallback_solo(self) -> None:
        ctx = SelectionContext(npc_id="x", year=12)
        action = deterministic_fallback_action(ctx)
        assert action.type == AgentActionType.meditate

    def test_select_uses_cache_on_hit(self) -> None:
        async def run() -> None:
            llm_call_count = 0

            async def mock_llm(sys_p, user_p, schema, model, temp):
                nonlocal llm_call_count
                llm_call_count += 1
                return {"type": "speak", "content": "bonjour", "importance": 0.5}

            with LLMCache(None) as cache:
                # trivial_state_shortcut=False : on teste le path LLM
                selector = ActionSelector(
                    llm_call=mock_llm, cache=cache,
                    trivial_state_shortcut=False,
                )
                mem = AgentMemory(npc_id="x")
                ctx = SelectionContext(npc_id="x", year=12)
                a1 = await selector.select(mem, ctx)
                a2 = await selector.select(mem, ctx)
                assert a1.content == "bonjour"
                assert a2.content == "bonjour"
                assert llm_call_count == 1  # 2eme appel = cache hit

        asyncio.run(run())

    def test_select_falls_back_when_llm_returns_none(self) -> None:
        async def run() -> None:
            async def mock_llm(*args, **kwargs):
                return None
            selector = ActionSelector(llm_call=mock_llm)
            mem = AgentMemory(npc_id="x")
            ctx = SelectionContext(npc_id="x", year=12)
            action = await selector.select(mem, ctx)
            # Tombe sur le fallback deterministe -> meditate
            assert action.type == AgentActionType.meditate

        asyncio.run(run())

    def test_select_no_llm_uses_fallback(self) -> None:
        async def run() -> None:
            selector = ActionSelector(llm_call=None)
            mem = AgentMemory(npc_id="x")
            ctx = SelectionContext(npc_id="x", year=12)
            action = await selector.select(mem, ctx)
            assert action.type == AgentActionType.meditate

        asyncio.run(run())


# ============================================================================
# 8. Reflector
# ============================================================================


class TestReflector:
    def test_filter_observations_threshold(self) -> None:
        r = Reflector(importance_threshold=0.5)
        obs = [
            Observation(npc_id="x", text="t1", year=12, importance=0.3),
            Observation(npc_id="x", text="t2", year=12, importance=0.6),
        ]
        kept = r.filter_observations(obs)
        assert len(kept) == 1
        assert kept[0].importance == 0.6

    def test_deterministic_fallback_one_reflection(self) -> None:
        obs = [
            Observation(
                npc_id="x", text=f"action {i}",
                year=12 + i, importance=0.6,
            )
            for i in range(3)
        ]
        out = deterministic_fallback_reflections("x", obs, 15)
        assert len(out) == 1
        assert out[0].source_observation_ids == tuple(o.id for o in obs)

    def test_reflect_uses_llm_when_available(self) -> None:
        async def run() -> None:
            async def mock_llm(*args, **kwargs):
                return {
                    "reflections": [{
                        "text": "Insight LLM-genere",
                        "gist": "auto",
                        "importance": 0.8,
                        "source_observation_ids": [],
                    }],
                }

            r = Reflector(llm_call=mock_llm, importance_threshold=0.0)
            obs = [Observation(npc_id="x", text="t", year=12, importance=1.0)]
            refls = await r.reflect("x", 12, obs)
            assert len(refls) == 1
            assert "LLM-genere" in refls[0].text

        asyncio.run(run())

    def test_reflect_fallback_when_no_obs(self) -> None:
        async def run() -> None:
            r = Reflector()
            refls = await r.reflect("x", 12, [])
            assert refls == []

        asyncio.run(run())

    def test_reflect_fallback_when_llm_returns_invalid(self) -> None:
        async def run() -> None:
            async def bad_llm(*args, **kwargs):
                return {"unrelated": "junk"}

            r = Reflector(llm_call=bad_llm, importance_threshold=0.0)
            obs = [Observation(npc_id="x", text="t", year=12, importance=1.0)]
            refls = await r.reflect("x", 12, obs)
            # Fallback deterministe
            assert len(refls) == 1

        asyncio.run(run())


# ============================================================================
# 9. MajorAgent
# ============================================================================


class TestMajorAgent:
    def test_act_basic_no_llm(self) -> None:
        async def run() -> None:
            store = AgentMemoryStore(None)
            selector = ActionSelector()
            reflector = Reflector()
            agent = MajorAgent(
                "uchiha_sasuke",
                memory_store=store,
                selector=selector,
                reflector=reflector,
            )
            inputs = AgentTickInputs(year=12, tick=1)
            result = await agent.act(inputs)
            assert result.action.npc_id == "uchiha_sasuke"
            assert result.action.year == 12

        asyncio.run(run())

    def test_perceive_filters_other_npcs(self) -> None:
        async def run() -> None:
            store = AgentMemoryStore(None)
            agent = MajorAgent(
                "x", memory_store=store,
                selector=ActionSelector(), reflector=Reflector(),
            )
            obs_x = Observation(npc_id="x", text="for x", year=12)
            obs_y = Observation(npc_id="y", text="for y", year=12)
            n = agent.perceive([obs_x, obs_y])
            assert n == 1
            assert agent.memory.size == 1

        asyncio.run(run())

    def test_reflect_period_triggers_reflections(self) -> None:
        """Apres REFLECTION_PERIOD_TICKS ticks, agent.reflect_if_due retourne
        une Reflection (fallback deterministe)."""
        async def run() -> None:
            store = AgentMemoryStore(None)
            agent = MajorAgent(
                "x", memory_store=store,
                selector=ActionSelector(), reflector=Reflector(importance_threshold=0.0),
            )
            agent.perceive([
                Observation(npc_id="x", text="something happened", year=12, importance=0.7),
            ])
            # Il faut REFLECTION_PERIOD_TICKS + 1 ticks pour atteindre le
            # seuil (tick 0..N-1 incrementent, tick N declenche reflect)
            for tick in range(MajorAgent.REFLECTION_PERIOD_TICKS + 1):
                await agent.act(AgentTickInputs(year=12, tick=tick))
            assert len(agent.memory.reflections) >= 1

        asyncio.run(run())


# ============================================================================
# 10. TickEngine
# ============================================================================


class TestTickEngine:
    def test_single_tick_simulates_all_majors(self) -> None:
        async def run() -> None:
            store = AgentMemoryStore(None)
            roster = initialize_roster(store)
            engine = TickEngine(
                roster=roster, memory_store=store,
                selector=ActionSelector(), reflector=Reflector(),
            )
            results = await engine.tick(year=12, tick=1)
            # 15 majors, secondary tick=1 % 10 != 0 -> 0
            assert len(results) == 15

        asyncio.run(run())

    def test_tick_10_includes_secondary(self) -> None:
        async def run() -> None:
            store = AgentMemoryStore(None)
            roster = initialize_roster(store)
            engine = TickEngine(
                roster=roster, memory_store=store,
                selector=ActionSelector(), reflector=Reflector(),
            )
            results = await engine.tick(year=12, tick=10)
            # 15 majors + ~52 secondary = 67
            assert len(results) >= 60

        asyncio.run(run())

    def test_fast_forward_30_days(self) -> None:
        """Spec : 'Tests : 30 jours simulation passive, output coherent'."""
        async def run() -> None:
            store = AgentMemoryStore(None)
            roster = initialize_roster(store)
            cache = LLMCache(None)
            engine = TickEngine(
                roster=roster, memory_store=store,
                selector=ActionSelector(cache=cache),
                reflector=Reflector(cache=cache),
                cache=cache,
            )
            # 30 jours = ~1 mois (4 ticks/mois -> 4 ticks pour le mois)
            digest = await engine.fast_forward(
                from_year=12, months=1, starting_tick=0,
            )
            # Verifications coherence :
            assert digest.from_year == 12
            assert digest.months_simulated == 1
            assert digest.ticks_simulated == 4
            assert digest.actions_total > 0
            # Au moins les 15 majors ont agi sur les 4 ticks
            assert len(digest.npcs_active) >= 15
            # Hit rate calculable
            assert 0.0 <= digest.cache_hit_rate <= 1.0

        asyncio.run(run())


# ============================================================================
# 11. Validation spec : 30 jours simulation passive, output coherent
# ============================================================================


class TestPhaseEValidation30DaysSimulation:
    """docs/02 §13 Phase E :
    'Tests : 30 jours simulation passive, output coherent'."""

    def test_30_days_simulation_passive_output_coherent(self) -> None:
        """30 jours en mode fast-forward sans player input.

        Coherence verifiee :
        - tous les majors apparaissent dans le digest npcs_active
        - chaque agent persiste un historique d'actions dans le store
        - aucune action n'a year < 12 (coherence temporelle)
        - cache_hit_rate >= 0 (pas d'exception)
        """
        async def run() -> None:
            store = AgentMemoryStore(None)
            roster = initialize_roster(store)
            cache = LLMCache(None)
            engine = TickEngine(
                roster=roster, memory_store=store,
                selector=ActionSelector(cache=cache),
                reflector=Reflector(cache=cache),
                cache=cache,
            )

            # 30 jours = ~1 mois (~4 ticks/mois) ; on prend 1 mois
            digest = await engine.fast_forward(
                from_year=12, months=1, starting_tick=0,
            )

            # 1. Tous les majors actifs au moins une fois
            major_ids = set(roster.major_npc_ids())
            active_ids = set(digest.npcs_active)
            assert major_ids.issubset(active_ids), (
                f"Manquants : {major_ids - active_ids}"
            )

            # 2. Historique d'actions persiste pour chaque major
            for npc_id in major_ids:
                actions = store.list_actions(npc_id)
                assert len(actions) > 0, f"Aucune action pour {npc_id}"

            # 3. Coherence temporelle : year >= 12 partout
            all_actions = store.list_actions(None)
            for a in all_actions:
                assert a.year >= 12, f"Action year {a.year} < 12"

            # 4. cache_hit_rate dans [0, 1]
            assert 0.0 <= digest.cache_hit_rate <= 1.0

            # 5. Aucune exception sur l'iteration : si on arrive ici, OK

        asyncio.run(run())

    def test_fast_forward_3_months_increases_year_appropriately(self) -> None:
        """3 mois ne traverse pas une annee (4 ticks/mois * 3 = 12 ticks),
        12 mois -> +1 an."""
        async def run() -> None:
            store = AgentMemoryStore(None)
            roster = initialize_roster(store)
            engine = TickEngine(
                roster=roster, memory_store=store,
                selector=ActionSelector(), reflector=Reflector(),
            )
            digest_3 = await engine.fast_forward(from_year=12, months=3)
            digest_12 = await engine.fast_forward(from_year=12, months=12)
            assert digest_3.to_year == 12  # pas de transition d'annee
            assert digest_12.to_year == 13  # 12 mois -> +1 an

        asyncio.run(run())


# ============================================================================
# 12. EmbeddingsIndex BGE-M3 (gap closure spec §6.1)
# ============================================================================


class TestEmbeddingsIndexBGE:
    """Spec docs/02 §6.1 : 'embeddings BGE-M3 pour le retrieval semantique'."""

    def test_cosine_similarity_basic(self) -> None:
        from shinobi.agents import cosine_similarity
        assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
        assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_index_and_retrieve_semantic(self) -> None:
        from shinobi.agents import EmbeddingsIndex

        # Encoder mock : maps keywords to fixed vectors
        def encoder(texts: list[str]) -> list[list[float]]:
            return [
                [1.0, 0.0, 0.0] if "massacre" in t.lower()
                else [0.0, 1.0, 0.0] if "training" in t.lower()
                else [0.0, 0.0, 1.0]
                for t in texts
            ]

        def query_encoder(t: str) -> list[float]:
            return encoder([t])[0]

        idx = EmbeddingsIndex(
            None, encoder=encoder, query_encoder=query_encoder,
        )
        idx.index_entry("x", entry_id="obs_1", kind="observation", text="massacre clan")
        idx.index_entry("x", entry_id="obs_2", kind="observation", text="training day")
        idx.index_entry("x", entry_id="obs_3", kind="observation", text="random fact")

        top = idx.retrieve_semantic("x", query="massacre", top_k=2)
        assert top[0][1] == "obs_1"
        assert top[0][0] > top[1][0]

    def test_retrieve_semantic_no_encoder_returns_empty(self) -> None:
        from shinobi.agents import EmbeddingsIndex
        idx = EmbeddingsIndex(None, encoder=None, query_encoder=None)
        idx.index_entry("x", entry_id="obs_1", kind="observation", text="t")
        # Sans encoder, l'index n'enregistre rien
        assert idx.size("x") == 0
        # Et retrieve renvoie []
        assert idx.retrieve_semantic("x", query="t", top_k=5) == []

    def test_index_entries_bulk(self) -> None:
        from shinobi.agents import EmbeddingsIndex

        def encoder(texts: list[str]) -> list[list[float]]:
            return [[float(i), 0.0, 0.0] for i, _ in enumerate(texts)]

        idx = EmbeddingsIndex(None, encoder=encoder)
        obs = [
            Observation(npc_id="x", text=f"obs {i}", year=12)
            for i in range(5)
        ]
        n = idx.index_entries("x", obs)
        assert n == 5
        assert idx.size("x") == 5

    def test_memory_auto_indexes_on_add(self) -> None:
        """AgentMemory avec embeddings_index auto-indexe a chaque add_*."""
        from shinobi.agents import AgentMemory, EmbeddingsIndex

        encoder = lambda texts: [[1.0, 0.0, 0.0] for _ in texts]
        idx = EmbeddingsIndex(None, encoder=encoder, query_encoder=lambda t: [1.0, 0.0, 0.0])
        m = AgentMemory(npc_id="x", embeddings_index=idx)
        assert idx.size("x") == 0
        m.add_observation(Observation(npc_id="x", text="hello", year=12))
        assert idx.size("x") == 1
        m.add_reflection(Reflection(npc_id="x", text="insight", year=12))
        assert idx.size("x") == 2
        m.add_plan(Plan(npc_id="x", description="train", year_started=12))
        assert idx.size("x") == 3

    def test_memory_retrieve_uses_self_index_by_default(self) -> None:
        """Si AgentMemory a un embeddings_index, retrieve l'utilise sans
        avoir besoin de le passer en kwarg."""
        from shinobi.agents import AgentMemory, EmbeddingsIndex

        def encoder(texts):
            return [
                [1.0, 0.0, 0.0] if "massacre" in t.lower() else [0.0, 1.0, 0.0]
                for t in texts
            ]

        def query_encoder(t):
            return encoder([t])[0]

        idx = EmbeddingsIndex(None, encoder=encoder, query_encoder=query_encoder)
        m = AgentMemory(npc_id="sasuke", embeddings_index=idx)
        m.add_observation(Observation(
            npc_id="sasuke", text="massacre clan", year=8, importance=0.5,
        ))
        m.add_observation(Observation(
            npc_id="sasuke", text="ate breakfast", year=12, importance=0.5,
        ))
        # Pas de embeddings_index= dans le call -> doit utiliser self._embeddings_index
        top = m.retrieve("massacre", top_k=2)
        assert "massacre" in top[0][1].text

    def test_save_passive_state_persists_world_without_action_result(
        self, tmp_path: Path,
    ) -> None:
        """Spec §6.5 'le monde tourne sans le joueur' -> apres fast-forward,
        le world state DOIT etre persiste. save_passive_state ne requiert
        pas d'action_result (pas de turn log) et persiste character + world."""
        from shinobi.canon.profiles import CanonicityProfile
        from shinobi.config import settings
        from shinobi.engine.character import (
            Character,
            CoreStats,
            ExtendedStats,
        )
        from shinobi.engine.world import create_default_world
        from shinobi.persistence import saves as save_module
        from shinobi.types import Gender

        original_saves = settings.saves_path
        settings.saves_path = str(tmp_path)
        try:
            character = Character(
                id="test_save_passive",
                name="TestSavePassive",
                gender=Gender.female,
                birth_year=0, birth_date="06-15", age_years=12,
                village_of_origin="konohagakure",
                current_village="konohagakure",
                current_location="konohagakure",
                rank="genin",
                stats=CoreStats(), extended_stats=ExtendedStats(),
            )
            world = create_default_world(
                profile=CanonicityProfile.default(), starting_year=12,
            )
            save_id = save_module.create_save(character, world)

            # Simule fast-forward : world avance, character vieillit
            advanced_world = world.with_time(
                year=13, date="06-15", hour=8, minute=0,
            )
            aged_character = character.model_copy(update={"age_years": 13})

            save_module.save_passive_state(
                save_id,
                turn_number=100,
                new_character=aged_character,
                new_world=advanced_world,
                seed_state=42,
            )

            # Reload + verifie persistance
            loaded_char, loaded_world, _ = save_module.load_save(save_id)
            assert loaded_world.current_year == 13
            assert loaded_world.current_date == "06-15"
            assert loaded_char.age_years == 13
        finally:
            settings.saves_path = original_saves

    def test_tick_engine_uses_batch_selector_for_secondary_tier(self) -> None:
        """Spec §6.4 : 'PNJ secondaires (~50) : simulation par lot toutes les
        10 ticks (1 inference batchee pour le groupe via prompt batched)'.

        TickEngine doit utiliser BatchActionSelector pour le tier secondary
        au lieu du selector individuel. Verifie en comptant les llm_calls."""
        async def run() -> None:
            from shinobi.agents import (
                AgentMemoryStore,
                BatchActionSelector,
                Reflector,
                TickEngine,
                initialize_roster,
            )

            llm_call_count = 0

            async def mock_batch_llm(sys_p, user_p, schema, model, temp):
                nonlocal llm_call_count
                llm_call_count += 1
                # 5 actions par batch (BatchActionSelector batch_size=5)
                return {
                    "actions": [
                        {"type": "idle", "content": f"act_{i}", "importance": 0.2}
                        for i in range(5)
                    ],
                }

            store = AgentMemoryStore(None)
            roster = initialize_roster(store)
            individual_selector = ActionSelector()
            batch_selector = BatchActionSelector(
                llm_call=mock_batch_llm, batch_size=5,
            )
            engine = TickEngine(
                roster=roster, memory_store=store,
                selector=individual_selector,
                reflector=Reflector(),
                batch_selector=batch_selector,
            )
            # tick=10 -> secondary actifs (% 10 == 0)
            await engine.tick(year=12, tick=10)
            # ~52 secondary -> ceil(52/5) = 11 batches -> 11 inferences LLM
            assert llm_call_count >= 10
            assert llm_call_count <= 12

        asyncio.run(run())

    def test_tick_engine_no_batch_selector_falls_back_to_individual(self) -> None:
        """Sans batch_selector, le tier secondary utilise le selector
        individuel (compatibilite)."""
        async def run() -> None:
            from shinobi.agents import (
                AgentMemoryStore,
                Reflector,
                TickEngine,
                initialize_roster,
            )

            store = AgentMemoryStore(None)
            roster = initialize_roster(store)
            engine = TickEngine(
                roster=roster, memory_store=store,
                selector=ActionSelector(),
                reflector=Reflector(),
                batch_selector=None,
            )
            results = await engine.tick(year=12, tick=10)
            # 15 majors + ~52 secondary
            assert len(results) >= 60

        asyncio.run(run())

    def test_apply_action_to_world_state_travel_updates_location(self) -> None:
        """Spec §6.5 : agent travel -> world.npc_states.current_location."""
        from shinobi.agents import apply_action_to_world_state
        from shinobi.engine.world import NPCState, WorldState

        npc_state = NPCState(
            character_id="naruto", current_location="konoha",
            current_year=12, current_age=12, current_rank="genin",
        )
        world = WorldState(
            current_year=12, current_date="01-01",
            current_hour=8, current_minute=0,
            seed=42, npc_states={"naruto": npc_state},
        )
        action = AgentAction(
            npc_id="naruto", type=AgentActionType.travel, year=12,
            location_id="suna", content="part en mission",
            importance=0.7,
        )
        new_world = apply_action_to_world_state(action, world)
        assert new_world.npc_states["naruto"].current_location == "suna"

    def test_apply_action_to_world_state_attack_marks_threatened(self) -> None:
        """Spec §6.5 : attack high-impact -> target.psychological_state."""
        from shinobi.agents import apply_action_to_world_state
        from shinobi.engine.world import NPCState, WorldState

        target = NPCState(
            character_id="hokage", current_location="konoha",
            current_year=12, current_age=70, current_rank="kage",
        )
        world = WorldState(
            current_year=12, current_date="01-01",
            current_hour=8, current_minute=0,
            seed=42, npc_states={"hokage": target},
        )
        action = AgentAction(
            npc_id="orochimaru", type=AgentActionType.attack, year=12,
            target_npc_id="hokage", content="invasion konoha",
            importance=0.9,
        )
        new_world = apply_action_to_world_state(action, world)
        assert new_world.npc_states["hokage"].psychological_state == "threatened"

    def test_apply_action_low_importance_no_mutation(self) -> None:
        """Action attack avec importance < 0.7 ne mute PAS le world."""
        from shinobi.agents import apply_action_to_world_state
        from shinobi.engine.world import NPCState, WorldState

        target = NPCState(
            character_id="hokage", current_location="konoha",
            current_year=12, current_age=70, current_rank="kage",
        )
        world = WorldState(
            current_year=12, current_date="01-01",
            current_hour=8, current_minute=0,
            seed=42, npc_states={"hokage": target},
        )
        action = AgentAction(
            npc_id="genin", type=AgentActionType.attack, year=12,
            target_npc_id="hokage", content="provocation mineure",
            importance=0.4,  # below threshold 0.7
        )
        new_world = apply_action_to_world_state(action, world)
        # Pas de mutation : meme objet retourne
        assert new_world is world

    def test_fast_forward_passes_actions_to_canon_scheduler(self) -> None:
        """Spec §6.5 : canon_scheduler_fn recoit `actions=` kwarg si supporte."""
        async def run() -> None:
            from shinobi.agents import (
                AgentMemoryStore,
                Reflector,
                TickEngine,
                initialize_roster,
            )

            received_actions = []

            def scheduler(state, year, tick, *, actions=()):
                received_actions.append(list(actions))
                return state, [], []

            store = AgentMemoryStore(None)
            roster = initialize_roster(store)
            engine = TickEngine(
                roster=roster, memory_store=store,
                selector=ActionSelector(),
                reflector=Reflector(),
            )
            await engine.fast_forward(
                from_year=12, months=1,
                canon_scheduler_fn=scheduler,
                canon_scheduler_state={},
            )
            # 4 ticks, chaque tick a passe 15 actions (top-15 majors)
            assert len(received_actions) == 4
            assert all(len(acts) >= 15 for acts in received_actions)

        asyncio.run(run())

    def test_fast_forward_world_time_advances_via_scheduler(self) -> None:
        """Spec §6.5 : 'le monde tourne sans le joueur ... events canon se
        declenchent'. canon_scheduler_fn doit etre appele a chaque tick
        et peut muter un etat externe (avancement du temps mondial)."""
        async def run() -> None:
            from shinobi.agents import (
                AgentMemoryStore,
                Reflector,
                TickEngine,
                initialize_roster,
            )

            world_state = {"ticks_advanced": 0}

            def scheduler(state, year, tick):
                world_state["ticks_advanced"] += 1
                if tick % 8 == 0:
                    class MockEv:
                        event_id = f"world_tick_{tick}"
                        involved_characters = ["uzumaki_naruto"]
                    return state, [MockEv()], []
                return state, [], []

            store = AgentMemoryStore(None)
            roster = initialize_roster(store)
            engine = TickEngine(
                roster=roster, memory_store=store,
                selector=ActionSelector(),
                reflector=Reflector(),
            )
            digest = await engine.fast_forward(
                from_year=12, months=2,
                canon_scheduler_fn=scheduler,
                canon_scheduler_state={},
            )
            # 2 mois * 4 ticks = 8 ticks -> scheduler appele 8 fois
            assert world_state["ticks_advanced"] == 8
            # Au moins 1 canon event dans le digest
            canon_entries = [
                e for e in digest.entries
                if e.related_event_id is not None
            ]
            assert len(canon_entries) >= 1

        asyncio.run(run())

    def test_tick_engine_uses_personality_store_for_agent_vector(self) -> None:
        """Spec §6.3 : 'Son vecteur de personnalite actuel'. Quand
        TickEngine recoit un PersonalityStore, _get_or_create_agent charge
        la personality du PNJ et le MajorAgent l'expose."""
        async def run() -> None:
            from shinobi.agents import (
                AgentMemoryStore,
                Reflector,
                TickEngine,
                initialize_roster,
            )
            from shinobi.personality import (
                NPCPersonality,
                PersonalityDimension,
                PersonalityStore,
            )

            store = AgentMemoryStore(None)
            roster = initialize_roster(store)
            with PersonalityStore(None) as p_store:
                # Inject une personality avec aggression elevee
                custom = NPCPersonality(
                    npc_id="uzumaki_naruto",
                    vector={dim: 0.9 if dim == PersonalityDimension.aggression
                            else 0.5 for dim in PersonalityDimension},
                    canon_baseline=dict.fromkeys(PersonalityDimension, 0.5),
                )
                p_store.upsert_personality(custom)

                engine = TickEngine(
                    roster=roster, memory_store=store,
                    selector=ActionSelector(),
                    reflector=Reflector(),
                    personality_store=p_store,
                )
                agent = engine._get_or_create_agent("uzumaki_naruto")
                assert agent.personality is not None
                assert agent.personality.value(PersonalityDimension.aggression) == 0.9

        asyncio.run(run())

    def test_tick_engine_no_personality_store_returns_none(self) -> None:
        """Sans personality_store, MajorAgent.personality reste None
        (graceful : action_selector skip personality dans le prompt)."""
        async def run() -> None:
            from shinobi.agents import (
                AgentMemoryStore,
                Reflector,
                TickEngine,
                initialize_roster,
            )

            store = AgentMemoryStore(None)
            roster = initialize_roster(store)
            engine = TickEngine(
                roster=roster, memory_store=store,
                selector=ActionSelector(),
                reflector=Reflector(),
                personality_store=None,
            )
            agent = engine._get_or_create_agent("uzumaki_naruto")
            assert agent.personality is None

        asyncio.run(run())

    def test_arc_relevant_npcs_returns_era_key_figures(self) -> None:
        """Spec §6.1 'top-15 + dynamique selon arc'. arc_relevant_npcs(year)
        retourne les key_figures de l'era contenant year."""
        from shinobi.agents import AgentMemoryStore, initialize_roster

        eras_data = [
            {
                "id": "warring_states",
                "year_start": -100, "year_end": -55,
                "key_figures": ["senju_hashirama", "uchiha_madara"],
            },
            {
                "id": "part_2",
                "year_start": 15, "year_end": 17,
                "key_figures": ["uzumaki_naruto", "uchiha_sasuke", "pain_nagato"],
            },
        ]
        with AgentMemoryStore(None) as store:
            roster = initialize_roster(store)
            assert "senju_hashirama" in roster.arc_relevant_npcs(-80, eras_data)
            assert "pain_nagato" in roster.arc_relevant_npcs(16, eras_data)
            # Year hors de toute ere
            assert roster.arc_relevant_npcs(99999, eras_data) == []
            # Pas de eras_data
            assert roster.arc_relevant_npcs(16, None) == []

    def test_promote_arc_relevant_promotes_to_secondary(self) -> None:
        """Apres promote_arc_relevant, les key_figures non-major sont en secondary."""
        from shinobi.agents import AgentMemoryStore, initialize_roster

        eras_data = [
            {
                "id": "test_era", "year_start": 10, "year_end": 20,
                "key_figures": ["new_npc_xyz"],  # Pas dans top-15 par defaut
            },
        ]
        with AgentMemoryStore(None) as store:
            roster = initialize_roster(store)
            # new_npc_xyz est background avant
            assert roster.tier_for("new_npc_xyz") == AgentTier.background
            promoted = roster.promote_arc_relevant(15, eras_data)
            assert "new_npc_xyz" in promoted
            assert roster.tier_for("new_npc_xyz") == AgentTier.secondary

    def test_promote_arc_relevant_skips_existing_majors(self) -> None:
        """Les NPCs deja major ne sont pas demotes/repromus."""
        from shinobi.agents import AgentMemoryStore, initialize_roster

        eras_data = [
            {
                "id": "konoha_modern", "year_start": 10, "year_end": 20,
                "key_figures": ["uzumaki_naruto"],  # deja major
            },
        ]
        with AgentMemoryStore(None) as store:
            roster = initialize_roster(store)
            assert roster.tier_for("uzumaki_naruto") == AgentTier.major
            promoted = roster.promote_arc_relevant(15, eras_data)
            assert "uzumaki_naruto" not in promoted
            assert roster.tier_for("uzumaki_naruto") == AgentTier.major

    def test_on_player_interaction_promotes_background(self) -> None:
        """Spec §6.4 : promote background -> secondary quand joueur interagit."""
        from shinobi.agents import AgentMemoryStore, initialize_roster

        with AgentMemoryStore(None) as store:
            roster = initialize_roster(store)
            assert roster.tier_for("ghost_npc") == AgentTier.background
            entry = roster.on_player_interaction(
                "ghost_npc", year=12, tick=5,
            )
            assert entry is not None
            assert roster.tier_for("ghost_npc") == AgentTier.secondary
            assert entry.last_active_year == 12
            assert entry.last_active_tick == 5

    def test_on_player_interaction_marks_active_for_existing(self) -> None:
        """Si le NPC est deja major/secondary, on marque just last_active."""
        from shinobi.agents import AgentMemoryStore, initialize_roster

        with AgentMemoryStore(None) as store:
            roster = initialize_roster(store)
            tier_before = roster.tier_for("uzumaki_naruto")
            roster.on_player_interaction("uzumaki_naruto", year=15, tick=42)
            assert roster.tier_for("uzumaki_naruto") == tier_before
            entry = roster.get("uzumaki_naruto")
            assert entry is not None
            assert entry.last_active_year == 15

    def test_on_event_impact_promotes_involved_npcs(self) -> None:
        """Spec §6.4 : NPCs impactes par event majeur -> secondary."""
        from shinobi.agents import AgentMemoryStore, initialize_roster

        with AgentMemoryStore(None) as store:
            roster = initialize_roster(store)
            promoted = roster.on_event_impact(
                ["bg_npc_a", "bg_npc_b", "uzumaki_naruto"],
                year=12, tick=5,
            )
            # Naruto est deja major, pas promu
            assert "uzumaki_naruto" not in promoted
            # bg_npc_a et bg_npc_b promus
            assert set(promoted) == {"bg_npc_a", "bg_npc_b"}
            assert roster.tier_for("bg_npc_a") == AgentTier.secondary

    def test_load_eras_data_reads_canon_file(self) -> None:
        """load_eras_data charge eras.json si present."""
        from shinobi.agents import load_eras_data

        path = Path("data/canonical/eras.json")
        if not path.exists():
            pytest.skip("eras.json absent")
        eras = load_eras_data(path)
        assert isinstance(eras, list)
        assert len(eras) > 0
        # Verifier schema attendu
        assert all("year_start" in e and "year_end" in e for e in eras if isinstance(e, dict))

    def test_load_eras_data_missing_returns_empty(self) -> None:
        from shinobi.agents import load_eras_data
        assert load_eras_data("/nonexistent/eras.json") == []

    def test_speculative_decoding_args_disabled_by_default(self) -> None:
        """Spec §13 + §11.4 : speculative decoding off par defaut.
        build_llama_server_args ne contient PAS --model-draft."""
        from shinobi.config import settings
        from shinobi.llm.server_bootstrap import build_llama_server_args

        # Sauvegarde + reset
        original = settings.llm_speculative_draft_model_path
        settings.llm_speculative_draft_model_path = ""
        try:
            args = build_llama_server_args(
                llama_path=Path("llama-server"),
                model_path=Path("model.gguf"),
                port=8080,
            )
            assert "--model-draft" not in args
            assert "--draft-max" not in args
        finally:
            settings.llm_speculative_draft_model_path = original

    def test_speculative_decoding_args_enabled_when_configured(
        self, tmp_path: Path,
    ) -> None:
        """Si draft model existe, args contiennent --model-draft + --draft-max + -ngld."""
        from shinobi.config import settings
        from shinobi.llm.server_bootstrap import build_llama_server_args

        # Cree un faux draft model file
        draft_file = tmp_path / "draft.gguf"
        draft_file.write_bytes(b"fake gguf")

        original = settings.llm_speculative_draft_model_path
        original_tokens = settings.llm_speculative_draft_tokens
        settings.llm_speculative_draft_model_path = str(draft_file)
        settings.llm_speculative_draft_tokens = 16
        try:
            args = build_llama_server_args(
                llama_path=Path("llama-server"),
                model_path=Path("model.gguf"),
                port=8080,
            )
            assert "--model-draft" in args
            assert str(draft_file) in args
            assert "--draft-max" in args
            assert "16" in args
            assert "-ngld" in args
        finally:
            settings.llm_speculative_draft_model_path = original
            settings.llm_speculative_draft_tokens = original_tokens

    def test_speculative_decoding_disabled_when_draft_missing(self) -> None:
        """Si draft path configure MAIS fichier absent : graceful skip."""
        from shinobi.config import settings
        from shinobi.llm.server_bootstrap import build_llama_server_args

        original = settings.llm_speculative_draft_model_path
        settings.llm_speculative_draft_model_path = "/nonexistent/draft.gguf"
        try:
            args = build_llama_server_args(
                llama_path=Path("llama-server"),
                model_path=Path("model.gguf"),
                port=8080,
            )
            assert "--model-draft" not in args  # graceful fallback
        finally:
            settings.llm_speculative_draft_model_path = original

    def test_try_load_bge_encoders_returns_tuple_or_none(self) -> None:
        """Helper safe-load BGE-M3 : retourne (encoder, query_encoder) ou
        (None, None) si modele indisponible. Ne crash JAMAIS."""
        from shinobi.agents import try_load_bge_encoders

        result = try_load_bge_encoders()
        assert isinstance(result, tuple)
        assert len(result) == 2
        encoder, query_encoder = result
        # Cas 1 : modele dispo -> les 2 sont callables
        # Cas 2 : modele absent -> les 2 sont None
        assert (encoder is None) == (query_encoder is None)
        if encoder is not None:
            # Test rapide : encode une string
            vec = query_encoder("test")
            assert isinstance(vec, list)
            assert len(vec) > 0

    def test_tick_engine_propagates_embeddings_index(self) -> None:
        """TickEngine -> MajorAgent -> AgentMemory : index BGE-M3 auto-attache."""
        async def run() -> None:
            from shinobi.agents import (
                ActionSelector,
                AgentMemoryStore,
                EmbeddingsIndex,
                Reflector,
                TickEngine,
                initialize_roster,
            )

            encoder = lambda texts: [[0.5] * 3 for _ in texts]
            idx = EmbeddingsIndex(
                None, encoder=encoder, query_encoder=lambda t: [0.5] * 3,
            )
            store = AgentMemoryStore(None)
            roster = initialize_roster(store)
            engine = TickEngine(
                roster=roster, memory_store=store,
                selector=ActionSelector(),
                reflector=Reflector(),
                embeddings_index=idx,
            )
            # Simule un agent perceoit une observation
            agent = engine._get_or_create_agent("uzumaki_naruto")
            obs = Observation(
                npc_id="uzumaki_naruto",
                text="combat important", year=12, importance=0.8,
            )
            agent.perceive([obs])
            # L'index doit avoir ete utilise via la chaine TickEngine->Agent->Memory
            assert idx.size("uzumaki_naruto") == 1

        asyncio.run(run())

    def test_memory_retrieve_uses_bge_when_provided(self) -> None:
        """AgentMemory.retrieve avec embeddings_index utilise les cosines."""
        from shinobi.agents import AgentMemory, EmbeddingsIndex

        def encoder(texts: list[str]) -> list[list[float]]:
            return [
                [1.0, 0.0, 0.0] if "massacre" in t.lower() else [0.0, 1.0, 0.0]
                for t in texts
            ]

        def query_encoder(t: str) -> list[float]:
            return encoder([t])[0]

        idx = EmbeddingsIndex(
            None, encoder=encoder, query_encoder=query_encoder,
        )
        m = AgentMemory(npc_id="sasuke")
        # 2 obs, 1 pertinent semantiquement
        o1 = Observation(npc_id="sasuke", text="massacre uchiha clan", year=8, importance=0.5)
        o2 = Observation(npc_id="sasuke", text="ate ramen for breakfast", year=12, importance=0.5)
        m.add_observation(o1)
        m.add_observation(o2)
        idx.index_entries("sasuke", [o1, o2])
        # query semantique 'massacre' doit propulser o1 en tete
        top = m.retrieve(
            "massacre", top_k=2, embeddings_index=idx,
        )
        assert top[0][1].id == o1.id


# ============================================================================
# 13. Actions agents -> mutation KG (gap closure spec §6.3)
# ============================================================================


class TestAgentActionsKGBridge:
    """Spec docs/02 §6.3 : 'Ces actions modifient le KG, qui a son tour
    change ce que les autres PNJ peuvent observer.'"""

    def test_action_to_fact_speak(self) -> None:
        from shinobi.agents import action_to_fact

        a = AgentAction(
            npc_id="naruto", type=AgentActionType.speak, year=12,
            target_npc_id="sasuke", content="bonjour",
        )
        fact = action_to_fact(a)
        assert fact is not None
        assert fact.subject == "naruto"
        assert fact.relation == "said_to"
        assert fact.object == "sasuke"

    def test_action_to_fact_idle_returns_none(self) -> None:
        from shinobi.agents import action_to_fact
        a = AgentAction(
            npc_id="x", type=AgentActionType.idle, year=12,
        )
        assert action_to_fact(a) is None

    def test_secret_plot_known_only_by_actor(self) -> None:
        """Une action 'plot' ne doit etre connue que de l'acteur."""
        from shinobi.agents import action_to_fact

        a = AgentAction(
            npc_id="orochimaru", type=AgentActionType.plot, year=12,
            target_npc_id="hiruzen", content="prepare invasion",
        )
        fact = action_to_fact(a)
        assert fact is not None
        assert fact.known_by_npc_ids == ["orochimaru"]

    def test_witness_observation_target_higher_importance(self) -> None:
        """Le target d'une action recoit une obs avec importance pleine."""
        from shinobi.agents import witness_observation

        a = AgentAction(
            npc_id="naruto", type=AgentActionType.attack, year=12,
            target_npc_id="sasuke", content="rasengan",
            importance=0.7,
        )
        obs = witness_observation(a, witness_npc_id="sasuke")
        assert obs.importance >= 0.7
        assert obs.npc_id == "sasuke"

    def test_witness_observation_bystander_dampened(self) -> None:
        """Un temoin secondaire recoit une obs avec importance amoindrie."""
        from shinobi.agents import witness_observation

        a = AgentAction(
            npc_id="naruto", type=AgentActionType.attack, year=12,
            target_npc_id="sasuke", content="rasengan",
            importance=1.0,
        )
        obs = witness_observation(a, witness_npc_id="sakura")
        assert obs.importance < 1.0
        assert obs.npc_id == "sakura"

    def test_collect_witness_observations(self) -> None:
        """Plusieurs actions -> dict des observations par temoin."""
        from shinobi.agents import collect_witness_observations

        actions = [
            AgentAction(
                npc_id="naruto", type=AgentActionType.speak, year=12,
                target_npc_id="sasuke", content="hello",
                location_id="konoha",
            ),
            AgentAction(
                npc_id="kakashi", type=AgentActionType.travel, year=12,
                location_id="konoha",
            ),
        ]
        scene_npcs = {"konoha": {"naruto", "sasuke", "sakura", "kakashi"}}
        obs_map = collect_witness_observations(
            actions, npcs_in_scene_per_location=scene_npcs,
        )
        # sasuke est target de speak -> obs
        # sakura est temoin de speak (bystander) + travel -> 2 obs
        # kakashi : ne se temoigne pas lui-meme + temoin de speak -> 1 obs
        assert "sasuke" in obs_map
        assert len(obs_map.get("sakura", [])) >= 1

    def test_secret_actions_skipped_in_witness_collection(self) -> None:
        from shinobi.agents import collect_witness_observations

        secret = AgentAction(
            npc_id="orochimaru", type=AgentActionType.plot, year=12,
            target_npc_id="hiruzen", content="invasion",
            location_id="oto",
        )
        scene = {"oto": {"orochimaru", "kabuto"}}
        obs_map = collect_witness_observations(
            [secret], npcs_in_scene_per_location=scene,
        )
        # plot est SECRET : aucun temoin ne doit etre genere
        assert obs_map == {}

    def test_push_action_to_kg_inserts_fact(self) -> None:
        from shinobi.agents import push_action_to_kg
        from shinobi.kg.store import KnowledgeGraphStore

        with KnowledgeGraphStore(None) as kg:
            a = AgentAction(
                npc_id="naruto", type=AgentActionType.travel, year=12,
                location_id="suna", content="visite Suna",
            )
            fid = push_action_to_kg(a, kg_store=kg)
            assert fid is not None
            facts = kg.get_facts(subject="naruto", relation="traveled_to")
            assert len(facts) == 1
            assert facts[0].object == "suna"


# ============================================================================
# 14. Batch inferences (gap closure spec §6.4 + §11)
# ============================================================================


class TestBatchInferences:
    """Spec docs/02 §6.4 + §11.1 :
    'Batch d'agents en un seul prompt (5 PNJ -> 1 inference Qwen3-4B
     multi-output)'."""

    def test_batch_selector_one_inference_for_n_agents(self) -> None:
        async def run() -> None:
            from shinobi.agents import BatchActionSelector

            llm_call_count = 0

            async def mock_llm(sys_p, user_p, schema, model, temp):
                nonlocal llm_call_count
                llm_call_count += 1
                # 5 actions
                return {
                    "actions": [
                        {"type": "speak", "content": f"hi from {i}", "importance": 0.5}
                        for i in range(5)
                    ],
                }

            batch = BatchActionSelector(llm_call=mock_llm, batch_size=5)
            items = [
                (
                    AgentMemory(npc_id=f"npc_{i}"),
                    SelectionContext(npc_id=f"npc_{i}", year=12),
                )
                for i in range(5)
            ]
            actions = await batch.select_batch(items)
            assert len(actions) == 5
            assert llm_call_count == 1  # UNE seule inference pour 5 agents
            for i, a in enumerate(actions):
                assert a.npc_id == f"npc_{i}"

        asyncio.run(run())

    def test_batch_selector_splits_by_batch_size(self) -> None:
        """10 agents avec batch_size=5 -> 2 inferences."""
        async def run() -> None:
            from shinobi.agents import BatchActionSelector

            llm_call_count = 0

            async def mock_llm(sys_p, user_p, schema, model, temp):
                nonlocal llm_call_count
                llm_call_count += 1
                return {
                    "actions": [
                        {"type": "idle", "content": "x", "importance": 0.2}
                        for _ in range(5)
                    ],
                }

            batch = BatchActionSelector(llm_call=mock_llm, batch_size=5)
            items = [
                (
                    AgentMemory(npc_id=f"npc_{i}"),
                    SelectionContext(npc_id=f"npc_{i}", year=12),
                )
                for i in range(10)
            ]
            actions = await batch.select_batch(items)
            assert len(actions) == 10
            assert llm_call_count == 2  # 10/5 = 2 batches

        asyncio.run(run())

    def test_batch_selector_fallback_when_llm_fails(self) -> None:
        """Si LLM retourne pas le bon nombre d'actions, fallback per-agent."""
        async def run() -> None:
            from shinobi.agents import BatchActionSelector

            async def bad_llm(*args, **kwargs):
                # Renvoie 2 actions au lieu de 5 -> invalide
                return {"actions": [
                    {"type": "speak", "content": "x", "importance": 0.5},
                    {"type": "speak", "content": "y", "importance": 0.5},
                ]}

            batch = BatchActionSelector(llm_call=bad_llm, batch_size=5)
            items = [
                (
                    AgentMemory(npc_id=f"npc_{i}"),
                    SelectionContext(npc_id=f"npc_{i}", year=12),
                )
                for i in range(5)
            ]
            actions = await batch.select_batch(items)
            # Fallback deterministe : tous meditate (pas de plans, pas de presents)
            assert len(actions) == 5
            for a in actions:
                assert a.type == AgentActionType.meditate

        asyncio.run(run())

    def test_batch_no_llm_uses_fallback(self) -> None:
        async def run() -> None:
            from shinobi.agents import BatchActionSelector

            batch = BatchActionSelector(llm_call=None)
            items = [
                (
                    AgentMemory(npc_id="x"),
                    SelectionContext(npc_id="x", year=12),
                ),
            ]
            actions = await batch.select_batch(items)
            assert len(actions) == 1

        asyncio.run(run())

    def test_context_auto_builder_world_summary(self) -> None:
        """Spec §6.3 : 'L'etat du monde local (KG filtre sur ce qu'il sait)'."""
        from shinobi.agents import build_world_summary_for_npc
        from shinobi.kg.schema import Fact, ObjectType
        from shinobi.kg.store import KnowledgeGraphStore

        with KnowledgeGraphStore(None) as kg:
            kg.add_fact(Fact(
                subject="naruto", relation="traveled_to", object="suna",
                object_type=ObjectType.entity, valid_from_year=12,
                known_by_npc_ids=["naruto", "sakura"],
            ))
            # Fact connu de sakura
            summary_sakura = build_world_summary_for_npc(
                kg_store=kg, npc_id="sakura", year=12,
            )
            assert "naruto" in summary_sakura
            # Fact pas connu de sasuke
            summary_sasuke = build_world_summary_for_npc(
                kg_store=kg, npc_id="sasuke", year=12,
            )
            assert summary_sasuke == ""

    def test_context_auto_builder_relations_summary(self) -> None:
        """Spec §6.3 : 'Sa relation avec les autres PNJ presents'."""
        from shinobi.agents import build_relations_summary_for_npc
        from shinobi.kg.schema import SocialLink
        from shinobi.kg.social import SocialNetwork
        from shinobi.kg.store import KnowledgeGraphStore

        with KnowledgeGraphStore(None) as kg:
            net = SocialNetwork(kg.conn)
            net.add_link(SocialLink(
                npc_a="naruto", npc_b="sasuke",
                link_type="rival", strength=0.8,
            ))
            net.add_link(SocialLink(
                npc_a="naruto", npc_b="iruka",
                link_type="mentor", strength=0.9,
            ))
            summary = build_relations_summary_for_npc(
                social_network=net, npc_id="naruto",
                present_npc_ids=["sasuke"],
            )
            # Sasuke present -> tag [present]
            assert "sasuke" in summary
            assert "[present]" in summary

    def test_auto_fill_selection_context(self) -> None:
        """L'auto-fill remplit world+relations vides depuis KG/SocialNetwork."""
        from shinobi.agents import auto_fill_selection_context
        from shinobi.kg.schema import Fact, ObjectType
        from shinobi.kg.store import KnowledgeGraphStore

        with KnowledgeGraphStore(None) as kg:
            kg.add_fact(Fact(
                subject="x", relation="said_to", object="y",
                object_type=ObjectType.entity, valid_from_year=12,
                known_by_npc_ids=["sasuke"],
            ))
            ctx = SelectionContext(npc_id="sasuke", year=12)
            new_ctx = auto_fill_selection_context(ctx, kg_store=kg)
            assert new_ctx.world_summary  # non-vide

    def test_sample_majors_k_strategy(self) -> None:
        """Spec §11.1 strategie 1 : 'Sampling top-K agents (5 sur 15) actifs
        ce tick'. TickEngine.sample_majors_k=5 -> 5 majors simules au lieu de 15."""
        async def run() -> None:
            from shinobi.agents import (
                ActionSelector,
                AgentMemoryStore,
                Reflector,
                TickEngine,
                initialize_roster,
            )

            store = AgentMemoryStore(None)
            roster = initialize_roster(store)
            engine = TickEngine(
                roster=roster, memory_store=store,
                selector=ActionSelector(),
                reflector=Reflector(),
                sample_majors_k=5,
                sampling_seed=42,
            )
            results = await engine.tick(year=12, tick=1)
            # 5 majors sampled, 0 secondary (tick 1 % 10 != 0)
            assert len(results) == 5
            # Determinisme : meme seed + meme tick -> meme sample
            results2 = await engine.tick(year=12, tick=1)
            assert {r.action.npc_id for r in results} == {
                r.action.npc_id for r in results2
            }

        asyncio.run(run())

    def test_sample_majors_k_none_simulates_all(self) -> None:
        """Sans sample_majors_k, on simule TOUS les majors (default behavior)."""
        async def run() -> None:
            from shinobi.agents import (
                ActionSelector,
                AgentMemoryStore,
                Reflector,
                TickEngine,
                initialize_roster,
            )

            store = AgentMemoryStore(None)
            roster = initialize_roster(store)
            engine = TickEngine(
                roster=roster, memory_store=store,
                selector=ActionSelector(),
                reflector=Reflector(),
                sample_majors_k=None,  # default
            )
            results = await engine.tick(year=12, tick=1)
            assert len(results) == 15  # tous les majors

        asyncio.run(run())

    def test_trivial_state_shortcut_skips_llm(self) -> None:
        """Spec §11.1 strategie 4 : 'Decision deterministe simplifiee si le
        PNJ est dans un etat trivial'. Le LLM n'est PAS appele."""
        async def run() -> None:
            from shinobi.agents import is_trivial_state

            llm_calls = 0

            async def mock_llm(*args, **kwargs):
                nonlocal llm_calls
                llm_calls += 1
                return {"type": "speak", "content": "x", "importance": 0.5}

            selector = ActionSelector(
                llm_call=mock_llm, trivial_state_shortcut=True,
            )
            mem = AgentMemory(npc_id="x")
            # Etat trivial : pas de presents, plan 'mediter'
            ctx = SelectionContext(
                npc_id="x", year=12,
                active_plans_text=("mediter en silence",),
            )
            assert is_trivial_state(ctx) is True
            await selector.select(mem, ctx)
            assert llm_calls == 0  # SHORTCUT applique
            assert selector.trivial_shortcuts == 1

        asyncio.run(run())

    def test_trivial_state_shortcut_disabled_calls_llm(self) -> None:
        """Si trivial_state_shortcut=False, le LLM est appele meme en etat trivial."""
        async def run() -> None:
            llm_calls = 0

            async def mock_llm(*args, **kwargs):
                nonlocal llm_calls
                llm_calls += 1
                return {"type": "speak", "content": "x", "importance": 0.5}

            selector = ActionSelector(
                llm_call=mock_llm, trivial_state_shortcut=False,
            )
            mem = AgentMemory(npc_id="x")
            ctx = SelectionContext(
                npc_id="x", year=12,
                active_plans_text=("mediter",),
            )
            await selector.select(mem, ctx)
            assert llm_calls == 1  # LLM appele
            assert selector.trivial_shortcuts == 0

        asyncio.run(run())

    def test_trivial_state_detection_logic(self) -> None:
        """is_trivial_state retourne True/False selon les heuristiques spec."""
        from shinobi.agents import is_trivial_state

        # Cas trivial : pas de presents, pas de memoires saillantes, plan trivial
        trivial = SelectionContext(
            npc_id="x", year=12, active_plans_text=("entrainement routine",),
        )
        assert is_trivial_state(trivial) is True

        # Cas non-trivial : presents
        busy = SelectionContext(
            npc_id="x", year=12,
            present_npc_ids=("y",),
        )
        assert is_trivial_state(busy) is False

        # Cas non-trivial : plan ambitieux
        ambitious = SelectionContext(
            npc_id="x", year=12,
            active_plans_text=("comploter contre le hokage",),
        )
        assert is_trivial_state(ambitious) is False

        # Cas non-trivial : memoire saillante
        from shinobi.agents.types import Observation
        salient = SelectionContext(
            npc_id="x", year=12,
            top_memories=(Observation(
                npc_id="x", text="trauma majeur", year=8, importance=0.9,
            ),),
        )
        assert is_trivial_state(salient) is False

    def test_fast_forward_with_canon_scheduler_wiring(self) -> None:
        """Spec §6.5 : 'events canon se declenchent ou s'annulent selon les
        actions agents'. Le fast_forward tick canon scheduler a chaque tick."""
        async def run() -> None:
            from shinobi.agents import (
                ActionSelector,
                AgentMemoryStore,
                LLMCache,
                Reflector,
                TickEngine,
                initialize_roster,
            )

            # Mock canon scheduler : retourne 1 fired tous les 5 ticks
            scheduler_calls = []

            def mock_scheduler(state, year, tick):
                scheduler_calls.append((year, tick))
                fired = []
                cancelled = []
                if tick % 5 == 0:
                    # Mock CompletedEvent simple
                    class MockEv:
                        def __init__(self, eid):
                            self.event_id = eid
                    fired.append(MockEv(f"event_year_{year}_tick_{tick}"))
                return state, fired, cancelled

            store = AgentMemoryStore(None)
            roster = initialize_roster(store)
            cache = LLMCache(None)
            engine = TickEngine(
                roster=roster, memory_store=store,
                selector=ActionSelector(cache=cache),
                reflector=Reflector(cache=cache),
                cache=cache,
            )
            digest = await engine.fast_forward(
                from_year=12, months=1,
                canon_scheduler_fn=mock_scheduler,
                canon_scheduler_state={},
            )
            # 4 ticks (1 mois * 4) -> scheduler appele 4 fois
            assert len(scheduler_calls) == 4
            # Au moins 1 canon event dans le digest (tick 0 % 5 == 0)
            canon_entries = [
                e for e in digest.entries
                if e.related_event_id is not None
            ]
            assert len(canon_entries) >= 1

        asyncio.run(run())

    def test_batch_cache_hit(self) -> None:
        """2eme appel avec memes contextes -> cache hit, 1 seule inference."""
        async def run() -> None:
            from shinobi.agents import BatchActionSelector

            llm_count = 0

            async def mock_llm(*args, **kwargs):
                nonlocal llm_count
                llm_count += 1
                return {
                    "actions": [
                        {"type": "idle", "content": "x", "importance": 0.2}
                        for _ in range(3)
                    ],
                }

            with LLMCache(None) as cache:
                batch = BatchActionSelector(
                    llm_call=mock_llm, cache=cache, batch_size=3,
                )
                items = [
                    (
                        AgentMemory(npc_id=f"npc_{i}"),
                        SelectionContext(npc_id=f"npc_{i}", year=12),
                    )
                    for i in range(3)
                ]
                await batch.select_batch(items)
                await batch.select_batch(items)
                assert llm_count == 1  # 2eme = cache hit

        asyncio.run(run())
