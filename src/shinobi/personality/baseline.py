"""Extraction de baselines vectoriels canon depuis `psycho_notes.json`.

L'objectif (docs/02 §6.2 + Phase D) est de fournir un point de depart canon
au vecteur de personnalite de chaque PNJ majeur. Phase H (offline batch)
remplacera ce module par une extraction LLM massive sur wiki_sections, mais
pour atteindre 100% strict de la Phase D on derive les baselines depuis les
sources canon deja indexees.

Strategie deterministe :

1. Charger psycho_notes.json (notes par tranche d'age).
2. Concatener toutes les notes pour un NPC en un texte.
3. Pour chaque dimension, calculer un score base sur des heuristiques
   declenchees par des keywords FR + romaji, ponderees par la frequence et
   l'intensite.
4. Ajuster vers le neutre si pas d'info, vers les extremes si signaux
   convergents.
5. Si le NPC n'a pas de note, retourner le vecteur neutre (0.5 partout).

Cette approche est volontairement simple : elle ne pretend pas remplacer
une extraction LLM, mais elle est REPRODUCTIBLE, deterministe, et donne un
baseline qui differencie par exemple Itachi (haute discipline + secrecy +
sacrifice) de Naruto (haute openness + ambition + idealism).
"""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from shinobi.personality.dimensions import (
    ALL_DIMENSIONS,
    DEFAULT_NEUTRAL_VALUE,
)
from shinobi.personality.dimensions import (
    PersonalityDimension as D,
)
from shinobi.personality.types import NPCPersonality

# Heuristiques par dimension : keywords (FR sans accent) -> contribution
#
# Clefs en MINUSCULE et SANS ACCENT (on normalise le texte avant scan).
# Valeurs : score de poussee vers la dimension (positif = +, negatif = -).
# Les FR sont prioritaires car psycho_notes.json est en FR.
_DIMENSION_KEYWORDS: dict[D, dict[str, float]] = {
    D.aggression: {
        "agressif": 0.18, "violent": 0.15, "frappe": 0.10, "rage": 0.15,
        "colere": 0.15, "brute": 0.12, "combat": 0.05, "haine": 0.12,
        "calme": -0.10, "doux": -0.10, "pacifique": -0.15,
    },
    D.recklessness: {
        "imprudent": 0.20, "tete brulee": 0.20, "fonce": 0.10, "impulsif": 0.20,
        "tete folle": 0.18, "kamikaze": 0.20, "sans reflechir": 0.20,
        "prudent": -0.15, "reflechi": -0.10, "mesure": -0.10,
    },
    D.discipline: {
        "discipline": 0.20, "rigueur": 0.15, "entrainement": 0.08,
        "studieux": 0.15, "methodique": 0.20, "rigoureux": 0.18,
        "paresseux": -0.20, "indiscipline": -0.20, "feignant": -0.15,
    },
    D.loyalty: {
        "loyal": 0.25, "fidele": 0.20, "devoue": 0.20, "protecteur": 0.10,
        "trahit": -0.15, "trahison": -0.15, "deserteur": -0.20, "deserte": -0.20,
        "trahir": -0.10,
    },
    D.empathy: {
        "empathique": 0.25, "compassion": 0.20, "empathie": 0.20, "bienveillant": 0.15,
        "altruiste": 0.20, "tendre": 0.10, "doux": 0.05,
        "froid": -0.15, "indifferent": -0.15, "cruel": -0.20, "sadique": -0.20,
    },
    D.isolationism: {
        "solitaire": 0.20, "isole": 0.20, "seul": 0.10, "ostracise": 0.15,
        "ostraciser": 0.10, "rejete": 0.12, "exile": 0.20,
        "sociable": -0.15, "chaleureux": -0.10,
    },
    D.secrecy: {
        "secret": 0.15, "cache": 0.12, "dissimule": 0.18, "espion": 0.20,
        "espionnage": 0.15, "anbu": 0.18, "infiltre": 0.20, "discret": 0.10,
        "transparent": -0.15, "ouvert": -0.10,
    },
    D.manipulation: {
        "manipulateur": 0.25, "manipule": 0.18, "fourbe": 0.20, "ruse": 0.10,
        "calculateur": 0.18, "machiavelique": 0.20, "intrigue": 0.10,
        "naif": -0.10, "honnete": -0.10,
    },
    D.pragmatism: {
        "pragmatique": 0.20, "realiste": 0.15, "froid stratege": 0.18,
        "calcul": 0.10, "logique": 0.08, "rationaliste": 0.15,
        "reveur": -0.10, "irrealiste": -0.15, "utopiste": -0.10,
    },
    D.fear: {
        "peur": 0.15, "terrifie": 0.20, "trauma": 0.15, "effraye": 0.15,
        "anxieux": 0.15, "phobie": 0.18, "tremble": 0.10,
        "courageux": -0.15, "fearless": -0.20, "intrepide": -0.15,
    },
    D.melancholy: {
        "melancolique": 0.25, "triste": 0.15, "deprime": 0.20, "douleur": 0.10,
        "souffrance": 0.10, "perdu": 0.08, "deuil": 0.20, "orphelin": 0.18,
        "joyeux": -0.20, "optimiste": -0.10, "rieur": -0.15,
    },
    D.paranoia: {
        "parano": 0.25, "paranoiaque": 0.25, "mefiant": 0.15, "soupconneux": 0.18,
        "espion": 0.10, "complot": 0.10,
        "confiant": -0.15, "insouciant": -0.10,
    },
    D.idealism: {
        "ideal": 0.15, "ideologue": 0.20, "utopiste": 0.20, "reveur": 0.15,
        "noble cause": 0.15, "convictions": 0.15, "espoir": 0.10,
        "cynique": -0.20, "desabuse": -0.15, "amer": -0.10,
    },
    D.honor: {
        "honneur": 0.25, "code": 0.10, "samourai": 0.20, "noble": 0.15,
        "integre": 0.15, "droiture": 0.15,
        "deshonore": -0.15, "vil": -0.20, "traitre": -0.10,
    },
    D.vengeance: {
        "vengeance": 0.30, "venger": 0.25, "rancune": 0.20, "represaille": 0.18,
        "revanche": 0.20, "haine": 0.10, "obsession": 0.10,
        "pardon": -0.20, "oublier": -0.10, "absoudre": -0.15,
    },
    D.ambition: {
        "ambition": 0.25, "ambitieux": 0.25, "hokage": 0.15, "depasser": 0.15,
        "puissant": 0.10, "domination": 0.20, "conquerir": 0.15,
        "modeste": -0.15, "humble": -0.15, "simple": -0.05,
    },
    D.confidence: {
        "confiant": 0.20, "confiance en soi": 0.20, "assure": 0.15, "decide": 0.10,
        "hesitant": -0.15, "doute": -0.15, "incertain": -0.10, "timide": -0.18,
    },
    D.pride: {
        "fier": 0.15, "orgueil": 0.25, "orgueilleux": 0.25, "vaniteux": 0.20,
        "arrogant": 0.20, "noble": 0.10,
        "humble": -0.20, "modeste": -0.15, "efface": -0.15,
    },
    D.openness: {
        "curieux": 0.25, "ouvert": 0.20, "explorateur": 0.18, "sociable": 0.15,
        "extraverti": 0.20, "communicatif": 0.18, "amis": 0.05,
        "ferme": -0.15, "introverti": -0.10, "renferme": -0.18,
    },
    D.humor: {
        "blague": 0.18, "farce": 0.20, "rieur": 0.20, "comique": 0.18,
        "espiegle": 0.15, "joyeux": 0.10, "leger": 0.08,
        "serieux": -0.10, "austere": -0.15, "sombre": -0.10,
    },
}


def _normalize_text(text: str) -> str:
    """Lower + strip accents (NFD)."""
    if not text:
        return ""
    s = unicodedata.normalize("NFD", text)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.lower()


def _score_dimension(text_norm: str, dim: D) -> float:
    """Score brut pour une dimension : somme des contributions des keywords."""
    if not text_norm:
        return 0.0
    score = 0.0
    for kw, weight in _DIMENSION_KEYWORDS.get(dim, {}).items():
        if kw in text_norm:
            score += weight
    return score


def _clip(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


@dataclass(frozen=True)
class BaselineExtractionResult:
    """Resultat d'une extraction baseline pour un NPC."""

    npc_id: str
    vector: dict[D, float]
    notes_count: int
    text_chars: int


def extract_baseline_from_text(
    npc_id: str,
    notes_text: str,
    *,
    notes_count: int = 0,
    base_value: float = DEFAULT_NEUTRAL_VALUE,
) -> BaselineExtractionResult:
    """Extrait un vecteur baseline depuis du texte canon (psycho_notes ou autre).

    Strategie : pour chaque dimension, score brut [-large, +large] puis
    clip [-0.4, +0.4] puis ajout au neutre 0.5 -> resultat dans [0.1, 0.9].
    """
    text_norm = _normalize_text(notes_text)
    vector: dict[D, float] = {}
    for dim in ALL_DIMENSIONS:
        raw = _score_dimension(text_norm, dim)
        # On limite la deviation du neutre a +/- 0.4 (plafond/plancher 0.1/0.9)
        bounded = max(-0.4, min(0.4, raw))
        vector[dim] = _clip(base_value + bounded)
    return BaselineExtractionResult(
        npc_id=npc_id,
        vector=vector,
        notes_count=notes_count,
        text_chars=len(notes_text),
    )


def _collect_notes_text(notes_entries: Iterable[dict]) -> tuple[str, int]:
    """Concatene tous les notes entries en un texte unique."""
    parts: list[str] = []
    count = 0
    for e in notes_entries:
        if not isinstance(e, dict):
            continue
        if note := e.get("note"):
            parts.append(str(note))
            count += 1
        for r in e.get("allowed_relations", []) or []:
            parts.append(str(r))
        for r in e.get("forbidden_relations", []) or []:
            parts.append(str(r))
    return " ".join(parts), count


def extract_baseline_for_npc(
    npc_id: str,
    psycho_notes_data: dict,
) -> BaselineExtractionResult:
    """Extrait le baseline pour un NPC donne depuis le payload psycho_notes."""
    notes = (psycho_notes_data.get("notes") or {}).get(npc_id) or []
    text, count = _collect_notes_text(notes)
    return extract_baseline_from_text(npc_id, text, notes_count=count)


def extract_baselines_from_file(
    psycho_notes_path: Path | str,
    *,
    only_npc_ids: Iterable[str] | None = None,
) -> dict[str, NPCPersonality]:
    """Extrait baselines pour tous les NPCs (ou un sous-ensemble) du fichier
    psycho_notes.json. Retourne un dict[npc_id -> NPCPersonality] ou
    `vector` == `canon_baseline`.

    Le baseline_year n'est PAS deduit ici (ne suit pas du psycho_notes).
    Le caller peut model_copy(update={'baseline_year': ...}) si necessaire.
    """
    p = Path(psycho_notes_path)
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    notes_dict = (data.get("notes") or {}) if isinstance(data, dict) else {}
    target_ids: list[str] = (
        list(only_npc_ids) if only_npc_ids is not None else list(notes_dict.keys())
    )
    out: dict[str, NPCPersonality] = {}
    for npc_id in target_ids:
        result = extract_baseline_for_npc(npc_id, data)
        # baseline = vector au temps T0 (vector courant identique au baseline)
        out[npc_id] = NPCPersonality(
            npc_id=npc_id,
            vector=dict(result.vector),
            canon_baseline=dict(result.vector),
        )
    return out


__all__ = [
    "BaselineExtractionResult",
    "extract_baseline_for_npc",
    "extract_baseline_from_text",
    "extract_baselines_from_file",
]
