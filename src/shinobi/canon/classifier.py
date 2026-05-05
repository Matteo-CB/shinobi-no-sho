"""Classification d'une page Narutopedia en type d'entite canonique.

S'appuie sur les categories MediaWiki pour deviner le type. Une page peut
appartenir a plusieurs categories ; on prend le premier match dans l'ordre
de priorite ci-dessous.
"""

from __future__ import annotations

from enum import StrEnum


class EntityType(StrEnum):
    character = "character"
    technique = "technique"
    clan = "clan"
    village = "village"
    organization = "organization"
    location = "location"
    weapon_tool = "weapon_tool"
    tailed_beast = "tailed_beast"
    kekkei_genkai = "kekkei_genkai"
    kekkei_mora = "kekkei_mora"
    hiden = "hiden"
    rank = "rank"
    era = "era"
    nature = "nature"
    timeline_event = "timeline_event"
    movie = "movie"
    novel = "novel"
    game = "game"
    chapter = "chapter"
    episode = "episode"
    other = "other"
    redirect = "redirect"


# Indicateurs ordonnes par priorite. Le premier match gagne.
_PRIORITY_RULES: list[tuple[EntityType, tuple[str, ...]]] = [
    (EntityType.tailed_beast, ("Category:Tailed Beasts", "Category:Tailed Beast")),
    (EntityType.kekkei_mora, ("Category:Kekkei Mora",)),
    (EntityType.kekkei_genkai, ("Category:Kekkei Genkai",)),
    (EntityType.hiden, ("Category:Hiden",)),
    (EntityType.clan, ("Category:Clans",)),
    (EntityType.village, ("Category:Villages", "Category:Hidden Villages", "Category:Lands")),
    (
        EntityType.organization,
        (
            "Category:Organisations",
            "Category:Organizations",
            "Category:Akatsuki",
            "Category:Anbu",
            "Category:Kara",
        ),
    ),
    (EntityType.weapon_tool, ("Category:Weapons", "Category:Tools", "Category:Equipment")),
    (EntityType.location, ("Category:Locations", "Category:Battles")),
    (EntityType.timeline_event, ("Category:Events", "Category:Wars", "Category:Battles")),
    (
        EntityType.technique,
        (
            "Category:Jutsu",
            "Category:Techniques",
            "Category:Ninjutsu",
            "Category:Genjutsu",
            "Category:Taijutsu",
        ),
    ),
    (EntityType.movie, ("Category:Movies", "Category:Films")),
    (EntityType.novel, ("Category:Novels",)),
    (EntityType.game, ("Category:Video Games", "Category:Games")),
    (EntityType.chapter, ("Category:Chapters",)),
    (EntityType.episode, ("Category:Episodes",)),
    (
        EntityType.character,
        (
            "Category:Characters",
            "Category:Konohagakure Characters",
            "Category:Sunagakure Characters",
            "Category:Kirigakure Characters",
            "Category:Kumogakure Characters",
            "Category:Iwagakure Characters",
            "Category:Otogakure Characters",
            "Category:Akatsuki Members",
        ),
    ),
]


def classify_categories(categories: list[str]) -> EntityType:
    """Retourne le type d'entite le plus pertinent pour ces categories."""
    if not categories:
        return EntityType.other
    cat_set = {c.strip() for c in categories}
    for entity_type, indicators in _PRIORITY_RULES:
        for ind in indicators:
            for cat in cat_set:
                if cat == ind or cat.startswith(ind):
                    return entity_type
    return EntityType.other


def classify_redirect(redirect_target: str | None) -> EntityType:
    """Marque une page comme redirect (pas une entite a part entiere)."""
    if redirect_target:
        return EntityType.redirect
    return EntityType.other
