"""Finalise le pipeline retrieval apres embedding + BM25 indexation.

Verifie :
- data/embeddings/ contient une collection 'crossdomain' non vide
- data/bm25/ contient un index BM25 valide
- Les counts sont coherents avec chunk_all(canon)

Cree le sentinel file `data/.pipeline_ready` qui debloque les tests
end-to-end (cf. tests/anti_hallu/test_end_to_end_scenarios.py).

Usage : uv run python scripts/finalize_pipeline.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    bm25_dir = ROOT / "data" / "bm25"
    chroma_dir = ROOT / "data" / "embeddings"
    flag_path = ROOT / "data" / ".pipeline_ready"

    print("=== Finalize retrieval pipeline ===")
    if not bm25_dir.exists():
        print(f"  FAIL : {bm25_dir} missing — run scripts/build_bm25_index.py")
        return 1
    if not (chroma_dir / "chroma.sqlite3").exists():
        print(f"  FAIL : {chroma_dir}/chroma.sqlite3 missing — run scripts/rebuild_embeddings.py")
        return 1

    from shinobi.canon.loader import load_canon
    from shinobi.rag.chunker import chunk_all
    from shinobi.rag.store import ChromaStore

    print("Loading canon and chunking...")
    canon = load_canon()
    expected_n = len(chunk_all(canon))
    print(f"  expected chunks : {expected_n}")

    print("Reading Chroma collection counts (read-only)...")
    store = ChromaStore()
    try:
        crossdomain_count = store.count("crossdomain")
    except Exception as exc:
        print(f"  FAIL : impossible de lire collection 'crossdomain' : {exc}")
        return 1

    print(f"  crossdomain count : {crossdomain_count}")
    if crossdomain_count < expected_n * 0.95:
        print(
            f"  FAIL : seulement {crossdomain_count} chunks indexes, "
            f"attendu >= {int(expected_n * 0.95)} (95% de {expected_n}). "
            f"Soit le rebuild n'est pas fini, soit il a echoue."
        )
        return 1

    print("Reading BM25 index...")
    from shinobi.retrieval.bm25_adapter import BM25Adapter
    adapter = BM25Adapter(persist_dir=bm25_dir)
    test_results = adapter.search("Hatake Kakashi", top_k=3)
    if not test_results:
        print("  FAIL : BM25 retourne 0 resultats sur 'Hatake Kakashi'")
        return 1
    print(f"  BM25 sanity check OK ({len(test_results)} results)")

    flag_path.parent.mkdir(parents=True, exist_ok=True)
    flag_path.write_text(
        f"Pipeline ready.\n"
        f"crossdomain={crossdomain_count}\n"
        f"bm25_dir={bm25_dir}\n"
        f"expected_n={expected_n}\n",
        encoding="utf-8",
    )
    print(f"\n>>> Created {flag_path.relative_to(ROOT)}")
    print("Tests end-to-end debloques : uv run pytest tests/anti_hallu/test_end_to_end_scenarios.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
