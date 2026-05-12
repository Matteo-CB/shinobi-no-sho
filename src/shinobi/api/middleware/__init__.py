"""Middlewares HTTP du serveur FastAPI."""

from shinobi.api.middleware.i18n import AcceptLanguageMiddleware

__all__ = ["AcceptLanguageMiddleware"]
