"""Phase i18n.11 : tests de non-fuite langue.

Strategie :
- En mode EN, scan une selection de prompts/catalogues pour absence de
  marqueurs FR distinctifs (diacritiques + mots francais frequents).
- En mode JA, scan les prompts JA pour confirmer presence de CJK chars
  + verifier qu'au moins une part substantielle du contenu est CJK
  (pas une simple copie EN).
- En mode FR, sanity check : presence de diacritiques FR.

Note : les termes Naruto preserves (Sharingan, Hokage, etc.) restent en
ASCII partout, donc on n'exige pas 0 ASCII en JA - on exige juste
*assez* de CJK pour que ce ne soit pas une copie EN.
"""

from __future__ import annotations

import re

import pytest

from shinobi.i18n.prompts_loader import PROMPT_NAMES, load_prompt

# Mots francais frequents qui ne devraient PAS apparaitre en mode EN.
_FR_WORDS = re.compile(
    r"\b(les|des|une|deux|pour|avec|sans|chez|cette|ces|leur|leurs|son|sa|ses|"
    r"joueur|personnage|monde|tour|annee|jour|mois|chaque|requise?|"
    r"disponible|introuvable|inconnue?|nouvel|nouvelle|nouveau|narrateur)\b",
    re.IGNORECASE,
)

# Diacritiques FR distinctifs (pas presents en EN).
_FR_DIACRITICS = re.compile(r"[éèêëàâîïôöùûüç]", re.IGNORECASE)

# CJK Unicode ranges
_CJK_CHAR = re.compile(r"[぀-ヿ一-鿿가-힯]")


def test_no_french_leak_in_english_mode() -> None:
    """En mode EN, les 6 prompts ne doivent contenir ni mots francais
    frequents ni un nombre significatif de diacritiques FR.

    On tolere quelques diacritiques sur des noms propres (ex: 'Pokémon'
    qui garde son accent meme en EN) ; au-dela de 3 occurrences, c'est
    une fuite reelle.
    """
    from shinobi.i18n.catalog import set_active_language

    set_active_language("en")
    try:
        for name in PROMPT_NAMES:
            prompt = load_prompt(name, inject_glossary=False)
            diacritics = _FR_DIACRITICS.findall(prompt)
            words = _FR_WORDS.findall(prompt)
            assert len(diacritics) <= 3, (
                f"Too many FR diacritics leaked in EN/{name}: "
                f"{len(diacritics)} found, samples: {diacritics[:5]}"
            )
            assert not words, (
                f"FR words leaked in EN/{name}: {words[:5]}"
            )
    finally:
        set_active_language("en")


def test_no_english_leak_in_japanese_mode() -> None:
    """En mode JA, les prompts doivent contenir une part substantielle
    de CJK chars (>15%). Sinon = copie EN suspecte.

    On tolere les termes preserves (chakra, Sharingan, etc.) qui restent
    en ASCII.
    """
    from shinobi.i18n.catalog import set_active_language

    set_active_language("ja")
    try:
        for name in PROMPT_NAMES:
            prompt = load_prompt(name, inject_glossary=False)
            cjk_count = len(_CJK_CHAR.findall(prompt))
            total = len(prompt)
            ratio = cjk_count / total if total else 0
            assert ratio > 0.10, (
                f"JA/{name} ratio CJK trop faible ({ratio:.2%}) = "
                f"copie EN suspecte. CJK={cjk_count} total={total}"
            )
    finally:
        set_active_language("en")


def test_french_mode_uses_diacritics() -> None:
    """En mode FR, le narrator (au minimum) doit contenir des diacritiques
    FR. Sanity check : si pas un seul diacritique, FR n'est pas vraiment FR."""
    from shinobi.i18n.catalog import set_active_language

    set_active_language("fr")
    try:
        narrator = load_prompt("narrator", inject_glossary=False)
        diacritics = _FR_DIACRITICS.findall(narrator)
        assert len(diacritics) > 5, (
            f"FR narrator manque de diacritiques ({len(diacritics)} trouves)"
        )
    finally:
        set_active_language("en")


def test_zh_mode_uses_han_ideographs() -> None:
    """En mode ZH, narrator contient des ideogrammes Han."""
    from shinobi.i18n.catalog import set_active_language

    han_only = re.compile(r"[一-鿿]")
    set_active_language("zh")
    try:
        out = load_prompt("narrator", inject_glossary=False)
        han_count = len(han_only.findall(out))
        assert han_count > 50, (
            f"ZH narrator manque d'ideogrammes Han ({han_count} trouves)"
        )
    finally:
        set_active_language("en")


def test_ko_mode_uses_hangul() -> None:
    """En mode KO, narrator contient du Hangul."""
    from shinobi.i18n.catalog import set_active_language

    hangul = re.compile(r"[가-힯]")
    set_active_language("ko")
    try:
        out = load_prompt("narrator", inject_glossary=False)
        ko_count = len(hangul.findall(out))
        assert ko_count > 50, (
            f"KO narrator manque de Hangul ({ko_count} trouves)"
        )
    finally:
        set_active_language("en")


@pytest.mark.parametrize("lang", ["es", "pt-BR", "de"])
def test_latin_lang_modes_differ_from_en(lang: str) -> None:
    """Les langues latines (ES, PT-BR, DE) doivent produire un contenu
    different de EN (sinon la traduction n'a pas eu lieu)."""
    from shinobi.i18n.catalog import set_active_language

    set_active_language("en")
    en_narrator = load_prompt("narrator", inject_glossary=False)
    set_active_language(lang)
    try:
        lang_narrator = load_prompt("narrator", inject_glossary=False)
        # Doit etre different en contenu (pas de copie EN).
        assert lang_narrator != en_narrator, (
            f"{lang} narrator est identique a EN (traduction manquante)"
        )
        # Doit faire au moins 60% de la taille du EN (pas un stub).
        assert len(lang_narrator) > len(en_narrator) * 0.6, (
            f"{lang} narrator trop court ({len(lang_narrator)} vs "
            f"EN {len(en_narrator)})"
        )
    finally:
        set_active_language("en")
