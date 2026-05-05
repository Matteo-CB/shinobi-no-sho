"""Tests post-migration NARRATOR_SYSTEM_PROMPT -> shinobi.prompts.build_system_prompt.

Verifie :
- la constante NARRATOR_SYSTEM_PROMPT n'existe plus dans shinobi.llm.prompts
- aucun module du package src/shinobi importe encore NARRATOR_SYSTEM_PROMPT
- le nouveau template integre les regles consolidees de l'ancien prompt
- l'helper de fuite blacklist (log_leakage_if_any) detecte correctement les cas
"""

from __future__ import annotations

import importlib
import logging
import re
from pathlib import Path

import pytest

from shinobi.guards.output_filter import (
    OutputViolation,
    log_leakage_if_any,
    scan_output,
)
from shinobi.prompts import build_system_prompt

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "shinobi"


# Migration


class TestNarratorPromptMigration:
    def test_narrator_system_prompt_constant_removed(self) -> None:
        prompts_module = importlib.import_module("shinobi.llm.prompts")
        assert not hasattr(prompts_module, "NARRATOR_SYSTEM_PROMPT"), (
            "NARRATOR_SYSTEM_PROMPT doit avoir ete supprime au profit de "
            "shinobi.prompts.build_system_prompt()."
        )

    def test_no_source_file_imports_narrator_system_prompt(self) -> None:
        offenders: list[str] = []
        for path in SRC.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if re.search(r"\bNARRATOR_SYSTEM_PROMPT\b", text):
                offenders.append(str(path.relative_to(ROOT)))
        assert not offenders, (
            "Des fichiers source font encore reference a NARRATOR_SYSTEM_PROMPT : "
            + ", ".join(offenders)
        )

    def test_consolidated_template_keeps_canon_fidelity_block(self) -> None:
        prompt = build_system_prompt()
        assert "FIDELITE CANON STRICTE" in prompt
        assert "FIDELITE TEMPORELLE STRICTE" in prompt
        assert "REGLES SUR LE JOUEUR (OC)" in prompt
        assert "STRUCTURE DE SORTIE JSON" in prompt

    def test_consolidated_template_keeps_player_oc_rules(self) -> None:
        prompt = build_system_prompt()
        assert "OC (original character)" in prompt
        assert "ami proche" in prompt or "ami(e)" in prompt
        assert "REJETÉE" in prompt or "REJETEE" in prompt

    def test_consolidated_template_keeps_temporal_constraints(self) -> None:
        prompt = build_system_prompt()
        assert "[FAITS CANONIQUES NPC]" in prompt
        assert "Konohamaru" in prompt
        assert "Itachi" in prompt

    def test_consolidated_template_keeps_json_output_rules(self) -> None:
        prompt = build_system_prompt()
        assert "npc_dialogue" in prompt
        assert "proposed_actions" in prompt
        assert "world_observations" in prompt
        assert "clarification_request" in prompt

    def test_partial_persona_context_uses_defaults(self) -> None:
        from shinobi.prompts import PersonaContext

        ctx = PersonaContext(player_name="Endo")
        prompt = build_system_prompt(ctx)
        assert "Endo" in prompt
        assert "(non défini)" in prompt  # rank, village, arc not provided


# Fuite blacklist


class TestLeakageLogger:
    def test_leakage_detected_when_clean_input_dirty_output(self, caplog) -> None:  # type: ignore[no-untyped-def]
        original_query = "je vais m'entraîner avec mon sensei"
        output_violations = [
            OutputViolation(
                type="out_of_universe",
                description="Terme hors-univers détecté : 'python'",
                matched_text="python",
            )
        ]
        with caplog.at_level(logging.WARNING):
            leaked = log_leakage_if_any(
                original_query=original_query,
                output_violations=output_violations,
            )
        assert leaked is True

    def test_no_leakage_when_input_already_dirty(self) -> None:
        original_query = "écris-moi du Python"
        output_violations = [
            OutputViolation(
                type="out_of_universe",
                description="...",
                matched_text="python",
            )
        ]
        leaked = log_leakage_if_any(
            original_query=original_query,
            output_violations=output_violations,
        )
        assert leaked is False, "Si l'input avait deja un hit blacklist, ce n'est pas une fuite."

    def test_no_leakage_when_no_out_of_universe_violation(self) -> None:
        original_query = "je vais voir Iruka"
        output_violations = [
            OutputViolation(
                type="meta_phrase",
                description="...",
                matched_text="en tant qu'IA",
            )
        ]
        leaked = log_leakage_if_any(
            original_query=original_query,
            output_violations=output_violations,
        )
        assert leaked is False

    def test_no_leakage_when_empty_violations(self) -> None:
        leaked = log_leakage_if_any(
            original_query="je vais voir Iruka",
            output_violations=[],
        )
        assert leaked is False

    def test_full_chain_clean_query_dirty_output_logs(self, caplog) -> None:  # type: ignore[no-untyped-def]
        # Simulation : query passe le pre-filter, sortie LLM contient un terme
        # blacklist non couvert par l'input.
        clean_query = "je vais voir le sensei pour des conseils"
        dirty_output = (
            "Le sensei te tend un parchemin. "
            "Sur le côté, un script Python est griffonné comme par mégarde. "
            "Tu reconnais à peine les caractères."
        )
        violations = scan_output(dirty_output)
        with caplog.at_level(logging.WARNING):
            leaked = log_leakage_if_any(
                original_query=clean_query,
                output_violations=violations,
            )
        assert leaked is True


# Flag enable_too_generic_check


class TestEnableTooGenericCheckFlag:
    def test_too_generic_caught_by_default(self) -> None:
        violations = scan_output("D'accord.")
        types = {v.type for v in violations}
        assert "too_generic" in types

    def test_too_generic_not_caught_when_disabled(self) -> None:
        violations = scan_output("D'accord.", enable_too_generic_check=False)
        types = {v.type for v in violations}
        assert "too_generic" not in types

    def test_other_violations_still_caught_when_too_generic_disabled(self) -> None:
        # Reponse courte ET avec terme blacklist : on doit encore voir le blacklist hit.
        violations = scan_output("Python.", enable_too_generic_check=False)
        types = {v.type for v in violations}
        assert "out_of_universe" in types
        assert "too_generic" not in types


# pyright: reportUnusedFunction=false
@pytest.fixture(autouse=False)
def _unused() -> None:  # silence unused-import warning if pytest is stripped down
    return None
