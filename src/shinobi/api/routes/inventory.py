"""Routes /inventory Phase 9.

View inventaire, achat boutique village, vente, utilisation.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from shinobi.api.schemas import (
    BuyItemRequest,
    InventoryItem,
    InventoryResponse,
    InvokeRequest,
    InvokeResponse,
    ItemActionResponse,
    SellItemRequest,
    ShopInventoryResponse,
    ShopItemSummary,
    SignContractRequest,
    SummonContractEntry,
    SummonsResponse,
    UseItemRequest,
    WeaponEntry,
    WeaponsResponse,
)
from shinobi.engine.items import use_item
from shinobi.engine.shop import (
    ITEM_CATALOG,
    buy_item,
    list_shop_inventory,
    sell_item,
    shop_item_description,
    shop_item_name,
)
from shinobi.errors import SaveNotFoundError
from shinobi.i18n import t
from shinobi.persistence import saves as save_module

router = APIRouter(prefix="/play/{save_id}", tags=["inventory"])


# Identifiants canoniques des contrats d'invocation (mirror CLI play.py).
# Le libelle est resolu via i18n (cle cli.play.summons.<id>.label).
CANONICAL_SUMMONS_IDS: tuple[str, ...] = (
    "toad", "snake", "slug", "hawk", "monkey", "ninken", "weasel", "crow", "dragon",
)


def _summon_label(contract_id: str) -> str:
    return t(f"cli.play.summons.{contract_id}.label")


def _persist_character(save_id: str, character) -> None:
    """Update the current character snapshot (without touching world / total_turns)."""
    from shinobi.persistence.database import close, open_connection
    from shinobi.persistence.serialize import encode_payload

    state_path = save_module._state_path(save_id)
    conn = open_connection(state_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE character SET payload = ? WHERE is_current = 1",
            (encode_payload(character),),
        )
        conn.commit()
    finally:
        close(conn)


@router.get(
    "/inventory",
    response_model=InventoryResponse,
    summary="Full inventory (items + weapons + ryos)",
)
def get_inventory(save_id: str) -> InventoryResponse:
    """List all owned items with quantity + category."""
    try:
        character, _, _ = save_module.load_save(save_id)
    except SaveNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc),
        ) from exc
    items: list[InventoryItem] = []
    for w in character.weapons:
        meta = ITEM_CATALOG.get(w.weapon_id)
        nm = shop_item_name(w.weapon_id) if meta else None
        items.append(
            InventoryItem(
                item_id=w.weapon_id,
                name=nm,  # Phase i18n.9 : alias jusqu'a stock multi-lang
                name_fr=nm,
                quantity=w.quantity,
                category="weapon",
            )
        )
    for scroll_id in character.inventory.scrolls:
        meta = ITEM_CATALOG.get(scroll_id)
        nm = shop_item_name(scroll_id) if meta else None
        items.append(
            InventoryItem(
                item_id=scroll_id,
                name=nm,
                name_fr=nm,
                quantity=1,
                category="scroll",
            )
        )
    for item_id, qty in character.inventory.misc.items():
        meta = ITEM_CATALOG.get(item_id)
        nm = shop_item_name(item_id) if meta else None
        items.append(
            InventoryItem(
                item_id=item_id,
                name=nm,
                name_fr=nm,
                quantity=qty,
                category=meta.category if meta else "misc",
            )
        )
    for cons_id, qty in character.inventory.consumables.items():
        meta = ITEM_CATALOG.get(cons_id)
        nm = shop_item_name(cons_id) if meta else None
        items.append(
            InventoryItem(
                item_id=cons_id,
                name=nm,
                name_fr=nm,
                quantity=qty,
                category="consumable",
            )
        )
    return InventoryResponse(
        save_id=save_id,
        money_ryos=getattr(character, "money", 0),
        items=items,
    )


@router.get(
    "/shop",
    response_model=ShopInventoryResponse,
    summary="Shop inventory at the current village",
)
def get_shop(save_id: str) -> ShopInventoryResponse:
    """List items sold at the current village + adjusted prices."""
    try:
        character, _, _ = save_module.load_save(save_id)
    except SaveNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc),
        ) from exc
    pairs = list_shop_inventory(character.current_village)
    return ShopInventoryResponse(
        village_id=character.current_village,
        items=[
            ShopItemSummary(
                id=item.id,
                name=shop_item_name(item.id),
                name_fr=shop_item_name(item.id),
                category=item.category,
                price_ryos=price,
                description=shop_item_description(item.id),
                description_fr=shop_item_description(item.id),
            )
            for item, price in pairs
        ],
    )


@router.post(
    "/shop/buy",
    response_model=ItemActionResponse,
    summary="Buy an item from the village shop",
)
def buy(save_id: str, payload: BuyItemRequest) -> ItemActionResponse:
    """Buy an item if the player has enough ryos."""
    try:
        character, _, _ = save_module.load_save(save_id)
    except SaveNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc),
        ) from exc
    pairs = list_shop_inventory(character.current_village)
    pair = next((p for p in pairs if p[0].id == payload.item_id), None)
    if pair is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=t(
                "api.inventory.item_unavailable",
                item_id=payload.item_id,
                village=character.current_village,
            ),
        )
    item, price = pair
    new_char, message = buy_item(character, item, price)
    if new_char is character:
        # Achat refuse (ex: pas assez de ryos)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=message,
        )
    _persist_character(save_id, new_char)
    return ItemActionResponse(
        item_id=item.id, message=message, new_money=new_char.money,
    )


@router.post(
    "/shop/sell",
    response_model=ItemActionResponse,
    summary="Sell an item at the village resale price",
)
def sell(save_id: str, payload: SellItemRequest) -> ItemActionResponse:
    """Sell an item from the inventory."""
    try:
        character, _, _ = save_module.load_save(save_id)
    except SaveNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc),
        ) from exc
    new_char, message = sell_item(character, payload.item_id)
    if new_char is character:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=message,
        )
    _persist_character(save_id, new_char)
    return ItemActionResponse(
        item_id=payload.item_id, message=message, new_money=new_char.money,
    )


@router.post(
    "/inventory/use",
    response_model=ItemActionResponse,
    summary="Consume an item (soldier_pill, antidote, ...)",
)
def use(save_id: str, payload: UseItemRequest) -> ItemActionResponse:
    """Utilise un item consommable."""
    try:
        character, _, _ = save_module.load_save(save_id)
    except SaveNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc),
        ) from exc
    new_char, effect = use_item(character, payload.item_id)
    if not effect.success:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=effect.summary_fr,
        )
    _persist_character(save_id, new_char)
    return ItemActionResponse(
        item_id=payload.item_id,
        message=effect.summary_fr,
        new_money=new_char.money,
    )


@router.get(
    "/weapons",
    response_model=WeaponsResponse,
    summary="Dedicated view of equipped weapons",
)
def get_weapons(save_id: str) -> WeaponsResponse:
    """List equipped weapons (typed subset of /inventory)."""
    try:
        character, _, _ = save_module.load_save(save_id)
    except SaveNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc),
        ) from exc
    return WeaponsResponse(
        save_id=save_id,
        weapons=[
            WeaponEntry(
                weapon_id=w.weapon_id, quantity=w.quantity, quality=w.quality,
            )
            for w in character.weapons
        ],
        count=len(character.weapons),
    )


@router.get(
    "/summons",
    response_model=SummonsResponse,
    summary="Signed summon contracts + canonical catalog",
)
def get_summons(save_id: str) -> SummonsResponse:
    """List already-signed contracts + those that can still be signed."""
    try:
        character, _, _ = save_module.load_save(save_id)
    except SaveNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc),
        ) from exc
    contracts = [
        SummonContractEntry(
            name=name,
            description=_summon_label(name) if name in CANONICAL_SUMMONS_IDS else None,
            description_fr=_summon_label(name) if name in CANONICAL_SUMMONS_IDS else None,
        )
        for name in character.summons
    ]
    available = [
        SummonContractEntry(
            name=cid,
            description=_summon_label(cid),
            description_fr=_summon_label(cid),
        )
        for cid in CANONICAL_SUMMONS_IDS
    ]
    return SummonsResponse(
        save_id=save_id,
        contracts=contracts,
        available_contracts=available,
    )


@router.post(
    "/summons/sign",
    response_model=SummonsResponse,
    summary="Sign a summon contract",
)
def sign_contract(save_id: str, payload: SignContractRequest) -> SummonsResponse:
    """Add the contract to the player's summons if it is canonical."""
    contract = payload.contract_name.strip().lower()
    if contract not in CANONICAL_SUMMONS_IDS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=t(
                "api.summons.unknown_contract",
                contract=contract,
                available=", ".join(CANONICAL_SUMMONS_IDS),
            ),
        )
    try:
        character, _, _ = save_module.load_save(save_id)
    except SaveNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc),
        ) from exc
    if contract not in character.summons:
        new_summons = [*character.summons, contract]
        new_char = character.model_copy(update={"summons": new_summons})
        _persist_character(save_id, new_char)
        character = new_char
    return SummonsResponse(
        save_id=save_id,
        contracts=[
            SummonContractEntry(
                name=n,
                description=_summon_label(n) if n in CANONICAL_SUMMONS_IDS else None,
                description_fr=_summon_label(n) if n in CANONICAL_SUMMONS_IDS else None,
            )
            for n in character.summons
        ],
        available_contracts=[
            SummonContractEntry(
                name=cid,
                description=_summon_label(cid),
                description_fr=_summon_label(cid),
            )
            for cid in CANONICAL_SUMMONS_IDS
        ],
    )


@router.post(
    "/summons/invoke",
    response_model=InvokeResponse,
    summary="Summon a creature (consumes 30 chakra)",
)
def invoke(save_id: str, payload: InvokeRequest) -> InvokeResponse:
    """Attempt a summoning. Consumes 30 chakra. Tier based on ninjutsu + chakra_control."""
    contract = payload.contract_name.strip().lower()
    try:
        character, _, _ = save_module.load_save(save_id)
    except SaveNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc),
        ) from exc
    if contract not in character.summons:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=t("api.inventory.contract_not_signed", contract=contract),
        )
    if character.chakra.current < 30:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=t(
                "api.inventory.chakra_insufficient",
                current=character.chakra.current,
            ),
        )
    new_chakra = character.chakra.model_copy(
        update={"current": character.chakra.current - 30},
    )
    new_char = character.with_chakra(new_chakra)
    skill = (character.stats.ninjutsu + character.extended_stats.chakra_control) / 2
    if skill < 1.5:
        tier = "failed"
        msg = "Ton invocation rate. Le chakra se dissipe."
        success = False
    elif skill < 3.0:
        tier = "minor"
        msg = (
            f"Une petite creature de la lignee des {contract} apparait. "
            "Modeste mais fidele."
        )
        success = True
    else:
        tier = "major"
        msg = (
            f"Une creature majeure de la lignee des {contract} apparait dans "
            "un nuage de fumee."
        )
        success = True
    _persist_character(save_id, new_char)
    return InvokeResponse(
        contract_name=contract,
        success=success,
        tier=tier,
        message_fr=msg,
        chakra_after=new_char.chakra.current,
    )
