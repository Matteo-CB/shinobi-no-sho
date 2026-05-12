"""Dependencies FastAPI Phase 9.

`get_canon` charge le canon une seule fois et le cache en memoire pour la
duree du process. Les tests reset le cache via `reset_canon_cache_for_tests`.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from shinobi.canon.loader import load_canon


_OPTIONAL_DATASETS = (
    "characters",
    "techniques",
    "clans",
    "villages",
    "organizations",
    "tailed_beasts",
    "kekkei_genkai",
    "kekkei_mora",
    "hiden",
    "weapons_tools",
    "locations",
    "timeline_events",
    "voice_profiles",
)


@lru_cache(maxsize=1)
def get_canon() -> Any:
    """Return the CanonBundle, loaded once per process."""
    return load_canon(optional=_OPTIONAL_DATASETS)


def reset_canon_cache_for_tests() -> None:
    """Force canon reload (tests only)."""
    get_canon.cache_clear()
