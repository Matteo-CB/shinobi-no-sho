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
from shinobi.canon.wikitext import strip_wiki_markup  # noqa: E402
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


# Sections a ignorer (meta, navigation, listes plates).
_BORING_SECTIONS = {
    "see also", "references", "external links", "navigation",
    "in other media", "trivia footer", "gallery",
}
# Limite par section pour eviter d'exploser les fichiers JSON (1300 NPCs * 8 sections * 5KB = 50MB).
_MAX_CHARS_PER_SECTION = 4000
_MAX_SECTIONS_PER_ENTITY = 20


def _extract_all_sections(parsed: dict) -> dict[str, str]:
    """Extrait TOUTES les sections wiki sous forme dict {titre: texte_nettoye}.

    Concatene les sous-sections sous le titre de leur parent direct (ex: 'Abilities'
    inclut 'Chakra', 'Taijutsu', 'Ninjutsu' indents). Tronque chaque section a
    _MAX_CHARS_PER_SECTION pour eviter les fichiers JSON gigantesques.
    """
    sections = parsed.get("sections", [])
    if not sections:
        return {}
    out: dict[str, list[str]] = {}
    current_h2: str | None = None
    for sec in sections:
        title = (sec.get("title") or "").strip()
        text = (sec.get("text") or "").strip()
        level = sec.get("level", 0)
        if not title or title == "(intro)":
            continue
        title_low = title.lower()
        if title_low in _BORING_SECTIONS:
            continue
        # H2 = nouvelle section principale, H3+ = sous-section a aplatir
        if level <= 2:
            current_h2 = title
            out.setdefault(current_h2, [])
            if text:
                out[current_h2].append(strip_wiki_markup(text))
        else:
            target = current_h2 or title
            out.setdefault(target, [])
            if text:
                out[target].append(f"[{title}] " + strip_wiki_markup(text))
    # Joint les morceaux + tronque
    final: dict[str, str] = {}
    for title, parts in out.items():
        joined = "\n".join(p for p in parts if p).strip()
        if not joined:
            continue
        if len(joined) > _MAX_CHARS_PER_SECTION:
            joined = joined[: _MAX_CHARS_PER_SECTION - 3] + "..."
        final[title] = joined
        if len(final) >= _MAX_SECTIONS_PER_ENTITY:
            break
    return final


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


# Mapping village -> id depuis les categories Narutopedia
_CATEGORY_TO_VILLAGE: dict[str, str] = {
    "Category:Konohagakure Characters": "konohagakure",
    "Category:Sunagakure Characters": "sunagakure",
    "Category:Kirigakure Characters": "kirigakure",
    "Category:Kumogakure Characters": "kumogakure",
    "Category:Iwagakure Characters": "iwagakure",
    "Category:Otogakure Characters": "otogakure",
    "Category:Amegakure Characters": "amegakure",
    "Category:Takigakure Characters": "takigakure",
    "Category:Yugakure Characters": "yugakure",
    "Category:Hoshigakure Characters": "hoshigakure",
    "Category:Kusagakure Characters": "kusagakure",
    "Category:Yukigakure Characters": "yukigakure",
    "Category:Shimogakure Characters": "shimogakure",
    "Category:Tanigakure Characters": "tanigakure",
}


def _detect_village_from_categories(categories: list[str]) -> str | None:
    """Detecte village depuis Category:<Village>gakure Characters."""
    for cat in categories:
        if cat in _CATEGORY_TO_VILLAGE:
            return _CATEGORY_TO_VILLAGE[cat]
    return None


# Mots-cles de natures dans les wiki_links (ex: Fire Release, Wind Release, etc.)
_NATURE_LINKS = {
    "Fire Release": "katon",
    "Water Release": "suiton",
    "Wind Release": "fuuton",
    "Earth Release": "doton",
    "Lightning Release": "raiton",
    "Wood Release": "mokuton",
    "Ice Release": "hyouton",
    "Lava Release": "youton",
    "Boil Release": "futton",
    "Storm Release": "ranton",
    "Magnet Release": "jiton",
    "Dust Release": "jinton",
    "Crystal Release": "shouton",
    "Scorch Release": "shakuton",
    "Yin Release": "inton",
    "Yang Release": "youton_yang",
    "Yin-Yang Release": "onmyoton",
}


def _detect_natures_from_links(parsed: dict) -> list[str]:
    """Extrait les natures depuis les wiki_links '{{X Release}}' classiques."""
    out: list[str] = []
    for link in parsed.get("wiki_links", []):
        if link in _NATURE_LINKS:
            slug = _NATURE_LINKS[link]
            if slug not in out:
                out.append(slug)
    return out


# Mots-cles de kekkei genkai dans wiki_links
_KEKKEI_LINKS = {
    "Sharingan": "sharingan",
    "Byakugan": "byakugan",
    "Rinnegan": "rinnegan",
    "Mangekyo Sharingan": "mangekyo_sharingan",
    "Tenseigan": "tenseigan",
    "Jougan": "jougan",
    "Ketsuryugan": "ketsuryugan",
    "Mokuton": "mokuton",
    "Hyouton": "hyouton",
    "Hyoton": "hyouton",
    "Youton": "youton",
    "Futton": "futton",
    "Jiton": "jiton",
    "Jinton": "jinton",
    "Shouton": "shouton",
    "Shakuton": "shakuton",
    "Ranton": "ranton",
    "Hyaton": "hyaton",
    "Shikotsumyaku": "shikotsumyaku",
    "Hydrification Technique": "hydrification",
}


def _detect_kekkei_from_links(parsed: dict) -> list[str]:
    """Extrait les kekkei genkai depuis les wiki_links."""
    out: list[str] = []
    for link in parsed.get("wiki_links", []):
        if link in _KEKKEI_LINKS:
            slug = _KEKKEI_LINKS[link]
            if slug not in out:
                out.append(slug)
    return out


# Date de naissance : pattern 'born on October 10' ou 'born in year XX' dans le wikitext
_BIRTH_PATTERNS = [
    r"born on (?:the night of )?(\w+) (\d+)",  # born on October 10
    r"born in (\d{4})",  # born in year 1234 (rare)
]


def _detect_birth_date_from_text(parsed: dict) -> str | None:
    """Extrait birth_date 'MM-DD' depuis l'intro si pattern reconnu."""
    sections = parsed.get("sections", [])
    if not sections:
        return None
    text = sections[0].get("text", "") + "\n"
    if len(sections) > 1:
        # Background section souvent
        for s in sections[1:5]:
            text += s.get("text", "") + "\n"
    months = {
        "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
        "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    }
    m = re.search(r"\bborn on (?:the night of |the )?(\w+) (\d+)", text, re.IGNORECASE)
    if m:
        month_name = m.group(1).lower()
        day = int(m.group(2))
        if month_name in months:
            return f"{months[month_name]:02d}-{day:02d}"
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
    # Village : priorite categories > infobox > heuristique texte > defaut
    village_slug = (
        _detect_village_from_categories(parsed.get("categories", []))
        or slugify(_first_link(params.get("affiliation")) or "")
        or _detect_village(intro)
        or "konohagakure"
    )
    sex = _gender(params.get("sex") or params.get("gender"))
    birth_year = _int(params.get("birthdate"))
    birth_date = _detect_birth_date_from_text(parsed)
    # Natures : infobox > wiki_links (Fire Release, etc.)
    natures = _slug_from_links(
        params.get("nature type") or params.get("nature_type") or params.get("nature")
    )
    if not natures:
        natures = _detect_natures_from_links(parsed)
    # Kekkei genkai : infobox > wiki_links (Sharingan, Byakugan, etc.)
    kekkei_genkai = _slug_from_links(params.get("kekkei genkai") or params.get("kekkei_genkai"))
    if not kekkei_genkai:
        kekkei_genkai = _detect_kekkei_from_links(parsed)
    techniques = _slug_from_links(params.get("jutsu"), transform=slug_technique)
    if not techniques:
        techniques = _detect_links_to_techniques(parsed)[:30]
    clan = _detect_clan(parsed)
    out = {
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
        "wiki_sections": _extract_all_sections(parsed),
    }
    if birth_date:
        out["birth_date"] = birth_date
    return out


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
        "wiki_sections": _extract_all_sections(parsed),
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
        "wiki_sections": _extract_all_sections(parsed),
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
        "wiki_sections": _extract_all_sections(parsed),
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
        "wiki_sections": _extract_all_sections(parsed),
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
        "wiki_sections": _extract_all_sections(parsed),
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
        "wiki_sections": _extract_all_sections(parsed),
    }


# CLI --------------------------------------------------------------------------


CLAN_ADVANTAGES_FR: dict[str, str] = {
    "uchiha": "Sharingan : vision aiguisee, hypnose, copie de techniques, eveil sous choc emotionnel. Affinite katon dominante. Tradition de la police militaire de Konoha.",
    "hyuga": "Byakugan : vision a 360 degres, voit les tenketsu (points de chakra), distingue le chakra ennemi. Maitrise du Juuken (Poing souple) qui ferme les meridiens.",
    "senju": "Mokuton (Bois) : controle des arbres, creation de constructions vivantes. Vitalite exceptionnelle, chakra immense, regeneration. Lignee du Premier Hokage.",
    "uzumaki": "Reserves de chakra immenses, vitalite tres au-dessus de la normale, fuinjutsu (sceaux) tres avance. Compatibilite naturelle avec les bijuu.",
    "nara": "Manipulation des ombres (Kage Mane no Jutsu). Intelligence remarquable, esprit strategique. Ils pratiquent souvent l'apothicairerie.",
    "akimichi": "Manipulation de la masse corporelle (Baika no Jutsu). Force decuplee. Reserve de chakra liee a la matiere grasse.",
    "yamanaka": "Techniques mentales (Shintenshin no Jutsu : echange de corps), sensorialite, infiltration cognitive.",
    "inuzuka": "Lien empathique avec les chiens-nin partenaires (ninken), sens olfactif et auditif developpes, taijutsu animal en duo.",
    "aburame": "Symbiose avec les insectes kikaichu : surveillance, drainage de chakra, interception silencieuse.",
    "sarutobi": "Techniques katon avancees, Enma le roi des singes en kuchiyose. Tradition de leadership a Konoha.",
    "hatake": "Lignee de combattants au sabre blanc (Hatake Sakumo), chakra raiton elevee, fideles serviteurs de Konoha.",
    "yuki": "Hyouton (Glace) : combinaison suiton + fuuton. Cree barrieres et miroirs glacaux, lignee de Haku.",
    "hozuki": "Hydrification : transformation corporelle en eau. Resistance aux blessures physiques, fluidite, suiton avance.",
    "kaguya": "Shikotsumyaku : controle absolu de la structure osseuse, creation d'armes osseuses tranchantes (lignee Kimimaro).",
    "kurama": "Genjutsu hereditaire de tres haut niveau, illusion devorant le pratiquant si elle echoue.",
    "chinoike": "Ketsuryugan : dojutsu permettant de manipuler le sang dans tous les corps presents, hypnose des fluides.",
    "fuma": "Techniques de shuriken (fuma shuriken legendaire), ninjutsu d'illusion.",
    "kohaku": "Tradition d'epeistes du Pays de la Riziere.",
    "shimura": "Lignee politique influente a Konoha, tradition de leadership militaire (Anbu Roto).",
    "lee": "Lignee axee sur le taijutsu pur et l'ouverture des Huit Portes.",
    "namikaze": "Ascendance liee au Yondaime Hokage, talent rare en fuinjutsu et Hiraishin.",
    "kazekage": "Lignee politique de Sunagakure, dominance jiton (magnetisme) et controle du sable.",
    "raikage": "Lignee politique de Kumogakure, raiton ultra-rapide et armure de chakra.",
    "tsuchikage": "Lignee politique d'Iwagakure, doton et techniques de leviation.",
    "yotsuki": "Clan Kumogakure aux capacites physiques surhumaines (super-strength).",
    "kamizuru": "Clan d'Iwagakure utilisant les techniques d'abeilles, rivalise historiquement avec les Aburame.",
    "explosion_corps": "Specialistes du Bakuton (explosion), tradition d'Iwagakure.",
    "yagura": "Lignee jinchuuriki d'Isobu (3 queues), chef historique de Kirigakure.",
    "terumi": "Lignee Mei Terumi : Yoton (lave) et Futton (vapeur), chefs de Kirigakure post-Yagura.",
    "karatachi": "Clan kirigakure des Sept Epees (Hoshigaki Kisame, Karatachi Yagura).",
}

CLAN_DISADVANTAGES_FR: dict[str, str] = {
    "uchiha": "Maledictions de la haine : trauma necessaire pour eveiller le Mangekyou. Risque de cecite progressive. Mefiance de Konoha apres l'incident de Kyuubi.",
    "hyuga": "Angle mort dans le champ visuel pres de la nuque. Division Souke/Bunke avec le sceau de l'oiseau en cage qui controle la branche secondaire.",
    "senju": "Quasi-extinction apres la Deuxieme Guerre. Mokuton tres rare, ne se transmet presque plus.",
    "uzumaki": "Decimes a Uzushiogakure. Ils sont la cible historique des autres villages a cause de leur fuinjutsu.",
    "nara": "Faible reserve de chakra par rapport a leur taille, taijutsu mediocre, sensibles a la luminosite quand leur ombre est captive.",
    "akimichi": "Les pilules de chakra ont des effets secondaires graves (rouge tue le pratiquant). Vulnerables au feu et a la perte de masse corporelle.",
    "yamanaka": "Le corps est sans defense pendant que l'esprit est en transposition. Echec critique = esprit perdu definitivement.",
    "inuzuka": "Si le ninken est tue, le combattant est diminue de moitie. Sens hyper-developpes vulnerables aux attaques sonores et olfactives.",
    "aburame": "Vulnerables au feu (les insectes brulent), aux insecticides et aux genjutsu de degout. Peu socialises.",
    "yuki": "Persecutes a Kirigakure, presque eteints. Vulnerables au katon avance.",
    "hozuki": "Tres vulnerables aux raiton (electricite + eau). Necessitent une source d'eau a proximite pour l'efficacite maximale.",
    "kaguya": "Eteints apres la rebellion contre Kiri. Maledictions liees a leur ascendance Otsutsuki.",
    "kurama": "Peu de membres maitrisent l'illusion ; la plupart en deviennent fous a force de l'utiliser.",
    "fuma": "Histoire de fragmentation, allegeances divisees entre Konoha et Otogakure.",
    "lee": "Aucune aptitude a manipuler le chakra pour le ninjutsu et le genjutsu (cas de Rock Lee).",
}


CLAN_KEKKEI_FR: dict[str, list[str]] = {
    "uchiha": ["sharingan"],
    "hyuga": ["byakugan"],
    "senju": ["mokuton"],
    "kaguya": ["shikotsumyaku"],
    "yuki": ["hyouton"],
    "hozuki": ["hydrification"],
    "chinoike": ["ketsuryugan"],
    "kurama": ["genjutsu_kekkei"],
    "shimura": [],
    "uzumaki": [],
    "namikaze": [],
}

CLAN_NATURES_FR: dict[str, list[str]] = {
    "uchiha": ["katon"],
    "hyuga": [],
    "senju": ["mokuton", "suiton", "doton"],
    "uzumaki": ["fuuton", "youton_yang"],
    "namikaze": ["fuuton", "raiton"],
    "sarutobi": ["katon"],
    "hatake": ["raiton"],
    "yuki": ["hyouton"],
    "hozuki": ["suiton"],
    "yamanaka": ["inton"],
    "nara": ["inton"],
    "akimichi": ["doton"],
    "kazekage": ["jiton", "fuuton"],
    "raikage": ["raiton"],
    "tsuchikage": ["doton", "jinton"],
}


def _post_process_links(canon_dir: Path) -> None:
    """Phase 2 : connecte les datasets entre eux apres production initiale."""
    clans_path = canon_dir / "clans.json"
    villages_path = canon_dir / "villages.json"
    chars_path = canon_dir / "characters.json"

    if not clans_path.exists() or not villages_path.exists():
        return

    clans = json.loads(clans_path.read_text(encoding="utf-8"))
    villages = json.loads(villages_path.read_text(encoding="utf-8"))
    clans_by_id = {c["id"]: c for c in clans}
    village_ids = {v["id"] for v in villages}

    # 0. Augmenter les clans avec connaissance canon hardcodee.
    for clan in clans:
        cid = clan["id"]
        if cid in CLAN_KEKKEI_FR and not clan.get("key_kekkei_genkai"):
            clan["key_kekkei_genkai"] = list(CLAN_KEKKEI_FR[cid])
        if cid in CLAN_NATURES_FR and not clan.get("key_natures"):
            clan["key_natures"] = list(CLAN_NATURES_FR[cid])
        if cid in CLAN_ADVANTAGES_FR and not clan.get("key_advantages_fr"):
            clan["key_advantages_fr"] = CLAN_ADVANTAGES_FR[cid]
        if cid in CLAN_DISADVANTAGES_FR and not clan.get("key_disadvantages_fr"):
            clan["key_disadvantages_fr"] = CLAN_DISADVANTAGES_FR[cid]

    # 1. Pour chaque village, populer main_clans avec les clans dont village_of_origin matche
    village_to_clans: dict[str, set[str]] = {vid: set() for vid in village_ids}
    for clan in clans:
        v = clan.get("village_of_origin")
        if v and v in village_ids:
            village_to_clans[v].add(clan["id"])

    # 2. Fallback canonique pour les 5 grands villages : clans connus mais pas auto-detectes
    known_villages: dict[str, tuple[str, ...]] = {
        "konohagakure": (
            "uchiha",
            "senju",
            "hyuga",
            "nara",
            "akimichi",
            "yamanaka",
            "inuzuka",
            "aburame",
            "sarutobi",
            "uzumaki",
            "hatake",
            "yuhi",
            "mitarashi",
            "kurama",
            "kohaku",
            "fuma",
            "shimura",
            "lee",
            "kazama",
            "hagoromo",
            "izumo",
        ),
        "sunagakure": ("kazekage", "fuma", "kazama"),
        "kirigakure": ("yuki", "hozuki", "kaguya", "yagura", "terumi", "karatachi"),
        "kumogakure": ("yotsuki", "raikage"),
        "iwagakure": ("kamizuru", "explosion_corps", "tsuchikage"),
        "amegakure": ("uzumaki", "fuma"),
        "uzushiogakure": ("uzumaki",),
    }
    for vid, ids in known_villages.items():
        if vid in village_to_clans:
            for cid in ids:
                if cid in clans_by_id:
                    village_to_clans[vid].add(cid)

    for v in villages:
        existing = set(v.get("main_clans") or [])
        existing.update(village_to_clans.get(v["id"], set()))
        v["main_clans"] = sorted(existing)

    villages_path.write_text(
        json.dumps(villages, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    logger.info(
        "postprocess_villages", villages_with_clans=sum(1 for v in villages if v["main_clans"])
    )

    # 3. Pour chaque character, detecter le clan depuis le nom et le village du clan
    chars: list[dict[str, Any]] = []
    if chars_path.exists():
        chars = json.loads(chars_path.read_text(encoding="utf-8"))
        clan_tokens = {cid.replace("_", " "): cid for cid in clans_by_id}
        clan_to_village = {c["id"]: c.get("village_of_origin") for c in clans}
        adjusted = 0
        for ch in chars:
            name = ch.get("name_romaji", "").lower()
            # Detecter le clan depuis le nom meme si un clan a deja ete assigne (override
            # quand un nom contient un id de clan canonique connu = priorite haute).
            best_match: str | None = None
            for token, cid in clan_tokens.items():
                if token and len(token) >= 3 and f" {token}" in f" {name} ":
                    best_match = cid
                    break
            if best_match and ch.get("clan") != best_match:
                ch["clan"] = best_match
                adjusted += 1
            cid = ch.get("clan")
            if cid:
                new_village = clan_to_village.get(cid)
                if new_village:
                    ch["village_of_origin"] = new_village
        chars_path.write_text(
            json.dumps(chars, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        logger.info("postprocess_characters", clan_assigned=adjusted)

    # 4. Reverse-resolve techniques par clan via :
    #    - canonical_users explicites du JSON technique
    #    - heuristique : tokens de clans presents dans le titre OU les wiki_links de la technique page
    techniques_path = canon_dir / "techniques.json"
    parsed_techs_dir = canon_dir.parent / "raw" / "narutopedia" / "parsed" / "technique"
    if techniques_path.exists() and chars:
        techniques = json.loads(techniques_path.read_text(encoding="utf-8"))
        char_to_clan: dict[str, str] = {c["id"]: c["clan"] for c in chars if c.get("clan")}
        clan_tokens = {cid.replace("_", " "): cid for cid in clans_by_id}
        clan_to_techniques: dict[str, set[str]] = {cid: set() for cid in clans_by_id}
        tech_users_to_add: dict[str, set[str]] = {t["id"]: set() for t in techniques}

        # 4a. via canonical_users explicites
        for tech in techniques:
            for user_id in tech.get("canonical_users") or []:
                clan = char_to_clan.get(user_id)
                if clan and clan in clan_to_techniques:
                    clan_to_techniques[clan].add(tech["id"])

        # 4b. via wiki_links des pages techniques scrapees
        if parsed_techs_dir.exists():
            char_name_to_id = {
                c.get("name_romaji", "").lower(): c["id"] for c in chars if c.get("name_romaji")
            }
            for parsed_file in parsed_techs_dir.glob("*.json"):
                try:
                    parsed = json.loads(parsed_file.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    continue
                title = parsed.get("title", "")
                tech_id = slug_technique(title)
                if tech_id not in tech_users_to_add:
                    continue
                # Scanner le titre pour token de clan
                lower_title = title.lower()
                for token, cid in clan_tokens.items():
                    if token and len(token) >= 3 and f" {token}" in f" {lower_title} ":
                        clan_to_techniques[cid].add(tech_id)
                # Scanner les wiki_links pour des noms de characters
                for link in parsed.get("wiki_links") or []:
                    char_id = char_name_to_id.get(link.lower())
                    if char_id:
                        tech_users_to_add[tech_id].add(char_id)
                        clan = char_to_clan.get(char_id)
                        if clan and clan in clan_to_techniques:
                            clan_to_techniques[clan].add(tech_id)

        # Mettre a jour techniques.canonical_users avec les nouveaux liens
        for tech in techniques:
            new_users = sorted(
                set(tech.get("canonical_users") or []) | tech_users_to_add.get(tech["id"], set())
            )
            tech["canonical_users"] = new_users
        techniques_path.write_text(
            json.dumps(techniques, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        for c in clans:
            existing = set(c.get("key_techniques") or [])
            existing.update(clan_to_techniques.get(c["id"], set()))
            c["key_techniques"] = sorted(existing)[:80]

        clans_path.write_text(
            json.dumps(clans, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        techniques_count = sum(len(c.get("key_techniques") or []) for c in clans)
        logger.info(
            "postprocess_clans",
            techniques_attached=techniques_count,
            techniques_users_filled=sum(1 for t in techniques if t["canonical_users"]),
        )


@cli.command()
def build(
    types: str = typer.Option("all", help="CSV des types a produire."),
    raw_dir: str = typer.Option("data/raw/narutopedia", help="Repertoire raw."),
    skip_postprocess: bool = typer.Option(False, help="Ne pas relier les datasets entre eux."),
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

    if not skip_postprocess:
        _post_process_links(canon_dir)

    print("Datasets produits :")
    for k, v in sorted(counts.items()):
        print(f"  {k}: {v}")


if __name__ == "__main__":
    cli()
