"""Post-filter de la sortie LLM avant affichage joueur.

Detecte :
- vocabulaire hors-univers (meme blacklist que le pre-filter)
- meta-commentaires ("en tant qu'IA", "je ne peux pas", etc.)
- casse 4e mur ("vous le joueur", "dans cette histoire")
- reponse trop courte / generique sans personnalite ninja

Aucun appel LLM. Retourne une liste de violations exploitable
pour declencher un regen avec feedback structure.

Helper `log_leakage_if_any` : detecte les fuites ou la query joueur ne contient
aucun terme blacklist mais ou la sortie LLM en contient. Sert a identifier les
patterns d'input qui contournent le pre-filter et qui meritent un patch dans
guards/blacklist.py. A appeler depuis l'orchestrateur (le validator du pilier 3
le fera quand il sera mis en place).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from shinobi.guards.blacklist import find_blacklist_matches, is_out_of_universe
from shinobi.logging_setup import get_logger

logger = get_logger(__name__)

# Phrases meta interdites en sortie.
_META_PHRASES = (
    r"\ben\s+tant\s+qu['e]\s*(?:ia|i\s*\.?\s*a\s*\.?|llm|assistant|claude|gpt|chatgpt|modele\s+de\s+langage|robot|programme|bot)\b",
    r"\bje\s+ne\s+peux\s+pas\s+(?:repondre|continuer|generer|creer|jouer|faire\s+cela|vous\s+aider|t['e]\s*aider)\b",
    r"\bje\s+suis\s+(?:une|un)\s+(?:ia|i\s*\.?\s*a\s*\.?|llm|assistant|programme|robot|chatgpt|claude|gpt|bot|modele)\b",
    r"\bvoici\s+(?:ma|la)\s+reponse\b",
    r"\bdesole\s+pour\s+(?:la\s+)?confusion\b",
    r"\b(?:en|dans)\s+cette\s+histoire\b",
    r"\b(?:vous|toi)\s*,?\s*(?:le\s+)?joueur\b",
    r"\bje\s+ne\s+suis\s+qu['e]un\s+(?:programme|modele|outil|ia|assistant|bot)\b",
    r"\ble\s+(?:personnage|protagoniste)\s+que\s+(?:tu|vous)\s+(?:incarne[sz]?|joue[sz]?)\b",
    r"\bdans\s+ce\s+(?:jeu|sc[eé]nario|prompt)\b",
    r"\bmes\s+(?:instructions|consignes|directives|regles)\b",
)
_META_RE = re.compile("|".join(_META_PHRASES), re.IGNORECASE)


# Reponses trop courtes / generiques sans personnalite.
_TOO_SHORT_PATTERNS = (
    r"^\s*(?:oui|non|d['e]\s*accord|ok|okay|peut[\s-]?etre|certainement|absolument|effectivement|exact|tout\s+a\s+fait)\s*[.!]*\s*$",
)
_TOO_SHORT_RE = re.compile("|".join(_TOO_SHORT_PATTERNS), re.IGNORECASE)


@dataclass(frozen=True)
class OutputViolation:
    """Une violation detectee dans la sortie LLM."""

    type: str
    description: str
    matched_text: str


def scan_output(
    text: str,
    *,
    min_length_chars: int = 30,
    enable_too_generic_check: bool = True,
) -> list[OutputViolation]:
    """Scan la sortie LLM pour les violations RP / persona.

    Args:
        text : sortie LLM brute a auditer.
        min_length_chars : seuil sous lequel une reponse est consideree trop courte.
        enable_too_generic_check : desactivable pour les cas ou des reponses ninja
            courtes sont valides (PNJ laconiques). Le vrai filtrage par contexte
            arrivera avec le risk-tagger du pilier 7.
    """
    if not text:
        return []

    out: list[OutputViolation] = []

    for hit in find_blacklist_matches(text):
        out.append(
            OutputViolation(
                type="out_of_universe",
                description=f"Terme hors-univers détecté : '{hit}'",
                matched_text=hit,
            )
        )

    for m in _META_RE.finditer(text):
        out.append(
            OutputViolation(
                type="meta_phrase",
                description=f"Phrase méta interdite détectée : '{m.group(0).strip()}'",
                matched_text=m.group(0),
            )
        )

    if enable_too_generic_check:
        stripped = text.strip()
        if _TOO_SHORT_RE.match(stripped) or len(stripped) < min_length_chars:
            out.append(
                OutputViolation(
                    type="too_generic",
                    description=(
                        f"Réponse trop courte ou générique ({len(stripped)} caractères). "
                        "Le narrateur doit développer en restant en personnage."
                    ),
                    matched_text=stripped[:80],
                )
            )

    return out


def log_leakage_if_any(
    *,
    original_query: str,
    output_violations: list[OutputViolation],
) -> bool:
    """Logge les fuites blacklist : input clean -> sortie sale.

    Une fuite est definie comme : la query joueur ne contient aucun terme
    blacklist (donc le pre-filter l'a laissee passer comme in-universe), mais
    la sortie LLM contient au moins une violation `out_of_universe`. Ces cas
    revelent soit des patterns d'input contournants, soit un LLM qui hallucine
    spontanement du vocabulaire moderne.

    Returns:
        True si une fuite a ete detectee et loggee, False sinon.
    """
    if not original_query or not output_violations:
        return False
    if is_out_of_universe(original_query):
        return False
    leaked_terms = [v.matched_text for v in output_violations if v.type == "out_of_universe"]
    if not leaked_terms:
        return False
    logger.warning(
        "Blacklist leakage detected: query passed pre-filter but output contains "
        "out-of-universe terms. Patch the blacklist if this pattern recurs.",
        original_query=original_query[:200],
        leaked_terms=leaked_terms,
    )
    return True


def format_violations_for_regen(violations: list[OutputViolation]) -> str:
    """Formate les violations pour injection dans le prompt de regen."""
    if not violations:
        return ""
    lines = ["Ta sortie précédente a été rejetée pour les raisons suivantes :"]
    for v in violations:
        lines.append(f"  - [{v.type}] {v.description}")
    lines.append(
        "\nRégénère en respectant strictement le persona ninja. "
        "Aucun méta-commentaire, aucun vocabulaire hors-univers, aucune cassure du 4e mur. "
        "Développe la scène avec détails sensoriels et ancrage canon."
    )
    return "\n".join(lines)
