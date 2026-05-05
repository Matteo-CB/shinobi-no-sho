"""Pilier 8 : hybrid retrieval (BM25 + dense + RRF + reranker).

Le code des composants est ecrit avec des Protocols pour permettre :
- des fakes en test (pas de dependance reseau, pas de chargement de modele)
- un branchement reel quand le corpus chunks RAG sera scrape (cf. pilier 5)

Le pilier 5 (re-tagging temporel) est differe ; jusqu'a ce qu'il soit
livre, l'index BM25 et l'index dense ne sont pas peuples. Les classes
ici sont testees sur des fakes deterministes.

TODO : brancher BM25Index a un index bm25s reel et DenseIndex a Chroma
quand le corpus chunks sera dispo. Voir scripts/pass5_tag_chunks.py.
"""

from __future__ import annotations

from shinobi.retrieval.bm25_adapter import BM25Adapter, build_bm25_index
from shinobi.retrieval.chroma_adapter import ChromaDenseAdapter
from shinobi.retrieval.hybrid_search import HybridSearcher
from shinobi.retrieval.rrf import reciprocal_rank_fusion
from shinobi.retrieval.types import (
    BM25Index,
    DenseIndex,
    Document,
    Reranker,
    ScoredDoc,
)

__all__ = [
    "BM25Adapter",
    "BM25Index",
    "ChromaDenseAdapter",
    "DenseIndex",
    "Document",
    "HybridSearcher",
    "Reranker",
    "ScoredDoc",
    "build_bm25_index",
    "reciprocal_rank_fusion",
]
