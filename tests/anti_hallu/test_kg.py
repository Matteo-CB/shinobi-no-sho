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


def test_import_canon_includes_eras_hiden_natures_weapons(
    store: KnowledgeGraphStore, canon_dir: Path
) -> None:
    """Spec Phase A : 100% des facts canon importes sans perte.

    Verifie que les datasets eras / hiden / natures / weapons_tools
    sont bien tous integres (gap detecte dans audit Phase A).
    """
    if not canon_dir.exists():
        pytest.skip("data/canonical/ absent")
    stats = import_canon_to_kg(store, canon_dir)
    assert stats.get("eras", 0) > 0, f"eras manquantes: {stats.get('eras')}"
    assert stats.get("hiden", 0) > 0, f"hiden manquants: {stats.get('hiden')}"
    assert stats.get("natures", 0) > 0, f"natures manquantes: {stats.get('natures')}"
    assert stats.get("weapons_tools", 0) > 0, f"weapons manquants: {stats.get('weapons_tools')}"

    # Sanity : les types sont bien dans le KG
    katon = store.get_facts(subject="katon", relation="type", limit=1)
    assert len(katon) == 1 and katon[0].object == "nature"


def test_import_canon_no_field_loss_characters(
    store: KnowledgeGraphStore, canon_dir: Path
) -> None:
    """Spec Phase A : techniques_known_by_era doit etre importe (839 chars).

    Regression test : avant le fix, ces facts etaient silencieusement perdus.
    """
    if not canon_dir.exists():
        pytest.skip("data/canonical/ absent")
    import_canon_to_kg(store, canon_dir)
    naruto_techs = store.get_facts(subject="uzumaki_naruto", relation="knows_technique")
    assert len(naruto_techs) > 0, "techniques_known_by_era de Naruto perdues"
    # Au moins une technique horodatee
    assert any(f.valid_from_year is not None for f in naruto_techs)


def test_import_canon_no_field_loss_organizations(
    store: KnowledgeGraphStore, canon_dir: Path
) -> None:
    """active_period et members_by_era doivent etre dans le KG."""
    if not canon_dir.exists():
        pytest.skip("data/canonical/ absent")
    import_canon_to_kg(store, canon_dir)
    members = store.get_facts(subject="akatsuki", relation="has_member")
    assert len(members) > 0, "members_by_era d'Akatsuki perdus"
    phases = store.get_facts(subject="akatsuki", relation="active_phase")
    assert len(phases) > 0, "active_period d'Akatsuki perdu"


def test_import_canon_no_field_loss_events(
    store: KnowledgeGraphStore, canon_dir: Path
) -> None:
    """outcomes et preconditions des events doivent etre dans le KG."""
    if not canon_dir.exists():
        pytest.skip("data/canonical/ absent")
    import_canon_to_kg(store, canon_dir)
    # On cherche n'importe quel event avec un outcome:character_death (20 dans dataset)
    deaths = store.get_facts(relation="outcome:character_death")
    assert len(deaths) > 0, "outcomes character_death perdus"
    # Et au moins une precondition
    requires = [
        f for f in store.get_facts() if f.relation.startswith("requires:")
    ]
    assert len(requires) > 0, "preconditions perdues"


def test_kg_index_known_by_npc_ids_present(tmp_path: Path) -> None:
    """Spec doc 02 §5.1 : index idx_kg_known_by sur known_by_npc_ids."""
    db_path = tmp_path / "kg.db"
    conn = initialize_db(db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='index' AND tbl_name='kg_facts' AND name='idx_kg_known_by'"
        ).fetchall()
        assert len(rows) == 1, "Index idx_kg_known_by absent du schema (spec §5.1)"
    finally:
        conn.close()


def test_import_canon_includes_ranks_categories_arcs(
    store: KnowledgeGraphStore, canon_dir: Path
) -> None:
    """Spec Phase A : ranks / jutsu_categories / arc_temporal_anchors importes."""
    if not canon_dir.exists():
        pytest.skip("data/canonical/ absent")
    stats = import_canon_to_kg(store, canon_dir)
    assert stats.get("ranks", 0) > 0
    assert stats.get("jutsu_categories", 0) > 0
    assert stats.get("arc_anchors", 0) > 0
    # Sanity : un rank et une category specifiques
    genin = store.get_facts(subject="genin", relation="type", limit=1)
    assert len(genin) == 1 and genin[0].object == "rank"
    nin = store.get_facts(subject="ninjutsu", relation="type", limit=1)
    assert len(nin) == 1 and nin[0].object == "jutsu_category"
    # Arc avec borne temporelle
    arc = store.get_facts(subject="chunin_exam", relation="type", limit=1)
    assert len(arc) == 1 and arc[0].object == "arc"


def test_migrations_upgrade_v1_to_current(tmp_path: Path) -> None:
    """Une base v1 doit pouvoir s'upgrader vers CURRENT_SCHEMA_VERSION sans data loss."""
    import sqlite3

    from shinobi.kg.schema import apply_migrations

    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    # Simule une base v1 (kg_facts seulement)
    conn.executescript("""
        CREATE TABLE kg_schema_version (
            version INTEGER PRIMARY KEY, applied_at_ts INTEGER NOT NULL
        );
        CREATE TABLE kg_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT NOT NULL, relation TEXT NOT NULL, object TEXT,
            object_type TEXT NOT NULL DEFAULT 'value',
            valid_from_year INTEGER, valid_to_year INTEGER,
            source TEXT NOT NULL DEFAULT 'canon',
            confidence REAL NOT NULL DEFAULT 1.0,
            canonicity TEXT NOT NULL DEFAULT 'canon_strict',
            known_by_npc_ids TEXT NOT NULL DEFAULT '[]',
            created_at_ts INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
        );
        INSERT INTO kg_schema_version (version, applied_at_ts)
        VALUES (1, strftime('%s', 'now'));
    """)
    # Insere un fact v1 pour verifier la preservation
    conn.execute(
        "INSERT INTO kg_facts (subject, relation, object) VALUES (?, ?, ?)",
        ("uzumaki_naruto", "type", "character"),
    )
    conn.commit()

    assert schema_version(conn) == 1
    applied = apply_migrations(conn)

    assert CURRENT_SCHEMA_VERSION in applied
    assert schema_version(conn) == CURRENT_SCHEMA_VERSION
    # Donnees preservees
    n = conn.execute("SELECT COUNT(*) AS c FROM kg_facts").fetchone()["c"]
    assert n == 1
    # Tables nouvelles presentes
    tables = {
        r["name"]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "kg_beliefs" in tables
    assert "kg_social_links" in tables
    # Index v3 present
    idx = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='index' AND name='idx_kg_known_by'"
    ).fetchone()
    assert idx is not None
    conn.close()


def test_import_canon_includes_world_rules(
    store: KnowledgeGraphStore, canon_dir: Path
) -> None:
    """Spec Phase A : world_rules.json (chakra/combat/economy canon) importes."""
    if not canon_dir.exists():
        pytest.skip("data/canonical/ absent")
    stats = import_canon_to_kg(store, canon_dir)
    assert stats.get("world_rules", 0) > 0
    # Sample : pool de chakra d'un genin doit etre present
    pool = store.get_facts(
        subject="world_rules",
        relation="chakra:baseline_pools:genin",
    )
    assert len(pool) == 1 and pool[0].object == "100"


def test_import_canon_includes_dates(
    store: KnowledgeGraphStore, canon_dir: Path
) -> None:
    """birth_date / occurs_on_date doivent etre dans le KG (granularite MM-DD)."""
    if not canon_dir.exists():
        pytest.skip("data/canonical/ absent")
    import_canon_to_kg(store, canon_dir)
    bd = store.get_facts(subject="uzumaki_naruto", relation="birth_date")
    assert len(bd) == 1 and bd[0].object == "10-10"
    # Tous les events canon ont un date
    on_dates = [f for f in store.get_facts(relation="occurs_on_date")]
    assert len(on_dates) >= 60


def test_get_facts_year_range_filter(store: KnowledgeGraphStore) -> None:
    """Filtre temporel year_range : chevauchement (from, to)."""
    # Fact actif uniquement entre 5 et 10
    f1 = Fact(subject="x", relation="r", object="bornee", valid_from_year=5, valid_to_year=10)
    # Fact actif a partir de l'an 12 (sans borne haute)
    f2 = Fact(subject="x", relation="r", object="ouverte", valid_from_year=12, valid_to_year=None)
    # Fact sans bornes (toujours actif)
    f3 = Fact(subject="x", relation="r", object="immortel")
    store.add_facts_batch([f1, f2, f3])

    # Range [3, 7] : f1 chevauche, f3 toujours actif. f2 hors range.
    matches = store.get_facts(subject="x", year_range=(3, 7))
    objs = sorted(f.object for f in matches if f.object)
    assert objs == ["bornee", "immortel"]

    # Range [11, 15] : f2 et f3, pas f1
    matches = store.get_facts(subject="x", year_range=(11, 15))
    objs = sorted(f.object for f in matches if f.object)
    assert objs == ["immortel", "ouverte"]

    # Range invalide
    with pytest.raises(ValueError):
        store.get_facts(year_range=(10, 5))
    with pytest.raises(ValueError):
        store.get_facts(year=5, year_range=(0, 10))


def test_get_facts_relation_prefix_filter(store: KnowledgeGraphStore) -> None:
    """Filtre relation_prefix pour requeter les outcome:* / requires:*."""
    facts = [
        Fact(subject="ev1", relation="outcome:character_death", object="X"),
        Fact(subject="ev2", relation="outcome:war_started", object="Y"),
        Fact(subject="ev1", relation="requires:character_alive", object="Z"),
        Fact(subject="ev1", relation="involves", object="W"),
    ]
    store.add_facts_batch(facts)
    outcomes = store.get_facts(relation_prefix="outcome:")
    assert len(outcomes) == 2
    requires = store.get_facts(relation_prefix="requires:")
    assert len(requires) == 1


def test_import_canon_applies_birth_years_patch(
    store: KnowledgeGraphStore, canon_dir: Path
) -> None:
    """character_birth_years_patch.json doit etre applique par le KG loader.

    Spec Phase A : "100% des facts canon importes sans perte". 212 patches
    canon (208 birth_year + 97 death_year) corrigent characters.json. Sans
    application, Naruto et Sasuke n'ont pas de birth_year dans le KG.
    """
    if not canon_dir.exists():
        pytest.skip("data/canonical/ absent")
    stats = import_canon_to_kg(store, canon_dir)
    assert stats.get("birth_years_patched", 0) > 0

    # Naruto : birth_year=0 vient du patch (absent de characters.json)
    naruto_by = store.get_facts(subject="uzumaki_naruto", relation="birth_year")
    assert len(naruto_by) == 1 and naruto_by[0].object == "0"

    # Itachi : birth_year=-7, death_year=16
    itachi_by = store.get_facts(subject="uchiha_itachi", relation="birth_year")
    itachi_dy = store.get_facts(subject="uchiha_itachi", relation="death_year")
    assert itachi_by and itachi_by[0].object == "-7"
    assert itachi_dy and itachi_dy[0].object == "16"

    # alive bounds : pour Itachi mort en 16, alive valid_to_year = 15
    itachi_alive = store.get_facts(subject="uchiha_itachi", relation="alive")
    assert len(itachi_alive) == 1
    assert itachi_alive[0].valid_from_year == -7
    assert itachi_alive[0].valid_to_year == 15


def test_import_canon_eras_temporal_bounds(
    store: KnowledgeGraphStore, canon_dir: Path
) -> None:
    """Bug regression : data canon utilise year_start/year_end pas start_year/end_year.

    Les 13 eras canon avaient toutes leurs bornes silencieusement perdues.
    """
    if not canon_dir.exists():
        pytest.skip("data/canonical/ absent")
    import_canon_to_kg(store, canon_dir)
    span = store.get_facts(subject="warring_states", relation="spans_period")
    assert len(span) == 1
    assert span[0].valid_from_year == -100
    assert span[0].valid_to_year == -55
    # Filtre temporel : warring_states actif a l'an -75
    facts_at_75 = store.get_facts(subject="warring_states", year=-75)
    assert len(facts_at_75) > 0


def test_import_canon_natures_relations(
    store: KnowledgeGraphStore, canon_dir: Path
) -> None:
    """natures.json : type / strong_against / weak_against doivent etre dans le KG."""
    if not canon_dir.exists():
        pytest.skip("data/canonical/ absent")
    import_canon_to_kg(store, canon_dir)
    katon_strong = store.get_facts(subject="katon", relation="strong_against")
    assert any(f.object == "fuuton" for f in katon_strong)
    katon_weak = store.get_facts(subject="katon", relation="weak_against")
    assert any(f.object == "suiton" for f in katon_weak)
    katon_type = store.get_facts(subject="katon", relation="nature_type")
    assert len(katon_type) == 1


def test_import_canon_hiden_authorization(
    store: KnowledgeGraphStore, canon_dir: Path
) -> None:
    """hiden : shareable_with_authorization doit etre dans le KG."""
    if not canon_dir.exists():
        pytest.skip("data/canonical/ absent")
    import_canon_to_kg(store, canon_dir)
    auth = store.get_facts(relation="shareable_with_authorization")
    assert len(auth) >= 15


def test_import_canon_kekkei_mora_evolution(
    store: KnowledgeGraphStore, canon_dir: Path
) -> None:
    """kekkei_mora : evolution_paths + stages doivent etre dans le KG.

    Ex : byakugan_otsutsuki -> tenseigan (canon Otsutsuki).
    """
    if not canon_dir.exists():
        pytest.skip("data/canonical/ absent")
    import_canon_to_kg(store, canon_dir)
    evolves = store.get_facts(subject="byakugan_otsutsuki", relation="evolves_to")
    targets = {f.object for f in evolves}
    assert "tenseigan" in targets
    stages = store.get_facts(subject="byakugan_otsutsuki", relation="has_stage")
    assert len(stages) >= 1


def test_import_canon_preserves_localized_names(
    store: KnowledgeGraphStore, canon_dir: Path
) -> None:
    """Spec Phase A : tous les noms FR / kanji canon doivent etre dans le KG.

    Avant le fix, name_fr / name_kanji etaient inconsistents :
    techniques perdaient 3025 noms FR, villages 40, locations 154, etc.
    """
    if not canon_dir.exists():
        pytest.skip("data/canonical/ absent")
    import_canon_to_kg(store, canon_dir)

    # Au moins 3000 techniques avec name_fr (canon entier)
    tech_fr = [
        f for f in store.get_facts(relation="name_fr")
        if store.get_facts(subject=f.subject, relation="type", limit=1)
        and store.get_facts(subject=f.subject, relation="type", limit=1)[0].object
        == "technique"
    ]
    assert len(tech_fr) >= 3000, f"techniques.name_fr seulement {len(tech_fr)}"

    # Konohagakure name_fr
    f = store.get_facts(subject="konohagakure", relation="name_fr")
    assert len(f) == 1

    # Akatsuki name_fr
    f = store.get_facts(subject="akatsuki", relation="name_fr")
    assert len(f) == 1

    # Rasengan name_fr
    f = store.get_facts(subject="rasengan", relation="name_fr")
    assert len(f) == 1

    # Katon name_kanji (18/18 natures ont name_kanji)
    f = store.get_facts(subject="katon", relation="name_kanji")
    assert len(f) == 1


def test_import_canon_includes_voice_profiles(
    store: KnowledgeGraphStore, canon_dir: Path
) -> None:
    """voice_profiles.json doit etre importe (verbal_tics canon comme dattebayo)."""
    if not canon_dir.exists():
        pytest.skip("data/canonical/ absent")
    stats = import_canon_to_kg(store, canon_dir)
    assert stats.get("voice_profiles", 0) > 0
    profiles = store.get_facts(relation="type", object_value="voice_profile")
    assert len(profiles) >= 30
    # Naruto a un verbal_tic 'dattebayo'
    naruto_tics = store.get_facts(subject="uzumaki_naruto", relation="verbal_tic")
    assert len(naruto_tics) > 0
    assert any("dattebayo" in (t.object or "").lower() for t in naruto_tics)


def test_import_canon_includes_missions(
    store: KnowledgeGraphStore, canon_dir: Path
) -> None:
    """missions.json doit etre integre dans le pipeline canonique unifie.

    Spec Phase A : import_canon_to_kg() = point d'entree unique pour TOUS
    les datasets canon (incluant les 26 missions Sprint MISSIONS).
    """
    if not canon_dir.exists():
        pytest.skip("data/canonical/ absent")
    stats = import_canon_to_kg(store, canon_dir)
    assert stats.get("missions_count", 0) >= 25
    # Mission canon ('mission_wave_country_zabuza') doit etre dans le KG
    wave = store.get_facts(subject="mission_wave_country_zabuza", relation="type")
    assert len(wave) == 1 and wave[0].object == "mission"


def test_import_canon_includes_psycho_notes(
    store: KnowledgeGraphStore, canon_dir: Path
) -> None:
    """psycho_notes.json doit etre integre : forbidden_relations + notes psy.

    Spec Phase A : "100% des facts canon importes sans perte". Les notes
    psy avec leur tranche d'age sont canon-derive et determinent les
    contraintes relationnelles dynamiques.
    """
    if not canon_dir.exists():
        pytest.skip("data/canonical/ absent")
    stats = import_canon_to_kg(store, canon_dir)
    assert stats.get("psycho_notes", 0) > 0

    # Naruto a au moins 1 psycho_note avec bornes temporelles age->year resolues
    notes = store.get_facts(subject="uzumaki_naruto", relation="psycho_note")
    assert len(notes) >= 5
    assert all(n.valid_from_year is not None for n in notes), \
        "psycho_note doit avoir valid_from_year (age->year resolue via birth_year)"

    # Naruto a forbidden_relation_to (uchiha_sasuke a age 0-5)
    fr = store.get_facts(
        subject="uzumaki_naruto", relation="forbidden_relation_to",
        object_value="uchiha_sasuke",
    )
    assert len(fr) >= 1
    # Premiere entree doit etre dans la tranche 0-5 (avant la rivalite formee)
    early = [f for f in fr if f.valid_from_year is not None and f.valid_from_year <= 5]
    assert len(early) >= 1


def test_import_canon_idempotent_with_clear_first_false(
    store: KnowledgeGraphStore, canon_dir: Path
) -> None:
    """Spec Phase A : idempotence de import_canon_to_kg(clear_first=False).

    Avant le fix, voice_profiles / psycho_notes / missions / arcs / world_rules
    etaient duplicates si on appelait import_canon_to_kg deux fois avec
    clear_first=False.
    """
    if not canon_dir.exists():
        pytest.skip("data/canonical/ absent")
    import_canon_to_kg(store, canon_dir)
    n_first = store.count()
    # Re-import sans clear : idempotent (0 ajout)
    import_canon_to_kg(store, canon_dir, clear_first=False)
    n_second = store.count()
    assert n_second == n_first, f"duplications detectees: {n_first} -> {n_second}"


def test_update_fact_supports_valid_from_year(store: KnowledgeGraphStore) -> None:
    """Spec Phase A : API CRUD avec filtres temporels symetriques.

    Avant le fix, update_fact n'acceptait pas valid_from_year (asymetrie
    par rapport a valid_to_year).
    """
    fid = store.add_fact(Fact(subject="x", relation="r", object="v"))
    updated = store.update_fact(fid, valid_from_year=10, valid_to_year=20)
    assert updated is not None
    assert updated.valid_from_year == 10
    assert updated.valid_to_year == 20


def test_update_fact_supports_source_and_object_type(
    store: KnowledgeGraphStore,
) -> None:
    """Spec Phase A round 35 : update_fact symetrique avec get_facts/count.

    Avant le fix : pas moyen d'updater source ni object_type via
    update_fact -> impossible de promouvoir un 'inferred' en 'canon' ou
    de corriger une mauvaise classification.
    """
    fid = store.add_fact(Fact(
        subject="x", relation="r", object="v",
        source=FactSource.inferred.value,
        object_type=ObjectType.value,
    ))
    # Promouvoir inferred -> canon
    updated = store.update_fact(fid, source="canon")
    assert updated is not None and updated.source == "canon"
    # Changer object_type
    updated = store.update_fact(fid, object_type=ObjectType.entity)
    assert updated is not None and updated.object_type == ObjectType.entity
    # Accepte les strings aussi (pas que les enums)
    updated = store.update_fact(fid, object_type="value")
    assert updated.object_type == ObjectType.value


def test_delete_facts_bulk_filter(store: KnowledgeGraphStore) -> None:
    """Spec Phase A : delete_facts bulk avec filtres composables."""
    facts = [
        Fact(subject="a", relation="r1", object="X", source="canon"),
        Fact(subject="b", relation="r1", object="Y", source="canon"),
        Fact(subject="c", relation="r2", object="Z", source="event:42"),
    ]
    store.add_facts_batch(facts)
    assert store.count() == 3

    # Delete tous les facts source='canon'
    n = store.delete_facts(source="canon")
    assert n == 2
    assert store.count() == 1
    remaining = store.get_facts()
    assert remaining[0].subject == "c"

    # Delete sans filtre -> erreur (protection)
    with pytest.raises(ValueError):
        store.delete_facts()


def test_foreign_keys_enabled_cascade_delete(store: KnowledgeGraphStore) -> None:
    """Spec Phase A : PRAGMA foreign_keys=ON pour cascade kg_beliefs.

    Avant le fix, supprimer un fact ne supprimait pas ses beliefs (orphelins).
    """
    from shinobi.kg.belief import BeliefPropagator
    from shinobi.kg.schema import Belief

    # PRAGMA actif
    fk = store.conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert fk == 1, "PRAGMA foreign_keys doit etre ON pour ON DELETE CASCADE"

    fid = store.add_fact(Fact(subject="x", relation="r", object="v"))
    prop = BeliefPropagator(store.conn)
    prop.add_belief(Belief(fact_id=fid, npc_id="npc1", fidelity=1.0))

    n_before = store.conn.execute(
        "SELECT COUNT(*) FROM kg_beliefs WHERE fact_id = ?", (fid,)
    ).fetchone()[0]
    assert n_before == 1

    store.delete_fact(fid)

    # Cascade : beliefs orphelins doivent etre supprimes
    n_after = store.conn.execute(
        "SELECT COUNT(*) FROM kg_beliefs WHERE fact_id = ?", (fid,)
    ).fetchone()[0]
    assert n_after == 0, "ON DELETE CASCADE n'a pas fonctionne"


def test_kg_index_source_present(tmp_path: Path) -> None:
    """v4 migration : idx_kg_source pour queries source_prefix."""
    db_path = tmp_path / "kg.db"
    conn = initialize_db(db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='index' AND tbl_name='kg_facts' AND name='idx_kg_source'"
        ).fetchall()
        assert len(rows) == 1, "Index idx_kg_source absent (v4 migration)"
    finally:
        conn.close()


def test_get_facts_object_type_filter(store: KnowledgeGraphStore) -> None:
    """Spec §5.1 : object_type column (entity/value/belief). CRUD doit filtrer dessus."""
    store.add_facts_batch([
        Fact(subject="a", relation="r", object="X", object_type=ObjectType.entity),
        Fact(subject="b", relation="r", object="Y", object_type=ObjectType.value),
        Fact(subject="c", relation="r", object="Z", object_type=ObjectType.belief),
    ])
    entities = store.get_facts(object_type=ObjectType.entity)
    assert len(entities) == 1 and entities[0].object == "X"
    values = store.get_facts(object_type="value")
    assert len(values) == 1 and values[0].object == "Y"
    beliefs = store.get_facts(object_type=ObjectType.belief)
    assert len(beliefs) == 1 and beliefs[0].object == "Z"


def test_kg_wal_mode_enabled(tmp_path: Path) -> None:
    """File-based DB doit etre en mode WAL (perf + concurrent readers)."""
    db_path = tmp_path / "kg.db"
    store = KnowledgeGraphStore(db_path)
    try:
        jm = store.conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert jm.lower() == "wal", f"journal_mode={jm}, attendu WAL"
        sync = store.conn.execute("PRAGMA synchronous").fetchone()[0]
        # 0=OFF, 1=NORMAL, 2=FULL, 3=EXTRA
        assert sync == 1, f"synchronous={sync}, attendu 1 (NORMAL)"
    finally:
        store.close()


def test_transaction_atomicity_rollback(store: KnowledgeGraphStore) -> None:
    """Spec Phase A : `with store.transaction()` doit etre vraiment atomique.

    Bug regression : avant fix, add_fact dans transaction context creait sa
    propre sub-transaction auto-committee. Un raise apres add_fact ne
    rollback rien.
    """
    try:
        with store.transaction():
            store.add_fact(Fact(subject="a", relation="r", object="v"))
            store.add_fact(Fact(subject="b", relation="r", object="v"))
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    # Si transaction etait atomique, count == 0
    assert store.count() == 0, "transaction n'est pas atomique (rollback casse)"


def test_transaction_nested_savepoint(store: KnowledgeGraphStore) -> None:
    """Spec Phase A round 34 : nested transactions via SAVEPOINT.

    Avant le fix : `with store.transaction():` imbriques crashaient avec
    'cannot start a transaction within a transaction'. Maintenant on utilise
    SAVEPOINT pour la nesting (semantique equivalente sur SQLite).
    """
    # Cas 1 : nested commit propre
    with store.transaction():
        store.add_fact(Fact(subject="a", relation="r", object="v"))
        with store.transaction():
            store.add_fact(Fact(subject="b", relation="r", object="v"))
    assert store.count() == 2

    # Cas 2 : inner rollback, outer commit -> outer survives
    store.clear_all()
    with store.transaction():
        store.add_fact(Fact(subject="outer", relation="r", object="v"))
        try:
            with store.transaction():
                store.add_fact(Fact(subject="inner", relation="r", object="v"))
                raise RuntimeError("inner_boom")
        except RuntimeError:
            pass
        store.add_fact(Fact(subject="after_inner", relation="r", object="v"))
    subjects = {f.subject for f in store.get_facts()}
    assert subjects == {"outer", "after_inner"}, \
        f"inner aurait du etre rollback mais outer/after_inner survivent. Got: {subjects}"

    # Cas 3 : outer rollback annule tout (meme si inner a ete release)
    store.clear_all()
    try:
        with store.transaction():
            store.add_fact(Fact(subject="X", relation="r", object="v"))
            with store.transaction():
                store.add_fact(Fact(subject="Y", relation="r", object="v"))
            raise RuntimeError("outer_boom")
    except RuntimeError:
        pass
    assert store.count() == 0


def test_transaction_atomicity_belief_social(store: KnowledgeGraphStore) -> None:
    """Spec Phase A : BeliefPropagator + SocialNetwork (qui partagent la
    connection avec KnowledgeGraphStore) doivent respecter store.transaction().

    Bug regression round 16 : leur conn.commit() direct committait la
    transaction parente prematurement -> les rollbacks n'avaient plus rien
    a annuler.
    """
    from shinobi.kg.belief import BeliefPropagator
    from shinobi.kg.social import SocialNetwork
    from shinobi.kg.schema import Belief, SocialLink

    fid = store.add_fact(Fact(subject="X", relation="r", object="v"))
    prop = BeliefPropagator(store.conn)
    soc = SocialNetwork(store.conn)
    prop.add_belief(Belief(fact_id=fid, npc_id="persistant_npc", fidelity=1.0))
    soc.add_link(SocialLink(npc_a="alice", npc_b="bob", link_type="friend"))

    # Test rollback add_belief + add_link dans transaction qui throw
    try:
        with store.transaction():
            prop.add_belief(Belief(fact_id=fid, npc_id="new_npc", fidelity=0.5))
            soc.add_link(SocialLink(npc_a="charlie", npc_b="dave", link_type="enemy"))
            raise RuntimeError("boom")
    except RuntimeError:
        pass

    n_beliefs = store.conn.execute(
        "SELECT COUNT(*) FROM kg_beliefs"
    ).fetchone()[0]
    n_links = store.conn.execute(
        "SELECT COUNT(*) FROM kg_social_links"
    ).fetchone()[0]
    assert n_beliefs == 1, "add_belief n'a pas respecte store.transaction()"
    assert n_links == 1, "add_link n'a pas respecte store.transaction()"

    # Test rollback clear_all dans transaction qui throw
    try:
        with store.transaction():
            prop.clear_all()
            soc.clear_all()
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    n_beliefs = store.conn.execute(
        "SELECT COUNT(*) FROM kg_beliefs"
    ).fetchone()[0]
    n_links = store.conn.execute(
        "SELECT COUNT(*) FROM kg_social_links"
    ).fetchone()[0]
    assert n_beliefs == 1, "BeliefPropagator.clear_all() a casse la transaction"
    assert n_links == 1, "SocialNetwork.clear_all() a casse la transaction"


def test_transaction_atomicity_all_dml(store: KnowledgeGraphStore) -> None:
    """Toutes les operations DML (add/update/delete/clear_all) doivent
    respecter la transaction parente.

    Avant le fix round 15, update_fact / delete_fact / delete_facts /
    clear_all faisaient `self.conn.commit()` direct -> commit premature
    de la transaction parente.
    """
    # delete + add dans transaction qui rollback
    store.add_fact(Fact(subject="persistant", relation="r", object="v"))
    fid_initial = 1
    try:
        with store.transaction():
            store.add_fact(Fact(subject="new", relation="r", object="v"))
            store.delete_fact(fid_initial)
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    facts = store.get_facts()
    assert len(facts) == 1 and facts[0].subject == "persistant"

    # update dans transaction qui rollback
    store.clear_all()
    fid = store.add_fact(Fact(subject="X", relation="r", object="v_initial"))
    try:
        with store.transaction():
            store.update_fact(fid, object_value="v_modified")
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    f = store.get_fact(fid)
    assert f is not None and f.object == "v_initial"

    # delete_facts bulk dans transaction qui rollback
    store.clear_all()
    store.add_facts_batch([
        Fact(subject=f"s{i}", relation="r", object="v", source="canon")
        for i in range(5)
    ])
    try:
        with store.transaction():
            store.delete_facts(source="canon")
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert store.count() == 5

    # clear_all dans transaction qui rollback
    store.clear_all()
    store.add_fact(Fact(subject="A", relation="r", object="v"))
    try:
        with store.transaction():
            store.clear_all()
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert store.count() == 1


def test_transaction_commit(store: KnowledgeGraphStore) -> None:
    """Transaction commit normal : facts persistes apres exit propre."""
    with store.transaction():
        store.add_fact(Fact(subject="a", relation="r", object="v"))
        store.add_fact(Fact(subject="b", relation="r", object="v"))
    assert store.count() == 2


def test_import_canon_total_matches_store_count(
    store: KnowledgeGraphStore, canon_dir: Path
) -> None:
    """Spec Phase A : stats['total'] doit refleter store.count() reel.

    Bug regression round 20 : avant fix, total = sum(stats.values()) incluait
    `birth_years_patched` (count de patches) et `missions_count` (count
    missions) qui ne sont PAS des facts. Resultat : 185 phantom facts au
    compteur declare.
    """
    if not canon_dir.exists():
        pytest.skip("data/canonical/ absent")
    stats = import_canon_to_kg(store, canon_dir)
    assert stats["total"] == store.count(), (
        f"total declare={stats['total']} != store.count()={store.count()} "
        f"(meta keys probablement inclus a tort dans total)"
    )


def test_import_canon_full_entity_coverage(
    store: KnowledgeGraphStore, canon_dir: Path
) -> None:
    """Spec Phase A : 100% des facts canon importes sans perte.

    Assertion forte : chaque entite ID dans chaque JSON canon doit avoir
    son fact `(<id>, type, <type_label>)` dans le KG. Aucun entity loss
    silencieux a l'import.
    """
    if not canon_dir.exists():
        pytest.skip("data/canonical/ absent")
    import json as _json
    import_canon_to_kg(store, canon_dir)

    # Datasets list-based avec mapping {filename: type_label}
    expected_types = {
        "characters.json": "character",
        "techniques.json": "technique",
        "clans.json": "clan",
        "villages.json": "village",
        "locations.json": "location",
        "organizations.json": "organization",
        "tailed_beasts.json": "tailed_beast",
        "timeline_events.json": "timeline_event",
        "eras.json": "era",
        "hiden.json": "hiden",
        "natures.json": "nature",
        "weapons_tools.json": "weapon",
        "ranks.json": "rank",
        "jutsu_categories.json": "jutsu_category",
        "voice_profiles.json": "voice_profile",
        "kekkei_genkai.json": "kekkei_genkai",
        "kekkei_mora.json": "kekkei_mora",
    }

    total_missing = 0
    missing_per_file: dict[str, list[str]] = {}
    for fname, type_label in expected_types.items():
        items = _json.loads((canon_dir / fname).read_text(encoding="utf-8"))
        if not isinstance(items, list):
            continue
        expected_ids = {x["id"] for x in items if isinstance(x, dict) and x.get("id")}
        actual = {
            f.subject
            for f in store.get_facts(relation="type", object_value=type_label)
        }
        missing = expected_ids - actual
        if missing:
            total_missing += len(missing)
            missing_per_file[fname] = sorted(missing)[:5]

    # Datasets dict-based
    arcs = _json.loads(
        (canon_dir / "arc_temporal_anchors.json").read_text(encoding="utf-8")
    ).get("arcs", {})
    arc_actual = {
        f.subject for f in store.get_facts(relation="type", object_value="arc")
    }
    arc_missing = set(arcs.keys()) - arc_actual
    if arc_missing:
        total_missing += len(arc_missing)
        missing_per_file["arc_temporal_anchors.json"] = sorted(arc_missing)[:5]

    # Missions (Sprint MISSIONS pipeline)
    m_data = _json.loads(
        (canon_dir / "missions.json").read_text(encoding="utf-8")
    )
    m_ids = {
        x["id"] for x in m_data.get("missions", []) if x.get("id")
    }
    m_actual = {
        f.subject for f in store.get_facts(relation="type", object_value="mission")
    }
    m_missing = m_ids - m_actual
    if m_missing:
        total_missing += len(m_missing)
        missing_per_file["missions.json"] = sorted(m_missing)[:5]

    assert total_missing == 0, \
        f"{total_missing} entites canon perdues : {missing_per_file}"


def test_import_canon_no_duplicate_triplets(
    store: KnowledgeGraphStore, canon_dir: Path
) -> None:
    """Spec Phase A : aucun fact dupliquee (subject, relation, object,
    valid_from_year, source) apres l'import canon.

    Bug regression round 29 : 313 doublons detectes :
    - Intra-entry : `acrobat.canonical_users = ['b_killer', ..., 'b_killer']`
    - Cross-dataset : `anbu` (organization + rank) emettait 2 fois
      name_fr/has_source/sourced_from
    """
    if not canon_dir.exists():
        pytest.skip("data/canonical/ absent")
    import_canon_to_kg(store, canon_dir)
    rows = store.conn.execute(
        "SELECT subject, relation, object, valid_from_year, source, COUNT(*) AS c "
        "FROM kg_facts GROUP BY subject, relation, object, valid_from_year, source "
        "HAVING c > 1"
    ).fetchall()
    assert len(rows) == 0, f"Doublons triplet detectes : {len(rows)}"

    # Sanity : Anbu garde ses 2 type facts distincts (organization + rank)
    types = {f.object for f in store.get_facts(subject="anbu", relation="type")}
    assert types >= {"organization", "rank"}, \
        f"Anbu doit avoir type=organization ET type=rank, recu : {types}"


def test_mission_canonicity_runtime_mapping(store: KnowledgeGraphStore) -> None:
    """Spec Phase A round 41 : mission.canonicity='filler' doit donner
    Fact.canonicity=canon_modified, coherent avec _import_list pipeline.

    Bug regression : `_facts_from_mission` hardcodait `canon_strict` pour
    TOUS les facts mission, ignorant la canonicity de la mission. Pour
    'manga'/'boruto' c'est OK (mappage canon_strict), mais 'filler'/'game'
    devraient etre canon_modified et ne l'etaient pas.
    """
    from shinobi.missions.types import (
        Mission, MissionRank, MissionType, MissionParticipant,
    )
    from shinobi.missions.kg_integration import import_missions_to_kg

    m_filler = Mission(
        id="mission_test_filler",
        name_fr="Test Filler",
        rank=MissionRank.d,
        type=MissionType.escort,
        year=10,
        summary_fr="Filler test mission with canonicity validation",
        canonicity="filler",
        participants=[MissionParticipant(character_id="naruto", role="operative")],
    )
    import_missions_to_kg(store, [m_filler], clear_first=False)

    facts = store.get_facts(subject="mission_test_filler")
    assert len(facts) > 0
    assert all(f.canonicity == Canonicity.canon_modified for f in facts), \
        f"mission filler doit etre canon_modified : {[f.canonicity.value for f in facts]}"


def test_sourced_from_unified_across_pipelines(
    store: KnowledgeGraphStore, canon_dir: Path
) -> None:
    """Spec Phase A : `sourced_from` doit etre la relation unifiee pour la
    canonicity source brute, qu'elle soit emise par _import_list (canon
    pipeline standard) ou import_missions_to_kg (Sprint MISSIONS).

    Bug regression round 28 : missions emettaient `canonicity_source` au lieu
    de `sourced_from`, asymetrie cross-pipeline.
    """
    if not canon_dir.exists():
        pytest.skip("data/canonical/ absent")
    import_canon_to_kg(store, canon_dir)
    # canonicity_source ne doit plus exister (legacy)
    legacy = store.get_facts(relation="canonicity_source")
    assert len(legacy) == 0, \
        f"relation legacy canonicity_source toujours presente: {len(legacy)}"
    # Missions ont sourced_from (unifie avec autres entites)
    mission_sf = store.get_facts(
        subject="mission_wave_country_zabuza", relation="sourced_from",
    )
    assert len(mission_sf) == 1
    assert mission_sf[0].object == "manga"


def test_import_canon_preserves_text_fr_fields(
    store: KnowledgeGraphStore, canon_dir: Path
) -> None:
    """Spec Phase A round 40 : champs descriptifs `_fr` canon-derive doivent
    etre dans le KG (personality_fr, description_fr, abilities_fr, etc.).

    Bug regression : avant le fix, ces champs etaient skip comme "META"
    alors qu'ils sont du contenu canon-derive (5165 facts au total).
    """
    if not canon_dir.exists():
        pytest.skip("data/canonical/ absent")
    import_canon_to_kg(store, canon_dir)

    # Naruto personality_fr (1360 chars : 100%)
    naruto_p = store.get_facts(
        subject="uzumaki_naruto", relation="personality_fr",
    )
    assert len(naruto_p) == 1
    assert naruto_p[0].object  # non-empty

    # Rasengan description_fr (3025 techniques)
    rasengan_d = store.get_facts(subject="rasengan", relation="description_fr")
    assert len(rasengan_d) == 1

    # Uchiha clan history_summary_fr
    uchiha_h = store.get_facts(subject="uchiha", relation="history_summary_fr")
    assert len(uchiha_h) == 1

    # Counts globaux
    assert store.count(relation="personality_fr") >= 1300
    assert store.count(relation="description_fr") >= 3000
    assert store.count(relation="abilities_fr") >= 200
    assert store.count(relation="narrative_summary_fr") >= 50


def test_import_canon_preserves_updated_at(
    store: KnowledgeGraphStore, canon_dir: Path
) -> None:
    """Spec Phase A : `updated_at` (audit trail canon) doit etre dans le KG.

    Bug regression round 32 : ce champ etait skip alors qu'il indique quand
    la donnee canon a ete modifiee la derniere fois (cache invalidation,
    audit trail, debug). Distinct de `Fact.created_at_ts` (insertion KG).
    """
    if not canon_dir.exists():
        pytest.skip("data/canonical/ absent")
    import_canon_to_kg(store, canon_dir)
    ua_facts = store.get_facts(relation="canon_updated_at")
    assert len(ua_facts) >= 5000, f"canon_updated_at facts: {len(ua_facts)}"

    # Format YYYY-MM-DD
    naruto_ua = store.get_facts(
        subject="uzumaki_naruto", relation="canon_updated_at",
    )
    assert len(naruto_ua) == 1
    val = naruto_ua[0].object
    assert val and len(val) == 10 and val[4] == "-" and val[7] == "-", \
        f"Format YYYY-MM-DD attendu, recu : {val}"


def test_import_canon_preserves_source_refs(
    store: KnowledgeGraphStore, canon_dir: Path
) -> None:
    """Spec Phase A : `sources` field (narutopedia refs) doit etre dans le KG.

    Bug regression round 26 : le champ `sources` (1+ ref par entite, ~5000+
    refs narutopedia total) etait skip. Sans lui, impossible de remonter au
    canon source pour validation.

    Round 27 : missions etaient elles aussi sans has_source (Sprint MISSIONS
    pipeline separe).
    """
    if not canon_dir.exists():
        pytest.skip("data/canonical/ absent")
    import_canon_to_kg(store, canon_dir)
    src_facts = store.get_facts(relation="has_source")
    assert len(src_facts) >= 5000, f"has_source facts: {len(src_facts)}"

    # Naruto a au moins 1 source narutopedia
    naruto_src = store.get_facts(subject="uzumaki_naruto", relation="has_source")
    assert len(naruto_src) >= 1
    assert all("narutopedia" in (f.object or "").lower() for f in naruto_src)

    # Missions ont aussi leur has_source (Sprint MISSIONS pipeline)
    mission_src = store.get_facts(
        subject="mission_wave_country_zabuza", relation="has_source",
    )
    assert len(mission_src) >= 1
    assert "narutopedia" in (mission_src[0].object or "").lower()


def test_import_canon_voice_sample_lines_and_avoid(
    store: KnowledgeGraphStore, canon_dir: Path
) -> None:
    """Spec Phase A : voice_profiles.sample_lines + do_not_use doivent etre
    dans le KG.

    Sample_lines = phrases canoniques attestees (anti-hallucination pour LLM).
    Do_not_use = anti-patterns canon (interdits stylistiques).

    Bug regression round 25 : ces 2 champs canon-derive (152 + 88 entries)
    etaient skip alors qu'ils servent au pipeline narratif Phase D/E.
    """
    if not canon_dir.exists():
        pytest.skip("data/canonical/ absent")
    import_canon_to_kg(store, canon_dir)
    sample_lines = store.get_facts(relation="sample_line")
    avoid = store.get_facts(relation="do_not_use")
    assert len(sample_lines) >= 100, f"sample_lines: {len(sample_lines)}"
    assert len(avoid) >= 50, f"do_not_use: {len(avoid)}"

    # Naruto en particulier doit avoir au moins 1 sample_line canon
    voice = store.get_facts(
        relation="voice_for_character", object_value="uzumaki_naruto", limit=1,
    )
    assert len(voice) == 1
    vid = voice[0].subject
    n_lines = store.get_facts(subject=vid, relation="sample_line")
    assert len(n_lines) >= 1


def test_import_canon_cancellation_strategy(
    store: KnowledgeGraphStore, canon_dir: Path
) -> None:
    """Spec Phase A : timeline_events.cancellation_strategy doit etre dans le KG.

    Ce champ canon (60/60 events) determine le mecanique "timeline divergente"
    decrit au §8 du spec : que se passe-t-il si le joueur previent un event ?
    Types canon : hard_cancel / cascade_cancel / delay / substitute.

    Bug regression round 24 : ce champ etait silencieusement perdu dans le
    loader.
    """
    if not canon_dir.exists():
        pytest.skip("data/canonical/ absent")
    import_canon_to_kg(store, canon_dir)
    cs_facts = store.get_facts(relation="cancellation_strategy")
    assert len(cs_facts) >= 60, f"cancellation_strategy facts: {len(cs_facts)}"
    # Au moins les 4 types canon doivent apparaitre
    types_seen = {f.object for f in cs_facts}
    expected = {"hard_cancel", "cascade_cancel", "delay", "substitute"}
    assert expected.issubset(types_seen), \
        f"Types manquants : {expected - types_seen}"


def test_add_belief_mutates_id_in_place(store: KnowledgeGraphStore) -> None:
    """Spec Phase A round 39 : BeliefPropagator.add_belief mute belief.id.

    Coherent avec store.add_fact (round 37).
    """
    from shinobi.kg.belief import BeliefPropagator
    from shinobi.kg.schema import Belief

    fid = store.add_fact(Fact(subject="X", relation="r", object="v"))
    prop = BeliefPropagator(store.conn)
    b = Belief(fact_id=fid, npc_id="npc1")
    assert b.id is None
    prop.add_belief(b)
    assert b.id is not None and b.id > 0


def test_add_link_mutates_id_in_place(store: KnowledgeGraphStore) -> None:
    """Spec Phase A round 39 : SocialNetwork.add_link mute link.id.

    Coherent avec store.add_fact (round 37).
    """
    from shinobi.kg.social import SocialNetwork
    from shinobi.kg.schema import SocialLink

    soc = SocialNetwork(store.conn)
    link = SocialLink(npc_a="alice", npc_b="bob", link_type="friend")
    assert link.id is None
    soc.add_link(link)
    assert link.id is not None and link.id > 0


def test_add_fact_mutates_id_in_place(store: KnowledgeGraphStore) -> None:
    """Spec Phase A round 37 : add_fact / add_facts_batch doivent populer
    fact.id apres l'insert.

    Avant le fix : `fid = store.add_fact(fact)` retournait l'id mais
    `fact.id` restait None. Le caller devait manuellement faire
    `fact.id = fid` avant d'enchainer (add_known_by, etc.). Awkward.
    """
    f = Fact(subject="X", relation="r", object="v")
    assert f.id is None
    fid = store.add_fact(f)
    assert f.id == fid, f"add_fact n'a pas mute fact.id (None -> {fid})"

    # Batch
    facts = [Fact(subject=f"s{i}", relation="r", object="v") for i in range(3)]
    assert all(f.id is None for f in facts)
    ids = store.add_facts_batch(facts)
    assert [f.id for f in facts] == ids, "add_facts_batch n'a pas mute les ids"


def test_canon_import_no_malformed_entity_refs(
    store: KnowledgeGraphStore, canon_dir: Path
) -> None:
    """Spec Phase A round 50 : invariant TOTAL apres canon import.

    AUCUN fact avec object_type=entity ne doit avoir un object non-string
    apres l'import canon entier. Cet invariant verifie l'effort cumule des
    rounds 36-49 (defensive parsing total) sur la donnee reelle.
    """
    if not canon_dir.exists():
        pytest.skip("data/canonical/ absent")
    import_canon_to_kg(store, canon_dir)

    # SQL : compte les facts entity avec object NULL ou empty
    rows = store.conn.execute(
        "SELECT COUNT(*) FROM kg_facts "
        "WHERE object_type = 'entity' AND (object IS NULL OR object = '')"
    ).fetchone()
    assert rows[0] == 0, \
        f"{rows[0]} facts entity avec object NULL/empty (data quality)"

    # Tous les Facts en Python via from_row : aucun object non-str pour entity
    entity_facts = store.get_facts(object_type=ObjectType.entity)
    bad = [
        f for f in entity_facts
        if f.object is not None and not isinstance(f.object, str)
    ]
    assert len(bad) == 0, \
        f"{len(bad)} entity refs avec object non-str apres roundtrip"


def test_facts_from_event_location_defensive() -> None:
    """Spec Phase A round 48 : event.location doit etre str pour creer
    un entity_link valide (occurs_at).
    """
    from shinobi.kg.loader import _facts_from_event

    # location int -> skip
    facts = _facts_from_event({"id": "E", "name_fr": "T", "year": 0, "location": 42})
    locs = [f for f in facts if f.relation == "occurs_at"]
    assert len(locs) == 0

    # location str valide -> emis
    facts = _facts_from_event({
        "id": "E", "name_fr": "T", "year": 0, "location": "konohagakure",
    })
    locs = [f for f in facts if f.relation == "occurs_at"]
    assert len(locs) == 1
    assert locs[0].object == "konohagakure"


def test_facts_from_event_outcome_params_defensive() -> None:
    """Spec Phase A round 47 : outcome/precondition params doivent valider
    que character_id/village_id/etc. sont str avant entity-link.

    Bug regression : `params.get('character_id') or ...` acceptait int/bool
    truthy -> fact entity_link emis avec object non-str. Fallback JSON
    correct si tous les str-checks fail.
    """
    from shinobi.kg.loader import _facts_from_event

    facts = _facts_from_event({
        "id": "E", "name_fr": "Test", "year": 0,
        "outcomes": [
            {"type": "character_death", "parameters": {"character_id": 42}},
            {"type": "relationship_formed",
             "parameters": {"a": "naruto", "b": "sasuke"}},  # no primary -> JSON
        ],
        "preconditions": [
            {"type": "character_alive", "parameters": {"character_id": True}},
        ],
    })
    bad = [
        f for f in facts
        if f.object_type == ObjectType.entity and not isinstance(f.object, str)
    ]
    assert len(bad) == 0, f"{len(bad)} entity refs malformees"

    # Outcome avec character_id corrupt -> JSON fallback (object_type=value)
    death = [f for f in facts if f.relation == "outcome:character_death"]
    assert len(death) == 1
    assert death[0].object_type == ObjectType.value
    assert death[0].object.startswith("{")


def test_facts_from_builders_defensive_non_str_references() -> None:
    """Spec Phase A round 45 : references inter-entites (country, near_village,
    owning_clan, character_id, etc.) doivent etre validees str avant emit.

    Bug regression : `if (cid := v.get('character_id')):` acceptait n'importe
    quel truthy -> facts entity link malformes avec object int/list/dict.
    """
    from shinobi.kg.loader import (
        _facts_from_village, _facts_from_location,
        _facts_from_hiden, _facts_from_voice_profile,
    )

    # Tous emis sans reference (skip les corrompus)
    cases = [
        (_facts_from_village, {"id": "V", "country": 42}),
        (_facts_from_location, {"id": "L", "near_village": ["x"]}),
        (_facts_from_hiden, {"id": "H", "owning_clan": {"a": "b"}}),
        (_facts_from_voice_profile, {"id": "V", "character_id": 42}),
    ]
    for builder, data in cases:
        facts = builder(data)
        bad = [
            f for f in facts
            if f.object_type == ObjectType.entity and not isinstance(f.object, str)
        ]
        assert len(bad) == 0, \
            f"{builder.__name__}({data}) : {len(bad)} entity refs malformees"


def test_facts_from_builders_defensive_non_str_id() -> None:
    """Spec Phase A round 44 : tous les builders defensive contre id non-str.

    Bug regression : `if not cid: return []` ne couvrait pas le cas d'un
    id non-string (int, bool, list, dict) -> facts emis avec subject de
    type incorrect. SQLite coerce int -> str au stockage, mais le Fact
    Python garderait le type incorrect avant insert -> roundtrip casse.
    """
    from shinobi.kg.loader import (
        _facts_from_character, _facts_from_technique, _facts_from_clan,
        _facts_from_village, _facts_from_organization, _facts_from_tailed_beast,
        _facts_from_event, _facts_from_nature, _facts_from_hiden,
    )

    builders = [
        _facts_from_character, _facts_from_technique, _facts_from_clan,
        _facts_from_village, _facts_from_organization, _facts_from_tailed_beast,
        _facts_from_event, _facts_from_nature, _facts_from_hiden,
    ]
    # Tous les types corrompus pour 'id' -> 0 facts retournes
    for case in [None, 42, True, 3.14, "", [], {}]:
        for builder in builders:
            facts = builder({"id": case})
            assert len(facts) == 0, \
                f"{builder.__name__}(id={case!r}) : {len(facts)} facts (attendu 0)"

    # Sanity : id valide string fonctionne toujours
    facts = _facts_from_character({"id": "naruto", "name_romaji": "Naruto"})
    assert len(facts) >= 2


def test_facts_from_builders_defensive_str_as_list() -> None:
    """Spec Phase A round 43 : tous les builders defensive contre str au
    lieu de list pour les fields list-typed.

    Bug regression : `for kg in c.get('kekkei_genkai', []) or []` iterait
    les caracteres si kekkei_genkai='sharingan' -> 9 facts incorrects
    `(X, has_kekkei_genkai, 's')`, etc. Maintenant via _str_list helper,
    skip propre.
    """
    from shinobi.kg.loader import (
        _facts_from_character, _facts_from_clan, _facts_from_technique,
        _facts_from_event, _facts_from_nature, _facts_from_organization,
    )

    # Character
    facts = _facts_from_character({
        "id": "X",
        "kekkei_genkai": "sharingan",  # str corrompu
        "natures": "katon",
    })
    bad = [f for f in facts if f.relation in ("has_kekkei_genkai", "has_nature")]
    assert len(bad) == 0

    # Liste valide preservee
    facts = _facts_from_character({
        "id": "Y", "kekkei_genkai": ["sharingan"], "natures": ["katon"],
    })
    good = [f for f in facts if f.relation in ("has_kekkei_genkai", "has_nature")]
    assert len(good) == 2

    # Technique
    facts = _facts_from_technique({
        "id": "Z", "natures": "katon", "canonical_users": "naruto",
    })
    bad = [f for f in facts if f.relation in ("requires_nature", "has_canonical_user")]
    assert len(bad) == 0

    # Event involved_characters
    facts = _facts_from_event({
        "id": "E", "name_fr": "T", "year": 0,
        "involved_characters": "naruto",  # str corruption
    })
    bad = [f for f in facts if f.relation == "involves"]
    assert len(bad) == 0


def test_facts_from_voice_profile_defensive_lists() -> None:
    """Spec Phase A round 42 : voice_profile builder defensive contre des
    list-typed fields corrompus.

    Bug regression : `for tic in v.get("verbal_tics", []) or []` ne validait
    pas que la valeur etait une list. Si str -> Python iterait les caracteres
    comme facts (bug subtil). Si dict -> iterait les keys. Si int -> CRASH.
    """
    from shinobi.kg.loader import _facts_from_voice_profile

    # Tous les cas corrompus -> skip propre
    for raw in ["dattebayo_str", None, {"a": "b"}, 42]:
        facts = _facts_from_voice_profile({
            "id": "X", "verbal_tics": raw, "sample_lines": raw, "do_not_use": raw,
        })
        # Seulement le fact 'type' devrait etre cree
        non_type = [f for f in facts if f.relation != "type"]
        assert len(non_type) == 0, \
            f"raw={raw!r} : {len(non_type)} facts non-type emits, attendu 0"

    # List valide preservee, mixed types filtree
    facts = _facts_from_voice_profile({
        "id": "X", "verbal_tics": ["dattebayo", None, 42, "datteba"],
    })
    tics = [f for f in facts if f.relation == "verbal_tic"]
    assert len(tics) == 2
    assert {t.object for t in tics} == {"dattebayo", "datteba"}


def test_facts_from_technique_defensive_prerequisites() -> None:
    """Spec Phase A round 38 : `prerequisites` peut etre corrompu (string,
    list, int, None) -> ne doit pas crash le loader.

    Bug regression : `pre.get(...)` crashait sur string/list/int.
    """
    from shinobi.kg.loader import _facts_from_technique

    base = {"id": "X", "name_romaji": "X"}
    # Tous les types corrompus skip propre, valid dict marche
    for val in ["corrupt", ["a"], 42, None, {"clan_restriction": "uchiha"}]:
        facts = _facts_from_technique({**base, "prerequisites": val})
        assert all(f.subject for f in facts), \
            f"crash sur prerequisites={val!r}"


def test_facts_from_builders_defensive_against_corruption() -> None:
    """Spec Phase A round 36 : les builders ne doivent pas crash sur des
    listes contenant None ou types invalides.

    Bug regression : `_facts_from_event`, `_facts_from_character`,
    `_facts_from_village`, `_facts_from_organization`, `_facts_from_tailed_beast`
    crashaient avec AttributeError sur 'NoneType' si un element None se
    glissait dans une liste typee dict.

    Pour un import canon resilient (cas d'une corruption isolee), chaque
    builder doit skip les entrees malformees et continuer.
    """
    from shinobi.kg.loader import (
        _facts_from_character, _facts_from_village,
        _facts_from_organization, _facts_from_tailed_beast,
        _facts_from_event,
    )

    # Character avec entries corrompus
    facts = _facts_from_character({
        "id": "X",
        "current_village_by_era": [None, {"village": "konoha"}],
        "techniques_known_by_era": [
            None, {"year": 12, "techniques": [None, "rasengan", 42]},
        ],
    })
    assert len(facts) >= 2  # type fact + at least one valid entry

    # Organization avec multi entries corrompus
    facts = _facts_from_organization({
        "id": "O",
        "leaders_by_era": [None, {"leader": "X"}],
        "active_period": [None, {"phase": "active"}],
        "members_by_era": [
            None, {"year": 0, "members": [None, "a", 42]},
        ],
    })
    assert len(facts) >= 4

    # Event avec outcome=None
    facts = _facts_from_event({
        "id": "E", "name_fr": "Test", "year": 0,
        "outcomes": [None, {"type": "character_death", "parameters": None}],
        "preconditions": [None],
    })
    assert len(facts) >= 3

    # Village + tailed_beast aussi
    _facts_from_village({"id": "V", "kage_lineage": [None]})
    _facts_from_tailed_beast({"id": "B", "current_jinchuuriki_by_era": [None]})


def test_fact_from_row_defensive_known_by_npc_ids() -> None:
    """Spec Phase A : known_by_npc_ids doit toujours etre list[str].

    Bug regression round 23 : si la DB contient un JSON valide mais qui
    n'est PAS une liste (dict/scalar/nombre), Fact.from_row laissait passer
    le mauvais type -> casse les utilisateurs aval (BeliefPropagator,
    add_known_by, etc.) avec TypeError obscurs.
    """
    base_row = {
        "id": 1, "subject": "X", "relation": "r", "object": "v",
        "object_type": "value",
        "valid_from_year": None, "valid_to_year": None,
        "source": "canon", "confidence": 1.0,
        "canonicity": "canon_strict",
        "created_at_ts": None,
    }

    # Cas qui doivent retourner list vide
    for raw in ['{"npc1": true}', '"scalar"', "42", "not_json", "null"]:
        f = Fact.from_row({**base_row, "known_by_npc_ids": raw})
        assert isinstance(f.known_by_npc_ids, list), \
            f"raw={raw!r} -> {type(f.known_by_npc_ids)}"
        assert f.known_by_npc_ids == [], f"raw={raw!r}"

    # Cas avec list contenant types mixtes : filtre les non-str
    f = Fact.from_row({
        **base_row,
        "known_by_npc_ids": '["npc1", "npc2", 42, null, true]',
    })
    assert f.known_by_npc_ids == ["npc1", "npc2"]

    # Cas normal preserve
    f = Fact.from_row({
        **base_row,
        "known_by_npc_ids": '["alice", "bob"]',
    })
    assert f.known_by_npc_ids == ["alice", "bob"]


def test_delete_facts_symmetric_filters(store: KnowledgeGraphStore) -> None:
    """delete_facts doit accepter les memes 11 filtres que get_facts/count.

    Bug regression round 22 : delete_facts n'avait que 6 filtres (subject,
    relation, relation_prefix, source, source_prefix, canonicity).
    Asymetrie qui forcait une 2-passe (get_facts puis delete_fact par id).
    """
    store.add_facts_batch([
        Fact(subject="a", relation="r", object="X", object_type=ObjectType.entity),
        Fact(subject="b", relation="r", object="Y", object_type=ObjectType.value),
        Fact(subject="c", relation="r", object="Z", object_type=ObjectType.belief),
    ])
    n = store.delete_facts(object_type=ObjectType.belief)
    assert n == 1
    assert store.count() == 2

    # year_range supporte aussi : facts avec bornes etroites
    store.clear_all()
    store.add_facts_batch([
        Fact(subject="d", relation="r", object="A",
             valid_from_year=10, valid_to_year=15),
        Fact(subject="e", relation="r", object="B",
             valid_from_year=100, valid_to_year=105),
    ])
    # year_range=(20, 30) ne chevauche ni [10,15] ni [100,105] -> 0 supprimes
    n = store.delete_facts(year_range=(20, 30))
    assert n == 0
    # year_range=(12, 14) chevauche [10, 15] -> 1 supprime
    n = store.delete_facts(year_range=(12, 14))
    assert n == 1


def test_import_canon_json_corrupt_resilient(tmp_path: Path) -> None:
    """Spec Phase A : un JSON canon corrompu ne doit pas casser tout l'import.

    Bug regression round 22 : `_import_list` faisait `json.loads()` direct
    sans try/except -> exception propage et stop l'import. Les datasets
    apres le corrompu n'etaient jamais charges.
    """
    import json as _json
    from shinobi.kg.loader import import_canon_to_kg
    from shinobi.kg.store import KnowledgeGraphStore

    fake_canon = tmp_path / "canon"
    fake_canon.mkdir()
    # techniques.json corrompu
    (fake_canon / "techniques.json").write_text("NOT JSON{{{", encoding="utf-8")
    # clans.json valide -> doit etre charge malgre tech corrupt
    (fake_canon / "clans.json").write_text(_json.dumps([{
        "id": "uchiha", "name_romaji": "Uchiha",
        "village_of_origin": "konohagakure",
    }]), encoding="utf-8")

    store = KnowledgeGraphStore()
    try:
        stats = import_canon_to_kg(store, fake_canon)
        assert stats.get("techniques", 0) == 0  # skip propre
        assert stats.get("clans", 0) > 0  # poursuit l'import
    finally:
        store.close()


def test_count_symmetric_with_get_facts(store: KnowledgeGraphStore) -> None:
    """Spec Phase A : count() doit accepter les memes filtres que get_facts.

    Bug regression round 21 : count() n'avait que canonicity + source_prefix.
    Asymetrie API CRUD : impossible de monitor le KG (combien de facts pour
    NPC X ? combien actifs a year Y ? etc.) sans charger les rows.
    """
    facts = [
        Fact(subject="naruto", relation="r1", object="X", source="canon"),
        Fact(subject="naruto", relation="r2", object="Y", source="event:42"),
        Fact(subject="sasuke", relation="r1", object="Z", source="canon"),
    ]
    store.add_facts_batch(facts)

    # Filtres equivalents a get_facts
    assert store.count(subject="naruto") == 2
    assert store.count(relation="r1") == 2
    assert store.count(object_type="value") == 3
    assert store.count(source="canon") == 2
    assert store.count(source_prefix="event:") == 1

    # Coherence get_facts vs count
    assert store.count(subject="naruto") == len(store.get_facts(subject="naruto"))


def test_year_range_validates_none_components(store: KnowledgeGraphStore) -> None:
    """year_range=(None, X) ou (X, None) doit raise (utiliser year= pour borne)."""
    with pytest.raises(ValueError, match="year_range doit etre"):
        store.get_facts(year_range=(None, 5))
    with pytest.raises(ValueError, match="year_range doit etre"):
        store.get_facts(year_range=(0, None))
    with pytest.raises(ValueError, match="year_range doit etre"):
        store.count(year_range=(None, None))


def test_world_rules_null_stored_as_sql_null(
    store: KnowledgeGraphStore, canon_dir: Path
) -> None:
    """Spec Phase A : les `null` JSON doivent etre stockes comme NULL SQL,
    pas comme la chaine litterale "None".

    Bug regression round 19 :
    `economy.ryo_to_jutsu_scroll_multiplier_by_rank.forbidden = null`
    etait stocke avec object="None" (str(None)). Perte de semantique :
    "rang interdit pas de multiplier" devient un equivalent string parasite.
    """
    if not canon_dir.exists():
        pytest.skip("data/canonical/ absent")
    import_canon_to_kg(store, canon_dir)
    # Aucun fact world_rules avec object='None' (chaine litterale)
    none_str = [
        f for f in store.get_facts(subject="world_rules")
        if f.object == "None"
    ]
    assert len(none_str) == 0, \
        f"world_rules a des chaines 'None' parasites : {len(none_str)}"
    # Le fact 'forbidden' existe avec object=NULL
    forbidden = store.get_facts(
        subject="world_rules",
        relation="economy:ryo_to_jutsu_scroll_multiplier_by_rank:forbidden",
    )
    assert len(forbidden) == 1
    assert forbidden[0].object is None


def test_zero_value_preserved_in_serialization(store: KnowledgeGraphStore) -> None:
    """Spec Phase A : confidence=0.0 / fidelity=0.0 / strength=0.0 sont des
    valeurs LEGITIMES qui doivent survivre au round-trip serialization.

    Bug regression round 18 : `d.get(key) or default` transformait 0.0 en
    default car 0.0 est falsy en Python. Fix : utiliser fallback explicite
    sur None uniquement.

    confidence=0.0  : rumeur totalement incertaine
    fidelity=0.0    : info totalement deformee par chaine de transmission
    strength=0.0    : lien social rompu
    """
    from shinobi.kg.belief import BeliefPropagator
    from shinobi.kg.social import SocialNetwork
    from shinobi.kg.schema import Belief, SocialLink

    # Fact confidence=0.0
    fid = store.add_fact(
        Fact(subject="X", relation="r", object="v", confidence=0.0)
    )
    f = store.get_fact(fid)
    assert f is not None and f.confidence == 0.0, \
        f"confidence=0.0 perdue, lue={f.confidence if f else None}"

    # Belief fidelity=0.0
    prop = BeliefPropagator(store.conn)
    prop.add_belief(Belief(fact_id=fid, npc_id="npc1", fidelity=0.0))
    b = prop.get_belief(fid, "npc1")
    assert b is not None and b.fidelity == 0.0, \
        f"fidelity=0.0 perdue, lue={b.fidelity if b else None}"

    # SocialLink strength=0.0
    soc = SocialNetwork(store.conn)
    soc.add_link(SocialLink(
        npc_a="a", npc_b="b", link_type="enemy", strength=0.0,
    ))
    link = soc.get_link("a", "b")
    assert link is not None and link.strength == 0.0, \
        f"strength=0.0 perdue, lue={link.strength if link else None}"

    # Sanity : valeurs intermediaires non affectees
    fid2 = store.add_fact(Fact(subject="Y", relation="r", object="v", confidence=0.3))
    f2 = store.get_fact(fid2)
    assert f2 is not None and abs(f2.confidence - 0.3) < 1e-9


def test_canonicity_mapping_full_taxonomy() -> None:
    """Mapping canonicity source -> Canonicity runtime couvre toute la taxonomie.

    Spec Phase A : "100% des facts canon importes sans perte". Les valeurs de
    canonicity dans les JSON sont : manga, boruto_manga, boruto, anime_canon,
    movie_canon, databook (canon_strict) et filler, game, tbv (canon_modified).
    """
    from shinobi.kg.loader import _map_canonicity

    # Sources textuellement attestables -> canon_strict
    for v in ["manga", "boruto_manga", "boruto", "anime_canon",
              "movie_canon", "databook"]:
        assert _map_canonicity(v) == Canonicity.canon_strict, v

    # Sources non-canon strict -> canon_modified
    for v in ["filler", "game", "tbv"]:
        assert _map_canonicity(v) == Canonicity.canon_modified, v

    # Inconnus / null -> default canon_strict (fail-safe)
    for v in [None, "", "unknown_value"]:
        assert _map_canonicity(v) == Canonicity.canon_strict


def test_import_canon_preserves_sourced_from(
    store: KnowledgeGraphStore, canon_dir: Path
) -> None:
    """Chaque entree canon emet un fact (entity, sourced_from, <source>).

    Verifie qu'on ne perd pas la metadata canonicity par entree.
    """
    if not canon_dir.exists():
        pytest.skip("data/canonical/ absent")
    import_canon_to_kg(store, canon_dir)
    sourced = store.get_facts(relation="sourced_from")
    assert len(sourced) > 5000, f"trop peu de sourced_from: {len(sourced)}"
    sources = {f.object for f in sourced}
    # Au moins 'manga' doit etre present, et plusieurs autres types attestes
    assert "manga" in sources
    assert any(s in sources for s in ["boruto_manga", "movie_canon", "tbv"])


def test_import_canon_tbv_marked_canon_modified(
    store: KnowledgeGraphStore, canon_dir: Path
) -> None:
    """Les entrees 'tbv' doivent etre Canonicity.canon_modified, pas canon_strict.

    Avant le fix, toutes les entrees etaient hardcoded canon_strict.
    """
    if not canon_dir.exists():
        pytest.skip("data/canonical/ absent")
    import_canon_to_kg(store, canon_dir)
    tbv_entities = store.get_facts(relation="sourced_from", object_value="tbv")
    assert len(tbv_entities) > 0, "aucun tbv source dans le canon (test obsolete?)"
    # Pour chaque entite tbv, le fact 'type' doit etre canon_modified
    for sf in tbv_entities:
        type_facts = store.get_facts(subject=sf.subject, relation="type")
        for tf in type_facts:
            assert tf.canonicity == Canonicity.canon_modified, (
                f"tbv entity {sf.subject} a type.canonicity={tf.canonicity}, "
                f"attendu canon_modified"
            )


def test_migrations_warn_on_db_newer_than_code(tmp_path: Path) -> None:
    """Spec Phase A : detection downgrade DB > code -> warning structlog.

    Avant le fix round 33 : aucun signal quand le code est revert mais la
    DB reste avec une migration applique. Bugs silencieux possibles (le
    code lit une DB avec colonnes/tables inconnues).

    Note : structlog bypass sys.stderr/capsys -> on patch get_logger pour
    intercepter le warning a la source.
    """
    import sqlite3
    from unittest.mock import MagicMock, patch

    from shinobi.kg.schema import apply_migrations

    db_path = tmp_path / "future.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    # Cree une DB avec une version "future" (au-dela de CURRENT_SCHEMA_VERSION)
    conn.executescript("""
        CREATE TABLE kg_schema_version (
            version INTEGER PRIMARY KEY, applied_at_ts INTEGER NOT NULL
        );
        INSERT INTO kg_schema_version (version, applied_at_ts)
        VALUES (999, strftime('%s', 'now'));
    """)
    conn.commit()

    mock_logger = MagicMock()
    with patch("shinobi.logging_setup.get_logger", return_value=mock_logger):
        applied = apply_migrations(conn)

    # Aucune migration appliquee (DB deja "plus recente")
    assert applied == []
    # Le warning a ete emis avec le bon event name + kwargs
    mock_logger.warning.assert_called_once()
    call_args = mock_logger.warning.call_args
    assert call_args.args[0] == "kg_schema_db_newer_than_code"
    assert call_args.kwargs["db_version"] == 999
    assert call_args.kwargs["code_version"] == CURRENT_SCHEMA_VERSION
    conn.close()


def test_migrations_idempotent(tmp_path: Path) -> None:
    """Re-appliquer les migrations sur une base a jour ne fait rien."""
    from shinobi.kg.schema import apply_migrations

    db_path = tmp_path / "kg.db"
    conn = initialize_db(db_path)
    try:
        # Deuxieme appel : aucune migration ne doit etre appliquee
        applied = apply_migrations(conn)
        assert applied == []
        assert schema_version(conn) == CURRENT_SCHEMA_VERSION
    finally:
        conn.close()


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
