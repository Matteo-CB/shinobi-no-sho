"""Slugification deterministe des ids canoniques.

Convention : ascii pur, snake_case, sans accents.
Exemples :
  Naruto Uzumaki                 -> uzumaki_naruto
  Katon: Goukakyuu no Jutsu      -> katon_goukakyuu_no_jutsu
"""

from __future__ import annotations

import re

from shinobi.utils.text import remove_accents

NON_ASCII_WORD = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    """Convertit une chaine en slug snake_case ascii."""
    cleaned = remove_accents(text).lower().strip()
    cleaned = cleaned.replace("'", "")
    cleaned = NON_ASCII_WORD.sub("_", cleaned)
    return cleaned.strip("_")


def slug_character(family_name: str | None, given_name: str) -> str:
    """Slug standard pour un personnage : famille_prenom."""
    if family_name:
        return f"{slugify(family_name)}_{slugify(given_name)}"
    return slugify(given_name)


def slug_technique(name_romaji: str) -> str:
    """Slug pour une technique : decoupage des mots romaji."""
    return slugify(name_romaji.replace(":", " "))
