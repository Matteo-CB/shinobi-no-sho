"""Boutiques par village : items disponibles + prix.

Catalogue d'items canoniquement coherents avec l'univers Naruto.
Permet /buy et /sell dans la CLI.
"""

from __future__ import annotations

from dataclasses import dataclass

from shinobi.engine.character import Character, Inventory, OwnedWeapon


@dataclass(frozen=True)
class ShopItem:
    """Item achetable dans une boutique."""

    id: str
    name_fr: str
    category: str  # "weapon", "consumable", "scroll", "tool", "clothing"
    base_price_ryos: int
    description_fr: str


# Catalogue universel d'items achetables.
ITEM_CATALOG: dict[str, ShopItem] = {
    "kunai": ShopItem(
        id="kunai", name_fr="Kunai", category="weapon", base_price_ryos=200,
        description_fr="Lame ninja standard, lancable ou maniable au corps a corps."
    ),
    "shuriken": ShopItem(
        id="shuriken", name_fr="Shuriken", category="weapon", base_price_ryos=150,
        description_fr="Etoile a lancer, projectile rapide a longue portee."
    ),
    "fuma_shuriken": ShopItem(
        id="fuma_shuriken", name_fr="Fuma shuriken", category="weapon", base_price_ryos=2500,
        description_fr="Grand shuriken pliable, tres puissant. Usage tactique."
    ),
    "smoke_bomb": ShopItem(
        id="smoke_bomb", name_fr="Bombe fumigene", category="consumable", base_price_ryos=500,
        description_fr="Disparait instantanement dans un nuage opaque."
    ),
    "explosive_tag": ShopItem(
        id="explosive_tag", name_fr="Sceau explosif", category="consumable", base_price_ryos=800,
        description_fr="Parchemin a apposer, explose au declenchement."
    ),
    "soldier_pill": ShopItem(
        id="soldier_pill", name_fr="Pilule du soldat", category="consumable", base_price_ryos=3000,
        description_fr="Restaure 50% du chakra max. Effets secondaires si abus."
    ),
    "blood_pill": ShopItem(
        id="blood_pill", name_fr="Pilule de sang", category="consumable", base_price_ryos=1500,
        description_fr="Acceler la regeneration sanguine apres une blessure."
    ),
    "antidote": ShopItem(
        id="antidote", name_fr="Antidote universel", category="consumable", base_price_ryos=2000,
        description_fr="Neutralise la plupart des poisons ninja."
    ),
    "ration_bar": ShopItem(
        id="ration_bar", name_fr="Ration de mission", category="consumable", base_price_ryos=100,
        description_fr="Concentre nutritionnel pour une journee."
    ),
    "scroll_basic_e": ShopItem(
        id="scroll_basic_e", name_fr="Parchemin technique E", category="scroll", base_price_ryos=100,
        description_fr="Technique mineure (Henge, Bunshin, Kawarimi)."
    ),
    "scroll_basic_d": ShopItem(
        id="scroll_basic_d", name_fr="Parchemin technique D", category="scroll", base_price_ryos=1000,
        description_fr="Technique de niveau genin."
    ),
    "scroll_basic_c": ShopItem(
        id="scroll_basic_c", name_fr="Parchemin technique C", category="scroll", base_price_ryos=10000,
        description_fr="Technique de niveau chunin (ex: Bunshin no Jutsu de masse)."
    ),
    "scroll_basic_b": ShopItem(
        id="scroll_basic_b", name_fr="Parchemin technique B", category="scroll", base_price_ryos=100000,
        description_fr="Technique tactique avancee, vendue rarement."
    ),
    "scroll_basic_a": ShopItem(
        id="scroll_basic_a", name_fr="Parchemin technique A", category="scroll", base_price_ryos=1000000,
        description_fr="Technique d'elite, vendue uniquement aux ninjas confirmes."
    ),
    "rope": ShopItem(
        id="rope", name_fr="Corde ninja", category="tool", base_price_ryos=100,
        description_fr="Corde renforcee de chakra, immobilise les cibles."
    ),
    "wire": ShopItem(
        id="wire", name_fr="Fil de chakra", category="tool", base_price_ryos=300,
        description_fr="Fil ultra-fin pour pieges et techniques de fuinjutsu."
    ),
    "makibishi": ShopItem(
        id="makibishi", name_fr="Pointes makibishi", category="tool", base_price_ryos=400,
        description_fr="Pointes a disperser au sol pour ralentir un poursuivant."
    ),
    "scope": ShopItem(
        id="scope", name_fr="Lunette ninja", category="tool", base_price_ryos=2000,
        description_fr="Optique a longue portee pour reconnaissance discrete."
    ),
    "sealing_scroll": ShopItem(
        id="sealing_scroll", name_fr="Parchemin de scellement", category="tool", base_price_ryos=1500,
        description_fr="Permet de stocker et transporter des objets dans un sceau."
    ),
    "ninja_outfit": ShopItem(
        id="ninja_outfit", name_fr="Tenue ninja standard", category="clothing", base_price_ryos=5000,
        description_fr="Tunique flexible et resistante, adaptable a toutes missions."
    ),
    "flak_jacket": ShopItem(
        id="flak_jacket", name_fr="Veste de chunin", category="clothing", base_price_ryos=15000,
        description_fr="Veste tactique chunin avec poches multiples et protection legere."
    ),
    "anbu_armor": ShopItem(
        id="anbu_armor", name_fr="Armure Anbu", category="clothing", base_price_ryos=80000,
        description_fr="Armure ceramique tres resistante, masque inclus. Reservee aux Anbu."
    ),
    "headband": ShopItem(
        id="headband", name_fr="Bandeau de village", category="clothing", base_price_ryos=2000,
        description_fr="Bandeau frontal avec embleme du village. Symbole d'appartenance."
    ),
    "first_aid_kit": ShopItem(
        id="first_aid_kit", name_fr="Trousse medicale", category="consumable", base_price_ryos=3500,
        description_fr="Bandages, sutures, herbes medicinales pour soins de terrain."
    ),
    "ramen_bowl": ShopItem(
        id="ramen_bowl", name_fr="Bol de ramen", category="consumable", base_price_ryos=300,
        description_fr="Repas chaud, restaure une partie de la fatigue."
    ),
    "sake_jar": ShopItem(
        id="sake_jar", name_fr="Jarre de sake", category="consumable", base_price_ryos=1500,
        description_fr="Boisson alcoolisee. Effets variables selon la consommation."
    ),
}


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
        return character, f"Pas assez de ryos ({character.money} / {price} requis)."
    new_money = character.money - price
    if item.category == "weapon":
        existing = next((w for w in character.weapons if w.weapon_id == item.id), None)
        if existing:
            updated = existing.model_copy(update={"quantity": existing.quantity + 1})
            new_weapons = [updated if w.weapon_id == item.id else w for w in character.weapons]
        else:
            new_weapons = [*character.weapons, OwnedWeapon(weapon_id=item.id, quantity=1)]
        new_char = character.model_copy(update={"money": new_money, "weapons": new_weapons})
        return new_char, f"Achete : {item.name_fr} pour {price} ryos. (arme equipee)"
    new_inv_misc = dict(character.inventory.misc)
    if item.category == "scroll":
        new_scrolls = list(character.inventory.scrolls)
        new_scrolls.append(item.id)
        new_inv = character.inventory.model_copy(update={"scrolls": new_scrolls})
    else:
        new_inv_misc[item.id] = new_inv_misc.get(item.id, 0) + 1
        new_inv = character.inventory.model_copy(update={"misc": new_inv_misc})
    new_char = character.model_copy(update={"money": new_money, "inventory": new_inv})
    return new_char, f"Achete : {item.name_fr} pour {price} ryos."


def sell_item(character: Character, item_id: str) -> tuple[Character, str]:
    """Vend un item de l'inventaire ou d'arme equipee. Retourne (character, message)."""
    item = ITEM_CATALOG.get(item_id)
    if item is None:
        return character, f"Item inconnu : {item_id}"
    sell_price = int(item.base_price_ryos * SELL_RATIO)
    if item.category == "weapon":
        existing = next((w for w in character.weapons if w.weapon_id == item_id), None)
        if existing is None or existing.quantity <= 0:
            return character, "Tu n'as pas cette arme."
        if existing.quantity > 1:
            updated = existing.model_copy(update={"quantity": existing.quantity - 1})
            new_weapons = [updated if w.weapon_id == item_id else w for w in character.weapons]
        else:
            new_weapons = [w for w in character.weapons if w.weapon_id != item_id]
        new_char = character.model_copy(
            update={"money": character.money + sell_price, "weapons": new_weapons}
        )
        return new_char, f"Vendu : {item.name_fr} pour {sell_price} ryos."
    if item.category == "scroll":
        if item_id not in character.inventory.scrolls:
            return character, "Tu n'as pas ce parchemin."
        new_scrolls = list(character.inventory.scrolls)
        new_scrolls.remove(item_id)
        new_inv = character.inventory.model_copy(update={"scrolls": new_scrolls})
    else:
        if character.inventory.misc.get(item_id, 0) <= 0:
            return character, "Tu n'as pas cet item."
        new_misc = dict(character.inventory.misc)
        new_misc[item_id] -= 1
        if new_misc[item_id] <= 0:
            del new_misc[item_id]
        new_inv = character.inventory.model_copy(update={"misc": new_misc})
    new_char = character.model_copy(
        update={"money": character.money + sell_price, "inventory": new_inv}
    )
    return new_char, f"Vendu : {item.name_fr} pour {sell_price} ryos."


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
