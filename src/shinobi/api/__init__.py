"""API FastAPI Phase 9.

Expose le moteur Shinobi no Sho via HTTP (saves, play, canon, health).
Tout reste local : aucune authentification, aucun appel externe.
"""

from __future__ import annotations

from shinobi.api.server import create_app

app = create_app()

__all__ = ["app", "create_app"]
