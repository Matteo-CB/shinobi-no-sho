"""Reciprocal Rank Fusion (RRF) : combine plusieurs rankings en un seul.

Algorithme pur, deterministe, testable sans aucune dependance externe.
Ref : Cormack, Clarke, Buettcher (2009) - Reciprocal Rank Fusion outperforms
Condorcet and individual Rank Learning Methods.

  RRF_score(d) = sum over rankings r of 1 / (k + rank_r(d))

`k` est la constante d'amortissement. La litterature recommande k=60 par
defaut ; valeurs plus grandes lissent davantage les rankings.
"""

from __future__ import annotations

from collections.abc import Iterable

from shinobi.retrieval.types import Document, ScoredDoc

DEFAULT_K = 60


def reciprocal_rank_fusion(
    rankings: Iterable[list[ScoredDoc]],
    *,
    k: int = DEFAULT_K,
    top_k: int | None = None,
) -> list[ScoredDoc]:
    """Combine plusieurs rankings en un seul via RRF.

    Args:
        rankings : iterable de listes de ScoredDoc, deja rankees (rank 1 = best).
        k : constante d'amortissement (default 60).
        top_k : si fourni, tronque le ranking final.

    Returns:
        Liste de ScoredDoc triee par score RRF decroissant. Le `score` du
        ScoredDoc est le score RRF, pas le score d'origine. Le `rank` est
        re-numerote 1-based dans le ranking final.
    """
    rankings_list = [list(r) for r in rankings]
    if not any(rankings_list):
        return []

    rrf_scores: dict[str, float] = {}
    docs_by_id: dict[str, Document] = {}

    for ranking in rankings_list:
        for sd in ranking:
            cid = sd.doc.chunk_id
            rrf_scores[cid] = rrf_scores.get(cid, 0.0) + 1.0 / (k + sd.rank)
            if cid not in docs_by_id:
                docs_by_id[cid] = sd.doc

    sorted_ids = sorted(rrf_scores.keys(), key=lambda c: -rrf_scores[c])

    out: list[ScoredDoc] = []
    for i, cid in enumerate(sorted_ids, start=1):
        out.append(ScoredDoc(
            doc=docs_by_id[cid],
            score=rrf_scores[cid],
            rank=i,
        ))
        if top_k is not None and i >= top_k:
            break
    return out
