"""Calcul d'age deterministe a partir du canon (pilier 4.2).

Pas de champ `age` stocke (drift garanti via les updates async).
Toujours derive de `year - birth_year` via cette fonction.

Decouple du `CanonBundle` Pydantic via deux Protocols minimalistes :
- `CanonCharacterLike` : id, birth_year, death_year
- `CanonView` : characters: Mapping[str, CanonCharacterLike]

Le `CanonBundle` reel satisfait ces Protocols par duck typing, et les tests
peuvent fournir un fake leger sans avoir a construire un `Character` Pydantic
complet (qui demande ~30 champs requis).
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Protocol, runtime_checkable

from shinobi.errors import (
    CharacterDeadError,
    CharacterNotFoundError,
    CharacterNotYetBornError,
)


class CanonStatus(StrEnum):
    """Statut canon d'un personnage a une annee donnee."""

    alive = "alive"
    not_yet_born = "not_yet_born"
    dead = "dead"
    unknown = "unknown"


@runtime_checkable
class CanonCharacterLike(Protocol):
    """Minimum requis sur un Character canon pour calculer un age."""

    id: str
    birth_year: int | None
    death_year: int | None


class CanonView(Protocol):
    """Minimum requis sur un CanonBundle pour exposer les ages."""

    @property
    def characters(self) -> Mapping[str, CanonCharacterLike]: ...


def get_age_from_birth_year(birth_year: int, year: int, *, strict: bool = True) -> int:
    """Calcule un age a partir d'un birth_year et d'une annee in-game. Pure.

    Args:
        birth_year : annee de naissance (an 0 = naissance Naruto, conventions
            du projet).
        year : annee in-game courante.
        strict : si True, raise `CharacterNotYetBornError` quand age < 0.
            Si False, clamp a 0.

    Returns:
        L'age en annees (entier >= 0).
    """
    age = year - birth_year
    if strict and age < 0:
        raise CharacterNotYetBornError(
            f"Pas encore ne en l'an {year} (birth {birth_year})."
        )
    return max(0, age)


def get_age(
    name_or_id: str,
    year: int,
    canon: CanonView,
    *,
    strict: bool = True,
) -> int:
    """Resout `name_or_id` en perso canon et retourne son age en `year`.

    Args:
        name_or_id : id slug (ex: 'uzumaki_naruto') ou nom commun (ex: 'naruto').
            La resolution alias passe par `shinobi.canon.fact_sheet.PRIMARY_NPC_NAMES`
            si disponible.
        year : annee in-game (an 0 = naissance Naruto).
        canon : bundle canonique satisfaisant le Protocol `CanonView`.
        strict : si True, raise `CharacterNotYetBornError` ou `CharacterDeadError`
            quand le perso n'est pas vivant a `year`. Si False, clamp a 0 et ignore
            la mort.

    Raises:
        CharacterNotFoundError : nom ou id inconnu, ou birth_year manquant.
        CharacterNotYetBornError : pas encore ne a `year` (en mode strict).
        CharacterDeadError : deja mort a `year` (en mode strict).
    """
    char = _resolve_character(name_or_id, canon)
    if char.birth_year is None:
        raise CharacterNotFoundError(
            f"birth_year manquant pour '{name_or_id}' (id: {char.id})."
        )
    age = year - char.birth_year
    if strict:
        if age < 0:
            raise CharacterNotYetBornError(
                f"{char.id} (birth {char.birth_year}) pas encore ne en l'an {year}."
            )
        if char.death_year is not None and year >= char.death_year:
            raise CharacterDeadError(
                f"{char.id} est mort en l'an {char.death_year}, on est en l'an {year}."
            )
    return max(0, age)


def is_alive(name_or_id: str, year: int, canon: CanonView) -> bool:
    """Vrai si le perso est ne et pas encore mort a `year`.

    Renvoie False sur perso inconnu (pas d'exception).
    Pour distinguer dead / not_yet_born / unknown / alive, voir `get_canon_status`.
    """
    return get_canon_status(name_or_id, year, canon) == CanonStatus.alive


def get_canon_status(name_or_id: str, year: int, canon: CanonView) -> CanonStatus:
    """Determine le statut canon d'un perso a `year`. Ne raise jamais.

    Permet aux validators de distinguer un PNJ canon mort (a rejeter) d'un
    PNJ inconnu du canon (a laisser passer comme generique).
    """
    try:
        char = _resolve_character(name_or_id, canon)
    except CharacterNotFoundError:
        return CanonStatus.unknown
    if char.birth_year is None:
        return CanonStatus.unknown
    if year < char.birth_year:
        return CanonStatus.not_yet_born
    if char.death_year is not None and year >= char.death_year:
        return CanonStatus.dead
    return CanonStatus.alive


def _resolve_character(name_or_id: str, canon: CanonView) -> CanonCharacterLike:
    """Resout un nom commun ou un id en `CanonCharacterLike`.

    Strategie :
    1. id exact dans canon.characters
    2. alias dans `PRIMARY_NPC_NAMES` (import lazy pour rester decouple en tests)
    3. raise `CharacterNotFoundError`
    """
    key = (name_or_id or "").lower().strip()
    if not key:
        raise CharacterNotFoundError("Nom de personnage vide.")
    if key in canon.characters:
        return canon.characters[key]
    try:
        from shinobi.canon.fact_sheet import PRIMARY_NPC_NAMES

        cid = PRIMARY_NPC_NAMES.get(key)
        if cid and cid in canon.characters:
            return canon.characters[cid]
    except ImportError:
        pass
    raise CharacterNotFoundError(f"Personnage '{name_or_id}' inconnu du canon.")
