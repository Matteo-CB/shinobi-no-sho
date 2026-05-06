"""Systeme de missions canon enrichies (Phase pre-D).

Distinct des `TimelineEvent` du canon (qui couvrent les arcs majeurs : massacre
Uchiha, attaque Kyuubi, etc.). Les `Mission` capturent les missions ninja de
la serie, du C-rank quotidien aux S-rank historiques.

Pourquoi un modele dedie :
- Une mission a des participants ASSIGNES (squad), un client, un type, un rang,
  un outcome (succes/echec/abandon), une location specifique.
- Une mission est ANCREE temporellement (date precise YYYY-MM-DD ou arc).
- Une mission s'integre au KG comme N facts, dont les outcomes peuvent
  declencher du drift de personnalite (Phase D).

Composants :
- types.py : Mission, MissionRank, MissionType, MissionOutcome
- catalog.py : MissionCatalog (load JSON, query)
- kg_integration.py : import Missions -> KG facts
"""

from __future__ import annotations

from shinobi.missions.catalog import MissionCatalog
from shinobi.missions.kg_integration import import_missions_to_kg
from shinobi.missions.types import (
    Mission,
    MissionOutcome,
    MissionParticipant,
    MissionRank,
    MissionType,
)

__all__ = [
    "Mission",
    "MissionCatalog",
    "MissionOutcome",
    "MissionParticipant",
    "MissionRank",
    "MissionType",
    "import_missions_to_kg",
]
