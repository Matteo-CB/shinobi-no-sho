"""Tests algorithmiques du pilier 8 : hybrid retrieval.

Pas de corpus reel ni de modele charge : tout passe par des fakes
satisfaisant les Protocols `BM25Index`, `DenseIndex`, `Reranker`.

Ce qui est teste :
- RRF combine deux rankings et privilegie les docs presents dans les deux
- HybridSearcher orchestre BM25 + dense + RRF correctement
- Le reranker fake reordonne selon ses scores
- Branchement reranker change le ranking final
- Branchement sans reranker retourne le RRF tronque

Le branchement reel a un index bm25s + Chroma + bge-reranker-v2-m3 sera
fait quand le corpus chunks RAG sera scrape (pilier 5). Voir TODO dans
shinobi/retrieval/__init__.py.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from shinobi.retrieval import (
    BM25Index,
    DenseIndex,
    Document,
    HybridSearcher,
    Reranker,
    ScoredDoc,
    reciprocal_rank_fusion,
)
from shinobi.retrieval.reranker import FakeReranker


# ------- Fakes ----------------------------------------------------------

@dataclass
class FakeBM25:
    docs: list[ScoredDoc]

    def search(self, query: str, top_k: int = 100) -> list[ScoredDoc]:
        return list(self.docs[:top_k])


@dataclass
class FakeDense:
    docs: list[ScoredDoc]

    def search(self, query: str, top_k: int = 100) -> list[ScoredDoc]:
        return list(self.docs[:top_k])


def _doc(cid: str, text: str = "") -> Document:
    return Document(chunk_id=cid, text=text or f"text of {cid}")


def _ranked(ids_with_text: list[tuple[str, str]]) -> list[ScoredDoc]:
    return [
        ScoredDoc(doc=_doc(cid, text), score=1.0 - i * 0.01, rank=i + 1)
        for i, (cid, text) in enumerate(ids_with_text)
    ]


# ------- RRF tests ------------------------------------------------------

def test_rrf_combines_two_rankings() -> None:
    r1 = _ranked([("a", ""), ("b", ""), ("c", "")])
    r2 = _ranked([("c", ""), ("d", ""), ("a", "")])
    fused = reciprocal_rank_fusion([r1, r2])
    ids = [sd.doc.chunk_id for sd in fused]
    assert "a" in ids
    assert "c" in ids
    assert "b" in ids
    assert "d" in ids


def test_rrf_doc_in_both_rankings_wins() -> None:
    """'a' appparait rank 1 puis rank 3 -> doit battre 'b' et 'd'."""
    r1 = _ranked([("a", ""), ("b", ""), ("c", "")])
    r2 = _ranked([("d", ""), ("e", ""), ("a", "")])
    fused = reciprocal_rank_fusion([r1, r2])
    assert fused[0].doc.chunk_id == "a"


def test_rrf_top_k_truncation() -> None:
    r1 = _ranked([(f"d{i}", "") for i in range(20)])
    fused = reciprocal_rank_fusion([r1], top_k=5)
    assert len(fused) == 5


def test_rrf_empty_input() -> None:
    assert reciprocal_rank_fusion([]) == []
    assert reciprocal_rank_fusion([[], []]) == []


def test_rrf_re_numbered_ranks() -> None:
    r1 = _ranked([("a", ""), ("b", "")])
    r2 = _ranked([("b", ""), ("a", "")])
    fused = reciprocal_rank_fusion([r1, r2])
    assert fused[0].rank == 1
    assert fused[1].rank == 2


# ------- HybridSearcher tests -------------------------------------------

def test_hybrid_search_combines_indexes() -> None:
    bm25 = FakeBM25(_ranked([("a", "exact"), ("b", "")]))
    dense = FakeDense(_ranked([("c", ""), ("a", "")]))
    s = HybridSearcher(bm25=bm25, dense=dense)
    results = s.search("anything", top_k=3)
    ids = [sd.doc.chunk_id for sd in results]
    assert "a" in ids
    assert "b" in ids
    assert "c" in ids


def test_hybrid_search_without_reranker_returns_rrf_truncated() -> None:
    bm25 = FakeBM25(_ranked([(f"d{i}", "") for i in range(10)]))
    dense = FakeDense(_ranked([(f"d{i}", "") for i in range(10)]))
    s = HybridSearcher(bm25=bm25, dense=dense)
    results = s.search("q", top_k=5)
    assert len(results) == 5


def test_hybrid_search_with_reranker_changes_order() -> None:
    """Le reranker fake score selon mots de la query dans le doc."""
    bm25 = FakeBM25(_ranked([("a", "rien ici"), ("b", "konoha rasengan")]))
    dense = FakeDense(_ranked([("a", "rien ici"), ("b", "konoha rasengan")]))
    s_no_rerank = HybridSearcher(bm25=bm25, dense=dense)
    s_rerank = HybridSearcher(
        bm25=bm25, dense=dense, reranker=FakeReranker(),
    )
    no_rerank = s_no_rerank.search("konoha rasengan", top_k=2)
    with_rerank = s_rerank.search("konoha rasengan", top_k=2)
    # Sans reranker, 'a' arrive en tete (rank 1 dans les deux)
    assert no_rerank[0].doc.chunk_id == "a"
    # Avec FakeReranker, 'b' (qui contient les mots) doit arriver en tete
    assert with_rerank[0].doc.chunk_id == "b"


def test_hybrid_search_with_reranker_truncates() -> None:
    bm25 = FakeBM25(_ranked([(f"d{i}", "") for i in range(15)]))
    dense = FakeDense(_ranked([(f"d{i}", "") for i in range(15)]))
    s = HybridSearcher(bm25=bm25, dense=dense, reranker=FakeReranker())
    results = s.search("q", top_k=3)
    assert len(results) == 3


# ------- Reranker isolated tests ----------------------------------------

def test_fake_reranker_orders_by_query_term_overlap() -> None:
    docs = [
        ScoredDoc(doc=_doc("low", "rien"), score=0.0, rank=1),
        ScoredDoc(doc=_doc("hi", "konoha kakashi rasengan"), score=0.0, rank=2),
        ScoredDoc(doc=_doc("med", "konoha"), score=0.0, rank=3),
    ]
    fr = FakeReranker()
    out = fr.rerank("konoha rasengan", docs, top_k=3)
    assert out[0].doc.chunk_id == "hi"
    assert out[1].doc.chunk_id == "med"
    assert out[2].doc.chunk_id == "low"


def test_fake_reranker_empty() -> None:
    fr = FakeReranker()
    assert fr.rerank("q", [], top_k=10) == []


# ------- Protocol satisfaction (compile-time check) ---------------------

def test_fakes_satisfy_protocols() -> None:
    """Les fakes doivent satisfaire les Protocols par duck typing."""
    bm25: BM25Index = FakeBM25([])
    dense: DenseIndex = FakeDense([])
    reranker: Reranker = FakeReranker()
    assert callable(bm25.search)
    assert callable(dense.search)
    assert callable(reranker.rerank)
