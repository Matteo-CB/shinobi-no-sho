"""Prompts et templates de cadrage persona (pilier 2 du plan anti-hallucination).

Le module fournit :
- un template `system_prompt.txt` injectable dans tout system prompt LLM
- une liste d'exemples few-shot de redirection in-character (jailbreak / hors-univers)
- des helpers pour instancier le template avec un contexte joueur

A injecter en complement (ou en remplacement) des prompts existants dans
`shinobi.llm.prompts`. Compatible avec un `PersonaContext` partiel : si le
state n'est pas encore initialise, des valeurs neutres sont utilisees.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent

SYSTEM_PROMPT_TEMPLATE_PATH = PROMPTS_DIR / "system_prompt.txt"
FEW_SHOT_REDIRECTIONS_PATH = PROMPTS_DIR / "few_shot_redirections.json"


@dataclass(frozen=True)
class PersonaContext:
    """Contexte minimal pour instancier le system prompt durci.

    Tous les champs ont des defaults pour permettre une instanciation partielle
    avant que le state tracker (pilier 4) ne soit alimente. La valeur sentinelle
    "(non défini)" indique au LLM qu'une donnee n'a pas encore ete posee.
    """

    player_name: str = "(non défini)"
    rank: str = "(non défini)"
    village: str = "(non défini)"
    age: int = 0
    arc: str = "(non défini)"
    year: int = 0


@dataclass(frozen=True)
class FewShotRedirection:
    """Exemple de redirection in-character face a un input hors-univers."""

    user_input: str
    good_response: str
    category: str


def load_few_shot_redirections() -> list[FewShotRedirection]:
    """Charge les exemples de redirection depuis le JSON."""
    raw = json.loads(FEW_SHOT_REDIRECTIONS_PATH.read_text(encoding="utf-8"))
    return [FewShotRedirection(**entry) for entry in raw]


def format_few_shot_block(redirections: list[FewShotRedirection]) -> str:
    """Formate les exemples pour injection dans le prompt."""
    lines: list[str] = []
    for r in redirections:
        lines.append(f'- Joueur : "{r.user_input}"')
        lines.append(f"  Sortie attendue : {r.good_response}")
    return "\n".join(lines)


def build_system_prompt(ctx: PersonaContext | None = None) -> str:
    """Instancie le system prompt durci avec le contexte fourni.

    Si ctx est None (avant initialisation du state §4), un PersonaContext par
    defaut est utilise, qui injecte "(non défini)" comme valeur sentinelle.

    Phase i18n.10 : la template est resolue par
    `shinobi.i18n.prompts_loader.load_prompt("narrator")` selon la langue
    active (Accept-Language ou preferences). Glossary footer auto-injecte.
    """
    from shinobi.i18n.prompts_loader import load_prompt

    template = load_prompt("narrator", inject_glossary=False)
    redirections = load_few_shot_redirections()
    few_shot = format_few_shot_block(redirections)
    if ctx is None:
        ctx = PersonaContext()
    body = template.format(
        player_name=ctx.player_name,
        rank=ctx.rank,
        village=ctx.village,
        age=ctx.age,
        arc=ctx.arc,
        year=ctx.year,
        few_shot_examples=few_shot,
    )
    # Footer glossary ajoute apres .format() pour eviter qu'un terme dans
    # le footer ne soit confondu avec un placeholder.
    from shinobi.i18n.catalog import get_active_language
    from shinobi.i18n.glossary import llm_prompt_footer

    footer = llm_prompt_footer(get_active_language())
    return body + footer if footer else body
