"""Glossary i18n : termes preserves en romaji dans toutes les langues.

Le glossary est lu une fois au demarrage depuis data/i18n/glossary.json.
Le module expose une liste plate `ALL_PRESERVED_TERMS` + des helpers pour :
- injecter le glossary dans les prompts LLM (footer "DO NOT TRANSLATE")
- valider qu'une traduction batch n'a pas perdu un terme protege (regex)

Les categories (techniques, ranks, etc.) servent surtout pour grouper le
contenu lors d'audits manuels ; au runtime, c'est la liste plate qui compte.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache

from shinobi.config import settings


@lru_cache(maxsize=1)
def _load_raw() -> dict[str, list[str] | str]:
    """Charge data/i18n/glossary.json une seule fois."""
    path = settings._abs_path("./data/i18n/glossary.json")
    if not path.exists():
        # Mode degrade : pas de glossary -> liste vide. Pas un blocker.
        return {"_schema": "i18n_glossary_v1"}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return {"_schema": "i18n_glossary_v1"}
    return raw


@lru_cache(maxsize=1)
def all_preserved_terms() -> tuple[str, ...]:
    """Liste plate de tous les termes preserves, dedoublonnee, triee.

    Retournee comme tuple immutable pour eviter les mutations accidentelles
    (et pour permettre lru_cache).
    """
    raw = _load_raw()
    flat: set[str] = set()
    for key, value in raw.items():
        if key.startswith("_"):
            continue
        if isinstance(value, list):
            for term in value:
                if isinstance(term, str) and term.strip():
                    flat.add(term.strip())
    return tuple(sorted(flat, key=lambda s: (-len(s), s.lower())))


@lru_cache(maxsize=1)
def categories() -> dict[str, tuple[str, ...]]:
    """Dict des categories du glossary (techniques, ranks, ...)."""
    raw = _load_raw()
    out: dict[str, tuple[str, ...]] = {}
    for key, value in raw.items():
        if key.startswith("_"):
            continue
        if isinstance(value, list):
            out[key] = tuple(value)
    return out


def is_preserved(term: str) -> bool:
    """True si `term` (case-insensitive) est dans le glossary."""
    if not term:
        return False
    needle = term.strip().lower()
    return any(t.lower() == needle for t in all_preserved_terms())


def llm_prompt_footer(target_language: str) -> str:
    """Footer a injecter dans un prompt systeme LLM pour preserver le glossary.

    Args:
        target_language: code ISO-ish (en, fr, ja, zh, ko, pt-BR, de, es).
    """
    terms = all_preserved_terms()
    if not terms:
        return ""
    listing = ", ".join(terms)
    return (
        "\n\n--- GLOSSARY (DO NOT TRANSLATE THESE TERMS, USE THEM VERBATIM "
        f"EVEN IN {target_language.upper()}) ---\n"
        f"{listing}"
    )


# Regex compose une fois pour validation rapide de batch outputs.
@lru_cache(maxsize=1)
def _preservation_regex() -> re.Pattern[str]:
    """Regex case-insensitive matchant n'importe quel terme du glossary."""
    terms = all_preserved_terms()
    if not terms:
        # Pattern qui ne matche rien
        return re.compile(r"(?!.*)")
    # Echappement + alternation, terms tries par len desc pour matcher les plus
    # longs d'abord (ex: "Mangekyou Sharingan" avant "Sharingan")
    pattern = "|".join(re.escape(t) for t in terms)
    return re.compile(rf"\b({pattern})\b", re.IGNORECASE)


def find_preserved_terms_in(text: str) -> list[str]:
    """Liste les termes du glossary trouves dans `text`. Utile pour audit
    de traduction (verifier que tous les termes du source sont presents
    dans la traduction)."""
    if not text:
        return []
    return [m.group(0) for m in _preservation_regex().finditer(text)]


def reset_cache_for_tests() -> None:
    """Force le rechargement du glossary (tests uniquement)."""
    _load_raw.cache_clear()
    all_preserved_terms.cache_clear()
    categories.cache_clear()
    _preservation_regex.cache_clear()
