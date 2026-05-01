"""Wrapper ChromaDB persistent."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from shinobi.config import settings
from shinobi.errors import RetrievalError
from shinobi.logging_setup import get_logger
from shinobi.rag.chunker import Chunk
from shinobi.types import ChunkType

logger = get_logger(__name__)


class ChromaStore:
    """ChromaDB persistent local.

    Une instance par session. Plusieurs collections, une par ChunkType + 'crossdomain'.
    """

    def __init__(self, persist_dir: Path | None = None) -> None:
        self.persist_dir = persist_dir or settings.chroma_persist_dir
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self._client = None
        self._collections: dict[str, Any] = {}

    def _client_lazy(self):
        if self._client is None:
            try:
                import chromadb
            except ImportError as exc:
                raise RetrievalError("chromadb non installe") from exc
            from chromadb.config import Settings as ChromaSettings

            self._client = chromadb.PersistentClient(
                path=str(self.persist_dir),
                settings=ChromaSettings(anonymized_telemetry=False),
            )
        return self._client

    def collection(self, name: str):
        """Retourne (ou cree) une collection."""
        if name not in self._collections:
            client = self._client_lazy()
            self._collections[name] = client.get_or_create_collection(
                name=name,
                metadata={"hnsw:space": "cosine"},
            )
        return self._collections[name]

    def add_chunks(
        self,
        chunks: list[Chunk],
        embeddings: list[list[float]],
        *,
        target_collection: str | None = None,
    ) -> None:
        """Insertion en lot. Si target_collection est None, repartit par type."""
        if not chunks:
            return
        if len(chunks) != len(embeddings):
            raise RetrievalError("nombre de chunks et d'embeddings incoherent")

        if target_collection:
            self._add_batch(target_collection, chunks, embeddings)
            return

        by_type: dict[str, list[int]] = {}
        for i, c in enumerate(chunks):
            by_type.setdefault(c.type.value, []).append(i)
        for ctype, idxs in by_type.items():
            self._add_batch(
                ctype,
                [chunks[i] for i in idxs],
                [embeddings[i] for i in idxs],
            )
        self._add_batch("crossdomain", chunks, embeddings)

    def _add_batch(self, name: str, chunks: list[Chunk], embeddings: list[list[float]]) -> None:
        col = self.collection(name)
        col.upsert(
            ids=[c.id for c in chunks],
            embeddings=embeddings,
            documents=[c.text for c in chunks],
            metadatas=[
                {
                    "type": c.type.value,
                    "source_id": c.source_id,
                    "canonicity": c.canonicity,
                    **{k: v for k, v in c.metadata.items()},
                }
                for c in chunks
            ],
        )
        logger.info("chroma_upsert", collection=name, count=len(chunks))

    def query(
        self,
        query_embedding: list[float],
        *,
        collection: str = "crossdomain",
        top_k: int = 5,
        where: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        col = self.collection(collection)
        result = col.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            where=where,
        )
        out: list[dict[str, Any]] = []
        ids = result.get("ids", [[]])[0]
        docs = result.get("documents", [[]])[0]
        metas = result.get("metadatas", [[]])[0]
        dists = result.get("distances", [[]])[0]
        for i, _id in enumerate(ids):
            out.append(
                {
                    "id": _id,
                    "document": docs[i] if i < len(docs) else "",
                    "metadata": metas[i] if i < len(metas) else {},
                    "score": 1.0 - float(dists[i]) if i < len(dists) else 0.0,
                }
            )
        return out

    def reset_collection(self, name: str) -> None:
        client = self._client_lazy()
        try:
            client.delete_collection(name)
        except Exception:
            pass
        self._collections.pop(name, None)

    def count(self, name: str) -> int:
        return self.collection(name).count()


def build_for_chunk_type(chunk_type: ChunkType) -> str:
    """Nom de collection pour un type de chunk."""
    return chunk_type.value
