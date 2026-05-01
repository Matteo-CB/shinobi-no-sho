"""Progression du temps : vieillissement, decay, croissance."""

from __future__ import annotations

from shinobi.engine.character import Character
from shinobi.engine.stats import CoreStats, ExtendedStats, aging_decay, aging_growth


def advance_age(character: Character, new_age: int) -> Character:
    """Avance l'age et applique les modifications stats par tranche d'age."""
    if new_age <= character.age_years:
        return character
    new_stats = _apply_aging_core(character.stats, age=new_age)
    new_extended = _apply_aging_extended(character.extended_stats, age=new_age)
    return character.model_copy(
        update={"age_years": new_age, "stats": new_stats, "extended_stats": new_extended}
    )


def _apply_aging_core(stats: CoreStats, *, age: int) -> CoreStats:
    """Decay physique apres 30, croissance avant 18."""
    if age >= 30:
        return stats.model_copy(
            update={
                "speed": aging_decay(stats.speed, age=age),
                "strength": aging_decay(stats.strength, age=age),
                "stamina": aging_decay(stats.stamina, age=age),
                "taijutsu": aging_decay(stats.taijutsu, age=age),
            }
        )
    if age < 18:
        target = 3.0
        return stats.model_copy(
            update={
                "speed": aging_growth(stats.speed, age=age, target=target, rate=0.02),
                "strength": aging_growth(stats.strength, age=age, target=target, rate=0.02),
                "stamina": aging_growth(stats.stamina, age=age, target=target, rate=0.02),
                "taijutsu": aging_growth(stats.taijutsu, age=age, target=target, rate=0.02),
                "ninjutsu": aging_growth(stats.ninjutsu, age=age, target=target, rate=0.02),
            }
        )
    return stats


def _apply_aging_extended(stats: ExtendedStats, *, age: int) -> ExtendedStats:
    """Sagesse augmente avec l'age, beauty pic vers 25."""
    if age >= 30:
        return stats.model_copy(
            update={
                "beauty": aging_decay(stats.beauty, age=age, peak_age=25),
            }
        )
    return stats
