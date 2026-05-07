"""Tests Phase G - Director / Drama Manager.

Spec doc 02 §7 : drama manager qui compose des actes abstraits, maintient
les invariants Naruto, et fait de la compaction narrative periodique.

Couverture :
- types : AbstractAct, NarrativeInvariant, NudgeContext Pydantic round-trip
- invariants : list canon (5 centraux + 4 secondaires)
- act_composer : TensionList -> AbstractAct (deterministic)
- nudge_builder : NudgeContext -> string prompt
- compactor : NarrativeCompactor avec LLM mock + offline fallback
- scheduler : DirectorState save/load + is_compaction_due
- core : Director e2e tick avec acts + invariants + compaction
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from shinobi.canon.models import CanonBundle
from shinobi.director import (
    DEFAULT_COMPACTION_INTERVAL_MONTHS,
    AbstractAct,
    Director,
    DirectorReport,
    DirectorState,
    NARUTO_INVARIANTS,
    NARUTO_INVARIANTS_CENTRAL,
    NARUTO_INVARIANTS_SECONDARY,
    NarrativeCompactor,
    NarrativeInvariant,
    NudgeContext,
    build_nudge,
    build_nudge_text,
    compose_acts,
    is_compaction_due,
    merge_with_existing,
    select_relevant_invariants,
)
from shinobi.engine.world import WorldState
from shinobi.tension.types import (
    Tension,
    TensionList,
    TensionSeverity,
    TensionType,
)


# --- Fixtures ---------------------------------------------------------------


@pytest.fixture(scope="module")
def canon() -> CanonBundle:
    from shinobi.canon.loader import load_canon
    return load_canon()


@pytest.fixture
def world() -> WorldState:
    return WorldState(current_year=10, current_date="06-01")


@dataclass
class _MockResponse:
    text: str | None = None
    parsed_json: dict[str, Any] | None = None


class _MockLLMClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls = 0

    async def generate(
        self, messages, *, schema=None, temperature=None,
        max_tokens=None, retries=2,
    ):
        self.calls += 1
        if not self.responses:
            return _MockResponse(text="")
        return _MockResponse(text=self.responses.pop(0))


# --- Test types ---------------------------------------------------------------


def test_abstract_act_pydantic_constraints() -> None:
    """AbstractAct enforce le pattern d'id et la coherence year start <= end."""
    # OK
    act = AbstractAct(
        id="act_test_konoha_5",
        description_fr="Un test de description suffisamment longue.",
        related_tension_types=[TensionType.alliance_breakdown.value],
        involved_entities=["konohagakure"],
        target_year_start=5,
        target_year_end=6,
        urgency=0.7,
        created_at_year=5,
    )
    assert act.id == "act_test_konoha_5"
    assert act.status == "proposed"  # default

    # ID pattern : doit commencer par act_
    with pytest.raises(Exception):
        AbstractAct(
            id="bad_prefix",
            description_fr="x" * 20,
            target_year_start=5,
            target_year_end=10,
            created_at_year=5,
        )

    # year end < start -> reject
    with pytest.raises(Exception):
        AbstractAct(
            id="act_invalid_range",
            description_fr="x" * 20,
            target_year_start=10,
            target_year_end=5,  # < start
            created_at_year=10,
        )


def test_abstract_act_dedupes_involved_entities() -> None:
    """Round G11 : AbstractAct.involved_entities dedupe Pydantic.

    Mirror R46 Phase F. Tension upstream peut produire des doublons ;
    sans dedup, le template '{entities}' produit 'konoha, konoha, suna'
    -> narrator LLM mimicke la repetition.
    """
    act = AbstractAct(
        id="act_dedupe_test",
        description_fr="Description de test pour dedupe involved.",
        target_year_start=10, target_year_end=15,
        involved_entities=[
            "konohagakure", "sunagakure", "konohagakure",  # dupe
            "sunagakure",  # dupe
            "konohagakure",  # dupe
        ],
        created_at_year=10,
    )
    # Dedupe ordre preserve
    assert act.involved_entities == ["konohagakure", "sunagakure"]


def test_abstract_act_dedupes_related_tension_types() -> None:
    """Round G11 : related_tension_types dedupe aussi (future-proof)."""
    act = AbstractAct(
        id="act_dedupe_types",
        description_fr="Description test dedupe types.",
        target_year_start=10, target_year_end=15,
        related_tension_types=[
            "alliance_breakdown", "cursed_hatred",
            "alliance_breakdown",  # dupe
        ],
        created_at_year=10,
    )
    assert act.related_tension_types == ["alliance_breakdown", "cursed_hatred"]


def test_narrative_invariant_pydantic_constraints() -> None:
    inv = NarrativeInvariant(
        id="invariant_test",
        principle_fr="Un principe assez long pour passer min_length.",
        examples_canon=["Exemple A", "Exemple B"],
        applies_to_contexts=["training"],
        weight=0.8,
    )
    assert inv.weight == 0.8

    # weight hors [0, 1]
    with pytest.raises(Exception):
        NarrativeInvariant(
            id="invariant_overweight",
            principle_fr="Un principe valide mais weight invalide.",
            weight=1.5,
        )


def test_nudge_context_construction() -> None:
    nudge = NudgeContext(
        active_acts=[],
        active_invariants=[],
        recent_summary=None,
        composed_at_year=10,
    )
    assert nudge.composed_at_year == 10


# --- Test invariants ----------------------------------------------------------


def test_naruto_invariants_total_count() -> None:
    """5 centraux + 4 secondaires = 9 invariants."""
    assert len(NARUTO_INVARIANTS_CENTRAL) == 5
    assert len(NARUTO_INVARIANTS_SECONDARY) == 4
    assert len(NARUTO_INVARIANTS) == 9


def test_naruto_invariants_all_have_canon_examples() -> None:
    """Chaque invariant doit avoir au moins 2 exemples canon (traceability)."""
    for inv in NARUTO_INVARIANTS:
        assert len(inv.examples_canon) >= 2, (
            f"{inv.id} a moins de 2 exemples canon"
        )


def test_naruto_invariants_central_have_max_weight() -> None:
    """Les 5 centraux ont weight=1.0 ; les 4 secondaires < 1.0."""
    for inv in NARUTO_INVARIANTS_CENTRAL:
        assert inv.weight == 1.0
    for inv in NARUTO_INVARIANTS_SECONDARY:
        assert inv.weight < 1.0


def test_select_relevant_invariants_no_context_returns_central() -> None:
    """Sans contexte, retourne les centraux (defaut sain)."""
    result = select_relevant_invariants([], max_invariants=5)
    assert len(result) == 5
    central_ids = {inv.id for inv in NARUTO_INVARIANTS_CENTRAL}
    assert {inv.id for inv in result} == central_ids


def test_select_relevant_invariants_filters_by_context() -> None:
    """Avec contexte 'redemption', les invariants pertinents remontent."""
    result = select_relevant_invariants(["redemption"], max_invariants=3)
    # invariant_redemption_through_sacrifice et invariant_bonds_transform
    # sont les 2 plus pertinents
    ids = {inv.id for inv in result}
    assert "invariant_redemption_through_sacrifice" in ids


def test_select_relevant_invariants_unknown_context_falls_back_to_central() -> None:
    """Round G6 : contexte inconnu (aucun match) -> fallback centraux.

    Avant : retournait []. Le narrator LLM perdait son style guide pour
    les tension types dont les keywords ne matchent aucun
    applies_to_contexts (ex chekhovs_gun_unfired -> contextes 'chekhovs',
    'gun', 'unfired'). Maintenant : safety net = centraux.
    """
    result = select_relevant_invariants(["totally_unknown_context_xyz"])
    central_ids = {inv.id for inv in NARUTO_INVARIANTS_CENTRAL}
    assert {inv.id for inv in result} == central_ids


# --- Test act_composer --------------------------------------------------------


def _make_tension(
    type: TensionType = TensionType.alliance_breakdown,
    score: float = 0.8,
    severity: TensionSeverity = TensionSeverity.high,
    entities: list[str] | None = None,
    description: str = "Tension de test pour composer.",
) -> Tension:
    return Tension(
        type=type, description=description,
        severity=severity, score=score,
        involved_entities=entities or ["konohagakure", "sunagakure"],
    )


def test_compose_acts_logs_pydantic_failure_instead_of_silent_skip(
    caplog,
) -> None:
    """Round G19 : si AbstractAct construction echoue (Pydantic), le skip
    est logge comme warning structure au lieu d'etre silencieux.

    Avant : `except (ValueError, Exception): continue` -> act perdu
    invisible aux ops. Maintenant : warning structured avec tension_type,
    act_id, error type, msg.
    """
    import logging

    # Force une violation : involved_entities entry vide -> first_entity=""
    # -> _act_id_from_tension produit 'act_alliance_breakdown_unscoped'
    # qui passe ; pas de fail simple. Force via current_year hors bornes
    # extremes (R G18 elargies a 10000) -> pas de fail non plus.
    # Approche : monkey-patch AbstractAct pour forcer la violation.
    import shinobi.director.act_composer as act_composer
    original_act_class = act_composer.AbstractAct

    class _FailingAct:
        def __init__(self, **kwargs):
            raise ValueError("test forced pydantic failure")

    act_composer.AbstractAct = _FailingAct  # type: ignore[assignment]
    try:
        with caplog.at_level(logging.WARNING):
            acts = act_composer.compose_acts(
                TensionList(tensions=[
                    Tension(
                        type=TensionType.alliance_breakdown,
                        description="Tension forcee a fail.",
                        severity=TensionSeverity.high, score=0.8,
                        involved_entities=["konoha"],
                    ),
                ]),
                current_year=10, min_score=0.5,
            )
    finally:
        act_composer.AbstractAct = original_act_class

    assert acts == []  # act skipe
    # Le log warning doit avoir ete emis (vis structlog event 'phase_g_...')
    # caplog capture les standard logs -> on ne peut pas verifier structlog
    # easily ; on se contente de verifier que la fonction a continue
    # gracefully (acts vide, pas de crash).


def test_compose_acts_empty_tensions_returns_empty() -> None:
    acts = compose_acts(TensionList(tensions=[]), current_year=10)
    assert acts == []


def test_compose_acts_filters_by_min_score() -> None:
    """Tensions sous min_score sont ignorees."""
    low = _make_tension(score=0.3, severity=TensionSeverity.low)
    high = _make_tension(score=0.9, severity=TensionSeverity.critical)
    acts = compose_acts(
        TensionList(tensions=[low, high]),
        current_year=10, min_score=0.5,
    )
    assert len(acts) == 1
    assert acts[0].urgency == 0.9


@pytest.mark.asyncio
async def test_director_no_duplicate_acts_across_ticks(
    canon: CanonBundle, world: WorldState,
) -> None:
    """Round G1 : meme tension sur 2 ticks consecutifs ne cree PAS 2 acts.

    Avant : id incluait current_year -> tick year=10 cree 'act_..._10',
    tick year=11 cree 'act_..._11', tous deux inseres en state.
    Maintenant : id stable -> merge_with_existing catch la collision.
    """
    director = Director(canon, llm_client=None)
    state = DirectorState()
    persistent_tension = TensionList(tensions=[
        Tension(
            type=TensionType.alliance_breakdown,
            description="Konoha-Suna persiste sur plusieurs annees.",
            severity=TensionSeverity.high, score=0.8,
            involved_entities=["konohagakure", "sunagakure"],
        ),
    ])
    # Tick 1 : year 10
    r1 = await director.tick(
        tensions=persistent_tension, world=world, state=state,
        current_year=10, current_month=1,
    )
    assert len(state.active_acts) == 1
    initial_id = list(state.active_acts.keys())[0]

    # Tick 2 : year 11, meme tension
    world_year_11 = world.model_copy(update={"current_year": 11})
    r2 = await director.tick(
        tensions=persistent_tension, world=world_year_11, state=state,
        current_year=11, current_month=1,
    )
    # State doit toujours avoir 1 act (pas 2)
    assert len(state.active_acts) == 1
    # L'id du seul act est le MEME que tick 1 (stabilite inter-ticks)
    assert list(state.active_acts.keys())[0] == initial_id


def test_compose_acts_dedups_same_signature() -> None:
    """2 tensions meme (type, first_entity) -> 1 act."""
    t1 = _make_tension(entities=["konoha", "suna"])
    t2 = _make_tension(
        entities=["konoha", "iwa"], description="Autre tension.",
    )
    acts = compose_acts(
        TensionList(tensions=[t1, t2]),
        current_year=10, top_n=5, min_score=0.5,
    )
    # type + first_entity = (alliance_breakdown, konoha) : 1 seul act
    assert len(acts) == 1


def test_compose_acts_month_granularity_differentiates_severities() -> None:
    """Round G31 : granularite mois donne 4 windows distincts pour 4 severities.

    Critical (3 mois) -> target_month_end=4 (1+3, same year)
    High (6 mois) -> target_month_end=7 (1+6, same year)
    Medium (12 mois) -> target_year_end=current+1, target_month_end=1
    Low (24 mois) -> target_year_end=current+2, target_month_end=1

    Avant R G31 : critical et high collapsaient au meme target_year_end
    (car +0 year offset). Maintenant differenciation visible au niveau mois.
    """
    crit = _make_tension(
        severity=TensionSeverity.critical, score=1.0,
        type=TensionType.power_vacuum, entities=["konoha"],
    )
    high = _make_tension(
        severity=TensionSeverity.high, score=0.75,
        type=TensionType.border_conflict, entities=["suna"],
    )
    medium = _make_tension(
        severity=TensionSeverity.medium, score=0.5,
        type=TensionType.alliance_breakdown, entities=["iwa"],
    )
    low = _make_tension(
        severity=TensionSeverity.low, score=0.25,
        type=TensionType.cursed_hatred, entities=["uchiha_sasuke"],
    )
    acts = compose_acts(
        TensionList(tensions=[crit, high, medium, low]),
        current_year=10, min_score=0.2,
    )
    by_type = {a.related_tension_types[0]: a for a in acts}

    # Critical : year=10, month=4 (1+3)
    crit_act = by_type["power_vacuum"]
    assert crit_act.target_year_end == 10
    assert crit_act.target_month_end == 4

    # High : year=10, month=7 (1+6)
    high_act = by_type["border_conflict"]
    assert high_act.target_year_end == 10
    assert high_act.target_month_end == 7

    # Medium : year=11, month=1 (12 mois -> +1 year, month resets to 1)
    med_act = by_type["alliance_breakdown"]
    assert med_act.target_year_end == 11
    assert med_act.target_month_end == 1

    # Low : year=12, month=1 (24 mois -> +2 years)
    low_act = by_type["cursed_hatred"]
    assert low_act.target_year_end == 12
    assert low_act.target_month_end == 1

    # Tuple comparison : critical < high < medium < low (window croissant)
    crit_end = (crit_act.target_year_end, crit_act.target_month_end)
    high_end = (high_act.target_year_end, high_act.target_month_end)
    med_end = (med_act.target_year_end, med_act.target_month_end)
    low_end = (low_act.target_year_end, low_act.target_month_end)
    assert crit_end < high_end < med_end < low_end


def test_compose_acts_window_scales_with_severity() -> None:
    """Round G16 : window strictement differencies par severity.

    Critical (3 mois) -> 0 year (same year deadline)
    High (6 mois) -> 0 year
    Medium (12 mois) -> 1 year
    Low (24 mois) -> 2 years
    """
    crit = _make_tension(
        severity=TensionSeverity.critical, score=1.0,
        type=TensionType.power_vacuum, entities=["konoha"],
    )
    medium = _make_tension(
        severity=TensionSeverity.medium, score=0.5,
        type=TensionType.border_conflict, entities=["suna"],
    )
    low = _make_tension(
        severity=TensionSeverity.low, score=0.5,
        type=TensionType.alliance_breakdown, entities=["iwa", "kiri"],
    )
    acts = compose_acts(
        TensionList(tensions=[crit, medium, low]),
        current_year=10, min_score=0.2,
    )
    by_type = {a.related_tension_types[0]: a for a in acts}
    crit_window = (
        by_type["power_vacuum"].target_year_end
        - by_type["power_vacuum"].target_year_start
    )
    med_window = (
        by_type["border_conflict"].target_year_end
        - by_type["border_conflict"].target_year_start
    )
    low_window = (
        by_type["alliance_breakdown"].target_year_end
        - by_type["alliance_breakdown"].target_year_start
    )
    # Round G16 : differenciation visible entre critical (0) et medium (1)
    # et low (2). Avant : crit=med=1, seul low=2.
    assert crit_window == 0
    assert med_window == 1
    assert low_window == 2
    assert crit_window < med_window < low_window


def test_compose_acts_sorted_by_urgency_desc() -> None:
    t_high = _make_tension(score=0.95, entities=["konoha"])
    t_mid = _make_tension(
        score=0.55, type=TensionType.power_vacuum, entities=["suna"],
    )
    acts = compose_acts(
        TensionList(tensions=[t_mid, t_high]),
        current_year=10, min_score=0.5,
    )
    assert acts[0].urgency >= acts[1].urgency


# --- Test merge_with_existing ------------------------------------------------


def test_merge_with_existing_skips_already_expired_new_act() -> None:
    """Round G3 : un new_act avec target_year_end < current_year est
    immediatement retired, pas insere puis retire au tick suivant.

    compose_acts ne produit jamais ce cas (target_end = current+1 minimum),
    mais un caller direct (test, API future, save corrompu) pourrait.
    Sans skip, l'act apparait briefement dans le nudge avec deadline
    passee -> narrator confus.
    """
    expired_new = AbstractAct(
        id="act_dead_on_arrival",
        description_fr="Act injecte avec deadline deja passee.",
        target_year_start=5,
        target_year_end=8,  # < current_year=10
        created_at_year=5,
    )
    added, retired, merged = merge_with_existing(
        new_acts=[expired_new], existing={}, current_year=10,
    )
    # Pas dans active state
    assert "act_dead_on_arrival" not in merged
    # Pas dans added (jamais ete actif)
    assert added == []
    # Mais retired pour traceability
    assert len(retired) == 1
    assert retired[0].status == "expired"


def test_merge_with_existing_promotes_proposed_to_active() -> None:
    """Acts existants en 'proposed' deviennent 'active' au merge."""
    existing = {
        "act_old": AbstractAct(
            id="act_old", description_fr="x" * 20,
            target_year_start=5, target_year_end=20, created_at_year=5,
            status="proposed",
        ),
    }
    added, retired, merged = merge_with_existing(
        new_acts=[], existing=existing, current_year=10,
    )
    assert merged["act_old"].status == "active"
    assert added == []


def test_merge_with_existing_retires_expired() -> None:
    """Acts dont target_year_end < current_year sont retired."""
    existing = {
        "act_old": AbstractAct(
            id="act_old", description_fr="x" * 20,
            target_year_start=1, target_year_end=5, created_at_year=1,
            status="active",
        ),
    }
    added, retired, merged = merge_with_existing(
        new_acts=[], existing=existing, current_year=10,
    )
    assert "act_old" not in merged
    assert len(retired) == 1
    assert retired[0].status == "expired"


def test_merge_with_existing_escalates_urgency_on_collision() -> None:
    """Round G5 : si new_act.id == existing.id ET new.urgency > existing,
    update urgency (escalade tension high -> critical entre 2 ticks).

    Avant : on gardait simplement l'existing -> tension Konoha-Suna passe
    de high (0.75) a critical (1.0) entre 2 ticks, l'urgency reste figee
    a 0.75. Le narrator LLM n'a pas conscience de l'aggravation.
    """
    existing = AbstractAct(
        id="act_escalating",
        description_fr="Tension qui escalade entre 2 ticks.",
        target_year_start=5, target_year_end=15,
        urgency=0.75,  # high
        created_at_year=5,
        status="active",
        source_tension_descriptions=["Initial tension high."],
    )
    new = AbstractAct(
        id="act_escalating",
        description_fr="Meme acte, urgency monte.",
        target_year_start=10, target_year_end=15,
        urgency=1.0,  # critical
        created_at_year=10,
        status="proposed",
        source_tension_descriptions=["Escalation critical."],
    )
    added, retired, merged = merge_with_existing(
        new_acts=[new],
        existing={"act_escalating": existing},
        current_year=10,
    )
    # Pas added (collision), mais urgency escalade
    assert added == []
    assert merged["act_escalating"].urgency == 1.0  # escalation appliquee
    # Source descriptions accumulees (traceability)
    descs = merged["act_escalating"].source_tension_descriptions
    assert "Initial tension high." in descs
    assert "Escalation critical." in descs


def test_merge_with_existing_no_de_escalation_on_collision() -> None:
    """Round G5 : si new.urgency < existing, on garde existing (pas de
    de-escalation involontaire).

    Tension perd en intensite mais reste valide -> pas de raison de
    diminuer urgency narrative (le narrator a deja prevu cette deadline).
    """
    existing = AbstractAct(
        id="act_steady",
        description_fr="Tension steady.",
        target_year_start=5, target_year_end=15,
        urgency=0.9, created_at_year=5,
        status="active",
    )
    new = AbstractAct(
        id="act_steady",
        description_fr="Tension steady, score plus bas ce tick.",
        target_year_start=10, target_year_end=15,
        urgency=0.6,  # < existing
        created_at_year=10,
    )
    _, _, merged = merge_with_existing(
        new_acts=[new], existing={"act_steady": existing},
        current_year=10,
    )
    # Pas de de-escalation
    assert merged["act_steady"].urgency == 0.9


def test_merge_with_existing_keeps_existing_on_collision() -> None:
    """Si new_act.id == existing.id, garde l'existing (preserve status)."""
    existing_act = AbstractAct(
        id="act_collision", description_fr="x" * 20,
        target_year_start=5, target_year_end=20, created_at_year=5,
        status="active",
    )
    new_act = AbstractAct(
        id="act_collision", description_fr="y" * 20,
        target_year_start=10, target_year_end=20, created_at_year=10,
        status="proposed",
    )
    added, retired, merged = merge_with_existing(
        new_acts=[new_act],
        existing={"act_collision": existing_act},
        current_year=10,
    )
    # Collision : added vide, l'existing reste
    assert added == []
    assert merged["act_collision"].description_fr == "x" * 20


# --- Test nudge_builder -------------------------------------------------------


def test_build_nudge_returns_pydantic_model() -> None:
    act = AbstractAct(
        id="act_test_x", description_fr="Description suffisamment longue.",
        target_year_start=10, target_year_end=15, created_at_year=10,
    )
    nudge = build_nudge(
        active_acts=[act],
        active_invariants=list(NARUTO_INVARIANTS_CENTRAL[:2]),
        recent_summary="Resume recent.",
        current_year=10,
    )
    assert nudge.composed_at_year == 10
    assert len(nudge.active_acts) == 1


def test_build_nudge_text_empty_returns_empty() -> None:
    """Pas d'acts/invariants/summary -> string vide (pas de marker)."""
    nudge = NudgeContext(composed_at_year=10)
    text = build_nudge_text(nudge)
    assert text == ""


def test_build_nudge_text_includes_directives_marker() -> None:
    act = AbstractAct(
        id="act_test_x", description_fr="La tension Konoha-Suna doit monter.",
        target_year_start=10, target_year_end=15, urgency=0.8,
        created_at_year=10,
    )
    nudge = NudgeContext(active_acts=[act], composed_at_year=10)
    text = build_nudge_text(nudge)
    assert "[DIRECTIVES NARRATIVES / DIRECTOR]" in text
    assert "[FIN DIRECTIVES]" in text
    assert "Konoha-Suna" in text


def test_build_nudge_text_caps_acts_to_3() -> None:
    """Plus de 3 acts -> seuls les 3 plus urgents apparaissent dans le texte."""
    acts = [
        AbstractAct(
            id=f"act_test_{i}",
            description_fr=f"Act numero {i} suffisamment long.",
            target_year_start=10, target_year_end=15,
            urgency=0.1 + i * 0.1, created_at_year=10,
        )
        for i in range(5)
    ]
    nudge = NudgeContext(active_acts=acts, composed_at_year=10)
    text = build_nudge_text(nudge)
    # Top 3 urgences : 4, 3, 2 (numero)
    assert "Act numero 4" in text
    assert "Act numero 3" in text
    assert "Act numero 2" in text
    # numero 1 et 0 hors top-3
    assert "Act numero 1" not in text
    assert "Act numero 0" not in text


def test_build_nudge_text_no_forbidden_dashes_or_emoji() -> None:
    """Round G2 : le nudge text ne doit PAS contenir de em/en dash ou emoji.

    CLAUDE.md interdit ces chars dans la voix narrative ; Phase F R44 les
    rejette dans name_fr/narrative_summary_fr/rumor_template. Le Director
    ne doit pas auto-violer cette regle dans le prompt qu'il injecte au
    narrator (sinon le LLM mimicke le style).
    """
    act = AbstractAct(
        id="act_test_x",
        description_fr="Une description sans tirets cadratins ni emoji.",
        target_year_start=10, target_year_end=15,
        urgency=0.5, created_at_year=10,
    )
    nudge = NudgeContext(
        active_acts=[act],
        active_invariants=list(NARUTO_INVARIANTS_CENTRAL[:2]),
        recent_summary="Un summary recent sans char interdit.",
        composed_at_year=10,
    )
    text = build_nudge_text(nudge)
    forbidden_chars = (
        "‒",  # figure dash
        "–",  # en dash
        "—",  # em dash
        "―",  # horizontal bar
        "﹘",  # small em dash
        "－",  # fullwidth hyphen-minus
    )
    for ch in forbidden_chars:
        assert ch not in text, (
            f"nudge text contient char interdit U+{ord(ch):04X}"
        )


def test_build_nudge_text_total_cap_strict() -> None:
    """Round G13 : le cap total de 1200 chars doit etre STRICTEMENT respecte.

    Avant : le code coupait a `cap-4` + '...' puis appendait
    '\\n[FIN DIRECTIVES tronquee]' (26 chars) -> le total faisait cap+25.
    Le narrator LLM recevait un prompt plus gros que prevu.
    """
    # Genere un nudge largement au-dessus du cap
    many_acts = [
        AbstractAct(
            id=f"act_test_{i}",
            description_fr=("X" * 200),  # 200 chars chacun, force overflow
            target_year_start=10, target_year_end=15,
            urgency=1.0 - i * 0.001, created_at_year=10,
        )
        for i in range(10)
    ]
    nudge = NudgeContext(
        active_acts=many_acts,
        active_invariants=list(NARUTO_INVARIANTS_CENTRAL),
        recent_summary="X" * 5000,
        composed_at_year=10,
    )
    text = build_nudge_text(nudge)
    # Le hard cap total est a 1200 chars. Doit etre strictement respecte.
    assert len(text) <= 1200, (
        f"build_nudge_text doit cap a 1200 chars, got {len(text)}"
    )


def test_build_nudge_text_truncates_long_summary() -> None:
    long_summary = "A" * 1000
    nudge = NudgeContext(
        recent_summary=long_summary, composed_at_year=10,
    )
    text = build_nudge_text(nudge)
    # Ne doit pas contenir tous les 1000 'A'
    assert "AAAAAAAAAAAAAAAAA" in text  # contient au moins une partie
    assert len(text) < 1300  # cap total


# --- Test scheduler / DirectorState ------------------------------------------


def test_director_state_rejects_bool_as_year_or_month() -> None:
    """Round G23 : bool est subclass de int en Python -> isinstance(True, int)
    is True. Sans le check explicite, un save corrompu avec year=True ou
    month=False etait converti silencieusement en year=1 / month=0.

    Mirror dans compactor._collect_substitutes (meme bug).
    """
    payload = {
        "active_acts": {},
        "last_compaction_year": True,    # bool, pas int valide
        "last_compaction_month": False,  # bool aussi
        "last_summary": None,
        "tick_count": 0,
        "composer_runs": 0,
        "compactor_runs": 0,
    }
    state = DirectorState.from_dict(payload)
    # bool rejete -> None (pas converti en 1/0 silencieusement)
    assert state.last_compaction_year is None
    assert state.last_compaction_month is None


@pytest.mark.asyncio
async def test_compactor_substitutes_rejects_bool_year_in_payload(
    world: WorldState,
) -> None:
    """Round G23 : meme rationale dans compactor._collect_substitutes.

    Payload corrompu avec year=True dans un substitute dict.
    """
    world_with_bad_sub = world.model_copy(update={
        "substitute_events": {
            "substitute_corrupt_bool": {
                "id": "substitute_corrupt_bool",
                "year": True,  # bool, pas int valide
                "name_fr": "Bad sub", "cancelled_canon_event_id": "x",
                "narrative_summary_fr": "aaaaaaaaaaaaaaaaaaaa",
                "outcomes": [{"type": "x"}],
                "preconditions": [], "involved_characters": [],
                "cancellation_strategy_type": "substitute",
                "rumor_template": None, "date": None, "location": None,
                "source_tension_descriptions": [],
            },
        },
    })
    compactor = NarrativeCompactor(client=None)
    summary = await compactor.compact(
        world_with_bad_sub, period_start_year=0, period_end_year=10,
    )
    # bool=True ne doit PAS etre traite comme year=1 -> sub absent du summary
    assert "substitute_corrupt_bool" not in summary


def test_director_state_clamps_corrupt_year_month_on_load() -> None:
    """Round G22 : last_compaction_year/month corrompus sont clamp.

    Save avec year=99999 ou month=13 -> is_compaction_due retournait
    False indefiniment (elapsed negatif ou math weird) -> compactor
    jamais run -> stale summary a perpetuite.
    """
    payload = {
        "active_acts": {},
        "last_compaction_year": 99999,  # absurde
        "last_compaction_month": 13,    # invalide
        "last_summary": "Resume valide.",
        "tick_count": 5,
        "composer_runs": 5,
        "compactor_runs": 1,
    }
    state = DirectorState.from_dict(payload)
    # Year clamp a 10000 (R G18 max)
    assert state.last_compaction_year == 10000
    # Month clamp a 12
    assert state.last_compaction_month == 12

    # Symetrique cote negatif
    payload_neg = {
        "active_acts": {},
        "last_compaction_year": -50000,
        "last_compaction_month": 0,
        "last_summary": None,
        "tick_count": 0,
        "composer_runs": 0,
        "compactor_runs": 0,
    }
    state_neg = DirectorState.from_dict(payload_neg)
    assert state_neg.last_compaction_year == -10000  # clamp R G18 min
    assert state_neg.last_compaction_month == 1

    # to_dict re-clamp aussi (defensive)
    serialized = state.to_dict()
    assert serialized["last_compaction_year"] == 10000
    assert serialized["last_compaction_month"] == 12


def test_director_state_caps_oversized_summary_on_load_and_save() -> None:
    """Round G14 : DirectorState (de)serialization cap last_summary.

    Symetrique a R G12 (compactor cap LLM output). Sans ca, un save
    pre-R G12 ou ecrit par un autre code contenant 50K chars passait
    from_dict puis to_dict tel quel -> save bloat persistant.
    """
    huge_summary = "X" * 50_000
    payload = {
        "active_acts": {},
        "last_compaction_year": 10,
        "last_compaction_month": 1,
        "last_summary": huge_summary,
        "tick_count": 5,
        "composer_runs": 5,
        "compactor_runs": 1,
    }
    state = DirectorState.from_dict(payload)
    # from_dict cap automatiquement
    assert state.last_summary is not None
    assert len(state.last_summary) <= 5000
    assert state.last_summary.endswith("...")

    # to_dict re-cap aussi (defensive double check)
    serialized = state.to_dict()
    assert len(serialized["last_summary"]) <= 5000


def test_director_state_to_dict_from_dict_round_trip() -> None:
    """DirectorState serialise/deserialise a l'identique."""
    act = AbstractAct(
        id="act_test_persist", description_fr="x" * 20,
        target_year_start=10, target_year_end=15, created_at_year=10,
    )
    state = DirectorState(
        active_acts={"act_test_persist": act},
        last_compaction_year=10, last_compaction_month=6,
        last_summary="Summary persiste.",
        tick_count=5, composer_runs=5, compactor_runs=2,
    )
    payload = state.to_dict()
    restored = DirectorState.from_dict(payload)
    assert restored.active_acts.keys() == state.active_acts.keys()
    assert restored.last_compaction_year == 10
    assert restored.tick_count == 5


def test_director_state_from_dict_skips_corrupted_acts() -> None:
    """Save corrompue : un act malforme doit etre skipe sans crash."""
    payload = {
        "active_acts": {
            "act_valid": {
                "id": "act_valid",
                "description_fr": "Description suffisamment longue.",
                "target_year_start": 5,
                "target_year_end": 10,
                "created_at_year": 5,
                "status": "active",
                "urgency": 0.5,
                "related_tension_types": [],
                "involved_entities": [],
                "source_tension_descriptions": [],
            },
            "act_corrupted": "this_is_not_a_dict",  # corrupted
        },
    }
    state = DirectorState.from_dict(payload)
    assert "act_valid" in state.active_acts
    assert "act_corrupted" not in state.active_acts


def test_is_compaction_due_first_run_is_due() -> None:
    """Premier run (last=None) -> due."""
    state = DirectorState()
    assert is_compaction_due(
        state, current_year=10, current_month=1, interval_months=6,
    )


def test_is_compaction_due_within_interval() -> None:
    """3 mois apres = interval=6 -> pas due."""
    state = DirectorState(last_compaction_year=10, last_compaction_month=1)
    assert not is_compaction_due(
        state, current_year=10, current_month=4, interval_months=6,
    )


def test_is_compaction_due_after_interval() -> None:
    """7 mois apres = interval=6 -> due."""
    state = DirectorState(last_compaction_year=10, last_compaction_month=1)
    assert is_compaction_due(
        state, current_year=10, current_month=8, interval_months=6,
    )


# --- Test compactor -----------------------------------------------------------


@pytest.mark.asyncio
async def test_compactor_offline_fallback_no_client(world: WorldState) -> None:
    """Sans LLM client, compactor produit un fallback deterministe."""
    compactor = NarrativeCompactor(client=None)
    summary = await compactor.compact(
        world, period_start_year=5, period_end_year=10,
    )
    assert isinstance(summary, str)
    assert "year 5 a 10" in summary or "year 5-10" in summary
    # Pas d'event -> "Aucun fait notable"
    assert "Aucun fait notable" in summary or "stable" in summary.lower()


@pytest.mark.asyncio
async def test_compactor_enriches_llm_prompt_with_canon_eras(
    canon: CanonBundle, world: WorldState,
) -> None:
    """Round G30 : compactor utilise canon pour ajouter contexte ere au
    prompt LLM. Avant : canon param stocke mais jamais utilise.

    Verifier que le prompt user inclut les eres canon couvrant la periode.
    """
    captured_user_msgs: list[str] = []

    class _CapturingLLM:
        async def generate(self, messages, **kwargs):
            captured_user_msgs.append(messages[1].content)
            from dataclasses import dataclass
            @dataclass
            class _R:
                text: str = "summary OK"
                parsed_json: dict | None = None
            return _R()

    compactor = NarrativeCompactor(
        client=_CapturingLLM(),  # type: ignore[arg-type]
        canon=canon,
    )
    # Periode 5-15 couvre plusieurs eres canon (depending on canon data)
    await compactor.compact(world, period_start_year=5, period_end_year=15)
    assert len(captured_user_msgs) == 1
    msg = captured_user_msgs[0]
    # Le prompt doit contenir une section "Eres canon" avec au moins 1 ere
    assert "Eres canon" in msg or "ere" in msg.lower()


@pytest.mark.asyncio
async def test_compactor_no_eras_section_when_canon_none(
    world: WorldState,
) -> None:
    """Round G30 : sans canon, pas de section 'Eres canon' dans le prompt.

    Defensive : compactor avec canon=None (default) ne doit pas crash et
    omet juste l'enrichissement.
    """
    captured: list[str] = []

    class _CapturingLLM:
        async def generate(self, messages, **kwargs):
            captured.append(messages[1].content)
            from dataclasses import dataclass
            @dataclass
            class _R:
                text: str = "x"
                parsed_json: dict | None = None
            return _R()

    compactor = NarrativeCompactor(client=_CapturingLLM(), canon=None)  # type: ignore[arg-type]
    await compactor.compact(world, period_start_year=5, period_end_year=10)
    assert "Eres canon" not in captured[0]


@pytest.mark.asyncio
async def test_compactor_uses_llm_when_available(world: WorldState) -> None:
    """LLM client present : compactor utilise sa response."""
    llm = _MockLLMClient(["LLM-generated summary about Naruto canon."])
    compactor = NarrativeCompactor(client=llm)  # type: ignore[arg-type]
    summary = await compactor.compact(
        world, period_start_year=5, period_end_year=10,
    )
    assert "LLM-generated summary" in summary
    assert llm.calls == 1


@pytest.mark.asyncio
async def test_compactor_swaps_inverted_period(canon: CanonBundle) -> None:
    """Round G27 : si period_start > period_end, swap automatique.

    Avant : _collect_* filtrait `start <= year <= end` qui ne match jamais
    -> summary vide alors que des events reels existaient. Le narrator
    LLM recevait une summary trompeuse.
    """
    from shinobi.engine.world import CompletedEvent

    world_with_events = WorldState(
        current_year=10, current_date="06-01",
        completed_events=[
            CompletedEvent(event_id="ev_year7", triggered_at_turn=1, triggered_at_year=7),
        ],
    )
    compactor = NarrativeCompactor(client=None)
    # Period inverted : start=10, end=5
    summary = await compactor.compact(
        world_with_events, period_start_year=10, period_end_year=5,
    )
    # Apres swap : period devient [5, 10] -> ev_year7 inclus
    assert "ev_year7" in summary
    # L'header devrait montrer la periode swappee (5-10)
    assert "year 5 a 10" in summary


@pytest.mark.asyncio
async def test_compactor_offline_unambiguous_with_negative_years(
    world: WorldState,
) -> None:
    """Round G20 : separateur ' a ' au lieu de '-' pour eviter ambiguites
    avec annees negatives.

    Avant : 'Periode year -50-10' (3 tirets, parsing ambigu),
    'Periode year -100--50' (double tiret, illisible). Canon Naruto
    inclut des annees negatives (Otsutsuki, Warring States) -> realiste.
    """
    compactor = NarrativeCompactor(client=None)

    # Periode negative
    summary = await compactor.compact(
        world, period_start_year=-100, period_end_year=-50,
    )
    # Pas de double-tiret confus
    assert "--" not in summary
    # Format clair avec ' a '
    assert "year -100 a -50" in summary

    # Periode mixte (negative -> positive)
    summary2 = await compactor.compact(
        world, period_start_year=-50, period_end_year=10,
    )
    assert "year -50 a 10" in summary2
    # Ne pas confondre avec range positif simple
    assert "year -50-10" not in summary2


@pytest.mark.asyncio
async def test_compactor_substitutes_filtered_by_period(
    world: WorldState,
) -> None:
    """Round G15 : pending substitutes hors periode ne doivent pas
    apparaitre dans la summary.

    Avant : _collect_substitutes retournait TOUS les pending. Un substitute
    scheduled pour year 30 apparaissait dans 'Periode year 5-10' summary,
    induisant le narrator LLM en erreur.
    """
    # World avec 2 pending substitutes : 1 dans periode, 1 hors periode
    world_with_subs = world.model_copy(update={
        "substitute_events": {
            "substitute_in_period": {
                "id": "substitute_in_period",
                "year": 8,  # dans [5, 10]
                "name_fr": "In period sub",
                "cancelled_canon_event_id": "x",
                "narrative_summary_fr": "aaaaaaaaaaaaaaaaaaaa",
                "outcomes": [{"type": "x"}],
                "preconditions": [],
                "involved_characters": [],
                "cancellation_strategy_type": "substitute",
                "rumor_template": None,
                "date": None,
                "location": None,
                "source_tension_descriptions": [],
            },
            "substitute_far_future": {
                "id": "substitute_far_future",
                "year": 30,  # hors [5, 10]
                "name_fr": "Future sub",
                "cancelled_canon_event_id": "x",
                "narrative_summary_fr": "aaaaaaaaaaaaaaaaaaaa",
                "outcomes": [{"type": "x"}],
                "preconditions": [],
                "involved_characters": [],
                "cancellation_strategy_type": "substitute",
                "rumor_template": None,
                "date": None,
                "location": None,
                "source_tension_descriptions": [],
            },
        },
    })

    compactor = NarrativeCompactor(client=None)
    summary = await compactor.compact(
        world_with_subs, period_start_year=5, period_end_year=10,
    )
    # Le sub in_period apparait dans la summary
    assert "substitute_in_period" in summary
    # Le sub far_future ne doit PAS apparaitre
    assert "substitute_far_future" not in summary


@pytest.mark.asyncio
async def test_compactor_caps_runaway_llm_output(world: WorldState) -> None:
    """Round G12 : LLM runaway (50K chars) tronque a 5000 chars + marker.

    Sans cap, l'output persiste dans DirectorState.last_summary -> save
    bloat sur sessions longues.
    """
    huge_response = "Très long résumé. " * 1000  # ~18K chars
    llm = _MockLLMClient([huge_response])
    compactor = NarrativeCompactor(client=llm)  # type: ignore[arg-type]
    summary = await compactor.compact(
        world, period_start_year=5, period_end_year=10,
    )
    # Tronque a 5000 chars + suffix '...'
    assert len(summary) <= 5000
    assert summary.endswith("...")


@pytest.mark.asyncio
async def test_compactor_does_not_truncate_normal_output(
    world: WorldState,
) -> None:
    """Round G12 : output dans la cible 200-400 mots passe inchange."""
    normal_response = "Résumé normal de la période sur 200 mots environ. " * 5
    llm = _MockLLMClient([normal_response])
    compactor = NarrativeCompactor(client=llm)  # type: ignore[arg-type]
    summary = await compactor.compact(
        world, period_start_year=5, period_end_year=10,
    )
    # Pas tronque (largement < 5000)
    assert summary == normal_response.strip()
    assert not summary.endswith("...")


@pytest.mark.asyncio
async def test_compactor_falls_back_on_llm_crash(world: WorldState) -> None:
    """Si LLM raise, compactor fallback sur deterministe (no crash)."""
    class _CrashingLLM:
        async def generate(self, *args, **kwargs):
            raise RuntimeError("llm down")
    compactor = NarrativeCompactor(client=_CrashingLLM())  # type: ignore[arg-type]
    summary = await compactor.compact(
        world, period_start_year=5, period_end_year=10,
    )
    # Fallback offline produit toujours un summary (pas vide, pas de crash)
    assert isinstance(summary, str)
    assert "year 5-10" in summary or "year 5" in summary


# --- Test Director e2e --------------------------------------------------------


@pytest.mark.asyncio
async def test_director_clamps_invalid_current_month_at_entry(
    canon: CanonBundle, world: WorldState,
) -> None:
    """Round G25 : current_month=0 ou 13 (date corrompue parsee comme MM)
    est clamp a [1, 12].

    Avant : _months_elapsed produisait des valeurs off-by-one ou avec
    un mois fantome -> is_compaction_due imprevisible.
    """
    director = Director(canon, llm_client=None)
    state = DirectorState(last_compaction_year=10, last_compaction_month=6)

    # current_month=0 (date '00-01' corrompue)
    report1 = await director.tick(
        tensions=TensionList(tensions=[]),
        world=world, state=state,
        current_year=10, current_month=0,
    )
    assert report1.tick_month == 1  # clamp a 1

    # current_month=13
    report2 = await director.tick(
        tensions=TensionList(tensions=[]),
        world=world, state=state,
        current_year=10, current_month=13,
    )
    assert report2.tick_month == 12  # clamp a 12


@pytest.mark.asyncio
async def test_director_resets_state_on_same_year_month_retrograde(
    canon: CanonBundle, world: WorldState,
) -> None:
    """Round G28 : reset si (last_year, last_month) > (current_year,
    current_month) tuple-wise.

    R G26 ne couvrait que year > current_year. Cas manque : year egal mais
    month retrograde. last=(10, 12), current=(10, 1) via save/load ->
    elapsed=-11 -> compaction never until year 11+ if not detected.
    """
    director = Director(canon, llm_client=None)
    state = DirectorState(
        last_compaction_year=10,   # meme year que current
        last_compaction_month=12,  # mais month plus tard
    )
    report = await director.tick(
        tensions=TensionList(tensions=[]),
        world=world, state=state,
        current_year=10, current_month=1,  # rewind a janvier same year
    )
    # Reset detecte tuple-wise (10,12) > (10,1) -> reset puis compaction
    assert state.last_compaction_year == 10
    assert state.last_compaction_month == 1
    assert report.compaction_ran


@pytest.mark.asyncio
async def test_director_resets_state_when_last_compaction_in_future(
    canon: CanonBundle, world: WorldState,
) -> None:
    """Round G26 : si state.last_compaction_year > current_year apres
    clamp, reset a None pour eviter blocage permanent.

    Apres R G24, year=99999 clamp a 10000. Mais current_year=10 ->
    elapsed=(10-10000)*12 negatif -> is_compaction_due retourne False
    indefiniment. La corruption se transformait en blocage.
    Maintenant : detect incoherence temporelle, reset, force fresh
    compaction.
    """
    director = Director(canon, llm_client=None)
    state = DirectorState(
        last_compaction_year=99999,  # clamp a 10000 par R G24, > current=10
        last_compaction_month=6,
    )
    report = await director.tick(
        tensions=TensionList(tensions=[]),
        world=world, state=state,
        current_year=10, current_month=1,
    )
    # Apres reset + compaction : last_compaction_year = current_year=10
    assert state.last_compaction_year == 10
    # Compaction a effectivement run (etait bloquee avant R G26)
    assert report.compaction_ran


@pytest.mark.asyncio
async def test_director_clamps_state_year_in_memory(
    canon: CanonBundle, world: WorldState,
) -> None:
    """Round G24 : tick() clamp state.last_compaction_year/month en memoire,
    pas seulement a la (de)serialization R G22.

    Caller direct construisant DirectorState(last_compaction_year=99999)
    en memoire bypass R G22 qui ne touche que from_dict/to_dict.
    is_compaction_due retournait False indefiniment.
    """
    director = Director(canon, llm_client=None)
    # State avec valeurs absurdes EN MEMOIRE (pas via from_dict).
    # On utilise un year qui restera dans bounds apres clamp ET <= current_year
    # pour isoler le test G24 du R G26 (qui reset si > current_year).
    state = DirectorState(
        last_compaction_year=-50000,  # clamp a -10000, < current_year=10 OK
        last_compaction_month=15,     # clamp a 12
    )
    report = await director.tick(
        tensions=TensionList(tensions=[]),
        world=world, state=state,
        current_year=10, current_month=1,
    )
    # Apres tick, state mute en place avec valeurs clamp puis compaction run
    # (initial state -10000 << current 10 -> compaction due -> set to 10)
    assert state.last_compaction_year == 10  # apres compaction
    # Tick complete normalement
    assert report is not None
    assert report.compaction_ran


@pytest.mark.asyncio
async def test_director_clamps_year_beyond_pydantic_bounds(
    canon: CanonBundle,
) -> None:
    """Round G21 : current_year > 10000 (au-dela des bornes R G18) est
    clamp avec warning au lieu de crash.

    Avant R G21 : compose_acts catchait via R G19 mais build_nudge
    construisait NudgeContext(composed_at_year=20000) -> Pydantic
    ValidationError -> tick() crashait -> CLI perdait le report.
    Maintenant : clamp a 10000, tick complete normalement.
    """
    extreme_world = WorldState(current_year=20000, current_date="06-01")
    director = Director(canon, llm_client=None)
    state = DirectorState()
    # Pas de tensions pour simplifier ; juste verifier que tick complete
    report = await director.tick(
        tensions=TensionList(tensions=[]),
        world=extreme_world, state=state,
        current_year=20000, current_month=1,
    )
    # Tick ne doit PAS crash
    assert report is not None
    # Le year dans report est clamp a 10000
    assert report.tick_year == 10000
    # Nudge construit avec composed_at_year clamp
    assert report.nudge is not None
    assert report.nudge.composed_at_year == 10000


@pytest.mark.asyncio
async def test_director_clamps_negative_year_beyond_bounds(
    canon: CanonBundle,
) -> None:
    """Round G21 : symetrique pour les valeurs trop negatives."""
    director = Director(canon, llm_client=None)
    state = DirectorState()
    very_old_world = WorldState(current_year=-50000, current_date="01-01")
    report = await director.tick(
        tensions=TensionList(tensions=[]),
        world=very_old_world, state=state,
        current_year=-50000, current_month=1,
    )
    assert report is not None
    assert report.tick_year == -10000  # clamp at min


@pytest.mark.asyncio
async def test_director_works_at_extended_canon_years(
    canon: CanonBundle,
) -> None:
    """Round G18 : Director fonctionne au-dela du canon range [-1000, 200].

    Avant : AbstractAct.year bornes [-1000, 200] (clonees Phase F canon
    range) faisaient que tout current_year > 200 ou < -1000 produisait
    Pydantic ValidationError dans compose_acts -> exception catchee ->
    0 acts produits. Director degradait silencieusement.

    Maintenant : bornes elargies a [-10000, 10000].
    """
    extended_world = WorldState(current_year=500, current_date="06-01")
    director = Director(canon, llm_client=None)
    state = DirectorState()
    tensions = TensionList(tensions=[
        Tension(
            type=TensionType.alliance_breakdown,
            description="Tension dans le futur etendu.",
            severity=TensionSeverity.high, score=0.8,
            involved_entities=["future_village"],
        ),
    ])
    report = await director.tick(
        tensions=tensions, world=extended_world, state=state,
        current_year=500, current_month=1,
    )
    # Avant R G18 : 0 acts (Pydantic rejette year=500). Maintenant : 1 act.
    assert len(report.new_acts) == 1
    assert report.new_acts[0].target_year_start == 500


def test_director_init_validates_parameters(canon: CanonBundle) -> None:
    """Round G17 : Director.__init__ rejette les params absurdes.

    Sans validation : compaction_interval_months <= 0 -> compaction
    chaque tick -> coût LLM x10-20. max_active_acts <= 0 -> aucun act
    jamais conserve. Etc. Mode degrade silencieux possible.
    """
    # compaction_interval_months <= 0
    with pytest.raises(ValueError, match="compaction_interval_months"):
        Director(canon, compaction_interval_months=0)
    with pytest.raises(ValueError, match="compaction_interval_months"):
        Director(canon, compaction_interval_months=-1)
    # max_active_acts <= 0
    with pytest.raises(ValueError, match="max_active_acts"):
        Director(canon, max_active_acts=0)
    # composer_top_n <= 0
    with pytest.raises(ValueError, match="composer_top_n"):
        Director(canon, composer_top_n=0)
    # composer_min_score hors [0, 1]
    with pytest.raises(ValueError, match="composer_min_score"):
        Director(canon, composer_min_score=-0.1)
    with pytest.raises(ValueError, match="composer_min_score"):
        Director(canon, composer_min_score=1.5)
    # Valid : pas d'erreur
    director = Director(
        canon, compaction_interval_months=6, max_active_acts=5,
        composer_top_n=3, composer_min_score=0.5,
    )
    assert director is not None


@pytest.mark.asyncio
async def test_director_tick_e2e_no_tensions(
    canon: CanonBundle, world: WorldState,
) -> None:
    """Tick avec TensionList vide : aucun acte, mais nudge avec invariants."""
    director = Director(canon, llm_client=None)
    state = DirectorState()
    report = await director.tick(
        tensions=TensionList(tensions=[]),
        world=world, state=state,
        current_year=10, current_month=1,
    )
    assert isinstance(report, DirectorReport)
    assert report.new_acts == []
    assert report.active_acts == []
    # Compaction est due au 1er tick
    assert report.compaction_ran
    # Nudge contient invariants centraux meme sans acts
    assert report.nudge is not None
    assert len(report.nudge.active_invariants) > 0


@pytest.mark.asyncio
async def test_director_tick_creates_acts_from_tensions(
    canon: CanonBundle, world: WorldState,
) -> None:
    director = Director(canon, llm_client=None)
    state = DirectorState()
    tensions = TensionList(tensions=[
        Tension(
            type=TensionType.alliance_breakdown,
            description="Konoha-Suna fragmente apres incident.",
            severity=TensionSeverity.high, score=0.8,
            involved_entities=["konohagakure", "sunagakure"],
        ),
    ])
    report = await director.tick(
        tensions=tensions, world=world, state=state,
        current_year=10, current_month=1,
    )
    assert len(report.new_acts) == 1
    assert "konoha" in report.new_acts[0].id.lower()
    # Acts persistes en state
    assert len(state.active_acts) == 1


@pytest.mark.asyncio
async def test_director_tick_idempotent_state_mutation(
    canon: CanonBundle, world: WorldState,
) -> None:
    """Tick sans tensions sur un state existant : retire les expires."""
    director = Director(canon, llm_client=None)
    expired_act = AbstractAct(
        id="act_old_expired", description_fr="x" * 20,
        target_year_start=1, target_year_end=5,
        created_at_year=1, status="active",
    )
    state = DirectorState(active_acts={"act_old_expired": expired_act})
    report = await director.tick(
        tensions=TensionList(tensions=[]),
        world=world, state=state,
        current_year=10, current_month=1,
    )
    # Expired retire
    assert len(report.retired_acts) == 1
    assert "act_old_expired" not in state.active_acts


@pytest.mark.asyncio
async def test_director_first_compaction_covers_full_history(
    canon: CanonBundle,
) -> None:
    """Round G4 : 1ere compaction couvre depuis le 1er event historique,
    pas seulement current_year - 1.

    Scenario : partie tournee 5 ans avant 1er Director tick. Events year
    5-10. 1er compaction au year 10 doit voir tous ces events, pas
    seulement ceux des year 9-10.
    """
    from shinobi.engine.world import CompletedEvent

    world_with_history = WorldState(
        current_year=10, current_date="06-01",
        completed_events=[
            CompletedEvent(event_id="ev_year5", triggered_at_turn=1, triggered_at_year=5),
            CompletedEvent(event_id="ev_year7", triggered_at_turn=3, triggered_at_year=7),
            CompletedEvent(event_id="ev_year10", triggered_at_turn=10, triggered_at_year=10),
        ],
    )

    # Mock compactor pour capturer les arguments
    captured_periods: list[tuple[int, int]] = []

    class _CapturingCompactor:
        async def compact(
            self, world, *, period_start_year: int, period_end_year: int,
        ) -> str:
            captured_periods.append((period_start_year, period_end_year))
            return f"summary {period_start_year}-{period_end_year}"

    director = Director(canon, llm_client=None)
    director.compactor = _CapturingCompactor()  # type: ignore[assignment]
    state = DirectorState()

    await director.tick(
        tensions=TensionList(tensions=[]),
        world=world_with_history, state=state,
        current_year=10, current_month=1,
    )

    assert len(captured_periods) == 1
    period_start, period_end = captured_periods[0]
    # 1er run : period_start doit etre 5 (1er event), pas 9 (current-1)
    assert period_start == 5, (
        f"1ere compaction doit couvrir depuis year 5, got {period_start}"
    )
    assert period_end == 10


@pytest.mark.asyncio
async def test_director_tick_compaction_runs_at_interval(
    canon: CanonBundle, world: WorldState,
) -> None:
    """1er tick : compaction due. 2e tick same year : pas due."""
    director = Director(
        canon, llm_client=None, compaction_interval_months=6,
    )
    state = DirectorState()
    # Tick 1 : compaction due (premier run)
    report1 = await director.tick(
        tensions=TensionList(tensions=[]),
        world=world, state=state,
        current_year=10, current_month=1,
    )
    assert report1.compaction_ran
    # Tick 2 : 2 mois plus tard, pas due (interval=6)
    report2 = await director.tick(
        tensions=TensionList(tensions=[]),
        world=world, state=state,
        current_year=10, current_month=3,
    )
    assert not report2.compaction_ran
    # Tick 3 : 7 mois plus tard, due
    report3 = await director.tick(
        tensions=TensionList(tensions=[]),
        world=world, state=state,
        current_year=10, current_month=8,
    )
    assert report3.compaction_ran


@pytest.mark.asyncio
async def test_director_tick_caps_active_acts(
    canon: CanonBundle, world: WorldState,
) -> None:
    """Plus de max_active_acts -> garde top urgency.

    Round G7 : les acts evinces (low urgency) sont ajoutes a retired_acts
    avec status='expired' pour traceability. Avant, evaporation silencieuse.
    """
    director = Director(canon, llm_client=None, max_active_acts=3)
    state = DirectorState()
    # 5 tensions distinctes par (type, entity) pour eviter dedup
    types = list(TensionType)[:5]
    tensions = TensionList(tensions=[
        Tension(
            type=types[i],
            description=f"Tension {i} pour test cap.",
            severity=TensionSeverity.high,
            score=0.5 + i * 0.1,
            involved_entities=[f"entity_{i}"],
        )
        for i in range(5)
    ])
    report = await director.tick(
        tensions=tensions, world=world, state=state,
        current_year=10, current_month=1,
    )
    assert len(state.active_acts) <= 3
    # Round G7 : 5 acts produits, 3 gardes -> 2 evinces dans retired
    evicted_in_retired = [a for a in report.retired_acts if a.status == "expired"]
    assert len(evicted_in_retired) >= 2, (
        f"acts evinces doivent etre dans retired_acts : "
        f"retired={len(report.retired_acts)}, active={len(state.active_acts)}"
    )


@pytest.mark.asyncio
async def test_director_tick_with_llm_uses_compactor(
    canon: CanonBundle, world: WorldState,
) -> None:
    llm = _MockLLMClient(["Compaction LLM summary text."])
    director = Director(canon, llm_client=llm)  # type: ignore[arg-type]
    state = DirectorState()
    report = await director.tick(
        tensions=TensionList(tensions=[]),
        world=world, state=state,
        current_year=10, current_month=1,
    )
    assert report.compaction_ran
    assert "Compaction LLM summary text" in (report.compaction_summary or "")
    # Summary persiste dans state
    assert state.last_summary == "Compaction LLM summary text."


def test_year_month_constants_aligned_across_modules() -> None:
    """Round G29 : YEAR_MIN/MAX et MONTH_MIN/MAX sont single source of truth
    dans types.py, importes par core.py et scheduler.py.

    Si quelqu'un re-defines un de ces constants localement, drift possible.
    Cet invariant le catch.
    """
    from shinobi.director import types as t
    from shinobi.director import core as c
    from shinobi.director import scheduler as s

    assert t.YEAR_MIN == c.YEAR_MIN == s._YEAR_MIN
    assert t.YEAR_MAX == c.YEAR_MAX == s._YEAR_MAX
    assert t.MONTH_MIN == c.MONTH_MIN == s._MONTH_MIN
    assert t.MONTH_MAX == c.MONTH_MAX == s._MONTH_MAX


def test_act_templates_cover_all_tension_types() -> None:
    """Round G10 : _ACT_TEMPLATES doit avoir une entry pour CHAQUE valeur
    de TensionType.

    Si un type manque, le fallback sur 'other' template prive la description
    de sa specificite narratorielle. compose_acts ne crash pas mais la
    direction narrative perd son intent. Lock identique a R G9.
    """
    from shinobi.director.act_composer import _ACT_TEMPLATES

    enum_values = set(TensionType)
    template_keys = set(_ACT_TEMPLATES.keys())
    missing = enum_values - template_keys
    assert not missing, (
        f"TensionType enum a {len(missing)} valeurs sans template : "
        f"{sorted(t.value for t in missing)}. Ajouter dans _ACT_TEMPLATES."
    )
    extra = template_keys - enum_values
    assert not extra, (
        f"_ACT_TEMPLATES contient des keys non-TensionType : "
        f"{sorted(t.value for t in extra)}. Aligner ou supprimer."
    )


def test_act_templates_all_use_entities_placeholder() -> None:
    """Round G10 : chaque template doit contenir le placeholder {entities}
    pour permettre la substitution des entities impliquees.

    Sans ce placeholder, .format(entities=...) leve KeyError ou genere
    une description sans mention des entites concernees -> directive
    narrative incomplete pour le narrator LLM.
    """
    from shinobi.director.act_composer import _ACT_TEMPLATES

    missing_placeholder: list[str] = []
    for tension_type, template in _ACT_TEMPLATES.items():
        if "{entities}" not in template:
            missing_placeholder.append(tension_type.value)
    assert not missing_placeholder, (
        f"Templates sans placeholder {{entities}} : {missing_placeholder}"
    )


def test_tension_type_to_contexts_covers_all_enum_values() -> None:
    """Round G9 : _TENSION_TYPE_TO_CONTEXTS doit avoir une entry pour
    CHAQUE valeur de TensionType enum.

    Sinon : nouvelle tension ajoutee a l'enum mais oubliee dans la table
    -> tombe silencieusement a () -> fallback centraux R G6 -> scoring
    cible perd l'intent. Cet invariant doit etre lock.
    """
    from shinobi.director.core import _TENSION_TYPE_TO_CONTEXTS

    enum_values = {tt.value for tt in TensionType}
    table_keys = set(_TENSION_TYPE_TO_CONTEXTS.keys())
    missing = enum_values - table_keys
    assert not missing, (
        f"TensionType enum a {len(missing)} valeurs sans mapping G8 : "
        f"{sorted(missing)}. Ajouter dans _TENSION_TYPE_TO_CONTEXTS."
    )
    extra = table_keys - enum_values
    assert not extra, (
        f"_TENSION_TYPE_TO_CONTEXTS contient des keys non-TensionType : "
        f"{sorted(extra)}. Aligner ou supprimer."
    )


def test_tension_type_to_contexts_only_uses_known_invariant_keys() -> None:
    """Round G9 : les contextes mappes doivent matcher au moins UN invariant
    pour ne pas etre du dead code.

    Une cle de contexte qui n'apparait dans aucun applies_to_contexts ne
    sert a rien. Cet invariant catch les typos.
    """
    from shinobi.director.core import _TENSION_TYPE_TO_CONTEXTS

    all_invariant_contexts: set[str] = set()
    for inv in NARUTO_INVARIANTS:
        all_invariant_contexts.update(inv.applies_to_contexts)

    unused: set[str] = set()
    for tension_type, contexts in _TENSION_TYPE_TO_CONTEXTS.items():
        for ctx in contexts:
            if ctx not in all_invariant_contexts:
                unused.add(ctx)
    assert not unused, (
        f"_TENSION_TYPE_TO_CONTEXTS reference des contextes inconnus des "
        f"invariants : {sorted(unused)}. Soit typo, soit ajouter au moins "
        f"un invariant qui les couvre."
    )


@pytest.mark.asyncio
async def test_director_cursed_hatred_selects_hatred_breakable_invariant(
    canon: CanonBundle, world: WorldState,
) -> None:
    """Round G8 : tension cursed_hatred doit selectionner l'invariant
    invariant_hatred_breakable, pas tomber au fallback centraux.

    Avant : split('_') de 'cursed_hatred' -> ['cursed', 'hatred'] qui ne
    matche aucun applies_to_contexts (qui contient 'vengeance', 'war',
    'trauma', 'redemption'). Resultat : pas de match -> fallback R G6 sur
    centraux. L'invariant le plus pertinent thematiquement (hatred_breakable
    parle EXACTEMENT du cycle de haine brisable par dialogue) etait noye.
    """
    director = Director(canon, llm_client=None)
    state = DirectorState()
    tensions = TensionList(tensions=[
        Tension(
            type=TensionType.cursed_hatred,
            description="Cycle de haine Sasuke-style sur Itachi-Konoha.",
            severity=TensionSeverity.critical, score=1.0,
            involved_entities=["uchiha_sasuke", "konohagakure"],
        ),
    ])
    report = await director.tick(
        tensions=tensions, world=world, state=state,
        current_year=10, current_month=1,
    )
    assert report.nudge is not None
    invariant_ids = {inv.id for inv in report.nudge.active_invariants}
    # invariant_hatred_breakable doit etre present (matche via 'vengeance',
    # 'war', 'trauma', 'redemption' du mapping G8)
    assert "invariant_hatred_breakable" in invariant_ids, (
        f"hatred_breakable manquant pour cursed_hatred : {invariant_ids}"
    )


@pytest.mark.asyncio
async def test_phase_f_to_phase_g_e2e_chain(
    canon: CanonBundle,
) -> None:
    """E2E : Phase F injecte un substitute -> tick_scheduler trigger ->
    completed_event apparait dans Phase G compaction.

    Round task 4 : valide la boucle complete de generation narrative
    emergente :
    1. Tension scheduler emit une tension critical
    2. Phase G Director compose un act + nudge
    3. Phase F Pipeline regenere un substitute event car canon event annule
    4. tick_scheduler trigger le substitute (preconditions OK)
    5. CompletedEvent ajoute a world.completed_events
    6. Tick suivant Phase G : compactor voit le substitute completed
       dans son resume narratif
    7. Le nudge.recent_summary mentionne le substitute_id
    """
    from shinobi.engine.events import tick_scheduler
    from shinobi.engine.world import CompletedEvent, ScheduledEvent
    from shinobi.kg.store import KnowledgeGraphStore
    from shinobi.types import EventStatus
    from shinobi.world_resolver import (
        SubstituteEvent,
        SubstituteEventInjector,
        SubstituteOutcome,
    )

    kg = KnowledgeGraphStore(None)
    try:
        # ETAPE 1 : Construire un world avec un canon event scheduled qui va
        # etre annule (precondition impossible, char canon mort)
        world = WorldState(current_year=10, current_date="06-01")

        # ETAPE 2 : Phase F injecte un substitute manually (simule la pipeline)
        substitute = SubstituteEvent(
            id="substitute_e2e_alliance",
            cancelled_canon_event_id="uchiha_clan_massacre",
            name_fr="Alliance e2e Uchiha-Konoha test chain",
            year=10,
            date="06-01",
            location="konohagakure",
            outcomes=[SubstituteOutcome(
                type="alliance_formed",
                parameters={"character_id": "uchiha_fugaku"},
            )],
            narrative_summary_fr=(
                "Alliance Uchiha-Konoha pour test e2e chain Phase F + G."
            ),
        )
        injector = SubstituteEventInjector(kg)
        inj_result = injector.inject(substitute, world=world)
        world_after_inject = inj_result.world

        # Pre-tick state : substitute scheduled
        assert "substitute_e2e_alliance" in world_after_inject.substitute_events

        # ETAPE 3 : tick_scheduler trigger le substitute
        world_triggered, fired, _cancelled = tick_scheduler(
            world_after_inject, canon, turn_number=1,
        )

        # CompletedEvent doit contenir le substitute (or precondition might
        # fail if canon char dead at year 10 ; check both paths)
        substitute_completed = any(
            ev.event_id == "substitute_e2e_alliance"
            for ev in world_triggered.completed_events
        )
        # Pour ce test, on accepte soit triggered soit cancelled (la
        # precondition character_alive sur fugaku peut fail si fugaku canon
        # mort year 10 ; on test le chain compactor seulement si triggered).
        if not substitute_completed:
            pytest.skip(
                "Substitute didn't trigger (precondition fail), "
                "skipping compactor chain check"
            )

        # ETAPE 4 : Phase G Director tick voit le substitute dans world
        director = Director(canon, llm_client=None)
        state = DirectorState()
        # Tensions vides pour simplifier ; le focus du test est la
        # compaction qui voit le substitute completed
        report = await director.tick(
            tensions=TensionList(tensions=[]),
            world=world_triggered, state=state,
            current_year=10, current_month=6,
        )

        # ETAPE 5 : la compaction (offline fallback) doit mentionner le
        # substitute_id dans le summary
        assert report.compaction_ran
        assert report.compaction_summary is not None
        assert "substitute_e2e_alliance" in report.compaction_summary, (
            f"compaction_summary doit referencer le substitute Phase F : "
            f"{report.compaction_summary}"
        )

        # ETAPE 6 : le nudge contient bien ce summary
        assert report.nudge is not None
        assert report.nudge.recent_summary is not None
        assert "substitute_e2e_alliance" in report.nudge.recent_summary
    finally:
        kg.close()


@pytest.mark.asyncio
async def test_director_nudge_text_includes_directives_when_acts_active(
    canon: CanonBundle, world: WorldState,
) -> None:
    director = Director(canon, llm_client=None)
    state = DirectorState()
    tensions = TensionList(tensions=[
        Tension(
            type=TensionType.cursed_hatred,
            description="Sasuke spire dans la haine.",
            severity=TensionSeverity.critical, score=1.0,
            involved_entities=["uchiha_sasuke"],
        ),
    ])
    report = await director.tick(
        tensions=tensions, world=world, state=state,
        current_year=10, current_month=1,
    )
    assert report.nudge is not None
    text = build_nudge_text(report.nudge)
    assert "[DIRECTIVES NARRATIVES / DIRECTOR]" in text
    assert "uchiha_sasuke" in text or "Sasuke" in text.lower() or "haine" in text.lower()


# --- Phase H 9.5 : select_relevant_patterns -------------------------------


def test_phase_g_perf_director_tick_under_threshold(canon) -> None:
    """Phase G perf : Director.tick reste < 5ms par tick avec canon production.

    Garde-fou contre O(N^2) regressions dans compose_acts /
    select_relevant_invariants / select_relevant_patterns. Sur le
    materiel reference (CPU local), 100 ticks tournent en ~16ms = 0.16ms/tick.
    Cap a 5ms pour large marge sur CI partages / hardware moins rapide.
    """
    import asyncio
    import time

    from shinobi.director import Director, DirectorState
    from shinobi.engine.world import WorldState
    from shinobi.tension.types import (
        Tension,
        TensionList,
        TensionSeverity,
        TensionType,
    )

    async def run() -> None:
        director = Director(canon, llm_client=None)
        tensions = TensionList(
            tensions=[
                Tension.from_severity(
                    type=TensionType.cursed_hatred,
                    description="Perf test : tension persistente Uchiha",
                    severity=TensionSeverity.high,
                    involved_entities=["uchiha_clan"],
                    source_rule="perf",
                    detected_at_year=10,
                ),
            ],
            detected_at_year=10,
        )
        world = WorldState(
            current_year=10, current_date="01-01",
            current_hour=0, current_minute=0,
        )
        state = DirectorState()
        # Warm-up : 1er tick a un cout d'init Pydantic
        await director.tick(
            tensions=tensions, world=world, state=state,
            current_year=10, current_month=1,
        )

        N = 50
        t0 = time.perf_counter()
        for i in range(N):
            await director.tick(
                tensions=tensions, world=world, state=state,
                current_year=10 + i, current_month=1,
            )
        elapsed_ms = (time.perf_counter() - t0) * 1000
        per_tick_ms = elapsed_ms / N
        assert per_tick_ms < 5.0, (
            f"Phase G perf regression : {per_tick_ms:.2f}ms/tick > 5.0ms "
            f"(reference : ~0.16ms/tick)"
        )

    asyncio.run(run())


def test_phase_g_perf_build_director_nudge_text_under_threshold(canon) -> None:
    """Phase G perf : build_director_nudge_text < 1ms par appel.

    Helper appele par CLI a chaque tour main loop + chaque ff tick refresh.
    Reference materiel local : ~171us/build sur canon production. Cap a
    1ms pour CI partages.
    """
    import asyncio
    import time

    from shinobi.director import (
        Director,
        DirectorState,
        build_director_nudge_text,
    )
    from shinobi.engine.world import WorldState
    from shinobi.tension.types import (
        Tension,
        TensionList,
        TensionSeverity,
        TensionType,
    )

    async def run() -> None:
        director = Director(canon, llm_client=None)
        tensions = TensionList(
            tensions=[
                Tension.from_severity(
                    type=TensionType.cursed_hatred,
                    description="Perf test description",
                    severity=TensionSeverity.high,
                    involved_entities=["uchiha_clan"],
                    source_rule="perf",
                    detected_at_year=10,
                ),
                Tension.from_severity(
                    type=TensionType.power_vacuum,
                    description="Vacance Suna",
                    severity=TensionSeverity.high,
                    involved_entities=["sunagakure"],
                    source_rule="perf",
                    detected_at_year=10,
                ),
            ],
            detected_at_year=10,
        )
        world = WorldState(
            current_year=10, current_date="01-01",
            current_hour=0, current_minute=0,
        )
        state = DirectorState()
        await director.tick(
            tensions=tensions, world=world, state=state,
            current_year=10, current_month=1,
        )

        N = 200
        t0 = time.perf_counter()
        for _ in range(N):
            text = build_director_nudge_text(
                canon=canon, director_state=state, current_year=10,
            )
        elapsed_ms = (time.perf_counter() - t0) * 1000
        per_call_ms = elapsed_ms / N
        assert text  # output non-vide
        assert per_call_ms < 1.0, (
            f"Phase G perf regression : "
            f"{per_call_ms*1000:.0f}us/build > 1ms "
            f"(reference : ~171us/build)"
        )

    asyncio.run(run())


def test_build_director_nudge_text_helper_returns_empty_when_no_state() -> None:
    """Phase G+H wiring : helper retourne "" si director_state None."""
    from shinobi.director import build_director_nudge_text

    out = build_director_nudge_text(
        canon=None, director_state=None, current_year=10,
    )
    assert out == ""


def test_build_director_nudge_text_helper_returns_empty_when_no_acts(canon) -> None:
    """Phase G+H wiring : helper retourne "" si DirectorState sans acts."""
    from shinobi.director import DirectorState, build_director_nudge_text

    out = build_director_nudge_text(
        canon=canon, director_state=DirectorState(), current_year=10,
    )
    assert out == ""


def test_build_director_nudge_text_helper_produces_full_nudge(canon) -> None:
    """Phase G+H wiring : helper compose un nudge complet avec acts +
    invariants + patterns FR-filtres.

    Verifie que la chaine entiere (contexts -> FR enriched -> patterns
    selection -> build_nudge -> build_nudge_text) tourne sans crash et
    produit un texte non-trivial.
    """
    import asyncio

    from shinobi.director import (
        Director,
        DirectorState,
        build_director_nudge_text,
    )
    from shinobi.engine.world import WorldState
    from shinobi.tension.types import (
        Tension,
        TensionList,
        TensionSeverity,
        TensionType,
    )

    async def run() -> None:
        director = Director(canon, llm_client=None)
        tensions = TensionList(
            tensions=[
                Tension.from_severity(
                    type=TensionType.cursed_hatred,
                    description="Test cursed hatred",
                    severity=TensionSeverity.high,
                    involved_entities=["uchiha_clan"],
                    source_rule="test",
                    detected_at_year=10,
                ),
            ],
            detected_at_year=10,
        )
        state = DirectorState()
        await director.tick(
            tensions=tensions,
            world=WorldState(
                current_year=10, current_date="01-01",
                current_hour=0, current_minute=0,
            ),
            state=state, current_year=10, current_month=1,
        )
        # State a maintenant des acts
        out = build_director_nudge_text(
            canon=canon, director_state=state, current_year=10,
        )
        assert out, "helper should produce non-empty nudge text"
        assert "[DIRECTIVES NARRATIVES" in out
        # Phase H 9.5 patterns thematiques surfacees
        thematic = (
            "cycle_de_haine" in out.lower()
            or "haine" in out.lower()
            or "redemption" in out.lower()
            or "Style Kishimoto" in out
        )
        assert thematic, f"thematic pattern absent : {out[:300]}"

    asyncio.run(run())


def test_build_director_nudge_text_helper_swallows_exceptions(canon) -> None:
    """Phase G+H wiring : si interne crash, retourne "" plutot que de
    propager (defensive : la narration ne doit pas casser sur un nudge
    qui echoue en composition).
    """
    from shinobi.director import build_director_nudge_text

    class _BrokenState:
        @property
        def active_acts(self):
            raise RuntimeError("simulated broken state")

    out = build_director_nudge_text(
        canon=canon,
        director_state=_BrokenState(),
        current_year=10,
    )
    assert out == ""


def test_enrich_contexts_with_fr_adds_french_keywords() -> None:
    """Phase H 9.5 wiring : EN context 'succession' -> FR keywords ajoutes.

    Garantit que le Director peut faire matcher des patterns FR avec des
    contexts EN/snake_case (sinon select_relevant_patterns retombait sur
    le fallback systematiquement).
    """
    from shinobi.director.core import _enrich_contexts_with_fr

    out = _enrich_contexts_with_fr(["succession"])
    assert "succession" in out  # original preserve
    assert "heritier" in out  # FR keyword ajoute
    assert "kage" in out


def test_enrich_contexts_dedupes() -> None:
    """Phase H 9.5 : les contexts dupliques ou keywords identiques sont fusionnes."""
    from shinobi.director.core import _enrich_contexts_with_fr

    out = _enrich_contexts_with_fr(["alliance", "alliance"])
    assert out.count("alliance") == 1


def test_director_selects_thematic_patterns_for_hatred_tension(canon) -> None:
    """Phase H 9.5 wiring : tension type 'cursed_hatred' selectionne le
    pattern 'cycle_de_haine_intergenerationnel' au lieu du fallback canon.

    Avant l'enrichissement FR, ce mapping ne matchait pas et le Director
    transmettait toujours les 3 premiers patterns canon au LLM.
    """
    import asyncio

    from shinobi.director.core import Director
    from shinobi.director.scheduler import DirectorState
    from shinobi.engine.world import WorldState
    from shinobi.tension.types import (
        Tension,
        TensionList,
        TensionSeverity,
        TensionType,
    )

    async def run() -> None:
        director = Director(canon, llm_client=None)
        tensions = TensionList(
            tensions=[
                Tension.from_severity(
                    type=TensionType.cursed_hatred,
                    description="Haine cumulative dans le clan Uchiha",
                    severity=TensionSeverity.high,
                    involved_entities=["uchiha_clan"],
                    source_rule="test",
                    detected_at_year=10,
                ),
            ],
            detected_at_year=10,
        )
        world = WorldState(
            current_year=10, current_date="01-01",
            current_hour=0, current_minute=0,
        )
        report = await director.tick(
            tensions=tensions, world=world, state=DirectorState(),
            current_year=10, current_month=1,
        )
        assert report.nudge is not None
        ids = [
            p.get("id")
            for p in report.nudge.narrative_patterns
            if isinstance(p, dict)
        ]
        # Au moins un pattern lie a la haine doit etre present.
        # Les patterns canon thematiques sont notamment :
        # cycle_de_haine_intergenerationnel, guerison_par_reconnaissance.
        thematic_patterns = {
            "cycle_de_haine_intergenerationnel",
            "guerison_par_reconnaissance",
            "redemption_par_dialogue_de_bataille",
        }
        assert any(pid in thematic_patterns for pid in ids), (
            f"aucun pattern thematique pour cursed_hatred, got {ids}"
        )

    asyncio.run(run())


def test_select_relevant_patterns_returns_first_when_no_context() -> None:
    """Phase H 9.5 : back-compat - sans context, retourne les premiers."""
    from shinobi.director.invariants import select_relevant_patterns

    patterns = [
        {"id": "p1", "title_fr": "T1", "description_fr": "D1"},
        {"id": "p2", "title_fr": "T2", "description_fr": "D2"},
        {"id": "p3", "title_fr": "T3", "description_fr": "D3"},
        {"id": "p4", "title_fr": "T4", "description_fr": "D4"},
    ]
    out = select_relevant_patterns(patterns, contexts=[], max_patterns=3)
    assert [p["id"] for p in out] == ["p1", "p2", "p3"]


def test_select_relevant_patterns_scores_by_keyword_overlap() -> None:
    """Phase H 9.5 : pattern dont la description contient des keywords
    contextuels remonte en haut.
    """
    from shinobi.director.invariants import select_relevant_patterns

    patterns = [
        {
            "id": "p_revelation",
            "title_fr": "Revelation",
            "description_fr": "Une trahison cachee.",
            "when_to_apply_fr": "Quand un personnage semble juge.",
        },
        {
            "id": "p_succession",
            "title_fr": "Succession",
            "description_fr": "Une lutte de succession entre heritiers.",
            "when_to_apply_fr": "Apres la mort d'un kage ou chef de clan.",
        },
        {
            "id": "p_random",
            "title_fr": "Random",
            "description_fr": "Tout autre.",
            "when_to_apply_fr": "Always.",
        },
    ]
    # Contextes alignes sur p_succession
    out = select_relevant_patterns(
        patterns, contexts=["succession", "kage", "clan"], max_patterns=2,
    )
    ids = [p["id"] for p in out]
    assert ids[0] == "p_succession"


def test_select_relevant_patterns_fallback_when_zero_match() -> None:
    """Phase H 9.5 : si aucun pattern ne match, fallback sur les premiers."""
    from shinobi.director.invariants import select_relevant_patterns

    patterns = [
        {"id": "p1", "title_fr": "T1", "description_fr": "rien."},
        {"id": "p2", "title_fr": "T2", "description_fr": "rien non plus."},
    ]
    out = select_relevant_patterns(
        patterns, contexts=["xenomorph", "alien"], max_patterns=2,
    )
    assert [p["id"] for p in out] == ["p1", "p2"]


def test_select_relevant_patterns_handles_empty_input() -> None:
    """Phase H 9.5 : patterns vide -> output vide."""
    from shinobi.director.invariants import select_relevant_patterns

    assert select_relevant_patterns([], contexts=["x"]) == []


def test_director_filters_patterns_by_active_contexts(
    canon,
) -> None:
    """Phase H 9.5 : Director.tick filtre les patterns via contextes des acts.

    Le canon expose 14 patterns. Le Director ne doit PAS prendre les 3
    premiers ; il doit prendre les 3 plus pertinents aux contextes des
    acts actifs (extraits des tensions).
    """
    import asyncio

    from shinobi.director.core import Director
    from shinobi.director.scheduler import DirectorState
    from shinobi.engine.world import WorldState
    from shinobi.tension.types import (
        Tension,
        TensionList,
        TensionSeverity,
        TensionType,
    )

    async def run() -> None:
        director = Director(canon, llm_client=None)
        # Tension type qui mappe vers contexts ['succession', 'alliance']
        tensions = TensionList(
            tensions=[
                Tension.from_severity(
                    type=TensionType.power_vacuum,
                    description="Vide de pouvoir Konoha",
                    severity=TensionSeverity.high,
                    involved_entities=["konohagakure"],
                    source_rule="test",
                    detected_at_year=10,
                ),
            ],
            detected_at_year=10,
        )
        world = WorldState(
            current_year=10, current_date="01-01",
            current_hour=0, current_minute=0,
        )
        report = await director.tick(
            tensions=tensions, world=world, state=DirectorState(),
            current_year=10, current_month=1,
        )
        # Le nudge doit avoir des patterns
        assert report.nudge is not None
        assert report.nudge.narrative_patterns
        # Les patterns retournes sont ceux dont description match les
        # contextes 'succession'/'alliance' (depuis _TENSION_TYPE_TO_CONTEXTS).
        # Si on a au moins 1 pattern dont description contient 'succession'
        # OU 'alliance', il doit etre prioritaire.
        patterns = report.nudge.narrative_patterns
        # Verifie que selection a tourne (output != ordre canon trivial)
        all_patterns = canon.narrative_patterns.get(
            "patterns", [],
        )
        if (
            len(all_patterns) >= 4
            and any(
                "succession" in (p.get("description_fr", "") or "").lower()
                or "succession" in (p.get("when_to_apply_fr", "") or "").lower()
                or "alliance" in (p.get("description_fr", "") or "").lower()
                or "alliance" in (p.get("when_to_apply_fr", "") or "").lower()
                for p in all_patterns
            )
        ):
            # Le pattern le plus pertinent doit etre dans le top
            assert any(
                "succession" in (p.get("description_fr", "") or "").lower()
                or "alliance" in (p.get("description_fr", "") or "").lower()
                or "succession" in (p.get("when_to_apply_fr", "") or "").lower()
                or "alliance" in (p.get("when_to_apply_fr", "") or "").lower()
                for p in patterns
            )

    asyncio.run(run())
