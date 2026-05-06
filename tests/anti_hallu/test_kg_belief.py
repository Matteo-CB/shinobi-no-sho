"""Tests Phase B : Belief Propagator + SocialNetwork + bootstrap canon.

Couvre :
- SocialNetwork CRUD : add_link, get_link, neighbors, strength_between
- Filtre temporel sur les liens
- BeliefPropagator : record_witness, propagate_to, propagate_cascade
- Decay multiplicatif sur cascade BFS (depth + channel + link strength)
- belief_view_for_npc joint avec filtres
- Bootstrap social network depuis canon (clans, psycho_notes, kage_lineage)
- Bootstrap canon beliefs (chaque NPC connait les facts qui le concernent)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from shinobi.kg import (
    Belief,
    BeliefPropagator,
    Fact,
    KnowledgeGraphStore,
    SocialLink,
    SocialNetwork,
    bootstrap_canon_beliefs,
    bootstrap_social_network_from_canon,
    import_canon_to_kg,
)


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


# --- SocialNetwork ---------------------------------------------------------


def test_social_link_normalizes_pair_order(social: SocialNetwork) -> None:
    """Convention : npc_a < npc_b apres construction."""
    link = SocialLink(npc_a="zzz", npc_b="aaa", link_type="friend", strength=0.8)
    assert link.npc_a == "aaa"
    assert link.npc_b == "zzz"
    social.add_link(link)
    got = social.get_link("aaa", "zzz")
    assert got is not None
    assert got.npc_a == "aaa"
    assert got.npc_b == "zzz"


def test_social_link_get_in_either_order(social: SocialNetwork) -> None:
    social.add_link(SocialLink(
        npc_a="naruto", npc_b="iruka", link_type="mentor", strength=0.85,
    ))
    g1 = social.get_link("naruto", "iruka")
    g2 = social.get_link("iruka", "naruto")
    assert g1 is not None and g2 is not None
    assert g1.id == g2.id


def test_social_neighbors(social: SocialNetwork) -> None:
    social.add_link(SocialLink(npc_a="naruto", npc_b="iruka", strength=0.85))
    social.add_link(SocialLink(npc_a="naruto", npc_b="hiruzen", strength=0.5))
    social.add_link(SocialLink(npc_a="sasuke", npc_b="itachi", strength=0.9))
    n = social.neighbors("naruto")
    assert len(n) == 2
    others = {link.other("naruto") for link in n}
    assert others == {"iruka", "hiruzen"}


def test_social_strength_between(social: SocialNetwork) -> None:
    social.add_link(SocialLink(npc_a="naruto", npc_b="iruka", strength=0.85))
    assert social.strength_between("naruto", "iruka") == pytest.approx(0.85)
    assert social.strength_between("naruto", "stranger") == 0.0


def test_social_temporal_filter(social: SocialNetwork) -> None:
    """Lien actif de an 12 a an 16. En dehors -> pas trouve."""
    social.add_link(SocialLink(
        npc_a="naruto", npc_b="sasuke",
        strength=0.7, valid_from_year=12, valid_to_year=16,
    ))
    assert social.get_link("naruto", "sasuke", year=14) is not None
    assert social.get_link("naruto", "sasuke", year=10) is None
    assert social.get_link("naruto", "sasuke", year=20) is None


def test_social_min_strength_filter(social: SocialNetwork) -> None:
    social.add_link(SocialLink(npc_a="a", npc_b="b", strength=0.3))
    social.add_link(SocialLink(npc_a="a", npc_b="c", strength=0.8))
    n = social.neighbors("a", min_strength=0.5)
    assert len(n) == 1
    assert n[0].other("a") == "c"


# --- BeliefPropagator simple CRUD ------------------------------------------


def test_record_witness_creates_belief(
    store: KnowledgeGraphStore, propagator: BeliefPropagator,
) -> None:
    fid = store.add_fact(Fact(subject="x", relation="r", object="v"))
    propagator.record_witness("naruto", fid, year=10)
    b = propagator.get_belief(fid, "naruto")
    assert b is not None
    assert b.fidelity == 1.0
    assert b.learned_via_channel == "witness"


def test_beliefs_of_filters_min_fidelity(
    store: KnowledgeGraphStore, propagator: BeliefPropagator,
) -> None:
    fid_high = store.add_fact(Fact(subject="x", relation="r", object="v1"))
    fid_low = store.add_fact(Fact(subject="x", relation="r", object="v2"))
    propagator.add_belief(Belief(fact_id=fid_high, npc_id="naruto", fidelity=0.9))
    propagator.add_belief(Belief(fact_id=fid_low, npc_id="naruto", fidelity=0.2))
    high = propagator.beliefs_of("naruto", min_fidelity=0.5)
    assert len(high) == 1
    assert high[0].fact_id == fid_high


def test_belief_idempotent_keeps_max_fidelity(
    store: KnowledgeGraphStore, propagator: BeliefPropagator,
) -> None:
    """Si on insert deux fois, on garde la fidelity la plus haute."""
    fid = store.add_fact(Fact(subject="x", relation="r"))
    propagator.add_belief(Belief(fact_id=fid, npc_id="naruto", fidelity=0.4))
    propagator.add_belief(Belief(fact_id=fid, npc_id="naruto", fidelity=0.9))
    b = propagator.get_belief(fid, "naruto")
    assert b.fidelity == pytest.approx(0.9)
    # Inversement, un upsert avec fidelity inferieure ne baisse pas
    propagator.add_belief(Belief(fact_id=fid, npc_id="naruto", fidelity=0.1))
    b2 = propagator.get_belief(fid, "naruto")
    assert b2.fidelity == pytest.approx(0.9)


# --- Propagation par chaine -------------------------------------------------


def test_propagate_to_with_strong_link(
    store: KnowledgeGraphStore, propagator: BeliefPropagator,
) -> None:
    propagator.social.add_link(SocialLink(
        npc_a="naruto", npc_b="iruka", strength=0.85,
    ))
    fid = store.add_fact(Fact(subject="x", relation="vu_par", object="naruto"))
    propagator.record_witness("naruto", fid, year=10)
    new_belief = propagator.propagate_to(
        "naruto", "iruka", fid, year=10, channel="rumor",
    )
    assert new_belief is not None
    # 1.0 * 0.85 * 0.7 (rumor decay) = 0.595
    assert new_belief.fidelity == pytest.approx(0.85 * 0.7)
    assert new_belief.learned_via_npc_id == "naruto"


def test_propagate_to_no_link_returns_none(
    store: KnowledgeGraphStore, propagator: BeliefPropagator,
) -> None:
    fid = store.add_fact(Fact(subject="x", relation="r"))
    propagator.record_witness("naruto", fid)
    # pas de lien naruto-stranger
    assert propagator.propagate_to("naruto", "stranger", fid) is None


def test_propagate_to_below_threshold(
    store: KnowledgeGraphStore, propagator: BeliefPropagator,
) -> None:
    """Decay total trop fort -> aucun belief inscrit."""
    propagator.social.add_link(SocialLink(
        npc_a="a", npc_b="b", strength=0.1,
    ))
    fid = store.add_fact(Fact(subject="x", relation="r"))
    propagator.add_belief(Belief(fact_id=fid, npc_id="a", fidelity=0.5))
    # 0.5 * 0.1 * 0.7 = 0.035 < 0.1 threshold
    res = propagator.propagate_to("a", "b", fid, channel="rumor")
    assert res is None


def test_propagate_cascade_bfs(
    store: KnowledgeGraphStore, propagator: BeliefPropagator,
) -> None:
    """Cascade BFS depth 2 : naruto -> iruka -> hiruzen avec decay."""
    propagator.social.add_link(SocialLink(npc_a="naruto", npc_b="iruka", strength=1.0))
    propagator.social.add_link(SocialLink(npc_a="iruka", npc_b="hiruzen", strength=1.0))
    fid = store.add_fact(Fact(subject="x", relation="r"))
    spread = propagator.propagate_cascade(
        "naruto", fid, year=10, max_depth=2, channel="rumor",
    )
    assert "naruto" in spread and spread["naruto"] == 1.0
    # iruka : 1.0 * 1.0 * 0.7 = 0.7
    assert spread["iruka"] == pytest.approx(0.7)
    # hiruzen : 0.7 * 1.0 * 0.7 = 0.49
    assert spread["hiruzen"] == pytest.approx(0.49)


def test_propagate_cascade_max_depth_limit(
    store: KnowledgeGraphStore, propagator: BeliefPropagator,
) -> None:
    """Au-dela de max_depth, ne propage pas."""
    for a, b in [("n", "i"), ("i", "h"), ("h", "j"), ("j", "k")]:
        propagator.social.add_link(SocialLink(npc_a=a, npc_b=b, strength=1.0))
    fid = store.add_fact(Fact(subject="x", relation="r"))
    spread = propagator.propagate_cascade("n", fid, max_depth=2, channel="witness")
    assert "i" in spread and "h" in spread
    # avec witness (decay 1.0) + max_depth 2, j est a depth 3
    assert "j" not in spread


def test_propagate_cascade_floor(
    store: KnowledgeGraphStore, propagator: BeliefPropagator,
) -> None:
    """min_fidelity coupe la cascade."""
    for a, b in [("n", "i"), ("i", "h"), ("h", "j")]:
        propagator.social.add_link(SocialLink(npc_a=a, npc_b=b, strength=0.5))
    fid = store.add_fact(Fact(subject="x", relation="r"))
    spread = propagator.propagate_cascade(
        "n", fid, max_depth=10, channel="rumor", min_fidelity=0.3,
    )
    # depth 1 : 0.5 * 0.7 = 0.35  -> ok
    # depth 2 : 0.35 * 0.5 * 0.7 = 0.1225 < 0.3 -> coupe
    assert "i" in spread
    assert "h" not in spread


def test_add_belief_syncs_fact_known_by_npc_ids(
    store: KnowledgeGraphStore, propagator: BeliefPropagator,
) -> None:
    """Spec §5.4 'sous-KG par PNJ' : add_belief doit synchroniser
    Fact.known_by_npc_ids pour que known_to(npc) (sub-KG view) reste
    coherent avec kg_beliefs."""
    fid = store.add_fact(Fact(subject="x", relation="r", object="y"))
    propagator.add_belief(Belief(
        fact_id=fid, npc_id="naruto",
        fidelity=0.8, learned_at_year=12,
    ))
    # Verifie que Fact.known_by_npc_ids contient maintenant 'naruto'
    fact = store.get_fact(fid)
    assert fact is not None
    assert "naruto" in fact.known_by_npc_ids
    # Et known_to(naruto) retourne ce fact
    known_facts = store.known_to("naruto")
    assert any(f.id == fid for f in known_facts)


def test_propagate_cascade_syncs_known_by_for_all_targets(
    store: KnowledgeGraphStore, propagator: BeliefPropagator,
) -> None:
    """Apres cascade, tous les NPCs avec belief doivent etre dans
    Fact.known_by_npc_ids."""
    for a, b in [("witness", "sasuke"), ("sasuke", "madara")]:
        propagator.social.add_link(SocialLink(npc_a=a, npc_b=b, strength=0.9))
    fid = store.add_fact(Fact(subject="x", relation="event", object="y"))
    propagator.propagate_cascade(
        "witness", fid, year=10, max_depth=2,
        channel="rumor", min_fidelity=0.2, initial_fidelity=1.0,
    )
    fact = store.get_fact(fid)
    assert fact is not None
    # witness, sasuke, madara doivent tous etre dans known_by
    assert "witness" in fact.known_by_npc_ids
    assert "sasuke" in fact.known_by_npc_ids
    assert "madara" in fact.known_by_npc_ids


def test_push_player_action_creates_kg_fact(
    store: KnowledgeGraphStore,
) -> None:
    """Spec §5.4 use case : action joueur -> Fact KG."""
    from shinobi.cli.play import _push_player_action_to_kg
    from shinobi.engine.actions import Action
    from shinobi.types import ActionType

    action = Action(
        action_type=ActionType.fight,
        summary="combat avec Itachi",
        target_id="uchiha_itachi",
    )
    fid = _push_player_action_to_kg(
        store, character_name="naruto", action=action, result=None, year=8,
    )
    assert fid is not None
    fact = store.get_fact(fid)
    assert fact is not None
    assert fact.subject == "naruto"
    assert fact.relation == "fought"
    assert fact.object == "uchiha_itachi"
    assert fact.valid_from_year == 8
    assert fact.source.startswith("player_action:")
    # Le joueur connait son propre acte
    assert "naruto" in fact.known_by_npc_ids


def test_push_player_action_private_returns_none(
    store: KnowledgeGraphStore,
) -> None:
    """Actions privees (rest, train, meditate) ne creent pas de fact public."""
    from shinobi.cli.play import _push_player_action_to_kg
    from shinobi.engine.actions import Action
    from shinobi.types import ActionType

    for atype in (ActionType.rest, ActionType.train_stat, ActionType.meditate):
        action = Action(action_type=atype, summary="...")
        fid = _push_player_action_to_kg(
            store, character_name="naruto",
            action=action, result=None, year=8,
        )
        assert fid is None


def test_notable_player_actions_includes_social_scandals(self=None) -> None:
    """Spec §5.4 : actions sociales (seduce/bribe/intimidate) sont notables."""
    from shinobi.cli.play import _NOTABLE_PLAYER_ACTIONS

    for atype in ("seduce", "bribe", "intimidate", "fight", "challenge"):
        assert atype in _NOTABLE_PLAYER_ACTIONS, (
            f"{atype} devrait etre notable (genere rumeur)"
        )


def test_propagate_cascade_temporal_offset(
    store: KnowledgeGraphStore, propagator: BeliefPropagator,
) -> None:
    """Spec §5.4 : 'Sasuke year 9, Madara year 12, Pain year 14'.

    Avec year_offset_per_hop > 0, chaque hop incremente learned_at_year.
    Modelise la propagation temporelle (vs immediate cascade)."""
    for a, b in [
        ("witness", "sasuke"),
        ("sasuke", "madara"),
        ("madara", "pain"),
    ]:
        propagator.social.add_link(SocialLink(npc_a=a, npc_b=b, strength=0.9))
    fid = store.add_fact(Fact(subject="x", relation="saved", object="itachi"))
    propagator.propagate_cascade(
        "witness", fid,
        year=8, max_depth=3,
        channel="rumor", min_fidelity=0.2,
        initial_fidelity=1.0,
        year_offset_per_hop=2,  # Each hop adds 2 years
    )
    # Recupere les beliefs et verifie learned_at_year
    rows = store.conn.execute(
        "SELECT npc_id, learned_at_year FROM kg_beliefs",
    ).fetchall()
    learned_by = {r["npc_id"]: r["learned_at_year"] for r in rows}
    # witness : year 8 (depth 0)
    # sasuke : year 8 + 2 = 10 (depth 1)
    # madara : year 8 + 4 = 12 (depth 2)
    # pain : year 8 + 6 = 14 (depth 3)
    assert learned_by.get("sasuke") == 10
    assert learned_by.get("madara") == 12
    assert learned_by.get("pain") == 14


def test_propagate_cascade_default_no_temporal_offset(
    store: KnowledgeGraphStore, propagator: BeliefPropagator,
) -> None:
    """Default (year_offset_per_hop=0) : tous les hops au meme year (back-compat)."""
    for a, b in [("a", "b"), ("b", "c"), ("c", "d")]:
        propagator.social.add_link(SocialLink(npc_a=a, npc_b=b, strength=0.9))
    fid = store.add_fact(Fact(subject="x", relation="r"))
    propagator.propagate_cascade(
        "a", fid, year=10, max_depth=3,
        channel="rumor", min_fidelity=0.1,
        # year_offset_per_hop default = 0
    )
    rows = store.conn.execute(
        "SELECT npc_id, learned_at_year FROM kg_beliefs",
    ).fetchall()
    learned_by = {r["npc_id"]: r["learned_at_year"] for r in rows}
    # Tous au meme year (back-compat)
    assert learned_by.get("b") == 10
    assert learned_by.get("c") == 10
    assert learned_by.get("d") == 10


# --- Belief view ------------------------------------------------------------


def test_belief_view_for_npc_joins_facts(
    store: KnowledgeGraphStore, propagator: BeliefPropagator,
) -> None:
    fid1 = store.add_fact(Fact(
        subject="naruto", relation="alive", object="true",
        valid_from_year=0,
    ))
    fid2 = store.add_fact(Fact(
        subject="hiruzen", relation="alive", object="true",
        valid_from_year=-69, valid_to_year=12,
    ))
    propagator.add_belief(Belief(fact_id=fid1, npc_id="kakashi", fidelity=0.95))
    propagator.add_belief(Belief(fact_id=fid2, npc_id="kakashi", fidelity=0.85))
    # En l'an 5 : Hiruzen est encore en vie, Naruto aussi
    view = propagator.belief_view_for_npc("kakashi", year=5)
    assert len(view) == 2
    # En l'an 13 : Hiruzen mort, son fact n'est plus actif
    view13 = propagator.belief_view_for_npc("kakashi", year=13)
    subjects = {row[1] for row in view13}
    assert "hiruzen" not in subjects
    assert "naruto" in subjects


def test_belief_view_min_fidelity(
    store: KnowledgeGraphStore, propagator: BeliefPropagator,
) -> None:
    fid_h = store.add_fact(Fact(subject="x", relation="r"))
    fid_l = store.add_fact(Fact(subject="x", relation="r", object="other"))
    propagator.add_belief(Belief(fact_id=fid_h, npc_id="x", fidelity=0.9))
    propagator.add_belief(Belief(fact_id=fid_l, npc_id="x", fidelity=0.2))
    high = propagator.belief_view_for_npc("x", min_fidelity=0.5)
    assert len(high) == 1


# --- Bootstrap canon --------------------------------------------------------


@pytest.fixture
def canon_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "canonical"


def test_bootstrap_social_network(
    store: KnowledgeGraphStore, canon_dir: Path,
) -> None:
    """Verifie que le bootstrap cree des liens depuis canon."""
    if not canon_dir.exists():
        pytest.skip("data/canonical/ absent")
    stats = bootstrap_social_network_from_canon(store, canon_dir)
    # On doit avoir au moins quelques liens (clans + psycho)
    assert stats["total"] > 0
    # psycho_notes cible specifiquement Naruto -> Iruka
    social = SocialNetwork(store.conn)
    naruto_iruka = social.get_link("uzumaki_naruto", "umino_iruka")
    # Si psycho_notes contient cette relation -> trouvee
    if naruto_iruka:
        assert naruto_iruka.link_type in ("mentor", "student")


def test_bootstrap_canon_beliefs_after_import(
    store: KnowledgeGraphStore, canon_dir: Path,
) -> None:
    """Les NPCs canon doivent connaitre les facts qui les mentionnent."""
    if not canon_dir.exists():
        pytest.skip("data/canonical/ absent")
    import_canon_to_kg(store, canon_dir)
    stats = bootstrap_canon_beliefs(store)
    # Au moins quelques beliefs crees
    assert stats["beliefs_inserted"] > 0
    propagator = BeliefPropagator(store.conn)
    # Naruto doit connaitre au moins 1 fact qui le concerne directement
    naruto_beliefs = propagator.beliefs_of("uzumaki_naruto")
    assert len(naruto_beliefs) > 0


# --- Channel decay coverage ------------------------------------------------


def test_witness_channel_no_decay(
    store: KnowledgeGraphStore, propagator: BeliefPropagator,
) -> None:
    propagator.social.add_link(SocialLink(npc_a="a", npc_b="b", strength=1.0))
    fid = store.add_fact(Fact(subject="x", relation="r"))
    propagator.record_witness("a", fid)
    new_b = propagator.propagate_to("a", "b", fid, channel="witness")
    assert new_b is not None
    # 1.0 * 1.0 * 1.0 (witness pas de decay) = 1.0
    assert new_b.fidelity == pytest.approx(1.0)


def test_spy_channel_moderate_decay(
    store: KnowledgeGraphStore, propagator: BeliefPropagator,
) -> None:
    propagator.social.add_link(SocialLink(npc_a="a", npc_b="b", strength=1.0))
    fid = store.add_fact(Fact(subject="x", relation="r"))
    propagator.record_witness("a", fid)
    new_b = propagator.propagate_to("a", "b", fid, channel="spy")
    # 1.0 * 1.0 * 0.85 = 0.85
    assert new_b.fidelity == pytest.approx(0.85)
