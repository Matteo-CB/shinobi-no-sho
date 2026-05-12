"""Boutiques par village : items disponibles + prix.

Catalogue d'items canoniquement coherents avec l'univers Naruto.
Permet /buy et /sell dans la CLI.
"""

from __future__ import annotations

from dataclasses import dataclass

from shinobi.engine.character import Character, Inventory, OwnedWeapon
from shinobi.i18n import t


@dataclass(frozen=True)
class ShopItem:
    """Item achetable dans une boutique.

    Le nom et la description sont resolus via i18n a l'affichage
    (utiliser shop_item_name / shop_item_description).
    """

    id: str
    category: str  # "weapon", "consumable", "scroll", "tool", "clothing"
    base_price_ryos: int


# Catalogue universel d'items achetables. Les libelles FR/EN sont stockes dans data/i18n.
ITEM_CATALOG: dict[str, ShopItem] = {
    "kunai": ShopItem(id="kunai", category="weapon", base_price_ryos=200),
    "shuriken": ShopItem(id="shuriken", category="weapon", base_price_ryos=150),
    "fuma_shuriken": ShopItem(id="fuma_shuriken", category="weapon", base_price_ryos=2500),
    "smoke_bomb": ShopItem(id="smoke_bomb", category="consumable", base_price_ryos=500),
    "explosive_tag": ShopItem(id="explosive_tag", category="consumable", base_price_ryos=800),
    "soldier_pill": ShopItem(id="soldier_pill", category="consumable", base_price_ryos=3000),
    "blood_pill": ShopItem(id="blood_pill", category="consumable", base_price_ryos=1500),
    "antidote": ShopItem(id="antidote", category="consumable", base_price_ryos=2000),
    "ration_bar": ShopItem(id="ration_bar", category="consumable", base_price_ryos=100),
    "scroll_basic_e": ShopItem(id="scroll_basic_e", category="scroll", base_price_ryos=100),
    "scroll_basic_d": ShopItem(id="scroll_basic_d", category="scroll", base_price_ryos=1000),
    "scroll_basic_c": ShopItem(id="scroll_basic_c", category="scroll", base_price_ryos=10000),
    "scroll_basic_b": ShopItem(id="scroll_basic_b", category="scroll", base_price_ryos=100000),
    "scroll_basic_a": ShopItem(id="scroll_basic_a", category="scroll", base_price_ryos=1000000),
    "rope": ShopItem(id="rope", category="tool", base_price_ryos=100),
    "wire": ShopItem(id="wire", category="tool", base_price_ryos=300),
    "makibishi": ShopItem(id="makibishi", category="tool", base_price_ryos=400),
    "scope": ShopItem(id="scope", category="tool", base_price_ryos=2000),
    "sealing_scroll": ShopItem(id="sealing_scroll", category="tool", base_price_ryos=1500),
    "ninja_outfit": ShopItem(id="ninja_outfit", category="clothing", base_price_ryos=5000),
    "flak_jacket": ShopItem(id="flak_jacket", category="clothing", base_price_ryos=15000),
    "anbu_armor": ShopItem(id="anbu_armor", category="clothing", base_price_ryos=80000),
    "headband": ShopItem(id="headband", category="clothing", base_price_ryos=2000),
    "first_aid_kit": ShopItem(id="first_aid_kit", category="consumable", base_price_ryos=3500),
    "ramen_bowl": ShopItem(id="ramen_bowl", category="consumable", base_price_ryos=300),
    "sake_jar": ShopItem(id="sake_jar", category="consumable", base_price_ryos=1500),
}


def shop_item_name(item_id: str) -> str:
    """Resout le nom localise d'un item du catalogue."""
    return t(f"engine.shop.items.{item_id}.name")


def shop_item_description(item_id: str) -> str:
    """Resout la description localisee d'un item du catalogue."""
    return t(f"engine.shop.items.{item_id}.description")


# Inventaire de chaque village (subset du catalogue + multiplicateur de prix).
VILLAGE_INVENTORIES: dict[str, dict[str, float]] = {
    "konohagakure": {
        "kunai": 1.0, "shuriken": 1.0, "fuma_shuriken": 1.0, "smoke_bomb": 1.0,
        "explosive_tag": 1.0, "soldier_pill": 1.0, "blood_pill": 1.0, "antidote": 1.2,
        "ration_bar": 1.0, "scroll_basic_e": 1.0, "scroll_basic_d": 1.0, "scroll_basic_c": 1.0,
        "scroll_basic_b": 1.5, "rope": 1.0, "wire": 1.0, "makibishi": 1.0, "scope": 1.0,
        "sealing_scroll": 1.0, "ninja_outfit": 1.0, "flak_jacket": 1.0, "headband": 1.0,
        "first_aid_kit": 1.0, "ramen_bowl": 1.0, "sake_jar": 1.0,
    },
    "sunagakure": {
        "kunai": 1.1, "shuriken": 1.1, "smoke_bomb": 0.9, "explosive_tag": 0.8,
        "antidote": 0.7, "ration_bar": 1.2, "scroll_basic_e": 1.0, "scroll_basic_d": 1.0,
        "scroll_basic_c": 1.0, "rope": 1.0, "wire": 1.0, "makibishi": 0.9, "scope": 1.1,
        "sealing_scroll": 1.0, "ninja_outfit": 1.0, "headband": 1.0,
        "first_aid_kit": 1.0,
    },
    "kirigakure": {
        "kunai": 1.1, "shuriken": 1.1, "smoke_bomb": 1.2, "soldier_pill": 1.3,
        "ration_bar": 1.0, "scroll_basic_e": 1.0, "scroll_basic_d": 1.0,
        "rope": 1.0, "wire": 1.0, "ninja_outfit": 1.0, "headband": 1.0,
        "first_aid_kit": 1.0, "sake_jar": 0.8,
    },
    "kumogakure": {
        "kunai": 1.0, "shuriken": 1.0, "smoke_bomb": 1.0, "explosive_tag": 1.0,
        "soldier_pill": 1.0, "ration_bar": 1.0, "scroll_basic_e": 1.0,
        "scroll_basic_d": 1.0, "scroll_basic_c": 1.1, "rope": 1.0, "wire": 1.0,
        "ninja_outfit": 1.0, "flak_jacket": 1.0, "headband": 1.0, "first_aid_kit": 1.0,
    },
    "iwagakure": {
        "kunai": 1.0, "shuriken": 1.0, "explosive_tag": 0.7, "smoke_bomb": 1.0,
        "ration_bar": 1.0, "scroll_basic_e": 1.0, "scroll_basic_d": 1.0,
        "rope": 1.0, "wire": 1.0, "makibishi": 1.0, "scope": 1.0,
        "ninja_outfit": 1.0, "headband": 1.0, "first_aid_kit": 1.0,
    },
}

# Multiplicateur de revente : on rachete a 40% du prix d'achat (typique).
SELL_RATIO = 0.4


def list_shop_inventory(village_id: str) -> list[tuple[ShopItem, int]]:
    """Liste des items disponibles dans un village avec prix ajustes."""
    inv = VILLAGE_INVENTORIES.get(village_id, VILLAGE_INVENTORIES["konohagakure"])
    out: list[tuple[ShopItem, int]] = []
    for item_id, multiplier in inv.items():
        item = ITEM_CATALOG.get(item_id)
        if item:
            adjusted = int(item.base_price_ryos * multiplier)
            out.append((item, adjusted))
    return sorted(out, key=lambda kv: kv[1])


def buy_item(character: Character, item: ShopItem, price: int) -> tuple[Character, str]:
    """Achete un item si possible. Retourne (character, message).

    Les items de categorie 'weapon' sont ajoutes a character.weapons (OwnedWeapon).
    Les autres vont dans inventory.scrolls ou inventory.misc.
    """
    if character.money < price:
        return character, t("engine.shop.buy.insufficient", money=character.money, price=price)
    new_money = character.money - price
    name = shop_item_name(item.id)
    if item.category == "weapon":
        existing = next((w for w in character.weapons if w.weapon_id == item.id), None)
        if existing:
            updated = existing.model_copy(update={"quantity": existing.quantity + 1})
            new_weapons = [updated if w.weapon_id == item.id else w for w in character.weapons]
        else:
            new_weapons = [*character.weapons, OwnedWeapon(weapon_id=item.id, quantity=1)]
        new_char = character.model_copy(update={"money": new_money, "weapons": new_weapons})
        return new_char, t("engine.shop.buy.success_weapon", name=name, price=price)
    new_inv_misc = dict(character.inventory.misc)
    if item.category == "scroll":
        new_scrolls = list(character.inventory.scrolls)
        new_scrolls.append(item.id)
        new_inv = character.inventory.model_copy(update={"scrolls": new_scrolls})
    else:
        new_inv_misc[item.id] = new_inv_misc.get(item.id, 0) + 1
        new_inv = character.inventory.model_copy(update={"misc": new_inv_misc})
    new_char = character.model_copy(update={"money": new_money, "inventory": new_inv})
    return new_char, t("engine.shop.buy.success", name=name, price=price)


def sell_item(character: Character, item_id: str) -> tuple[Character, str]:
    """Vend un item de l'inventaire ou d'arme equipee. Retourne (character, message)."""
    item = ITEM_CATALOG.get(item_id)
    if item is None:
        return character, t("engine.shop.sell.unknown_item", item_id=item_id)
    sell_price = int(item.base_price_ryos * SELL_RATIO)
    name = shop_item_name(item.id)
    if item.category == "weapon":
        existing = next((w for w in character.weapons if w.weapon_id == item_id), None)
        if existing is None or existing.quantity <= 0:
            return character, t("engine.shop.sell.no_weapon")
        if existing.quantity > 1:
            updated = existing.model_copy(update={"quantity": existing.quantity - 1})
            new_weapons = [updated if w.weapon_id == item_id else w for w in character.weapons]
        else:
            new_weapons = [w for w in character.weapons if w.weapon_id != item_id]
        new_char = character.model_copy(
            update={"money": character.money + sell_price, "weapons": new_weapons}
        )
        return new_char, t("engine.shop.sell.success", name=name, price=sell_price)
    if item.category == "scroll":
        if item_id not in character.inventory.scrolls:
            return character, t("engine.shop.sell.no_scroll")
        new_scrolls = list(character.inventory.scrolls)
        new_scrolls.remove(item_id)
        new_inv = character.inventory.model_copy(update={"scrolls": new_scrolls})
    else:
        if character.inventory.misc.get(item_id, 0) <= 0:
            return character, t("engine.shop.sell.no_item")
        new_misc = dict(character.inventory.misc)
        new_misc[item_id] -= 1
        if new_misc[item_id] <= 0:
            del new_misc[item_id]
        new_inv = character.inventory.model_copy(update={"misc": new_misc})
    new_char = character.model_copy(
        update={"money": character.money + sell_price, "inventory": new_inv}
    )
    return new_char, t("engine.shop.sell.success", name=name, price=sell_price)


def get_inventory_summary(
    inventory: Inventory, weapons: list[OwnedWeapon] | None = None
) -> list[tuple[str, int]]:
    """Liste les items detenus avec leurs quantites (inventaire + armes)."""
    out: list[tuple[str, int]] = []
    for scroll_id in inventory.scrolls:
        out.append((scroll_id, 1))
    for item_id, qty in inventory.misc.items():
        out.append((item_id, qty))
    if weapons:
        for w in weapons:
            out.append((w.weapon_id, w.quantity))
    return out
