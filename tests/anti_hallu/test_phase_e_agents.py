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
                selector = ActionSelector(llm_call=mock_llm, cache=cache)
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
