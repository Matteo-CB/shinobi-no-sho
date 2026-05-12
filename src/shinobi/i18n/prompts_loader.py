"""Phase i18n.10 : loader des system prompts LLM par langue.

Centralise l'acces aux 6 prompts systeme :
    - narrator
    - goal_pathfinder
    - character_interpreter
    - world_resolver
    - tension_analyst
    - director_compactor

Strategie :
    1. Lit `data/i18n/prompts/<active_lang>/<name>.txt`.
    2. Fallback `data/i18n/prompts/en/<name>.txt` si manquant.
    3. Erreur explicite si meme EN est manquant (les 6 fichiers EN doivent
       toujours exister comme source canonique).
    4. Optionnellement, injecte un footer glossary (`llm_prompt_footer(lang)`)
       pour preserver les termes Naruto en romaji meme apres traduction.

Le loader est utilise par les 6 modules LLM :
    - `shinobi.prompts.build_system_prompt` (narrator)
    - `shinobi.goals.pathfinder` (goal_pathfinder)
    - `shinobi.llm.narration` (character_interpreter)
    - `shinobi.world_resolver.generator` (world_resolver)
    - `shinobi.tension.llm_analyst` (tension_analyst)
    - `shinobi.director.compactor` (director_compactor)

Tous les call-sites passent par `load_prompt(name)` (lang resolue
runtime via `get_active_language()`) ou `load_prompt(name, lang="ja")`
si la langue cible doit etre forcee.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from shinobi.config import settings
from shinobi.i18n.catalog import get_active_language
from shinobi.i18n.glossary import llm_prompt_footer
from shinobi.i18n.loader import DEFAULT_LANGUAGE, is_supported
from shinobi.logging_setup import get_logger

logger = get_logger(__name__)


PROMPT_NAMES: tuple[str, ...] = (
    "narrator",
    "goal_pathfinder",
    "character_interpreter",
    "world_resolver",
    "tension_analyst",
    "director_compactor",
)


def _prompts_dir() -> Path:
    return settings._abs_path("./data/i18n/prompts")


@lru_cache(maxsize=len(PROMPT_NAMES) * 8)
def _read_prompt_file(lang: str, name: str) -> str:
    """Lecture brute du fichier (lazy + cached). Vide si absent."""
    path = _prompts_dir() / lang / f"{name}.txt"
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning(
            "prompt_read_failed",
            lang=lang,
            name=name,
            path=str(path),
            error=type(exc).__name__,
        )
        return ""


def load_prompt(
    name: str,
    *,
    lang: str | None = None,
    inject_glossary: bool = True,
) -> str:
    """Charge un prompt systeme localise.

    Args:
        name: l'un de `PROMPT_NAMES` (ex: "narrator", "goal_pathfinder").
        lang: code ISO supporte (ex: "ja"). Si None, utilise
            `get_active_language()` (lit la ContextVar de la requete API ou
            le global initialise depuis preferences.json).
        inject_glossary: si True (defaut), ajoute le footer
            `llm_prompt_footer(lang)` avec les ~50 termes Naruto preserves.

    Returns:
        Le contenu du prompt (UTF-8) avec footer glossary optionnel.

    Raises:
        ValueError: si `name` n'est pas dans `PROMPT_NAMES`.
        FileNotFoundError: si ni le fichier `<lang>/<name>.txt` ni le
            fallback `en/<name>.txt` n'existe.
    """
    if name not in PROMPT_NAMES:
        raise ValueError(
            f"Unknown prompt name: {name!r}. "
            f"Expected one of: {', '.join(PROMPT_NAMES)}"
        )

    target_lang = lang or get_active_language()
    if not is_supported(target_lang):
        logger.warning(
            "prompt_lang_unsupported_fallback_default",
            requested=target_lang,
            default=DEFAULT_LANGUAGE,
        )
        target_lang = DEFAULT_LANGUAGE

    content = _read_prompt_file(target_lang, name)
    if not content and target_lang != DEFAULT_LANGUAGE:
        # Fallback EN
        logger.info(
            "prompt_fallback_default_lang",
            requested_lang=target_lang,
            name=name,
        )
        content = _read_prompt_file(DEFAULT_LANGUAGE, name)
        target_lang = DEFAULT_LANGUAGE

    if not content:
        raise FileNotFoundError(
            f"Prompt {name!r} not found for lang={target_lang!r} or fallback "
            f"{DEFAULT_LANGUAGE!r}. Expected file: "
            f"{_prompts_dir() / target_lang / (name + '.txt')}"
        )

    # Strip trailing whitespace to make the optional footer attachment clean.
    content = content.rstrip()

    if inject_glossary:
        footer = llm_prompt_footer(target_lang)
        if footer:
            content = content + footer

    return content


def reset_cache_for_tests() -> None:
    """Reset le cache lru_cache (tests uniquement)."""
    _read_prompt_file.cache_clear()


__all__ = [
    "PROMPT_NAMES",
    "load_prompt",
    "reset_cache_for_tests",
]
