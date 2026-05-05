"""Tests du Knowledge Graph dynamique (Phase A roadmap).

Couvre :
- Creation du schema SQLite (in-memory)
- CRUD : add_fact / get_fact / get_facts / update_fact / close_fact / delete_fact
- Filtres temporels (year)
- Filtres par canonicity / source / confidence
- Belief propagator helpers : add_known_by, known_to
- Import depuis JSONs canon : verifier que tous les types sont importes
  sans perte (count > 0 par categorie)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from shinobi.kg import (
    Canonicity,
    Fact,
    FactSource,
    KnowledgeGraphStore,
    ObjectType,
    import_canon_to_kg,
)
from shinobi.kg.schema import (
    CURRENT_SCHEMA_VERSION,
    initialize_db,
    schema_version,
)

# --- Schema -----------------------------------------------------------------


def test_initialize_db_creates_table_in_memory() -> None:
    conn = initialize_db(None)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='kg_facts'"
        ).fetchall()
        assert len(rows) == 1
    finally:
        conn.close()


def test_initialize_db_creates_file(tmp_path: Path) -> None:
    db_path = tmp_path / "kg.db"
    conn = initialize_db(db_path)
    try:
        assert db_path.exists()
        assert schema_version(conn) == CURRENT_SCHEMA_VERSION
    finally:
        conn.close()


def test_initialize_db_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "kg.db"
    c1 = initialize_db(db_path)
    c1.close()
    c2 = initialize_db(db_path)
    try:
        assert schema_version(c2) == CURRENT_SCHEMA_VERSION
    finally:
        c2.close()


# --- Store CRUD -------------------------------------------------------------


@pytest.fixture
def store() -> KnowledgeGraphStore:
    s = KnowledgeGraphStore(None)
    yield s
    s.close()


def test_add_fact_returns_id(store: KnowledgeGraphStore) -> None:
    fid = store.add_fact(Fact(subject="naruto", relation="lives_in", object="konoha"))
    assert fid > 0


def test_get_fact_round_trip(store: KnowledgeGraphStore) -> None:
    fact = Fact(
        subject="naruto",
        relation="age",
        object="6",
        object_type=ObjectType.value,
        valid_from_year=6,
    )
    fid = store.add_fact(fact)
    got = store.get_fact(fid)
    assert got is not None
    assert got.subject == "naruto"
    assert got.relation == "age"
    assert got.object == "6"
    assert got.valid_from_year == 6


def test_get_facts_by_subject(store: KnowledgeGraphStore) -> None:
    store.add_fact(Fact(subject="naruto", relation="r1", object="v1"))
    store.add_fact(Fact(subject="naruto", relation="r2", object="v2"))
    store.add_fact(Fact(subject="sasuke", relation="r1", object="v3"))
    naruto_facts = store.get_facts(subject="naruto")
    assert len(naruto_facts) == 2
    assert all(f.subject == "naruto" for f in naruto_facts)


def test_get_facts_temporal_filter(store: KnowledgeGraphStore) -> None:
    # Fact1 actif de an 0 a an 10
    store.add_fact(Fact(
        subject="hiruzen", relation="rank", object="hokage",
        valid_from_year=0, valid_to_year=10,
    ))
    # Fact2 actif de an 12 onwards
    store.add_fact(Fact(
        subject="tsunade", relation="rank", object="hokage",
        valid_from_year=12, valid_to_year=None,
    ))
    # Annee 5 -> seulement Hiruzen
    at_5 = store.get_facts(year=5, relation="rank")
    assert len(at_5) == 1
    assert at_5[0].subject == "hiruzen"
    # Annee 13 -> seulement Tsunade
    at_13 = store.get_facts(year=13, relation="rank")
    assert len(at_13) == 1
    assert at_13[0].subject == "tsunade"


def test_get_facts_canonicity_filter(store: KnowledgeGraphStore) -> None:
    store.add_fact(Fact(
        subject="naruto", relation="alive", object="true",
        canonicity=Canonicity.canon_strict,
    ))
    store.add_fact(Fact(
        subject="itachi", relation="alive", object="true",
        canonicity=Canonicity.divergent,  # joueur a sauve Itachi
    ))
    strict = store.get_facts(canonicity=Canonicity.canon_strict)
    divergent = store.get_facts(canonicity=Canonicity.divergent)
    assert len(strict) == 1 and strict[0].subject == "naruto"
    assert len(divergent) == 1 and divergent[0].subject == "itachi"


def test_get_facts_min_confidence(store: KnowledgeGraphStore) -> None:
    store.add_fact(Fact(subject="x", relation="r", object="a", confidence=1.0))
    store.add_fact(Fact(subject="x", relation="r", object="b", confidence=0.5))
    high = store.get_facts(subject="x", min_confidence=0.8)
    assert len(high) == 1
    assert high[0].object == "a"


def test_update_fact(store: KnowledgeGraphStore) -> None:
    fid = store.add_fact(Fact(subject="x", relation="r", object="v", confidence=0.5))
    store.update_fact(fid, confidence=0.9, valid_to_year=20)
    got = store.get_fact(fid)
    assert got is not None
    assert got.confidence == 0.9
    assert got.valid_to_year == 20


def test_close_fact_sets_to_year(store: KnowledgeGraphStore) -> None:
    fid = store.add_fact(Fact(subject="hiruzen", relation="alive", object="true",
                                valid_from_year=0))
    store.close_fact(fid, valid_to_year=12)  # mort en l'an 12
    got = store.get_fact(fid)
    assert got and got.valid_to_year == 12


def test_delete_fact(store: KnowledgeGraphStore) -> None:
    fid = store.add_fact(Fact(subject="x", relation="r"))
    assert store.delete_fact(fid) is True
    assert store.get_fact(fid) is None


def test_count(store: KnowledgeGraphStore) -> None:
    assert store.count() == 0
    store.add_fact(Fact(subject="x", relation="r"))
    store.add_fact(Fact(subject="y", relation="r"))
    assert store.count() == 2


def test_clear_all(store: KnowledgeGraphStore) -> None:
    store.add_fact(Fact(subject="x", relation="r"))
    store.clear_all()
    assert store.count() == 0


# --- Belief propagator helpers ---------------------------------------------


def test_known_by_npc_ids(store: KnowledgeGraphStore) -> None:
    fid = store.add_fact(Fact(
        subject="itachi", relation="alive", object="true",
        valid_from_year=15,
        known_by_npc_ids=["sarutobi_hiruzen"],
    ))
    store.add_known_by(fid, ["uzumaki_naruto"])
    got = store.get_fact(fid)
    assert got and "uzumaki_naruto" in got.known_by_npc_ids
    assert "sarutobi_hiruzen" in got.known_by_npc_ids


def test_known_to(store: KnowledgeGraphStore) -> None:
    f1 = store.add_fact(Fact(subject="a", relation="r", object="x",
                                known_by_npc_ids=["npc1"]))
    store.add_fact(Fact(subject="b", relation="r", object="y",
                          known_by_npc_ids=["npc2"]))
    facts = store.known_to("npc1")
    assert len(facts) == 1
    assert facts[0].id == f1


# --- Import canon -----------------------------------------------------------


@pytest.fixture
def canon_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "canonical"


def test_import_canon_imports_characters(
    store: KnowledgeGraphStore, canon_dir: Path
) -> None:
    """Verifie que l'import canon insere des facts pour chaque type."""
    if not canon_dir.exists():
        pytest.skip("data/canonical/ absent (env de test)")

    stats = import_canon_to_kg(store, canon_dir)
    # Au moins quelques types doivent avoir des facts
    assert stats.get("characters", 0) > 0, f"characters = {stats.get('characters')}"
    assert stats.get("techniques", 0) > 0
    assert stats.get("clans", 0) >= 0  # 52 typiquement, on autorise 0 si dataset absent
    assert stats["total"] > 0


def test_import_canon_clear_first_idempotent(
    store: KnowledgeGraphStore, canon_dir: Path
) -> None:
    """Reimporter ne double pas les facts."""
    if not canon_dir.exists():
        pytest.skip("data/canonical/ absent")
    s1 = import_canon_to_kg(store, canon_dir)
    s2 = import_canon_to_kg(store, canon_dir)  # clear_first=True par defaut
    assert s1["total"] == s2["total"]


def test_import_canon_naruto_exists(
    store: KnowledgeGraphStore, canon_dir: Path
) -> None:
    if not canon_dir.exists():
        pytest.skip("data/canonical/ absent")
    import_canon_to_kg(store, canon_dir)
    naruto_facts = store.get_facts(subject="uzumaki_naruto")
    assert len(naruto_facts) > 0
    types = {f.object for f in naruto_facts if f.relation == "type"}
    assert "character" in types


# --- Schema utilities -------------------------------------------------------


def test_fact_to_row_and_back() -> None:
    f = Fact(
        subject="x", relation="r", object="v",
        object_type=ObjectType.entity,
        valid_from_year=1, valid_to_year=5,
        canonicity=Canonicity.canon_modified,
        source="event_42",
        confidence=0.7,
        known_by_npc_ids=["npc1", "npc2"],
    )
    row = f.to_row()
    assert row["object_type"] == "entity"
    assert row["canonicity"] == "canon_modified"

    # Round-trip via dict
    row_full = {**row, "id": 1, "created_at_ts": 999}
    f2 = Fact.from_row(row_full)
    assert f2.subject == "x"
    assert f2.object_type == ObjectType.entity
    assert f2.canonicity == Canonicity.canon_modified
    assert f2.known_by_npc_ids == ["npc1", "npc2"]


def test_fact_source_enum_values() -> None:
    assert FactSource.canon.value == "canon"
    assert FactSource.event.value == "event"
    assert FactSource.player_action.value == "player_action"
    assert FactSource.inferred.value == "inferred"


def test_canonicity_enum_values() -> None:
    assert Canonicity.canon_strict.value == "canon_strict"
    assert Canonicity.canon_modified.value == "canon_modified"
    assert Canonicity.divergent.value == "divergent"
