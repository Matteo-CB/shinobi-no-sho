"""Phase 9 : tests routes /weapons + /summons + /summons/sign + /summons/invoke."""
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
    r = client.post("/saves", json={"mode": "random", "name": "SummonTest"})
    return r.json()["save_id"]


# === /weapons ============================================================


def test_weapons_empty_initially(client: TestClient, save_id: str) -> None:
    """Pas d'armes sur un new save."""
    r = client.get(f"/play/{save_id}/weapons")
    assert r.status_code == 200
    body = r.json()
    assert body["save_id"] == save_id
    assert body["count"] == 0
    assert body["weapons"] == []


def test_weapons_unknown_save_404(client: TestClient) -> None:
    r = client.get("/play/unknown/weapons")
    assert r.status_code == 404


# === /summons ============================================================


def test_summons_lists_canonical_contracts(
    client: TestClient, save_id: str,
) -> None:
    """GET /summons retourne les contrats signables canoniques."""
    r = client.get(f"/play/{save_id}/summons")
    assert r.status_code == 200
    body = r.json()
    assert body["contracts"] == []
    available_names = {c["name"] for c in body["available_contracts"]}
    for canon in ("toad", "snake", "slug", "hawk", "monkey", "ninken"):
        assert canon in available_names


def test_sign_unknown_contract_returns_404(
    client: TestClient, save_id: str,
) -> None:
    r = client.post(
        f"/play/{save_id}/summons/sign",
        json={"contract_name": "imaginary_xyz"},
    )
    assert r.status_code == 404


def test_sign_canonical_contract_persists(
    client: TestClient, save_id: str,
) -> None:
    """Sign toad ajoute aux contracts du joueur."""
    r = client.post(
        f"/play/{save_id}/summons/sign", json={"contract_name": "toad"},
    )
    assert r.status_code == 200
    body = r.json()
    contract_names = {c["name"] for c in body["contracts"]}
    assert "toad" in contract_names
    # Verify persistance
    r2 = client.get(f"/play/{save_id}/summons")
    contracts2 = {c["name"] for c in r2.json()["contracts"]}
    assert "toad" in contracts2


def test_sign_idempotent(client: TestClient, save_id: str) -> None:
    """Signer 2x le meme contrat ne duplique pas."""
    client.post(
        f"/play/{save_id}/summons/sign", json={"contract_name": "toad"},
    )
    client.post(
        f"/play/{save_id}/summons/sign", json={"contract_name": "toad"},
    )
    r = client.get(f"/play/{save_id}/summons")
    contracts = [c["name"] for c in r.json()["contracts"]]
    assert contracts.count("toad") == 1


# === /summons/invoke =====================================================


def test_invoke_without_contract_returns_409(
    client: TestClient, save_id: str,
) -> None:
    """Invoquer sans avoir signe -> 409."""
    r = client.post(
        f"/play/{save_id}/summons/invoke", json={"contract_name": "toad"},
    )
    assert r.status_code == 409


def test_invoke_low_chakra_returns_409(client: TestClient, save_id: str) -> None:
    """Pas assez de chakra -> 409."""
    # Default chakra = 100, drop a 10 via direct DB
    from shinobi.persistence import saves as save_module
    from shinobi.persistence.database import close, open_connection
    from shinobi.persistence.serialize import decode_payload, encode_payload
    from shinobi.engine.character import Character

    state = save_module._state_path(save_id)
    conn = open_connection(state)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT payload FROM character WHERE is_current = 1 ORDER BY id DESC LIMIT 1",
        )
        row = cur.fetchone()
        char = decode_payload(
            row[0] if isinstance(row[0], bytes) else bytes(row[0], "utf-8"),
            Character,
        )
        new_chakra = char.chakra.model_copy(update={"current": 10})
        new_char = char.with_chakra(new_chakra).model_copy(
            update={"summons": ["toad"]},
        )
        cur.execute(
            "UPDATE character SET payload = ? WHERE is_current = 1",
            (encode_payload(new_char),),
        )
        conn.commit()
    finally:
        close(conn)
    r = client.post(
        f"/play/{save_id}/summons/invoke", json={"contract_name": "toad"},
    )
    assert r.status_code == 409
    assert "chakra" in r.json()["detail"].lower()


def test_invoke_consumes_chakra(client: TestClient, save_id: str) -> None:
    """Invoquer reussi consomme 30 chakra."""
    client.post(
        f"/play/{save_id}/summons/sign", json={"contract_name": "toad"},
    )
    before = client.get(f"/play/{save_id}/status").json()["chakra_current"]
    r = client.post(
        f"/play/{save_id}/summons/invoke", json={"contract_name": "toad"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["tier"] in ("minor", "major", "failed")
    assert body["chakra_after"] == before - 30
