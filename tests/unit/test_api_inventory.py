"""Phase 9 : tests routes /inventory (view/buy/sell/use) + /shop."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch) -> TestClient:
    from shinobi.config import settings

    monkeypatch.setattr(settings, "saves_path", str(tmp_path))
    monkeypatch.setattr(
        type(settings), "saves_dir", property(lambda self: tmp_path),
    )
    from shinobi.api import app
    return TestClient(app)


@pytest.fixture()
def save_id(client: TestClient) -> str:
    r = client.post("/saves", json={"mode": "random", "name": "InvTest"})
    return r.json()["save_id"]


def _give_money(save_id: str, amount: int) -> None:
    """Helper : injecte des ryos en lisant/modifiant la save SQLite directement."""
    from shinobi.persistence import saves as save_module
    from shinobi.persistence.database import close, open_connection
    from shinobi.persistence.serialize import decode_payload, encode_payload
    from shinobi.engine.character import Character

    state_path = save_module._state_path(save_id)
    conn = open_connection(state_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT payload FROM character WHERE is_current = 1 ORDER BY id DESC LIMIT 1",
        )
        row = cur.fetchone()
        char = decode_payload(row[0] if isinstance(row[0], bytes) else bytes(row[0], "utf-8"), Character)
        new_char = char.model_copy(update={"money": amount})
        cur.execute(
            "UPDATE character SET payload = ? WHERE is_current = 1",
            (encode_payload(new_char),),
        )
        conn.commit()
    finally:
        close(conn)


def test_inventory_empty(client: TestClient, save_id: str) -> None:
    """Inventaire de depart vide pour un new save."""
    r = client.get(f"/play/{save_id}/inventory")
    assert r.status_code == 200
    body = r.json()
    assert body["save_id"] == save_id
    assert body["money_ryos"] == 0
    assert body["items"] == []


def test_shop_lists_village_items(client: TestClient, save_id: str) -> None:
    """GET /shop liste les items du village."""
    r = client.get(f"/play/{save_id}/shop")
    assert r.status_code == 200
    body = r.json()
    assert body["village_id"] == "konohagakure"
    assert len(body["items"]) > 0
    for item in body["items"]:
        assert item["price_ryos"] > 0
        assert item["category"] in (
            "weapon", "consumable", "scroll", "tool", "clothing",
        )


def test_buy_with_no_money_returns_409(client: TestClient, save_id: str) -> None:
    """Achat refuse si money insuffisant."""
    r = client.post(
        f"/play/{save_id}/shop/buy",
        json={"item_id": "kunai"},
    )
    assert r.status_code == 409


def test_buy_with_money_succeeds(client: TestClient, save_id: str) -> None:
    """Achat reussi avec money suffisant."""
    _give_money(save_id, 5000)
    r = client.post(
        f"/play/{save_id}/shop/buy",
        json={"item_id": "kunai"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["item_id"] == "kunai"
    assert body["new_money"] < 5000
    inv = client.get(f"/play/{save_id}/inventory").json()
    assert any(it["item_id"] == "kunai" for it in inv["items"])


def test_buy_unknown_item_returns_404(client: TestClient, save_id: str) -> None:
    """Item inconnu -> 404."""
    _give_money(save_id, 5000)
    r = client.post(
        f"/play/{save_id}/shop/buy",
        json={"item_id": "non_existent_item_xyz"},
    )
    assert r.status_code == 404


def test_sell_after_buy(client: TestClient, save_id: str) -> None:
    """Vendre un item recupere une fraction du prix."""
    _give_money(save_id, 5000)
    client.post(f"/play/{save_id}/shop/buy", json={"item_id": "kunai"})
    inv_before = client.get(f"/play/{save_id}/inventory").json()
    money_before = inv_before["money_ryos"]
    r = client.post(
        f"/play/{save_id}/shop/sell",
        json={"item_id": "kunai"},
    )
    assert r.status_code == 200
    assert r.json()["new_money"] > money_before


def test_sell_item_not_owned_returns_409(client: TestClient, save_id: str) -> None:
    """Vendre un item non possede -> 409."""
    r = client.post(
        f"/play/{save_id}/shop/sell",
        json={"item_id": "kunai"},
    )
    assert r.status_code == 409


def test_use_item_not_owned_returns_409(client: TestClient, save_id: str) -> None:
    """Utiliser un item absent -> 409."""
    r = client.post(
        f"/play/{save_id}/inventory/use",
        json={"item_id": "soldier_pill"},
    )
    assert r.status_code == 409


def test_inventory_unknown_save_404(client: TestClient) -> None:
    """Save inconnue -> 404."""
    r = client.get("/play/unknown_xyz/inventory")
    assert r.status_code == 404
