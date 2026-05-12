"""Tests cross-validation Phase H.

Verifie la coherence referentielle entre les 5 datasets Phase H et le
canon canonique :
- Tous les `event_id` referencs dans 9.4 (divergence_points) existent
  dans 9.1 (timeline_events_enriched) ou dans canon timeline_events.
- Tous les `character_id` keys de 9.2 (deep_motivations) existent dans
  canon characters.
- Tous les `leader_id` referencs dans 9.3 (political_forces) existent
  dans canon characters.
- Tous les `members` / `allies` / `enemies` dans 9.3 sont des ids canon
  valides (character ou faction).
- Cap de 5 erreurs maximum tolerees (LLM peut introduire qq references
  approximatives, mais > 5 -> probleme systemique).

Si Phase H a ete tournee proprement, ces tests passent.
"""

from __future__ import annotations

import pytest

from shinobi.canon.loader import load_canon
from shinobi.canon.models import CanonBundle


@pytest.fixture(scope="module")
def canon() -> CanonBundle:
    return load_canon()


# --- 9.1 Timeline events enrichis -------------------------------------------


def test_phase_h_timeline_events_enriched_present(canon: CanonBundle) -> None:
    """9.1 doit contenir au moins 200 events (target spec doc 02 §9.1)."""
    assert len(canon.timeline_events_enriched) >= 200, (
        f"Spec target 200-500 events, got {len(canon.timeline_events_enriched)}"
    )


def test_phase_h_enriched_includes_canon_events(canon: CanonBundle) -> None:
    """Les 60 events canon de timeline_events.json doivent tous etre
    presents dans timeline_events_enriched (preserve l'historique)."""
    canon_ids = set(canon.timeline_events.keys())
    enriched_ids = set(canon.timeline_events_enriched.keys())
    missing = canon_ids - enriched_ids
    assert not missing, f"{len(missing)} canon events missing from enriched : {sorted(missing)[:5]}"


# --- 9.2 Deep motivations ----------------------------------------------------


def test_phase_h_motivations_count(canon: CanonBundle) -> None:
    """50 PNJ profils attendus (top-50 spec)."""
    assert len(canon.deep_motivations) == 50, (
        f"Expected 50 deep_motivations profiles, got {len(canon.deep_motivations)}"
    )


def test_phase_h_motivations_chars_in_canon(canon: CanonBundle) -> None:
    """Tous les character_id keys de 9.2 doivent etre dans canon.characters."""
    motivation_ids = set(canon.deep_motivations.keys())
    canon_char_ids = set(canon.characters.keys())
    invalid = motivation_ids - canon_char_ids
    assert not invalid, (
        f"deep_motivations contient {len(invalid)} ids non-canon : "
        f"{sorted(invalid)[:5]}"
    )


# --- 9.3 Political forces ----------------------------------------------------


def test_phase_h_political_forces_present(canon: CanonBundle) -> None:
    """Au moins 25 factions (target spec)."""
    factions = canon.political_forces.get("factions", [])
    assert len(factions) >= 25, (
        f"Expected >= 25 factions, got {len(factions)}"
    )


def test_phase_h_political_forces_leader_ids_canon(canon: CanonBundle) -> None:
    """Tous les leader_id non-null doivent etre des character_id canon.

    Tolere 5 erreurs max (LLM peut citer des ids approximatifs).
    """
    canon_chars = set(canon.characters.keys())
    factions = canon.political_forces.get("factions", [])
    invalid: list[str] = []
    for f in factions:
        leader = f.get("leader_id")
        if leader and leader not in canon_chars:
            invalid.append(f"{f.get('id', '?')} -> leader '{leader}'")
    assert len(invalid) <= 5, (
        f"{len(invalid)} factions ont leader_id non-canon : "
        f"{invalid[:5]}"
    )


def test_phase_h_political_forces_members_mostly_canon(
    canon: CanonBundle,
) -> None:
    """La plupart des `members` sont des character_id canon.

    Tolere jusqu'a 20% de members invalides (LLM peut citer des
    sub-organizations ou groupes non-individuels).
    """
    canon_chars = set(canon.characters.keys())
    factions = canon.political_forces.get("factions", [])
    total_members = 0
    invalid_members = 0
    for f in factions:
        for m in f.get("members", []):
            total_members += 1
            if m not in canon_chars:
                invalid_members += 1
    if total_members > 0:
        ratio = invalid_members / total_members
        assert ratio <= 0.20, (
            f"{invalid_members}/{total_members} members non-canon "
            f"({ratio:.1%}) - LLM accuracy degraded ?"
        )


# --- 9.4 Divergence points ---------------------------------------------------


def test_phase_h_divergence_points_count(canon: CanonBundle) -> None:
    """10-30 divergence points (target spec)."""
    pts = canon.divergence_points.get("divergence_points", [])
    assert 10 <= len(pts) <= 35, (
        f"Expected 10-35 divergence points, got {len(pts)}"
    )


def test_phase_h_divergence_event_ids_in_timeline(
    canon: CanonBundle,
) -> None:
    """Tous les event_id dans 9.4 doivent etre dans 9.1 OU canon timeline.

    Tolere 3 erreurs max (LLM peut citer des events implicites).
    """
    pts = canon.divergence_points.get("divergence_points", [])
    valid_ids = set(canon.timeline_events.keys()) | set(canon.timeline_events_enriched.keys())
    invalid: list[str] = []
    for pt in pts:
        eid = pt.get("event_id", "?")
        if eid not in valid_ids:
            invalid.append(eid)
    assert len(invalid) <= 5, (
        f"{len(invalid)} divergence_points avec event_id non-trouve : "
        f"{invalid[:5]}"
    )


# --- 9.5 Narrative patterns --------------------------------------------------


def test_phase_h_patterns_count(canon: CanonBundle) -> None:
    """5-20 patterns Kishimoto (target spec)."""
    patterns = canon.narrative_patterns.get("patterns", [])
    assert 5 <= len(patterns) <= 20, (
        f"Expected 5-20 patterns, got {len(patterns)}"
    )


def test_phase_h_patterns_have_canon_examples(canon: CanonBundle) -> None:
    """Chaque pattern doit avoir 2-8 exemples canon (text-level)."""
    patterns = canon.narrative_patterns.get("patterns", [])
    for p in patterns:
        examples = p.get("canon_examples", [])
        assert 2 <= len(examples) <= 10, (
            f"pattern {p.get('id')} a {len(examples)} examples (expected 2-10)"
        )


# --- Cohérence stricte cross-datasets (locks CI contre futures regressions) ---


def test_phase_h_9_2_all_keys_in_canon_characters(canon: CanonBundle) -> None:
    """Phase H 9.2 strict : 100% des deep_motivations keys doivent etre
    dans canon.characters. Sinon le LLM selector recoit un profil pour
    un personnage non-canon -> hallucination potentielle.
    """
    invalid = [
        cid for cid in canon.deep_motivations
        if cid not in canon.characters
    ]
    assert not invalid, (
        f"Phase H 9.2 : {len(invalid)} deep_motivations keys absentes "
        f"de canon.characters : {invalid[:10]}"
    )


def test_phase_h_9_3_all_leader_ids_in_canon_characters(canon: CanonBundle) -> None:
    """Phase H 9.3 strict : 100% des leader_ids non-null doivent etre
    dans canon.characters. Sinon `political_alliance_brittle_via_dead_leader`
    n'arrive jamais a faire son lookup char_deaths.
    """
    invalid = []
    for f in canon.political_forces.get("factions", []):
        lid = f.get("leader_id")
        if isinstance(lid, str) and lid and lid not in canon.characters:
            invalid.append((f.get("id"), lid))
    assert not invalid, (
        f"Phase H 9.3 : {len(invalid)} leader_ids absents de canon.characters : "
        f"{invalid[:10]}"
    )


def test_phase_h_9_3_members_mostly_in_canon(canon: CanonBundle) -> None:
    """Phase H 9.3 strict : 100% des members doivent etre dans canon.characters
    (le post-processeur normalize_canon_refs.py a garanti ce mapping).
    """
    invalid = []
    total = 0
    for f in canon.political_forces.get("factions", []):
        members = f.get("members", [])
        if not isinstance(members, list):
            continue
        for m in members:
            if not isinstance(m, str) or not m:
                continue
            total += 1
            if m not in canon.characters:
                invalid.append((f.get("id"), m))
    assert not invalid, (
        f"Phase H 9.3 : {len(invalid)}/{total} members absents de canon : "
        f"{invalid[:10]}"
    )


def test_phase_h_9_4_all_event_ids_in_canon_timeline(canon: CanonBundle) -> None:
    """Phase H 9.4 strict : 100% des divergence event_ids doivent etre
    dans canon.timeline_events. Sinon l'urgency boost dans act_composer
    et le block 'POINT DE DIVERGENCE' dans le generator prompt ne firent
    pas pour cet event.
    """
    invalid = []
    for d in canon.divergence_points.get("divergence_points", []):
        eid = d.get("event_id")
        if isinstance(eid, str) and eid and eid not in canon.timeline_events:
            invalid.append(eid)
    assert not invalid, (
        f"Phase H 9.4 : {len(invalid)} event_ids absents de canon.timeline_events : "
        f"{invalid[:10]}"
    )


def test_phase_h_9_1_covers_all_canon_timeline_events(canon: CanonBundle) -> None:
    """Phase H 9.1 strict : 100% des canon.timeline_events doivent avoir
    une entry dans timeline_events_enriched.

    Sans couverture complete, certaines cancellations Phase F seraient
    rejetees pour 'no enriched subjects' alors que d'autres beneficient de
    la guidance generator + actor_overlap. Cohérence inter-event.
    """
    missing = [
        eid for eid in canon.timeline_events
        if eid not in canon.timeline_events_enriched
    ]
    assert not missing, (
        f"Phase H 9.1 : {len(missing)} canon timeline events sans enrichment : "
        f"{missing[:10]}"
    )


def test_phase_h_9_5_all_patterns_have_required_fields(canon: CanonBundle) -> None:
    """Phase H 9.5 strict : tous les patterns ont les fields critiques
    (id, title, description, when_to_apply) dans la langue active.

    Phase i18n.7 : selon la langue active, les fields sont suffixes par
    le code lang (title_fr / title_en / title_ja ...). On accepte donc
    n'importe quel suffixe lang supporte tant que les 3 fields sont
    presents et non vides.
    """
    from shinobi.i18n.loader import SUPPORTED_LANGUAGES

    field_bases = ("title", "description", "when_to_apply")
    candidate_suffixes = ["fr", *(lng for lng in SUPPORTED_LANGUAGES if lng != "fr")]
    incomplete: list[tuple[str, str]] = []
    for p in canon.narrative_patterns.get("patterns", []):
        if not isinstance(p, dict):
            incomplete.append(("?", "not_a_dict"))
            continue
        if not p.get("id"):
            incomplete.append((p.get("id", "?"), "id"))
        for base in field_bases:
            # Trouve au moins UNE variante lang-suffixee non vide.
            found = any(
                p.get(f"{base}_{suffix}") for suffix in candidate_suffixes
            )
            if not found:
                incomplete.append((p.get("id", "?"), base))
    assert not incomplete, (
        f"Phase H 9.5 : {len(incomplete)} patterns avec fields manquants : "
        f"{incomplete[:10]}"
    )
