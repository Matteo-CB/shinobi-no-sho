"""Factory FastAPI Phase 9.

Cree l'application avec metadata OpenAPI personnalisee + monte les routes
(health, saves, play, canon). L'app reste sans etat global : chaque
requete recharge la save SQLite cible et le canon (memoize).
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from shinobi import __version__
from shinobi.api.routes.canon import router as canon_router
from shinobi.api.routes.dialogues import router as dialogues_router
from shinobi.api.routes.goals import router as goals_router
from shinobi.api.routes.health import router as health_router
from shinobi.api.routes.inspectors import router as inspectors_router
from shinobi.api.routes.inventory import router as inventory_router
from shinobi.api.routes.missions import router as missions_router
from shinobi.api.routes.play import router as play_router
from shinobi.api.routes.preferences import router as preferences_router
from shinobi.api.routes.saves import router as saves_router
from shinobi.api.routes.status_views import router as status_router


def create_app() -> FastAPI:
    """Build the FastAPI application exposing the Shinobi engine."""
    # Phase i18n.2 : initialise la langue runtime du processus serveur
    # depuis preferences.json. Sans ca, le runtime restait a DEFAULT_LANGUAGE
    # jusqu'a un PUT /preferences/language, creant un mismatch avec
    # GET /preferences (qui lit toujours disk).
    try:
        from shinobi.i18n import initialize_from_preferences

        initialize_from_preferences()
    except Exception:
        # Best-effort : si platformdirs / preferences echouent, l'API
        # demarre quand meme avec EN par defaut.
        pass


    app = FastAPI(
        title="Shinobi no Sho API",
        description=(
            "Local HTTP API for the Shinobi no Sho engine. "
            "Exposes save creation/loading, turn execution, and access to "
            "Naruto canonical datasets. OpenAPI doc stays in English per "
            "spec doc 14 §i18n.9; response bodies are localized per "
            "Accept-Language."
        ),
        version=__version__,
        contact={"name": "Hidden Lab", "url": "https://github.com/"},
        license_info={"name": "Private"},
        openapi_tags=[
            {"name": "health", "description": "Server health."},
            {"name": "preferences", "description": "User preferences (Phase i18n language)."},
            {"name": "saves", "description": "Save file management."},
            {"name": "play", "description": "Player action execution."},
            {"name": "status", "description": "State views (goals, missions, inventory, biography, ...)."},
            {"name": "goals", "description": "Player goals (Phase 5)."},
            {"name": "missions", "description": "Village missions."},
            {"name": "inventory", "description": "Inventory, shop buy/sell, item use."},
            {"name": "canon", "description": "Canonical datasets browse."},
        ],
    )
    # CORS : indispensable pour permettre une UI graphique (Phase 10) servie
    # depuis un autre origin (Tauri/Electron/React local). On autorise tout
    # pour usage strictement local; durcir en prod si jamais publie.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Phase i18n.9 : Accept-Language middleware. Doit etre ajoute APRES
    # CORS pour que les preflight OPTIONS ne traversent pas la negociation
    # de langue inutilement, mais l'ordre est subtil dans Starlette : les
    # middlewares sont appliques dans l'ordre inverse de leur ajout. On
    # le place ici pour qu'il s'execute *avant* les routes (donc apres
    # CORS dans le pipeline d'entree).
    from shinobi.api.middleware import AcceptLanguageMiddleware

    app.add_middleware(AcceptLanguageMiddleware)

    @app.exception_handler(HTTPException)
    async def http_exc_handler(_: Request, exc: HTTPException) -> JSONResponse:
        """Uniform error schema {error, detail}."""
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": exc.__class__.__name__,
                "detail": str(exc.detail) if exc.detail else None,
            },
        )

    app.include_router(health_router)
    app.include_router(preferences_router)
    app.include_router(saves_router)
    app.include_router(play_router)
    app.include_router(status_router)
    app.include_router(goals_router)
    app.include_router(missions_router)
    app.include_router(inventory_router)
    app.include_router(dialogues_router)
    app.include_router(inspectors_router)
    app.include_router(canon_router)
    return app
