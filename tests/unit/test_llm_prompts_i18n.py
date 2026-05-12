"""Phase i18n.10 : tests des prompts LLM localises.

Couvre :
1. Tous les 48 fichiers prompts existent (8 langs x 6 prompts).
2. Loader : lit selon `get_active_language()`.
3. Loader : fallback EN si lang manquante (n'arrive pas en pratique car
   tous les fichiers existent, mais on teste le code path via une lang
   non supportee).
4. Loader : injection glossary footer par defaut.
5. Loader : `inject_glossary=False` court-circuite l'injection.
6. Loader : refus de noms inconnus (ValueError).
7. 6 prompts x 2 langs sample : prompt non vide + contient au moins un
   indicateur de la langue (heuristique simple : caractere ASCII pour EN,
   caractere CJK pour JA).
8. Roundtrip : `set_active_language('ja')` + load -> contenu JA.
9. Modules LLM : `goals.pathfinder` utilise load_prompt (smoke check
   import + call-site existe).
"""

from __future__ import annotations

import pytest

from shinobi.i18n.catalog import set_active_language
from shinobi.i18n.loader import SUPPORTED_LANGUAGES
from shinobi.i18n.prompts_loader import (
    PROMPT_NAMES,
    load_prompt,
    reset_cache_for_tests,
)


@pytest.fixture(autouse=True)
def reset_active_lang() -> None:
    """Remet la langue active a EN apres chaque test."""
    yield
    set_active_language("en")
    reset_cache_for_tests()


# === 1. 48 fichiers prompts existent =================================


def test_all_48_prompt_files_exist() -> None:
    """8 langs x 6 prompts = 48 fichiers, tous non vides."""
    from pathlib import Path

    from shinobi.config import settings

    base = settings._abs_path("./data/i18n/prompts")
    missing: list[str] = []
    for lang in SUPPORTED_LANGUAGES:
        for name in PROMPT_NAMES:
            p = Path(base) / lang / f"{name}.txt"
            if not p.exists():
                missing.append(str(p))
                continue
            if p.stat().st_size < 50:
                missing.append(f"{p} (size={p.stat().st_size})")
    assert not missing, f"Fichiers manquants ou trop petits : {missing[:5]}"


# === 2. Loader lit selon get_active_language ==========================


def test_loader_reads_active_language() -> None:
    set_active_language("en")
    en = load_prompt("narrator", inject_glossary=False)
    set_active_language("ja")
    ja = load_prompt("narrator", inject_glossary=False)
    # Doivent etre differents en contenu.
    assert en != ja
    assert "PERSONA FRAMEWORK" in en  # EN
    # JA contient au moins un char CJK.
    assert any("぀" <= ch <= "鿿" for ch in ja), \
        "JA narrator should contain CJK characters"


# === 3. Fallback EN si lang inconnue =================================


def test_loader_falls_back_to_en_for_unsupported_lang() -> None:
    # On force via le parametre `lang` (la voie publique pour les tests).
    out = load_prompt("narrator", lang="klingon", inject_glossary=False)
    assert "PERSONA FRAMEWORK" in out  # contenu EN


# === 4. Glossary footer injecte par defaut ============================


def test_loader_injects_glossary_footer_by_default() -> None:
    out = load_prompt("narrator", lang="en")
    assert "GLOSSARY" in out
    assert "DO NOT TRANSLATE" in out


# === 5. inject_glossary=False ========================================


def test_loader_skips_glossary_when_disabled() -> None:
    out = load_prompt("narrator", lang="en", inject_glossary=False)
    assert "GLOSSARY" not in out
    assert "DO NOT TRANSLATE" not in out


# === 6. Noms inconnus ================================================


def test_loader_rejects_unknown_prompt_name() -> None:
    with pytest.raises(ValueError, match="Unknown prompt name"):
        load_prompt("not_a_prompt")


# === 7. 6 prompts x 2 langs sample (12 cas) ==========================


@pytest.mark.parametrize("prompt_name", list(PROMPT_NAMES))
@pytest.mark.parametrize("lang", ["en", "ja"])
def test_each_prompt_loads_in_two_langs(prompt_name: str, lang: str) -> None:
    """Chaque prompt charge sans erreur dans EN + JA et est non vide."""
    out = load_prompt(prompt_name, lang=lang, inject_glossary=False)
    assert len(out) > 100, f"{prompt_name}/{lang} too short: {len(out)}"
    if lang == "en":
        # Doit contenir des caracteres ASCII anglais standard.
        assert any(ch.isascii() and ch.isalpha() for ch in out)
    elif lang == "ja":
        # Doit contenir au moins un CJK char (Hiragana/Katakana/Han)
        # ou rester partiellement en romaji pour les termes preserves.
        has_cjk = any("぀" <= ch <= "鿿" for ch in out)
        assert has_cjk, f"{prompt_name}/ja missing CJK chars"


# === 8. Glossary contient les termes preserves ========================


def test_glossary_footer_contains_preserved_terms() -> None:
    out = load_prompt("narrator", lang="ja")
    # Quelques termes Naruto critiques doivent y figurer (case-insensitive,
    # car le glossary.json utilise des cases mixtes : 'chakra'/'hokage' en
    # lower, 'Sharingan'/'Konohagakure' en CamelCase).
    out_lower = out.lower()
    for term in ("chakra", "hokage", "sharingan", "rasengan"):
        assert term in out_lower, f"glossary missing term: {term}"


# === 9. Smoke test : modules LLM importent et appellent load_prompt ===


def test_llm_modules_wire_through_loader() -> None:
    """Les 6 modules cibles importent et utilisent `load_prompt`.

    On ne lance pas le LLM (lourd), juste qu'aucun ImportError n'apparait
    et que le source mentionne `load_prompt`.
    """
    import inspect

    from shinobi.director import compactor
    from shinobi.goals import pathfinder
    from shinobi.llm import narration
    from shinobi.prompts import build_system_prompt
    from shinobi.tension import llm_analyst
    from shinobi.world_resolver import generator

    # Verifie que chaque module reference `load_prompt` dans son source.
    for mod in (pathfinder, narration, generator, llm_analyst, compactor):
        src = inspect.getsource(mod)
        assert "load_prompt(" in src, (
            f"{mod.__name__} should call load_prompt(...)"
        )

    # narrator passe par build_system_prompt qui appelle load_prompt
    out = build_system_prompt()
    assert isinstance(out, str)
    assert len(out) > 500
