"""Tests du systeme Mission + integration KG.

Couvre :
- Mission Pydantic : champs requis, validation rank/type/outcome,
  participant_ids, has_participant, date_iso
- MissionCatalog : load JSON, by_id, by_year_range, by_rank, by_type,
  by_participant, by_arc, by_location, persistance
- Mass dataset canon : data/canonical/missions.json est valide et chargeable
- Integration KG : facts crees pour chaque mission, idempotence,
  invariants Phase C consomment les missions correctement
"""

from __future__ import annotations

from pathlib import Path

import pytest

from shinobi.kg import KnowledgeGraphStore
from shinobi.missions import (
    Mission,
    MissionCatalog,
    MissionOutcome,
    MissionParticipant,
    MissionRank,
    MissionType,
    import_missions_to_kg,
)

CANON_MISSIONS_PATH = Path(__file__).resolve().parents[2] / "data" / "canonical" / "missions.json"


# ============================================================================
# Mission Pydantic
# ============================================================================


def test_mission_minimal_required() -> None:
    m = Mission(
        id="mission_test_minimal",
        name_fr="Test",
        rank=MissionRank.c,
        type=MissionType.escort,
        year=12,
        summary_fr="Une mission de test minimale.",
    )
    assert m.id == "mission_test_minimal"
    assert m.rank == MissionRank.c
    assert m.outcome == MissionOutcome.unknown


def test_mission_id_pattern_enforced() -> None:
    """Les ids invalides (majuscules, tirets) sont rejetees."""
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Mission(
            id="Mission-Invalid",
            name_fr="x",
            rank=MissionRank.c,
            type=MissionType.escort,
            year=12,
            summary_fr="dummy summary text",
        )


def test_mission_with_full_participants() -> None:
    m = Mission(
        id="m_team",
        name_fr="Team mission",
        rank=MissionRank.b,
        type=MissionType.protection,
        year=12,
        summary_fr="Mission a plusieurs.",
        participants=[
            MissionParticipant(character_id="kakashi", role="leader"),
            MissionParticipant(character_id="naruto", role="operative"),
            MissionParticipant(character_id="sakura", role="medic"),
        ],
    )
    ids = m.participant_ids()
    assert ids == ["kakashi", "naruto", "sakura"]
    assert m.has_participant("naruto") is True
    assert m.has_participant("sasuke") is False


def test_mission_date_iso_partial() -> None:
    m1 = Mission(
        id="m1", name_fr="x", rank=MissionRank.c, type=MissionType.escort,
        year=12, summary_fr="dummy summary text",
    )
    m2 = Mission(
        id="m2", name_fr="x", rank=MissionRank.c, type=MissionType.escort,
        year=12, month=4, summary_fr="dummy summary text",
    )
    m3 = Mission(
        id="m3", name_fr="x", rank=MissionRank.c, type=MissionType.escort,
        year=12, month=4, day=15, summary_fr="dummy summary text",
    )
    assert m1.date_iso() == "+12"
    assert m2.date_iso() == "+12-04"
    assert m3.date_iso() == "+12-04-15"


def test_mission_immutable() -> None:
    from pydantic import ValidationError
    m = Mission(
        id="m_ix", name_fr="x", rank=MissionRank.c, type=MissionType.escort,
        year=10, summary_fr="dummy summary text",
    )
    with pytest.raises((ValidationError, TypeError, AttributeError)):
        m.year = 99  # type: ignore[misc]


# ============================================================================
# MissionCatalog
# ============================================================================


@pytest.fixture
def sample_catalog() -> MissionCatalog:
    return MissionCatalog([
        Mission(
            id="m_wave", name_fr="Wave", rank=MissionRank.c,
            type=MissionType.escort, year=12, canonical_arc="wave",
            location_id="wave_country",
            summary_fr="dummy summary text",
            participants=[
                MissionParticipant(character_id="naruto"),
                MissionParticipant(character_id="kakashi", role="leader"),
            ],
        ),
        Mission(
            id="m_chunin", name_fr="Chunin Exam", rank=MissionRank.b,
            type=MissionType.chunin_exam, year=12, canonical_arc="chunin_exam",
            location_id="konoha", outcome=MissionOutcome.partial_success,
            summary_fr="dummy summary text",
            participants=[
                MissionParticipant(character_id="naruto"),
                MissionParticipant(character_id="sasuke"),
            ],
        ),
        Mission(
            id="m_pain_def", name_fr="Pain Defense", rank=MissionRank.s,
            type=MissionType.protection, year=14, canonical_arc="pain_invasion",
            location_id="konoha",
            summary_fr="dummy summary text",
            participants=[MissionParticipant(character_id="naruto", role="leader")],
        ),
    ])


def test_catalog_count(sample_catalog: MissionCatalog) -> None:
    assert sample_catalog.count == 3


def test_catalog_by_id(sample_catalog: MissionCatalog) -> None:
    assert sample_catalog.by_id("m_wave") is not None
    assert sample_catalog.by_id("inexistant") is None


def test_catalog_by_year_range(sample_catalog: MissionCatalog) -> None:
    in_12 = sample_catalog.by_year_range(year_min=12, year_max=12)
    assert len(in_12) == 2
    in_14 = sample_catalog.by_year_range(year_min=13)
    assert len(in_14) == 1
    assert in_14[0].id == "m_pain_def"


def test_catalog_by_rank(sample_catalog: MissionCatalog) -> None:
    s_rank = sample_catalog.by_rank(MissionRank.s)
    assert len(s_rank) == 1
    assert s_rank[0].id == "m_pain_def"


def test_catalog_by_type(sample_catalog: MissionCatalog) -> None:
    escorts = sample_catalog.by_type(MissionType.escort)
    assert len(escorts) == 1


def test_catalog_by_participant(sample_catalog: MissionCatalog) -> None:
    naruto_missions = sample_catalog.by_participant("naruto")
    assert len(naruto_missions) == 3


def test_catalog_by_arc(sample_catalog: MissionCatalog) -> None:
    wave = sample_catalog.by_arc("wave")
    assert len(wave) == 1


def test_catalog_by_location(sample_catalog: MissionCatalog) -> None:
    konoha = sample_catalog.by_location("konoha")
    assert len(konoha) == 2


def test_catalog_duplicate_id_rejected() -> None:
    with pytest.raises(ValueError):
        MissionCatalog([
            Mission(id="m_dup", name_fr="x", rank=MissionRank.c,
                    type=MissionType.escort, year=10,
                    summary_fr="dummy summary text"),
            Mission(id="m_dup", name_fr="x", rank=MissionRank.c,
                    type=MissionType.escort, year=11,
                    summary_fr="dummy summary text"),
        ])


def test_catalog_round_trip_json(sample_catalog: MissionCatalog, tmp_path: Path) -> None:
    fp = tmp_path / "missions.json"
    n = sample_catalog.to_json_file(fp)
    assert n == 3
    loaded = MissionCatalog.from_json_file(fp)
    assert loaded.count == 3
    assert loaded.by_id("m_wave") is not None


def test_catalog_from_missing_file(tmp_path: Path) -> None:
    cat = MissionCatalog.from_json_file(tmp_path / "absent.json")
    assert cat.count == 0


# ============================================================================
# Canon dataset (data/canonical/missions.json)
# ============================================================================


def test_canon_missions_loads() -> None:
    """Le dataset canon initial est valide et chargeable."""
    if not CANON_MISSIONS_PATH.exists():
        pytest.skip("missions.json absent")
    cat = MissionCatalog.from_json_file(CANON_MISSIONS_PATH)
    assert cat.count >= 20, f"On veut au moins 20 missions, on a {cat.count}"


def test_canon_missions_have_unique_ids() -> None:
    if not CANON_MISSIONS_PATH.exists():
        pytest.skip("missions.json absent")
    cat = MissionCatalog.from_json_file(CANON_MISSIONS_PATH)
    ids = [m.id for m in cat.all()]
    assert len(ids) == len(set(ids))


def test_canon_missions_cover_main_arcs() -> None:
    """Verifie que des arcs canon majeurs sont couverts."""
    if not CANON_MISSIONS_PATH.exists():
        pytest.skip("missions.json absent")
    cat = MissionCatalog.from_json_file(CANON_MISSIONS_PATH)
    arcs = {m.canonical_arc for m in cat.all() if m.canonical_arc}
    expected_subset = {
        "wave_country", "chunin_exam_arc", "fourth_shinobi_world_war",
        "pain_invasion_arc",
    }
    assert expected_subset.issubset(arcs)


def test_canon_missions_temporal_coverage() -> None:
    """Couverture temporelle : missions de Part I (year 12) jusqu'a Boruto."""
    if not CANON_MISSIONS_PATH.exists():
        pytest.skip("missions.json absent")
    cat = MissionCatalog.from_json_file(CANON_MISSIONS_PATH)
    years = sorted({m.year for m in cat.all()})
    assert min(years) <= 12, f"Doit couvrir Part I, min year = {min(years)}"
    assert max(years) >= 16, f"Doit couvrir 4e guerre, max year = {max(years)}"


def test_canon_missions_have_participants() -> None:
    if not CANON_MISSIONS_PATH.exists():
        pytest.skip("missions.json absent")
    cat = MissionCatalog.from_json_file(CANON_MISSIONS_PATH)
    for m in cat.all():
        assert len(m.participants) > 0, f"Mission {m.id} sans participants"


def test_canon_missions_naruto_features_in_team_7_arcs() -> None:
    """Naruto doit etre participant de la mission Wave et de Pain defense."""
    if not CANON_MISSIONS_PATH.exists():
        pytest.skip("missions.json absent")
    cat = MissionCatalog.from_json_file(CANON_MISSIONS_PATH)
    wave = cat.by_id("mission_wave_country_zabuza")
    if wave:
        assert wave.has_participant("uzumaki_naruto")
    pain_def = cat.by_id("mission_pain_invasion_konoha")
    if pain_def:
        assert pain_def.has_participant("uzumaki_naruto")


# ============================================================================
# Integration KG
# ============================================================================


@pytest.fixture
def store() -> KnowledgeGraphStore:
    s = KnowledgeGraphStore(None)
    yield s
    s.close()


def test_kg_import_creates_facts(
    store: KnowledgeGraphStore, sample_catalog: MissionCatalog,
) -> None:
    stats = import_missions_to_kg(store, sample_catalog.all())
    assert stats["missions_imported"] == 3
    assert stats["facts_inserted"] > 0


def test_kg_import_creates_type_fact(
    store: KnowledgeGraphStore, sample_catalog: MissionCatalog,
) -> None:
    import_missions_to_kg(store, sample_catalog.all())
    facts = store.get_facts(subject="m_wave", relation="type")
    assert any(f.object == "mission" for f in facts)


def test_kg_import_temporal_fact(
    store: KnowledgeGraphStore, sample_catalog: MissionCatalog,
) -> None:
    import_missions_to_kg(store, sample_catalog.all())
    occurs = store.get_facts(subject="m_pain_def", relation="occurs_in_year")
    assert len(occurs) == 1
    assert occurs[0].object == "14"
    assert occurs[0].valid_from_year == 14


def test_kg_import_participant_dual_direction(
    store: KnowledgeGraphStore, sample_catalog: MissionCatalog,
) -> None:
    """Pour chaque participant, on a (mission, involves, npc) ET
    (npc, participated_in_mission, mission)."""
    import_missions_to_kg(store, sample_catalog.all())
    # mission -> involves -> naruto
    forward = store.get_facts(subject="m_wave", relation="involves",
                                object_value="naruto")
    assert len(forward) == 1
    # naruto -> participated_in_mission -> m_wave
    backward = store.get_facts(subject="naruto",
                                  relation="participated_in_mission",
                                  object_value="m_wave")
    assert len(backward) == 1


def test_kg_import_idempotent(
    store: KnowledgeGraphStore, sample_catalog: MissionCatalog,
) -> None:
    """Re-import ne double pas les facts."""
    import_missions_to_kg(store, sample_catalog.all())
    facts_before = store.count(source_prefix="mission:m_wave")
    import_missions_to_kg(store, sample_catalog.all())
    facts_after = store.count(source_prefix="mission:m_wave")
    assert facts_before == facts_after


def test_kg_import_full_canon_dataset(store: KnowledgeGraphStore) -> None:
    """Integration : import du dataset canon complet."""
    if not CANON_MISSIONS_PATH.exists():
        pytest.skip("missions.json absent")
    cat = MissionCatalog.from_json_file(CANON_MISSIONS_PATH)
    stats = import_missions_to_kg(store, cat.all())
    assert stats["missions_imported"] >= 20
    assert stats["facts_inserted"] > 100
    # Spot check : Wave country et son leader
    wave_facts = store.get_facts(subject="mission_wave_country_zabuza")
    assert len(wave_facts) > 0
    leader = store.get_facts(
        subject="hatake_kakashi",
        relation="participated_in_mission",
        object_value="mission_wave_country_zabuza",
    )
    assert len(leader) == 1


def test_kg_query_missions_by_year(
    store: KnowledgeGraphStore, sample_catalog: MissionCatalog,
) -> None:
    """On peut requeter les missions par annee via le KG."""
    import_missions_to_kg(store, sample_catalog.all())
    # Toutes les missions occurrant en l'an 12
    occurs_12 = store.get_facts(relation="occurs_in_year",
                                   object_value="12")
    mission_ids = {f.subject for f in occurs_12}
    assert "m_wave" in mission_ids
    assert "m_chunin" in mission_ids
    assert "m_pain_def" not in mission_ids
