"""Producteur des datasets canoniques depuis les wikitext parses.

Lit data/raw/narutopedia/parsed/<type>/*.json et produit data/canonical/*.json
en respectant les schemas pydantic.

Approche :
- Charge tous les parsed JSON par type d'entite
- Resout les references croisees via un index nom_canonique -> slug_id
- Mappe les params d'infobox vers les champs canoniques avec defaults raisonnables
- Filtre par profil de canonicite (defaut : tout, l'utilisateur ajustera plus tard)
- Ecrit les fichiers data/canonical/*.json avec sort_keys=True

Usage :
  python scripts/build_canonical_jsons.py
  python scripts/build_canonical_jsons.py --types characters,techniques
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import typer

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from shinobi.canon.models import (  # noqa: E402
    Character,
    Clan,
    KekkeiGenkai,
    Location,
    Technique,
    Village,
    WeaponTool,
)
from shinobi.config import settings  # noqa: E402
from shinobi.logging_setup import configure_logging, get_logger  # noqa: E402
from shinobi.types import Canonicity, Gender, TechniqueCategory, TechniqueRank  # noqa: E402
from shinobi.utils.slug import slug_character, slug_technique, slugify  # noqa: E402

configure_logging()
logger = get_logger("build_canonical")

cli = typer.Typer(add_completion=False, no_args_is_help=False)


# Helpers communs --------------------------------------------------------------


def _list_param(value: str | None) -> list[str]:
    """Decoupe un champ d'infobox en liste."""
    if not value:
        return []
    cleaned = re.sub(r"<[^>]+>", "\n", value)
    cleaned = re.sub(r"\*\s*", "\n", cleaned)
    cleaned = cleaned.replace(",", "\n")
    items = []
    for line in cleaned.split("\n"):
        line = re.sub(r"\[\[([^|\]]+)(?:\|[^\]]+)?\]\]", r"\1", line)
        line = re.sub(r"'+", "", line)
        line = line.strip(" \t.;:|()[]")
        if line:
            items.append(line)
    return items


def _first_link(value: str | None) -> str | None:
    if not value:
        return None
    m = re.search(r"\[\[([^|\]]+)", value)
    if m:
        return m.group(1).strip()
    cleaned = re.sub(r"<[^>]+>", "", value).strip()
    return cleaned.split(",")[0].strip() if cleaned else None


def _int(value: str | None) -> int | None:
    if not value:
        return None
    m = re.search(r"-?\d+", value)
    return int(m.group(0)) if m else None


def _gender(value: str | None) -> Gender:
    if not value:
        return Gender.male
    v = value.lower()
    if "female" in v or "femme" in v:
        return Gender.female
    if "non" in v or "agender" in v:
        return Gender.non_binary
    return Gender.male


def _technique_category(value: str | None) -> TechniqueCategory:
    if not value:
        return TechniqueCategory.ninjutsu
    v = value.lower()
    for cat in TechniqueCategory:
        if cat.value in v:
            return cat
    if "tai" in v:
        return TechniqueCategory.taijutsu
    if "gen" in v:
        return TechniqueCategory.genjutsu
    return TechniqueCategory.ninjutsu


def _technique_rank(value: str | None) -> TechniqueRank:
    if not value:
        return TechniqueRank.c
    v = value.upper().strip()
    for r in (
        TechniqueRank.s,
        TechniqueRank.a,
        TechniqueRank.b,
        TechniqueRank.c,
        TechniqueRank.d,
        TechniqueRank.e,
    ):
        if v.startswith(r.value):
            return r
    if "forbidden" in value.lower() or "kinjutsu" in value.lower():
        return TechniqueRank.forbidden
    return TechniqueRank.c


def _slug_from_links(value: str | None, transform=slugify) -> list[str]:
    """Extrait les ids slugifies depuis un champ contenant des wiki-links."""
    if not value:
        return []
    out = []
    for m in re.finditer(r"\[\[([^|\]]+)(?:\|[^\]]+)?\]\]", value):
        target = m.group(1).strip()
        if target.startswith("Category:") or target.startswith("File:"):
            continue
        out.append(transform(target))
    if not out:
        for token in _list_param(value):
            out.append(transform(token))
    return out


def _template_params(parsed: dict, names: list[str]) -> dict[str, str]:
    """Retourne les params du premier template dont le nom correspond."""
    lower = {n.lower() for n in names}
    for tpl in parsed.get("templates", []):
        if tpl.get("name", "").lower().strip() in lower:
            return {k.lower(): v for k, v in tpl.get("params", {}).items()}
    return {}


def _intro_text(parsed: dict, max_chars: int = 600) -> str:
    """Retourne l'intro de prose."""
    sections = parsed.get("sections", [])
    if not sections:
        return ""
    intro = sections[0].get("text", "")[:max_chars].strip()
    return intro


# Mappers par entite -----------------------------------------------------------


_VILLAGE_KEYWORDS = {
    "konohagakure": "konohagakure",
    "konoha": "konohagakure",
    "sunagakure": "sunagakure",
    "suna": "sunagakure",
    "kirigakure": "kirigakure",
    "kiri": "kirigakure",
    "kumogakure": "kumogakure",
    "kumo": "kumogakure",
    "iwagakure": "iwagakure",
    "iwa": "iwagakure",
    "amegakure": "amegakure",
    "ame": "amegakure",
    "otogakure": "otogakure",
    "oto": "otogakure",
    "takigakure": "takigakure",
    "kusagakure": "kusagakure",
    "yugakure": "yugakure",
    "hoshigakure": "hoshigakure",
    "yukigakure": "yukigakure",
    "shimogakure": "shimogakure",
    "tanigakure": "tanigakure",
    "getsugakure": "getsugakure",
}


def _detect_village(text: str) -> str | None:
    """Detecte le village d'origine dans la prose."""
    lower = text.lower()
    for kw, slug in _VILLAGE_KEYWORDS.items():
        if kw in lower:
            return slug
    return None


def _detect_links_to_techniques(parsed: dict) -> list[str]:
    """Slug des wiki-links pointant vers des techniques (heuristique)."""
    out = []
    for link in parsed.get("wiki_links", []):
        lower = link.lower()
        if "technique" in lower or "jutsu" in lower or "release" in lower:
            out.append(slug_technique(link))
    return out


def _detect_clan(parsed: dict) -> str | None:
    for link in parsed.get("wiki_links", []):
        if " clan" in link.lower():
            name = link.lower().replace(" clan", "").strip()
            return slugify(name)
    return None


def map_character(parsed: dict) -> dict[str, Any] | None:
    title = parsed.get("title", "").strip()
    if not title or title.startswith("List of") or title.startswith("Category:"):
        return None
    if "(" in title:  # disambiguations / pages techniques
        return None
    params = _template_params(
        parsed, ["Character", "Infobox character", "Infobox/Character", "Infobox"]
    )
    parts = title.split(maxsplit=1)
    if len(parts) == 2:
        given, family = parts
    else:
        family, given = "", title
    char_id = slug_character(family if family else None, given)
    if not char_id:
        return None
    intro = _intro_text(parsed, max_chars=1200)
    village_slug = (
        slugify(_first_link(params.get("affiliation")) or "")
        or _detect_village(intro)
        or "konohagakure"
    )
    sex = _gender(params.get("sex") or params.get("gender"))
    birth_year = _int(params.get("birthdate"))
    natures = _slug_from_links(
        params.get("nature type") or params.get("nature_type") or params.get("nature")
    )
    kekkei_genkai = _slug_from_links(params.get("kekkei genkai") or params.get("kekkei_genkai"))
    techniques = _slug_from_links(params.get("jutsu"), transform=slug_technique)
    if not techniques:
        techniques = _detect_links_to_techniques(parsed)[:30]
    clan = _detect_clan(parsed)
    return {
        "canonicity": str(Canonicity.manga),
        "id": char_id,
        "clan": clan,
        "kekkei_genkai": kekkei_genkai,
        "name_kanji": params.get("japanese"),
        "name_romaji": title,
        "natures": natures,
        "personality_fr": intro,
        "sources": [f"narutopedia:{title.replace(' ', '_')}"],
        "techniques_known_by_era": (
            [{"year": (birth_year or 0) + 18, "techniques": techniques}] if techniques else []
        ),
        "updated_at": "2026-05-02",
        "village_of_origin": village_slug,
        "gender": sex.value,
        "birth_year": birth_year,
        "current_village_by_era": [],
    }


def map_technique(parsed: dict) -> dict[str, Any] | None:
    title = parsed.get("title", "").strip()
    if not title:
        return None
    params = _template_params(parsed, ["Jutsu", "Infobox jutsu", "Infobox/Jutsu", "Infobox"])
    if not params:
        return None
    tech_id = slug_technique(title)
    name_romaji = params.get("unnamed") or title
    rank = _technique_rank(params.get("rank") or params.get("classification"))
    cat = _technique_category(params.get("classification") or params.get("type"))
    natures = _slug_from_links(params.get("nature") or params.get("element"))
    users = _slug_from_links(params.get("users"))
    return {
        "canonicity": str(Canonicity.manga),
        "category": cat.value,
        "canonical_users": [
            slug_character(*u.rsplit(" ", 1)[::-1]) if " " in u else u for u in users
        ],
        "description_fr": _intro_text(parsed),
        "id": tech_id,
        "name_fr": title,
        "name_kanji": params.get("japanese"),
        "name_romaji": name_romaji,
        "natures": natures,
        "rank": rank.value,
        "sources": [f"narutopedia:{title.replace(' ', '_')}"],
        "updated_at": "2026-05-02",
    }


def map_clan(parsed: dict) -> dict[str, Any] | None:
    title = parsed.get("title", "").strip()
    if not title:
        return None
    params = _template_params(parsed, ["Clan", "Infobox clan", "Infobox/Clan", "Infobox"])
    name = title.replace(" Clan", "").replace(" clan", "").strip()
    clan_id = slugify(name)
    if not clan_id:
        return None
    village = _first_link(params.get("affiliation")) or ""
    return {
        "canonicity": str(Canonicity.manga),
        "history_summary_fr": _intro_text(parsed),
        "id": clan_id,
        "key_kekkei_genkai": _slug_from_links(
            params.get("kekkei genkai") or params.get("kekkei_genkai")
        ),
        "key_natures": _slug_from_links(params.get("nature")),
        "key_techniques": _slug_from_links(params.get("jutsu"), transform=slug_technique),
        "name_kanji": params.get("japanese"),
        "name_romaji": name,
        "sources": [f"narutopedia:{title.replace(' ', '_')}"],
        "updated_at": "2026-05-02",
        "village_of_origin": slugify(village) if village else None,
    }


def map_village(parsed: dict) -> dict[str, Any] | None:
    title = parsed.get("title", "").strip()
    if not title:
        return None
    params = _template_params(
        parsed, ["Country", "Village", "Infobox country", "Infobox village", "Infobox"]
    )
    name = title.strip()
    village_id = slugify(name)
    if not village_id:
        return None
    country = params.get("country", title)
    return {
        "canonicity": str(Canonicity.manga),
        "country": slugify(country) or village_id,
        "country_name_fr": country or name,
        "geography_fr": _intro_text(parsed),
        "id": village_id,
        "main_clans": _slug_from_links(params.get("clans")),
        "name_fr": name,
        "name_kanji": params.get("japanese"),
        "name_romaji": params.get("unnamed") or name,
        "sources": [f"narutopedia:{title.replace(' ', '_')}"],
        "specialties": [],
        "updated_at": "2026-05-02",
    }


def map_kekkei_genkai(parsed: dict) -> dict[str, Any] | None:
    title = parsed.get("title", "").strip()
    if not title:
        return None
    params = _template_params(
        parsed, ["Kekkei", "Kekkei Genkai", "Infobox kekkei genkai", "Infobox"]
    )
    kek_id = slugify(title)
    if not kek_id:
        return None
    return {
        "activation_conditions_fr": _intro_text(parsed),
        "canonicity": str(Canonicity.manga),
        "carrier_clans": _slug_from_links(params.get("clans") or params.get("clan")),
        "category": "kekkei_genkai",
        "id": kek_id,
        "name_kanji": params.get("japanese"),
        "name_romaji": title,
        "sources": [f"narutopedia:{title.replace(' ', '_')}"],
        "type": "dojutsu"
        if "dojutsu" in title.lower() or "gan" in title.lower()
        else "non_elemental",
        "updated_at": "2026-05-02",
    }


def map_location(parsed: dict) -> dict[str, Any] | None:
    title = parsed.get("title", "").strip()
    if not title:
        return None
    params = _template_params(
        parsed, ["Location", "Infobox location", "Infobox/Location", "Infobox"]
    )
    loc_id = slugify(title)
    return {
        "canonicity": str(Canonicity.manga),
        "country": slugify(_first_link(params.get("country")) or ""),
        "geography_fr": _intro_text(parsed),
        "id": loc_id,
        "name_fr": title,
        "name_romaji": title,
        "near_village": slugify(_first_link(params.get("affiliation")) or ""),
        "sources": [f"narutopedia:{title.replace(' ', '_')}"],
        "updated_at": "2026-05-02",
    }


def map_weapon(parsed: dict) -> dict[str, Any] | None:
    title = parsed.get("title", "").strip()
    if not title:
        return None
    params = _template_params(
        parsed, ["Tool", "Weapon", "Infobox tool", "Infobox weapon", "Infobox"]
    )
    weapon_id = slugify(title)
    return {
        "abilities_fr": _intro_text(parsed),
        "canonicity": str(Canonicity.manga),
        "id": weapon_id,
        "name_fr": title,
        "name_romaji": title,
        "rarity": "uncommon",
        "sources": [f"narutopedia:{title.replace(' ', '_')}"],
        "type": params.get("type") or "weapon",
        "updated_at": "2026-05-02",
        "wielders_canonical": _slug_from_links(params.get("users")),
    }


# CLI --------------------------------------------------------------------------


@cli.command()
def build(
    types: str = typer.Option("all", help="CSV des types a produire."),
    raw_dir: str = typer.Option("data/raw/narutopedia", help="Repertoire raw."),
) -> None:
    """Produit les datasets canoniques."""
    base = (
        (settings.canonical_data_dir.parent / "raw" / "narutopedia")
        if raw_dir == "data/raw/narutopedia"
        else Path(raw_dir)
    )
    parsed_dir = base / "parsed"
    canon_dir = settings.canonical_data_dir
    canon_dir.mkdir(parents=True, exist_ok=True)

    accepted_types = None if types == "all" else {t.strip() for t in types.split(",")}

    counts: dict[str, int] = defaultdict(int)

    plans = [
        ("character", "characters.json", map_character, Character),
        ("technique", "techniques.json", map_technique, Technique),
        ("clan", "clans.json", map_clan, Clan),
        ("village", "villages.json", map_village, Village),
        ("kekkei_genkai", "kekkei_genkai.json", map_kekkei_genkai, KekkeiGenkai),
        ("location", "locations.json", map_location, Location),
        ("weapon_tool", "weapons_tools.json", map_weapon, WeaponTool),
    ]

    for entity_type, output_file, mapper, model_cls in plans:
        if accepted_types is not None and entity_type not in accepted_types:
            continue
        out_path = canon_dir / output_file
        in_dir = parsed_dir / entity_type
        if not in_dir.exists():
            logger.info("build_skip", entity_type=entity_type, reason="no_parsed_dir")
            continue
        rows: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        rejected_validation = 0
        for parsed_file in sorted(in_dir.glob("*.json")):
            try:
                parsed = json.loads(parsed_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            payload = mapper(parsed)
            if payload is None:
                continue
            oid = payload.get("id")
            if not oid or oid in seen_ids:
                continue
            try:
                model_cls.model_validate(payload)
            except Exception as exc:
                rejected_validation += 1
                logger.debug("build_reject", entity=oid, error=str(exc)[:120])
                continue
            seen_ids.add(oid)
            rows.append(payload)
        rows.sort(key=lambda d: d["id"])
        out_path.write_text(
            json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        counts[entity_type] = len(rows)
        if rejected_validation:
            logger.warning(
                "build_rejected_count",
                entity_type=entity_type,
                rejected=rejected_validation,
            )
        logger.info(
            "build_ok",
            entity_type=entity_type,
            count=len(rows),
            file=str(out_path),
        )

    print("Datasets produits :")
    for k, v in sorted(counts.items()):
        print(f"  {k}: {v}")


if __name__ == "__main__":
    cli()
