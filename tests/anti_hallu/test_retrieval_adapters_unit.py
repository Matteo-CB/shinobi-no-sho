"""Tests unitaires des adapters BM25 + ChromaDense via fakes.

Different de test_retrieval_adapters.py qui utilise les VRAIS index
sur disque (skip si absents). Ici, on injecte des fakes pour valider
la logique de mapping bm25s/Chroma -> Protocol BM25Index/DenseIndex
sans dependance d'index ni de modele charge.

Ces tests doivent toujours tourner, meme sans data/embeddings/ ou
data/bm25/.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from shinobi.retrieval.bm25_adapter import BM25Adapter
from shinobi.retrieval.chroma_adapter import ChromaDenseAdapter
from shinobi.retrieval.types import Document, ScoredDoc


# ------- Fake bm25s.BM25 ------------------------------------------------

@dataclass
class FakeBM25Native:
    """Mimique bm25s.BM25.retrieve() pour les tests."""

    canned_results: list[dict]
    canned_scores: list[float]

    def retrieve(self, query_tokens, k=10, show_progress=False):
        # bm25s renvoie (results, scores) avec results[0] = list de items
        # et scores[0] = list de floats
        import numpy as np
        return (
            np.array([self.canned_results[:k]], dtype=object),
            np.array([self.canned_scores[:k]], dtype=float),
        )


def test_bm25_adapter_maps_dict_results_to_scoreddoc() -> None:
    """BM25Adapter doit convertir items dict (corpus charge) en ScoredDoc."""
    fake = FakeBM25Native(
        canned_results=[
            {"chunk_id": "character:naruto", "text": "Naruto", "metadata": {"clan": "uzumaki"}},
            {"chunk_id": "character:sasuke", "text": "Sasuke", "metadata": {"clan": "uchiha"}},
        ],
        canned_scores=[5.0, 3.0],
    )
    adapter = BM25Adapter()
    adapter._retriever = fake  # injection directe, bypass load

    results = adapter.search("anything", top_k=2)
    assert len(results) == 2
    assert results[0].doc.chunk_id == "character:naruto"
    assert results[0].score == 5.0
    assert results[0].rank == 1
    assert results[0].doc.metadata == {"clan": "uzumaki"}
    assert results[1].rank == 2


def test_bm25_adapter_handles_string_corpus_items() -> None:
    """Fallback : si bm25s renvoie des strings, on construit un Document degrade."""
    fake = FakeBM25Native(
        canned_results=["plain text doc 1", "plain text doc 2"],
        canned_scores=[2.0, 1.0],
    )
    adapter = BM25Adapter()
    adapter._retriever = fake
    results = adapter.search("q", top_k=2)
    assert len(results) == 2
    assert results[0].doc.chunk_id == "plain text doc 1"


def test_bm25_adapter_top_k_truncation() -> None:
    fake = FakeBM25Native(
        canned_results=[
            {"chunk_id": f"d{i}", "text": "", "metadata": {}}
            for i in range(10)
        ],
        canned_scores=[float(10 - i) for i in range(10)],
    )
    adapter = BM25Adapter()
    adapter._retriever = fake
    results = adapter.search("q", top_k=3)
    assert len(results) == 3


def test_bm25_adapter_raises_if_index_missing(tmp_path) -> None:
    """Si persist_dir n'existe pas, un FileNotFoundError clair est raise."""
    bad_dir = tmp_path / "missing"
    adapter = BM25Adapter(persist_dir=bad_dir)
    with pytest.raises(FileNotFoundError, match="Index BM25"):
        adapter.search("q", top_k=5)


# ------- Fake ChromaStore -----------------------------------------------

@dataclass
class FakeChromaStore:
    """Mimique ChromaStore.query() pour les tests."""

    canned_response: list[dict[str, Any]]

    def query(self, query_vec, *, collection="crossdomain", top_k=5, where=None):
        return list(self.canned_response[:top_k])


def test_chroma_adapter_maps_query_response_to_scoreddoc(monkeypatch) -> None:
    """ChromaDenseAdapter doit convertir le format Chroma en ScoredDoc."""
    # Patch embed_query pour ne pas charger BGE-M3
    monkeypatch.setattr(
        "shinobi.retrieval.chroma_adapter.embed_query",
        lambda text: [0.1] * 1024,
    )

    fake_store = FakeChromaStore(canned_response=[
        {
            "id": "character:naruto",
            "document": "Naruto Uzumaki...",
            "metadata": {"clan": "uzumaki", "village": "konohagakure"},
            "score": 0.95,
        },
        {
            "id": "technique:rasengan",
            "document": "Rasengan...",
            "metadata": {"category": "ninjutsu"},
            "score": 0.80,
        },
    ])

    adapter = ChromaDenseAdapter(store=fake_store, collection="crossdomain")
    results = adapter.search("le pouvoir du jinchuuriki", top_k=2)
    assert len(results) == 2
    assert results[0].doc.chunk_id == "character:naruto"
    assert results[0].score == 0.95
    assert results[0].rank == 1
    assert results[0].doc.metadata.get("village") == "konohagakure"
    assert results[1].doc.chunk_id == "technique:rasengan"


def test_chroma_adapter_empty_response(monkeypatch) -> None:
    monkeypatch.setattr(
        "shinobi.retrieval.chroma_adapter.embed_query",
        lambda text: [0.1] * 1024,
    )
    fake_store = FakeChromaStore(canned_response=[])
    adapter = ChromaDenseAdapter(store=fake_store)
    results = adapter.search("anything", top_k=10)
    assert results == []


def test_chroma_adapter_uses_specified_collection(monkeypatch) -> None:
    """Le param `collection` doit etre passe a store.query()."""
    captured = {}

    class CapturingStore:
        def query(self, query_vec, *, collection, top_k, where=None):
            captured["collection"] = collection
            captured["top_k"] = top_k
            return []

    monkeypatch.setattr(
        "shinobi.retrieval.chroma_adapter.embed_query",
        lambda text: [0.1] * 1024,
    )
    adapter = ChromaDenseAdapter(store=CapturingStore(), collection="character")
    adapter.search("q", top_k=42)
    assert captured["collection"] == "character"
    assert captured["top_k"] == 42


def test_chroma_adapter_ranks_are_sequential(monkeypatch) -> None:
    monkeypatch.setattr(
        "shinobi.retrieval.chroma_adapter.embed_query",
        lambda text: [0.1] * 1024,
    )
    fake_store = FakeChromaStore(canned_response=[
        {"id": f"d{i}", "document": "", "metadata": {}, "score": 1.0 - i * 0.1}
        for i in range(5)
    ])
    adapter = ChromaDenseAdapter(store=fake_store)
    results = adapter.search("q", top_k=5)
    assert [r.rank for r in results] == [1, 2, 3, 4, 5]
