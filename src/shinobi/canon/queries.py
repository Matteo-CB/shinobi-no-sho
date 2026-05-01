"""Requetes structurees sur le bundle canonique."""

from __future__ import annotations

from collections.abc import Iterable

from shinobi.canon.models import (
    CanonBundle,
    Character,
    Clan,
    Technique,
    TimelineEvent,
    Village,
)
from shinobi.types import TechniqueCategory, TechniqueRank


def list_villages(canon: CanonBundle) -> list[Village]:
    """Tous les villages canoniques."""
    return list(canon.villages.values())


def get_village(canon: CanonBundle, village_id: str) -> Village | None:
    """Village par id (None si absent)."""
    return canon.villages.get(village_id)


def list_active_clans_in_village_at(
    canon: CanonBundle,
    village_id: str,
    year: int,
) -> list[Clan]:
    """Clans actifs dans un village donne a une date donnee."""
    village = canon.villages.get(village_id)
    if village is None:
        return []
    out: list[Clan] = []
    for clan_id in village.main_clans:
        clan = canon.clans.get(clan_id)
        if clan is None:
            continue
        if _is_clan_active(clan, year):
            out.append(clan)
    return out


def _is_clan_active(clan: Clan, year: int) -> bool:
    if not clan.status_by_era:
        return True
    for entry in clan.status_by_era:
        if entry.from_year <= year and (entry.to_year is None or year < entry.to_year):
            return entry.status not in {"extinct"}
    return True


def find_living_characters_at(
    canon: CanonBundle,
    *,
    year: int,
    village: str | None = None,
    clan: str | None = None,
) -> list[Character]:
    """Personnages canoniques vivants a une date, eventuellement filtres."""
    out: list[Character] = []
    for char in canon.characters.values():
        if not _is_alive_at(char, year):
            continue
        if village and _village_at(char, year) != village:
            continue
        if clan and char.clan != clan:
            continue
        out.append(char)
    return out


def _is_alive_at(char: Character, year: int) -> bool:
    if char.birth_year is not None and year < char.birth_year:
        return False
    if char.death_year is not None and year >= char.death_year:
        return False
    return True


def _village_at(char: Character, year: int) -> str:
    for entry in char.current_village_by_era:
        if entry.from_year <= year and (entry.to_year is None or year < entry.to_year):
            return entry.village
    return char.village_of_origin


def find_techniques(
    canon: CanonBundle,
    *,
    category: TechniqueCategory | None = None,
    natures: Iterable[str] | None = None,
    max_rank: TechniqueRank | None = None,
    user_id: str | None = None,
) -> list[Technique]:
    """Recherche structuree de techniques."""
    rank_order = {r.value: i for i, r in enumerate(TechniqueRank)}
    natures_set = set(natures or [])
    out: list[Technique] = []
    for tech in canon.techniques.values():
        if category and tech.category != category:
            continue
        if natures_set and not natures_set.issubset(set(tech.natures)):
            continue
        if max_rank and rank_order.get(tech.rank, 99) > rank_order.get(max_rank, 99):
            continue
        if user_id and user_id not in tech.canonical_users:
            continue
        out.append(tech)
    return out


def techniques_teachable_by(canon: CanonBundle, character_id: str) -> list[Technique]:
    """Techniques que ce personnage peut enseigner."""
    char = canon.characters.get(character_id)
    if char is None:
        return []
    return [canon.techniques[tid] for tid in char.teachable_techniques if tid in canon.techniques]


def upcoming_events_in(
    canon: CanonBundle,
    *,
    from_year: int,
    horizon_years: int = 100,
) -> list[TimelineEvent]:
    """Evenements canon planifies dans un horizon donne."""
    return sorted(
        (
            ev
            for ev in canon.timeline_events.values()
            if from_year <= ev.year < from_year + horizon_years
        ),
        key=lambda e: (e.year, e.date or ""),
    )


def kage_at(canon: CanonBundle, village_id: str, year: int) -> str | None:
    """Identifiant du Kage en place a cette annee dans ce village."""
    village = canon.villages.get(village_id)
    if village is None:
        return None
    for entry in sorted(village.kage_lineage, key=lambda k: k.from_year):
        if entry.from_year <= year and (entry.to_year is None or year < entry.to_year):
            return entry.character_id
    return None


def voice_profile_for(
    canon: CanonBundle,
    character_id: str,
):
    """Voice profile du personnage si disponible."""
    char = canon.characters.get(character_id)
    if char is None or char.voice_profile_id is None:
        return None
    return canon.voice_profiles.get(char.voice_profile_id)
