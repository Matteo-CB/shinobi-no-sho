"""Phase i18n.11 : tests cross-langue LLM prompts.

Tous les 6 prompts systeme LLM sont chargeables et non vides dans
chacune des 8 langues. Glossary footer auto-injecte. Glossary footer
contient le terme `chakra` dans chaque langue (preserve verbatim).
"""

from __future__ import annotations

import pytest

from shinobi.i18n.prompts_loader import PROMPT_NAMES, load_prompt


@pytest.mark.parametrize("prompt_name", list(PROMPT_NAMES))
def test_llm_prompt_loads_in_each_lang(lang: str, prompt_name: str) -> None:
    """48 cas : 6 prompts x 8 langs. Tous chargent + non vides."""
    out = load_prompt(prompt_name, inject_glossary=False)
    assert len(out) > 100, (
        f"prompt {prompt_name} in {lang} too short ({len(out)} chars)"
    )


def test_llm_prompt_glossary_injected_per_lang(lang: str) -> None:
    """Pour chacune des 8 langues, le footer GLOSSARY est injecte au
    moins sur le narrator (test prend narrator comme echantillon)."""
    out = load_prompt("narrator")
    assert "GLOSSARY" in out, f"glossary footer missing in {lang}"
    assert "DO NOT TRANSLATE" in out


def test_llm_narrator_per_lang_contains_chakra_term(lang: str) -> None:
    """Le terme `chakra` (preserve, glossary) doit figurer dans le footer
    de chacune des 8 langues, en romaji."""
    out = load_prompt("narrator")
    assert "chakra" in out.lower(), f"chakra not in narrator footer for {lang}"
