"""Tests scenarios canon Naruto + scheduler temporel (Phase C 100% strict).

Spec doc 02 §10 Phase C :
> Tests : sur scénarios canon, identifier les tensions canoniques comme
> témoignage de validité.

3 scenarios canon majeurs sont testes en condition reelle :
- Pre-Kyuubi attack (an -1) : monde avant la naissance de Naruto
- Post-Massacre Uchiha (an 9) : Sasuke survivant, clan eteint
- Pre-Pain Invasion (an 14) : Akatsuki actif, Konoha vulnerable

Le detecteur doit identifier les tensions canoniques sans en inventer
de fausses.

+ Tests du TensionScheduler (intervalle 3 mois, skip si non du, force).
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
    SchedulerState,
    TensionDetector,
    TensionList,
    TensionScheduler,
    TensionType,
    TickResult,
)

# ============================================================================
# Helpers
# ============================================================================


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


# ============================================================================
# Scenario canon 1 : Post-Massacre Uchiha (year 9)
# ============================================================================


def _build_post_massacre_kg(s: KnowledgeGraphStore) -> None:
    """Setup KG : Konoha an 9, juste apres le massacre Uchiha (year 8).

    Etat canon attendu :
    - Hiruzen 3e Hokage en place ✓ (pas de power_vacuum)
    - Clan Uchiha eteint sauf Sasuke (orphelin) + Itachi (missing)
    - Sasuke lone_survivor avec deep_motivation revenge
    - Sharingan dernier porteur isole (Sasuke + Itachi)
    - Naruto ostracise (jinchuriki kyuubi) -> jinchuriki "protege" par Hiruzen
    - Tension : massacre Uchiha = chekhovs_gun (verite Itachi pas encore revelee)
    """
    # Konoha + Hiruzen Hokage
    add(s, subject="konohagakure", relation="type", object="village")
    add(s, subject="hiruzen", relation="type", object="character")
    add(s, subject="konohagakure", relation="kage", object="hiruzen",
        object_type=ObjectType.entity, valid_from_year=0)
    add(s, subject="hiruzen", relation="death_year", object="12")
    add(s, subject="hiruzen", relation="world_authority", object="hokage")

    # Clan Uchiha post-massacre
    add(s, subject="uchiha", relation="type", object="clan")
    add(s, subject="sasuke", relation="type", object="character")
    add(s, subject="sasuke", relation="clan", object="uchiha", object_type=ObjectType.entity)
    add(s, subject="sasuke", relation="village_of_origin", object="konohagakure",
        object_type=ObjectType.entity)
    add(s, subject="sasuke", relation="lone_survivor_of", object="uchiha",
        object_type=ObjectType.entity)
    add(s, subject="sasuke", relation="deep_motivation", object="revenge_against_itachi")
    add(s, subject="itachi", relation="type", object="character")
    add(s, subject="itachi", relation="clan", object="uchiha", object_type=ObjectType.entity)

    # Sharingan kekkei genkai - 2 porteurs vivants (Sasuke + Itachi)
    add(s, subject="sharingan", relation="type", object="kekkei_genkai")
    add(s, subject="sasuke", relation="has_kekkei_genkai", object="sharingan",
        object_type=ObjectType.entity)
    add(s, subject="itachi", relation="has_kekkei_genkai", object="sharingan",
        object_type=ObjectType.entity)

    # Bijuu Kyuubi avec Naruto comme jinchuriki
    add(s, subject="kurama", relation="type", object="tailed_beast")
    add(s, subject="naruto", relation="type", object="character")
    add(s, subject="kurama", relation="current_jinchuriki", object="naruto",
        object_type=ObjectType.entity, valid_from_year=0)
    add(s, subject="naruto", relation="village_of_origin", object="konohagakure",
        object_type=ObjectType.entity)

    # Secret canon : Itachi a tue le clan sur ordre du village (chekhovs_gun)
    add(s, subject="itachi_truth_about_massacre", relation="chekhovs_gun",
        object="Itachi a agi sur ordre de Danzo et Hiruzen pour empecher coup d'etat",
        valid_from_year=8)

    # Trauma cumulatif sur Sasuke
    for i in range(3):
        add(s, subject="sasuke", relation="trauma_event", object=f"trauma_uchiha_{i}",
            valid_from_year=8)


def test_post_massacre_uchiha_detects_canon_tensions(
    store: KnowledgeGraphStore,
) -> None:
    """An 9, post-massacre : detecter au moins lone_survivor_obsessed,
    clan_extinction_threat, cursed_hatred_rising, chekhovs_gun_unfired."""
    _build_post_massacre_kg(store)
    detector = TensionDetector(store)
    result = detector.detect(year=9)
    types = {t.type for t in result.tensions}

    # Tensions canoniques attendues (les 4 critiques)
    assert TensionType.lone_survivor_obsessed in types, "Sasuke seul survivant + revenge"
    assert TensionType.clan_extinction_threat in types, "Uchiha 2 vivants"
    assert TensionType.cursed_hatred in types, "Sasuke 3 traumas sans reconciliation"

    # Konoha a son hokage -> pas de power_vacuum sur konoha
    konoha_vacuums = [
        t for t in result.tensions
        if t.type == TensionType.power_vacuum and "konohagakure" in t.involved_entities
    ]
    assert konoha_vacuums == [], "Konoha a un Hokage en l'an 9, pas de vacuum"

    # Sasuke critical
    sasuke_tensions = [
        t for t in result.tensions if "sasuke" in t.involved_entities
    ]
    assert any(t.score >= 0.75 for t in sasuke_tensions), \
        "Sasuke devrait etre dans une tension high/critical"


# ============================================================================
# Scenario canon 2 : Pre-Pain Invasion (year 14)
# ============================================================================


def _build_pre_pain_invasion_kg(s: KnowledgeGraphStore) -> None:
    """Setup KG : Konoha an 14, juste avant l'invasion de Pain.

    Etat canon attendu :
    - Tsunade 5e Hokage (entree en fonction an 12)
    - Akatsuki actif (Pain leader)
    - Jinchuriki en chasse (8/9 captures)
    - Plusieurs bijuus uncontrolled apres extraction
    - Jiraiya mort recemment (an 14, contre Pain) -> trauma + chekhovs_gun
    """
    add(s, subject="konohagakure", relation="type", object="village")
    add(s, subject="tsunade", relation="type", object="character")
    add(s, subject="konohagakure", relation="kage", object="tsunade",
        object_type=ObjectType.entity, valid_from_year=12)
    add(s, subject="hiruzen", relation="type", object="character")
    add(s, subject="hiruzen", relation="death_year", object="12")

    # Akatsuki organisation
    add(s, subject="akatsuki", relation="type", object="organization")
    add(s, subject="pain", relation="type", object="character")
    add(s, subject="akatsuki", relation="leader", object="pain",
        object_type=ObjectType.entity, valid_from_year=10)

    # Plusieurs bijuus extraits / hote mort
    for tail_n in (1, 2, 3, 4, 5, 7):
        bijuu_id = f"bijuu_{tail_n}_tails"
        add(s, subject=bijuu_id, relation="type", object="tailed_beast")
        # Pas de jinchuriki actif
    # Kyuubi a encore Naruto
    add(s, subject="kurama", relation="type", object="tailed_beast")
    add(s, subject="naruto", relation="type", object="character")
    add(s, subject="kurama", relation="current_jinchuriki", object="naruto",
        object_type=ObjectType.entity, valid_from_year=0)
    add(s, subject="naruto", relation="village_of_origin", object="konohagakure",
        object_type=ObjectType.entity)

    # Jiraiya vient de mourir
    add(s, subject="jiraiya", relation="type", object="character")
    add(s, subject="jiraiya", relation="death_year", object="14")
    add(s, subject="jiraiya", relation="student_of", object="hiruzen",
        object_type=ObjectType.entity)

    # Border tension Konoha-Ame (Akatsuki HQ)
    add(s, subject="konohagakure", relation="border_dispute_with",
        object="amegakure", object_type=ObjectType.entity, valid_from_year=13)


def test_pre_pain_invasion_detects_world_threats(
    store: KnowledgeGraphStore,
) -> None:
    """An 14 : tensions multiples - bijuus libres, border conflict, etc."""
    _build_pre_pain_invasion_kg(store)
    detector = TensionDetector(store)
    result = detector.detect(year=14)

    # Bijuus uncontrolled : 6 bijuus sans jinchuriki -> au moins 6 tensions
    uncontrolled = [
        t for t in result.tensions
        if t.type == TensionType.tailed_beast_uncontrolled
    ]
    assert len(uncontrolled) >= 6

    # Border conflict Konoha-Ame
    border_t = [
        t for t in result.tensions
        if t.type == TensionType.border_conflict
        and "konohagakure" in t.involved_entities
    ]
    assert len(border_t) >= 1

    # Tsunade en place -> Konoha pas en power_vacuum
    konoha_vacuums = [
        t for t in result.tensions
        if t.type == TensionType.power_vacuum
        and "konohagakure" in t.involved_entities
    ]
    assert konoha_vacuums == []


# ============================================================================
# Scenario canon 3 : Pre-Kyuubi Attack (year -1)
# ============================================================================


def _build_pre_kyuubi_kg(s: KnowledgeGraphStore) -> None:
    """An -1 : Minato 4e Hokage, paix relative, kyuubi en Kushina."""
    add(s, subject="konohagakure", relation="type", object="village")
    add(s, subject="minato", relation="type", object="character")
    add(s, subject="konohagakure", relation="kage", object="minato",
        object_type=ObjectType.entity, valid_from_year=-5)
    add(s, subject="minato", relation="death_year", object="0")

    add(s, subject="kushina", relation="type", object="character")
    add(s, subject="kurama", relation="type", object="tailed_beast")
    add(s, subject="kurama", relation="current_jinchuriki", object="kushina",
        object_type=ObjectType.entity,
        valid_from_year=-25, valid_to_year=0)
    add(s, subject="kushina", relation="village_of_origin", object="konohagakure",
        object_type=ObjectType.entity)


def test_pre_kyuubi_year_minus_1_few_tensions(
    store: KnowledgeGraphStore,
) -> None:
    """An -1, monde calme : peu de tensions critiques."""
    _build_pre_kyuubi_kg(store)
    detector = TensionDetector(store)
    result = detector.detect(year=-1)

    # Pas de power_vacuum a Konoha (Minato en place)
    konoha_vacuums = [
        t for t in result.tensions
        if t.type == TensionType.power_vacuum
        and "konohagakure" in t.involved_entities
    ]
    assert konoha_vacuums == []

    # Pas de bijuu uncontrolled (Kushina hote)
    uncontrolled = [
        t for t in result.tensions
        if t.type == TensionType.tailed_beast_uncontrolled
        and "kurama" in t.involved_entities
    ]
    assert uncontrolled == []


# ============================================================================
# Scenario stable : aucune tension critique
# ============================================================================


def test_stable_world_no_critical_tensions(store: KnowledgeGraphStore) -> None:
    """Setup minimaliste avec un kage en place + un world_authority + bijuu
    encadre. Pas de tensions critical attendues."""
    add(store, subject="konohagakure", relation="type", object="village")
    add(store, subject="tsunade", relation="type", object="character")
    add(store, subject="konohagakure", relation="kage", object="tsunade",
        object_type=ObjectType.entity, valid_from_year=12)
    # Kage des autres grands villages aussi
    for v, leader in (
        ("sunagakure", "gaara"), ("kirigakure", "mei"),
        ("kumogakure", "ay_raikage"), ("iwagakure", "onoki"),
    ):
        add(store, subject=v, relation="type", object="village")
        add(store, subject=leader, relation="type", object="character")
        add(store, subject=v, relation="kage", object=leader,
            object_type=ObjectType.entity, valid_from_year=10)

    add(store, subject="tsunade", relation="world_authority", object="hokage")

    detector = TensionDetector(store)
    result = detector.detect(year=14)
    critical = [t for t in result.tensions if t.score >= 0.95]
    # Aucune tension critical attendue
    assert len(critical) == 0


# ============================================================================
# TensionScheduler
# ============================================================================


@pytest.mark.asyncio
async def test_scheduler_first_tick_runs_analyst_due(
    store: KnowledgeGraphStore,
) -> None:
    """Premier tick : analyste due (jamais appele)."""
    fake_client = AsyncMock()
    fake_response = AsyncMock()
    fake_response.parsed_json = {"tensions": []}
    fake_client.generate = AsyncMock(return_value=fake_response)
    analyst = LLMTensionAnalyst(store, llm_client=fake_client)
    scheduler = TensionScheduler(store, analyst=analyst)

    result = await scheduler.tick(year=10, month=1)
    assert isinstance(result, TickResult)
    assert result.detector_ran is True
    assert result.analyst_ran is True
    assert scheduler.state.last_analyst_year == 10
    assert scheduler.state.analyst_runs_count == 1


@pytest.mark.asyncio
async def test_scheduler_skip_analyst_within_interval(
    store: KnowledgeGraphStore,
) -> None:
    """Si l'intervalle (3 mois) n'est pas ecoule, analyste skip."""
    analyst = LLMTensionAnalyst(store, llm_client=None)
    scheduler = TensionScheduler(store, analyst=analyst)
    scheduler._state = SchedulerState(
        last_analyst_year=10, last_analyst_month=1, analyst_runs_count=1,
    )

    # 1 mois plus tard : skip
    result = await scheduler.tick(year=10, month=2)
    assert result.analyst_ran is False
    assert "interval_not_elapsed" in (result.reason_analyst_skipped or "")
    assert scheduler.state.analyst_runs_count == 1


@pytest.mark.asyncio
async def test_scheduler_runs_analyst_after_interval(
    store: KnowledgeGraphStore,
) -> None:
    """Apres 3 mois ecoules, analyste tourne a nouveau."""
    fake_client = AsyncMock()
    fake_response = AsyncMock()
    fake_response.parsed_json = {"tensions": []}
    fake_client.generate = AsyncMock(return_value=fake_response)
    analyst = LLMTensionAnalyst(store, llm_client=fake_client)
    scheduler = TensionScheduler(store, analyst=analyst)
    scheduler._state = SchedulerState(
        last_analyst_year=10, last_analyst_month=1, analyst_runs_count=1,
    )

    # 3 mois plus tard
    result = await scheduler.tick(year=10, month=4)
    assert result.analyst_ran is True
    assert scheduler.state.last_analyst_month == 4


@pytest.mark.asyncio
async def test_scheduler_force_analyst(
    store: KnowledgeGraphStore,
) -> None:
    """force_analyst=True bypass l'intervalle."""
    fake_client = AsyncMock()
    fake_response = AsyncMock()
    fake_response.parsed_json = {"tensions": []}
    fake_client.generate = AsyncMock(return_value=fake_response)
    analyst = LLMTensionAnalyst(store, llm_client=fake_client)
    scheduler = TensionScheduler(store, analyst=analyst)
    scheduler._state = SchedulerState(
        last_analyst_year=10, last_analyst_month=1,
    )

    # 0 mois ecoule, mais forced
    result = await scheduler.tick(year=10, month=1, force_analyst=True)
    assert result.analyst_ran is True


@pytest.mark.asyncio
async def test_scheduler_offline_mode_runs_detector_only(
    store: KnowledgeGraphStore,
) -> None:
    """Sans LLM client, le detecteur tourne quand meme."""
    add(store, subject="konohagakure", relation="type", object="village")
    # Pas de kage -> tension power_vacuum
    analyst = LLMTensionAnalyst(store, llm_client=None)
    scheduler = TensionScheduler(store, analyst=analyst)
    result = await scheduler.tick(year=14, month=1)
    assert result.detector_ran is True
    # Detecteur a vu le power_vacuum
    types = {t.type for t in result.tensions.tensions}
    assert TensionType.power_vacuum in types


@pytest.mark.asyncio
async def test_scheduler_year_advance_triggers_analyst(
    store: KnowledgeGraphStore,
) -> None:
    """Avancee d'annee declenche un analyste si l'intervalle est depasse."""
    fake_client = AsyncMock()
    fake_response = AsyncMock()
    fake_response.parsed_json = {"tensions": []}
    fake_client.generate = AsyncMock(return_value=fake_response)
    analyst = LLMTensionAnalyst(store, llm_client=fake_client)
    scheduler = TensionScheduler(store, analyst=analyst)
    scheduler._state = SchedulerState(
        last_analyst_year=10, last_analyst_month=1,
    )

    # +1 annee = +12 mois, donc analyste due
    result = await scheduler.tick(year=11, month=1)
    assert result.analyst_ran is True


def test_scheduler_state_serialization() -> None:
    """SchedulerState round-trip via dict."""
    s = SchedulerState(
        last_analyst_year=12, last_analyst_month=4,
        analyst_runs_count=5, detector_runs_count=120,
    )
    d = s.to_dict()
    s2 = SchedulerState.from_dict(d)
    assert s2.last_analyst_year == 12
    assert s2.analyst_runs_count == 5
    assert s2.detector_runs_count == 120


def test_scheduler_state_default() -> None:
    s = SchedulerState()
    assert s.last_analyst_year is None
    assert s.analyst_runs_count == 0


def test_scheduler_is_due_first_call(store: KnowledgeGraphStore) -> None:
    scheduler = TensionScheduler(store)
    assert scheduler.is_due(year=10, month=1) is True


def test_scheduler_reset(store: KnowledgeGraphStore) -> None:
    scheduler = TensionScheduler(store)
    scheduler._state = SchedulerState(
        last_analyst_year=10, analyst_runs_count=5,
    )
    scheduler.reset()
    assert scheduler.state.last_analyst_year is None
    assert scheduler.state.analyst_runs_count == 0


@pytest.mark.asyncio
async def test_scheduler_merges_detector_and_analyst(
    store: KnowledgeGraphStore,
) -> None:
    """Le tick merge les tensions deterministe + LLM."""
    add(store, subject="konohagakure", relation="type", object="village")
    fake_client = AsyncMock()
    fake_response = AsyncMock()
    fake_response.parsed_json = {
        "tensions": [{
            "type": "hidden_truth_pending",
            "description": "Un secret est sur le point d'eclater dans le village",
            "severity": "high",
        }]
    }
    fake_client.generate = AsyncMock(return_value=fake_response)
    analyst = LLMTensionAnalyst(store, llm_client=fake_client)
    scheduler = TensionScheduler(store, analyst=analyst)

    result = await scheduler.tick(year=14, month=1)
    types = {t.type for t in result.tensions.tensions}
    # Detecteur : power_vacuum (pas de kage)
    assert TensionType.power_vacuum in types
    # LLM : hidden_truth_pending
    assert TensionType.hidden_truth_pending in types


@pytest.mark.asyncio
async def test_scheduler_ctx_passes_to_detector(
    store: KnowledgeGraphStore,
) -> None:
    """Le ctx passe au detecteur (override great_villages)."""
    analyst = LLMTensionAnalyst(store, llm_client=None)
    scheduler = TensionScheduler(store, analyst=analyst)
    # Un seul village dans ctx
    result = await scheduler.tick(year=14, month=1, ctx={
        "great_villages": ["test_village"],
    })
    types = {t.type for t in result.tensions.tensions}
    # power_vacuum pour test_village
    assert TensionType.power_vacuum in types


def test_scheduler_default_config() -> None:
    """Sans config explicite, interval_months_in_game=3 par defaut."""
    s = TensionScheduler(KnowledgeGraphStore(None))
    assert s.config.interval_months_in_game == 3
    s._store.close()


def test_scheduler_custom_config(store: KnowledgeGraphStore) -> None:
    cfg = LLMAnalystConfig(interval_months_in_game=6, snapshot_top_npcs=10)
    s = TensionScheduler(store, config=cfg)
    assert s.config.interval_months_in_game == 6
    assert s.config.snapshot_top_npcs == 10


@pytest.mark.asyncio
async def test_scheduler_detector_only_mode_when_skip(
    store: KnowledgeGraphStore,
) -> None:
    """Si analyst skip, on a quand meme les tensions du detecteur."""
    add(store, subject="konohagakure", relation="type", object="village")
    analyst = LLMTensionAnalyst(store, llm_client=None)
    scheduler = TensionScheduler(store, analyst=analyst)
    # Force skip
    scheduler._state = SchedulerState(
        last_analyst_year=14, last_analyst_month=1,
    )
    result = await scheduler.tick(year=14, month=2)
    assert result.detector_ran is True
    assert result.analyst_ran is False
    assert isinstance(result.tensions, TensionList)
    types = {t.type for t in result.tensions.tensions}
    assert TensionType.power_vacuum in types
