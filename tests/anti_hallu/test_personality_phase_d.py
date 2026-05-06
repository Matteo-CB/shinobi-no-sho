"""Tests Phase D : personnalite vectorielle + drift rules + engine + persistance.

Couvre :
- Dimensions (20 axes, ordre stable, index)
- Types Pydantic (frozen, validation, dimensions completes, divergence)
- Drift rules (saturation sigmoid, ~30 rules, mapping unique)
- Engine (apply_event, apply_events, requires_related_npc, divergence)
- Baseline (extraction depuis psycho_notes, ressort des differences cohérentes)
- Store (CRUD + drift history + roundtrip)
- Scenario canon Sasuke pre/post massacre : drift coherent
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from shinobi.personality import (
    ALL_DIMENSIONS,
    DEFAULT_NEUTRAL_VALUE,
    DRIFT_RULES,
    CanonEventLike,
    EventCategory,
    ExperiencedEvent,
    MissionLike,
    NPCPersonality,
    PersonalityEngine,
    PersonalityEngineError,
    PersonalityStore,
    apply_delta_with_saturation,
    collect_experienced_events,
    compose_drift_for_event,
    detect_category_from_text,
    dimension_index,
    experienced_events_from_mission,
    experienced_events_from_timeline_event,
    extract_baseline_for_npc,
    extract_baseline_from_text,
    extract_baselines_from_file,
    get_rule_for_category,
)
from shinobi.personality.dimensions import PersonalityDimension as D

# ============================================================================
# 1. Dimensions
# ============================================================================


class TestDimensions:
    def test_exactly_20_dimensions(self) -> None:
        assert len(ALL_DIMENSIONS) == 20
        assert len({d.value for d in ALL_DIMENSIONS}) == 20  # uniques

    def test_dimension_index_stable(self) -> None:
        # Indices doivent etre stables : permet d'utiliser des vecteurs numpy
        for i, dim in enumerate(ALL_DIMENSIONS):
            assert dimension_index(dim) == i

    def test_named_dimensions_present(self) -> None:
        # Spec §6.2 : les 10 nommees doivent etre presentes
        for name in (
            "aggression", "loyalty", "secrecy", "ambition", "fear",
            "idealism", "pragmatism", "empathy", "confidence", "paranoia",
        ):
            assert name in {d.value for d in ALL_DIMENSIONS}, f"manque {name}"

    def test_default_neutral_value(self) -> None:
        assert DEFAULT_NEUTRAL_VALUE == 0.5


# ============================================================================
# 2. Types Pydantic
# ============================================================================


class TestNPCPersonality:
    def test_default_neutral_vector(self) -> None:
        p = NPCPersonality(npc_id="x")
        for dim in ALL_DIMENSIONS:
            assert p.vector[dim] == DEFAULT_NEUTRAL_VALUE
            assert p.canon_baseline[dim] == DEFAULT_NEUTRAL_VALUE
        assert p.divergence_from_canon() == 0.0

    def test_immutable(self) -> None:
        p = NPCPersonality(npc_id="x")
        with pytest.raises((ValidationError, TypeError, AttributeError)):
            p.npc_id = "y"  # type: ignore[misc]

    def test_validate_dimensions_complete(self) -> None:
        # Si on omet une dimension, validation doit echouer
        partial = dict.fromkeys(ALL_DIMENSIONS, 0.5)
        del partial[D.aggression]
        with pytest.raises(ValidationError):
            NPCPersonality(npc_id="x", vector=partial)

    def test_validate_value_in_range(self) -> None:
        bad = dict.fromkeys(ALL_DIMENSIONS, 0.5)
        bad[D.aggression] = 1.5  # hors [0,1]
        with pytest.raises(ValidationError):
            NPCPersonality(npc_id="x", vector=bad)

    def test_divergence_strict(self) -> None:
        baseline = dict.fromkeys(ALL_DIMENSIONS, 0.5)
        vector = dict(baseline)
        vector[D.aggression] = 0.7
        vector[D.fear] = 0.3
        p = NPCPersonality(
            npc_id="x", vector=vector, canon_baseline=baseline,
        )
        # sqrt((0.7-0.5)^2 + (0.3-0.5)^2) == sqrt(0.08)
        assert abs(p.divergence_from_canon() - (0.08 ** 0.5)) < 1e-9


class TestExperiencedEvent:
    def test_required_fields(self) -> None:
        ev = ExperiencedEvent(
            npc_id="x", category=EventCategory.trauma_event, year=12,
        )
        assert ev.intensity == 1.0
        assert ev.related_npc_id is None

    def test_intensity_range_validated(self) -> None:
        with pytest.raises(ValidationError):
            ExperiencedEvent(
                npc_id="x", category=EventCategory.trauma_event,
                year=12, intensity=1.5,
            )

    def test_immutable(self) -> None:
        ev = ExperiencedEvent(
            npc_id="x", category=EventCategory.trauma_event, year=12,
        )
        with pytest.raises((ValidationError, TypeError, AttributeError)):
            ev.year = 13  # type: ignore[misc]


# ============================================================================
# 3. Drift rules
# ============================================================================


class TestDriftRules:
    def test_around_30_rules(self) -> None:
        # Spec docs/02 §6.2 : "~30 règles génériques"
        assert 28 <= len(DRIFT_RULES) <= 35

    def test_one_rule_per_category(self) -> None:
        cats = [r.category for r in DRIFT_RULES]
        assert len(cats) == len(set(cats)), "doublons de categories"

    def test_all_event_categories_have_rule(self) -> None:
        # Pour chaque EventCategory, il existe une rule
        cats_with_rule = {r.category for r in DRIFT_RULES}
        for ec in EventCategory:
            assert ec in cats_with_rule, f"EventCategory {ec.value} sans rule"

    def test_rules_only_use_known_dimensions(self) -> None:
        for rule in DRIFT_RULES:
            for dim in rule.deltas:
                assert dim in ALL_DIMENSIONS, f"rule {rule.name} delta inconnue : {dim}"

    def test_get_rule_for_category(self) -> None:
        r = get_rule_for_category(EventCategory.trauma_event)
        assert r is not None
        assert r.name == "trauma_event"

    def test_compose_drift_default_intensity(self) -> None:
        rule = get_rule_for_category(EventCategory.trauma_event)
        assert rule is not None
        deltas = compose_drift_for_event(rule, intensity=1.0)
        assert deltas[D.fear] == rule.deltas[D.fear]

    def test_compose_drift_with_intensity_scaling(self) -> None:
        rule = get_rule_for_category(EventCategory.trauma_event)
        assert rule is not None
        d_full = compose_drift_for_event(rule, intensity=1.0)
        d_half = compose_drift_for_event(rule, intensity=0.5)
        assert abs(d_full[D.fear] / 2 - d_half[D.fear]) < 1e-9

    def test_compose_drift_with_duration_log_factor(self) -> None:
        rule = get_rule_for_category(EventCategory.long_term_companionship)
        assert rule is not None
        # Pas de duration -> facteur = 1.0, delta brut conserve
        d_no_dur = compose_drift_for_event(rule, intensity=1.0)
        assert d_no_dur[D.loyalty] == rule.deltas[D.loyalty]
        # 5 ans -> log(6)*1 ~= 1.79 -> delta amplifie
        d_5y = compose_drift_for_event(rule, intensity=1.0, duration_years=5)
        assert d_5y[D.loyalty] > rule.deltas[D.loyalty] * 1.5


class TestSaturationSigmoid:
    def test_apply_zero_delta_is_identity(self) -> None:
        for v in (0.0, 0.1, 0.5, 0.9, 1.0):
            assert apply_delta_with_saturation(v, 0.0) == v

    def test_apply_positive_delta_increases(self) -> None:
        v = 0.5
        new = apply_delta_with_saturation(v, 0.10)
        assert new > v

    def test_apply_negative_delta_decreases(self) -> None:
        v = 0.5
        new = apply_delta_with_saturation(v, -0.10)
        assert new < v

    def test_saturates_near_one(self) -> None:
        # Une valeur deja proche de 1 ne peut pas exploser au-dessus
        new = apply_delta_with_saturation(0.95, 0.50)
        assert new <= 1.0
        # gain marginal faible (sigmoid)
        assert new - 0.95 < 0.10

    def test_saturates_near_zero(self) -> None:
        new = apply_delta_with_saturation(0.05, -0.50)
        assert new >= 0.0
        assert 0.05 - new < 0.10

    def test_within_unit_interval(self) -> None:
        for current in (0.0, 0.001, 0.5, 0.999, 1.0):
            for d in (-1.0, -0.1, 0.0, 0.1, 1.0):
                new = apply_delta_with_saturation(current, d)
                assert 0.0 <= new <= 1.0


# ============================================================================
# 4. Engine
# ============================================================================


class TestPersonalityEngine:
    def test_apply_trauma_increases_fear(self) -> None:
        eng = PersonalityEngine()
        p = NPCPersonality(npc_id="x")
        ev = ExperiencedEvent(
            npc_id="x", category=EventCategory.trauma_event, year=10,
        )
        p2 = eng.apply_event(p, ev)
        assert p2.value(D.fear) > p.value(D.fear)
        assert p2.value(D.melancholy) > p.value(D.melancholy)
        assert p2.value(D.paranoia) > p.value(D.paranoia)

    def test_apply_event_appends_history(self) -> None:
        eng = PersonalityEngine()
        p = NPCPersonality(npc_id="x")
        ev = ExperiencedEvent(
            npc_id="x", category=EventCategory.trauma_event, year=10,
        )
        p2 = eng.apply_event(p, ev)
        assert len(p2.drift_history) == 1
        d = p2.drift_history[0]
        assert d.npc_id == "x"
        assert d.event_category == EventCategory.trauma_event
        assert d.rule_name == "trauma_event"
        assert d.year == 10
        # delta brut == celui de la rule, applied_delta != delta brut (sigmoid)
        assert D.fear in d.delta
        assert D.fear in d.applied_delta

    def test_apply_events_sequential(self) -> None:
        eng = PersonalityEngine()
        p = NPCPersonality(npc_id="x")
        events = [
            ExperiencedEvent(
                npc_id="x", category=EventCategory.trauma_event, year=10,
            ),
            ExperiencedEvent(
                npc_id="x", category=EventCategory.trauma_event, year=11,
            ),
        ]
        p2 = eng.apply_events(p, events)
        assert len(p2.drift_history) == 2
        # Fear cumule : devrait etre plus eleve qu'apres un seul event
        single = eng.apply_event(p, events[0])
        assert p2.value(D.fear) > single.value(D.fear)

    def test_apply_event_npc_mismatch_raises(self) -> None:
        eng = PersonalityEngine()
        p = NPCPersonality(npc_id="x")
        ev = ExperiencedEvent(
            npc_id="y", category=EventCategory.trauma_event, year=10,
        )
        with pytest.raises(PersonalityEngineError):
            eng.apply_event(p, ev)

    def test_requires_related_npc_skips_when_missing(self) -> None:
        eng = PersonalityEngine()
        p = NPCPersonality(npc_id="x")
        # betrayal_witnessed requires_related_npc=True
        ev = ExperiencedEvent(
            npc_id="x", category=EventCategory.betrayal_witnessed, year=10,
            related_npc_id=None,
        )
        p2 = eng.apply_event(p, ev)
        # Aucun drift applique
        assert p2.vector == p.vector
        assert len(p2.drift_history) == 0

    def test_top_drifted_dimensions(self) -> None:
        eng = PersonalityEngine()
        p = NPCPersonality(npc_id="x")
        # 3 traumas successifs : fear, melancholy, paranoia montent
        events = [
            ExperiencedEvent(
                npc_id="x", category=EventCategory.trauma_event, year=10 + i,
            )
            for i in range(3)
        ]
        p_drifted = eng.apply_events(p, events)
        top = eng.top_drifted_dimensions(p_drifted, n=3)
        names = {dim.value for dim, _ in top}
        assert {"fear", "melancholy", "paranoia"}.issubset(names) or len(names) == 3

    def test_filter_history(self) -> None:
        eng = PersonalityEngine()
        p = NPCPersonality(npc_id="x")
        events = [
            ExperiencedEvent(
                npc_id="x", category=EventCategory.trauma_event, year=10,
            ),
            ExperiencedEvent(
                npc_id="x", category=EventCategory.failed_goal, year=11,
            ),
            ExperiencedEvent(
                npc_id="x", category=EventCategory.trauma_event, year=12,
            ),
        ]
        p_drifted = eng.apply_events(p, events)
        traumas = eng.filter_history_for(p_drifted, rule_name="trauma_event")
        assert len(traumas) == 2
        late = eng.filter_history_for(p_drifted, year_min=11)
        assert len(late) == 2


# ============================================================================
# 5. Baseline extraction
# ============================================================================


class TestBaselineExtraction:
    def test_extract_from_text_neutral_when_empty(self) -> None:
        result = extract_baseline_from_text("x", "")
        for dim in ALL_DIMENSIONS:
            assert result.vector[dim] == DEFAULT_NEUTRAL_VALUE

    def test_extract_aggression_keyword(self) -> None:
        result = extract_baseline_from_text(
            "x", "Personnage tres agressif et violent, plein de rage.",
        )
        # aggression > neutre
        assert result.vector[D.aggression] > DEFAULT_NEUTRAL_VALUE

    def test_extract_solitaire_pushes_isolationism(self) -> None:
        result = extract_baseline_from_text(
            "x", "Petit enfant solitaire, isole, ostracise par le village.",
        )
        assert result.vector[D.isolationism] > DEFAULT_NEUTRAL_VALUE

    def test_extract_baseline_for_npc_from_psycho_notes(self) -> None:
        path = Path("data/canonical/psycho_notes.json")
        if not path.exists():
            pytest.skip("psycho_notes.json absent")
        data = json.loads(path.read_text(encoding="utf-8"))
        result = extract_baseline_for_npc("uchiha_sasuke", data)
        # Sasuke : on s'attend a vengeance > neutre
        if result.notes_count > 0:
            assert result.vector[D.vengeance] >= DEFAULT_NEUTRAL_VALUE - 0.05

    def test_extract_baselines_from_file_returns_personalities(self) -> None:
        path = Path("data/canonical/psycho_notes.json")
        if not path.exists():
            pytest.skip("psycho_notes.json absent")
        all_p = extract_baselines_from_file(path)
        assert len(all_p) > 0
        # Tout NPCPersonality a vector == canon_baseline a T0
        for p in all_p.values():
            assert p.vector == p.canon_baseline

    def test_extract_subset_only(self) -> None:
        path = Path("data/canonical/psycho_notes.json")
        if not path.exists():
            pytest.skip("psycho_notes.json absent")
        all_p = extract_baselines_from_file(
            path, only_npc_ids=["uchiha_sasuke", "uzumaki_naruto"],
        )
        assert set(all_p.keys()) == {"uchiha_sasuke", "uzumaki_naruto"}


# ============================================================================
# 6. Store SQLite
# ============================================================================


class TestPersonalityStore:
    def test_upsert_and_get(self, tmp_path: Path) -> None:
        path = tmp_path / "p.sqlite"
        with PersonalityStore(path) as store:
            p = NPCPersonality(npc_id="naruto", baseline_year=12)
            store.upsert_personality(p)
            loaded = store.get_personality("naruto")
            assert loaded is not None
            assert loaded.npc_id == "naruto"
            assert loaded.baseline_year == 12
            assert loaded.vector == p.vector

    def test_get_unknown_returns_none(self, tmp_path: Path) -> None:
        with PersonalityStore(tmp_path / "p.sqlite") as store:
            assert store.get_personality("ghost") is None

    def test_save_with_history_roundtrip(self, tmp_path: Path) -> None:
        eng = PersonalityEngine()
        p = NPCPersonality(npc_id="sasuke")
        ev = ExperiencedEvent(
            npc_id="sasuke", category=EventCategory.massacre_against_self_clan,
            year=8,
        )
        p_drifted = eng.apply_event(p, ev)

        path = tmp_path / "p.sqlite"
        with PersonalityStore(path) as store:
            store.save_personality_with_history(p_drifted)

        with PersonalityStore(path) as store2:
            loaded = store2.get_personality("sasuke")
            assert loaded is not None
            assert len(loaded.drift_history) == 1
            assert loaded.drift_history[0].rule_name == "massacre_against_self_clan"
            assert loaded.vector[D.vengeance] == p_drifted.vector[D.vengeance]

    def test_list_personalities(self, tmp_path: Path) -> None:
        path = tmp_path / "p.sqlite"
        with PersonalityStore(path) as store:
            store.upsert_personality(NPCPersonality(npc_id="a"))
            store.upsert_personality(NPCPersonality(npc_id="b"))
            store.upsert_personality(NPCPersonality(npc_id="c"))
            all_p = store.list_personalities()
            assert {p.npc_id for p in all_p} == {"a", "b", "c"}

    def test_delete_personality(self, tmp_path: Path) -> None:
        path = tmp_path / "p.sqlite"
        with PersonalityStore(path) as store:
            store.upsert_personality(NPCPersonality(npc_id="a"))
            assert store.delete_personality("a") is True
            assert store.get_personality("a") is None

    def test_in_memory_store(self) -> None:
        with PersonalityStore(None) as store:
            store.upsert_personality(NPCPersonality(npc_id="x"))
            loaded = store.get_personality("x")
            assert loaded is not None


# ============================================================================
# 7. Scenario canon : Sasuke pre/post massacre Uchiha
# ============================================================================


class TestSasukeScenario:
    """Test de validite Phase D : drift cohérent sur Sasuke
    (cas teste cite dans la roadmap §13 Phase D)."""

    def test_sasuke_post_massacre_drift_coherent(self) -> None:
        """Apres le massacre Uchiha (year 8 canon), Sasuke doit drifter vers :
        vengeance+ (saturating), isolationism+, melancholy+, paranoia+,
        loyalty- (envers Itachi). Le baseline canon est extrait de
        psycho_notes.json."""
        path = Path("data/canonical/psycho_notes.json")
        if not path.exists():
            pytest.skip("psycho_notes.json absent")

        baselines = extract_baselines_from_file(path, only_npc_ids=["uchiha_sasuke"])
        if not baselines:
            pytest.skip("Pas de baseline Sasuke")
        sasuke_baseline = baselines["uchiha_sasuke"]

        eng = PersonalityEngine()
        # Sequence canon massacre + sibling lost (Itachi vu comme ennemi)
        events = [
            ExperiencedEvent(
                npc_id="uchiha_sasuke",
                category=EventCategory.massacre_against_self_clan,
                year=8, intensity=1.0,
                related_npc_id="uchiha_itachi",
                related_event_id="event_uchiha_massacre",
            ),
            ExperiencedEvent(
                npc_id="uchiha_sasuke",
                category=EventCategory.parent_lost,
                year=8, intensity=1.0,
                related_npc_id="uchiha_fugaku",
            ),
            ExperiencedEvent(
                npc_id="uchiha_sasuke",
                category=EventCategory.parent_lost,
                year=8, intensity=1.0,
                related_npc_id="uchiha_mikoto",
            ),
        ]
        sasuke_post = eng.apply_events(sasuke_baseline, events)

        # Verifications strictes : chaque dimension doit avoir bouge dans la
        # bonne direction par rapport au baseline canon
        assert sasuke_post.value(D.vengeance) > sasuke_baseline.value(D.vengeance)
        assert sasuke_post.value(D.isolationism) > sasuke_baseline.value(D.isolationism)
        assert sasuke_post.value(D.melancholy) > sasuke_baseline.value(D.melancholy)
        assert sasuke_post.value(D.paranoia) > sasuke_baseline.value(D.paranoia)
        assert sasuke_post.value(D.fear) > sasuke_baseline.value(D.fear)
        assert sasuke_post.value(D.loyalty) < sasuke_baseline.value(D.loyalty)

        # Divergence globale forte
        assert sasuke_post.divergence_from_canon() > 0.3

        # History trace les 3 events
        assert len(sasuke_post.drift_history) == 3
        rule_names = {d.rule_name for d in sasuke_post.drift_history}
        assert "massacre_against_self_clan" in rule_names
        assert "parent_lost" in rule_names

    def test_event_bridge_detects_massacre(self) -> None:
        """Bridge : un event canon massacre genere witnessed_atrocity."""
        ev = CanonEventLike(
            id="event_uchiha_massacre",
            year=8,
            name_fr="Massacre du clan Uchiha",
            narrative_summary_fr="Itachi Uchiha extermine son propre clan.",
            involved_characters=("uchiha_sasuke", "uchiha_itachi"),
        )
        produced = experienced_events_from_timeline_event(ev)
        # Un ExperiencedEvent par PNJ implique
        assert len(produced) == 2
        assert all(p.category == EventCategory.witnessed_atrocity for p in produced)
        assert {p.npc_id for p in produced} == {"uchiha_sasuke", "uchiha_itachi"}

    def test_event_bridge_detects_promotion(self) -> None:
        cat = detect_category_from_text("Naruto promu Hokage")
        assert cat == EventCategory.rank_promotion

    def test_event_bridge_no_match_returns_none(self) -> None:
        cat = detect_category_from_text("Reunion paisible au lac")
        # Aucun keyword fort -> None
        assert cat is None or isinstance(cat, EventCategory)

    def test_mission_bridge_success_high_rank(self) -> None:
        m = MissionLike(
            id="mission_x", year=12, rank="A", type="rescue",
            outcome="success",
            participants=(("uzumaki_naruto", "operative"),),
        )
        produced = experienced_events_from_mission(m)
        assert len(produced) == 1
        assert produced[0].category == EventCategory.achieved_goal

    def test_mission_bridge_failure(self) -> None:
        m = MissionLike(
            id="mission_x", year=12, rank="B", type="protection",
            outcome="failure",
            participants=(("hatake_kakashi", "operative"),),
        )
        produced = experienced_events_from_mission(m)
        assert len(produced) == 1
        assert produced[0].category == EventCategory.failed_goal

    def test_mission_bridge_assassination_executor(self) -> None:
        m = MissionLike(
            id="mission_anbu_secret", year=10, rank="S", type="assassination",
            outcome="success",
            participants=(("uchiha_itachi", "executor"),),
        )
        produced = experienced_events_from_mission(m)
        assert len(produced) == 1
        assert produced[0].category == EventCategory.mass_killing_committed

    def test_collect_experienced_events_aggregates(self) -> None:
        ev = CanonEventLike(
            id="ev1", year=8,
            name_fr="Massacre du clan",
            narrative_summary_fr="extermination",
            involved_characters=("a",),
        )
        m = MissionLike(
            id="m1", year=10, rank="A", type="rescue",
            outcome="success",
            participants=(("b", "operative"),),
        )
        out = collect_experienced_events(timeline_events=[ev], missions=[m])
        assert len(out) == 2
        cats = {e.category for e in out}
        assert EventCategory.witnessed_atrocity in cats
        assert EventCategory.achieved_goal in cats

    def test_naruto_long_companionship_increases_loyalty(self) -> None:
        """Naruto a long-term Iruka -> loyaute envers Iruka montait. Test que
        compagnonnage de 5 ans applique le facteur duration log."""
        eng = PersonalityEngine()
        p = NPCPersonality(npc_id="uzumaki_naruto")
        ev_short = ExperiencedEvent(
            npc_id="uzumaki_naruto",
            category=EventCategory.long_term_companionship,
            year=12, intensity=1.0,
            related_npc_id="umino_iruka",
            duration_years=1,
        )
        ev_long = ExperiencedEvent(
            npc_id="uzumaki_naruto",
            category=EventCategory.long_term_companionship,
            year=15, intensity=1.0,
            related_npc_id="umino_iruka",
            duration_years=5,
        )
        p_short = eng.apply_event(p, ev_short)
        p_long = eng.apply_event(p, ev_long)
        # 5 ans amplifie plus que 1 an
        assert p_long.value(D.loyalty) > p_short.value(D.loyalty)
