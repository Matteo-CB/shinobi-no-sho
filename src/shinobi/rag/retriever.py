"""Retrieval hybride pour le RAG.

Combine recherche semantique (ChromaDB) et requetes structurees (canon queries)
pour fournir un contexte calibre a chaque tour de jeu.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shinobi.canon.models import CanonBundle
from shinobi.canon.queries import (
    voice_profile_for,
)
from shinobi.rag.embedder import embed_query
from shinobi.rag.store import ChromaStore
from shinobi.types import ChunkType


@dataclass
class RetrievedChunk:
    """Chunk trouve par le retriever."""

    id: str
    text: str
    type: str
    source_id: str
    score: float
    metadata: dict[str, Any]


@dataclass
class RetrievedContext:
    """Bundle de chunks retournes pour un tour."""

    semantic_hits: list[RetrievedChunk]
    structured_hits: list[RetrievedChunk]

    def deduplicated(self, max_count: int = 20) -> list[RetrievedChunk]:
        """Deduplique par id et tronque."""
        seen: dict[str, RetrievedChunk] = {}
        for c in [*self.structured_hits, *self.semantic_hits]:
            if c.id not in seen:
                seen[c.id] = c
            if len(seen) >= max_count:
                break
        return list(seen.values())


class Retriever:
    """Facade unifiee pour le retrieval."""

    def __init__(self, store: ChromaStore, canon: CanonBundle) -> None:
        self.store = store
        self.canon = canon

    def query_specific(
        self,
        query: str,
        *,
        chunk_type: ChunkType | None = None,
        top_k: int = 5,
        where: dict[str, Any] | None = None,
    ) -> list[RetrievedChunk]:
        """Recherche semantique sur une collection donnee."""
        emb = embed_query(query)
        collection = chunk_type.value if chunk_type else "crossdomain"
        raw = self.store.query(emb, collection=collection, top_k=top_k, where=where)
        return [
            RetrievedChunk(
                id=r["id"],
                text=r["document"],
                type=r["metadata"].get("type", "unknown"),
                source_id=r["metadata"].get("source_id", ""),
                score=r["score"],
                metadata=r["metadata"],
            )
            for r in raw
        ]

    def query_dialogue_examples(
        self,
        character_id: str,
        situation: str,
        *,
        top_k: int = 3,
    ) -> list[RetrievedChunk]:
        """Recupere des sample lines pertinents pour un PNJ."""
        return self.query_specific(
            situation,
            chunk_type=ChunkType.dialogue,
            top_k=top_k,
            where={"character_id": character_id},
        )

    def query_for_turn(
        self,
        *,
        action_text: str,
        location_id: str | None,
        present_npcs: list[str],
        active_breadcrumb_descriptions: list[str],
        top_k: int = 8,
    ) -> RetrievedContext:
        """Compose un contexte pour un tour de jeu."""
        semantic_query = self._compose_query(
            action_text=action_text,
            location_id=location_id,
            breadcrumbs=active_breadcrumb_descriptions,
        )
        semantic_hits = self.query_specific(semantic_query, top_k=top_k)

        structured_hits: list[RetrievedChunk] = []
        for npc_id in present_npcs:
            voice = voice_profile_for(self.canon, npc_id)
            if voice:
                structured_hits.extend(self.query_dialogue_examples(npc_id, action_text, top_k=2))
        if location_id:
            structured_hits.extend(
                self.query_specific(location_id, chunk_type=ChunkType.village, top_k=2)
            )

        return RetrievedContext(
            semantic_hits=semantic_hits,
            structured_hits=structured_hits,
        )

    def _compose_query(
        self,
        *,
        action_text: str,
        location_id: str | None,
        breadcrumbs: list[str],
    ) -> str:
        parts = [action_text]
        if location_id:
            parts.append(f"Lieu : {location_id}")
        if breadcrumbs:
            parts.append("Objectifs : " + " ; ".join(breadcrumbs))
        return ". ".join(parts)
