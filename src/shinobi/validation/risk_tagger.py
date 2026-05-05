"""Pilier 7.1 : risk-tagger.

Decoupe une `NarrativeOutput` en segments classifies par risque,
pour permettre au Validator d'activer plus ou moins de couches selon
le segment :

  risk=low        : couche A seule (prose generique, aucune entite canon)
  risk=medium     : couches A + C (dialogue ou prose avec 1 entite canon)
  risk=high       : couches A + B + C (prose avec >= 2 entites canon ou
                    factual_claim non actionnable)
  risk=very_high  : couches A + B + C + D (action avec actor+jutsu :
                    triplet_check obligatoire)

Implementation MVP : regex + comptage d'entites canon contre les enums
extraits par scripts/pass6_extract_enums.py. Pas de classifier ML pour
l'instant ; il pourra etre ajoute (CRAG-style 0.5B) plus tard sans
casser cette interface.

Le tagger ne valide rien, il oriente la verification.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from shinobi.validation.validator import NarrativeOutput

ROOT = Path(__file__).resolve().parents[3]
CANON_ENUMS_DIR = ROOT / "data" / "canon"


class RiskLevel(StrEnum):
    low = "low"
    medium = "medium"
    high = "high"
    very_high = "very_high"


class SegmentType(StrEnum):
    prose_descriptive = "prose_descriptive"
    dialogue = "dialogue"
    factual_claim = "factual_claim"
    action = "action"


@dataclass(frozen=True)
class RiskSegment:
    """Un segment tagge par le risk-tagger."""

    type: SegmentType
    risk_level: RiskLevel
    text: str
    matched_entities: tuple[str, ...]  # ids canon detectes
    actor: str | None = None           # pour les actions seulement
    jutsu: str | None = None           # pour les actions seulement


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _load_enum_ids(name: str) -> set[str]:
    """Charge les ids depuis data/canon/<name>.json. Retourne set vide si manquant."""
    path = CANON_ENUMS_DIR / name
    if not path.exists():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    return {e["id"] for e in data if isinstance(e, dict) and "id" in e}


# Lazy-loaded pour ne pas payer au import
_CHAR_IDS: set[str] | None = None
_JUTSU_IDS: set[str] | None = None
_LOCATION_IDS: set[str] | None = None
_VILLAGE_IDS: set[str] | None = None
_CLAN_IDS: set[str] | None = None
_KG_IDS: set[str] | None = None


def _get_canon_ids() -> dict[str, set[str]]:
    global _CHAR_IDS, _JUTSU_IDS, _LOCATION_IDS, _VILLAGE_IDS, _CLAN_IDS, _KG_IDS
    if _CHAR_IDS is None:
        _CHAR_IDS = _load_enum_ids("character_list.json")
        _JUTSU_IDS = _load_enum_ids("jutsu_list.json")
        _LOCATION_IDS = _load_enum_ids("location_list.json")
        _VILLAGE_IDS = _load_enum_ids("village_list.json")
        _CLAN_IDS = _load_enum_ids("clan_list.json")
        _KG_IDS = _load_enum_ids("kekkei_genkai_list.json")
    return {
        "characters": _CHAR_IDS or set(),
        "jutsus": _JUTSU_IDS or set(),
        "locations": _LOCATION_IDS or set(),
        "villages": _VILLAGE_IDS or set(),
        "clans": _CLAN_IDS or set(),
        "kekkei_genkai": _KG_IDS or set(),
    }


def _scan_canon_entities(text: str, canon: dict[str, set[str]]) -> tuple[str, ...]:
    """Detecte les ids canon presents dans `text`.

    Match strict : separateurs de mots autour de l'id snake_case. Evite les
    faux positifs sur sous-chaines ('naruto' dans 'narutopedia').
    """
    if not text:
        return ()
    found: list[str] = []
    lc = text.lower()
    for kind in ("characters", "jutsus", "locations", "villages", "clans", "kekkei_genkai"):
        for cid in canon[kind]:
            if cid in lc:
                # `_` traite comme alphanumeric pour eviter que 'uchiha' matche
                # dans 'uchiha_itachi'.
                pattern = r"(?<![a-z0-9_])" + re.escape(cid) + r"(?![a-z0-9_])"
                if re.search(pattern, lc):
                    found.append(cid)
    seen: set[str] = set()
    out: list[str] = []
    for e in found:
        if e not in seen:
            seen.add(e)
            out.append(e)
    return tuple(out)


def _split_sentences(text: str) -> list[str]:
    if not text:
        return []
    parts = _SENTENCE_SPLIT_RE.split(text.strip())
    return [p for p in (p.strip() for p in parts) if p]


def tag_narrative_output(output: NarrativeOutput) -> list[RiskSegment]:
    """Tag tous les segments d'une `NarrativeOutput`.

    Order : actions > dialogues > prose. Les actions arrivent en premier
    car ce sont les segments les plus haut risque (triplet check candidate).
    """
    canon = _get_canon_ids()
    segments: list[RiskSegment] = []

    for action in output.actions:
        actor = action.actor
        jutsu = action.jutsu
        text = action.label_fr or f"{actor} {action.type or ''} {jutsu or ''}".strip()
        entities = []
        if actor:
            entities.append(actor)
        if jutsu:
            entities.append(jutsu)
        if action.location:
            entities.append(action.location)
        if action.target:
            entities.append(action.target)

        if actor and jutsu:
            risk = RiskLevel.very_high
        elif actor or jutsu:
            risk = RiskLevel.high
        else:
            risk = RiskLevel.medium
        segments.append(RiskSegment(
            type=SegmentType.action,
            risk_level=risk,
            text=text,
            matched_entities=tuple(e for e in entities if e),
            actor=actor,
            jutsu=jutsu,
        ))

    for d in output.npc_dialogue:
        line = d.line or ""
        entities = _scan_canon_entities(line, canon)
        if len(entities) >= 2:
            risk = RiskLevel.high
        elif len(entities) == 1:
            risk = RiskLevel.medium
        else:
            risk = RiskLevel.low
        segments.append(RiskSegment(
            type=SegmentType.dialogue,
            risk_level=risk,
            text=line,
            matched_entities=entities,
        ))

    for sentence in _split_sentences(output.narrative or ""):
        entities = _scan_canon_entities(sentence, canon)
        if len(entities) >= 2:
            risk = RiskLevel.high
            stype = SegmentType.factual_claim
        elif len(entities) == 1:
            risk = RiskLevel.medium
            stype = SegmentType.factual_claim
        else:
            risk = RiskLevel.low
            stype = SegmentType.prose_descriptive
        segments.append(RiskSegment(
            type=stype,
            risk_level=risk,
            text=sentence,
            matched_entities=entities,
        ))

    return segments


def required_layers_for_risk(risk: RiskLevel) -> tuple[str, ...]:
    """Map risk_level -> couches de validation a activer.

    Cf. v2.md §7.2. Aujourd'hui les couches D et E ne sont pas livrees,
    mais on retourne deja leurs noms pour que le Validator puisse les
    filtrer / loguer comme 'reportees'.
    """
    if risk == RiskLevel.low:
        return ("sherlock_rules",)
    if risk == RiskLevel.medium:
        return ("sherlock_rules", "age_coherence")
    if risk == RiskLevel.high:
        return ("sherlock_rules", "triplet_check", "age_coherence")
    return ("sherlock_rules", "triplet_check", "age_coherence", "nli", "llm_judge")


def max_risk_in(segments: list[RiskSegment]) -> RiskLevel:
    """Retourne le risk_level maximum sur une liste de segments."""
    order = [RiskLevel.low, RiskLevel.medium, RiskLevel.high, RiskLevel.very_high]
    if not segments:
        return RiskLevel.low
    return max(segments, key=lambda s: order.index(s.risk_level)).risk_level
