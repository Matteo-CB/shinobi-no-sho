"""Pilier 6 phase B : conversion d'un dict LLM brut en NarrativeOutput valide.

Approche Pydantic-based : valide le shape (champs requis, types, taille),
puis valide les enums canon (character_id, jutsu, location, village)
contre les listes extraites par scripts/pass6_extract_enums.py.

Ne fait PAS de constrained decoding au niveau token. C'est de la
post-validation : si le LLM produit un id hors enum, l'erreur remonte
au pipeline qui peut alors regen avec feedback structure.

Le contrat est :
    parse_narrative_output(raw_dict) -> NarrativeOutput
ou
    raise StructuredOutputError(violations=[...])

Les violations contiennent assez de detail pour etre re-injectees dans
le prompt de retry (cf. validation/regen_loop.py format_violations_for_regen).
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from shinobi.validation.validator import (
    NarrativeAction,
    NarrativeDialogue,
    NarrativeOutput,
)

ROOT = Path(__file__).resolve().parents[3]
CANON_ENUMS_DIR = ROOT / "data" / "canon"


@dataclass
class StructuredOutputError(Exception):
    """Erreur de validation structure ou enum."""

    violations: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return f"StructuredOutputError({len(self.violations)} violations) : " + " | ".join(self.violations)


_ENUMS_CACHE: dict[str, set[str]] | None = None


def _load_enums() -> dict[str, set[str]]:
    """Charge en lazy-init les enums canon. Retourne sets vides si manquants."""
    global _ENUMS_CACHE
    if _ENUMS_CACHE is not None:
        return _ENUMS_CACHE

    def _load(name: str) -> set[str]:
        path = CANON_ENUMS_DIR / name
        if not path.exists():
            return set()
        data = json.loads(path.read_text(encoding="utf-8"))
        return {e["id"] for e in data if isinstance(e, dict) and "id" in e}

    _ENUMS_CACHE = {
        "characters": _load("character_list.json"),
        "jutsus": _load("jutsu_list.json"),
        "locations": _load("location_list.json"),
        "villages": _load("village_list.json"),
        "clans": _load("clan_list.json"),
        "kekkei_genkai": _load("kekkei_genkai_list.json"),
    }
    return _ENUMS_CACHE


_GENERIC_ROLE_PREFIXES: tuple[str, ...] = (
    "sensei_", "marchand_", "garde_", "civil_", "pnj_",
    "villageois_", "enfant_", "inconnu_",
    "jonin_", "chunin_", "genin_", "anbu_",
)


def _is_generic_role(s: str | None) -> bool:
    if not s:
        return False
    return s.startswith(_GENERIC_ROLE_PREFIXES)


def _validate_id_against_enum(
    value: str | None, enum_kind: str, *, allow_generic: bool = False,
) -> str | None:
    """Retourne un message de violation si value n'appartient pas a l'enum.

    Si allow_generic=True, accepte les ids commencant par un prefixe de
    role generique (sensei_..., marchand_..., etc.). Sinon, exige le canon.

    Si value est None ou vide, pas de validation (champ optionnel).
    """
    if not value:
        return None
    if allow_generic and _is_generic_role(value):
        return None
    enums = _load_enums()
    valid_ids = enums.get(enum_kind, set())
    if not valid_ids:
        # Enums non charges : tolerant, on laisse passer.
        return None
    if value not in valid_ids:
        return f"{enum_kind}: '{value}' inconnu du canon"
    return None


def _validate_dialogue_ids(
    dialogues: Iterable[NarrativeDialogue],
) -> list[str]:
    out: list[str] = []
    for d in dialogues:
        msg = _validate_id_against_enum(d.character_id, "characters", allow_generic=True)
        if msg:
            out.append(f"npc_dialogue.{msg}")
    return out


def _validate_action_ids(actions: Iterable[NarrativeAction], *, kind: str) -> list[str]:
    out: list[str] = []
    for a in actions:
        msg = _validate_id_against_enum(a.actor, "characters", allow_generic=True)
        if msg:
            out.append(f"{kind}.{msg}")
        msg = _validate_id_against_enum(a.target, "characters", allow_generic=True)
        if msg:
            out.append(f"{kind}.{msg}")
        msg = _validate_id_against_enum(a.jutsu, "jutsus")
        if msg:
            out.append(f"{kind}.{msg}")
        # Location peut etre une location, un village, ou un clan ; on
        # accepte tout ce qui est dans une de ces enums.
        if a.location:
            enums = _load_enums()
            if enums.get("locations") or enums.get("villages") or enums.get("clans"):
                if (
                    a.location not in enums["locations"]
                    and a.location not in enums["villages"]
                    and a.location not in enums["clans"]
                    and not _is_generic_role(a.location)
                ):
                    out.append(f"{kind}.location: '{a.location}' inconnu du canon")
    return out


def parse_narrative_output(raw: dict[str, Any]) -> NarrativeOutput:
    """Parse + valide un dict brut LLM en NarrativeOutput.

    Etapes :
    1. Pydantic validation (shape, types). Si echec, raise avec violations
       extraites du ValidationError.
    2. Enum validation (character_id, jutsu, location, etc.). Si une
       violation, raise avec la liste.

    Si tout passe, retourne un `NarrativeOutput` propre.
    """
    try:
        output = NarrativeOutput.model_validate(raw)
    except ValidationError as exc:
        violations = [
            f"shape : {e['loc']} : {e['msg']}" for e in exc.errors()
        ]
        raise StructuredOutputError(violations=violations) from exc

    enum_violations: list[str] = []
    enum_violations.extend(_validate_dialogue_ids(output.npc_dialogue))
    enum_violations.extend(_validate_action_ids(output.actions, kind="actions"))
    enum_violations.extend(_validate_action_ids(output.proposed_actions, kind="proposed_actions"))

    if enum_violations:
        raise StructuredOutputError(violations=enum_violations)

    return output
