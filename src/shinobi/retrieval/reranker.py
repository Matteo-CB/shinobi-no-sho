"""Reranker bge-reranker-v2-m3 (cross-encoder, multilingue, 600M params).

Wrapper minimal autour de sentence-transformers.CrossEncoder. Charge le
modele en lazy-init au premier appel pour ne pas penaliser les imports.

Compatible avec le Protocol `Reranker` de retrieval/types.py.

Branchement reel quand le corpus chunks RAG est dispo. En attendant,
les tests utilisent `FakeReranker` qui re-trie selon des heuristiques
deterministes (mots de la query qui apparaissent dans le doc).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from shinobi.retrieval.types import ScoredDoc

if TYPE_CHECKING:
    from sentence_transformers import CrossEncoder

DEFAULT_MODEL = "BAAI/bge-reranker-v2-m3"


class CrossEncoderReranker:
    """Wrapper bge-reranker-v2-m3 via sentence-transformers.CrossEncoder."""

    def __init__(self, model_name: str = DEFAULT_MODEL) -> None:
        self.model_name = model_name
        self._encoder: CrossEncoder | None = None

    def _ensure_loaded(self) -> CrossEncoder:
        if self._encoder is None:
            from sentence_transformers import CrossEncoder
            self._encoder = CrossEncoder(self.model_name, max_length=512)
        return self._encoder

    def rerank(
        self, query: str, docs: Sequence[ScoredDoc], top_k: int = 10,
    ) -> list[ScoredDoc]:
        if not docs:
            return []
        encoder = self._ensure_loaded()
        pairs = [(query, sd.doc.text) for sd in docs]
        scores = encoder.predict(pairs)
        order = sorted(range(len(docs)), key=lambda i: -scores[i])[:top_k]
        return [
            ScoredDoc(doc=docs[i].doc, score=float(scores[i]), rank=rank)
            for rank, i in enumerate(order, start=1)
        ]


@dataclass
class FakeReranker:
    """Reranker deterministe pour tests : score = nb mots de la query
    presents dans le texte du doc.

    Pas de modele, pas de download. Utile pour valider que le pipeline
    hybrid_search appelle bien le reranker et reordonne selon ses scores.
    """

    def rerank(
        self, query: str, docs: Sequence[ScoredDoc], top_k: int = 10,
    ) -> list[ScoredDoc]:
        if not docs:
            return []
        query_terms = {t for t in query.lower().split() if t}
        scored: list[tuple[int, float]] = []
        for i, sd in enumerate(docs):
            doc_lc = (sd.doc.text or "").lower()
            score = sum(1.0 for t in query_terms if t in doc_lc)
            scored.append((i, score))
        scored.sort(key=lambda kv: -kv[1])
        out: list[ScoredDoc] = []
        for rank, (i, sc) in enumerate(scored[:top_k], start=1):
            out.append(ScoredDoc(doc=docs[i].doc, score=sc, rank=rank))
        return out
