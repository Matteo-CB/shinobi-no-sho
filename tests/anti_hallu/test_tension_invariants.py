"""Tests des 20 invariants deterministes du Tension Detector (Phase C).

Pour chaque invariant : au moins 1 cas positif (tension detectee) + 1 cas
negatif (pas de tension). Total : 20 invariants x 2 = 40+ tests.

Approche : on construit un KG minimaliste pour chaque scenario via le
KnowledgeGraphStore in-memory, on verifie le retour de l'invariant.
"""

from __future__ import annotations

import pytest

from shinobi.kg import (
    Canonicity,
    Fact,
    KnowledgeGraphStore,
    ObjectType,
)
from shinobi.tension.invariants import (
    border_dispute,
    chekhovs_gun_unfired,
    clan_extinction_threat,
    cursed_hatred_rising,
    death_anniversary,
    forbidden_jutsu_threat,
    geographic_imbalance,
    hidden_truth_about_to_surface,
    jinchuuriki_unprotected,
    kage_absent_or_dead,
    kekkei_genkai_carrier_isolated,
    lone_survivor_obsessed,
    obsessive_npc_idle,
    power_vacuum_global,
    prophecy_unfulfilled,
    student_surpasses_master,
    tailed_beast_uncontrolled,
    unresolved_blood_ties,
    wartime_alliance_unstable,
    wronged_faction_unrevenged,
)
from shinobi.tension.types import TensionSeverity, TensionType


@pytest.fixture
def store() -> KnowledgeGraphStore:
    s = KnowledgeGraphStore(None)
    yield s
    s.close()


def add(s: KnowledgeGraphStore, **kwargs) -> int:
    """Helper : insert un Fact avec defaults sains."""
    kwargs.setdefault("source", "canon")
    kwargs.setdefault("canonicity", Canonicity.canon_strict)
    kwargs.setdefault("object_type", ObjectType.value)
    return s.add_fact(Fact(**kwargs))


# === 1. kage_absent_or_dead =================================================


def test_kage_absent_great_village(store: KnowledgeGraphStore) -> None:
    """Aucun kage en place a Konoha -> tension critical."""
    out = kage_absent_or_dead(store, year=12, ctx={
        "great_villages": ["konohagakure"],
    })
    assert len(out) == 1
    assert out[0].type == TensionType.power_vacuum
    assert out[0].severity == TensionSeverity.critical


def test_kage_in_place_no_tension(store: KnowledgeGraphStore) -> None:
    add(store, subject="konohagakure", relation="kage", object="tsunade",
        object_type=ObjectType.entity, valid_from_year=12)
    add(store, subject="tsunade", relation="death_year", object="100")
    out = kage_absent_or_dead(store, year=14, ctx={
        "great_villages": ["konohagakure"],
    })
    assert out == []


def test_kage_dead_creates_high_tension(store: KnowledgeGraphStore) -> None:
    add(store, subject="konohagakure", relation="kage", object="hiruzen",
        object_type=ObjectType.entity, valid_from_year=0)
    add(store, subject="hiruzen", relation="death_year", object="12")
    out = kage_absent_or_dead(store, year=15, ctx={
        "great_villages": ["konohagakure"],
    })
    assert len(out) == 1
    assert out[0].severity == TensionSeverity.high


# === 2. jinchuuriki_unprotected =============================================


def test_jinchuriki_in_village_without_kage(store: KnowledgeGraphStore) -> None:
    add(store, subject="kurama", relation="type", object="tailed_beast")
    add(store, subject="kurama", relation="current_jinchuriki", object="naruto",
        object_type=ObjectType.entity)
    add(store, subject="naruto", relation="village_of_origin", object="konohagakure",
        object_type=ObjectType.entity)
    # Pas de fact 'kage' pour Konoha -> village sans kage
    out = jinchuuriki_unprotected(store, year=14, ctx={})
    assert len(out) == 1
    assert out[0].type == TensionType.jinchuuriki_unprotected


def test_jinchuriki_protected_no_tension(store: KnowledgeGraphStore) -> None:
    add(store, subject="kurama", relation="type", object="tailed_beast")
    add(store, subject="kurama", relation="current_jinchuriki", object="naruto",
        object_type=ObjectType.entity)
    add(store, subject="naruto", relation="village_of_origin", object="konohagakure",
        object_type=ObjectType.entity)
    add(store, subject="konohagakure", relation="kage", object="tsunade",
        object_type=ObjectType.entity, valid_from_year=12)
    out = jinchuuriki_unprotected(store, year=14, ctx={})
    assert out == []


# === 3. obsessive_npc_idle ==================================================


def test_obsessive_npc_idle_detected(store: KnowledgeGraphStore) -> None:
    add(store, subject="sasuke", relation="deep_motivation", object="revenge_against_itachi")
    # Pas de last_action_year recent
    out = obsessive_npc_idle(store, year=12, ctx={})
    assert len(out) == 1
    assert out[0].type == TensionType.obsessive_npc_idle


def test_obsessive_npc_recent_action_no_tension(store: KnowledgeGraphStore) -> None:
    add(store, subject="sasuke", relation="deep_motivation", object="revenge")
    add(store, subject="sasuke", relation="last_action_year", object="11")
    out = obsessive_npc_idle(store, year=12, ctx={})
    assert out == []


# === 4. wronged_faction_unrevenged ==========================================


def test_wronged_faction_no_revenge(store: KnowledgeGraphStore) -> None:
    add(store, subject="uzumaki_clan", relation="wronged_by", object="kumogakure",
        object_type=ObjectType.entity)
    out = wronged_faction_unrevenged(store, year=10, ctx={})
    assert len(out) == 1
    assert out[0].severity == TensionSeverity.high


def test_wronged_faction_revenge_completed(store: KnowledgeGraphStore) -> None:
    add(store, subject="uzumaki_clan", relation="wronged_by", object="kumogakure",
        object_type=ObjectType.entity)
    add(store, subject="uzumaki_clan", relation="revenge_completed",
        object="true", valid_from_year=8)
    out = wronged_faction_unrevenged(store, year=10, ctx={})
    assert out == []


# === 5. power_vacuum_global =================================================


def test_no_world_authority_creates_vacuum(store: KnowledgeGraphStore) -> None:
    out = power_vacuum_global(store, year=14, ctx={})
    assert len(out) == 1


def test_world_authority_present_no_vacuum(store: KnowledgeGraphStore) -> None:
    add(store, subject="naruto", relation="world_authority", object="hokage")
    out = power_vacuum_global(store, year=20, ctx={})
    assert out == []


# === 6. unresolved_blood_ties ===============================================


def test_blood_oath_unresolved(store: KnowledgeGraphStore) -> None:
    add(store, subject="madara", relation="blood_oath_with", object="hashirama",
        object_type=ObjectType.entity)
    # Les deux vivants
    out = unresolved_blood_ties(store, year=10, ctx={})
    assert len(out) >= 1


def test_blood_oath_resolved(store: KnowledgeGraphStore) -> None:
    add(store, subject="madara", relation="blood_oath_with", object="hashirama",
        object_type=ObjectType.entity)
    add(store, subject="madara", relation="blood_oath_with_resolved",
        object="hashirama", object_type=ObjectType.entity, valid_from_year=8)
    out = unresolved_blood_ties(store, year=10, ctx={})
    assert out == []


# === 7. clan_extinction_threat ==============================================


def test_clan_with_two_members(store: KnowledgeGraphStore) -> None:
    add(store, subject="uchiha", relation="type", object="clan")
    add(store, subject="sasuke", relation="clan", object="uchiha",
        object_type=ObjectType.entity)
    add(store, subject="itachi", relation="clan", object="uchiha",
        object_type=ObjectType.entity)
    out = clan_extinction_threat(store, year=8, ctx={})
    assert len(out) == 1
    assert out[0].severity == TensionSeverity.medium


def test_clan_with_one_member(store: KnowledgeGraphStore) -> None:
    add(store, subject="uchiha", relation="type", object="clan")
    add(store, subject="sasuke", relation="clan", object="uchiha",
        object_type=ObjectType.entity)
    out = clan_extinction_threat(store, year=8, ctx={})
    assert len(out) == 1
    assert out[0].severity == TensionSeverity.high


def test_clan_with_many_members_no_tension(store: KnowledgeGraphStore) -> None:
    add(store, subject="hyuga", relation="type", object="clan")
    for i in range(10):
        add(store, subject=f"member_{i}", relation="clan", object="hyuga",
            object_type=ObjectType.entity)
    out = clan_extinction_threat(store, year=10, ctx={})
    assert out == []


# === 8. tailed_beast_uncontrolled ===========================================


def test_bijuu_without_jinchuriki(store: KnowledgeGraphStore) -> None:
    add(store, subject="kurama", relation="type", object="tailed_beast")
    out = tailed_beast_uncontrolled(store, year=14, ctx={})
    assert len(out) == 1


def test_bijuu_with_jinchuriki_no_tension(store: KnowledgeGraphStore) -> None:
    add(store, subject="kurama", relation="type", object="tailed_beast")
    add(store, subject="kurama", relation="current_jinchuriki", object="naruto",
        object_type=ObjectType.entity)
    out = tailed_beast_uncontrolled(store, year=14, ctx={})
    assert out == []


# === 9. wartime_alliance_unstable ===========================================


def test_alliance_low_resource_share(store: KnowledgeGraphStore) -> None:
    add(store, subject="suna", relation="allied_with", object="oto",
        object_type=ObjectType.entity, valid_from_year=11)
    add(store, subject="suna", relation="alliance_resource_share_with_oto",
        object="0.2", valid_from_year=11)
    out = wartime_alliance_unstable(store, year=12, ctx={})
    assert len(out) == 1


def test_alliance_strong_resource_share(store: KnowledgeGraphStore) -> None:
    add(store, subject="konoha", relation="allied_with", object="suna",
        object_type=ObjectType.entity, valid_from_year=14)
    add(store, subject="konoha", relation="alliance_resource_share_with_suna",
        object="0.85", valid_from_year=14)
    out = wartime_alliance_unstable(store, year=15, ctx={})
    assert out == []


# === 10. hidden_truth_about_to_surface ======================================


def test_secret_known_by_many(store: KnowledgeGraphStore) -> None:
    fact = Fact(
        subject="itachi", relation="hidden_secret",
        object="ordered_to_kill_clan_by_konoha",
        known_by_npc_ids=["danzo", "hiruzen", "itachi", "obito"],
    )
    store.add_fact(fact)
    out = hidden_truth_about_to_surface(store, year=10, ctx={})
    assert len(out) == 1


def test_secret_few_holders_no_tension(store: KnowledgeGraphStore) -> None:
    fact = Fact(
        subject="itachi", relation="hidden_secret",
        object="ordered_to_kill_clan",
        known_by_npc_ids=["danzo"],  # 1 seul, sous threshold 3
    )
    store.add_fact(fact)
    out = hidden_truth_about_to_surface(store, year=10, ctx={})
    assert out == []


# === 11. death_anniversary ==================================================


def test_death_anniversary_5_years(store: KnowledgeGraphStore) -> None:
    add(store, subject="minato", relation="death_year", object="0")
    out = death_anniversary(store, year=5, ctx={})
    assert len(out) == 1


def test_death_anniversary_no_match(store: KnowledgeGraphStore) -> None:
    add(store, subject="minato", relation="death_year", object="0")
    out = death_anniversary(store, year=3, ctx={})
    assert out == []


# === 12. geographic_imbalance ===============================================


def test_geographic_imbalance_konoha_dominant(store: KnowledgeGraphStore) -> None:
    add(store, subject="konohagakure", relation="type", object="village")
    add(store, subject="amegakure", relation="type", object="village")
    for i in range(10):
        add(store, subject=f"konoha_n_{i}", relation="village_of_origin",
            object="konohagakure", object_type=ObjectType.entity)
    add(store, subject="ame_n", relation="village_of_origin",
        object="amegakure", object_type=ObjectType.entity)
    out = geographic_imbalance(store, year=12, ctx={
        "great_villages": ["konohagakure", "amegakure"],
    })
    assert len(out) >= 1


def test_geographic_balance_no_tension(store: KnowledgeGraphStore) -> None:
    for i in range(5):
        add(store, subject=f"konoha_{i}", relation="village_of_origin",
            object="konohagakure", object_type=ObjectType.entity)
    for i in range(5):
        add(store, subject=f"suna_{i}", relation="village_of_origin",
            object="sunagakure", object_type=ObjectType.entity)
    out = geographic_imbalance(store, year=12, ctx={
        "great_villages": ["konohagakure", "sunagakure"],
    })
    assert out == []


# === 13. student_surpasses_master ===========================================


def test_student_above_master(store: KnowledgeGraphStore) -> None:
    add(store, subject="naruto", relation="student_of", object="jiraiya",
        object_type=ObjectType.entity)
    add(store, subject="naruto", relation="power_level", object="9.5")
    add(store, subject="jiraiya", relation="power_level", object="7.0")
    out = student_surpasses_master(store, year=15, ctx={})
    assert len(out) == 1


def test_student_below_master_no_tension(store: KnowledgeGraphStore) -> None:
    add(store, subject="naruto", relation="student_of", object="jiraiya",
        object_type=ObjectType.entity)
    add(store, subject="naruto", relation="power_level", object="3.0")
    add(store, subject="jiraiya", relation="power_level", object="9.0")
    out = student_surpasses_master(store, year=12, ctx={})
    assert out == []


# === 14. prophecy_unfulfilled ===============================================


def test_prophecy_past_deadline(store: KnowledgeGraphStore) -> None:
    add(store, subject="prophecy_child_of_prophecy", relation="type", object="prophecy")
    add(store, subject="prophecy_child_of_prophecy", relation="deadline_year", object="15")
    out = prophecy_unfulfilled(store, year=18, ctx={})
    assert len(out) == 1


def test_prophecy_fulfilled(store: KnowledgeGraphStore) -> None:
    add(store, subject="prophecy_x", relation="type", object="prophecy")
    add(store, subject="prophecy_x", relation="deadline_year", object="15")
    add(store, subject="prophecy_x", relation="fulfilled", object="true")
    out = prophecy_unfulfilled(store, year=18, ctx={})
    assert out == []


# === 15. cursed_hatred_rising ===============================================


def test_cursed_hatred_three_traumas(store: KnowledgeGraphStore) -> None:
    for i in range(3):
        add(store, subject="sasuke", relation="trauma_event", object=f"trauma_{i}",
            valid_from_year=8 + i)
    out = cursed_hatred_rising(store, year=12, ctx={})
    assert len(out) == 1


def test_cursed_hatred_with_reconciliation(store: KnowledgeGraphStore) -> None:
    for i in range(3):
        add(store, subject="sasuke", relation="trauma_event", object=f"trauma_{i}",
            valid_from_year=8)
    add(store, subject="sasuke", relation="reconciliation", object="naruto",
        valid_from_year=15)
    out = cursed_hatred_rising(store, year=16, ctx={})
    assert out == []


# === 16. kekkei_genkai_carrier_isolated =====================================


def test_kekkei_one_carrier_isolated(store: KnowledgeGraphStore) -> None:
    add(store, subject="hyouton", relation="type", object="kekkei_genkai")
    add(store, subject="haku", relation="has_kekkei_genkai", object="hyouton",
        object_type=ObjectType.entity)
    out = kekkei_genkai_carrier_isolated(store, year=10, ctx={})
    assert len(out) == 1


def test_kekkei_multiple_carriers_no_tension(store: KnowledgeGraphStore) -> None:
    add(store, subject="sharingan", relation="type", object="kekkei_genkai")
    for npc in ("itachi", "sasuke", "kakashi"):
        add(store, subject=npc, relation="has_kekkei_genkai", object="sharingan",
            object_type=ObjectType.entity)
    out = kekkei_genkai_carrier_isolated(store, year=12, ctx={})
    assert out == []


# === 17. forbidden_jutsu_threat =============================================


def test_forbidden_jutsu_alive_user(store: KnowledgeGraphStore) -> None:
    add(store, subject="edo_tensei", relation="rank", object="forbidden")
    add(store, subject="edo_tensei", relation="has_canonical_user", object="orochimaru",
        object_type=ObjectType.entity)
    # Orochimaru pas mort
    out = forbidden_jutsu_threat(store, year=12, ctx={})
    assert len(out) == 1


def test_forbidden_jutsu_dead_user_no_tension(store: KnowledgeGraphStore) -> None:
    add(store, subject="kuchiyose_dead", relation="rank", object="forbidden")
    add(store, subject="kuchiyose_dead", relation="has_canonical_user",
        object="orochimaru", object_type=ObjectType.entity)
    add(store, subject="orochimaru", relation="death_year", object="14")
    out = forbidden_jutsu_threat(store, year=15, ctx={})
    assert out == []


# === 18. lone_survivor_obsessed =============================================


def test_lone_survivor_with_revenge(store: KnowledgeGraphStore) -> None:
    add(store, subject="sasuke", relation="lone_survivor_of", object="uchiha",
        object_type=ObjectType.entity)
    add(store, subject="sasuke", relation="deep_motivation",
        object="revenge_against_itachi")
    out = lone_survivor_obsessed(store, year=12, ctx={})
    assert len(out) == 1
    assert out[0].severity == TensionSeverity.critical


def test_lone_survivor_without_revenge(store: KnowledgeGraphStore) -> None:
    add(store, subject="sasuke", relation="lone_survivor_of", object="uchiha",
        object_type=ObjectType.entity)
    add(store, subject="sasuke", relation="deep_motivation", object="rebuild_clan")
    out = lone_survivor_obsessed(store, year=12, ctx={})
    assert out == []


# === 19. border_dispute =====================================================


def test_border_dispute_active(store: KnowledgeGraphStore) -> None:
    add(store, subject="konohagakure", relation="border_dispute_with",
        object="kusagakure", object_type=ObjectType.entity, valid_from_year=10)
    out = border_dispute(store, year=11, ctx={})
    assert len(out) == 1


def test_border_dispute_resolved_no_tension(store: KnowledgeGraphStore) -> None:
    add(store, subject="konoha", relation="border_dispute_with",
        object="kusa", object_type=ObjectType.entity,
        valid_from_year=5, valid_to_year=8)
    out = border_dispute(store, year=12, ctx={})
    assert out == []


# === 20. chekhovs_gun_unfired ===============================================


def test_chekhovs_gun_introduced_no_payoff(store: KnowledgeGraphStore) -> None:
    add(store, subject="curse_seal_orochimaru", relation="chekhovs_gun",
        object="seal_left_on_sasuke_must_be_resolved", valid_from_year=12)
    out = chekhovs_gun_unfired(store, year=15, ctx={})
    assert len(out) == 1


def test_chekhovs_gun_fired_no_tension(store: KnowledgeGraphStore) -> None:
    add(store, subject="curse_seal", relation="chekhovs_gun",
        object="seal_must_be_resolved", valid_from_year=12)
    add(store, subject="curse_seal", relation="chekhovs_gun_fired", object="true")
    out = chekhovs_gun_unfired(store, year=15, ctx={})
    assert out == []


def test_chekhovs_gun_too_recent_no_tension(store: KnowledgeGraphStore) -> None:
    add(store, subject="curse_seal", relation="chekhovs_gun",
        object="seal_must_be_resolved", valid_from_year=14)
    out = chekhovs_gun_unfired(store, year=15, ctx={})
    assert out == []  # < 2 ans depuis introduction
