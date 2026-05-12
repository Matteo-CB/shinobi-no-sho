"""Persistance des preferences utilisateur (langue, etc.).

Stockage cross-platform via `platformdirs` :
- Linux : ~/.config/shinobi-no-sho/preferences.json
- macOS : ~/Library/Application Support/shinobi-no-sho/preferences.json
- Windows : C:/Users/<USER>/AppData/Local/shinobi-no-sho/preferences.json

Schema persiste :

```json
{
  "schema_version": 1,
  "language": "en",
  "first_launch_completed": true,
  "language_chosen_at": "2026-05-08T09:30:00Z"
}
```

Si le fichier est absent / corrompu / d'une version inconnue, on retourne
des Preferences par defaut et le picker s'affichera au prochain lancement.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import platformdirs

from shinobi.i18n.loader import DEFAULT_LANGUAGE, is_supported
from shinobi.logging_setup import get_logger

logger = get_logger(__name__)


PREFERENCES_SCHEMA_VERSION: int = 1
APP_NAME: str = "shinobi-no-sho"


@dataclass(frozen=True)
class Preferences:
    """Preferences utilisateur persistees."""

    language: str = DEFAULT_LANGUAGE
    first_launch_completed: bool = False
    language_chosen_at: str | None = None
    schema_version: int = PREFERENCES_SCHEMA_VERSION


def _override_dir() -> Path | None:
    """Override par variable d'environnement (utile en tests / dev)."""
    raw = os.environ.get("SHINOBI_PREFERENCES_DIR")
    if raw:
        return Path(raw)
    return None


def preferences_dir() -> Path:
    """Repertoire ou stocker preferences.json."""
    override = _override_dir()
    if override is not None:
        return override
    return Path(platformdirs.user_config_dir(APP_NAME, appauthor=False))


def preferences_path() -> Path:
    """Chemin absolu de preferences.json."""
    return preferences_dir() / "preferences.json"


def load_preferences() -> Preferences:
    """Charge les preferences. Retourne defaults si fichier absent/corrompu."""
    path = preferences_path()
    if not path.exists():
        return Preferences()

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(
            "preferences_load_failed",
            error=type(exc).__name__,
            msg=str(exc)[:200],
        )
        return Preferences()

    if not isinstance(raw, dict):
        return Preferences()

    schema_version = raw.get("schema_version", 0)
    if not isinstance(schema_version, int) or schema_version > PREFERENCES_SCHEMA_VERSION:
        logger.warning(
            "preferences_schema_unknown",
            schema_version=schema_version,
            supported=PREFERENCES_SCHEMA_VERSION,
        )
        return Preferences()

    language = raw.get("language", DEFAULT_LANGUAGE)
    if not isinstance(language, str) or not is_supported(language):
        logger.warning("preferences_invalid_language", language=language)
        language = DEFAULT_LANGUAGE

    first_launch = raw.get("first_launch_completed", False)
    chosen_at = raw.get("language_chosen_at")
    if chosen_at is not None and not isinstance(chosen_at, str):
        chosen_at = None

    return Preferences(
        language=language,
        first_launch_completed=bool(first_launch),
        language_chosen_at=chosen_at,
        schema_version=PREFERENCES_SCHEMA_VERSION,
    )


def save_preferences(prefs: Preferences) -> None:
    """Ecrit prefs sur disque. Cree le dossier si besoin."""
    path = preferences_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": prefs.schema_version,
        "language": prefs.language,
        "first_launch_completed": prefs.first_launch_completed,
        "language_chosen_at": prefs.language_chosen_at,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("preferences_saved", language=prefs.language)


def set_language(language: str) -> Preferences:
    """Change la langue persistee. Marque first_launch_completed=True."""
    if not is_supported(language):
        raise ValueError(
            f"Unsupported language: {language!r}. "
            f"Use one of the SUPPORTED_LANGUAGES."
        )
    new = Preferences(
        language=language,
        first_launch_completed=True,
        language_chosen_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        schema_version=PREFERENCES_SCHEMA_VERSION,
    )
    save_preferences(new)
    return new


def needs_first_launch_picker() -> bool:
    """True si le picker doit etre affiche (preferences absentes ou neuves)."""
    return not load_preferences().first_launch_completed
