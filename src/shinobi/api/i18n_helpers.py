"""Phase i18n.9 : helpers de localisation pour les routes API canon.

Fournit `localize_name(canon_obj)` et `localize_text(canon_obj, base)` qui,
selon `get_active_language()`, retournent la chaine appropriee parmi
`<base>_<lang>` avec fallback sur `<base>_fr` puis `<base>_romaji`.

Ces helpers s'appliquent aux modeles canon Pydantic (Character, Technique,
Clan, Village, etc.) qui exposent typiquement des champs suffixes par
code lang. Ils sont tolerants : si le champ exact n'existe pas, on
descend la chaine de fallback.
"""

from __future__ import annotations

from typing import Any

from shinobi.i18n.catalog import get_active_language
from shinobi.i18n.loader import DEFAULT_LANGUAGE


def _get(obj: Any, attr: str) -> Any:
    """Acces attribut/dict-key tolerant."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(attr)
    return getattr(obj, attr, None)


def localize_field(
    obj: Any,
    base: str,
    *,
    fallback_chain: tuple[str, ...] = ("fr", DEFAULT_LANGUAGE),
) -> str | None:
    """Retourne `obj.<base>_<active_lang>` avec fallback.

    Chaine de resolution :
      1. `<base>_<active_lang>` (ex: name_ja en mode ja)
      2. `<base>_<fallback_chain[0]>` (par defaut name_fr)
      3. `<base>_<fallback_chain[1]>` (par defaut name_<DEFAULT_LANGUAGE>=en)
      4. `<base>_romaji` (si name)
      5. `<base>` lui-meme (chaine brute, ex: village pour les villages)

    Retourne None si rien.

    Note : on essaie d'abord la langue active. Si elle n'existe pas comme
    field (ex: pas de `name_ja`), on fallback FR puis EN puis romaji.
    Important : meme en lang="fr", on tente d'abord `<base>_fr` (pas de
    fallback inutile).
    """
    active = get_active_language()
    candidates: list[str] = [f"{base}_{active}"]
    for lng in fallback_chain:
        suffix = f"{base}_{lng}"
        if suffix not in candidates:
            candidates.append(suffix)
    # romaji comme dernier recours pour les bases qui en ont
    candidates.append(f"{base}_romaji")
    # base brute (champ scalar sans suffixe lang)
    candidates.append(base)
    for cand in candidates:
        val = _get(obj, cand)
        if isinstance(val, str) and val:
            return val
    return None


def localize_name(obj: Any) -> str | None:
    """Raccourci pour `localize_field(obj, "name")`.

    Strategie : si `name_<active_lang>` existe et est non vide, l'utiliser ;
    sinon `name_fr` ; sinon `name_en` ; sinon `name_romaji`.
    Le `name_romaji` est le dernier recours car il est toujours rempli
    pour les chars / clans / techniques canon.
    """
    return localize_field(obj, "name")


def localize_description(obj: Any) -> str | None:
    """Raccourci pour `localize_field(obj, "description")`."""
    return localize_field(obj, "description")


__all__ = ["localize_description", "localize_field", "localize_name"]
