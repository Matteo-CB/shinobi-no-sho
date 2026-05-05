"""Tests des enums canon extraits par scripts/pass6_extract_enums.py.

Verifie :
- les fichiers data/canon/*.json existent et sont parsables
- les counts sont coherents avec data/canonical/
- l'integrite cross-fichiers : tout user dans canonical_users existe,
  tout clan dans carrier_clans existe, etc.
- les ids sont uniques par fichier
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CANON_DIR = ROOT / "data" / "canon"


@pytest.fixture(scope="module")
def enums() -> dict:
    files = ["character_list", "jutsu_list", "location_list",
             "clan_list", "kekkei_genkai_list", "nature_list", "enums_summary"]
    out = {}
    for name in files:
        p = CANON_DIR / f"{name}.json"
        if not p.exists():
            pytest.skip(f"data/canon/{name}.json missing — run pass6_extract_enums.py --apply")
        out[name] = json.loads(p.read_text(encoding="utf-8"))
    return out


def test_files_exist_and_parsable(enums: dict) -> None:
    assert isinstance(enums["character_list"], list)
    assert isinstance(enums["jutsu_list"], list)
    assert isinstance(enums["location_list"], list)
    assert isinstance(enums["clan_list"], list)
    assert isinstance(enums["kekkei_genkai_list"], list)
    assert isinstance(enums["nature_list"], list)
    assert isinstance(enums["enums_summary"], dict)


def test_counts_match_summary(enums: dict) -> None:
    counts = enums["enums_summary"]["counts"]
    assert counts["characters"] == len(enums["character_list"])
    assert counts["jutsus"] == len(enums["jutsu_list"])
    assert counts["locations"] == len(enums["location_list"])
    assert counts["clans"] == len(enums["clan_list"])
    assert counts["kekkei_genkai"] == len(enums["kekkei_genkai_list"])
    assert counts["natures"] == len(enums["nature_list"])


def test_ids_unique_per_file(enums: dict) -> None:
    for name in ["character_list", "jutsu_list", "location_list",
                 "clan_list", "kekkei_genkai_list", "nature_list"]:
        ids = [e["id"] for e in enums[name]]
        assert len(ids) == len(set(ids)), f"{name} contient des ids dupliques"


def test_jutsu_users_all_exist_in_character_list(enums: dict) -> None:
    char_ids = {c["id"] for c in enums["character_list"]}
    orphans = []
    for j in enums["jutsu_list"]:
        for u in j["canonical_users"]:
            if u not in char_ids:
                orphans.append((j["id"], u))
    assert orphans == [], f"{len(orphans)} canonical_users orphelins, ex: {orphans[:3]}"


def test_kekkei_genkai_carriers_all_exist_in_clan_list(enums: dict) -> None:
    clan_ids = {c["id"] for c in enums["clan_list"]}
    orphans = []
    for kg in enums["kekkei_genkai_list"]:
        for c in kg["eligible_clans"]:
            if c not in clan_ids:
                orphans.append((kg["id"], c))
    assert orphans == [], f"{len(orphans)} clan carriers orphelins"


def test_clan_key_kgs_all_exist_in_kg_list(enums: dict) -> None:
    kg_ids = {k["id"] for k in enums["kekkei_genkai_list"]}
    orphans = []
    for clan in enums["clan_list"]:
        for kg_id in clan["key_kekkei_genkai"]:
            if kg_id not in kg_ids:
                orphans.append((clan["id"], kg_id))
        for kg_id in clan["available_kekkei_genkai"]:
            if kg_id not in kg_ids:
                orphans.append((clan["id"], kg_id))
    assert orphans == [], f"{len(orphans)} kg refs orphelines : {orphans[:3]}"


def test_jutsu_canonical_users_dedup(enums: dict) -> None:
    """Les canonical_users ne doivent pas avoir de doublons (rasengan en avait 16)."""
    for j in enums["jutsu_list"]:
        users = j["canonical_users"]
        assert len(users) == len(set(users)), \
            f"{j['id']} a des canonical_users dupliques"


def test_known_canon_attestations(enums: dict) -> None:
    """Sanity check : quelques cas canon classiques doivent etre presents."""
    jutsus_by_id = {j["id"]: j for j in enums["jutsu_list"]}
    assert "rasengan" in jutsus_by_id
    rasengan = jutsus_by_id["rasengan"]
    assert "uzumaki_naruto" in rasengan["canonical_users"]
    assert "namikaze_minato" in rasengan["canonical_users"]
    assert "jiraiya" in rasengan["canonical_users"]

    clans_by_id = {c["id"]: c for c in enums["clan_list"]}
    assert "uchiha" in clans_by_id
    assert "sharingan" in clans_by_id["uchiha"]["key_kekkei_genkai"]
    assert "byakugan" in clans_by_id["hyuga"]["key_kekkei_genkai"]


def test_summary_integrity_section_presence(enums: dict) -> None:
    integrity = enums["enums_summary"]["integrity"]
    assert "jutsu_users_unknown" in integrity
    assert integrity["jutsu_users_unknown"] == 0, \
        "Aucun user de jutsu ne doit etre inconnu apres apply (verifier les sources)"
