"""Catalogue runtime : singleton mutable pour la langue active.

Centralise l'etat de la langue active du processus. Au demarrage, lue depuis
preferences.json. Modifiable a la volee via `set_active_language(...)`.

Le lookup `t(key, **kwargs)` :
1. Cherche `key` dans le catalogue de la langue active.
2. Si absent, fallback sur le catalogue EN.
3. Si toujours absent, retourne la cle elle-meme (avec log warning).
4. Si la valeur contient des placeholders `{name}`, les substitue depuis kwargs.
"""

from __future__ import annotations

import threading
from contextvars import ContextVar
from string import Formatter

from shinobi.i18n.loader import (
    DEFAULT_LANGUAGE,
    SUPPORTED_LANGUAGES,
    is_supported,
    load_catalog,
)
from shinobi.logging_setup import get_logger

logger = get_logger(__name__)


# Etat global du processus. Lock pour set thread-safe.
_LOCK = threading.RLock()
_ACTIVE_LANGUAGE: str = DEFAULT_LANGUAGE
_INITIALIZED: bool = False
# Set de cles signalees comme manquantes pour eviter le spam de warnings.
_MISSING_KEYS_LOGGED: set[tuple[str, str]] = set()

# Phase i18n.9 : override par requete via ContextVar (FastAPI middleware).
# Sans cette indirection, deux requetes API concurrentes avec des
# `Accept-Language` differents se clobberaient mutuellement le global.
# La ContextVar est portee par le task asyncio courant : safe en concurrence.
_REQUEST_LANGUAGE: ContextVar[str | None] = ContextVar(
    "shinobi_request_language", default=None,
)


def initialize_from_preferences() -> str:
    """Initialise la langue active depuis preferences.json.

    Idempotent : peut etre appelee plusieurs fois sans effet adverse.
    Retourne la langue active resultante.
    """
    global _INITIALIZED, _ACTIVE_LANGUAGE
    with _LOCK:
        if _INITIALIZED:
            return _ACTIVE_LANGUAGE
        # Import paresseux pour eviter cycle preferences <-> catalog
        from shinobi.i18n.preferences import load_preferences

        prefs = load_preferences()
        _ACTIVE_LANGUAGE = (
            prefs.language if is_supported(prefs.language) else DEFAULT_LANGUAGE
        )
        _INITIALIZED = True
        logger.info("i18n_initialized", language=_ACTIVE_LANGUAGE)
        return _ACTIVE_LANGUAGE


def get_active_language() -> str:
    """Retourne la langue active.

    Phase i18n.9 : si une override per-request est definie via la ContextVar
    `_REQUEST_LANGUAGE` (middleware Accept-Language), elle a priorite sur
    le global `_ACTIVE_LANGUAGE`. Sinon, retourne le global (init via
    `initialize_from_preferences()` ou DEFAULT_LANGUAGE).
    """
    override = _REQUEST_LANGUAGE.get()
    if override is not None:
        return override
    return _ACTIVE_LANGUAGE


def set_request_language(language: str | None) -> object:
    """Override la langue active pour le contexte courant (ContextVar).

    Retourne le token a passer a `reset_request_language(token)` pour
    restaurer l'etat precedent. Permet au middleware FastAPI de scoper
    la langue a la duree d'une requete sans race condition.

    Si `language` est None, reset l'override (revient au global).
    """
    if language is not None and not is_supported(language):
        raise ValueError(
            f"Unsupported language: {language!r}. "
            f"Available: {SUPPORTED_LANGUAGES}"
        )
    return _REQUEST_LANGUAGE.set(language)


def reset_request_language(token: object) -> None:
    """Restaure la ContextVar `_REQUEST_LANGUAGE` a son etat avant le set."""
    _REQUEST_LANGUAGE.reset(token)  # type: ignore[arg-type]


def set_active_language(language: str) -> None:
    """Change la langue active du processus (runtime hot-swap).

    Ne touche PAS preferences.json (c'est `preferences.set_language` qui le
    fait). Utile pour :
    - tests parametrises sur les 8 langues
    - middleware API qui set la langue par requete depuis Accept-Language
    """
    global _ACTIVE_LANGUAGE, _INITIALIZED
    if not is_supported(language):
        raise ValueError(
            f"Unsupported language: {language!r}. "
            f"Available: {SUPPORTED_LANGUAGES}"
        )
    with _LOCK:
        _ACTIVE_LANGUAGE = language
        _INITIALIZED = True
        # Reset le warning cache pour la nouvelle langue
        _MISSING_KEYS_LOGGED.clear()


def t(key: str, /, **kwargs: object) -> str:
    """Lookup d'une cle dans le catalogue actif. Fallback EN puis cle elle-meme.

    Args:
        key: cle plate, ex `"cli.menu.welcome"`.
        **kwargs: placeholders pour interpolation. Les placeholders absents
            de la valeur sont ignores. Les placeholders presents dans la
            valeur mais absents des kwargs leveront KeyError (signal d'un
            bug dans le code appelant).

    Returns:
        Chaine localisee + interpolee. Si la cle est introuvable a la fois
        dans la langue active ET dans EN, retourne la cle telle quelle
        avec un log warning (one-shot par cle pour eviter le spam).
    """
    if not isinstance(key, str) or not key:
        return ""

    active = get_active_language()
    catalog = load_catalog(active)
    value = catalog.get(key)

    if value is None and active != DEFAULT_LANGUAGE:
        # Fallback EN
        value = load_catalog(DEFAULT_LANGUAGE).get(key)
        if value is not None:
            cache_key = (active, key)
            if cache_key not in _MISSING_KEYS_LOGGED:
                _MISSING_KEYS_LOGGED.add(cache_key)
                logger.warning(
                    "i18n_key_missing_fallback_en",
                    language=active,
                    key=key,
                )

    if value is None:
        # Aucun catalogue n'a la cle. Retourne la cle elle-meme.
        cache_key = (active, key)
        if cache_key not in _MISSING_KEYS_LOGGED:
            _MISSING_KEYS_LOGGED.add(cache_key)
            logger.warning(
                "i18n_key_missing_no_fallback",
                language=active,
                key=key,
            )
        return key

    if not kwargs:
        return value

    try:
        return value.format(**kwargs)
    except (KeyError, IndexError) as exc:
        # Placeholder du template absent des kwargs : signal d'un bug.
        logger.error(
            "i18n_interpolation_error",
            language=active,
            key=key,
            template=value,
            error=type(exc).__name__,
        )
        return value


def has_key(key: str) -> bool:
    """True si `key` existe dans le catalogue actif (sans fallback)."""
    return key in load_catalog(get_active_language())


def reset_for_tests() -> None:
    """Reset complet de l'etat global (tests uniquement)."""
    global _ACTIVE_LANGUAGE, _INITIALIZED
    with _LOCK:
        _ACTIVE_LANGUAGE = DEFAULT_LANGUAGE
        _INITIALIZED = False
        _MISSING_KEYS_LOGGED.clear()
        _REQUEST_LANGUAGE.set(None)
        from shinobi.i18n.loader import reset_cache_for_tests as _reset_loader

        _reset_loader()


def list_template_placeholders(template: str) -> tuple[str, ...]:
    """Extrait les noms de placeholders {x} d'un template str.format.

    Utile pour les tests de coherence (verifier qu'une traduction conserve
    les placeholders du source).
    """
    return tuple(
        field
        for _, field, _, _ in Formatter().parse(template)
        if field is not None and field != ""
    )
