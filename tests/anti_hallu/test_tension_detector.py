"""Tests du TensionDetector et du LLMTensionAnalyst (Phase C).

Couvre :
- TensionDetector : orchestration + skip + tri
- TensionList : top, by_type, merge
- SnapshotBuilder : sections du snapshot
- LLMTensionAnalyst : mode offline (no client), parsing valide,
  parsing invalide, schema strict
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from shinobi.kg import (
    Canonicity,
    Fact,
    KnowledgeGraphStore,
    ObjectType,
)
from shinobi.tension import (
    LLMAnalystConfig,
    LLMTensionAnalyst,
    SnapshotBuilder,
    Tension,
    TensionDetector,
    TensionList,
    TensionSeverity,
    TensionType,
)
from shinobi.tension.invariants import INVARIANTS


@pytest.fixture
def store() -> KnowledgeGraphStore:
    s = KnowledgeGraphStore(None)
    yield s
    s.close()


def add(s: KnowledgeGraphStore, **kwargs) -> int:
    kwargs.setdefault("source", "canon")
    kwargs.setdefault("canonicity", Canonicity.canon_strict)
    kwargs.setdefault("object_type", ObjectType.value)
    return s.add_fact(Fact(**kwargs))


# === TensionDetector =======================================================


def test_detector_runs_all_invariants(store: KnowledgeGraphStore) -> None:
    """Avec un KG vide on peut au moins ne pas crasher."""
    detector = TensionDetector(store)
    result = detector.detect(year=12)
    assert isinstance(result, TensionList)


def test_detector_finds_tensions_in_canon_scenario(store: KnowledgeGraphStore) -> None:
    """Scenario canon massacre Uchiha (year 8) : doit detecter plusieurs tensions."""
    # Massacre Uchiha : clan reduit a 2 (Itachi + Sasuke)
    add(store, subject="uchiha", relation="type", object="clan")
    add(store, subject="itachi", relation="clan", object="uchiha", object_type=ObjectType.entity)
    add(store, subject="sasuke", relation="clan", object="uchiha", object_type=ObjectType.entity)
    add(store, subject="sasuke", relation="lone_survivor_of", object="uchiha",
        object_type=ObjectType.entity)
    add(store, subject="sasuke", relation="deep_motivation", object="revenge_against_itachi")
    # Sharingan dernier porteur
    add(store, subject="sharingan", relation="type", object="kekkei_genkai")
    add(store, subject="sasuke", relation="has_kekkei_genkai", object="sharingan",
        object_type=ObjectType.entity)

    detector = TensionDetector(store)
    result = detector.detect(year=8)
    types = {t.type for t in result.tensions}
    assert TensionType.lone_survivor_obsessed in types
    assert TensionType.clan_extinction_threat in types


def test_detector_skip_invariants(store: KnowledgeGraphStore) -> None:
    """L'option skip_invariants doit ignorer les regles nommees."""
    detector = TensionDetector(store)
    result = detector.detect(
        year=12, skip_invariants=("kage_absent_or_dead", "power_vacuum_global"),
    )
    sources = {t.source_rule for t in result.tensions}
    assert "kage_absent_or_dead" not in sources
    assert "power_vacuum_global" not in sources


def test_detector_top_n(store: KnowledgeGraphStore) -> None:
    """Detect_with_top doit retourner au plus n tensions, triees par score."""
    add(store, subject="uchiha", relation="type", object="clan")
    add(store, subject="sasuke", relation="clan", object="uchiha", object_type=ObjectType.entity)
    add(store, subject="sasuke", relation="lone_survivor_of", object="uchiha",
        object_type=ObjectType.entity)
    add(store, subject="sasuke", relation="deep_motivation", object="revenge")
    detector = TensionDetector(store)
    top = detector.detect_with_top(year=8, n=3)
    assert len(top) <= 3
    # Tri decroissant
    scores = [t.score for t in top]
    assert scores == sorted(scores, reverse=True)


def test_detector_robust_to_failing_invariant(
    store: KnowledgeGraphStore, monkeypatch,
) -> None:
    """Si une regle leve une exception, les autres continuent."""
    from shinobi.tension.invariants import TensionInvariant

    def broken(s, y, c):
        raise RuntimeError("boom")

    inv = TensionInvariant("broken_test", "broken", broken)
    detector = TensionDetector(store, invariants=(inv, *INVARIANTS))
    result = detector.detect(year=12)
    # Doit avoir au moins 0 ou plus tensions, sans crash
    assert isinstance(result, TensionList)


# === TensionList ===========================================================


def test_tension_list_top() -> None:
    tl = TensionList(tensions=[
        Tension.from_severity(
            type=TensionType.power_vacuum, description="Vide pol critical",
            severity=TensionSeverity.critical,
        ),
        Tension.from_severity(
            type=TensionType.death_anniversary, description="Anniversaire 5e",
            severity=TensionSeverity.medium,
        ),
        Tension.from_severity(
            type=TensionType.cursed_hatred, description="Haine montante",
            severity=TensionSeverity.high,
        ),
    ])
    top2 = tl.top(2)
    assert len(top2) == 2
    assert top2[0].severity == TensionSeverity.critical
    assert top2[1].severity == TensionSeverity.high


def test_tension_list_by_type() -> None:
    tl = TensionList(tensions=[
        Tension.from_severity(type=TensionType.power_vacuum, description="x" * 20,
                               severity=TensionSeverity.high),
        Tension.from_severity(type=TensionType.death_anniversary, description="y" * 20,
                               severity=TensionSeverity.medium),
        Tension.from_severity(type=TensionType.power_vacuum, description="z" * 20,
                               severity=TensionSeverity.low),
    ])
    pv = tl.by_type(TensionType.power_vacuum)
    assert len(pv) == 2
    assert tl.total() == 3


def test_tension_list_merge() -> None:
    a = TensionList(tensions=[
        Tension.from_severity(type=TensionType.power_vacuum, description="abc def ghi",
                               severity=TensionSeverity.high),
    ], detected_at_year=10)
    b = TensionList(tensions=[
        Tension.from_severity(type=TensionType.cursed_hatred, description="def ghi jkl",
                               severity=TensionSeverity.medium),
    ], detected_at_year=10)
    merged = a.merge(b)
    assert merged.total() == 2
    assert merged.detected_at_year == 10


# === SnapshotBuilder =======================================================


def test_snapshot_builder_basic_format(store: KnowledgeGraphStore) -> None:
    add(store, subject="naruto", relation="type", object="character")
    add(store, subject="naruto", relation="clan", object="uzumaki", object_type=ObjectType.entity)
    add(store, subject="naruto", relation="village_of_origin", object="konohagakure",
        object_type=ObjectType.entity)
    builder = SnapshotBuilder(store)
    snap = builder.build(year=12)
    assert "Snapshot du monde - an 12" in snap
    assert "naruto" in snap


def test_snapshot_includes_anniversaries(store: KnowledgeGraphStore) -> None:
    add(store, subject="minato", relation="death_year", object="0")
    builder = SnapshotBuilder(store)
    snap = builder.build(year=10)  # 10e anniversaire
    assert "anniversaire" in snap.lower()


def test_snapshot_includes_recent_events(store: KnowledgeGraphStore) -> None:
    add(store, subject="uchiha_massacre", relation="type", object="timeline_event")
    add(store, subject="uchiha_massacre", relation="name_fr",
        object="Massacre du clan Uchiha")
    add(store, subject="uchiha_massacre", relation="occurs_in_year", object="8",
        valid_from_year=8)
    builder = SnapshotBuilder(store)
    snap = builder.build(year=12)
    assert "Evenements recents" in snap


def test_snapshot_handles_empty_kg(store: KnowledgeGraphStore) -> None:
    builder = SnapshotBuilder(store)
    snap = builder.build(year=10)
    # Doit pas crasher, retourne au moins le header
    assert "Snapshot du monde" in snap


def test_snapshot_includes_relations_when_social_network_provided(
    store: KnowledgeGraphStore,
) -> None:
    """Spec §5.3 : 'top-50 PNJ + leurs etats + RELATIONS + events recents'.
    Quand SocialNetwork est fourni au SnapshotBuilder, les relations entre
    top NPCs apparaissent dans le snapshot."""
    from shinobi.kg.schema import SocialLink
    from shinobi.kg.social import SocialNetwork

    add(store, subject="naruto", relation="type", object="character")
    add(store, subject="sasuke", relation="type", object="character")
    add(store, subject="naruto", relation="clan", object="uzumaki",
        object_type=ObjectType.entity)
    add(store, subject="sasuke", relation="clan", object="uchiha",
        object_type=ObjectType.entity)

    net = SocialNetwork(store.conn)
    net.add_link(SocialLink(
        npc_a="naruto", npc_b="sasuke",
        link_type="rival", strength=0.85,
    ))

    builder = SnapshotBuilder(store, social_network=net)
    snap = builder.build(year=12)
    assert "Relations sociales clefs" in snap
    assert "naruto" in snap and "sasuke" in snap
    assert "rival" in snap


def test_snapshot_no_relations_section_without_social_network(
    store: KnowledgeGraphStore,
) -> None:
    """Sans SocialNetwork (default), pas de section relations (back-compat)."""
    add(store, subject="naruto", relation="type", object="character")
    builder = SnapshotBuilder(store)  # social_network=None
    snap = builder.build(year=12)
    assert "Relations sociales" not in snap


def test_play_cli_print_tensions_runs_detector(tmp_path) -> None:
    """Spec §5.3 + §13 : TensionDetector wired via CLI /tensions command.
    Verifie que _print_tensions() se branche au KG de la save sans crash."""
    from shinobi.cli.play import _print_tensions
    from shinobi.config import settings

    # Pas de KG -> message yellow non-crash
    original_saves = settings.saves_path
    settings.saves_path = str(tmp_path)
    try:
        # Save_id qui n'existe pas : doit gracefully informer
        _print_tensions("nonexistent_save", year=12)
    finally:
        settings.saves_path = original_saves


# === LLMTensionAnalyst (offline) ===========================================


@pytest.mark.asyncio
async def test_llm_analyst_no_client_returns_empty(
    store: KnowledgeGraphStore,
) -> None:
    """Sans LLM client, doit retourner TensionList vide sans crash."""
    analyst = LLMTensionAnalyst(store, llm_client=None)
    result = await analyst.analyze(year=12)
    assert isinstance(result, TensionList)
    assert result.total() == 0
    assert result.detected_at_year == 12


@pytest.mark.asyncio
async def test_llm_analyst_parses_valid_response(
    store: KnowledgeGraphStore,
) -> None:
    """Avec une reponse LLM valide, parse en Tensions."""
    fake_client = AsyncMock()
    fake_response = AsyncMock()
    fake_response.parsed_json = {
        "tensions": [
            {
                "type": "power_vacuum",
                "description": "Vide politique apres mort de Hiruzen",
                "severity": "high",
                "involved_entities": ["konohagakure"],
            },
            {
                "type": "factional_revenge",
                "description": "Le clan Uzumaki cherche vengeance",
                "severity": "medium",
            },
        ],
        "summary": "Konoha fragile",
    }
    fake_client.generate = AsyncMock(return_value=fake_response)

    analyst = LLMTensionAnalyst(store, llm_client=fake_client)
    result = await analyst.analyze(year=12)
    assert result.total() == 2
    types = [t.type for t in result.tensions]
    assert TensionType.power_vacuum in types
    assert TensionType.factional_revenge in types


@pytest.mark.asyncio
async def test_llm_analyst_handles_unknown_type(
    store: KnowledgeGraphStore,
) -> None:
    """Un type inconnu retourne par le LLM est rabattu sur 'other'."""
    fake_client = AsyncMock()
    fake_response = AsyncMock()
    fake_response.parsed_json = {
        "tensions": [{
            "type": "totally_invented_type",
            "description": "Quelque chose se trame en coulisses",
            "severity": "low",
        }],
    }
    fake_client.generate = AsyncMock(return_value=fake_response)

    analyst = LLMTensionAnalyst(store, llm_client=fake_client)
    result = await analyst.analyze(year=10)
    assert result.total() == 1
    assert result.tensions[0].type == TensionType.other


@pytest.mark.asyncio
async def test_llm_analyst_handles_invalid_severity(
    store: KnowledgeGraphStore,
) -> None:
    """Severity invalide -> fallback medium."""
    fake_client = AsyncMock()
    fake_response = AsyncMock()
    fake_response.parsed_json = {
        "tensions": [{
            "type": "power_vacuum",
            "description": "Vide politique",
            "severity": "ULTRA_MEGA_HIGH",
        }],
    }
    fake_client.generate = AsyncMock(return_value=fake_response)

    analyst = LLMTensionAnalyst(store, llm_client=fake_client)
    result = await analyst.analyze(year=10)
    assert result.tensions[0].severity == TensionSeverity.medium


@pytest.mark.asyncio
async def test_llm_analyst_handles_llm_error(
    store: KnowledgeGraphStore,
) -> None:
    """Si le LLM plante, on retourne TensionList vide."""
    from shinobi.errors import LLMUnavailableError

    fake_client = AsyncMock()
    fake_client.generate = AsyncMock(side_effect=LLMUnavailableError("server down"))
    analyst = LLMTensionAnalyst(store, llm_client=fake_client)
    result = await analyst.analyze(year=10)
    assert result.total() == 0


@pytest.mark.asyncio
async def test_llm_analyst_handles_none_response(
    store: KnowledgeGraphStore,
) -> None:
    """Reponse None ou parsed_json None -> empty list."""
    fake_client = AsyncMock()
    fake_response = AsyncMock()
    fake_response.parsed_json = None
    fake_client.generate = AsyncMock(return_value=fake_response)
    analyst = LLMTensionAnalyst(store, llm_client=fake_client)
    result = await analyst.analyze(year=10)
    assert result.total() == 0


def test_llm_analyst_config_defaults() -> None:
    cfg = LLMAnalystConfig()
    assert cfg.interval_months_in_game == 3
    assert cfg.snapshot_top_npcs == 50


def test_llm_analyst_build_snapshot_public(store: KnowledgeGraphStore) -> None:
    """build_snapshot est utilisable sans client."""
    add(store, subject="naruto", relation="type", object="character")
    analyst = LLMTensionAnalyst(store, llm_client=None)
    snap = analyst.build_snapshot(year=12)
    assert "naruto" in snap or "Snapshot" in snap


# === Phase H wiring 9.3 : political_alliance_brittle_via_dead_leader =======


def test_phase_h_9_3_political_invariant_detects_dead_leader_alliance(
    store: KnowledgeGraphStore,
) -> None:
    """Phase H 9.3 : alliance_breakdown via leader mort + canon political_forces."""
    from shinobi.tension.invariants import (
        political_alliance_brittle_via_dead_leader,
    )

    political_forces = {
        "factions": [
            {
                "id": "uchiha_clan",
                "leader_id": "uchiha_fugaku",
                "allies": ["konohagakure"],
                "enemies": [],
                "active_year_start": -50,
                "active_year_end": None,
            },
            {
                "id": "konohagakure",
                "leader_id": "sarutobi_hiruzen",
                "allies": ["uchiha_clan"],
                "enemies": [],
                "active_year_start": -65,
                "active_year_end": None,
            },
        ],
    }
    char_deaths = {"uchiha_fugaku": 9}  # mort en l'an 9

    tensions = political_alliance_brittle_via_dead_leader(
        store, year=12,  # 3 ans apres mort fugaku
        ctx={
            "political_forces": political_forces,
            "char_deaths": char_deaths,
        },
    )
    assert len(tensions) >= 1
    t = tensions[0]
    assert t.type == TensionType.alliance_breakdown
    assert "uchiha_clan" in t.involved_entities
    assert "konohagakure" in t.involved_entities
    assert "uchiha_fugaku" in t.involved_entities


def test_phase_h_9_3_political_invariant_skips_when_ctx_empty(
    store: KnowledgeGraphStore,
) -> None:
    """Phase H 9.3 : sans ctx['political_forces'], la regle fire pas."""
    from shinobi.tension.invariants import (
        political_alliance_brittle_via_dead_leader,
    )
    assert political_alliance_brittle_via_dead_leader(
        store, year=12, ctx={},
    ) == []


def test_phase_h_9_3_political_invariant_skips_when_leader_alive(
    store: KnowledgeGraphStore,
) -> None:
    """Phase H 9.3 : si leader vivant, pas de fragility detectee."""
    from shinobi.tension.invariants import (
        political_alliance_brittle_via_dead_leader,
    )
    political_forces = {
        "factions": [
            {
                "id": "uchiha_clan",
                "leader_id": "uchiha_fugaku",
                "allies": ["konohagakure"],
                "active_year_start": -50,
                "active_year_end": None,
            },
            {
                "id": "konohagakure",
                "leader_id": "sarutobi_hiruzen",
                "allies": ["uchiha_clan"],
                "active_year_start": -65,
                "active_year_end": None,
            },
        ],
    }
    # Fugaku mort dans le futur (year=20 > 12), donc pas mort a year=12
    char_deaths = {"uchiha_fugaku": 20}
    tensions = political_alliance_brittle_via_dead_leader(
        store, year=12,
        ctx={
            "political_forces": political_forces,
            "char_deaths": char_deaths,
        },
    )
    assert tensions == []


def test_phase_h_9_3_political_invariant_skips_inactive_factions(
    store: KnowledgeGraphStore,
) -> None:
    """Phase H 9.3 : faction dissoute (active_year_end < year) skip."""
    from shinobi.tension.invariants import (
        political_alliance_brittle_via_dead_leader,
    )
    political_forces = {
        "factions": [
            {
                "id": "old_clan",
                "leader_id": "dead_leader",
                "allies": ["other_clan"],
                "active_year_start": -200,
                "active_year_end": -10,  # dissoute avant year=12
            },
            {
                "id": "other_clan",
                "leader_id": None,
                "allies": [],
                "active_year_start": -200,
                "active_year_end": None,
            },
        ],
    }
    char_deaths = {"dead_leader": -50}
    tensions = political_alliance_brittle_via_dead_leader(
        store, year=12,
        ctx={
            "political_forces": political_forces,
            "char_deaths": char_deaths,
        },
    )
    assert tensions == []


def test_phase_h_9_3_detector_uses_canon_ctx(
    store: KnowledgeGraphStore,
) -> None:
    """Phase H 9.3 : TensionDetector(canon=...) populate auto le ctx."""

    class _FakeChar:
        def __init__(self, dy: int | None) -> None:
            self.death_year = dy

    class _FakeCanon:
        def __init__(self) -> None:
            self.political_forces = {
                "factions": [
                    {
                        "id": "uchiha_clan",
                        "leader_id": "uchiha_fugaku",
                        "allies": ["konohagakure"],
                        "active_year_start": -50,
                        "active_year_end": None,
                    },
                    {
                        "id": "konohagakure",
                        "leader_id": "sarutobi_hiruzen",
                        "allies": ["uchiha_clan"],
                        "active_year_start": -65,
                        "active_year_end": None,
                    },
                ],
            }
            self.characters = {
                "uchiha_fugaku": _FakeChar(9),
                "sarutobi_hiruzen": _FakeChar(13),
            }

    detector = TensionDetector(store, canon=_FakeCanon())
    result = detector.detect(year=12)
    political_tensions = [
        t for t in result.tensions
        if t.source_rule == "political_alliance_brittle_via_dead_leader"
    ]
    assert len(political_tensions) >= 1


def test_phase_h_9_3_detector_no_canon_no_political_tensions(
    store: KnowledgeGraphStore,
) -> None:
    """Phase H 9.3 : sans canon=, pas de political_alliance fire (back-compat)."""
    detector = TensionDetector(store)
    result = detector.detect(year=12)
    political_tensions = [
        t for t in result.tensions
        if t.source_rule == "political_alliance_brittle_via_dead_leader"
    ]
    assert len(political_tensions) == 0


def test_phase_h_9_3_build_canon_ctx_handles_none() -> None:
    """Phase H 9.3 : build_canon_ctx(None) -> dict vide."""
    from shinobi.tension.detector import build_canon_ctx
    assert build_canon_ctx(None) == {}


def test_phase_h_9_3_invariant_in_default_registry() -> None:
    """Phase H 9.3 : la 21eme regle est bien dans INVARIANTS."""
    names = [inv.name for inv in INVARIANTS]
    assert "political_alliance_brittle_via_dead_leader" in names


def test_phase_c_perf_detect_under_threshold(
    store: KnowledgeGraphStore,
) -> None:
    """Phase C perf : TensionDetector.detect() < 5ms par appel.

    Garde-fou contre O(N^2) regressions dans les 22 invariants. Reference
    materiel local : ~0.18ms/detect avec canon production charge. Cap a
    5ms pour large marge sur CI partages.
    """
    import time

    from shinobi.canon.loader import load_canon

    canon = load_canon()
    detector = TensionDetector(store, canon=canon)
    # Warm-up : 1er detect a un cout d'init Pydantic
    detector.detect(year=10)

    N = 50
    t0 = time.perf_counter()
    for _ in range(N):
        detector.detect(year=10)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    per_detect_ms = elapsed_ms / N
    assert per_detect_ms < 5.0, (
        f"Phase C perf regression : {per_detect_ms:.2f}ms/detect > 5.0ms "
        f"(reference : ~0.18ms/detect avec canon production)"
    )


def test_phase_h_9_3_isolated_faction_with_active_enemies(
    store: KnowledgeGraphStore,
) -> None:
    """Phase H 9.3 (suite) : faction sans leader + >=2 ennemis -> tension high."""
    from shinobi.tension.invariants import (
        political_faction_isolated_with_active_enemies,
    )

    political_forces = {
        "factions": [
            {
                "id": "uchiha_clan",
                "leader_id": "uchiha_fugaku",
                "allies": [],
                "enemies": ["konohagakure", "senju_clan"],
                "active_year_start": -50,
                "active_year_end": None,
            },
            {
                "id": "konohagakure",
                "leader_id": "sarutobi_hiruzen",
                "allies": [], "enemies": ["uchiha_clan"],
                "active_year_start": -65, "active_year_end": None,
            },
            {
                "id": "senju_clan",
                "leader_id": "senju_hashirama",
                "allies": [], "enemies": ["uchiha_clan"],
                "active_year_start": -100, "active_year_end": None,
            },
        ],
    }
    char_deaths = {"uchiha_fugaku": 9}
    tensions = political_faction_isolated_with_active_enemies(
        store, year=12,
        ctx={
            "political_forces": political_forces,
            "char_deaths": char_deaths,
        },
    )
    assert len(tensions) >= 1
    t = tensions[0]
    assert t.type == TensionType.factional_revenge
    assert t.severity == TensionSeverity.high
    assert "uchiha_clan" in t.involved_entities
    assert "konohagakure" in t.involved_entities or (
        "senju_clan" in t.involved_entities
    )


def test_phase_h_9_3_isolated_faction_skips_when_only_one_enemy(
    store: KnowledgeGraphStore,
) -> None:
    """Phase H 9.3 (suite) : 1 seul ennemi = pas de threshold atteint."""
    from shinobi.tension.invariants import (
        political_faction_isolated_with_active_enemies,
    )

    political_forces = {
        "factions": [
            {
                "id": "uchiha_clan",
                "leader_id": "uchiha_fugaku",
                "allies": [], "enemies": ["konohagakure"],
                "active_year_start": -50, "active_year_end": None,
            },
            {
                "id": "konohagakure",
                "leader_id": "sarutobi_hiruzen",
                "allies": [], "enemies": [],
                "active_year_start": -65, "active_year_end": None,
            },
        ],
    }
    char_deaths = {"uchiha_fugaku": 9}
    tensions = political_faction_isolated_with_active_enemies(
        store, year=12,
        ctx={
            "political_forces": political_forces,
            "char_deaths": char_deaths,
        },
    )
    assert tensions == []


def test_phase_h_9_5_nudge_includes_canon_example() -> None:
    """Phase H 9.5 : canon_examples ancre le pattern dans un cas canonique.

    Sans exemple, le pattern reste abstrait. Avec 1 exemple canon (Itachi,
    Sasuke, Obito...) le LLM voit comment l'imiter directement.
    """
    from shinobi.director.nudge_builder import build_nudge, build_nudge_text

    nudge = build_nudge(
        active_acts=[],
        active_invariants=[],
        recent_summary=None,
        current_year=10,
        narrative_patterns=[
            {
                "id": "p1",
                "title_fr": "Revelation en couches",
                "description_fr": "Reserver une verite cachee.",
                "when_to_apply_fr": "Quand un personnage semble juge.",
                "canon_examples": [
                    "Itachi : presente comme traitre puis revele protecteur",
                    "Tobi revele etre Obito Uchiha",
                ],
            },
        ],
    )
    text = build_nudge_text(nudge)
    assert "Ex. canon :" in text
    assert "Itachi" in text


def test_phase_h_9_5_nudge_omits_canon_example_when_absent() -> None:
    """Phase H 9.5 : pas de ligne 'Ex. canon' si canon_examples vide."""
    from shinobi.director.nudge_builder import build_nudge, build_nudge_text

    nudge = build_nudge(
        active_acts=[], active_invariants=[],
        recent_summary=None, current_year=10,
        narrative_patterns=[
            {
                "id": "p1", "title_fr": "T", "description_fr": "D",
                # PAS de canon_examples
            },
        ],
    )
    text = build_nudge_text(nudge)
    assert "Ex. canon :" not in text


def test_phase_h_9_5_nudge_includes_when_to_apply() -> None:
    """Phase H 9.5 : when_to_apply_fr injecte dans le nudge sous chaque pattern.

    Sans cette ligne, le LLM avait le pattern + description mais pas le
    contexte d'application -> patterns lus comme decoratifs au lieu d'etre
    actionables.
    """
    from shinobi.director.nudge_builder import build_nudge, build_nudge_text

    nudge = build_nudge(
        active_acts=[],
        active_invariants=[],
        recent_summary=None,
        current_year=10,
        narrative_patterns=[
            {
                "id": "p1",
                "title_fr": "Revelation en couches",
                "description_fr": "Reserver une verite cachee.",
                "when_to_apply_fr": (
                    "Quand un personnage semble juge : reveler une verite."
                ),
            },
        ],
    )
    text = build_nudge_text(nudge)
    assert "Revelation en couches" in text
    assert "Quand :" in text
    assert "Quand un personnage semble juge" in text


def test_phase_h_9_3_scheduler_propagates_canon_to_detector(
    store: KnowledgeGraphStore,
) -> None:
    """Phase H 9.3 : TensionScheduler(canon=) populate son detector interne.

    Garantit que le wiring CLI (qui passe canon au scheduler) active la
    21eme regle pour toute la duree du fast-forward.
    """
    from shinobi.tension.scheduler import TensionScheduler

    class _FakeChar:
        def __init__(self, dy: int | None) -> None:
            self.death_year = dy

    class _FakeCanon:
        political_forces = {
            "factions": [
                {
                    "id": "uchiha_clan",
                    "leader_id": "uchiha_fugaku",
                    "allies": ["konohagakure"],
                    "active_year_start": -50,
                    "active_year_end": None,
                },
                {
                    "id": "konohagakure",
                    "leader_id": "sarutobi_hiruzen",
                    "allies": ["uchiha_clan"],
                    "active_year_start": -65,
                    "active_year_end": None,
                },
            ],
        }
        characters = {
            "uchiha_fugaku": _FakeChar(9),
            "sarutobi_hiruzen": _FakeChar(13),
        }

    scheduler = TensionScheduler(store, canon=_FakeCanon())
    # Acces direct au detector pour verifier qu'il a bien le ctx canon
    detector = scheduler._detector  # noqa: SLF001
    assert "political_forces" in detector._canon_ctx  # noqa: SLF001
    assert "char_deaths" in detector._canon_ctx  # noqa: SLF001
    assert detector._canon_ctx["char_deaths"]["uchiha_fugaku"] == 9  # noqa: SLF001
