"""Adapter bm25s -> Protocol BM25Index.

Indexe en sparse BM25 le corpus de chunks RAG produit par chunk_all().
Persiste l'index sous data/bm25/ pour reprise rapide.

Usage indirect via shinobi.retrieval.HybridSearcher.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import bm25s

from shinobi.retrieval.types import Document, ScoredDoc

DEFAULT_BM25_DIR = Path("data") / "bm25"


class BM25Adapter:
    """Wrappe bm25s pour satisfaire le Protocol BM25Index.

    L'indexation est lazy : le premier `search` declenche le load depuis
    DEFAULT_BM25_DIR, ou raise si l'index n'a pas ete construit.
    """

    def __init__(self, persist_dir: Path | None = None) -> None:
        self.persist_dir = persist_dir or DEFAULT_BM25_DIR
        self._retriever: bm25s.BM25 | None = None
        self._docs: dict[int, Document] = {}

    def _ensure_loaded(self) -> bm25s.BM25:
        if self._retriever is not None:
            return self._retriever
        if not self.persist_dir.exists():
            raise FileNotFoundError(
                f"Index BM25 non trouve sous {self.persist_dir}. "
                f"Run scripts/build_bm25_index.py d'abord."
            )
        self._retriever = bm25s.BM25.load(str(self.persist_dir), load_corpus=True)
        return self._retriever

    def search(self, query: str, top_k: int = 100) -> list[ScoredDoc]:
        retriever = self._ensure_loaded()
        results, scores = retriever.retrieve(
            bm25s.tokenize([query], show_progress=False),
            k=top_k,
            show_progress=False,
        )
        # Avec load_corpus=True, results contient les items du corpus (dicts)
        # plutot que des indices.
        items = results[0]
        sc = scores[0]
        out: list[ScoredDoc] = []
        for rank, (item, s) in enumerate(zip(items, sc), start=1):
            if isinstance(item, dict):
                doc = Document(
                    chunk_id=item.get("chunk_id", f"_unknown_{rank}"),
                    text=item.get("text", ""),
                    metadata=item.get("metadata", {}) or {},
                )
            else:
                doc = Document(chunk_id=str(item), text=str(item))
            out.append(ScoredDoc(doc=doc, score=float(s), rank=rank))
        return out


def build_bm25_index(
    documents: Iterable[Document], *, persist_dir: Path | None = None,
) -> Path:
    """Indexe une liste de documents en BM25 et persiste sur disque.

    Returns:
        Le chemin de l'index persiste.
    """
    persist_dir = persist_dir or DEFAULT_BM25_DIR
    persist_dir.mkdir(parents=True, exist_ok=True)
    docs_list = list(documents)
    texts = [d.text for d in docs_list]
    corpus_serialized = [
        {"chunk_id": d.chunk_id, "text": d.text, "metadata": d.metadata}
        for d in docs_list
    ]
    retriever = bm25s.BM25(corpus=corpus_serialized)
    retriever.index(bm25s.tokenize(texts, show_progress=False))
    retriever.save(str(persist_dir), corpus=corpus_serialized)
    return persist_dir
