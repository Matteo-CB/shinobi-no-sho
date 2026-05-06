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
