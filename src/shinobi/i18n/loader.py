"""Loader des fichiers de traduction par langue.

Charge `data/i18n/<lang>.json` paresseusement (1ere demande -> read disk +
cache memoire). Le format attendu est :

```json
{
  "_schema": "i18n_v1",
  "cli.menu.welcome": "Welcome to Shinobi no Sho",
  "engine.outcome.full_success": "Brilliant success",
  ...
}
```

Les cles commencant par "_" sont des metadonnees ignorees par le lookup.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from shinobi.config import settings
from shinobi.logging_setup import get_logger

logger = get_logger(__name__)


# Codes ISO-ish autorises. Doit matcher data/i18n/<code>.json.
SUPPORTED_LANGUAGES: tuple[str, ...] = (
    "en", "fr", "es", "ja", "zh", "ko", "pt-BR", "de",
)

DEFAULT_LANGUAGE: str = "en"

# Noms d'affichage natifs (pour le picker au 1er lancement).
NATIVE_NAMES: dict[str, str] = {
    "en": "English",
    "fr": "Français",
    "es": "Español",
    "ja": "日本語",
    "zh": "中文",
    "ko": "한국어",
    "pt-BR": "Português (Brasil)",
    "de": "Deutsch",
}


def is_supported(language: str) -> bool:
    """True si `language` est dans SUPPORTED_LANGUAGES."""
    return language in SUPPORTED_LANGUAGES


def _i18n_dir() -> Path:
    return settings._abs_path("./data/i18n")


@lru_cache(maxsize=len(SUPPORTED_LANGUAGES))
def load_catalog(language: str) -> dict[str, str]:
    """Charge le catalogue d'une langue (lazy + cached).

    Si le fichier n'existe pas ou est mal forme, retourne un dict vide
    et log un warning. C'est le `t()` qui gere le fallback EN.

    Args:
        language: code ISO-ish (en, fr, ja, etc.)

    Returns:
        Dict cle -> traduction. Les meta-keys (`_schema`, etc.) sont
        filtrees automatiquement. Strings uniquement.
    """
    if not is_supported(language):
        logger.warning("i18n_unsupported_language", language=language)
        return {}

    path = _i18n_dir() / f"{language}.json"
    if not path.exists():
        logger.warning(
            "i18n_catalog_missing",
            language=language,
            path=str(path),
        )
        return {}

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        logger.error(
            "i18n_catalog_corrupt",
            language=language,
            error=type(exc).__name__,
            msg=str(exc)[:200],
        )
        return {}

    if not isinstance(raw, dict):
        logger.error("i18n_catalog_not_dict", language=language)
        return {}

    # Filtre meta-keys (_schema, _version, etc.) et garde seulement les
    # strings.
    out: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or key.startswith("_"):
            continue
        if isinstance(value, str):
            out[key] = value
    logger.info(
        "i18n_catalog_loaded",
        language=language,
        keys=len(out),
    )
    return out


def reset_cache_for_tests() -> None:
    """Force le rechargement de tous les catalogues (tests uniquement)."""
    load_catalog.cache_clear()
