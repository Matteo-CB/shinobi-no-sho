"""Tests du pont engine/rumors.py <-> kg/belief.py.

Couvre :
- rumor_to_fact : conversion Rumor (world) -> Fact (KG) avec preservation
  fidelity / radius / born_at_year / expires_at_year
- insert_rumor_as_fact : persistance dans le store
- propagate_rumor_to_npcs : insere des beliefs pour une liste de NPCs
- propagate_rumor_via_social : insere fact + temoins primaires + cascade BFS
- belief_to_rumor : conversion inverse pour publication world-level
- sync_world_rumors_to_kg : idempotence (re-sync ne double pas les facts)

Verifie que la spec roadmap §5.4 est satisfaite : 'Les rumeurs (systeme
existant engine/rumors.py) propagent les faits entre sous-KG selon les
liens sociaux'.
"""

from __future__ import annotations

import pytest

from shinobi.engine.rumors import make_rumor_from_event
from shinobi.engine.world import Rumor, WorldState
from shinobi.kg import (
    BeliefPropagator,
    Canonicity,
    Fact,
    KnowledgeGraphStore,
    ObjectType,
    SocialLink,
    SocialNetwork,
    belief_to_rumor,
    insert_rumor_as_fact,
    propagate_rumor_to_npcs,
    propagate_rumor_via_social,
    rumor_to_fact,
    sync_world_rumors_to_kg,
)
from shinobi.kg.schema import Belief


@pytest.fixture
def store() -> KnowledgeGraphStore:
    s = KnowledgeGraphStore(None)
    yield s
    s.close()


@pytest.fixture
def social(store: KnowledgeGraphStore) -> SocialNetwork:
    return SocialNetwork(store.conn)


@pytest.fixture
def propagator(store: KnowledgeGraphStore) -> BeliefPropagator:
    return BeliefPropagator(store.conn)


def make_rumor(
    *,
    rid: str = "rumor1",
    content: str = "Itachi a tue le clan",
    fidelity: float = 0.8,
    radius: str = "regional",
    born: int = 8,
) -> Rumor:
    return Rumor(
        id=rid,
        source_event_id="uchiha_massacre",
        content=content,
        fidelity=fidelity,
        diffusion_radius=radius,  # type: ignore[arg-type]
        born_at_year=born,
        expires_at_year=born + 5,
    )


# --- rumor_to_fact ----------------------------------------------------------


def test_rumor_to_fact_preserves_fidelity_and_temporal() -> None:
    rumor = make_rumor(fidelity=0.7, born=10)
    fact = rumor_to_fact(rumor)
    assert fact.subject == "uchiha_massacre"
    assert fact.relation == "is_rumored"
    assert fact.confidence == 0.7
    assert fact.valid_from_year == 10
    assert fact.valid_to_year == 15
    assert fact.canonicity == Canonicity.canon_modified
    assert fact.source.startswith("rumor:")
    assert fact.object_type == ObjectType.belief


def test_rumor_to_fact_subject_override() -> None:
    rumor = make_rumor()
    fact = rumor_to_fact(rumor, subject_override="custom_subject")
    assert fact.subject == "custom_subject"


def test_insert_rumor_as_fact_persists(store: KnowledgeGraphStore) -> None:
    rumor = make_rumor()
    fid = insert_rumor_as_fact(store, rumor)
    got = store.get_fact(fid)
    assert got is not None
    assert got.relation == "is_rumored"
    assert got.confidence == pytest.approx(0.8)


# --- propagate_rumor_to_npcs -----------------------------------------------


def test_propagate_rumor_to_npcs_creates_beliefs(
    store: KnowledgeGraphStore, propagator: BeliefPropagator,
) -> None:
    rumor = make_rumor(fidelity=0.9)
    fid = insert_rumor_as_fact(store, rumor)
    spread = propagate_rumor_to_npcs(
        propagator, rumor, fid,
        npcs_in_radius=["naruto", "sakura", "iruka"],
        channel="rumor",
    )
    # 0.9 * 0.7 (rumor decay) = 0.63
    assert spread["naruto"] == pytest.approx(0.63)
    assert all(propagator.get_belief(fid, n) is not None
               for n in ["naruto", "sakura", "iruka"])


def test_propagate_rumor_to_npcs_witness_channel_no_decay(
    store: KnowledgeGraphStore, propagator: BeliefPropagator,
) -> None:
    rumor = make_rumor(fidelity=0.85)
    fid = insert_rumor_as_fact(store, rumor)
    spread = propagate_rumor_to_npcs(
        propagator, rumor, fid, ["naruto"], channel="witness",
    )
    # witness decay = 1.0 -> fidelity preservee
    assert spread["naruto"] == pytest.approx(0.85)


# --- propagate_rumor_via_social --------------------------------------------


def test_propagate_rumor_via_social_combines_primary_and_cascade(
    store: KnowledgeGraphStore,
    propagator: BeliefPropagator,
    social: SocialNetwork,
) -> None:
    """Un rumeur propagee via temoin primaire + cascade BFS via social network."""
    # naruto temoin primaire, lien fort vers iruka, lien moyen vers hiruzen
    social.add_link(SocialLink(npc_a="naruto", npc_b="iruka", strength=1.0))
    social.add_link(SocialLink(npc_a="iruka", npc_b="hiruzen", strength=0.8))
    rumor = make_rumor(fidelity=1.0)

    fact_id, spread = propagate_rumor_via_social(
        store, propagator, rumor,
        primary_witnesses=["naruto"],
        max_depth=2,
    )

    # Naruto temoin primaire, fidelity = rumor.fidelity = 1.0
    assert spread["naruto"] == pytest.approx(1.0)
    # Iruka : 1.0 * 1.0 * 0.7 (rumor) = 0.7
    assert spread["iruka"] == pytest.approx(0.7)
    # Hiruzen : 0.7 * 0.8 * 0.7 = 0.392
    assert spread["hiruzen"] == pytest.approx(0.392)
    # Le fact existe bien
    assert store.get_fact(fact_id) is not None


def test_propagate_rumor_via_social_no_witnesses_just_fact(
    store: KnowledgeGraphStore,
    propagator: BeliefPropagator,
) -> None:
    """Sans temoins primaires, on cree juste le fact, pas de beliefs."""
    rumor = make_rumor()
    fact_id, spread = propagate_rumor_via_social(
        store, propagator, rumor, primary_witnesses=[], max_depth=3,
    )
    assert spread == {}
    assert store.get_fact(fact_id) is not None
    assert propagator.count() == 0


# --- belief_to_rumor --------------------------------------------------------


def test_belief_to_rumor_publishes_belief() -> None:
    fact = Fact(
        subject="player_event_42",
        relation="happened",
        object="Le joueur a sauve Itachi",
        object_type=ObjectType.belief,
        valid_from_year=8,
    )
    belief = Belief(
        fact_id=1, npc_id="naruto",
        fidelity=0.85, learned_at_year=8,
    )
    rumor = belief_to_rumor(fact, belief, radius="proximity", expires_in_years=3)
    assert rumor.fidelity == pytest.approx(0.85)
    assert rumor.diffusion_radius == "proximity"
    assert rumor.born_at_year == 8
    assert rumor.expires_at_year == 11
    assert "Le joueur a sauve Itachi" in rumor.content


def test_belief_to_rumor_preserves_subject_for_entity_facts() -> None:
    fact = Fact(
        subject="uchiha_massacre",
        relation="cancelled_by_player",
        object="player_uchiha_endo",
        object_type=ObjectType.entity,
    )
    belief = Belief(fact_id=2, npc_id="sasuke", fidelity=0.6, learned_at_year=10)
    rumor = belief_to_rumor(fact, belief)
    assert rumor.source_event_id == "uchiha_massacre"


# --- sync_world_rumors_to_kg ------------------------------------------------


def test_sync_world_rumors_creates_facts_and_beliefs(
    store: KnowledgeGraphStore, propagator: BeliefPropagator,
) -> None:
    r1 = make_rumor(rid="r1", content="Itachi est mort", born=12)
    r2 = make_rumor(rid="r2", content="Pain a attaque Konoha", born=14)
    world = WorldState(
        current_year=14, current_date="01-01",
        rumors=[r1, r2],
    )
    npcs_per = {"r1": ["sakura", "naruto"], "r2": ["jiraiya"]}
    stats = sync_world_rumors_to_kg(store, propagator, world,
                                       npcs_per_rumor=npcs_per)
    assert stats["rumors_processed"] == 2
    assert stats["facts_created"] == 2
    assert stats["beliefs_created"] == 3


def test_sync_world_rumors_idempotent(
    store: KnowledgeGraphStore, propagator: BeliefPropagator,
) -> None:
    """Re-sync ne double pas les facts."""
    rumor = make_rumor(rid="rA")
    world = WorldState(
        current_year=10, current_date="01-01", rumors=[rumor],
    )
    s1 = sync_world_rumors_to_kg(store, propagator, world)
    s2 = sync_world_rumors_to_kg(store, propagator, world)
    assert s1["facts_created"] == 1
    assert s2["facts_created"] == 0  # deja la
    assert store.count(source_prefix="rumor:") == 1


# --- Integration scenario : event canon -> rumor -> beliefs ----------------


def test_full_scenario_uchiha_massacre_rumor_propagation(
    store: KnowledgeGraphStore,
    propagator: BeliefPropagator,
    social: SocialNetwork,
) -> None:
    """Scenario integration : massacre Uchiha (year 8) -> rumeur regional ->
    propagation via reseau social konoha."""
    # Reseau social konoha
    social.add_link(SocialLink(npc_a="kakashi", npc_b="iruka", strength=0.9))
    social.add_link(SocialLink(npc_a="iruka", npc_b="naruto", strength=0.85))
    social.add_link(SocialLink(npc_a="kakashi", npc_b="sarutobi_hiruzen", strength=0.8))

    # Massacre Uchiha en l'an 8 -> rumeur regionale
    from shinobi.canon.models import (
        CancellationStrategy,
        TimelineEvent,
    )
    from shinobi.types import Canonicity as CanonCanonicity

    event = TimelineEvent(
        id="uchiha_massacre",
        name_fr="Massacre du clan Uchiha",
        year=8,
        narrative_summary_fr="Itachi extermine son clan en une nuit.",
        canonicity=CanonCanonicity.manga,
        cancellation_strategy=CancellationStrategy(type="hard_cancel"),
        sources=["narutopedia:Uchiha_Clan_Downfall"],
        updated_at="2026-05-03",
    )
    rumor = make_rumor_from_event(event, born_at_year=8, radius="regional")
    # 'regional' radius -> fidelity 0.8

    fact_id, spread = propagate_rumor_via_social(
        store, propagator, rumor,
        primary_witnesses=["kakashi"],  # Kakashi etait Anbu, temoin
        max_depth=3,
    )

    # Kakashi : 0.8 (fidelity rumor regional)
    assert spread["kakashi"] == pytest.approx(0.8)
    # Iruka via Kakashi : 0.8 * 0.9 * 0.7 = 0.504
    assert spread["iruka"] == pytest.approx(0.504)
    # Naruto via Iruka : 0.504 * 0.85 * 0.7 = 0.3
    assert spread["naruto"] == pytest.approx(0.504 * 0.85 * 0.7, abs=1e-3)

    # Le fact KG existe et a la bonne canonicity
    fact = store.get_fact(fact_id)
    assert fact is not None
    assert fact.canonicity == Canonicity.canon_modified
    # 4 NPCs ont des beliefs (Kakashi + Iruka + Hiruzen + Naruto)
    assert propagator.count() >= 4
