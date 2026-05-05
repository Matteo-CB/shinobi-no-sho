"""Indexe le corpus de chunks RAG en BM25 sparse.

Pendant qu'on a deja le pipeline dense (Chroma + BGE-M3), BM25 est
necessaire pour les noms propres japonais translitteres ou les ids
canoniques exacts (ex: 'Hatake Kakashi', 'Tsukuyomi').

Source des chunks : chunk_all(canon), aligne sur l'index Chroma.
Output : data/bm25/ (index + corpus serialise).

Usage : uv run python scripts/build_bm25_index.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from shinobi.canon.loader import load_canon
from shinobi.rag.chunker import chunk_all
from shinobi.retrieval.bm25_adapter import DEFAULT_BM25_DIR, build_bm25_index
from shinobi.retrieval.types import Document


def main() -> int:
    print("Loading canon...")
    canon = load_canon()
    print("Chunking...")
    chunks = chunk_all(canon)
    print(f"  {len(chunks)} chunks")

    docs = [
        Document(
            chunk_id=c.id,
            text=c.text,
            metadata={
                "type": c.type.value,
                "source_id": c.source_id,
                "canonicity": c.canonicity,
                **(c.metadata or {}),
            },
        )
        for c in chunks
    ]

    persist_dir = ROOT / DEFAULT_BM25_DIR
    print(f"Indexing in {persist_dir}...")
    t0 = time.perf_counter()
    build_bm25_index(docs, persist_dir=persist_dir)
    elapsed = time.perf_counter() - t0
    print(f"Done in {elapsed:.1f}s ({len(docs) / elapsed:.0f} chunks/s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
