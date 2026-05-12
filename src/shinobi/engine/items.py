"""Effets canon des items consommables sur le personnage."""

from __future__ import annotations

from dataclasses import dataclass

from shinobi.engine.character import Character
from shinobi.engine.progression import (
    apply_fatigue,
)
from shinobi.i18n import t


@dataclass(frozen=True)
class ItemEffect:
    """Resultat de l'utilisation d'un item."""

    success: bool
    summary_fr: str


def use_item(character: Character, item_id: str) -> tuple[Character, ItemEffect]:
    """Consomme un item de l'inventaire et applique son effet."""
    if character.inventory.misc.get(item_id, 0) <= 0:
        return character, ItemEffect(
            success=False,
            summary_fr=t("engine.items.no_inventory", item_id=item_id),
        )

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
            summary_fr=t("engine.items.soldier_pill.summary", gain=gain),
        )

    if item_id == "blood_pill":
        gain_hp = 30
        new_health = new_char.health.model_copy(
            update={"hp_current": min(new_char.health.hp_max, new_char.health.hp_current + gain_hp)}
        )
        new_char = new_char.with_health(new_health)
        return new_char, ItemEffect(
            success=True,
            summary_fr=t("engine.items.blood_pill.summary", gain=gain_hp),
        )

    if item_id == "antidote":
        new_health = new_char.health.model_copy(update={"poison_status": []})
        new_char = new_char.with_health(new_health)
        return new_char, ItemEffect(
            success=True,
            summary_fr=t("engine.items.antidote.summary"),
        )

    if item_id == "ration_bar":
        new_char = apply_fatigue(new_char, -10)  # -10 fatigue
        return new_char, ItemEffect(
            success=True,
            summary_fr=t("engine.items.ration_bar.summary"),
        )

    if item_id == "ramen_bowl":
        new_char = apply_fatigue(new_char, -25)
        new_health = new_char.health.model_copy(
            update={"hp_current": min(new_char.health.hp_max, new_char.health.hp_current + 10)}
        )
        new_char = new_char.with_health(new_health)
        return new_char, ItemEffect(
            success=True,
            summary_fr=t("engine.items.ramen_bowl.summary"),
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
            summary_fr=t("engine.items.first_aid_kit.summary", gain=gain_hp),
        )

    if item_id == "sake_jar":
        new_char = apply_fatigue(new_char, -15)
        # Sake = baisse temporaire de perception (non modelisee ici, narratif)
        return new_char, ItemEffect(
            success=True,
            summary_fr=t("engine.items.sake_jar.summary"),
        )

    if item_id == "smoke_bomb":
        return new_char, ItemEffect(
            success=True,
            summary_fr=t("engine.items.smoke_bomb.summary"),
        )

    if item_id == "explosive_tag":
        return new_char, ItemEffect(
            success=True,
            summary_fr=t("engine.items.explosive_tag.summary"),
        )

    return new_char, ItemEffect(
        success=True,
        summary_fr=t("engine.items.unknown_effect", item_id=item_id),
    )
