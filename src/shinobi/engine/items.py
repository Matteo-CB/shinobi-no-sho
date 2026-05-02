"""Effets canon des items consommables sur le personnage."""

from __future__ import annotations

from dataclasses import dataclass

from shinobi.engine.character import Character
from shinobi.engine.progression import (
    apply_fatigue,
)


@dataclass(frozen=True)
class ItemEffect:
    """Resultat de l'utilisation d'un item."""

    success: bool
    summary_fr: str


def use_item(character: Character, item_id: str) -> tuple[Character, ItemEffect]:
    """Consomme un item de l'inventaire et applique son effet."""
    if character.inventory.misc.get(item_id, 0) <= 0:
        return character, ItemEffect(success=False, summary_fr=f"Tu n'as pas d'{item_id} dans ton inventaire.")

    new_misc = dict(character.inventory.misc)
    new_misc[item_id] -= 1
    if new_misc[item_id] <= 0:
        del new_misc[item_id]
    new_inv = character.inventory.model_copy(update={"misc": new_misc})
    new_char = character.model_copy(update={"inventory": new_inv})

    # Effets specifiques
    if item_id == "soldier_pill":
        gain = character.chakra.max // 2
        new_chakra = new_char.chakra.model_copy(
            update={"current": min(new_char.chakra.max, new_char.chakra.current + gain)}
        )
        new_char = new_char.with_chakra(new_chakra)
        # Effet secondaire : +5 fatigue
        new_char = apply_fatigue(new_char, 5)
        return new_char, ItemEffect(
            success=True,
            summary_fr=f"Tu avales la pilule du soldat. Chakra +{gain}, mais ton corps en paiera le prix plus tard.",
        )

    if item_id == "blood_pill":
        gain_hp = 30
        new_health = new_char.health.model_copy(
            update={"hp_current": min(new_char.health.hp_max, new_char.health.hp_current + gain_hp)}
        )
        new_char = new_char.with_health(new_health)
        return new_char, ItemEffect(
            success=True,
            summary_fr=f"Tu avales la pilule de sang. La regeneration s'accelere : HP +{gain_hp}.",
        )

    if item_id == "antidote":
        new_health = new_char.health.model_copy(update={"poison_status": []})
        new_char = new_char.with_health(new_health)
        return new_char, ItemEffect(
            success=True,
            summary_fr="Tu prends l'antidote. Tous les poisons en circulation sont neutralises.",
        )

    if item_id == "ration_bar":
        new_char = apply_fatigue(new_char, -10)  # -10 fatigue
        return new_char, ItemEffect(
            success=True,
            summary_fr="Tu manges la ration. Fatigue legerement reduite.",
        )

    if item_id == "ramen_bowl":
        new_char = apply_fatigue(new_char, -25)
        new_health = new_char.health.model_copy(
            update={"hp_current": min(new_char.health.hp_max, new_char.health.hp_current + 10)}
        )
        new_char = new_char.with_health(new_health)
        return new_char, ItemEffect(
            success=True,
            summary_fr="Le bol de ramen chaud te ravigote. Fatigue -25, HP +10.",
        )

    if item_id == "first_aid_kit":
        gain_hp = 50
        new_health = new_char.health.model_copy(
            update={
                "hp_current": min(new_char.health.hp_max, new_char.health.hp_current + gain_hp),
                "injuries": [],
            }
        )
        new_char = new_char.with_health(new_health)
        return new_char, ItemEffect(
            success=True,
            summary_fr=f"Tu utilises la trousse medicale. HP +{gain_hp}, blessures soignees.",
        )

    if item_id == "sake_jar":
        new_char = apply_fatigue(new_char, -15)
        # Sake = baisse temporaire de perception (non modelisee ici, narratif)
        return new_char, ItemEffect(
            success=True,
            summary_fr="Tu bois la jarre de sake. Tu te sens plus detendu, mais ta perception en patit.",
        )

    if item_id == "smoke_bomb":
        return new_char, ItemEffect(
            success=True,
            summary_fr="Tu actives la bombe fumigene. Un nuage opaque masque ta retraite.",
        )

    if item_id == "explosive_tag":
        return new_char, ItemEffect(
            success=True,
            summary_fr="Tu apposes le sceau explosif. Pret a declencher.",
        )

    return new_char, ItemEffect(
        success=True,
        summary_fr=f"Tu utilises {item_id}. Effet narratif uniquement, pas de mecanique definie.",
    )
