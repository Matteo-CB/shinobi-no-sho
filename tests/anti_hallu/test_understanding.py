"""Tests sur la resolution referentielle et la comprehension (pilier 2.4).

Le state du pilier 4 n'etant pas encore implemente, on utilise un FakeStateView
qui satisfait le Protocol. Quand le pilier 4 sera fait, ces tests deviendront
des integration tests via le vrai tracker.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from shinobi.guards.intent_classifier import Intent
from shinobi.preprocessing.query_rewriter import rewrite_query
from shinobi.preprocessing.reference_resolver import (
    NullStateView,
    resolve_references,
)


@dataclass
class FakeStateView:
    """State minimal pour tester sans le pilier 4."""

    last_mentioned_character: str | None = None
    present_characters: Sequence[str] = field(default_factory=tuple)
    current_location: str | None = None


# Pronouns


class TestPronounResolutionWithSingleAntecedent:
    def test_pronoun_resolved_with_last_mentioned(self) -> None:
        state = FakeStateView(last_mentioned_character="Sasuke")
        result = resolve_references("je vais le voir", state)
        assert not result.is_ambiguous
        assert "Sasuke" in result.rewritten

    def test_pronoun_resolved_with_unique_present(self) -> None:
        state = FakeStateView(present_characters=("Sasuke",))
        result = resolve_references("je lui parle", state)
        assert not result.is_ambiguous
        assert "Sasuke" in result.rewritten

    def test_present_takes_priority_over_last_mentioned(self) -> None:
        state = FakeStateView(
            last_mentioned_character="Sakura",
            present_characters=("Iruka",),
        )
        result = resolve_references("je le suis", state)
        assert "Iruka" in result.rewritten
        assert "Sakura" not in result.rewritten


class TestPronounAmbiguity:
    def test_ambiguous_with_multiple_present(self) -> None:
        state = FakeStateView(present_characters=("Sasuke", "Sakura"))
        result = resolve_references("je lui parle", state)
        assert result.is_ambiguous
        assert result.clarification_needed is not None
        assert "Sasuke" in result.clarification_needed
        assert "Sakura" in result.clarification_needed

    def test_ambiguous_with_no_state(self) -> None:
        state = FakeStateView()
        result = resolve_references("je le vois", state)
        assert result.is_ambiguous
        assert result.clarification_needed is not None


class TestEllipsisExpansion:
    def test_jy_vais_with_state_resolves(self) -> None:
        state = FakeStateView(
            last_mentioned_character="Sasuke",
            current_location="konoha_hospital",
        )
        result = resolve_references("j'y vais", state)
        assert not result.is_ambiguous
        # Doit mentionner soit la destination soit la location de depart.
        assert "konoha_hospital" in result.rewritten or "Sasuke" in result.rewritten

    def test_jy_vais_without_state_asks_clarification(self) -> None:
        state = FakeStateView()
        result = resolve_references("j'y vais", state)
        assert result.is_ambiguous

    def test_ok_alone_with_state_resolves(self) -> None:
        state = FakeStateView(
            last_mentioned_character="Iruka",
            current_location="academie",
        )
        result = resolve_references("ok", state)
        assert not result.is_ambiguous

    def test_dataccord_treated_as_ellipsis(self) -> None:
        state = FakeStateView(current_location="dojo")
        result = resolve_references("d'accord", state)
        assert not result.is_ambiguous


class TestNoReferentNoOp:
    def test_action_without_pronoun_passes_through(self) -> None:
        state = FakeStateView()
        result = resolve_references("je m'entraine au taijutsu", state)
        assert not result.is_ambiguous
        assert result.rewritten == "je m'entraine au taijutsu"

    def test_question_without_pronoun_passes_through(self) -> None:
        state = FakeStateView()
        result = resolve_references("qui est l'Hokage", state)
        assert not result.is_ambiguous
        assert result.rewritten == "qui est l'Hokage"

    def test_empty_input_asks_clarification(self) -> None:
        state = FakeStateView()
        result = resolve_references("", state)
        assert result.is_ambiguous
        assert result.clarification_needed is not None


class TestNullStateView:
    def test_null_state_view_returns_none_everywhere(self) -> None:
        s = NullStateView()
        assert s.last_mentioned_character is None
        assert tuple(s.present_characters) == ()
        assert s.current_location is None


# Pipeline complet


class TestFullPipelineEnrichedQuery:
    def test_simple_action_classified_correctly(self) -> None:
        eq = rewrite_query("je m'entraine au lancer de shuriken")
        assert eq.intent == Intent.in_universe_action
        assert not eq.is_ambiguous

    def test_pronoun_with_state_in_pipeline(self) -> None:
        state = FakeStateView(last_mentioned_character="Iruka")
        eq = rewrite_query("je vais le voir", state)
        assert "Iruka" in eq.rewritten

    def test_ambiguous_short_input_flagged(self) -> None:
        eq = rewrite_query("ok")
        assert eq.intent == Intent.ambiguous

    def test_ambiguous_input_with_present_characters_asks_clarif(self) -> None:
        state = FakeStateView(present_characters=("Sasuke", "Sakura"))
        eq = rewrite_query("je lui parle", state)
        assert eq.is_ambiguous
        assert eq.clarification_needed is not None
