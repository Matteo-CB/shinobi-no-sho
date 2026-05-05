"""Blacklist de termes hors-univers Naruto.

Premiere passe deterministe (regex) avant tout appel LLM.
Couvre : programmation, tech moderne, IA / LLM, autres oeuvres de fiction,
electromenager moderne, vehicules modernes, reseaux sociaux.

Termes deliberement exclus de la blacklist :
- 'naruto' : ambigu (perso + nom de la serie)
- 'code' : ambigu (code d'honneur, code secret)
- 'train' / 'metro' : presents dans Boruto
- 'ia' tout court : trop susceptible de faux positifs en francais
"""

from __future__ import annotations

import re

OUT_OF_UNIVERSE_TERMS: tuple[str, ...] = (
    # Programmation et langages
    "python",
    "javascript",
    "typescript",
    "java",
    "ruby on rails",
    "rust",
    "golang",
    "kotlin",
    "swift",
    "haskell",
    "c\\+\\+",
    "c\\#",
    "html",
    "css",
    "sql",
    "regex",
    "framework",
    "compiler",
    "compilateur",
    "coder",
    "coderais",
    "programmer en",
    "programming",
    "developpeur",
    "developpeuse",
    "debogue",
    "debogger",
    "function call",
    "callback",
    "boucle for",
    "boucle while",
    "import sys",
    "stack overflow",
    # Tech moderne et reseaux
    "internet",
    "wifi",
    "bluetooth",
    "smartphone",
    "iphone",
    "android",
    "ordinateur",
    "laptop",
    "tablette",
    "telephone portable",
    "email",
    "e-mail",
    "courriel",
    "google",
    "youtube",
    "twitter",
    "facebook",
    "instagram",
    "tiktok",
    "snapchat",
    "discord",
    "slack",
    "telegram",
    "whatsapp",
    "github",
    "gitlab",
    "stackoverflow",
    "reddit",
    # IA / LLM / meta
    "intelligence artificielle",
    "llm",
    "chatgpt",
    "gpt-3",
    "gpt-4",
    "claude ai",
    "claude sonnet",
    "claude opus",
    "gemini google",
    "openai",
    "anthropic",
    "mistral ai",
    "modele de langage",
    "language model",
    "transformer model",
    "neural network",
    "reseau neuronal",
    "prompt engineering",
    "embedding vectoriel",
    "dataset d'entrainement",
    "fine-tune",
    "fine tuning",
    # Autres oeuvres de fiction
    "marvel",
    "dc comics",
    "harry potter",
    "star wars",
    "lord of the rings",
    "seigneur des anneaux",
    "one piece",
    "dragon ball",
    "bleach",
    "pokemon",
    "digimon",
    "iron man",
    "spider-man",
    "spiderman",
    "batman",
    "superman",
    "goku",
    "luffy",
    "pikachu",
    # Tech jeu / dev
    "unity",
    "unreal engine",
    "godot engine",
    "blender 3d",
    "photoshop",
    "illustrator",
    "figma",
    # Vehicules modernes
    "voiture",
    "automobile",
    "avion",
    "helicoptere",
    "scooter",
    # Vie moderne
    "supermarche",
    "internet cafe",
    "cinema",
    "television",
    "frigidaire",
    "lave-linge",
    "micro-ondes",
    "climatisation",
    # Pays / institutions modernes (pour bloquer les references hors-monde)
    "etats-unis",
    "europe",
    "onu",
    "otan",
    "nasa",
    "fbi",
    "cia",
)


_BLACKLIST_RE = re.compile(
    r"\b(?:" + "|".join(OUT_OF_UNIVERSE_TERMS) + r")\b",
    re.IGNORECASE,
)


def is_out_of_universe(text: str) -> bool:
    """Vrai si text contient au moins un terme hors-univers."""
    if not text:
        return False
    return bool(_BLACKLIST_RE.search(text))


def find_blacklist_matches(text: str) -> list[str]:
    """Retourne la liste des termes blacklist matches (lowercase, dedoublonnes)."""
    if not text:
        return []
    return sorted({m.group(0).lower() for m in _BLACKLIST_RE.finditer(text)})


DEFAULT_REDIRECT_OUT_OF_UNIVERSE = (
    "Le ninja te regarde sans comprendre tes mots. Ces concepts ne semblent rien évoquer "
    "pour lui. Reformule dans le langage de son monde."
)
