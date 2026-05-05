"""Pilier 6 phase B : structured generation.

Approche Pydantic-based (pas Outlines) pour rester aligne avec le reste
du projet (Pydantic v2 partout) et eviter les dependances lourdes
(transformers + torch que Outlines reclame).

Le contrat tient : la sortie LLM brute (dict) est convertie en
`NarrativeOutput` valide ou raise `StructuredOutputError`. Si plus tard
on veut du vrai constrained decoding au niveau token, on basculera sur
XGrammar quand un LLM local sera vraiment branche.
"""

from __future__ import annotations

from shinobi.generation.structured_output import (
    StructuredOutputError,
    parse_narrative_output,
)

__all__ = [
    "StructuredOutputError",
    "parse_narrative_output",
]
