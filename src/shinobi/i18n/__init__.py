"""Module i18n : internationalisation 8 langues du projet.

API publique :

```python
from shinobi.i18n import t, set_language, get_language, available_languages

# Lookup avec interpolation
t("cli.menu.welcome")
# -> "Welcome to Shinobi no Sho" (en)
# -> "Bienvenue dans Shinobi no Sho" (fr)
# -> "シノビの書へようこそ" (ja)

t("engine.outcome.partial_success_with_target", target="Itachi")
# -> "Partial success against Itachi"

# Change la langue persistee + active
set_language("ja")

# Lecture
get_language()  # -> "ja"

# Liste des langues
available_languages()  # -> ("en", "fr", "es", "ja", "zh", "ko", "pt-BR", "de")
```
"""

from __future__ import annotations

from shinobi.i18n.catalog import (
    get_active_language,
    has_key,
    initialize_from_preferences,
    list_template_placeholders,
    set_active_language,
    t,
)
from shinobi.i18n.glossary import (
    all_preserved_terms,
    find_preserved_terms_in,
    is_preserved,
    llm_prompt_footer,
)
from shinobi.i18n.loader import (
    DEFAULT_LANGUAGE,
    NATIVE_NAMES,
    SUPPORTED_LANGUAGES,
    is_supported,
)
from shinobi.i18n.preferences import (
    Preferences,
    load_preferences,
    needs_first_launch_picker,
    save_preferences,
    set_language,
)


def get_language() -> str:
    """Retourne la langue active du processus."""
    return get_active_language()


def available_languages() -> tuple[str, ...]:
    """Liste des codes de langue supportes."""
    return SUPPORTED_LANGUAGES


__all__ = [
    "DEFAULT_LANGUAGE",
    "NATIVE_NAMES",
    "SUPPORTED_LANGUAGES",
    "Preferences",
    "all_preserved_terms",
    "available_languages",
    "find_preserved_terms_in",
    "get_active_language",
    "get_language",
    "has_key",
    "initialize_from_preferences",
    "is_preserved",
    "is_supported",
    "list_template_placeholders",
    "llm_prompt_footer",
    "load_preferences",
    "needs_first_launch_picker",
    "save_preferences",
    "set_active_language",
    "set_language",
    "t",
]
