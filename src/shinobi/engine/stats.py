"""Stats de personnage et derives."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from shinobi.constants import MAX_STAT_VALUE_LEGENDARY, MIN_STAT_VALUE


class CoreStats(BaseModel):
    """Stats principales (echelle 0.0 a 5.0, jusqu'a 10 pour legendaires)."""

    model_config = ConfigDict(frozen=True)

    ninjutsu: float = 1.0
    taijutsu: float = 1.0
    genjutsu: float = 1.0
    intelligence: float = 1.0
    strength: float = 1.0
    speed: float = 1.0
    stamina: float = 1.0
    hand_seals: float = 1.0


class ExtendedStats(BaseModel):
    """Stats etendues."""

    model_config = ConfigDict(frozen=True)

    chakra_pool_max: int = 100
    chakra_control: float = 1.0
    chakra_reserves: float = 1.0
    learning_genius: float = 1.0
    social_charisma: float = 1.0
    leadership: float = 1.0
    luck: float = 1.0
    beauty: float = 1.0
    lineage_value: float = 1.0
    willpower: float = 1.0
    perception: float = 1.0
    medical_knowledge: float = 0.0
    fuinjutsu_knowledge: float = 0.0
    senjutsu_aptitude: float = 0.0


def clamp_stat(value: float) -> float:
    """Borne une stat entre 0.0 et la limite legendaire."""
    return max(MIN_STAT_VALUE, min(MAX_STAT_VALUE_LEGENDARY, value))


def databook_total(stats: CoreStats) -> float:
    """Total databook : somme des 8 stats principales."""
    return (
        stats.ninjutsu
        + stats.taijutsu
        + stats.genjutsu
        + stats.intelligence
        + stats.strength
        + stats.speed
        + stats.stamina
        + stats.hand_seals
    )


def average_combat_stat(stats: CoreStats) -> float:
    """Moyenne pour estimer la puissance de combat globale."""
    return (stats.ninjutsu + stats.taijutsu + stats.genjutsu + stats.strength + stats.speed) / 5.0


def aging_decay(value: float, *, age: int, peak_age: int = 30) -> float:
    """Applique un decay lie a l'age sur une stat physique."""
    if age <= peak_age:
        return value
    decline = (age - peak_age) * 0.02
    return clamp_stat(value - decline)


def aging_growth(value: float, *, age: int, target: float, rate: float = 0.05) -> float:
    """Croissance progressive vers une cible (jeunes en formation)."""
    diff = target - value
    return clamp_stat(value + diff * rate)
