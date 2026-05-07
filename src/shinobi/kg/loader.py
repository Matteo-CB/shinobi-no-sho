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

from shinobi.kg.schema import (
    Canonicity, Fact, FactSource, ObjectType, map_source_canonicity,
)
from shinobi.kg.store import KnowledgeGraphStore
from shinobi.logging_setup import get_logger

logger = get_logger(__name__)


# Mapping canonicity source (champ JSON par entree) -> Canonicity enum runtime.
# Spec Phase A doc 02 §5.1 : Canonicity = canon_strict | canon_modified | divergent.
# - manga / boruto_manga / boruto / anime_canon / movie_canon / databook :
#   sources textuellement attestables -> canon_strict
# - filler / game : statut non-canon strict (anime fillers, jeux video) -> canon_modified
# - tbv : "to be verified", attribution canon incertaine -> canon_modified
# - None / chaine vide / valeur inconnue : default canon_strict (fail-safe import)
_CANONICITY_MAP: dict[str, Canonicity] = {
    "manga": Canonicity.canon_strict,
    "boruto_manga": Canonicity.canon_strict,
    "boruto": Canonicity.canon_strict,
    "anime_canon": Canonicity.canon_strict,
    "movie_canon": Canonicity.canon_strict,
    "databook": Canonicity.canon_strict,
    "filler": Canonicity.canon_modified,
    "game": Canonicity.canon_modified,
    "tbv": Canonicity.canon_modified,
}


def _map_canonicity(raw: Any) -> Canonicity:
    """Mappe une valeur source vers Canonicity (alias backward-compat).

    Round 41 : delegue a schema.map_source_canonicity (helper unifie
    cross-pipeline) pour eliminer la divergence canon-vs-mission.
    """
    return map_source_canonicity(raw)


def _entity_fact(subject: str, type_: str) -> Fact:
    """Triplet (subject, type, value)."""
    return Fact(
        subject=subject, relation="type", object=type_,
        object_type=ObjectType.value,
        source=FactSource.canon.value, canonicity=Canonicity.canon_strict,
    )


def _dedupe_facts(facts: list[Fact]) -> list[Fact]:
    """Deduplique une liste de Facts par tuple identifiant.

    Spec Phase A : un meme triplet (subject, relation, object,
    valid_from_year, valid_to_year, source) ne devrait apparaitre qu'une fois.

    Sources de duplicates dans le canon :
    - Dans une meme entree : `canonical_users: ["b_killer", "b_killer", ...]`
      (data quality)
    - Cross-dataset : `anbu` apparait dans organizations.json ET ranks.json,
      generant des doublons name_fr / has_source / sourced_from
    """
    seen: set[tuple] = set()
    out: list[Fact] = []
    for f in facts:
        key = (
            f.subject, f.relation, f.object,
            f.valid_from_year, f.valid_to_year, f.source,
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out


def _source_refs_facts(subject: str, sources: Any) -> list[Fact]:
    """Emit (entity, has_source, "narutopedia:...") par ref canonique.

    Spec Phase A : "100% des facts canon importes sans perte". Le champ
    `sources` (1 par entite typiquement) est canon traceability metadata,
    permettant a l'utilisateur de remonter au narutopedia/databook source.
    """
    if not isinstance(sources, list):
        return []
    facts: list[Fact] = []
    for src in sources:
        if not isinstance(src, str) or not src:
            continue
        facts.append(Fact(
            subject=subject, relation="has_source", object=src,
            object_type=ObjectType.value,
            source=FactSource.canon.value,
            canonicity=Canonicity.canon_strict,
        ))
    return facts


def _text_fr_facts(subject: str, item: dict[str, Any]) -> list[Fact]:
    """Emet les champs descriptifs *_fr canon comme facts texte.

    Spec Phase A "100% des facts canon importes sans perte" : les champs
    description_fr / personality_fr / abilities_fr / history_summary_fr /
    ideology_fr / geography_fr / activation_conditions_fr / weaknesses_fr /
    key_advantages_fr / key_disadvantages_fr / narrative_summary_fr /
    country_name_fr / death_circumstances_fr / summary_fr sont du contenu
    canon-derive et doivent etre dans le KG (5165 facts au total).

    Auto-detection : toute cle terminant par `_fr` qui n'est PAS un nom
    deja gere par le builder (name_fr, name_kanji etc.). Le dedup global
    (round 29) gere les overlaps eventuels.
    """
    if not isinstance(item, dict):
        return []
    facts: list[Fact] = []
    for key, value in item.items():
        # Skip champs name_fr / kanji deja emis par les builders specifiques
        if key in ("name_fr", "name_kanji"):
            continue
        if not key.endswith("_fr"):
            continue
        if not isinstance(value, str) or not value:
            continue
        facts.append(_value_fact(subject, key, value))
    return facts


def _updated_at_fact(subject: str, raw: Any) -> Fact | None:
    """Emit (entity, canon_updated_at, "YYYY-MM-DD") timestamp source.

    Spec Phase A : "100% des facts canon importes sans perte". Le champ
    `updated_at` indique quand l'entree JSON a ete modifiee la derniere
    fois (audit trail / cache invalidation). Distinct de `Fact.created_at_ts`
    qui est le timestamp d'insertion en KG.
    """
    if not isinstance(raw, str) or not raw:
        return None
    return Fact(
        subject=subject, relation="canon_updated_at", object=raw,
        object_type=ObjectType.value,
        source=FactSource.canon.value, canonicity=Canonicity.canon_strict,
    )


def _source_canonicity_fact(subject: str, raw: Any) -> Fact | None:
    """Preserve la canonicity source brute (manga/boruto_manga/filler/game/tbv...).

    Spec Phase A : "100% des facts canon importes sans perte". Le champ
    `canonicity` par entree decrit la PROVENANCE canon (quel media). Le
    champ `Fact.canonicity` decrit la VALIDITE runtime (canon_strict /
    canon_modified / divergent). On preserve les 2 informations distinctes.
    """
    if raw is None or raw == "":
        return None
    return Fact(
        subject=subject, relation="sourced_from", object=str(raw),
        object_type=ObjectType.value,
        source=FactSource.canon.value,
        canonicity=_map_canonicity(raw),  # propage filler->canon_modified, etc.
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
    if not isinstance(cid, str) or not cid:
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
    by = c.get("birth_year")
    dy = c.get("death_year")
    if by is not None:
        facts.append(_value_fact(cid, "birth_year", by))
    if (bd := c.get("birth_date")):
        # Format MM-DD (canon : Naruto 10-10, Minato 10-10)
        facts.append(_value_fact(cid, "birth_date", bd))
    if dy is not None:
        f = _value_fact(cid, "death_year", dy)
        f.valid_from_year = dy
        facts.append(f)
    if by is not None:
        alive = _value_fact(cid, "alive", "true")
        alive.valid_from_year = by
        # Si death_year connu, alive.valid_to_year = death_year - 1
        if dy is not None:
            alive.valid_to_year = dy - 1
        facts.append(alive)
    if (village := _str_or_none(c.get("village_of_origin"))):
        facts.append(_entity_link(cid, "village_of_origin", village))
    if (clan := _str_or_none(c.get("clan"))):
        facts.append(_entity_link(cid, "clan", clan))
    for kg in _str_list(c, "kekkei_genkai"):
        facts.append(_entity_link(cid, "has_kekkei_genkai", kg))
    for n in _str_list(c, "natures"):
        facts.append(_entity_link(cid, "has_nature", n))
    # current_village_by_era
    for entry in c.get("current_village_by_era", []) or []:
        if not isinstance(entry, dict):
            continue
        if village := entry.get("village"):
            f = _entity_link(cid, "current_village", village)
            f.valid_from_year = entry.get("from_year")
            f.valid_to_year = entry.get("to_year")
            facts.append(f)
    # techniques_known_by_era : par annee, liste des techniques connues
    # Spec §5.1 : valid_from_year horodate "depuis quand le NPC connait X"
    for entry in c.get("techniques_known_by_era", []) or []:
        if not isinstance(entry, dict):
            continue
        year = entry.get("year")
        for tech in entry.get("techniques", []) or []:
            if not isinstance(tech, str):
                continue
            f = _entity_link(cid, "knows_technique", tech)
            f.valid_from_year = year
            facts.append(f)
    return facts


def _facts_from_technique(t: dict[str, Any]) -> list[Fact]:
    tid = t.get("id")
    if not isinstance(tid, str) or not tid:
        return []
    facts: list[Fact] = [_entity_fact(tid, "technique")]
    if name := t.get("name_romaji"):
        facts.append(_value_fact(tid, "name_romaji", name))
    if (name_fr := t.get("name_fr")):
        facts.append(_value_fact(tid, "name_fr", name_fr))
    if (name_kanji := t.get("name_kanji")):
        facts.append(_value_fact(tid, "name_kanji", name_kanji))
    if (rank := t.get("rank")):
        facts.append(_value_fact(tid, "rank", rank))
    if (cat := t.get("category")):
        facts.append(_value_fact(tid, "category", cat))
    for n in _str_list(t, "natures"):
        facts.append(_entity_link(tid, "requires_nature", n))
    for u in _str_list(t, "canonical_users"):
        facts.append(_entity_link(tid, "has_canonical_user", u))
    if (creator := _str_or_none(t.get("creator_id"))):
        facts.append(_entity_link(tid, "created_by", creator))
    pre = t.get("prerequisites") or {}
    if not isinstance(pre, dict):  # defensive : prerequisites corrupted
        pre = {}
    for kg in pre.get("kekkei_genkai_restriction") or []:
        if isinstance(kg, str):
            facts.append(_entity_link(tid, "requires_kekkei_genkai", kg))
    if (clan := _str_or_none(pre.get("clan_restriction"))):
        facts.append(_entity_link(tid, "requires_clan", clan))
    return facts


def _facts_from_clan(c: dict[str, Any]) -> list[Fact]:
    cid = c.get("id")
    if not isinstance(cid, str) or not cid:
        return []
    facts: list[Fact] = [_entity_fact(cid, "clan")]
    if name := c.get("name_romaji"):
        facts.append(_value_fact(cid, "name_romaji", name))
    if (origin := _str_or_none(c.get("village_of_origin"))):
        facts.append(_entity_link(cid, "village_of_origin", origin))
    for kg in _str_list(c, "key_kekkei_genkai"):
        facts.append(_entity_link(cid, "has_key_kekkei_genkai", kg))
    for n in _str_list(c, "key_natures"):
        facts.append(_entity_link(cid, "has_key_nature", n))
    for tech in _str_list(c, "key_techniques"):
        facts.append(_entity_link(cid, "has_key_technique", tech))
    # available_* : ce qui est accessible aux membres du clan (canon)
    for kg in _str_list(c, "available_kekkei_genkai"):
        facts.append(_entity_link(cid, "available_kekkei_genkai", kg))
    for n in _str_list(c, "available_natures"):
        facts.append(_entity_link(cid, "available_nature", n))
    for tech in _str_list(c, "available_techniques"):
        facts.append(_entity_link(cid, "available_technique", tech))
    return facts


def _facts_from_village(v: dict[str, Any]) -> list[Fact]:
    vid = v.get("id")
    if not isinstance(vid, str) or not vid:
        return []
    facts: list[Fact] = [_entity_fact(vid, "village")]
    if (name := v.get("name_romaji")):
        facts.append(_value_fact(vid, "name_romaji", name))
    if (name_fr := v.get("name_fr")):
        facts.append(_value_fact(vid, "name_fr", name_fr))
    if (name_kanji := v.get("name_kanji")):
        facts.append(_value_fact(vid, "name_kanji", name_kanji))
    if (country := _str_or_none(v.get("country"))):
        facts.append(_entity_link(vid, "country", country))
    if (founded := v.get("founded_year")) is not None:
        facts.append(_value_fact(vid, "founded_year", founded))
    for kage in v.get("kage_lineage", []) or []:
        if not isinstance(kage, dict):
            continue
        if cid := _str_or_none(kage.get("character_id")):
            f = _entity_link(vid, "kage", cid)
            f.valid_from_year = kage.get("from_year")
            f.valid_to_year = kage.get("to_year")
            facts.append(f)
    for clan in _str_list(v, "main_clans"):
        facts.append(_entity_link(vid, "main_clan", clan))
    for spec in _str_list(v, "specialties"):
        facts.append(_value_fact(vid, "specialty", spec))
    return facts


def _facts_from_location(loc: dict[str, Any]) -> list[Fact]:
    lid = loc.get("id")
    if not isinstance(lid, str) or not lid:
        return []
    facts: list[Fact] = [_entity_fact(lid, "location")]
    if (name := loc.get("name_romaji")):
        facts.append(_value_fact(lid, "name_romaji", name))
    if (name_fr := loc.get("name_fr")):
        facts.append(_value_fact(lid, "name_fr", name_fr))
    if (country := _str_or_none(loc.get("country"))):
        facts.append(_value_fact(lid, "country", country))
    if (near := _str_or_none(loc.get("near_village"))):
        facts.append(_entity_link(lid, "near_village", near))
    return facts


def _facts_from_kekkei(k: dict[str, Any]) -> list[Fact]:
    kid = k.get("id")
    if not isinstance(kid, str) or not kid:
        return []
    facts: list[Fact] = [_entity_fact(kid, "kekkei_genkai" if k.get("category") != "kekkei_mora" else "kekkei_mora")]
    if (name := k.get("name_romaji")):
        facts.append(_value_fact(kid, "name_romaji", name))
    if (name_fr := k.get("name_fr")):
        facts.append(_value_fact(kid, "name_fr", name_fr))
    if (name_kanji := k.get("name_kanji")):
        facts.append(_value_fact(kid, "name_kanji", name_kanji))
    if (kt := k.get("type")):
        facts.append(_value_fact(kid, "kekkei_type", kt))
    for clan in _str_list(k, "carrier_clans"):
        facts.append(_entity_link(kid, "carried_by", clan))
    # kekkei_mora : evolution_paths + stages (canon byakugan -> tenseigan)
    for target in _str_list(k, "evolution_paths"):
        facts.append(_entity_link(kid, "evolves_to", target))
    for stage in k.get("stages", []) or []:
        if isinstance(stage, dict) and (st_num := stage.get("stage")) is not None:
            facts.append(_value_fact(kid, "has_stage", st_num))
    return facts


def _facts_from_organization(o: dict[str, Any]) -> list[Fact]:
    oid = o.get("id")
    if not isinstance(oid, str) or not oid:
        return []
    facts: list[Fact] = [_entity_fact(oid, "organization")]
    if (name := o.get("name_romaji")):
        facts.append(_value_fact(oid, "name_romaji", name))
    if (name_fr := o.get("name_fr")):
        facts.append(_value_fact(oid, "name_fr", name_fr))
    for hq in _str_list(o, "headquarters"):
        facts.append(_value_fact(oid, "headquarters", hq))
    for f_id in _str_list(o, "founders"):
        facts.append(_entity_link(oid, "founded_by", f_id))
    for leader in o.get("leaders_by_era", []) or []:
        if not isinstance(leader, dict):
            continue
        if (lid := _str_or_none(leader.get("leader"))):
            f = _entity_link(oid, "leader", lid)
            f.valid_from_year = leader.get("from_year")
            f.valid_to_year = leader.get("to_year")
            facts.append(f)
    # active_period : phases d'activite avec valid_from/to par phase
    for phase in o.get("active_period", []) or []:
        if not isinstance(phase, dict):
            continue
        phase_name = phase.get("phase") or "active"
        f = _value_fact(oid, "active_phase", phase_name)
        f.valid_from_year = phase.get("from_year")
        f.valid_to_year = phase.get("to_year")
        facts.append(f)
    # members_by_era : qui appartient a l'organisation a chaque era
    for entry in o.get("members_by_era", []) or []:
        if not isinstance(entry, dict):
            continue
        year = entry.get("year")
        for member_id in entry.get("members", []) or []:
            if not isinstance(member_id, str):
                continue
            f = _entity_link(oid, "has_member", member_id)
            f.valid_from_year = year
            facts.append(f)
    return facts


def _facts_from_tailed_beast(b: dict[str, Any]) -> list[Fact]:
    bid = b.get("id")
    if not isinstance(bid, str) or not bid:
        return []
    facts: list[Fact] = [_entity_fact(bid, "tailed_beast")]
    if (name := b.get("name_romaji")):
        facts.append(_value_fact(bid, "name_romaji", name))
    if (tails := b.get("tails")) is not None:
        facts.append(_value_fact(bid, "tails", tails))
    for entry in b.get("current_jinchuuriki_by_era", []) or []:
        if not isinstance(entry, dict):
            continue
        if (jid := _str_or_none(entry.get("jinchuuriki"))):
            f = _entity_link(bid, "current_jinchuriki", jid)
            f.valid_from_year = entry.get("from_year")
            f.valid_to_year = entry.get("to_year")
            facts.append(f)
    if (color := b.get("chakra_signature_color")):
        facts.append(_value_fact(bid, "chakra_signature_color", color))
    for ep in _str_list(b, "epithets"):
        facts.append(_value_fact(bid, "epithet", ep))
    return facts


def _facts_from_era(e: dict[str, Any]) -> list[Fact]:
    """Era canonique (Warring States, Founding Konoha, Shinobi Wars, etc.).

    Note : le canon utilise `year_start` / `year_end` (PAS `start_year` /
    `end_year`). Le bug initial d'inversion perdait les bornes temporelles
    des 13 eras canon.
    """
    eid = e.get("id")
    if not isinstance(eid, str) or not eid:
        return []
    facts: list[Fact] = [_entity_fact(eid, "era")]
    if (name := e.get("name_romaji")):
        facts.append(_value_fact(eid, "name_romaji", name))
    if (name_fr := e.get("name_fr")):
        facts.append(_value_fact(eid, "name_fr", name_fr))
    # Tolere les 2 conventions de nommage par robustesse
    year_start = e.get("year_start") if e.get("year_start") is not None \
        else e.get("start_year")
    year_end = e.get("year_end") if e.get("year_end") is not None \
        else e.get("end_year")
    if year_start is not None:
        f = _value_fact(eid, "year_start", year_start)
        f.valid_from_year = year_start
        facts.append(f)
    if year_end is not None:
        f = _value_fact(eid, "year_end", year_end)
        f.valid_to_year = year_end
        facts.append(f)
    # spans_period : fact avec validite globale [year_start, year_end]
    if year_start is not None or year_end is not None:
        f = _value_fact(eid, "spans_period", f"{year_start}..{year_end}")
        f.valid_from_year = year_start
        f.valid_to_year = year_end
        facts.append(f)
    for fig in _str_list(e, "key_figures"):
        facts.append(_entity_link(eid, "key_figure", fig))
    return facts


def _facts_from_hiden(h: dict[str, Any]) -> list[Fact]:
    """Hiden : technique secrete d'un clan ou d'un village."""
    hid = h.get("id")
    if not isinstance(hid, str) or not hid:
        return []
    facts: list[Fact] = [_entity_fact(hid, "hiden")]
    if (name := h.get("name_romaji")):
        facts.append(_value_fact(hid, "name_romaji", name))
    if (name_fr := h.get("name_fr")):
        facts.append(_value_fact(hid, "name_fr", name_fr))
    if (clan := _str_or_none(h.get("owning_clan"))):
        facts.append(_entity_link(hid, "owning_clan", clan))
    if (village := _str_or_none(h.get("owning_village"))):
        facts.append(_entity_link(hid, "owning_village", village))
    if (sharable := h.get("shareable_outside_clan")) is not None:
        facts.append(_value_fact(hid, "shareable_outside_clan", str(sharable).lower()))
    if (auth := h.get("shareable_with_authorization")) is not None:
        facts.append(_value_fact(hid, "shareable_with_authorization", str(auth).lower()))
    return facts


def _facts_from_nature(n: dict[str, Any]) -> list[Fact]:
    """Nature : element/affinite chakra (katon, suiton, ...).

    Spec Phase A : ne pas perdre type / strong_against / weak_against
    (relations canon entre natures, ex: katon strong_against fuuton).
    """
    nid = n.get("id")
    if not isinstance(nid, str) or not nid:
        return []
    facts: list[Fact] = [_entity_fact(nid, "nature")]
    if (name := n.get("name_romaji")):
        facts.append(_value_fact(nid, "name_romaji", name))
    if (name_fr := n.get("name_fr")):
        facts.append(_value_fact(nid, "name_fr", name_fr))
    if (name_kanji := n.get("name_kanji")):
        facts.append(_value_fact(nid, "name_kanji", name_kanji))
    if (ntype := n.get("type")):
        facts.append(_value_fact(nid, "nature_type", ntype))
    for clan in _str_list(n, "common_clans"):
        facts.append(_entity_link(nid, "common_in_clan", clan))
    for village in _str_list(n, "common_villages"):
        facts.append(_entity_link(nid, "common_in_village", village))
    for tgt in _str_list(n, "strong_against"):
        facts.append(_entity_link(nid, "strong_against", tgt))
    for tgt in _str_list(n, "weak_against"):
        facts.append(_entity_link(nid, "weak_against", tgt))
    return facts


def _facts_from_weapon(w: dict[str, Any]) -> list[Fact]:
    """Weapon/tool canonique.

    Note : la donnee canon utilise `wielders_canonical` (pas `canonical_users`).
    On accepte les 2 par robustesse forward-compat.
    """
    wid = w.get("id")
    if not isinstance(wid, str) or not wid:
        return []
    facts: list[Fact] = [_entity_fact(wid, "weapon")]
    if (name := w.get("name_romaji")):
        facts.append(_value_fact(wid, "name_romaji", name))
    if (name_fr := w.get("name_fr")):
        facts.append(_value_fact(wid, "name_fr", name_fr))
    if (wtype := w.get("type")):
        facts.append(_value_fact(wid, "weapon_type", wtype))
    if (rarity := w.get("rarity")):
        facts.append(_value_fact(wid, "rarity", rarity))
    # Defensive : tolere les 2 noms canon, valide list[str]
    wielders = (
        _str_list(w, "wielders_canonical")
        or _str_list(w, "canonical_users")
    )
    for owner in wielders:
        facts.append(_entity_link(wid, "has_canonical_user", owner))
    return facts


def _facts_from_rank(r: dict[str, Any]) -> list[Fact]:
    """Rank canonique (academy_student, genin, chunin, jonin, kage...)."""
    rid = r.get("id")
    if not isinstance(rid, str) or not rid:
        return []
    facts: list[Fact] = [_entity_fact(rid, "rank")]
    if (name := r.get("name_romaji")):
        facts.append(_value_fact(rid, "name_romaji", name))
    if (name_fr := r.get("name_fr")):
        facts.append(_value_fact(rid, "name_fr", name_fr))
    if (name_kanji := r.get("name_kanji")):
        facts.append(_value_fact(rid, "name_kanji", name_kanji))
    if (level := r.get("level")) is not None:
        facts.append(_value_fact(rid, "level", level))
    if (min_age := r.get("min_age")) is not None:
        facts.append(_value_fact(rid, "min_age", min_age))
    if (max_age := r.get("typical_max_age")) is not None:
        facts.append(_value_fact(rid, "typical_max_age", max_age))
    return facts


def _facts_from_jutsu_category(c: dict[str, Any]) -> list[Fact]:
    """Categorie de jutsu (ninjutsu, taijutsu, genjutsu, fuinjutsu...)."""
    cid = c.get("id")
    if not isinstance(cid, str) or not cid:
        return []
    facts: list[Fact] = [_entity_fact(cid, "jutsu_category")]
    if (name := c.get("name_romaji")):
        facts.append(_value_fact(cid, "name_romaji", name))
    if (name_fr := c.get("name_fr")):
        facts.append(_value_fact(cid, "name_fr", name_fr))
    if (diff := c.get("typical_difficulty")) is not None:
        facts.append(_value_fact(cid, "typical_difficulty", diff))
    return facts


def _facts_from_arc_anchor(arc_id: str, arc: dict[str, Any]) -> list[Fact]:
    """Arc temporal anchor (chunin_exam, akatsuki_suppression, etc.).

    Spec : year_min/year_max delimitent la periode canonique de l'arc.
    """
    if not arc_id:
        return []
    facts: list[Fact] = [_entity_fact(arc_id, "arc")]
    if (desc := arc.get("description")):
        facts.append(_value_fact(arc_id, "description_fr", desc))
    year_min = arc.get("year_min")
    year_max = arc.get("year_max")
    if year_min is not None or year_max is not None:
        # Anchor temporel : valid_from/valid_to bornent l'arc
        f = _value_fact(arc_id, "spans_period", f"{year_min}..{year_max}")
        f.valid_from_year = year_min
        f.valid_to_year = year_max
        facts.append(f)
    if year_min is not None:
        facts.append(_value_fact(arc_id, "year_min", year_min))
    if year_max is not None:
        facts.append(_value_fact(arc_id, "year_max", year_max))
    return facts


def _str_or_none(value: Any) -> str | None:
    """Helper defensif : retourne value si str non-vide, sinon None.

    Spec Phase A round 45 : les references inter-entites (character_id,
    location_id, owning_clan, near_village, country, etc.) sont attendues
    string. Si type incorrect (int/list/dict), skip plutot que d'emettre
    un fact malforme.
    """
    if isinstance(value, str) and value:
        return value
    return None


def _str_list(item: dict[str, Any], field: str) -> list[str]:
    """Helper defensif : retourne item[field] s'il est une list[str], sinon [].

    Spec Phase A round 43 : les builders iteraient `for x in item.get(field, []) or []`
    sans valider le type. Si le field etait corrompu en str (ex:
    'sharingan' au lieu de ['sharingan']), Python iterait les caracteres
    -> 9 facts incorrects. Ce helper garantit que seules les vraies listes
    de strings sont iterees.
    """
    if not isinstance(item, dict):
        return []
    raw = item.get(field, [])
    if not isinstance(raw, list):
        return []
    return [x for x in raw if isinstance(x, str) and x]


def _null_fact(subject: str, relation: str) -> Fact:
    """Triplet (subject, relation, NULL) pour cas explicit-absence canon."""
    return Fact(
        subject=subject, relation=relation, object=None,
        object_type=ObjectType.value,
        source=FactSource.canon.value, canonicity=Canonicity.canon_strict,
    )


def _facts_from_world_rules(rules: dict[str, Any]) -> list[Fact]:
    """Flatten world_rules.json en facts atomiques.

    Structure : { category: { sous_cle: valeur ou dict } } -> facts
    `(world_rules, <category>:<key>, <value>)`. Les dicts imbriques sont
    flattens recursivement (1-2 niveaux suffisent pour le canon actuel).

    Spec Phase A : les valeurs `null` du JSON sont stockees comme NULL SQL
    (object=None), pas comme la chaine litterale "None". Ex :
    economy.ryo_to_jutsu_scroll_multiplier_by_rank.forbidden = null
    -> Fact(object=None) pour preserver la semantique "rang interdit, pas
    de multiplier applicable".
    """
    facts: list[Fact] = [_entity_fact("world_rules", "world_rules")]
    if not isinstance(rules, dict):
        return facts

    def _emit(relation: str, value: Any) -> None:
        if value is None:
            facts.append(_null_fact("world_rules", relation))
        else:
            facts.append(_value_fact("world_rules", relation, value))

    for category, content in rules.items():
        if not isinstance(content, dict):
            _emit(str(category), content)
            continue
        for key, value in content.items():
            if isinstance(value, dict):
                # Niveau 2 : flatten cle imbriquee
                for sub_key, sub_val in value.items():
                    _emit(f"{category}:{key}:{sub_key}", sub_val)
            elif isinstance(value, list):
                relation = f"{category}:{key}"
                for v in value:
                    _emit(relation, v)
            else:
                _emit(f"{category}:{key}", value)
    return facts


def _facts_from_voice_profile(v: dict[str, Any]) -> list[Fact]:
    """Voice profile canon : tic verbal, registre, phrases canoniques.

    Spec Phase A : "100% des facts canon importes sans perte". Importe :
    - register_fr : registre de langue
    - verbal_tics : tics verbaux ("dattebayo", "Itai!")
    - vocabulary_themes : themes lexicaux preferes
    - syntactic_patterns : patterns de phrases
    - sample_lines : phrases canoniques attestees (anti-hallucination LLM)
    - do_not_use : anti-patterns canon (interdits LLM)
    """
    vid = v.get("id")
    cid = _str_or_none(v.get("character_id"))
    if not isinstance(vid, str) or not vid:
        return []
    facts: list[Fact] = [_entity_fact(vid, "voice_profile")]
    if cid:
        facts.append(_entity_link(vid, "voice_for_character", cid))
    if (reg := v.get("register_fr")):
        facts.append(_value_fact(vid, "register_fr", reg))

    def _list_of_str(field: str) -> list[str]:
        # Defensive : si field est str/dict/int, retourne []. Evite que
        # `for tic in 'dattebayo'` itere les caracteres comme facts.
        raw = v.get(field, []) or []
        if not isinstance(raw, list):
            return []
        return [x for x in raw if isinstance(x, str)]

    for tic in _list_of_str("verbal_tics"):
        facts.append(_value_fact(vid, "verbal_tic", tic))
        # Lien direct sur le character pour requete intuitive
        if cid:
            facts.append(_value_fact(cid, "verbal_tic", tic))
    for theme in _list_of_str("vocabulary_themes"):
        facts.append(_value_fact(vid, "vocabulary_theme", theme))
    for pat in _list_of_str("syntactic_patterns"):
        facts.append(_value_fact(vid, "syntactic_pattern", pat))
    # Spec Phase A : phrases canoniques + anti-patterns (152 + 88 entries)
    for line in _list_of_str("sample_lines"):
        facts.append(_value_fact(vid, "sample_line", line))
    for forbidden in _list_of_str("do_not_use"):
        facts.append(_value_fact(vid, "do_not_use", forbidden))
    return facts


def _facts_from_event(e: dict[str, Any]) -> list[Fact]:
    eid = e.get("id")
    if not isinstance(eid, str) or not eid:
        return []
    facts: list[Fact] = [_entity_fact(eid, "timeline_event")]
    if (name := e.get("name_fr")):
        facts.append(_value_fact(eid, "name_fr", name))
    if (year := e.get("year")) is not None:
        f = _value_fact(eid, "occurs_in_year", year)
        f.valid_from_year = year
        facts.append(f)
    if (date := e.get("date")):
        # Format MM-DD (granularite mois/jour)
        facts.append(_value_fact(eid, "occurs_on_date", date))
    if (loc := _str_or_none(e.get("location"))):
        facts.append(_entity_link(eid, "occurs_at", loc))
    for ch in _str_list(e, "involved_characters"):
        facts.append(_entity_link(eid, "involves", ch))
    # cancellation_strategy : type canon de gestion du cancel pour la
    # timeline divergente. Types : hard_cancel, cascade_cancel, delay,
    # substitute. Spec §8 (world simulation) : determine ce qui se passe si
    # le joueur previent l'event.
    cs = e.get("cancellation_strategy")
    if isinstance(cs, dict):
        if (cs_type := cs.get("type")):
            facts.append(_value_fact(eid, "cancellation_strategy", cs_type))
    # outcomes : effets canon de l'event (character_death, war_started, etc.)
    # Stocke type comme relation; primary param (character_id si present)
    # comme object pour requetabilite. Params complets en JSON pour preserver.
    for outcome in e.get("outcomes", []) or []:
        # Defensive : skip si outcome corrupte (None / scalaire)
        if not isinstance(outcome, dict):
            continue
        otype = outcome.get("type")
        params = outcome.get("parameters") or {}
        if not isinstance(params, dict):
            params = {}
        if not otype:
            continue
        # Defensive : valide que les params primary sont strings
        primary_str = (
            _str_or_none(params.get("character_id"))
            or _str_or_none(params.get("village_id"))
            or _str_or_none(params.get("organization_id"))
            or _str_or_none(params.get("location_id"))
        )
        is_entity = primary_str is not None
        primary = primary_str or json.dumps(
            params, ensure_ascii=False, sort_keys=True,
        )
        f = Fact(
            subject=eid,
            relation=f"outcome:{otype}",
            object=str(primary),
            object_type=ObjectType.entity if is_entity else ObjectType.value,
            source=FactSource.canon.value,
            canonicity=Canonicity.canon_strict,
        )
        if (year := e.get("year")) is not None:
            f.valid_from_year = year
        facts.append(f)
    # preconditions : prerequis canon (character_alive, has_age, etc.)
    for precond in e.get("preconditions", []) or []:
        # Defensive : skip si precond corrupte
        if not isinstance(precond, dict):
            continue
        ptype = precond.get("type")
        params = precond.get("parameters") or {}
        if not isinstance(params, dict):
            params = {}
        if not ptype:
            continue
        primary_str = (
            _str_or_none(params.get("character_id"))
            or _str_or_none(params.get("village_id"))
        )
        is_entity = primary_str is not None
        primary = primary_str or json.dumps(
            params, ensure_ascii=False, sort_keys=True,
        )
        facts.append(Fact(
            subject=eid,
            relation=f"requires:{ptype}",
            object=str(primary),
            object_type=ObjectType.entity if is_entity else ObjectType.value,
            source=FactSource.canon.value,
            canonicity=Canonicity.canon_strict,
        ))
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
    # Spec Phase A : dedupe global cross-dataset. Ex : 'anbu' apparait dans
    # organizations.json ET ranks.json ; leurs facts non-distincts (name_fr,
    # has_source) seraient sinon inseres 2 fois. Tracker partage entre tous
    # les _import_list calls dans cette session.
    _seen_keys: set[tuple] = set()

    def _filter_seen(facts: list[Fact]) -> list[Fact]:
        out: list[Fact] = []
        for f in facts:
            key = (
                f.subject, f.relation, f.object,
                f.valid_from_year, f.valid_to_year, f.source,
            )
            if key in _seen_keys:
                continue
            _seen_keys.add(key)
            out.append(f)
        return out

    # Helper local
    def _import_list(filename: str, fact_builder, label: str) -> None:
        path = canon / filename
        if not path.exists():
            stats[label] = 0
            return
        # Idempotence (clear_first=False) : detecte si dataset deja importe
        # via marker fact "(any subject, type, <type_label>)". Skip si trouve.
        if not clear_first:
            type_label = label.rstrip("s") if label.endswith("s") else label
            # Mapping cas particuliers ou type singular != label
            type_map = {
                "kekkei_genkai": "kekkei_genkai",
                "kekkei_mora": "kekkei_mora",
                "tailed_beasts": "tailed_beast",
                "voice_profiles": "voice_profile",
                "jutsu_categories": "jutsu_category",
                "weapons_tools": "weapon",
                "timeline_events": "timeline_event",
            }
            type_label = type_map.get(label, type_label)
            if store.count() > 0 and len(store.get_facts(
                relation="type", object_value=type_label, limit=1,
            )) > 0:
                stats[label] = 0
                return
        # Spec Phase A robustesse : JSON corrompu ne doit pas casser tout
        # l'import canon. On log et skip ce dataset, les autres continuent.
        try:
            items = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
            logger.warning(
                "kg_import_json_corrupt", file=filename, error=str(exc),
            )
            stats[label] = 0
            return
        if not isinstance(items, list):
            stats[label] = 0
            return
        all_facts: list[Fact] = []
        for item in items:
            try:
                item_facts = fact_builder(item)
                # Spec Phase A : preserver canonicity source par entree.
                # Si l'entree a un `canonicity` declare, on :
                # 1. Emet un fact (entity, sourced_from, "manga"|"filler"|...)
                # 2. Override la canonicity runtime de TOUS les facts de
                #    l'entree (filler/game/tbv -> canon_modified)
                raw_canon = item.get("canonicity") if isinstance(item, dict) else None
                mapped = _map_canonicity(raw_canon)
                if mapped != Canonicity.canon_strict:
                    for f in item_facts:
                        f.canonicity = mapped
                eid = item.get("id") if isinstance(item, dict) else None
                if eid:
                    sf = _source_canonicity_fact(eid, raw_canon)
                    if sf is not None:
                        item_facts.append(sf)
                    # Spec Phase A : preserver les refs narutopedia/databook
                    item_facts.extend(_source_refs_facts(eid, item.get("sources")))
                    # Spec Phase A : preserver updated_at (audit trail canon)
                    ua = _updated_at_fact(eid, item.get("updated_at"))
                    if ua is not None:
                        item_facts.append(ua)
                    # Spec Phase A : preserver les champs descriptifs *_fr canon
                    item_facts.extend(_text_fr_facts(eid, item))
                all_facts.extend(item_facts)
            except Exception as exc:
                logger.warning("kg_import_item_failed", file=filename, error=str(exc))
        # Spec Phase A : dedupe global (intra-dataset + cross-dataset)
        all_facts = _filter_seen(all_facts)
        store.add_facts_batch(all_facts)
        stats[label] = len(all_facts)

    # characters.json : applique d'abord character_birth_years_patch.json
    # (208 birth_year + 97 death_year canon corrections, ex: Naruto, Sasuke...)
    # Spec Phase A : "100% des facts canon importes sans perte".
    chars_path = canon / "characters.json"
    patch_path = canon / "character_birth_years_patch.json"
    chars_already = (
        not clear_first and store.count() > 0 and
        len(store.get_facts(relation="type", object_value="character", limit=1)) > 0
    )
    if chars_already:
        stats["characters"] = 0
        stats["birth_years_patched"] = 0
    elif chars_path.exists():
        try:
            chars = json.loads(chars_path.read_text(encoding="utf-8"))
            patch_applied = 0
            if patch_path.exists() and isinstance(chars, list):
                patch_data = json.loads(patch_path.read_text(encoding="utf-8"))
                patches = patch_data.get("patches", {}) if isinstance(patch_data, dict) else {}
                if isinstance(patches, dict) and patches:
                    by_id = {c.get("id"): c for c in chars if isinstance(c, dict)}
                    for cid, fields in patches.items():
                        if not isinstance(fields, dict):
                            continue
                        char = by_id.get(cid)
                        if char is None:
                            continue
                        # Ne remplace que les champs absents (jamais override)
                        for k in ("birth_year", "death_year"):
                            if char.get(k) is None and k in fields:
                                char[k] = fields[k]
                                patch_applied += 1
            stats["birth_years_patched"] = patch_applied
            # Reutilise _import_list mais sur la liste deja patchee
            all_facts: list[Fact] = []
            for item in chars:
                if not isinstance(item, dict):
                    continue
                try:
                    item_facts = _facts_from_character(item)
                    raw_canon = item.get("canonicity")
                    mapped = _map_canonicity(raw_canon)
                    if mapped != Canonicity.canon_strict:
                        for f in item_facts:
                            f.canonicity = mapped
                    eid = item.get("id")
                    if eid:
                        sf = _source_canonicity_fact(eid, raw_canon)
                        if sf is not None:
                            item_facts.append(sf)
                        # Refs narutopedia/databook canon
                        item_facts.extend(
                            _source_refs_facts(eid, item.get("sources"))
                        )
                        # Audit trail canon
                        ua = _updated_at_fact(eid, item.get("updated_at"))
                        if ua is not None:
                            item_facts.append(ua)
                        # Champs descriptifs *_fr canon (personality_fr, etc.)
                        item_facts.extend(_text_fr_facts(eid, item))
                    all_facts.extend(item_facts)
                except Exception as exc:
                    logger.warning("kg_import_item_failed", file="characters.json",
                                   error=str(exc))
            all_facts = _filter_seen(all_facts)
            store.add_facts_batch(all_facts)
            stats["characters"] = len(all_facts)
        except Exception as exc:
            logger.warning("kg_import_characters_failed", error=str(exc))
            stats["characters"] = 0
    else:
        stats["characters"] = 0
        stats["birth_years_patched"] = 0
    _import_list("techniques.json", _facts_from_technique, "techniques")
    _import_list("clans.json", _facts_from_clan, "clans")
    _import_list("villages.json", _facts_from_village, "villages")
    _import_list("locations.json", _facts_from_location, "locations")
    _import_list("kekkei_genkai.json", _facts_from_kekkei, "kekkei_genkai")
    _import_list("kekkei_mora.json", _facts_from_kekkei, "kekkei_mora")
    _import_list("organizations.json", _facts_from_organization, "organizations")
    _import_list("tailed_beasts.json", _facts_from_tailed_beast, "tailed_beasts")
    _import_list("timeline_events.json", _facts_from_event, "timeline_events")
    # Spec Phase A : "100% des facts canon importes sans perte"
    _import_list("eras.json", _facts_from_era, "eras")
    _import_list("hiden.json", _facts_from_hiden, "hiden")
    _import_list("natures.json", _facts_from_nature, "natures")
    _import_list("weapons_tools.json", _facts_from_weapon, "weapons_tools")
    _import_list("ranks.json", _facts_from_rank, "ranks")
    _import_list("jutsu_categories.json", _facts_from_jutsu_category, "jutsu_categories")
    _import_list("voice_profiles.json", _facts_from_voice_profile, "voice_profiles")

    # arc_temporal_anchors.json : structure dict, pas list -> import dedie
    arc_path = canon / "arc_temporal_anchors.json"
    arc_already = (
        not clear_first and store.count() > 0 and
        len(store.get_facts(relation="type", object_value="arc", limit=1)) > 0
    )
    if arc_already:
        stats["arc_anchors"] = 0
    elif arc_path.exists():
        try:
            data = json.loads(arc_path.read_text(encoding="utf-8"))
            arcs = data.get("arcs", {}) if isinstance(data, dict) else {}
            arc_facts: list[Fact] = []
            for arc_id, arc in arcs.items():
                # Defensive : arc_id doit etre un id string valide
                if not isinstance(arc_id, str) or not arc_id:
                    continue
                if isinstance(arc, dict):
                    arc_facts.extend(_facts_from_arc_anchor(arc_id, arc))
            arc_facts = _filter_seen(arc_facts)
            store.add_facts_batch(arc_facts)
            stats["arc_anchors"] = len(arc_facts)
        except Exception as exc:
            logger.warning("kg_import_arc_anchors_failed", error=str(exc))
            stats["arc_anchors"] = 0
    else:
        stats["arc_anchors"] = 0

    # world_rules.json : regles canon (chakra pools, combat formulas, economy)
    # Structure : dict imbrique 2-3 niveaux. On flatten en facts atomiques.
    rules_path = canon / "world_rules.json"
    rules_already = (
        not clear_first and store.count() > 0 and
        len(store.get_facts(subject="world_rules", limit=1)) > 0
    )
    if rules_already:
        stats["world_rules"] = 0
    elif rules_path.exists():
        try:
            rules = json.loads(rules_path.read_text(encoding="utf-8"))
            rule_facts = _facts_from_world_rules(rules)
            rule_facts = _filter_seen(rule_facts)
            store.add_facts_batch(rule_facts)
            stats["world_rules"] = len(rule_facts)
        except Exception as exc:
            logger.warning("kg_import_world_rules_failed", error=str(exc))
            stats["world_rules"] = 0
    else:
        stats["world_rules"] = 0

    # psycho_notes.json : notes psy par tranche d'age + relations canoniques.
    # Spec Phase A : canon-derive (forbidden_relations notamment) doit etre dans
    # le KG pour servir de contraintes au moteur. allowed_relations sont deja
    # extraites en social_links par bootstrap_social_network_from_canon().
    psycho_path = canon / "psycho_notes.json"
    psycho_already = (
        not clear_first and store.count() > 0 and
        len(store.get_facts(relation="psycho_note", limit=1)) > 0
    )
    if psycho_already:
        stats["psycho_notes"] = 0
    elif psycho_path.exists():
        try:
            data = json.loads(psycho_path.read_text(encoding="utf-8"))
            notes = data.get("notes", {}) if isinstance(data, dict) else {}
            # Map birth_years pour conversion age -> year absolue
            birth_year_map: dict[str, int] = {}
            for f in store.get_facts(relation="birth_year"):
                try:
                    birth_year_map[f.subject] = int(f.object) if f.object else None  # type: ignore
                except (ValueError, TypeError):
                    pass
            psy_facts: list[Fact] = []
            for npc_id, entries in notes.items():
                # Defensive : npc_id doit etre un id string valide pour
                # constituer un subject de fact. Skip les corruptions.
                if not isinstance(npc_id, str) or not npc_id:
                    continue
                if not isinstance(entries, list):
                    continue
                by = birth_year_map.get(npc_id)
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    from_age = entry.get("from_age")
                    to_age = entry.get("to_age")
                    vf = (by + from_age) if (by is not None and isinstance(from_age, int)) else None
                    vt = (by + to_age) if (by is not None and isinstance(to_age, int)) else None
                    if (note_text := entry.get("note")):
                        f = _value_fact(npc_id, "psycho_note", note_text)
                        f.valid_from_year = vf
                        f.valid_to_year = vt
                        psy_facts.append(f)
                    # forbidden_relations : contrainte canon "X n'a pas de
                    # relation avec Y a cet age". Format string : "id (annot)"
                    for rel_str in entry.get("forbidden_relations", []) or []:
                        if not isinstance(rel_str, str):
                            continue
                        # Extrait l'id avant la parenthese (ex: 'uchiha_sasuke (rivalite naitra plus tard)' -> 'uchiha_sasuke')
                        target_id = rel_str.split("(", 1)[0].strip()
                        if not target_id or target_id == npc_id:
                            continue
                        # Skip cas non-id (ex: 'tous les autres enfants')
                        if " " in target_id or not target_id.replace("_", "").isalnum():
                            continue
                        f = Fact(
                            subject=npc_id, relation="forbidden_relation_to",
                            object=target_id, object_type=ObjectType.entity,
                            source=FactSource.canon.value,
                            canonicity=Canonicity.canon_strict,
                        )
                        f.valid_from_year = vf
                        f.valid_to_year = vt
                        psy_facts.append(f)
            psy_facts = _filter_seen(psy_facts)
            store.add_facts_batch(psy_facts)
            stats["psycho_notes"] = len(psy_facts)
        except Exception as exc:
            logger.warning("kg_import_psycho_notes_failed", error=str(exc))
            stats["psycho_notes"] = 0
    else:
        stats["psycho_notes"] = 0

    # missions.json : import via Sprint MISSIONS architecture (source='mission:<id>')
    # Spec Phase A : pipeline canon unifie, 100% sans perte. Idempotent : skip
    # si missions deja importees (clear_first=False).
    missions_path = canon / "missions.json"
    if missions_path.exists():
        already_have_missions = (
            not clear_first and store.count(source_prefix="mission:") > 0
        )
        if already_have_missions:
            stats["missions"] = 0
            stats["missions_count"] = 0
        else:
            try:
                from shinobi.missions.catalog import MissionCatalog
                from shinobi.missions.kg_integration import import_missions_to_kg

                catalog = MissionCatalog.from_json_file(missions_path)
                if catalog.count > 0:
                    m_stats = import_missions_to_kg(
                        store, catalog.all(), clear_first=False,
                    )
                    stats["missions"] = m_stats.get("facts_inserted", 0)
                    stats["missions_count"] = m_stats.get("missions_imported", 0)
                else:
                    stats["missions"] = 0
                    stats["missions_count"] = 0
            except Exception as exc:
                logger.warning("kg_import_missions_failed", error=str(exc))
                stats["missions"] = 0
                stats["missions_count"] = 0
    else:
        stats["missions"] = 0
        stats["missions_count"] = 0

    # Bug round 20 : `total = sum(stats.values())` incluait des compteurs
    # non-facts (birth_years_patched, missions_count) -> ecart vs store.count().
    # Spec Phase A : `total` doit refleter le nombre reel de facts en base.
    # On exclut les cles meta (compteurs d'entites/patches, pas de facts).
    _meta_keys = {"birth_years_patched", "missions_count"}
    total = sum(v for k, v in stats.items() if k not in _meta_keys)
    logger.info("kg_import_complete", total_facts=total, **stats)
    stats["total"] = total
    return stats


__all__ = ["import_canon_to_kg"]
