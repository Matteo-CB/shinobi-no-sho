"""Routes /play Phase 9.

Execution mecanique d'un tour : interpret -> resolve -> apply -> save.
La narration LLM n'est pas appelee depuis l'API : elle reste un concern de
la CLI / d'un futur frontend qui orchestrera plusieurs requetes API.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from shinobi.api.dependencies import get_canon
from shinobi.api.schemas import (
    FastForwardRequest,
    FastForwardResponse,
    InitializeResponse,
    SkipTimeRequest,
    SkipTimeResponse,
    StatusResponse,
    TurnRequest,
    TurnResponse,
)
from shinobi.engine.actions import (
    Action,
    ResolutionInputs,
    apply_action_to_state,
    resolve_action,
)
from shinobi.engine.events import tick_scheduler
from shinobi.engine.interpreter import interpret
from shinobi.engine.progression import advance_age
from shinobi.engine.rng import next_seed
from shinobi.engine.time import advance_time
from shinobi.errors import SaveCorruptError, SaveNotFoundError
from shinobi.i18n import t
from shinobi.persistence import saves as save_module
from shinobi.utils.time_utils import GameDate

router = APIRouter(prefix="/play", tags=["play"])


_PLAYER_ACTION_RELATIONS: dict[str, str] = {
    "fight": "fought",
    "challenge": "challenged",
    "spy": "spied_on",
    "steal": "stole_from",
    "seduce": "courted",
    "bribe": "bribed",
    "intimidate": "intimidated",
    "talk": "spoke_with",
    "use_technique": "used_technique_on",
}

_NOTABLE_PLAYER_ACTIONS = {
    "fight", "challenge", "spy", "steal", "seduce", "bribe", "intimidate",
}


def _push_action_to_kg(kg_store: Any, character_name: str, action: Action, result: Any, year: int) -> int | None:
    """Convert a player Action into a KG Fact (parity with CLI _push_player_action_to_kg)."""
    from shinobi.kg.schema import Canonicity, Fact, ObjectType

    atype = action.action_type.value
    base_relation = _PLAYER_ACTION_RELATIONS.get(atype)
    if base_relation is None:
        return None

    relation = base_relation
    success_factor = 1.0
    outcome = getattr(result, "outcome", None)
    outcome_value = getattr(outcome, "value", str(outcome) if outcome else "")
    if atype == "fight":
        if outcome_value == "full_success":
            relation = "defeated"
        elif outcome_value == "partial_success":
            relation = "wounded"
        elif outcome_value in ("minor_failure", "catastrophic_failure"):
            relation = "lost_against"
            success_factor = 0.5
    elif atype == "challenge":
        if outcome_value == "full_success":
            relation = "challenged_and_defeated"
        elif outcome_value in ("minor_failure", "catastrophic_failure"):
            relation = "challenged_and_lost"
            success_factor = 0.5
    elif atype == "spy":
        relation = (
            "spied_on_successfully"
            if outcome_value in ("full_success", "partial_success")
            else "spy_attempt_failed"
        )
        if "fail" in relation:
            success_factor = 0.5
    elif atype == "steal":
        relation = (
            "stole_from"
            if outcome_value in ("full_success", "partial_success")
            else "theft_attempt_failed"
        )
        if "fail" in relation:
            success_factor = 0.5

    target = action.target_id or action.parameters.get("target_id") or ""
    if not target:
        target = action.summary[:100] if action.summary else atype
        obj_type = ObjectType.value
    else:
        obj_type = ObjectType.entity

    base_importance = 0.8 if atype in _NOTABLE_PLAYER_ACTIONS else 0.5
    importance = base_importance * success_factor

    fact = Fact(
        subject=character_name,
        relation=relation,
        object=str(target),
        object_type=obj_type,
        valid_from_year=year,
        valid_to_year=year,
        source=f"player_action:{atype}",
        canonicity=Canonicity.divergent,
        confidence=importance,
        known_by_npc_ids=[character_name],
    )
    return kg_store.add_fact(fact)


@router.get(
    "/{save_id}/status",
    response_model=StatusResponse,
    summary="Current character and world state",
)
def status_endpoint(save_id: str) -> StatusResponse:
    """Read-only: return the current character + world snapshot."""
    try:
        character, world, meta = save_module.load_save(save_id)
    except SaveNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except SaveCorruptError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
    return StatusResponse(
        save_id=save_id,
        character_name=character.name,
        character_id=character.id,
        age_years=character.age_years,
        rank=character.rank,
        village=character.current_village,
        current_location=character.current_location,
        hp_current=character.health.hp_current,
        hp_max=character.health.hp_max,
        chakra_current=character.chakra.current,
        chakra_max=character.chakra.max,
        fatigue=character.health.fatigue,
        money_ryos=getattr(character, "money", 0),
        current_year=world.current_year,
        current_date=world.current_date,
        current_hour=world.current_hour,
        total_turns=meta.total_turns,
        techniques_known=[t.technique_id for t in character.techniques_known],
        natures=list(character.natures),
        kekkei_genkai=list(character.kekkei_genkai),
    )


@router.post(
    "/{save_id}/turn",
    response_model=TurnResponse,
    summary="Execute a turn",
)
def play_turn(
    save_id: str,
    payload: TurnRequest,
    canon: Any = Depends(get_canon),
) -> TurnResponse:
    """Resolve a mechanical turn: interpret intent -> resolve -> apply.

    No LLM narration, no network call: everything is deterministic from the
    WorldState seed and the intent_text. The save is updated in place
    (incremental snapshot).
    """
    try:
        character, world, meta = save_module.load_save(save_id)
    except SaveNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    parsed = interpret(payload.intent_text)
    parameters = dict(parsed.parameters)
    if payload.duration_hours is not None:
        parameters["duration_hours"] = payload.duration_hours

    action = Action(
        action_type=parsed.action_type,
        summary=parsed.summary,
        parameters=parameters,
        declared_text=payload.intent_text,
    )

    seed_state = world.seed
    char_before = character
    result = resolve_action(
        ResolutionInputs(
            character=character, world=world, action=action, seed=seed_state,
        )
    )
    new_char, new_world, enriched_result = apply_action_to_state(
        character, world, result,
    )

    # Desertion : si l'interpreter a flag _desert=True, applique non-interactif
    if action.parameters.get("_desert") is True and not new_char.is_missing_nin:
        from shinobi.engine.relations import add_reputation as _add_rep

        village = new_char.current_village
        new_char = new_char.model_copy(
            update={
                "is_missing_nin": True,
                "rank": "missing_nin",
                "current_location": "wilderness",
                "current_village": "wilderness",
            }
        )
        new_char = _add_rep(new_char, village, -100)
        new_rep = new_char.reputation.model_copy(
            update={"bingo_book_entry": True},
        )
        new_char = new_char.model_copy(update={"reputation": new_rep})

    # Avance le temps
    month_str, day_str = new_world.current_date.split("-")
    current = GameDate(
        year=new_world.current_year,
        month=int(month_str),
        day=int(day_str),
        hour=new_world.current_hour,
        minute=new_world.current_minute,
    )
    new_date = advance_time(current, enriched_result.duration_minutes or 1)
    new_world = new_world.with_time(
        year=new_date.year,
        date=f"{new_date.month:02d}-{new_date.day:02d}",
        hour=new_date.hour,
        minute=new_date.minute,
    )
    new_world = new_world.with_seed(next_seed(seed_state))

    # === Parite CLI : tick_scheduler + age + goal/breadcrumb auto-checks ===
    new_world, fired_evs, cancelled_evs = tick_scheduler(
        new_world, canon, turn_number=meta.total_turns + 1,
    )
    fired_event_ids = [ev.event_id for ev in fired_evs]
    cancelled_event_ids = [ev.event_id for ev in cancelled_evs]

    # Phase F (substitute injection) : delegue a /fast-forward pour
    # eviter d'imposer une latence LLM sur chaque /turn. Les events
    # cancelles dans un /turn restent cancelles tant qu'aucun appel
    # /fast-forward ou pipeline batch n'est lance.

    # Phase B : push player action au KG (best-effort)
    try:
        kg_db_path = save_module.kg_db_path(save_id)
        if kg_db_path.exists():
            from shinobi.kg.store import KnowledgeGraphStore

            with KnowledgeGraphStore(kg_db_path) as kg_store:
                _push_action_to_kg(
                    kg_store, new_char.name, action, enriched_result,
                    new_world.current_year,
                )
    except Exception:
        pass

    # Phase E : promote NPCs interagis par le joueur (best-effort)
    if payload.present_npc_ids:
        try:
            agents_db_path = save_module.agents_db_path(save_id)
            if agents_db_path.exists():
                from shinobi.agents import AgentMemoryStore, AgentRoster

                with AgentMemoryStore(agents_db_path) as store:
                    roster = AgentRoster(store)
                    for nid in payload.present_npc_ids:
                        roster.on_player_interaction(
                            nid,
                            year=new_world.current_year,
                            tick=meta.total_turns + 1,
                        )
        except Exception:
            pass

    # Vieillissement automatique si une annee est passee
    aged = False
    expected_age = new_world.current_year - new_char.birth_year
    if expected_age != new_char.age_years and expected_age >= 0:
        new_char = advance_age(new_char, expected_age)
        aged = True

    # Record fired events as known (CLI _record_fired_events_as_known)
    if fired_evs:
        new_known = dict(new_char.knowledge.known_events)
        added = False
        for ev in fired_evs:
            canon_ev = canon.timeline_events.get(ev.event_id)
            if canon_ev is None or ev.event_id in new_known:
                continue
            summary = (
                getattr(canon_ev, "narrative_summary_fr", "") or ""
            )[:120]
            new_known[ev.event_id] = (
                f"an {new_world.current_year} : "
                f"{getattr(canon_ev, 'name_fr', ev.event_id)} ({summary})"
            )
            added = True
        if added:
            new_kn = new_char.knowledge.model_copy(
                update={"known_events": new_known},
            )
            new_char = new_char.model_copy(update={"knowledge": new_kn})

    # Rumors : marque les rumors fraiches que le player peut entendre
    rumors_received_ids: list[str] = []
    if new_world.rumors:
        from shinobi.engine.rumors import player_can_hear, receive_rumor

        fired_set = set(fired_event_ids)
        for rumor in new_world.rumors:
            if rumor.received_by_player:
                continue
            if rumor.born_at_year != new_world.current_year:
                continue
            event_location = new_char.current_location
            if rumor.source_event_id:
                ev = canon.timeline_events.get(rumor.source_event_id)
                if ev and getattr(ev, "location", None):
                    event_location = ev.location
            if not player_can_hear(
                rumor,
                player_location=new_char.current_location,
                event_location=event_location,
                current_year=new_world.current_year,
            ):
                continue
            new_world = receive_rumor(
                new_world, rumor.id, year=new_world.current_year,
            )
            rumors_received_ids.append(rumor.id)

    # Living cost (parite CLI _charge_living_cost)
    living_cost_charged = 0
    days_passed = max(1, enriched_result.duration_minutes // (60 * 24))
    if days_passed > 0:
        try:
            from shinobi.engine.economy import cost_of_living_for_period
            from shinobi.engine.progression import apply_damage, apply_fatigue

            cost = cost_of_living_for_period(
                days=days_passed,
                inflation_factor=new_world.economy.inflation_factor,
            )
            if cost > 0:
                if new_char.money >= cost:
                    new_char = new_char.with_money(-cost)
                    living_cost_charged = cost
                else:
                    short = cost - new_char.money
                    living_cost_charged = new_char.money
                    new_char = new_char.with_money(-new_char.money)
                    new_char = apply_fatigue(
                        new_char, min(40, short // 20),
                    )
                    if short > 200:
                        new_char = apply_damage(
                            new_char, min(15, short // 100),
                            description="malnutrition",
                        )
        except Exception:
            living_cost_charged = 0

    # Touch present NPCs (parite CLI _touch_present_npcs)
    if payload.present_npc_ids:
        try:
            from shinobi.engine.relations import touch_relationship
            from shinobi.engine.world import NPCState

            for npc_id in payload.present_npc_ids:
                canon_npc = canon.characters.get(npc_id)
                if canon_npc is None:
                    continue
                rank = "unknown"
                if canon_npc.rank_progression:
                    rank = canon_npc.rank_progression[-1].rank
                npc_age = (
                    new_world.current_year - canon_npc.birth_year
                    if canon_npc.birth_year else 25
                )
                is_alive = True
                if (
                    canon_npc.death_year is not None
                    and new_world.current_year >= canon_npc.death_year
                ):
                    is_alive = False
                existing = new_world.npc_states.get(npc_id)
                new_world = new_world.with_npc_state(
                    NPCState(
                        character_id=npc_id,
                        is_alive=is_alive if existing is None else existing.is_alive,
                        current_location=new_char.current_location,
                        current_year=new_world.current_year,
                        current_age=max(0, npc_age),
                        current_rank=rank,
                        last_updated_year=new_world.current_year,
                    )
                )
                new_char = touch_relationship(
                    new_char,
                    with_id=npc_id,
                    year=new_world.current_year,
                )
        except Exception:
            pass

    # Phase D : drift personnalite pour events fired (best-effort)
    if fired_evs:
        try:
            from shinobi.personality import (
                PersonalityEngine,
                PersonalityStore,
                collect_experienced_events,
            )

            db_path = save_module.personality_db_path(save_id)
            if db_path.exists():
                canon_events = [
                    canon.timeline_events.get(ev.event_id)
                    for ev in fired_evs
                ]
                canon_events = [e for e in canon_events if e is not None]
                experienced = collect_experienced_events(
                    timeline_events=canon_events,
                )
                if experienced:
                    engine = PersonalityEngine()
                    per_npc: dict[str, list] = {}
                    for e in experienced:
                        per_npc.setdefault(e.npc_id, []).append(e)
                    with PersonalityStore(db_path) as store:
                        for npc_id, events in per_npc.items():
                            p = store.get_personality(npc_id)
                            if p is None:
                                continue
                            p = engine.apply_events(p, events)
                            store.save_personality_with_history(p)
        except Exception:
            pass

    # Biography milestones (parite CLI _log_biography_milestones)
    try:
        from shinobi.engine.character import BiographyEvent

        bio_events: list[BiographyEvent] = []
        year_now = new_world.current_year
        age_now = new_char.age_years
        if char_before.rank != new_char.rank:
            bio_events.append(
                BiographyEvent(
                    year=year_now, age=age_now,
                    summary=t(
                        "engine.biography.summary.promotion",
                        old=char_before.rank,
                        new=new_char.rank,
                    ),
                    category="rank_promotion",
                )
            )
        before_techs = {tk.technique_id for tk in char_before.techniques_known}
        after_techs = {tk.technique_id for tk in new_char.techniques_known}
        for tid in after_techs - before_techs:
            bio_events.append(
                BiographyEvent(
                    year=year_now, age=age_now,
                    summary=t("engine.biography.summary.technique_learned", technique_id=tid),
                    category="technique_learned",
                )
            )
        if not char_before.is_dead and new_char.is_dead:
            bio_events.append(
                BiographyEvent(
                    year=year_now, age=age_now,
                    summary=new_char.death_circumstances
                    or t("engine.biography.summary.death_default"),
                    category="trauma",
                )
            )
        else:
            ratio_after = (
                new_char.health.hp_current / max(1, new_char.health.hp_max)
            )
            ratio_before = (
                char_before.health.hp_current
                / max(1, char_before.health.hp_max)
            )
            if ratio_after < 0.2 <= ratio_before:
                bio_events.append(
                    BiographyEvent(
                        year=year_now, age=age_now,
                        summary=t(
                            "engine.biography.summary.severe_injury",
                            hp=new_char.health.hp_current,
                            hp_max=new_char.health.hp_max,
                        ),
                        category="trauma",
                    )
                )
        if not char_before.is_missing_nin and new_char.is_missing_nin:
            bio_events.append(
                BiographyEvent(
                    year=year_now, age=age_now,
                    summary=t(
                        "engine.biography.summary.becomes_nukenin",
                        village=char_before.current_village,
                    ),
                    category="other",
                )
            )
        if bio_events:
            new_log = [*new_char.biography_log, *bio_events]
            new_char = new_char.model_copy(update={"biography_log": new_log})
    except Exception:
        pass

    turn_number = meta.total_turns + 1
    save_module.save_turn(
        save_id,
        turn_number=turn_number,
        action_result=enriched_result,
        new_character=new_char,
        new_world=new_world,
        seed_state=seed_state,
    )

    # Goals : auto-completion + auto-failure (parite CLI _check_goal_completions)
    completed_goal_descriptions: list[str] = []
    failed_goal_descriptions: list[str] = []
    try:
        from shinobi.goals.completion import (
            check_goal_by_target,
            check_goal_completion,
        )
        from shinobi.goals.declaration import (
            complete_goal,
            detect_goal_failure,
            fail_goal,
        )

        goals_now = save_module.load_goals(save_id)
        breadcrumbs_now = save_module.load_breadcrumbs(save_id)
        canon_chars = canon.characters
        player_dead = getattr(new_char, "is_dead", False)
        for g in goals_now:
            if g.status.value not in ("declared", "in_progress"):
                continue
            fail_reason = detect_goal_failure(
                g, canon_characters=canon_chars,
                current_year=new_world.current_year,
                player_is_dead=player_dead,
            )
            if fail_reason is not None:
                failed = fail_goal(g, new_world.current_year, reason=fail_reason)
                save_module.save_goal(save_id, failed)
                failed_goal_descriptions.append(g.description_player)
                continue
            if check_goal_completion(g, breadcrumbs_now) or check_goal_by_target(
                g, new_char,
            ):
                done = complete_goal(g, new_world.current_year)
                save_module.save_goal(save_id, done)
                completed_goal_descriptions.append(g.description_player)
    except Exception:
        pass

    # Breadcrumbs : auto-completion (parite CLI _check_breadcrumb_completions)
    completed_breadcrumb_descriptions: list[str] = []
    try:
        from shinobi.goals.breadcrumbs import mark_completed
        from shinobi.goals.completion import check_breadcrumb_completion

        for bc in save_module.load_breadcrumbs(save_id):
            if bc.completed or not bc.revealed:
                continue
            if check_breadcrumb_completion(
                bc, action_result=enriched_result, character=new_char,
            ):
                updated_bc = mark_completed(bc, new_world.current_year)
                save_module.save_breadcrumb(save_id, updated_bc)
                completed_breadcrumb_descriptions.append(bc.description)
    except Exception:
        pass
    save_module.append_narrative_log(
        save_id,
        {
            "turn": turn_number,
            "year": new_world.current_year,
            "date": new_world.current_date,
            "intent": payload.intent_text,
            "outcome": enriched_result.outcome.value,
            "summary_fr": enriched_result.summary_fr,
        },
    )
    return TurnResponse(
        turn_number=turn_number,
        action_type=enriched_result.action.action_type.value,
        outcome=enriched_result.outcome.value,
        summary_fr=enriched_result.summary_fr,
        duration_minutes=enriched_result.duration_minutes,
        chakra_cost=enriched_result.chakra_cost,
        money_delta=enriched_result.money_delta,
        hp_delta=enriched_result.hp_delta,
        fatigue_delta=enriched_result.fatigue_delta,
        stat_changes=list(enriched_result.stat_changes),
        consequences=list(enriched_result.consequences),
        current_year=new_world.current_year,
        current_date=new_world.current_date,
        current_hour=new_world.current_hour,
        character_age=new_char.age_years,
        character_hp=new_char.health.hp_current,
        character_chakra=new_char.chakra.current,
        fired_event_ids=fired_event_ids,
        cancelled_event_ids=cancelled_event_ids,
        completed_goal_descriptions=completed_goal_descriptions,
        failed_goal_descriptions=failed_goal_descriptions,
        completed_breadcrumb_descriptions=completed_breadcrumb_descriptions,
        aged=aged,
        rumors_received_ids=rumors_received_ids,
        living_cost_charged=living_cost_charged,
        new_money=new_char.money,
    )


@router.get(
    "/{save_id}/narrative_log",
    summary="Append-only narrative log",
)
def narrative_log(
    save_id: str,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """Read the append-only narrative log of a save.

    Returns the last `limit` events after skipping `offset`.
    """
    if save_id not in {m.save_id for m in save_module.list_saves()}:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=t("api.saves.not_found", save_id=save_id),
        )
    log_path = save_module._narrative_log_path(save_id)
    entries: list[dict[str, Any]] = []
    if log_path.exists():
        with log_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    sliced = entries[-(limit + offset):]
    if offset > 0:
        sliced = sliced[: -offset] if offset < len(sliced) else []
    return {
        "save_id": save_id,
        "total": len(entries),
        "offset": offset,
        "limit": limit,
        "entries": sliced[-limit:],
    }


@router.post(
    "/{save_id}/skip-time",
    response_model=SkipTimeResponse,
    summary="Skip time (days/weeks/months)",
)
def skip_time(
    save_id: str,
    payload: SkipTimeRequest,
    canon: Any = Depends(get_canon),
) -> SkipTimeResponse:
    """Advance the world by N days/weeks/months and tick the scheduler monthly.

    No player action: the character is unchanged, canon events fire by
    date. Equivalent to /skip + /fast-forward (mechanical only).
    """
    try:
        character, world, meta = save_module.load_save(save_id)
    except SaveNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc),
        ) from exc

    days_total = payload.days + payload.weeks * 7 + payload.months * 30
    if days_total <= 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=t("api.play.skip_time.requires_positive_duration"),
        )

    fired_ids: list[str] = []
    new_world = world
    remaining_days = days_total
    turn = meta.total_turns
    # Tick par increments de 30 jours pour eviter d'evaluer le scheduler
    # mille fois sur de longs skips (CLI utilise la meme strategie).
    while remaining_days > 0:
        step_days = min(30, remaining_days)
        month_str, day_str = new_world.current_date.split("-")
        current = GameDate(
            year=new_world.current_year,
            month=int(month_str),
            day=int(day_str),
            hour=new_world.current_hour,
            minute=new_world.current_minute,
        )
        advanced = current.add_days(step_days)
        new_world = new_world.with_time(
            year=advanced.year,
            date=f"{advanced.month:02d}-{advanced.day:02d}",
            hour=advanced.hour,
            minute=advanced.minute,
        )
        new_world, fired, _cancelled = tick_scheduler(
            new_world, canon, turn_number=turn,
        )
        fired_ids.extend(ev.event_id for ev in fired)
        turn += 1
        remaining_days -= step_days

    save_module.save_passive_state(
        save_id,
        turn_number=meta.total_turns,
        new_character=character,
        new_world=new_world,
        seed_state=new_world.seed,
    )
    return SkipTimeResponse(
        new_year=new_world.current_year,
        new_date=new_world.current_date,
        new_hour=new_world.current_hour,
        days_skipped=days_total,
        fired_event_ids=fired_ids,
    )


@router.post(
    "/{save_id}/fast-forward",
    response_model=FastForwardResponse,
    summary="Simulate N months in passive mode (CLI parity /fast-forward)",
)
async def fast_forward(
    save_id: str,
    payload: FastForwardRequest,
    canon: Any = Depends(get_canon),
) -> FastForwardResponse:
    """Advance the world by N months without player intervention. Each month:
    tick scheduler, age the character if the year changes, accumulate the digest.

    If the LLM is available and an event was cancelled, attempt to inject a
    substitute via WorldResolverPipeline (best-effort, no-op if LLM down).
    """
    try:
        character, world, meta = save_module.load_save(save_id)
    except SaveNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc),
        ) from exc

    fired_ids: list[str] = []
    cancelled_ids: list[str] = []
    substitute_ids: list[str] = []
    llm_used = False

    # Probe LLM une fois pour decider du best-effort Phase F
    try:
        from shinobi.llm.client import LLMClient

        async with LLMClient() as probe:
            llm_up = await probe.health()
    except Exception:
        llm_up = False

    new_world = world
    new_char = character
    starting_year = world.current_year
    turn = meta.total_turns
    for offset_month in range(payload.months):
        month_str, day_str = new_world.current_date.split("-")
        current = GameDate(
            year=new_world.current_year,
            month=int(month_str),
            day=int(day_str),
            hour=new_world.current_hour,
            minute=new_world.current_minute,
        )
        advanced = current.add_days(30)
        new_world = new_world.with_time(
            year=advanced.year,
            date=f"{advanced.month:02d}-{advanced.day:02d}",
            hour=advanced.hour,
            minute=advanced.minute,
        )
        new_world, fired, cancelled = tick_scheduler(
            new_world, canon, turn_number=turn,
        )
        fired_ids.extend(ev.event_id for ev in fired)
        cancelled_ids.extend(ev.event_id for ev in cancelled)

        # Best-effort Phase F : si event annule + LLM up, tente substitute
        if cancelled and llm_up:
            try:
                from shinobi.world_resolver import (
                    WorldResolverPipeline,
                )

                async with LLMClient() as llm:
                    pipeline = WorldResolverPipeline(llm_client=llm, canon=canon)
                    for canc in cancelled:
                        sub = await pipeline.resolve(
                            cancelled_event_id=canc.event_id,
                            world=new_world,
                            character=new_char,
                        )
                        if sub is not None:
                            substitute_ids.append(getattr(sub, "id", canc.event_id))
                            llm_used = True
            except Exception:
                pass

        # Vieillit si une annee est passee depuis le dernier age applique
        new_age = new_world.current_year - new_char.birth_year
        if new_age != new_char.age_years and new_age > 0:
            new_char = advance_age(new_char, new_age)

        turn += 1

    save_module.save_passive_state(
        save_id,
        turn_number=meta.total_turns,
        new_character=new_char,
        new_world=new_world,
        seed_state=new_world.seed,
    )
    return FastForwardResponse(
        months_simulated=payload.months,
        new_year=new_world.current_year,
        new_date=new_world.current_date,
        new_age=new_char.age_years,
        fired_event_ids=fired_ids,
        cancelled_event_ids=cancelled_ids,
        substitute_injected=substitute_ids,
        llm_used=llm_used,
    )


@router.post(
    "/{save_id}/initialize",
    response_model=InitializeResponse,
    summary="Bootstrap Phase A/B/D/E (KG + personality + agents)",
)
def initialize(
    save_id: str,
    canon: Any = Depends(get_canon),
) -> InitializeResponse:
    """Initialize Phase A/B subsystems (KG canon + missions),
    Phase D (personality baselines), Phase E (agent roster). Idempotent:
    if the DB is already populated, do not re-import.

    Without this call (or /turn/skip-time/fast-forward which can trigger
    them), the Phase B/D/E hooks in /turn remain no-ops.
    """
    try:
        _, world, _meta = save_module.load_save(save_id)
    except SaveNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc),
        ) from exc

    errors: list[str] = []

    # Phase A/B : KG canon + missions
    kg_initialized = False
    kg_facts_count = 0
    try:
        from shinobi.config import settings as _s
        from shinobi.kg.loader import import_canon_to_kg
        from shinobi.kg.store import KnowledgeGraphStore
        from shinobi.missions.catalog import MissionCatalog
        from shinobi.missions.kg_integration import import_missions_to_kg

        db_path = save_module.kg_db_path(save_id)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with KnowledgeGraphStore(db_path) as store:
            existing_canon = store.count(source_prefix="canon")
            if existing_canon == 0:
                import_canon_to_kg(
                    store, _s.canonical_data_dir, clear_first=False,
                )
            missions_path = _s.canonical_data_dir / "missions.json"
            if missions_path.exists():
                existing_missions = store.count(source_prefix="mission:")
                if existing_missions == 0:
                    catalog = MissionCatalog.from_json_file(missions_path)
                    if catalog.count > 0:
                        import_missions_to_kg(
                            store, catalog.all(), clear_first=False,
                        )
            kg_facts_count = store.count(source_prefix="")
        kg_initialized = True
    except Exception as exc:
        errors.append(f"kg: {type(exc).__name__}: {exc}")

    # Phase D : personality baselines
    personality_initialized = False
    personality_baselines_count = 0
    try:
        from shinobi.config import settings as _s
        from shinobi.personality import (
            PersonalityStore,
            extract_baselines_combined,
        )

        db_path = save_module.personality_db_path(save_id)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        psycho = _s.canonical_data_dir / "psycho_notes.json"
        chars = _s.canonical_data_dir / "characters.json"
        with PersonalityStore(db_path) as store:
            existing = {p.npc_id for p in store.list_personalities()}
            if psycho.exists() or chars.exists():
                baselines = extract_baselines_combined(
                    psycho_notes_path=psycho if psycho.exists() else None,
                    characters_path=chars if chars.exists() else None,
                )
                for npc_id, p in baselines.items():
                    if npc_id in existing:
                        continue
                    store.upsert_personality(p)
            personality_baselines_count = len(store.list_personalities())
        personality_initialized = True
    except Exception as exc:
        errors.append(f"personality: {type(exc).__name__}: {exc}")

    # Phase E : agent roster
    agents_initialized = False
    agents_count = 0
    try:
        from shinobi.agents import (
            AgentMemoryStore,
            AgentRoster,
            initialize_roster,
            load_eras_data,
        )
        from shinobi.config import settings as _s

        db_path = save_module.agents_db_path(save_id)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with AgentMemoryStore(db_path) as store:
            existing = len(store.list_roster())
            if existing == 0:
                initialize_roster(store, included_since_year=world.current_year)
            roster = AgentRoster(store)
            eras_path = _s.canonical_data_dir / "eras.json"
            eras_data = load_eras_data(eras_path)
            if eras_data:
                roster.promote_arc_relevant(world.current_year, eras_data)
            agents_count = len(store.list_roster())
        agents_initialized = True
    except Exception as exc:
        errors.append(f"agents: {type(exc).__name__}: {exc}")

    # Phase 3 : bootstrap RAG index (best-effort)
    rag_status = "skipped"
    try:
        from shinobi.rag.bootstrap import bootstrap_index

        rag_status = bootstrap_index(allow_local_build=False)
    except Exception as exc:
        rag_status = "failed"
        errors.append(f"rag: {type(exc).__name__}: {exc}")

    # Phase G : DirectorState (init si absent)
    director_state_initialized = False
    try:
        import json as _json

        from shinobi.director import DirectorState

        d_path = save_module.director_state_path(save_id)
        d_path.parent.mkdir(parents=True, exist_ok=True)
        if not d_path.exists():
            empty_state = DirectorState()
            d_path.write_text(
                _json.dumps(empty_state.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        director_state_initialized = True
    except Exception as exc:
        errors.append(f"director: {type(exc).__name__}: {exc}")

    # Phase i18n.8 : migration silencieuse des goals existants vers le
    # nouveau schema (detection langue + cache traduction). Idempotent : un
    # goal deja migre est skip. Si Qwen est down, les goals restent en
    # pending et seront retentes au prochain /initialize.
    goals_i18n_migrated = 0
    goals_i18n_pending = 0
    try:
        from shinobi.i18n.catalog import get_active_language
        from shinobi.i18n.goal_migration import migrate_save_goals

        target_lang = get_active_language()
        stats = migrate_save_goals(save_id, target_lang=target_lang)
        goals_i18n_migrated = stats["migrated"]
        goals_i18n_pending = stats["pending"]
    except Exception as exc:
        errors.append(f"goals_i18n: {type(exc).__name__}: {exc}")

    return InitializeResponse(
        save_id=save_id,
        kg_initialized=kg_initialized,
        kg_facts_count=kg_facts_count,
        personality_initialized=personality_initialized,
        personality_baselines_count=personality_baselines_count,
        agents_initialized=agents_initialized,
        agents_count=agents_count,
        rag_index_status=rag_status,
        director_state_initialized=director_state_initialized,
        goals_i18n_migrated=goals_i18n_migrated,
        goals_i18n_pending=goals_i18n_pending,
        errors=errors,
    )
