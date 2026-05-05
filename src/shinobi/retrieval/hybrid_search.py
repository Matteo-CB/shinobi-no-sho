"""Hybrid retrieval : compose BM25 + dense + RRF + reranker optionnel.

Pipeline standard :
1. BM25 top-K (typiquement 100) sur la query
2. Dense top-K sur la query
3. RRF combine les deux rankings
4. Reranker (cross-encoder bge-v2-m3) reordonne le top-100 -> top-10

Le reranker est optionnel ; sans lui, le top-K est directement le RRF
output. C'est utile en mode rapide ou quand on n'a pas charge le modele.

Sans corpus reel (pilier 5 differe), HybridSearcher peut etre instancie
avec des fakes pour les tests algorithmiques.
"""

from __future__ import annotations

from shinobi.retrieval.rrf import reciprocal_rank_fusion
from shinobi.retrieval.types import BM25Index, DenseIndex, Reranker, ScoredDoc


class HybridSearcher:
    """Compose BM25 + dense + RRF + reranker."""

    def __init__(
        self,
        *,
        bm25: BM25Index,
        dense: DenseIndex,
        reranker: Reranker | None = None,
        bm25_top_k: int = 100,
        dense_top_k: int = 100,
        rrf_k: int = 60,
    ) -> None:
        self.bm25 = bm25
        self.dense = dense
        self.reranker = reranker
        self.bm25_top_k = bm25_top_k
        self.dense_top_k = dense_top_k
        self.rrf_k = rrf_k

    def search(self, query: str, top_k: int = 10) -> list[ScoredDoc]:
        """Recherche hybride. Retourne les top-k chunks finaux."""
        bm25_results = self.bm25.search(query, top_k=self.bm25_top_k)
        dense_results = self.dense.search(query, top_k=self.dense_top_k)
        fused = reciprocal_rank_fusion(
            (bm25_results, dense_results),
            k=self.rrf_k,
        )
        if self.reranker is None:
            return fused[:top_k]
        return self.reranker.rerank(query, fused[:max(top_k * 4, 40)], top_k=top_k)
