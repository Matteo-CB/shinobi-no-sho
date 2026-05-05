"""Knowledge Graph dynamique du monde Naruto (Phase A de la roadmap).

Graphe RDF-like ou chaque fait est un triplet `(subject, relation, object)`
avec timestamps de validite, source (canon/event/player_action/inferred),
confidence (0-1), canonicity (canon_strict/canon_modified/divergent), et
liste des NPCs au courant.

Distinct des datasets statiques `data/canonical/*.json` qui restent la
source de verite immuable. Le KG capture l'etat ACTUEL du monde, qui
peut diverger du canon en cours de partie.

Voir docs/02-PROJET-ROADMAP-SUITE.md §5 pour la specification complete.
"""

from __future__ import annotations

from shinobi.kg.belief import (
    CHANNEL_DECAY,
    DEFAULT_MAX_DEPTH,
    DEFAULT_MIN_FIDELITY,
    BeliefPropagator,
)
from shinobi.kg.bootstrap import (
    bootstrap_canon_beliefs,
    bootstrap_social_network_from_canon,
)
from shinobi.kg.loader import import_canon_to_kg
from shinobi.kg.schema import (
    KG_SCHEMA_SQL,
    Belief,
    Canonicity,
    Fact,
    FactSource,
    ObjectType,
    SocialLink,
    initialize_db,
)
from shinobi.kg.social import DEFAULT_STRENGTH_BY_TYPE, SocialNetwork
from shinobi.kg.store import KnowledgeGraphStore

__all__ = [
    "CHANNEL_DECAY",
    "DEFAULT_MAX_DEPTH",
    "DEFAULT_MIN_FIDELITY",
    "DEFAULT_STRENGTH_BY_TYPE",
    "KG_SCHEMA_SQL",
    "Belief",
    "BeliefPropagator",
    "Canonicity",
    "Fact",
    "FactSource",
    "KnowledgeGraphStore",
    "ObjectType",
    "SocialLink",
    "SocialNetwork",
    "bootstrap_canon_beliefs",
    "bootstrap_social_network_from_canon",
    "import_canon_to_kg",
    "initialize_db",
]
