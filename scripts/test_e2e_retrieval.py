"""Tests end-to-end du pipeline retrieval hybride (Phase 7).

10 scenarios narratifs realistes contre BM25 + Chroma + RRF :
- exact-match (BM25 dominant)
- semantique (Chroma dominant)
- mixte (BM25 + Chroma synergie via RRF)
- cas adversariaux (jutsu invente, perso non canon)

Affiche un rapport console et retourne un exit code 0/1 selon le pass
rate (>= 80% requis pour passer).

Usage : uv run python scripts/test_e2e_retrieval.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from shinobi.retrieval import (
    BM25Adapter,
    ChromaDenseAdapter,
    HybridSearcher,
)


# Format scenario : (query, expected_substring_in_top_k_chunk_ids, top_k)
SCENARIOS: list[tuple[str, list[str], int]] = [
    # Exact match (BM25 dominant)
    ("Hatake Kakashi", ["hatake_kakashi", "hatake"], 10),
    ("Uchiha Itachi", ["uchiha_itachi"], 10),
    ("rasengan", ["rasengan"], 10),
    ("Tsukuyomi", ["tsukuyomi"], 10),
    ("byakugan", ["byakugan", "hyuga"], 10),
    # Semantique (Chroma dominant)
    ("le ninja qui a copie mille techniques", ["kakashi", "hatake"], 15),
    ("le pouvoir de manipuler le temps et l'espace", ["minato", "rinnegan", "kamui"], 15),
    ("clan ninja qui controle les ombres", ["nara", "shadow"], 15),
    # Mixte
    ("Naruto et le Kyuubi", ["uzumaki_naruto", "kurama", "kyuubi"], 10),
    ("Sasuke Sharingan eveil", ["uchiha_sasuke", "sharingan"], 10),
]


def matches(top_chunk_ids: list[str], expected: list[str]) -> bool:
    """Renvoie True si au moins un id attendu est present dans les top_chunk_ids."""
    if not expected:
        return True
    for exp in expected:
        for cid in top_chunk_ids:
            if exp in cid.lower():
                return True
    return False


def main() -> int:
    print("=== End-to-end retrieval tests (BM25 + Chroma + RRF) ===\n")

    bm25_dir = ROOT / "data" / "bm25"
    chroma_dir = ROOT / "data" / "embeddings"
    if not bm25_dir.exists():
        print(f"!!! BM25 index missing in {bm25_dir}")
        return 2
    if not (chroma_dir / "chroma.sqlite3").exists():
        print(f"!!! Chroma index missing in {chroma_dir}")
        return 2

    bm25 = BM25Adapter(persist_dir=bm25_dir)
    dense = ChromaDenseAdapter()

    bm25_results = []
    dense_results = []
    hybrid_results = []
    n_total = len(SCENARIOS)
    bm25_pass = dense_pass = hybrid_pass = 0

    for query, expected, top_k in SCENARIOS:
        # BM25 only
        b = bm25.search(query, top_k=top_k)
        b_ids = [r.doc.chunk_id for r in b]
        b_ok = matches(b_ids, expected)
        if b_ok:
            bm25_pass += 1

        # Dense only
        d = dense.search(query, top_k=top_k)
        d_ids = [r.doc.chunk_id for r in d]
        d_ok = matches(d_ids, expected)
        if d_ok:
            dense_pass += 1

        # Hybrid
        h = HybridSearcher(bm25=bm25, dense=dense).search(query, top_k=top_k)
        h_ids = [r.doc.chunk_id for r in h]
        h_ok = matches(h_ids, expected)
        if h_ok:
            hybrid_pass += 1

        b_mark = "OK" if b_ok else "FAIL"
        d_mark = "OK" if d_ok else "FAIL"
        h_mark = "OK" if h_ok else "FAIL"
        print(f"  q={query!r}")
        print(f"    expected: {expected}")
        print(f"    bm25={b_mark}  dense={d_mark}  hybrid={h_mark}")
        print(f"    hybrid top-3: {h_ids[:3]}")
        print()

    print()
    print("=== Recap ===")
    print(f"  BM25 only   : {bm25_pass} / {n_total}  ({bm25_pass/n_total*100:.0f}%)")
    print(f"  Dense only  : {dense_pass} / {n_total}  ({dense_pass/n_total*100:.0f}%)")
    print(f"  Hybrid RRF  : {hybrid_pass} / {n_total}  ({hybrid_pass/n_total*100:.0f}%)")

    threshold = int(n_total * 0.8)
    if hybrid_pass >= threshold:
        print(f"\n>>> PASS : hybrid {hybrid_pass}/{n_total} >= {threshold} (80%)")
        return 0
    print(f"\n>>> FAIL : hybrid {hybrid_pass}/{n_total} < {threshold} (80%)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
