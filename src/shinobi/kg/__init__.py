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

from shinobi.kg.loader import import_canon_to_kg
from shinobi.kg.schema import (
    KG_SCHEMA_SQL,
    Canonicity,
    Fact,
    FactSource,
    ObjectType,
    initialize_db,
)
from shinobi.kg.store import KnowledgeGraphStore

__all__ = [
    "KG_SCHEMA_SQL",
    "Canonicity",
    "Fact",
    "FactSource",
    "KnowledgeGraphStore",
    "ObjectType",
    "import_canon_to_kg",
    "initialize_db",
]
