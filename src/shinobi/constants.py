"""Constantes globales immuables du projet."""

from __future__ import annotations

from typing import Final

PROJECT_NAME: Final = "shinobi-no-sho"
SCHEMA_VERSION: Final = 1

# Hierarchie de canonicite (ordre du plus fiable au moins fiable)
CANONICITY_ORDER: Final = (
    "manga",
    "boruto_manga",
    "tbv",
    "databook",
    "movie_canon",
    "movie_filler",
    "anime_filler",
    "novel",
    "game",
)

# Profils de canonicite par defaut
DEFAULT_CANONICITY_PROFILE: Final = (
    "manga",
    "boruto_manga",
    "tbv",
    "databook",
    "movie_canon",
)

# Difficultes types pour resolution d'actions
DIFFICULTY_TRIVIAL: Final = 5
DIFFICULTY_EASY: Final = 10
DIFFICULTY_MODERATE: Final = 15
DIFFICULTY_HARD: Final = 20
DIFFICULTY_VERY_HARD: Final = 25
DIFFICULTY_EXTREME: Final = 30
DIFFICULTY_LEGENDARY: Final = 40

# Limites
MAX_STAT_VALUE: Final = 5.0
MAX_STAT_VALUE_LEGENDARY: Final = 10.0
MIN_STAT_VALUE: Final = 0.0

# Annees signed (an 1 = naissance de Naruto)
YEAR_VILLAGES_FOUNDED: Final = -55
YEAR_NARUTO_BIRTH: Final = 1
YEAR_NARUTO_PART1_START: Final = 12
YEAR_NARUTO_PART2_START: Final = 16
YEAR_FOURTH_WAR_END: Final = 17

# Datasets canoniques
CANONICAL_DATASETS: Final = (
    "world_rules",
    "natures",
    "ranks",
    "eras",
    "jutsu_categories",
    "villages",
    "clans",
    "organizations",
    "characters",
    "tailed_beasts",
    "kekkei_genkai",
    "kekkei_mora",
    "hiden",
    "techniques",
    "weapons_tools",
    "locations",
    "timeline_events",
    "voice_profiles",
)
