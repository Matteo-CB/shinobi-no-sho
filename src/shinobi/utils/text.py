"""Helpers texte pour respecter les contraintes de style.

Les regles cles : pas d'em dash, pas d'en dash, pas d'emoji, pas d'argot otaku.
"""

from __future__ import annotations

import re
import unicodedata

EM_DASH = "—"
EN_DASH = "–"

# Substituts standardises pour les tirets cadratin et moyen.
DASH_SUBSTITUTES = {
    EM_DASH: ", ",
    EN_DASH: " a ",
}

# Patterns interdits dans la narration (insensibles a la casse).
FORBIDDEN_PATTERNS = (
    r"\bepique\b",
    r"\btrop\s+op\b",
    r"\boverpowered\b",
    r"\bkyaa\b",
    r"\btoo\s+stylish\b",
)

EMOJI_RANGES = (
    (0x1F300, 0x1FAFF),  # symbols and pictographs
    (0x1F600, 0x1F64F),  # emoticons
    (0x1F680, 0x1F6FF),  # transport and map
    (0x1F700, 0x1F77F),  # alchemical
    (0x2600, 0x27BF),  # misc symbols + dingbats
    (0x1F900, 0x1F9FF),  # supplemental symbols and pictographs
)


def contains_em_dash(text: str) -> bool:
    """Detecte la presence d'un em dash ou en dash."""
    return EM_DASH in text or EN_DASH in text


def strip_dashes(text: str) -> str:
    """Remplace em dash et en dash par des substituts standardises."""
    out = text
    for source, replacement in DASH_SUBSTITUTES.items():
        out = out.replace(source, replacement)
    return out


def contains_emoji(text: str) -> bool:
    """Verifie qu'aucun emoji unicode n'est present."""
    return any(_is_emoji(ch) for ch in text)


def _is_emoji(ch: str) -> bool:
    code = ord(ch)
    return any(start <= code <= end for start, end in EMOJI_RANGES)


def strip_emojis(text: str) -> str:
    """Supprime les emojis unicodes."""
    return "".join(ch for ch in text if not _is_emoji(ch))


def contains_forbidden_slang(text: str) -> bool:
    """Detecte l'argot otaku dans la voix narrative."""
    lower = text.lower()
    return any(re.search(pattern, lower) for pattern in FORBIDDEN_PATTERNS)


def remove_accents(text: str) -> str:
    """Retire les diacritiques pour les contextes ascii pur (logs, slugs, ids)."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch))


def sanitize_narrative(text: str) -> str:
    """Nettoie une narration generee : retire em dash et emojis, signale slang."""
    cleaned = strip_dashes(text)
    cleaned = strip_emojis(cleaned)
    return cleaned


def is_clean_narrative(text: str) -> bool:
    """Retourne True si la narration respecte les contraintes de style."""
    return not (contains_em_dash(text) or contains_emoji(text) or contains_forbidden_slang(text))
