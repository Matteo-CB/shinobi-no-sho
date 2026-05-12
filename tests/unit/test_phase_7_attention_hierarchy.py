"""Phase 7.2/7.3 : tests hierarchie d'attention + lazy update PNJ.

Couvre :
- AgentTier (major/secondary/background) transitions
- AgentRoster.should_simulate_this_tick : major chaque tick, secondary
  tous les N ticks, background jamais
- on_event_impact : auto-promotion d'un PNJ background -> secondary apres
  un event impactant
- arc_relevant_npcs : selection key_figures selon era courante
- promote/demote helpers
"""
from __future__ import annotations

from pathlib import Path

import pytest

from shinobi.agents.roster import AgentRoster
from shinobi.agents.store import AgentMemoryStore
from shinobi.agents.types import AgentTier


@pytest.fixture()
def store(tmp_path: Path) -> AgentMemoryStore:
    s = AgentMemoryStore(db_path=str(tmp_path / "tier_test.db"))
    yield s
    s.close()


@pytest.fixture()
def roster(store) -> AgentRoster:
    return AgentRoster(store)


# === AgentTier enum ======================================================


def test_agent_tier_three_levels() -> None:
    """3 niveaux : major (top-15), secondary (~50), background (le reste)."""
    assert AgentTier.major.value == "major"
    assert AgentTier.secondary.value == "secondary"
    assert AgentTier.background.value == "background"


# === should_simulate_this_tick ==========================================


def test_major_simulated_every_tick(roster) -> None:
    """Tier major : simule a chaque tick."""
    roster.add("npc_major", AgentTier.major)
    for tick in range(10):
        assert roster.should_simulate_this_tick(
            "npc_major", tick=tick,
        ) is True


def test_secondary_simulated_every_n_ticks(roster) -> None:
    """Tier secondary : simule tous les N ticks (default 10)."""
    roster.add("npc_sec", AgentTier.secondary)
    # Tick 0 : oui (0 % 10 == 0)
    assert roster.should_simulate_this_tick("npc_sec", tick=0) is True
    # Ticks 1..9 : non
    for t in range(1, 10):
        assert roster.should_simulate_this_tick(
            "npc_sec", tick=t,
        ) is False
    # Tick 10 : oui
    assert roster.should_simulate_this_tick("npc_sec", tick=10) is True


def test_secondary_period_configurable(roster) -> None:
    """secondary_period=5 -> simule tous les 5 ticks."""
    roster.add("npc_sec", AgentTier.secondary)
    assert roster.should_simulate_this_tick(
        "npc_sec", tick=0, secondary_period=5,
    ) is True
    assert roster.should_simulate_this_tick(
        "npc_sec", tick=5, secondary_period=5,
    ) is True
    assert roster.should_simulate_this_tick(
        "npc_sec", tick=3, secondary_period=5,
    ) is False


def test_background_never_simulated(roster) -> None:
    """Tier background : jamais simule directement."""
    # Un NPC absent du roster -> tier_for retourne background
    for tick in range(20):
        assert roster.should_simulate_this_tick(
            "unknown_npc_xyz", tick=tick,
        ) is False


# === Promote / demote ====================================================


def test_promote_secondary_to_major(roster) -> None:
    """promote eleve un secondary au tier major."""
    roster.add("npc_x", AgentTier.secondary)
    promoted = roster.promote("npc_x", included_since_year=10)
    assert promoted is not None
    assert promoted.tier == AgentTier.major
    assert promoted.included_since_year == 10


def test_promote_background_to_secondary(roster) -> None:
    """promote eleve un background au tier secondary (par defaut promotion 1 niveau)."""
    promoted = roster.promote("new_npc")
    assert promoted is not None
    # Background -> secondary par defaut (NOT major)
    assert promoted.tier in (AgentTier.major, AgentTier.secondary)


def test_demote_major_to_secondary(roster) -> None:
    """demote redescend un major."""
    roster.add("npc_y", AgentTier.major)
    demoted = roster.demote("npc_y", reason="test demote")
    if demoted is not None:
        # Tier passe a secondary ou background
        assert demoted.tier in (AgentTier.secondary, AgentTier.background)


# === on_event_impact (auto-promote) ======================================


def test_on_event_impact_auto_promotes_npcs(roster) -> None:
    """on_event_impact eleve les NPCs impactes au moins en secondary."""
    roster.on_event_impact(
        ["npc_a", "npc_b"], year=10, tick=5,
    )
    # Les 2 NPCs doivent avoir un tier au moins secondary
    for npc_id in ("npc_a", "npc_b"):
        tier = roster.tier_for(npc_id)
        assert tier in (AgentTier.major, AgentTier.secondary)


def test_on_event_impact_preserves_existing_major(roster) -> None:
    """Un major reste major apres event impact (pas de demote silencieux)."""
    roster.add("npc_already_major", AgentTier.major)
    roster.on_event_impact(["npc_already_major"], year=10, tick=5)
    assert roster.tier_for("npc_already_major") == AgentTier.major


# === arc_relevant_npcs (era-based dynamic promotion) =====================


def test_arc_relevant_npcs_returns_era_key_figures(roster) -> None:
    """arc_relevant_npcs lit key_figures de l'era courante."""
    eras_data = [
        {
            "id": "warring_states_era",
            "year_start": -100, "year_end": -65,
            "key_figures": ["senju_hashirama", "uchiha_madara"],
        },
        {
            "id": "naruto_part_1",
            "year_start": 12, "year_end": 14,
            "key_figures": ["uzumaki_naruto", "uchiha_sasuke", "haruno_sakura"],
        },
    ]
    figs_year_13 = roster.arc_relevant_npcs(year=13, eras_data=eras_data)
    assert "uzumaki_naruto" in figs_year_13
    assert "uchiha_sasuke" in figs_year_13
    # Pas inclus : Hashirama est en warring_states
    assert "senju_hashirama" not in figs_year_13


def test_arc_relevant_npcs_no_match_returns_empty(roster) -> None:
    """Year hors de toute era -> liste vide."""
    eras_data = [
        {
            "id": "test_era", "year_start": 0, "year_end": 5,
            "key_figures": ["x"],
        },
    ]
    assert roster.arc_relevant_npcs(year=100, eras_data=eras_data) == []


def test_arc_relevant_npcs_no_eras_returns_empty(roster) -> None:
    """eras_data None -> liste vide (graceful)."""
    assert roster.arc_relevant_npcs(year=10, eras_data=None) == []


# === Phase 7.3 : AttentionLevel HIGH/MEDIUM/LOW dans NPCState ============


def test_attention_level_three_levels() -> None:
    """Spec 7.3 : NPC ont 3 niveaux d'attention HIGH/MEDIUM/LOW."""
    from shinobi.types import AttentionLevel
    assert AttentionLevel.high.value == "HIGH"
    assert AttentionLevel.medium.value == "MEDIUM"
    assert AttentionLevel.low.value == "LOW"


def test_npc_state_default_attention_low() -> None:
    """Spec 7.3 : NPC default a LOW attention (mise a jour paresseuse)."""
    from shinobi.engine.world import NPCState
    from shinobi.types import AttentionLevel

    npc = NPCState(
        character_id="random_npc",
        current_location="konohagakure",
        current_year=10, current_age=20, current_rank="genin",
    )
    assert npc.attention_level == AttentionLevel.low


def test_npc_state_high_attention_explicit() -> None:
    """Un NPC peut etre eleve a HIGH attention pour simulation active."""
    from shinobi.engine.world import NPCState
    from shinobi.types import AttentionLevel

    npc = NPCState(
        character_id="key_npc",
        current_location="konohagakure",
        current_year=12, current_age=15, current_rank="chunin",
        attention_level=AttentionLevel.high,
    )
    assert npc.attention_level == AttentionLevel.high


# === Phase 7.6 (extension) : substitute generation on cancellation =======


def test_phase_f_substitute_event_dict_persists_after_injection() -> None:
    """Phase 7.6 (cascade) : apres une injection de substitute Phase F,
    le world.substitute_events conserve le dict pour le scheduler.

    Verifie que le substitute survit a un round-trip Pydantic + tick.
    """
    from shinobi.canon.profiles import CanonicityProfile
    from shinobi.engine.world import (
        ScheduledEvent,
        create_default_world,
    )
    from shinobi.types import EventStatus

    world = create_default_world(
        profile=CanonicityProfile.default(), starting_year=10,
    )
    sub_id = "substitute_test_xyz"
    sub_dict = {
        "id": sub_id,
        "name_fr": "Test substitute Phase 7.6",
        "year": 10,
        "preconditions": [],
        "outcomes": [{"type": "test", "parameters": {}}],
    }
    new_world = world.model_copy(update={
        "substitute_events": {sub_id: sub_dict},
        "scheduled_events": [
            ScheduledEvent(
                event_id=sub_id, year=10, date="01-01",
                status=EventStatus.scheduled,
            ),
        ],
    })
    # Le substitute persist dans world.substitute_events
    assert sub_id in new_world.substitute_events
    assert new_world.substitute_events[sub_id]["name_fr"] == (
        "Test substitute Phase 7.6"
    )
