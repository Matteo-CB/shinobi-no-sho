"""Helpers de localisation pour le canon (multi-langue futur).

Strategie : chaque entite a un champ `aliases_by_lang: dict[str, list[str]]`
qui mappe code de langue (ISO 639-1) -> liste de noms acceptables. Permet :
- au matching d'accepter n'importe quel nom dans n'importe quelle langue
- au narrator de choisir le nom prefere selon settings.locale
- aux traductions futures de coexister sans casser le matching

Exemples de codes : 'en', 'fr', 'ja' (kanji), 'ja-romaji' (romanise).
"""

from __future__ import annotations

# Code de langue par defaut pour la sortie utilisateur. Lu depuis settings.locale
# si on l'expose plus tard. Pour l'instant : 'fr' fixe.
DEFAULT_DISPLAY_LANG = "fr"


def all_alias_lists(entity: object) -> dict[str, list[str]]:
    """Retourne tous les alias d'une entite, agreges par langue.

    Strategie de fallback :
    - Si entity a `aliases_by_lang` (dict[lang, list]), on l'utilise.
    - Sinon, on collecte heuristiquement depuis les champs name_<lang>:
      name_fr -> 'fr', name_romaji -> 'ja-romaji', name_kanji -> 'ja',
      name_en -> 'en', name -> 'en' (defaut).
    - aliases : ajoute a la langue par defaut.
    """
    out: dict[str, list[str]] = {}
    aliases_by_lang = getattr(entity, "aliases_by_lang", None)
    if isinstance(aliases_by_lang, dict):
        for lang, names in aliases_by_lang.items():
            if isinstance(names, list):
                out.setdefault(lang, []).extend(str(n) for n in names if n)
    # Fallback heuristique sur les champs name_*
    name_fr = getattr(entity, "name_fr", None)
    if name_fr:
        out.setdefault("fr", []).append(str(name_fr))
    name_romaji = getattr(entity, "name_romaji", None)
    if name_romaji:
        out.setdefault("ja-romaji", []).append(str(name_romaji))
    name_kanji = getattr(entity, "name_kanji", None)
    if name_kanji:
        out.setdefault("ja", []).append(str(name_kanji))
    name_en = getattr(entity, "name_en", None)
    if name_en:
        out.setdefault("en", []).append(str(name_en))
    aliases = getattr(entity, "aliases", None)
    if isinstance(aliases, list):
        out.setdefault(DEFAULT_DISPLAY_LANG, []).extend(str(a) for a in aliases if a)
    return {k: list(dict.fromkeys(v)) for k, v in out.items() if v}


def all_alias_strings(entity: object) -> list[str]:
    """Retourne tous les alias d'une entite, toutes langues confondues, dedupes."""
    seen: set[str] = set()
    out: list[str] = []
    for names in all_alias_lists(entity).values():
        for n in names:
            if n not in seen:
                seen.add(n)
                out.append(n)
    return out


def display_name(entity: object, lang: str | None = None) -> str:
    """Retourne le nom prefere de l'entite dans la langue donnee (ou fallback)."""
    target = lang or DEFAULT_DISPLAY_LANG
    by_lang = all_alias_lists(entity)
    if by_lang.get(target):
        return by_lang[target][0]
    # Fallback ordre : DEFAULT > en > ja-romaji > premier dispo
    for fb in (DEFAULT_DISPLAY_LANG, "en", "ja-romaji", "ja"):
        if by_lang.get(fb):
            return by_lang[fb][0]
    if by_lang:
        return next(iter(by_lang.values()))[0]
    return getattr(entity, "id", "?")
