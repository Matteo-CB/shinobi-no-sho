"""Normalisation post-extraction Pass 2 : valeurs anglaises -> slugs canon.

Le modele Llama (et autres LLM) retourne souvent les noms anglais des
wikis ('Wood Release', 'Butsuma Senju', 'Sharingan') au lieu des slugs
canon snake_case ('mokuton', 'senju_butsuma', 'sharingan').

Ce module mappe deterministiquement (sans LLM) :
- KG / natures : 'Wood Release' -> 'mokuton', 'Fire Release' -> 'katon'
- Characters : 'Butsuma Senju' -> 'senju_butsuma'
- Clans / villages : 'Senju' -> 'senju', 'Konohagakure' -> 'konohagakure'

Construit les enums canon depuis :
- data/canonical/kekkei_genkai.json
- data/canonical/natures.json
- data/canonical/characters.json (incluant aliases)
- data/canonical/clans.json
- data/canonical/villages.json

Flag les valeurs qui ne matchent AUCUN enum comme `unknown_in_canon`
(potentielle hallucination ou perso filler non scrape).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CANONICAL_DIR = ROOT / "data" / "canonical"


# Mapping fixe anglais -> slug pour les natures basiques + Yin/Yang.
NATURE_ENGLISH_TO_SLUG: dict[str, str] = {
    "fire release": "katon",
    "fire": "katon",
    "katon": "katon",
    "water release": "suiton",
    "water": "suiton",
    "suiton": "suiton",
    "earth release": "doton",
    "earth": "doton",
    "doton": "doton",
    "wind release": "fuuton",
    "wind": "fuuton",
    "fuuton": "fuuton",
    "futon": "fuuton",
    "lightning release": "raiton",
    "lightning": "raiton",
    "raiton": "raiton",
    "yin release": "inton",
    "yin": "inton",
    "inton": "inton",
    "yang release": "youton_yang",
    "yang": "youton_yang",
    "youton_yang": "youton_yang",
}


# Mapping fixe anglais -> slug pour les KG.
# IDs canon : kekkei_genkai.json utilise les noms anglais snake_case
# pour les KG combinatoires (wood_release, lava_release) et le romaji
# pour les dojutsu (sharingan, byakugan, mangekyo_sharingan).
# Inconsistance avec natures.json qui utilise le romaji partout (mokuton, yoton).
# On normalise vers les IDs reels de kekkei_genkai.json.
KG_ENGLISH_TO_SLUG: dict[str, str] = {
    # KG combinatoires : ids canon en anglais snake_case
    "wood release": "wood_release",
    "ice release": "ice_release",
    "lava release": "lava_release",
    "scorch release": "scorch_release",
    "magnet release": "magnet_release",
    "explosion release": "explosion_release",
    "crystal release": "crystal_release",
    "storm release": "storm_release",
    "boil release": "boil_release",
    "swift release": "swift_release",
    "dust release": "swift_release",
    "dark release": "dark_release",
    "mud release": "mud_release",
    "steel release": "steel_release",
    "typhoon release": "typhoon_release",
    # Romaji -> ids anglais (au cas ou le LLM produit du romaji)
    "mokuton": "wood_release",
    "hyouton": "ice_release",
    "yoton": "lava_release",
    "youton": "lava_release",
    "jiton": "magnet_release",
    "bakuton": "explosion_release",
    "shouton": "crystal_release",
    "ranton": "storm_release",
    "futton": "boil_release",
    "jinton": "swift_release",
    "meiton": "dark_release",
    # Dojutsu : ids canon en romaji simple
    "sharingan": "sharingan",
    "mangekyo sharingan": "mangekyo_sharingan",
    "mangekyou sharingan": "mangekyo_sharingan",
    "mangekyo_sharingan": "mangekyo_sharingan",
    # NB: 'Eternal Mangekyo' n'est PAS distinct dans le canon JSON.
    # On le mappe au mangekyo standard. Le contexte (perso) le distingue.
    "eternal mangekyo sharingan": "mangekyo_sharingan",
    "eternal mangekyou sharingan": "mangekyo_sharingan",
    "rinnegan": "rinnegan",
    # NB: Rinne Sharingan absent du canon JSON. Mappe vers rinnegan ou inconnu.
    "rinne sharingan": "rinnegan",
    "byakugan": "byakugan",
    "tenseigan": "tenseigan",
    "jogan": "jogan",
    "jougan": "jogan",
    "ketsuryugan": "ketsuryugan",
    "ketsuryuugan": "ketsuryugan",
    "shikotsumyaku": "shikotsumyaku",
    "dead bone pulse": "shikotsumyaku",
    # Hydrification : pas dans kekkei_genkai.json (c'est un Hiden Hozuki).
    # On laisse 'hydrification' qui sera flag canonical=False.
    "hydrification": "hydrification",
    "hydrification technique": "hydrification",
}


def _strip_diacritics(text: str) -> str:
    """Decompose puis garde uniquement ASCII (utile pour ū vs u)."""
    import unicodedata as _ud
    n = _ud.normalize("NFKD", text)
    return "".join(c for c in n if not _ud.combining(c))


def _normalize_lookup_key(value: str) -> str:
    """Cle de lookup : strip, lowercase, ASCII, espaces collapsés."""
    if not value:
        return ""
    v = _strip_diacritics(value).lower().strip()
    v = re.sub(r"[\s\-]+", " ", v)
    return v


def _name_to_clan_first_slug(name: str) -> str:
    """'Butsuma Senju' -> 'senju_butsuma' (heuristique 2 mots)."""
    parts = _strip_diacritics(name).strip().split()
    if len(parts) == 2:
        return f"{parts[1].lower()}_{parts[0].lower()}"
    if len(parts) == 1:
        return parts[0].lower()
    # 3+ mots : on tente "Last First Mid" -> "last_first_mid"
    return "_".join(p.lower() for p in parts)


@dataclass
class CanonContext:
    """Tous les enums canon charges en memoire pour le lookup."""

    kg_ids: set[str] = field(default_factory=set)
    nature_ids: set[str] = field(default_factory=set)
    char_ids: set[str] = field(default_factory=set)
    clan_ids: set[str] = field(default_factory=set)
    village_ids: set[str] = field(default_factory=set)

    # Lookups : key -> id canon
    char_alias_to_id: dict[str, str] = field(default_factory=dict)
    clan_alias_to_id: dict[str, str] = field(default_factory=dict)
    village_alias_to_id: dict[str, str] = field(default_factory=dict)


def load_canon_context() -> CanonContext:
    """Charge tous les enums canon depuis data/canonical/."""
    ctx = CanonContext()

    # Kekkei genkai
    kg_data = json.loads((CANONICAL_DIR / "kekkei_genkai.json").read_text(encoding="utf-8"))
    for k in kg_data:
        ctx.kg_ids.add(k["id"])

    # Natures
    nat_data = json.loads((CANONICAL_DIR / "natures.json").read_text(encoding="utf-8"))
    for n in nat_data:
        ctx.nature_ids.add(n["id"])

    # Characters
    char_data = json.loads((CANONICAL_DIR / "characters.json").read_text(encoding="utf-8"))
    for c in char_data:
        ctx.char_ids.add(c["id"])
        # Index par name_romaji (lowercased + ASCII)
        name = c.get("name_romaji")
        if name:
            ctx.char_alias_to_id[_normalize_lookup_key(name)] = c["id"]
        # Index par aliases
        for alias in c.get("aliases") or []:
            ctx.char_alias_to_id[_normalize_lookup_key(alias)] = c["id"]
        # Index par id (identite, mais en cas ou le LLM produit deja le slug correct)
        ctx.char_alias_to_id[_normalize_lookup_key(c["id"].replace("_", " "))] = c["id"]

    # Clans
    clan_data = json.loads((CANONICAL_DIR / "clans.json").read_text(encoding="utf-8"))
    for c in clan_data:
        ctx.clan_ids.add(c["id"])
        if c.get("name_romaji"):
            ctx.clan_alias_to_id[_normalize_lookup_key(c["name_romaji"])] = c["id"]
        ctx.clan_alias_to_id[_normalize_lookup_key(c["id"])] = c["id"]

    # Villages
    village_data = json.loads((CANONICAL_DIR / "villages.json").read_text(encoding="utf-8"))
    for v in village_data:
        ctx.village_ids.add(v["id"])
        if v.get("name_romaji"):
            ctx.village_alias_to_id[_normalize_lookup_key(v["name_romaji"])] = v["id"]
        ctx.village_alias_to_id[_normalize_lookup_key(v["id"])] = v["id"]
        # Konoha -> konohagakure (frequent alias)
        if v.get("name_romaji"):
            short = v["name_romaji"].replace("gakure", "")
            if short:
                ctx.village_alias_to_id[_normalize_lookup_key(short)] = v["id"]

    return ctx


@dataclass
class NormalizedValue:
    """Resultat d'une normalisation."""

    original: str
    normalized: str
    was_changed: bool
    is_canonical: bool  # True si la valeur normalisee est dans un enum canon


def normalize_kg(value: str, ctx: CanonContext) -> NormalizedValue:
    if not value:
        return NormalizedValue(value, value, False, False)
    key = _normalize_lookup_key(value)
    # Direct mapping anglais -> slug
    if key in KG_ENGLISH_TO_SLUG:
        slug = KG_ENGLISH_TO_SLUG[key]
        return NormalizedValue(value, slug, slug != value, slug in ctx.kg_ids)
    # Sinon, essayer le slug par lowercase + replace
    slug = re.sub(r"\s+", "_", key)
    return NormalizedValue(value, slug, slug != value, slug in ctx.kg_ids)


def normalize_nature(value: str, ctx: CanonContext) -> NormalizedValue:
    if not value:
        return NormalizedValue(value, value, False, False)
    key = _normalize_lookup_key(value)
    if key in NATURE_ENGLISH_TO_SLUG:
        slug = NATURE_ENGLISH_TO_SLUG[key]
        return NormalizedValue(value, slug, slug != value, slug in ctx.nature_ids)
    slug = re.sub(r"\s+", "_", key)
    return NormalizedValue(value, slug, slug != value, slug in ctx.nature_ids)


def normalize_character(value: str, ctx: CanonContext) -> NormalizedValue:
    if not value:
        return NormalizedValue(value, value, False, False)
    key = _normalize_lookup_key(value)
    # Direct lookup via alias_to_id (couvre name_romaji et aliases)
    if key in ctx.char_alias_to_id:
        slug = ctx.char_alias_to_id[key]
        return NormalizedValue(value, slug, slug != value, True)
    # Essayer si la valeur est deja un slug canon
    slug_attempt = re.sub(r"\s+", "_", key)
    if slug_attempt in ctx.char_ids:
        return NormalizedValue(value, slug_attempt, slug_attempt != value, True)
    # FALLBACK 1 : swap snake_case ('obito_uchiha' -> 'uchiha_obito')
    # Le LLM produit parfois firstname_clan au lieu de clan_firstname.
    if "_" in slug_attempt:
        parts = slug_attempt.split("_")
        if len(parts) == 2:
            swapped = f"{parts[1]}_{parts[0]}"
            if swapped in ctx.char_ids:
                return NormalizedValue(value, swapped, swapped != value, True)
    # FALLBACK 2 : strip clan prefix ('senju_tsunade' -> 'tsunade')
    # Pour les persos canon dont l'id n'a pas de clan prefix
    # (Tsunade, Jiraiya, Orochimaru, Konan, Tenten, etc.).
    if "_" in slug_attempt:
        parts = slug_attempt.split("_")
        if len(parts) >= 2:
            last = parts[-1]
            if last in ctx.char_ids:
                return NormalizedValue(value, last, last != value, True)
            # Egalement le first (au cas ou)
            first = parts[0]
            if first in ctx.char_ids:
                return NormalizedValue(value, first, first != value, True)
    # Heuristique 'Firstname Lastname' -> 'lastname_firstname' (avec espace)
    flipped = _name_to_clan_first_slug(value)
    if flipped in ctx.char_ids:
        return NormalizedValue(value, flipped, flipped != value, True)
    # Pas trouve dans le canon : retourner snake_case lowercase + flag non-canon
    fallback = slug_attempt
    return NormalizedValue(value, fallback, fallback != value, False)


def normalize_clan(value: str, ctx: CanonContext) -> NormalizedValue:
    if not value:
        return NormalizedValue(value, value, False, False)
    key = _normalize_lookup_key(value)
    if key in ctx.clan_alias_to_id:
        slug = ctx.clan_alias_to_id[key]
        return NormalizedValue(value, slug, slug != value, True)
    slug = re.sub(r"\s+", "_", key)
    return NormalizedValue(value, slug, slug != value, slug in ctx.clan_ids)


def normalize_village(value: str, ctx: CanonContext) -> NormalizedValue:
    if not value:
        return NormalizedValue(value, value, False, False)
    key = _normalize_lookup_key(value)
    if key in ctx.village_alias_to_id:
        slug = ctx.village_alias_to_id[key]
        return NormalizedValue(value, slug, slug != value, True)
    slug = re.sub(r"\s+", "_", key)
    return NormalizedValue(value, slug, slug != value, slug in ctx.village_ids)


# Mapping field_path -> normalizer pour la traversal d'extraction.
FIELD_NORMALIZERS = {
    "village_of_origin.value": normalize_village,
    "clan.value": normalize_clan,
    "kekkei_genkai_possessed[].value": normalize_kg,
    "natures_possessed[].value": normalize_nature,
    "team_members[].value": normalize_character,
    "sensei_id.value": normalize_character,
    "parents[].value": normalize_character,
    "children[].value": normalize_character,
    "siblings[].value": normalize_character,
    "spouse.value": normalize_character,
    "relative_age_to[].other_char": normalize_character,
}


@dataclass
class NormalizationReport:
    """Rapport agrege de la normalisation d'une extraction."""

    char_id: str
    total_normalized: int = 0
    total_canonical: int = 0
    flags: list[dict[str, str]] = field(default_factory=list)


def normalize_extraction(extraction: dict, ctx: CanonContext) -> tuple[dict, NormalizationReport]:
    """Normalise toutes les valeurs d'une extraction. Modifie une copie.

    Retourne (extraction_normalisee, report).
    """
    out = json.loads(json.dumps(extraction))  # deep copy
    report = NormalizationReport(char_id=out.get("character_id", "?"))
    fields = out.get("fields", {})

    # Champs scalaires {value, source_quote, ...}
    scalar_normalizers = {
        "village_of_origin": normalize_village,
        "clan": normalize_clan,
        "sensei_id": normalize_character,
        "spouse": normalize_character,
    }

    list_value_normalizers = {
        "kekkei_genkai_possessed": normalize_kg,
        "natures_possessed": normalize_nature,
        "team_members": normalize_character,
        "parents": normalize_character,
        "children": normalize_character,
        "siblings": normalize_character,
    }

    def apply(value: Any, normalizer, field_path: str) -> Any:
        if not isinstance(value, str) or not value:
            return value
        result = normalizer(value, ctx)
        if result.was_changed:
            report.total_normalized += 1
        if result.is_canonical:
            report.total_canonical += 1
        else:
            report.flags.append({
                "field_path": field_path,
                "original": result.original,
                "normalized": result.normalized,
                "reason": "unknown_in_canon",
            })
        return result.normalized

    for fname, normalizer in scalar_normalizers.items():
        f = fields.get(fname)
        if isinstance(f, dict) and f.get("value"):
            f["value"] = apply(f["value"], normalizer, f"{fname}.value")

    for fname, normalizer in list_value_normalizers.items():
        lst = fields.get(fname)
        if isinstance(lst, list):
            for i, item in enumerate(lst):
                if isinstance(item, dict) and item.get("value"):
                    item["value"] = apply(item["value"], normalizer, f"{fname}[{i}].value")

    # relative_age_to[].other_char
    rat = fields.get("relative_age_to")
    if isinstance(rat, list):
        for i, item in enumerate(rat):
            if isinstance(item, dict) and item.get("other_char"):
                item["other_char"] = apply(
                    item["other_char"], normalize_character,
                    f"relative_age_to[{i}].other_char"
                )

    # tailed_beast.value : pas de canon JSON dispose pour ca, on lowercase juste
    tb = fields.get("tailed_beast")
    if isinstance(tb, dict) and isinstance(tb.get("value"), str) and tb["value"]:
        original = tb["value"]
        normalized = re.sub(r"\s+", "_", _normalize_lookup_key(original))
        tb["value"] = normalized
        if original != normalized:
            report.total_normalized += 1

    return out, report
