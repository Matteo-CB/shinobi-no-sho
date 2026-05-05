"""Pass 6 phase A : extraction des enums canon depuis data/canonical/.

Pure lecture, zero appel LLM. Construit les listes d'enums utilisees par :
- la generation contrainte (pilier 6 phase B) : Outlines / XGrammar ne
  laissera generer que des ids appartenant a ces listes
- le triplet_check (couche B du validator) : verifier que (actor, jutsu)
  est canon en consultant `jutsu_canonical_users`

Sortie sous data/canon/ (distinct de data/canonical/ qui est la source) :
- character_list.json    : 1360 ids + meta minimale
- jutsu_list.json        : 3025 ids + canonical_users dedup
- location_list.json     : 154 ids (sub-locations dans les villages)
- village_list.json      : 40 ids (Konohagakure, Sunagakure, etc.)
- clan_list.json         : 52 ids + key_*/available_*
- kekkei_genkai_list.json: 32 ids + eligible_clans (carrier_clans cleane)
- nature_list.json       : 18 ids
- enums_summary.json     : meta global (counts + integrity flags)

Integrity checks :
- chaque user dans canonical_users doit exister dans character_list
- chaque carrier dans kg.carrier_clans doit exister dans clan_list
- chaque clan dans nature.common_clans doit exister dans clan_list

Usage :
    uv run python scripts/pass6_extract_enums.py            # dry-run
    uv run python scripts/pass6_extract_enums.py --apply    # ecrit
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "data" / "canonical"
OUT_DIR = ROOT / "data" / "canon"


def load(name: str) -> list[dict] | dict:
    path = SRC_DIR / name
    return json.loads(path.read_text(encoding="utf-8"))


def extract_character_list(chars: list[dict]) -> list[dict]:
    out = []
    for c in chars:
        out.append({
            "id": c["id"],
            "name_romaji": c.get("name_romaji"),
            "clan": c.get("clan"),
            "village_of_origin": c.get("village_of_origin"),
            "birth_year": c.get("birth_year"),
            "canonicity": c.get("canonicity"),
        })
    return out


def extract_jutsu_list(techs: list[dict]) -> list[dict]:
    out = []
    for t in techs:
        users = t.get("canonical_users") or []
        seen: set[str] = set()
        users_dedup: list[str] = []
        for u in users:
            if isinstance(u, str) and u not in seen:
                seen.add(u)
                users_dedup.append(u)
        out.append({
            "id": t["id"],
            "name_romaji": t.get("name_romaji"),
            "category": t.get("category"),
            "rank": t.get("rank"),
            "natures": t.get("natures") or [],
            "canonical_users": users_dedup,
        })
    return out


def extract_location_list(locs: list[dict]) -> list[dict]:
    out = []
    for loc in locs:
        out.append({
            "id": loc["id"],
            "name_fr": loc.get("name_fr"),
            "country": loc.get("country"),
            "canonicity": loc.get("canonicity"),
        })
    return out


def extract_village_list(villages: list[dict]) -> list[dict]:
    out = []
    for v in villages:
        out.append({
            "id": v["id"],
            "name_fr": v.get("name_fr"),
            "country": v.get("country"),
            "main_clans": v.get("main_clans") or [],
            "canonicity": v.get("canonicity"),
        })
    return out


def extract_clan_list(clans: list[dict]) -> list[dict]:
    out = []
    for c in clans:
        out.append({
            "id": c["id"],
            "key_kekkei_genkai": c.get("key_kekkei_genkai") or [],
            "available_kekkei_genkai": c.get("available_kekkei_genkai") or [],
            "key_natures": c.get("key_natures") or [],
            "available_natures": c.get("available_natures") or [],
            "key_techniques": c.get("key_techniques") or [],
            "available_techniques": c.get("available_techniques") or [],
            "canonicity": c.get("canonicity"),
        })
    return out


def extract_kekkei_genkai_list(kgs: list[dict]) -> list[dict]:
    out = []
    for kg in kgs:
        out.append({
            "id": kg["id"],
            "name_romaji": kg.get("name_romaji"),
            "type": kg.get("type"),
            "category": kg.get("category"),
            "eligible_clans": kg.get("carrier_clans") or [],
            "canonicity": kg.get("canonicity"),
        })
    return out


def extract_nature_list(natures: list[dict]) -> list[dict]:
    out = []
    for n in natures:
        out.append({
            "id": n["id"],
            "common_clans": n.get("common_clans") or [],
            "common_villages": n.get("common_villages") or [],
        })
    return out


def integrity_check(
    *,
    character_list: list[dict],
    jutsu_list: list[dict],
    clan_list: list[dict],
    kekkei_genkai_list: list[dict],
    nature_list: list[dict],
) -> dict:
    char_ids = {c["id"] for c in character_list}
    clan_ids = {c["id"] for c in clan_list}
    kg_ids = {k["id"] for k in kekkei_genkai_list}

    flags = {
        "jutsu_users_unknown": [],
        "kg_carriers_unknown": [],
        "nature_clans_unknown": [],
        "clan_key_kgs_unknown": [],
        "clan_available_kgs_unknown": [],
    }

    for j in jutsu_list:
        for u in j["canonical_users"]:
            if u not in char_ids:
                flags["jutsu_users_unknown"].append({"jutsu": j["id"], "user": u})

    for kg in kekkei_genkai_list:
        for c in kg["eligible_clans"]:
            if c not in clan_ids:
                flags["kg_carriers_unknown"].append({"kg": kg["id"], "clan": c})

    for n in nature_list:
        for c in n["common_clans"]:
            if c not in clan_ids:
                flags["nature_clans_unknown"].append({"nature": n["id"], "clan": c})

    for clan in clan_list:
        for kg_id in clan["key_kekkei_genkai"]:
            if kg_id not in kg_ids:
                flags["clan_key_kgs_unknown"].append({"clan": clan["id"], "kg": kg_id})
        for kg_id in clan["available_kekkei_genkai"]:
            if kg_id not in kg_ids:
                flags["clan_available_kgs_unknown"].append({"clan": clan["id"], "kg": kg_id})

    return flags


def build_enums_summary(
    *, character_list, jutsu_list, location_list, village_list,
    clan_list, kekkei_genkai_list, nature_list, integrity,
) -> dict:
    return {
        "counts": {
            "characters": len(character_list),
            "jutsus": len(jutsu_list),
            "locations": len(location_list),
            "villages": len(village_list),
            "clans": len(clan_list),
            "kekkei_genkai": len(kekkei_genkai_list),
            "natures": len(nature_list),
        },
        "integrity": {
            k: len(v) for k, v in integrity.items()
        },
        "integrity_samples": {
            k: v[:5] for k, v in integrity.items()
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Persist sous data/canon/. Sans, dry-run console only.")
    args = parser.parse_args()

    print("Loading canonical sources...")
    chars = load("characters.json")
    techs = load("techniques.json")
    locs = load("locations.json")
    villages = load("villages.json")
    clans = load("clans.json")
    kgs = load("kekkei_genkai.json")
    natures = load("natures.json")
    print(f"  chars={len(chars)} techs={len(techs)} locs={len(locs)} "
          f"villages={len(villages)} clans={len(clans)} "
          f"kgs={len(kgs)} natures={len(natures)}")

    print("Extracting enums...")
    character_list = extract_character_list(chars)
    jutsu_list = extract_jutsu_list(techs)
    location_list = extract_location_list(locs)
    village_list = extract_village_list(villages)
    clan_list = extract_clan_list(clans)
    kekkei_genkai_list = extract_kekkei_genkai_list(kgs)
    nature_list = extract_nature_list(natures)

    print("Running integrity checks...")
    integrity = integrity_check(
        character_list=character_list, jutsu_list=jutsu_list,
        clan_list=clan_list, kekkei_genkai_list=kekkei_genkai_list,
        nature_list=nature_list,
    )
    summary = build_enums_summary(
        character_list=character_list, jutsu_list=jutsu_list,
        location_list=location_list, village_list=village_list,
        clan_list=clan_list, kekkei_genkai_list=kekkei_genkai_list,
        nature_list=nature_list, integrity=integrity,
    )

    print("\n=== Summary ===")
    print(f"  Counts : {summary['counts']}")
    print(f"  Integrity (corruption candidates) :")
    for k, n in summary["integrity"].items():
        print(f"    {k}: {n}")

    if not args.apply:
        print("\n>>> DRY-RUN. Re-run with --apply to persist.")
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pairs = [
        ("character_list.json", character_list),
        ("jutsu_list.json", jutsu_list),
        ("location_list.json", location_list),
        ("village_list.json", village_list),
        ("clan_list.json", clan_list),
        ("kekkei_genkai_list.json", kekkei_genkai_list),
        ("nature_list.json", nature_list),
        ("enums_summary.json", summary),
    ]
    for name, payload in pairs:
        (OUT_DIR / name).write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"  wrote data/canon/{name}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
