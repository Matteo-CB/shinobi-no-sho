"""Orchestrateur central des couches de validation (pilier 3).

Chaque couche implemente le Protocol `ValidationLayer` et retourne un
`ValidationResult` unique. Le `Validator` enchaine les couches dans l'ordre
et short-circuit (par defaut) au premier reject.

Couches du MVP : sherlock_rules (A), age_coherence (C).
Couches reportees : triplet_check (B, pilier 6), nli (D, pilier 7),
llm_judge (E, pilier 7).

Le Validator sera l'endroit ou `shinobi.guards.output_filter.log_leakage_if_any`
sera appele une fois branche au pipeline narrateur (TODO, voir
`shinobi.validation.regen_loop`).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from shinobi.state.age_calculator import CanonView
from shinobi.state.world_state import RuntimeState


class NarrativeDialogue(BaseModel):
    """Dialogue d'un PNJ ou du joueur dans la sortie narrative."""

    model_config = ConfigDict(extra="ignore")

    character_id: str
    line: str
    tone: str | None = None


class NarrativeAction(BaseModel):
    """Action structuree dans la sortie narrative ou proposed_actions."""

    model_config = ConfigDict(extra="ignore")

    actor: str | None = None
    type: str | None = None
    location: str | None = None
    jutsu: str | None = None
    target: str | None = None
    label_fr: str | None = None


class NarrativeOutput(BaseModel):
    """Sortie LLM normalisee a valider.

    Aligne sur le format de `shinobi.llm.narration.NarrationResponse` (champs
    narrative, npc_dialogue, proposed_actions, world_observations) plus un
    champ `actions` reserve aux actions deja resolues / committees au state.

    Pour le MVP, la couche A et la couche C ne valident que les `npc_dialogue`
    et les `actions` (les `proposed_actions` sont des suggestions au joueur,
    leur validation requiert plus de contexte et viendra plus tard).
    """

    model_config = ConfigDict(extra="ignore")

    narrative: str = ""
    npc_dialogue: list[NarrativeDialogue] = Field(default_factory=list)
    actions: list[NarrativeAction] = Field(default_factory=list)
    proposed_actions: list[NarrativeAction] = Field(default_factory=list)
    world_observations: list[str] = Field(default_factory=list)


class ValidationResult(BaseModel):
    """Verdict d'une couche de validation.

    Une couche detectant plusieurs violations agrege celles-ci dans `details`
    et resume le tout dans `reason`.
    """

    model_config = ConfigDict(extra="forbid")

    is_valid: bool
    layer: str
    reason: str | None = None
    details: list[str] = Field(default_factory=list)


class ValidationLayer(Protocol):
    """Interface d'une couche de validation."""

    name: str

    def validate(
        self,
        *,
        narrative_output: NarrativeOutput,
        state: RuntimeState,
        canon: CanonView,
    ) -> ValidationResult: ...


class Validator:
    """Orchestrateur des couches.

    `short_circuit=True` (default) : s'arrete au premier reject.
    `short_circuit=False` : execute toutes les couches, utile pour le logging
    et le feedback regen complet.
    """

    def __init__(self, layers: Iterable[ValidationLayer]) -> None:
        self.layers: list[ValidationLayer] = list(layers)

    def validate(
        self,
        *,
        narrative_output: NarrativeOutput,
        state: RuntimeState,
        canon: CanonView,
        short_circuit: bool = True,
    ) -> list[ValidationResult]:
        results: list[ValidationResult] = []
        for layer in self.layers:
            r = layer.validate(narrative_output=narrative_output, state=state, canon=canon)
            results.append(r)
            if short_circuit and not r.is_valid:
                break
        return results

    @staticmethod
    def is_valid(results: list[ValidationResult]) -> bool:
        """Vrai si toutes les couches executees ont passe."""
        return all(r.is_valid for r in results)
