"""Types et Protocols du module retrieval.

Decouple les algorithmes (RRF, hybrid composition) des implementations
concretes (bm25s, Chroma, sentence-transformers) pour permettre les tests
sur fakes. Les implementations reelles arrivent quand le corpus chunks
RAG est scrape (pilier 5).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class Document:
    """Un chunk RAG avec son texte et ses metadata.

    Aligne sur le format produit par le pilier 5 (re-tagging temporel) :
    arc, year_min, year_max, tier, entities_mentioned.
    """

    chunk_id: str
    text: str
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ScoredDoc:
    """Un document avec son score (BM25, dense ou hybrid)."""

    doc: Document
    score: float
    rank: int  # 1-based dans le ranking d'origine


class BM25Index(Protocol):
    """Index BM25 sparse, ideal pour les noms propres translitteres.

    Implementation reelle prevue : bm25s (`pip install bm25s`).
    Pour les tests, un fake retourne une liste predefinie.
    """

    def search(self, query: str, top_k: int = 100) -> list[ScoredDoc]:
        """Top-k chunks BM25 pour la query."""
        ...


class DenseIndex(Protocol):
    """Index dense (embeddings + cosine).

    Implementation reelle prevue : Chroma + sentence-transformers (existant
    dans le venv). Pour les tests, un fake retourne une liste predefinie.
    """

    def search(self, query: str, top_k: int = 100) -> list[ScoredDoc]:
        """Top-k chunks dense pour la query."""
        ...


class Reranker(Protocol):
    """Reranker cross-encoder (bge-reranker-v2-m3 par defaut).

    Pour les tests, un fake reorganise selon une heuristique deterministe
    (ex: mots cles dans la query qui apparaissent dans le doc).
    """

    def rerank(
        self, query: str, docs: Sequence[ScoredDoc], top_k: int = 10,
    ) -> list[ScoredDoc]:
        """Retourne les top-k docs apres rerank."""
        ...
