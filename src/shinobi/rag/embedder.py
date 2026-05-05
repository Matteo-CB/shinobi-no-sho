"""Wrapper sentence-transformers pour BGE-M3.

Charge le modele a la premiere utilisation. Pre-telecharge automatiquement.
"""

from __future__ import annotations

from collections.abc import Iterable

from shinobi.config import settings
from shinobi.errors import EmbeddingError
from shinobi.logging_setup import get_logger

logger = get_logger(__name__)

_MODEL = None


def get_embedder():
    """Retourne l'instance partagee du modele d'embedding."""
    global _MODEL
    if _MODEL is None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise EmbeddingError("sentence-transformers non installe") from exc
        logger.info(
            "embedder_load",
            model=settings.embeddings_model_name,
            device=settings.embeddings_device,
        )
        _MODEL = SentenceTransformer(
            settings.embeddings_model_name,
            device=settings.embeddings_device,
        )
    return _MODEL


def embed_texts(texts: Iterable[str], *, batch_size: int = 32) -> list[list[float]]:
    """Encode une liste de textes en vecteurs (dim 1024 pour BGE-M3)."""
    model = get_embedder()
    materialized = list(texts)
    if not materialized:
        return []
    out = model.encode(
        materialized,
        batch_size=batch_size,
        show_progress_bar=False,
        normalize_embeddings=True,
        convert_to_numpy=True,
    )
    return [list(map(float, vec)) for vec in out]


def embed_query(text: str) -> list[float]:
    """Encode une requete unique."""
    model = get_embedder()
    vec = model.encode(
        text,
        show_progress_bar=False,
        normalize_embeddings=True,
        convert_to_numpy=True,
    )
    return [float(x) for x in vec]
