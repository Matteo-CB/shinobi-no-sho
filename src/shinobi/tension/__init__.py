"""Tension Detector (Phase C de la roadmap, docs/02 §5.3).

Detecte les opportunites dramatiques emergentes dans le monde simule, sans
hard-code de scripts d'evenements. Approche hybride :

A) ~20 invariants abstraits de physique sociale Naruto (deterministes,
   sans LLM, lecture du KG).
B) LLM analyste periodique (Qwen3-4B local, ~1 inf/3 mois in-game) qui
   recoit un snapshot synthetique du KG et identifie des fils narratifs
   en suspens, configurations critiques, anniversaires d'events.

Le Tension Detector ne genere PAS d'evenements. Il signale des opportunites
que la couche Director (Phase G) decidera d'exploiter et la couche
Multi-Agent (Phase E) incarnera concretement.

Voir docs/02-PROJET-ROADMAP-SUITE.md §5.3 et §10 Phase C.
"""

from __future__ import annotations

from shinobi.tension.detector import TensionDetector
from shinobi.tension.invariants import (
    INVARIANTS,
    TensionInvariant,
)
from shinobi.tension.llm_analyst import (
    LLMAnalystConfig,
    LLMTensionAnalyst,
    SnapshotBuilder,
)
from shinobi.tension.scheduler import (
    SchedulerState,
    TensionScheduler,
    TickResult,
)
from shinobi.tension.types import (
    Tension,
    TensionList,
    TensionSeverity,
    TensionType,
)

__all__ = [
    "INVARIANTS",
    "LLMAnalystConfig",
    "LLMTensionAnalyst",
    "SchedulerState",
    "SnapshotBuilder",
    "Tension",
    "TensionDetector",
    "TensionInvariant",
    "TensionList",
    "TensionScheduler",
    "TensionSeverity",
    "TensionType",
    "TickResult",
]
