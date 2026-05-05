"""Adapter ChromaStore -> Protocol DenseIndex.

Wrappe shinobi.rag.store.ChromaStore + shinobi.rag.embedder.embed_query
pour satisfaire le Protocol DenseIndex de shinobi.retrieval.types.

Supporte un filtrage temporel : si on passe `narrative_year`, exclut les
chunks dont year_max est strictement superieur (anachronismes futurs).
Les chunks sans tag temporel (year_max == TEMPORAL_SENTINEL = 9999) sont
laisses passer (lore generique non-time-locked).

Usage indirect via shinobi.retrieval.HybridSearcher.
"""

from __future__ import annotations

from shinobi.rag.embedder import embed_query
from shinobi.rag.store import ChromaStore
from shinobi.retrieval.types import Document, ScoredDoc

TEMPORAL_SENTINEL = 9999  # untagged / lore generique


class ChromaDenseAdapter:
    """Wrappe ChromaStore pour satisfaire DenseIndex.

    Par defaut interroge la collection 'crossdomain' qui contient tous les
    chunks. Une autre collection peut etre passee a l'init pour cibler
    (e.g. 'character', 'technique').

    Si `narrative_year` est fourni a l'init OU au search, applique un
    filtre Chroma `where={year_max <= narrative_year OR year_max == sentinel}`
    pour eviter les anachronismes futurs.
    """

    def __init__(
        self,
        store: ChromaStore | None = None,
        *,
        collection: str = "crossdomain",
        narrative_year: int | None = None,
    ) -> None:
        self.store = store or ChromaStore()
        self.collection = collection
        self.narrative_year = narrative_year

    def search(
        self,
        query: str,
        top_k: int = 100,
        *,
        narrative_year: int | None = None,
    ) -> list[ScoredDoc]:
        year = narrative_year if narrative_year is not None else self.narrative_year
        where = None
        if year is not None:
            where = {"$or": [
                {"year_max": {"$lte": int(year)}},
                {"year_max": {"$eq": TEMPORAL_SENTINEL}},
            ]}
        query_vec = embed_query(query)
        raw = self.store.query(
            query_vec, collection=self.collection, top_k=top_k, where=where,
        )
        out: list[ScoredDoc] = []
        for rank, item in enumerate(raw, start=1):
            doc = Document(
                chunk_id=item["id"],
                text=item.get("document", ""),
                metadata=item.get("metadata", {}) or {},
            )
            out.append(ScoredDoc(
                doc=doc,
                score=float(item.get("score", 0.0)),
                rank=rank,
            ))
        return out
