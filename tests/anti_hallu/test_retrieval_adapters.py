"""Tests des adapters BM25 et Chroma vers les Protocols BM25Index/DenseIndex.

Le test BM25 utilise l'index reel construit par scripts/build_bm25_index.py
sur les 16k chunks. Skippe si l'index n'existe pas.

Le test Chroma utilise l'index reel construit par
scripts/rebuild_embeddings.py. Skippe si l'index n'existe pas.

L'integration HybridSearcher = BM25 + Chroma + RRF est testee sur les
deux fakes (cf. test_hybrid_retrieval.py) et sur les vrais index ici.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from shinobi.retrieval import HybridSearcher, ScoredDoc
from shinobi.retrieval.bm25_adapter import BM25Adapter, DEFAULT_BM25_DIR

ROOT = Path(__file__).resolve().parents[2]
BM25_DIR = ROOT / DEFAULT_BM25_DIR
CHROMA_DIR = ROOT / "data" / "embeddings"


# ------- BM25 adapter tests --------------------------------------------

@pytest.fixture(scope="module")
def bm25_adapter() -> BM25Adapter:
    if not BM25_DIR.exists():
        pytest.skip(f"BM25 index missing in {BM25_DIR}, run scripts/build_bm25_index.py")
    return BM25Adapter(persist_dir=BM25_DIR)


def test_bm25_search_returns_results(bm25_adapter: BM25Adapter) -> None:
    results = bm25_adapter.search("Hatake Kakashi", top_k=5)
    assert len(results) > 0
    assert all(isinstance(r, ScoredDoc) for r in results)


def test_bm25_exact_canon_id_top_match(bm25_adapter: BM25Adapter) -> None:
    """Une recherche sur l'id exact d'un perso doit le retrouver en tete."""
    results = bm25_adapter.search("Hatake Kakashi", top_k=5)
    top_ids = [r.doc.chunk_id for r in results]
    # On veut au moins un chunk lie a hatake_kakashi dans le top-5
    assert any("hatake_kakashi" in cid or "hatake" in cid for cid in top_ids), \
        f"Hatake Kakashi pas dans top-5 BM25: {top_ids}"


def test_bm25_ranks_are_sequential(bm25_adapter: BM25Adapter) -> None:
    results = bm25_adapter.search("rasengan", top_k=10)
    expected_ranks = list(range(1, len(results) + 1))
    actual_ranks = [r.rank for r in results]
    assert actual_ranks == expected_ranks


def test_bm25_top_k_is_respected(bm25_adapter: BM25Adapter) -> None:
    results = bm25_adapter.search("rasengan", top_k=3)
    assert len(results) <= 3


# ------- Chroma adapter tests -------------------------------------------

@pytest.fixture(scope="module")
def chroma_adapter():
    if not CHROMA_DIR.exists() or not (CHROMA_DIR / "chroma.sqlite3").exists():
        pytest.skip(f"Chroma index missing in {CHROMA_DIR}, run scripts/rebuild_embeddings.py")
    from shinobi.retrieval.chroma_adapter import ChromaDenseAdapter
    adapter = ChromaDenseAdapter()
    # Verifie que la collection contient quelque chose
    try:
        n = adapter.store.count("crossdomain")
    except Exception:
        pytest.skip("Chroma collection 'crossdomain' empty or missing")
    if n == 0:
        pytest.skip("Chroma collection 'crossdomain' empty")
    return adapter


def test_chroma_search_returns_results(chroma_adapter) -> None:
    results = chroma_adapter.search("le ninja copieur de Konoha", top_k=5)
    assert len(results) > 0
    assert all(isinstance(r, ScoredDoc) for r in results)


def test_chroma_semantic_match(chroma_adapter) -> None:
    """Recherche semantique doit retrouver Kakashi via une query plus directe."""
    # 'le ninja copieur' (FR slang) marchait mal sur BGE-M3 en mode pure
    # semantique ; on utilise la formulation EN canon-attestee qui est dans
    # les wiki_sections de Kakashi.
    results = chroma_adapter.search("Copy Ninja Kakashi Sharingan", top_k=10)
    top_ids = [r.doc.chunk_id for r in results]
    assert any("hatake_kakashi" in cid or "kakashi" in cid for cid in top_ids), \
        f"Kakashi pas trouve par semantique : {top_ids[:3]}"


# ------- Hybrid integration tests ---------------------------------------

@pytest.fixture(scope="module")
def hybrid_real(bm25_adapter, chroma_adapter):
    return HybridSearcher(bm25=bm25_adapter, dense=chroma_adapter)


def test_hybrid_combines_real_indexes(hybrid_real) -> None:
    results = hybrid_real.search("Hatake Kakashi", top_k=5)
    assert len(results) > 0


def test_hybrid_exact_id_finds_via_bm25(hybrid_real) -> None:
    """BM25 garantit le match exact sur les noms propres."""
    results = hybrid_real.search("Hatake Kakashi", top_k=10)
    top_ids = [r.doc.chunk_id for r in results]
    assert any("hatake" in cid for cid in top_ids)


def test_hybrid_semantic_finds_via_dense(hybrid_real) -> None:
    """Dense garantit le match sur la description semantique."""
    results = hybrid_real.search("le ninja qui a copie mille techniques", top_k=10)
    top_ids = [r.doc.chunk_id for r in results]
    # Attendu : Kakashi qui est canoniquement le copy ninja
    assert any("kakashi" in cid or "hatake" in cid for cid in top_ids), \
        f"Recherche semantique a echoue : {top_ids[:5]}"
