"""20 dimensions de personnalite continues normalisees [0.0, 1.0].

Phase D / docs/02 §6.2 :

> 20 dimensions continues normalisees 0-1
>   aggression, loyalty, secrecy, ambition, fear, idealism,
>   pragmatism, empathy, confidence, paranoia
>   ... 10 autres

Choix des 20 axes : on couvre les grandes familles de la psychologie
de personnage de Naruto (combat / loyaute / strategie / emotionnel /
moral / social) sans faire un Big Five academique. Chaque axe est :

- continu (float dans [0.0, 1.0])
- additif (les drifts s'accumulent par sigmoid)
- comparable au baseline canon (distance euclidienne)
- interpretable seul ('aggression eleve = tendance a frapper d'abord')

Les dimensions sont NON exclusives. Un PNJ peut avoir loyalty=0.9 ET
paranoia=0.9 (Itachi, Obito, Sasuke). Pas de zero-sum entre axes.
"""

from __future__ import annotations

from enum import StrEnum


class PersonalityDimension(StrEnum):
    """Liste exhaustive et stable des 20 axes de personnalite."""

    # Famille combat / impulsivite
    aggression = "aggression"
    recklessness = "recklessness"
    discipline = "discipline"

    # Famille loyaute / lien
    loyalty = "loyalty"
    empathy = "empathy"
    isolationism = "isolationism"

    # Famille strategie / controle
    secrecy = "secrecy"
    manipulation = "manipulation"
    pragmatism = "pragmatism"

    # Famille emotion
    fear = "fear"
    melancholy = "melancholy"
    paranoia = "paranoia"

    # Famille morale
    idealism = "idealism"
    honor = "honor"
    vengeance = "vengeance"

    # Famille amour-propre / drive
    ambition = "ambition"
    confidence = "confidence"
    pride = "pride"

    # Famille curiosite / ouverture
    openness = "openness"

    # Humour et legerete
    humor = "humor"


ALL_DIMENSIONS: tuple[PersonalityDimension, ...] = tuple(PersonalityDimension)
"""Tuple gele des 20 dimensions, ordre stable (pour vecteurs)."""


def dimension_index(dim: PersonalityDimension) -> int:
    """Retourne l'index canonique d'une dimension (0..19)."""
    return ALL_DIMENSIONS.index(dim)


# Defaut neutre = 0.5 (moyen). Les baselines canon viennent supplanter.
DEFAULT_NEUTRAL_VALUE: float = 0.5


__all__ = [
    "ALL_DIMENSIONS",
    "DEFAULT_NEUTRAL_VALUE",
    "PersonalityDimension",
    "dimension_index",
]
