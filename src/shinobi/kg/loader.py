"""Importer les datasets canonical/*.json vers le KG dynamique.

Strategie : chaque entree des JSON canon devient N triplets dans le KG.

- Character `uzumaki_naruto` :
    (uzumaki_naruto, type, character)
    (uzumaki_naruto, name_romaji, "Naruto Uzumaki")
    (uzumaki_naruto, birth_year, "0")
    (uzumaki_naruto, village_of_origin, konohagakure)
    (uzumaki_naruto, clan, uzumaki)
    (uzumaki_naruto, has_kekkei_genkai, ...) [si liste non vide]
    (uzumaki_naruto, has_nature, katon) [par nature]
    (uzumaki_naruto, has_tailed_beast, kurama)

- Technique `rasengan` :
    (rasengan, type, technique)
    (rasengan, rank, A)
    (rasengan, has_canonical_user, uzumaki_naruto)
    (rasengan, requires_nature, ...) [si liste]

- Clan `uchiha` :
    (uchiha, type, clan)
    (uchiha, has_key_kekkei_genkai, sharingan)
    (uchiha, has_key_nature, katon)
    (uchiha, key_member, uchiha_madara) [par membre canon]

- Village, Location, KekkeiGenkai, Organization, TailedBeast, Hiden similaire.

Tous les facts importes ont :
- source = 'canon'
- canonicity = 'canon_strict'
- confidence = 1.0
- valid_from_year / valid_to_year nuls (sauf pour les rangs progressifs et
  les morts canon -> closes proprement)

Idempotence : avant d'importer, on `clear_all()` (sinon doublons).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from shinobi.kg.schema import Canonicity, Fact, FactSource, ObjectType
from shinobi.kg.store import KnowledgeGraphStore
from shinobi.logging_setup import get_logger

logger = get_logger(__name__)


def _entity_fact(subject: str, type_: str) -> Fact:
    """Triplet (subject, type, value)."""
    return Fact(
        subject=subject, relation="type", object=type_,
        object_type=ObjectType.value,
        source=FactSource.canon.value, canonicity=Canonicity.canon_strict,
    )


def _value_fact(subject: str, relation: str, value: Any) -> Fact:
    """Triplet (subject, relation, value) ou value est un scalaire."""
    return Fact(
        subject=subject, relation=relation, object=str(value),
        object_type=ObjectType.value,
        source=FactSource.canon.value, canonicity=Canonicity.canon_strict,
    )


def _entity_link(subject: str, relation: str, target: str) -> Fact:
    """Triplet (subject, relation, target_id) ou target est un autre id canon."""
    return Fact(
        subject=subject, relation=relation, object=target,
        object_type=ObjectType.entity,
        source=FactSource.canon.value, canonicity=Canonicity.canon_strict,
    )


def _facts_from_character(c: dict[str, Any]) -> list[Fact]:
    cid = c.get("id")
    if not cid:
        return []
    facts: list[Fact] = [_entity_fact(cid, "character")]
    if name := c.get("name_romaji"):
        facts.append(_value_fact(cid, "name_romaji", name))
    if name_fr := c.get("name_fr"):
        facts.append(_value_fact(cid, "name_fr", name_fr))
    if name_kanji := c.get("name_kanji"):
        facts.append(_value_fact(cid, "name_kanji", name_kanji))
    if (gender := c.get("gender")):
        facts.append(_value_fact(cid, "gender", gender))
    if (by := c.get("birth_year")) is not None:
        facts.append(_value_fact(cid, "birth_year", by))
    if (dy := c.get("death_year")) is not None:
        f = _value_fact(cid, "death_year", dy)
        f.valid_from_year = dy
        facts.append(f)
        # Marquer aussi le statut alive avec une borne haute
        alive = _value_fact(cid, "alive", "true")
        alive.valid_from_year = c.get("birth_year")
        alive.valid_to_year = dy - 1
        facts.append(alive)
    elif (by := c.get("birth_year")) is not None:
        alive = _value_fact(cid, "alive", "true")
        alive.valid_from_year = by
        facts.append(alive)
    if (village := c.get("village_of_origin")):
        facts.append(_entity_link(cid, "village_of_origin", village))
    if (clan := c.get("clan")):
        facts.append(_entity_link(cid, "clan", clan))
    for kg in c.get("kekkei_genkai", []) or []:
        facts.append(_entity_link(cid, "has_kekkei_genkai", kg))
    for n in c.get("natures", []) or []:
        facts.append(_entity_link(cid, "has_nature", n))
    if (tb := c.get("tailed_beast")):
        facts.append(_entity_link(cid, "is_jinchuriki_of", tb))
    # rank_progression -> facts horodates
    for entry in c.get("rank_progression", []) or []:
        f = _value_fact(cid, "rank", entry.get("rank") or "")
        f.valid_from_year = entry.get("year")
        facts.append(f)
    # current_village_by_era
    for entry in c.get("current_village_by_era", []) or []:
        if village := entry.get("village"):
            f = _entity_link(cid, "current_village", village)
            f.valid_from_year = entry.get("from_year")
            f.valid_to_year = entry.get("to_year")
            facts.append(f)
    # location_by_year
    for entry in c.get("location_by_year", []) or []:
        if loc := entry.get("location"):
            f = _entity_link(cid, "located_at", loc)
            f.valid_from_year = entry.get("year")
            facts.append(f)
    return facts


def _facts_from_technique(t: dict[str, Any]) -> list[Fact]:
    tid = t.get("id")
    if not tid:
        return []
    facts: list[Fact] = [_entity_fact(tid, "technique")]
    if name := t.get("name_romaji"):
        facts.append(_value_fact(tid, "name_romaji", name))
    if (rank := t.get("rank")):
        facts.append(_value_fact(tid, "rank", rank))
    if (cat := t.get("category")):
        facts.append(_value_fact(tid, "category", cat))
    for n in t.get("natures", []) or []:
        facts.append(_entity_link(tid, "requires_nature", n))
    for u in t.get("canonical_users", []) or []:
        facts.append(_entity_link(tid, "has_canonical_user", u))
    if (creator := t.get("creator_id")):
        facts.append(_entity_link(tid, "created_by", creator))
    pre = t.get("prerequisites") or {}
    for kg in pre.get("kekkei_genkai_restriction") or []:
        if isinstance(kg, str):
            facts.append(_entity_link(tid, "requires_kekkei_genkai", kg))
    if (clan := pre.get("clan_restriction")):
        facts.append(_entity_link(tid, "requires_clan", clan))
    return facts


def _facts_from_clan(c: dict[str, Any]) -> list[Fact]:
    cid = c.get("id")
    if not cid:
        return []
    facts: list[Fact] = [_entity_fact(cid, "clan")]
    if name := c.get("name_romaji"):
        facts.append(_value_fact(cid, "name_romaji", name))
    if (origin := c.get("village_of_origin")):
        facts.append(_entity_link(cid, "village_of_origin", origin))
    for kg in c.get("key_kekkei_genkai", []) or []:
        facts.append(_entity_link(cid, "has_key_kekkei_genkai", kg))
    for n in c.get("key_natures", []) or []:
        facts.append(_entity_link(cid, "has_key_nature", n))
    for tech in c.get("key_techniques", []) or []:
        facts.append(_entity_link(cid, "has_key_technique", tech))
    return facts


def _facts_from_village(v: dict[str, Any]) -> list[Fact]:
    vid = v.get("id")
    if not vid:
        return []
    facts: list[Fact] = [_entity_fact(vid, "village")]
    if (name := v.get("name_romaji")):
        facts.append(_value_fact(vid, "name_romaji", name))
    if (country := v.get("country")):
        facts.append(_entity_link(vid, "country", country))
    if (founded := v.get("founded_year")) is not None:
        facts.append(_value_fact(vid, "founded_year", founded))
    for kage in v.get("kage_lineage", []) or []:
        if cid := kage.get("character_id"):
            f = _entity_link(vid, "kage", cid)
            f.valid_from_year = kage.get("from_year")
            f.valid_to_year = kage.get("to_year")
            facts.append(f)
    for clan in v.get("main_clans", []) or []:
        facts.append(_entity_link(vid, "main_clan", clan))
    return facts


def _facts_from_location(loc: dict[str, Any]) -> list[Fact]:
    lid = loc.get("id")
    if not lid:
        return []
    facts: list[Fact] = [_entity_fact(lid, "location")]
    if (name := loc.get("name_romaji")):
        facts.append(_value_fact(lid, "name_romaji", name))
    if (country := loc.get("country")):
        facts.append(_value_fact(lid, "country", country))
    if (near := loc.get("near_village")):
        facts.append(_entity_link(lid, "near_village", near))
    return facts


def _facts_from_kekkei(k: dict[str, Any]) -> list[Fact]:
    kid = k.get("id")
    if not kid:
        return []
    facts: list[Fact] = [_entity_fact(kid, "kekkei_genkai" if k.get("category") != "kekkei_mora" else "kekkei_mora")]
    if (name := k.get("name_romaji")):
        facts.append(_value_fact(kid, "name_romaji", name))
    if (kt := k.get("type")):
        facts.append(_value_fact(kid, "kekkei_type", kt))
    for clan in k.get("carrier_clans", []) or []:
        facts.append(_entity_link(kid, "carried_by", clan))
    return facts


def _facts_from_organization(o: dict[str, Any]) -> list[Fact]:
    oid = o.get("id")
    if not oid:
        return []
    facts: list[Fact] = [_entity_fact(oid, "organization")]
    if (name := o.get("name_romaji")):
        facts.append(_value_fact(oid, "name_romaji", name))
    for hq in o.get("headquarters", []) or []:
        facts.append(_value_fact(oid, "headquarters", hq))
    for f_id in o.get("founders", []) or []:
        facts.append(_entity_link(oid, "founded_by", f_id))
    for leader in o.get("leaders_by_era", []) or []:
        if (lid := leader.get("leader")):
            f = _entity_link(oid, "leader", lid)
            f.valid_from_year = leader.get("from_year")
            f.valid_to_year = leader.get("to_year")
            facts.append(f)
    return facts


def _facts_from_tailed_beast(b: dict[str, Any]) -> list[Fact]:
    bid = b.get("id")
    if not bid:
        return []
    facts: list[Fact] = [_entity_fact(bid, "tailed_beast")]
    if (name := b.get("name_romaji")):
        facts.append(_value_fact(bid, "name_romaji", name))
    if (tails := b.get("tails")) is not None:
        facts.append(_value_fact(bid, "tails", tails))
    for entry in b.get("current_jinchuuriki_by_era", []) or []:
        if (jid := entry.get("jinchuuriki")):
            f = _entity_link(bid, "current_jinchuriki", jid)
            f.valid_from_year = entry.get("from_year")
            f.valid_to_year = entry.get("to_year")
            facts.append(f)
    return facts


def _facts_from_event(e: dict[str, Any]) -> list[Fact]:
    eid = e.get("id")
    if not eid:
        return []
    facts: list[Fact] = [_entity_fact(eid, "timeline_event")]
    if (name := e.get("name_fr")):
        facts.append(_value_fact(eid, "name_fr", name))
    if (year := e.get("year")) is not None:
        f = _value_fact(eid, "occurs_in_year", year)
        f.valid_from_year = year
        facts.append(f)
    if (loc := e.get("location")):
        facts.append(_entity_link(eid, "occurs_at", loc))
    for ch in e.get("involved_characters", []) or []:
        facts.append(_entity_link(eid, "involves", ch))
    return facts


def import_canon_to_kg(
    store: KnowledgeGraphStore,
    canon_dir: Path | str,
    *,
    clear_first: bool = True,
) -> dict[str, int]:
    """Importe TOUS les datasets canon dans le KG. Retourne stats par type.

    Args:
        store : KG store ouvert
        canon_dir : repertoire data/canonical/
        clear_first : si True, vide le KG avant import (default True pour idempotence)
    """
    canon = Path(canon_dir)
    if clear_first:
        store.clear_all()

    stats: dict[str, int] = {}

    # Helper local
    def _import_list(filename: str, fact_builder, label: str) -> None:
        path = canon / filename
        if not path.exists():
            stats[label] = 0
            return
        items = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(items, list):
            stats[label] = 0
            return
        all_facts: list[Fact] = []
        for item in items:
            try:
                all_facts.extend(fact_builder(item))
            except Exception as exc:
                logger.warning("kg_import_item_failed", file=filename, error=str(exc))
        store.add_facts_batch(all_facts)
        stats[label] = len(all_facts)

    _import_list("characters.json", _facts_from_character, "characters")
    _import_list("techniques.json", _facts_from_technique, "techniques")
    _import_list("clans.json", _facts_from_clan, "clans")
    _import_list("villages.json", _facts_from_village, "villages")
    _import_list("locations.json", _facts_from_location, "locations")
    _import_list("kekkei_genkai.json", _facts_from_kekkei, "kekkei_genkai")
    _import_list("kekkei_mora.json", _facts_from_kekkei, "kekkei_mora")
    _import_list("organizations.json", _facts_from_organization, "organizations")
    _import_list("tailed_beasts.json", _facts_from_tailed_beast, "tailed_beasts")
    _import_list("timeline_events.json", _facts_from_event, "timeline_events")

    total = sum(stats.values())
    logger.info("kg_import_complete", total_facts=total, **stats)
    stats["total"] = total
    return stats


__all__ = ["import_canon_to_kg"]
