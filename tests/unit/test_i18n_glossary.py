"""Phase i18n.11 : tests de preservation du glossary.

Pour chacune des 8 langues, on verifie que les ~50 termes preserves
(chakra, Sharingan, Konohagakure, Hokage, etc.) :
1. Sont presents dans le footer LLM via `llm_prompt_footer(lang)`.
2. Sont effectivement enumeres au moins une fois dans le prompt
   complet du narrator.
"""

from __future__ import annotations

from shinobi.i18n.glossary import all_preserved_terms, llm_prompt_footer
from shinobi.i18n.prompts_loader import load_prompt

# Termes critiques toujours preserves quelle que soit la langue.
CRITICAL_TERMS = (
    "chakra",
    "shinobi",
    "hokage",
    "ninjutsu",
    "Sharingan",
    "Konohagakure",
    "Akatsuki",
    "Rasengan",
    "kekkei genkai",
    "Byakugan",
)


def test_glossary_preserved_in_all_langs(lang: str) -> None:
    """Pour chaque langue, le footer contient tous les termes critiques
    (case-insensitive : le glossary.json a des cases mixtes)."""
    footer = llm_prompt_footer(lang)
    footer_lower = footer.lower()
    for term in CRITICAL_TERMS:
        assert term.lower() in footer_lower, (
            f"term {term!r} missing from footer for lang={lang}"
        )


def test_glossary_terms_count_uniform_across_langs(lang: str) -> None:
    """Le glossary est global (pas par langue), donc le footer doit lister
    le meme nombre de termes pour toutes les langues."""
    footer = llm_prompt_footer(lang)
    # Comptage des virgules + 1 = nombre de termes dans le footer.
    listing_part = footer.split("---\n", 1)[-1].strip()
    term_count = len([s for s in listing_part.split(",") if s.strip()])
    expected = len(all_preserved_terms())
    assert term_count == expected, (
        f"footer for {lang} has {term_count} terms, expected {expected}"
    )


def test_glossary_footer_marker_language_aware(lang: str) -> None:
    """Le footer mentionne la langue cible en upper-case."""
    footer = llm_prompt_footer(lang)
    assert lang.upper() in footer, (
        f"footer for {lang} should mention {lang.upper()}"
    )


def test_glossary_preserved_in_full_narrator_prompt(lang: str) -> None:
    """Quand on charge le narrator complet (avec injection auto du footer),
    les termes critiques apparaissent."""
    prompt = load_prompt("narrator")
    prompt_lower = prompt.lower()
    for term in CRITICAL_TERMS:
        assert term.lower() in prompt_lower, (
            f"term {term!r} missing from full narrator prompt in {lang}"
        )
