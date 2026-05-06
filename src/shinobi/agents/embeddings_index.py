"""Index BGE-M3 par PNJ pour retrieval semantique des memoires.

Spec docs/02 §6.1 :
> Stockage : SQLite par PNJ + embeddings BGE-M3 pour le retrieval
>   semantique des memories.

Implementation :
- Encode chaque entry (Observation/Reflection/Plan) avec BGE-M3 (1024 dim)
- Stocke le vecteur en SQLite blob (numpy bytes)
- Retrieve par cosine similarity (vecteurs deja normalises par BGE-M3)
- Fallback gracieux : si BGE-M3 n'est pas dispo, on utilise la
  relevance_score Jaccard de `memory.py` (mode degrade fonctionnel).

L'index est lazy : on n'encode pas tout au demarrage, on encode au moment
de l'insertion via `index_entry()`. Le retrieve charge tous les vecteurs
deja indexes pour ce npc_id et fait un cosine sur place (CPU rapide
pour <1000 entries).
"""

from __future__ import annotations

import math
import sqlite3
import struct
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

from shinobi.agents.types import MemoryEntry

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS agent_embeddings (
    entry_id TEXT PRIMARY KEY,
    npc_id TEXT NOT NULL,
    kind TEXT NOT NULL,            -- observation/reflection/plan
    vector_blob BLOB NOT NULL,     -- float32 * dim
    dim INTEGER NOT NULL,
    text_chars INTEGER NOT NULL,
    created_at_ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_emb_npc ON agent_embeddings(npc_id, kind);
"""


def _vector_to_blob(vec: list[float]) -> bytes:
    """Serialise un vecteur float en bytes (struct float32)."""
    return struct.pack(f"{len(vec)}f", *vec)


def _blob_to_vector(blob: bytes) -> list[float]:
    """Deserialise des bytes en list[float]."""
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity entre deux vecteurs de meme dim. BGE-M3 normalise
    deja les vecteurs (norm=1) donc dot product = cosine. On garde la
    division pour robustesse au cas ou."""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _initialize_db(db_path: Path | str | None) -> sqlite3.Connection:
    if db_path is None:
        conn = sqlite3.connect(":memory:")
    else:
        p = Path(db_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA_SQL)
    conn.commit()
    return conn


class EmbeddingsIndex:
    """Index vectoriel SQLite-backed BGE-M3 par PNJ.

    Usage :

    ```python
    idx = EmbeddingsIndex('agents_emb.sqlite', encoder=embed_texts)
    idx.index_entry('uchiha_sasuke', obs_id='obs_123', text='...')
    top = idx.retrieve_semantic('uchiha_sasuke', query='massacre', top_k=5)
    ```

    `encoder` est injectable (callable[[list[str]] -> list[list[float]]])
    pour permettre tests et fallback gracieux. Si encoder=None, le retrieve
    semantique retourne [] (le caller fallback sur Jaccard).
    """

    def __init__(
        self,
        db_path: Path | str | None = None,
        *,
        encoder=None,  # Callable[[list[str]], list[list[float]]] | None
        query_encoder=None,  # Callable[[str], list[float]] | None
    ) -> None:
        self._conn: sqlite3.Connection | None = _initialize_db(db_path)
        self._encoder = encoder
        self._query_encoder = query_encoder

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("EmbeddingsIndex is closed.")
        return self._conn

    @property
    def has_encoder(self) -> bool:
        return self._encoder is not None or self._query_encoder is not None

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> EmbeddingsIndex:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        c = self.conn
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise

    # --- index -------------------------------------------------------------

    def _encode_one(self, text: str) -> list[float] | None:
        """Encode un texte. Retourne None si pas d'encoder."""
        if self._query_encoder is not None:
            return self._query_encoder(text)
        if self._encoder is not None:
            out = self._encoder([text])
            if out and len(out) > 0:
                return out[0]
        return None

    def _encode_batch(self, texts: list[str]) -> list[list[float]] | None:
        """Encode un batch. Retourne None si pas d'encoder."""
        if not texts:
            return []
        if self._encoder is not None:
            return self._encoder(texts)
        if self._query_encoder is not None:
            return [self._query_encoder(t) for t in texts]
        return None

    def index_entry(
        self, npc_id: str, *, entry_id: str, kind: str, text: str,
    ) -> bool:
        """Indexe une entry. Retourne False si pas d'encoder dispo."""
        if not text:
            return False
        vec = self._encode_one(text)
        if vec is None:
            return False
        import time

        self.conn.execute(
            """
            INSERT OR REPLACE INTO agent_embeddings (
                entry_id, npc_id, kind, vector_blob, dim, text_chars,
                created_at_ts
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry_id, npc_id, kind, _vector_to_blob(vec),
                len(vec), len(text), time.time(),
            ),
        )
        self.conn.commit()
        return True

    def index_entries(
        self, npc_id: str, entries: Iterable[MemoryEntry],
    ) -> int:
        """Bulk index. Retourne nb d'entries effectivement indexees."""
        items = list(entries)
        if not items:
            return 0
        texts = [e.text for e in items]
        vecs = self._encode_batch(texts)
        if vecs is None:
            return 0
        import time

        n = 0
        with self.transaction() as conn:
            for entry, vec in zip(items, vecs, strict=False):
                conn.execute(
                    """
                    INSERT OR REPLACE INTO agent_embeddings (
                        entry_id, npc_id, kind, vector_blob, dim,
                        text_chars, created_at_ts
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry.id, npc_id, entry.kind, _vector_to_blob(vec),
                        len(vec), len(entry.text), time.time(),
                    ),
                )
                n += 1
        return n

    def has_entry(self, entry_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM agent_embeddings WHERE entry_id = ? LIMIT 1",
            (entry_id,),
        ).fetchone()
        return row is not None

    def size(self, npc_id: str | None = None) -> int:
        if npc_id is None:
            row = self.conn.execute(
                "SELECT COUNT(*) AS c FROM agent_embeddings",
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT COUNT(*) AS c FROM agent_embeddings WHERE npc_id = ?",
                (npc_id,),
            ).fetchone()
        return int(row["c"])

    def delete_entry(self, entry_id: str) -> bool:
        cur = self.conn.execute(
            "DELETE FROM agent_embeddings WHERE entry_id = ?", (entry_id,),
        )
        self.conn.commit()
        return cur.rowcount > 0

    # --- retrieve ----------------------------------------------------------

    def retrieve_semantic(
        self,
        npc_id: str,
        *,
        query: str,
        top_k: int = 5,
        kind_filter: tuple[str, ...] | None = None,
    ) -> list[tuple[float, str, str]]:
        """Top-k entries semantiquement proches de query.

        Retourne list[(cosine_score, entry_id, kind)] triee desc.
        Retourne [] si pas d'encoder ou pas d'entries.
        """
        if not query or top_k <= 0:
            return []
        qvec = self._encode_one(query)
        if qvec is None:
            return []

        if kind_filter:
            placeholders = ",".join("?" for _ in kind_filter)
            sql = (
                f"SELECT entry_id, kind, vector_blob FROM agent_embeddings "
                f"WHERE npc_id = ? AND kind IN ({placeholders})"
            )
            params: list[object] = [npc_id, *kind_filter]
        else:
            sql = (
                "SELECT entry_id, kind, vector_blob FROM agent_embeddings "
                "WHERE npc_id = ?"
            )
            params = [npc_id]

        rows = self.conn.execute(sql, params).fetchall()
        if not rows:
            return []

        scored: list[tuple[float, str, str]] = []
        for r in rows:
            vec = _blob_to_vector(r["vector_blob"])
            sim = cosine_similarity(qvec, vec)
            scored.append((sim, r["entry_id"], r["kind"]))
        scored.sort(key=lambda t: t[0], reverse=True)
        return scored[:top_k]


__all__ = ["EmbeddingsIndex", "cosine_similarity"]
