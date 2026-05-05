"""Tests adversariaux du risk-tagger (pilier 7.1).

Couvre les 4 niveaux de risk :
- low        : prose generique sans entite canon
- medium     : dialogue ou prose avec 1 entite canon
- high       : prose avec >= 2 entites canon, dialogue idem
- very_high  : action avec actor + jutsu (triplet check candidate)

Egalement teste : `required_layers_for_risk`, `max_risk_in`,
deduplication des entites detectees.
"""

from __future__ import annotations

import pytest

from shinobi.validation import (
    NarrativeAction,
    NarrativeDialogue,
    NarrativeOutput,
    RiskLevel,
    SegmentType,
    max_risk_in,
    required_layers_for_risk,
    tag_narrative_output,
)


def test_pure_prose_no_canon_is_low_risk() -> None:
    out = NarrativeOutput(
        narrative="Le vent fait bruisser les feuilles. Le soleil decline lentement.",
    )
    segs = tag_narrative_output(out)
    assert len(segs) == 2
    assert all(s.risk_level == RiskLevel.low for s in segs)
    assert all(s.type == SegmentType.prose_descriptive for s in segs)


def test_prose_one_canon_entity_is_medium() -> None:
    out = NarrativeOutput(
        narrative="Le ninja regarde le drapeau de konohagakure flotter.",
    )
    segs = tag_narrative_output(out)
    assert len(segs) == 1
    seg = segs[0]
    assert seg.risk_level == RiskLevel.medium
    assert seg.type == SegmentType.factual_claim
    assert "konohagakure" in seg.matched_entities


def test_prose_two_canon_entities_is_high() -> None:
    out = NarrativeOutput(
        narrative="uzumaki_naruto a appris le rasengan de jiraiya pendant le timeskip.",
    )
    segs = tag_narrative_output(out)
    assert len(segs) == 1
    seg = segs[0]
    assert seg.risk_level == RiskLevel.high
    assert seg.type == SegmentType.factual_claim
    assert len(seg.matched_entities) >= 2


def test_dialogue_no_canon_is_low() -> None:
    out = NarrativeOutput(
        npc_dialogue=[NarrativeDialogue(
            character_id="random_merchant",
            line="Bonjour voyageur, que puis-je pour toi ?",
        )],
    )
    segs = tag_narrative_output(out)
    assert len(segs) == 1
    assert segs[0].type == SegmentType.dialogue
    assert segs[0].risk_level == RiskLevel.low


def test_dialogue_with_canon_entity_is_medium() -> None:
    out = NarrativeOutput(
        npc_dialogue=[NarrativeDialogue(
            character_id="hatake_kakashi",
            line="Tu n'es pas pret a affronter uchiha_itachi.",
        )],
    )
    segs = tag_narrative_output(out)
    assert segs[0].risk_level == RiskLevel.medium
    assert "uchiha_itachi" in segs[0].matched_entities


def test_action_actor_plus_jutsu_is_very_high() -> None:
    out = NarrativeOutput(
        actions=[NarrativeAction(
            actor="uzumaki_naruto",
            type="cast",
            jutsu="rasengan",
            target="uchiha_sasuke",
        )],
    )
    segs = tag_narrative_output(out)
    assert len(segs) == 1
    seg = segs[0]
    assert seg.type == SegmentType.action
    assert seg.risk_level == RiskLevel.very_high
    assert seg.actor == "uzumaki_naruto"
    assert seg.jutsu == "rasengan"


def test_action_with_only_actor_is_high() -> None:
    out = NarrativeOutput(
        actions=[NarrativeAction(
            actor="uzumaki_naruto",
            type="walk",
            location="konohagakure",
        )],
    )
    segs = tag_narrative_output(out)
    assert segs[0].risk_level == RiskLevel.high


def test_required_layers_progression() -> None:
    assert required_layers_for_risk(RiskLevel.low) == ("sherlock_rules",)
    medium = required_layers_for_risk(RiskLevel.medium)
    assert "sherlock_rules" in medium and "age_coherence" in medium
    high = required_layers_for_risk(RiskLevel.high)
    assert "triplet_check" in high
    very_high = required_layers_for_risk(RiskLevel.very_high)
    assert "nli" in very_high and "llm_judge" in very_high


def test_max_risk_in_picks_highest() -> None:
    out = NarrativeOutput(
        narrative="Le vent souffle.",
        actions=[NarrativeAction(
            actor="uzumaki_naruto",
            jutsu="rasengan",
            type="cast",
        )],
    )
    segs = tag_narrative_output(out)
    assert max_risk_in(segs) == RiskLevel.very_high


def test_max_risk_empty_is_low() -> None:
    assert max_risk_in([]) == RiskLevel.low


def test_canon_entity_match_does_not_overlap_substring() -> None:
    """'naruto' ne doit pas matcher dans 'narutopedia'."""
    out = NarrativeOutput(narrative="Une narutopedia est ouverte sur la table.")
    segs = tag_narrative_output(out)
    # uzumaki_naruto ne doit PAS etre detecte. Si pourtant 'naruto' est dans
    # la liste seul, il ne devrait pas matcher 'narutopedia' grace au regex
    # avec word boundaries.
    assert all("naruto" != e for e in segs[0].matched_entities)


def test_entities_deduplicated() -> None:
    out = NarrativeOutput(
        narrative="uzumaki_naruto et uzumaki_naruto se rencontrent.",
    )
    segs = tag_narrative_output(out)
    if segs[0].matched_entities:
        assert segs[0].matched_entities.count("uzumaki_naruto") == 1


def test_full_narrative_tags_each_segment() -> None:
    out = NarrativeOutput(
        narrative="Le vent souffle. uzumaki_naruto sourit.",
        npc_dialogue=[NarrativeDialogue(
            character_id="hatake_kakashi",
            line="Bien joue.",
        )],
        actions=[NarrativeAction(
            actor="uzumaki_naruto",
            jutsu="rasengan",
            type="cast",
        )],
    )
    segs = tag_narrative_output(out)
    types = {s.type for s in segs}
    assert SegmentType.action in types
    assert SegmentType.dialogue in types
    assert SegmentType.prose_descriptive in types or SegmentType.factual_claim in types
