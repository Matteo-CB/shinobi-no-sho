"""Classification d'intent de la query joueur.

Premiere passe : regex deterministe, sub-ms, zero appel LLM.

Intents :
- in_universe_action : combat, deplacement, dialogue ninja
- in_universe_question : lore, perso, monde
- meta_command : sauvegarder, options, quitter (bypass LLM)
- out_of_universe : programmation, tech moderne, autre fiction (reject in-character)
- ambiguous : input court / flou (le query rewriter tentera resolution via state)

Le fallback LLM pour les cas non matches est volontairement laisse pour plus tard
si necessaire : la regex couvre 95% des cas observes en prod.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from shinobi.guards.blacklist import (
    DEFAULT_REDIRECT_OUT_OF_UNIVERSE,
    find_blacklist_matches,
    is_out_of_universe,
)


class Intent(StrEnum):
    """Categorisation d'une query joueur."""

    in_universe_action = "in_universe_action"
    in_universe_question = "in_universe_question"
    meta_command = "meta_command"
    out_of_universe = "out_of_universe"
    ambiguous = "ambiguous"


# Meta-commandes du jeu lui-meme (sauvegarde, options, quitter, etc).
# Voulu strict : exige une formulation imperative ou un mot reserve isole.
_META_PATTERNS = (
    r"\b(?:sauvegarde|sauve|save|enregistre|enregistrer)\s+(?:la\s+)?(?:partie|jeu|sauvegarde)\b",
    r"\b(?:quitte|quitter|exit)\s+(?:le\s+)?jeu\b",
    r"\bsortir\s+du\s+jeu\b",
    r"^\s*(?:options|menu|aide|help|status|stats|inventaire)\s*$",
    r"\bnouvelle\s+partie\b",
    r"\bcharger?\s+(?:une\s+)?(?:sauvegarde|partie)\b",
    r"\bfiche\s+perso\b",
)
_META_RE = re.compile("|".join(_META_PATTERNS), re.IGNORECASE)


# Tentatives de jailbreak / rupture du RP / extraction du system prompt.
_JAILBREAK_PATTERNS = (
    r"\btu\s+es\s+(?:un|une)\s+(?:ia|llm|chatgpt|claude|gpt|robot|programme|assistant|bot)\b",
    r"\btu\s+n['e]s\s+(?:qu['e]un|qu['e]une|pas)\s+(?:vrai|veritable|reel|programme|ia)\b",
    r"\bignore\s+(?:tes|les|toutes\s+les)\s+(?:instructions|consignes|ordres|regles)\b",
    r"\bsors\s+du\s+(?:jeu|role|personnage|rp)\b",
    r"\ben\s+r[eé]alit[eé]\s+tu\s+(?:es|n['e]s)\b",
    r"\b(?:role[\s-]?play|rp)\s+(?:over|fini|termine)\b",
    r"\b(?:print|affiche|montre|donne|revele)\s+(?:moi\s+)?(?:le|tes|ton|ta)\s+(?:prompt|system|instruction|consigne)s?\b",
    r"\bbreak\s+character\b",
    r"\bjailbreak\b",
)
_JAILBREAK_RE = re.compile("|".join(_JAILBREAK_PATTERNS), re.IGNORECASE)


# Question lore (interrogative).
_QUESTION_PATTERNS = (
    r"^\s*(?:qui|que|quoi|quel|quelle|quels|quelles|comment|pourquoi|ou|où|quand|combien)\b",
    r"\?\s*$",
    r"\b(?:c['e]\s*est\s+quoi|ca\s+veut\s+dire|qu['e]\s*est[- ]?ce\s+que)\b",
)
_QUESTION_RE = re.compile("|".join(_QUESTION_PATTERNS), re.IGNORECASE)


# Action a la premiere personne (verbe d'action).
_ACTION_PATTERNS = (
    r"\bje\s+(?:vais|veux|m['e]\s*en\s+vais|cours|attaque|parle|prends|achete|"
    r"ach[eè]te|dors|m['e]\s*entraine|m['e]\s*entra[iî]ne|cherche|frappe|esquive|"
    r"saute|monte|descends|entre|sors|m['e]\s*approche|m['e]\s*[eé]loigne|"
    r"me\s+bats|combats|medite|m[eé]dite|repose|attends|observe|[eé]coute|"
    r"regarde|suis|fais|fuis)\b",
    r"^\s*(?:vas|va|cours|attaque|parle|prends|frappe|saute|monte|descends|"
    r"entre|sors|attends|observe|regarde|fuis)\s+",
)
_ACTION_RE = re.compile("|".join(_ACTION_PATTERNS), re.IGNORECASE)


# Inputs courts / ambigus susceptibles d'etre des ellipses.
_AMBIGUOUS_PATTERNS = (
    r"^\s*(?:ok|okay|d['e]\s*accord|oui|non|peut[\s-]?etre|euh|hm|hmm|bof)\s*[.!?]*\s*$",
    r"^\s*(?:j['e]\s*y\s+vais|on\s+y\s+va|on\s+va|allons[- ]y)\s*[.!?]*\s*$",
    r"^\s*(?:je\s+(?:le|la|les|lui)\s+(?:vois|suis|prends|attaque|parle))\s*[.!?]*\s*$",
)
_AMBIGUOUS_RE = re.compile("|".join(_AMBIGUOUS_PATTERNS), re.IGNORECASE)


@dataclass(frozen=True)
class IntentResult:
    """Resultat de la classification d'intent."""

    intent: Intent
    confidence: float  # 0..1, heuristique
    suggested_redirect: str | None  # message in-character si reject
    blacklist_hits: tuple[str, ...]  # termes hors-univers detectes
    raw_input: str


def classify_intent(text: str) -> IntentResult:
    """Classifie une query joueur.

    Ordre de priorite (le premier match gagne) :
    1. Empty / whitespace : ambiguous
    2. Meta-commande : bypass LLM
    3. Jailbreak ou blacklist : out_of_universe
    4. Patterns d'ellipse : ambiguous
    5. Pattern interrogatif : in_universe_question
    6. Pattern d'action : in_universe_action
    7. Fallback : in_universe_action a confidence basse
    """
    if not text or not text.strip():
        return IntentResult(
            intent=Intent.ambiguous,
            confidence=1.0,
            suggested_redirect="Le ninja attend, immobile, que tu te décides.",
            blacklist_hits=(),
            raw_input=text or "",
        )

    stripped = text.strip()

    if _META_RE.search(stripped):
        return IntentResult(
            intent=Intent.meta_command,
            confidence=1.0,
            suggested_redirect=None,
            blacklist_hits=(),
            raw_input=stripped,
        )

    if _JAILBREAK_RE.search(stripped) or is_out_of_universe(stripped):
        hits = find_blacklist_matches(stripped)
        return IntentResult(
            intent=Intent.out_of_universe,
            confidence=1.0,
            suggested_redirect=DEFAULT_REDIRECT_OUT_OF_UNIVERSE,
            blacklist_hits=tuple(hits),
            raw_input=stripped,
        )

    if _AMBIGUOUS_RE.match(stripped):
        return IntentResult(
            intent=Intent.ambiguous,
            confidence=0.8,
            suggested_redirect=None,
            blacklist_hits=(),
            raw_input=stripped,
        )

    if _QUESTION_RE.search(stripped):
        return IntentResult(
            intent=Intent.in_universe_question,
            confidence=0.8,
            suggested_redirect=None,
            blacklist_hits=(),
            raw_input=stripped,
        )

    if _ACTION_RE.search(stripped):
        return IntentResult(
            intent=Intent.in_universe_action,
            confidence=0.7,
            suggested_redirect=None,
            blacklist_hits=(),
            raw_input=stripped,
        )

    return IntentResult(
        intent=Intent.in_universe_action,
        confidence=0.4,
        suggested_redirect=None,
        blacklist_hits=(),
        raw_input=stripped,
    )
