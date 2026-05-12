"""Routes /play/{id}/<inspect> Phase 9.

Inspecteurs Phase A-H : personality (D), beliefs (B), tensions (C),
agents (E) roster + detail. Lecture seule, robust quand la sous-base
n'a pas ete initialisee (retourne available=False / liste vide).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status

from shinobi.api.schemas import (
    AgentDetailResponse,
    AgentSummary,
    AgentsRosterResponse,
    BeliefFact,
    BeliefsResponse,
    PersonalityResponse,
    TensionEntry,
    TensionsResponse,
)
from shinobi.i18n import t
from shinobi.persistence import saves as save_module


router = APIRouter(prefix="/play/{save_id}", tags=["status"])


def _ensure_save(save_id: str) -> None:
    if not save_module._meta_path(save_id).exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=t("api.saves.not_found", save_id=save_id),
        )


@router.get(
    "/personality/{npc_id}",
    response_model=PersonalityResponse,
    summary="NPC personality vector + drift (Phase D)",
)
def personality(save_id: str, npc_id: str) -> PersonalityResponse:
    """Snapshot of an NPC's personality vector."""
    _ensure_save(save_id)
    db_path = save_module.personality_db_path(save_id)
    if not db_path.exists():
        return PersonalityResponse(npc_id=npc_id, available=False)
    try:
        from shinobi.personality import PersonalityStore

        with PersonalityStore(db_path) as store:
            p = store.get_personality(npc_id)
    except Exception:
        return PersonalityResponse(npc_id=npc_id, available=False)
    if p is None:
        return PersonalityResponse(npc_id=npc_id, available=False)
    baseline = {
        dim.value: p.baseline(dim)
        for dim in p.baseline_vector.keys()
    }
    drift = {
        dim.value: p.value(dim) - p.baseline(dim)
        for dim in p.baseline_vector.keys()
    }
    return PersonalityResponse(
        npc_id=npc_id, available=True, baseline=baseline, drift=drift,
    )


@router.get(
    "/beliefs/{npc_id}",
    response_model=BeliefsResponse,
    summary="NPC sub-KG (Phase B §5.4)",
)
def beliefs(save_id: str, npc_id: str) -> BeliefsResponse:
    """List the facts an NPC knows from the dynamic KG."""
    _ensure_save(save_id)
    _, world, _ = save_module.load_save(save_id)
    kg_db = save_module.kg_db_path(save_id)
    if not kg_db.exists():
        return BeliefsResponse(npc_id=npc_id, available=False, facts=[], count=0)
    try:
        from shinobi.kg.store import KnowledgeGraphStore

        with KnowledgeGraphStore(kg_db) as kg:
            known_facts = kg.known_to(npc_id, year=world.current_year)
    except Exception:
        return BeliefsResponse(npc_id=npc_id, available=False, facts=[], count=0)
    facts: list[BeliefFact] = []
    for f in known_facts[:200]:
        facts.append(
            BeliefFact(
                fact_id=getattr(f, "id", "") or "",
                subject=f.subject,
                predicate=f.relation,
                object=f.object or "",
                fidelity=float(getattr(f, "confidence", 1.0)),
            )
        )
    return BeliefsResponse(
        npc_id=npc_id, available=True, facts=facts, count=len(facts),
    )


@router.get(
    "/tensions",
    response_model=TensionsResponse,
    summary="Detected narrative tensions (Phase C, 21 invariants)",
)
def tensions(save_id: str) -> TensionsResponse:
    """Run the TensionDetector on the current KG.

    Passes the canon to enable the 21st Phase H 9.3 rule
    (political_alliance_brittle_via_dead_leader).
    """
    _ensure_save(save_id)
    _, world, _ = save_module.load_save(save_id)
    kg_db = save_module.kg_db_path(save_id)
    if not kg_db.exists():
        return TensionsResponse(save_id=save_id, tensions=[], count=0)
    try:
        from shinobi.api.dependencies import get_canon
        from shinobi.kg.store import KnowledgeGraphStore
        from shinobi.tension import TensionDetector

        try:
            canon_obj = get_canon()
        except Exception:
            canon_obj = None
        with KnowledgeGraphStore(kg_db) as kg:
            detector = TensionDetector(kg, canon=canon_obj)
            result = detector.detect(world.current_year)
    except Exception:
        return TensionsResponse(save_id=save_id, tensions=[], count=0)
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    sorted_tensions = sorted(
        result.tensions.tensions,
        key=lambda t: (
            severity_order.get(getattr(t.severity, "value", "low"), 99),
            -getattr(t, "score", 0),
        ),
    )
    out: list[TensionEntry] = []
    for t in sorted_tensions[:50]:
        out.append(
            TensionEntry(
                invariant_id=getattr(t.type, "value", str(t.type)),
                severity=getattr(t.severity, "value", "low"),
                summary_fr=t.description,
            )
        )
    return TensionsResponse(save_id=save_id, tensions=out, count=len(out))


@router.post(
    "/tensions-llm",
    response_model=TensionsResponse,
    summary="Force-run the Qwen3-4B analyst LLM (Phase C §5.3)",
)
async def tensions_llm(save_id: str) -> TensionsResponse:
    """Force-run the LLMTensionAnalyst on the KG snapshot (force_analyst=True).

    If LLM unavailable or KG missing, returns empty list (graceful
    degradation). Persists SchedulerState after tick.
    """
    _ensure_save(save_id)
    _, world, _meta = save_module.load_save(save_id)
    kg_db = save_module.kg_db_path(save_id)
    if not kg_db.exists():
        return TensionsResponse(save_id=save_id, tensions=[], count=0)
    try:
        import json as _json

        from shinobi.kg.social import SocialNetwork
        from shinobi.kg.store import KnowledgeGraphStore
        from shinobi.llm.client import LLMClient
        from shinobi.tension import (
            LLMTensionAnalyst,
            SchedulerState,
            TensionScheduler,
        )

        with KnowledgeGraphStore(kg_db) as kg:
            social = SocialNetwork(kg.conn)
            async with LLMClient() as llm:
                if not await llm.health():
                    return TensionsResponse(
                        save_id=save_id, tensions=[], count=0,
                    )
                analyst = LLMTensionAnalyst(
                    kg, llm_client=llm, social_network=social,
                )
                state_path = save_module.tension_scheduler_state_path(save_id)
                initial = SchedulerState()
                if state_path.exists():
                    try:
                        initial = SchedulerState.from_dict(
                            _json.loads(state_path.read_text(encoding="utf-8")),
                        )
                    except (OSError, ValueError):
                        initial = SchedulerState()
                from shinobi.api.dependencies import get_canon as _gc

                try:
                    _canon = _gc()
                except Exception:
                    _canon = None
                scheduler = TensionScheduler(
                    kg, analyst=analyst, social_network=social,
                    state=initial, canon=_canon,
                )
                result = await scheduler.tick(
                    world.current_year, month=1, force_analyst=True,
                )
                try:
                    state_path.parent.mkdir(parents=True, exist_ok=True)
                    state_path.write_text(
                        _json.dumps(
                            scheduler.state.to_dict(),
                            ensure_ascii=False, indent=2,
                        ),
                        encoding="utf-8",
                    )
                except OSError:
                    pass
    except Exception:
        return TensionsResponse(save_id=save_id, tensions=[], count=0)
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    sorted_t = sorted(
        result.tensions.tensions,
        key=lambda t: (
            severity_order.get(getattr(t.severity, "value", "low"), 99),
            -getattr(t, "score", 0),
        ),
    )
    out: list[TensionEntry] = []
    for t in sorted_t[:50]:
        out.append(
            TensionEntry(
                invariant_id=getattr(t.type, "value", str(t.type)),
                severity=getattr(t.severity, "value", "low"),
                summary_fr=t.description,
            )
        )
    return TensionsResponse(save_id=save_id, tensions=out, count=len(out))


@router.get(
    "/agents",
    response_model=AgentsRosterResponse,
    summary="Phase E agent roster (top-15 + secondary)",
)
def agents_roster(save_id: str) -> AgentsRosterResponse:
    """List all agents persisted in the Phase E roster."""
    _ensure_save(save_id)
    db_path = save_module.agents_db_path(save_id)
    if not db_path.exists():
        return AgentsRosterResponse(save_id=save_id, agents=[], count=0)
    try:
        from shinobi.agents import AgentMemoryStore, AgentTier

        agents: list[AgentSummary] = []
        with AgentMemoryStore(db_path) as store:
            for tier in (AgentTier.major, AgentTier.secondary):
                for entry in store.list_roster(tier=tier):
                    agents.append(
                        AgentSummary(
                            npc_id=entry.npc_id,
                            tier=tier.value,
                            included_since_year=getattr(
                                entry, "included_since_year", None,
                            ),
                            last_simulated_turn=getattr(
                                entry, "last_active_tick", None,
                            ),
                        )
                    )
    except Exception:
        return AgentsRosterResponse(save_id=save_id, agents=[], count=0)
    return AgentsRosterResponse(
        save_id=save_id, agents=agents, count=len(agents),
    )


@router.get(
    "/agents/{npc_id}",
    response_model=AgentDetailResponse,
    summary="Phase E agent detail (3-tier memory + actions)",
)
def agent_detail(save_id: str, npc_id: str) -> AgentDetailResponse:
    """3-tier memory + last actions of an agent."""
    _ensure_save(save_id)
    db_path = save_module.agents_db_path(save_id)
    if not db_path.exists():
        return AgentDetailResponse(npc_id=npc_id, available=False)
    try:
        from shinobi.agents import AgentMemoryStore

        with AgentMemoryStore(db_path) as store:
            entry = store.get_roster_entry(npc_id)
            memory = store.load_memory(npc_id)
            actions = store.list_actions(npc_id, limit=10)
    except Exception:
        return AgentDetailResponse(npc_id=npc_id, available=False)
    if entry is None and getattr(memory, "size", 0) == 0:
        return AgentDetailResponse(npc_id=npc_id, available=False)
    tier_value = entry.tier.value if entry else "background"
    recent_actions = [
        {
            "year": getattr(a, "year", None),
            "type": getattr(getattr(a, "type", None), "value", None),
            "content": (a.content[:120] if getattr(a, "content", None) else None),
        }
        for a in (actions or [])[-10:]
    ]
    snippets: list[dict[str, Any]] = []
    for o in list(getattr(memory, "observations", []) or [])[-5:]:
        snippets.append(
            {"kind": "observation", "year": o.year, "text": o.text[:160]},
        )
    for r in list(getattr(memory, "reflections", []) or [])[-5:]:
        snippets.append(
            {
                "kind": "reflection",
                "year": r.year,
                "text": r.gist or r.text[:160],
            },
        )
    return AgentDetailResponse(
        npc_id=npc_id,
        available=True,
        tier=tier_value,
        recent_actions=recent_actions,
        memory_snippets=snippets,
    )
