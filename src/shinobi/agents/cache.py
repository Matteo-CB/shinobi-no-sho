"""LLMCache disk-backed pour caching agressif des inferences agents.

Spec docs/02 §11.2 :
> import hashlib
> import diskcache
> cache = diskcache.Cache('data/llm_cache')
> def cached_inference(prompt, model_id, temperature):
>     key = hashlib.sha256(f'{model_id}:{temperature}:{prompt}'.encode()).hexdigest()
>     if key in cache: return cache[key]
>     result = llama_inference(prompt, model_id, temperature)
>     cache[key] = result
>     return result
> Hit rate attendu sur partie longue : 30-50% des inferences.

On evite la dep diskcache (poids bibliotheque) pour rester pure-stdlib.
Implementation : SQLite key-value avec hash SHA-256.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS llm_cache (
    cache_key TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    model_id TEXT NOT NULL,
    temperature REAL NOT NULL,
    prompt_chars INTEGER NOT NULL,
    created_at_ts REAL NOT NULL,
    hits INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_llm_cache_model ON llm_cache(model_id);
"""


def compute_cache_key(prompt: str, model_id: str, temperature: float) -> str:
    """Cle SHA-256 deterministe sur (model_id, temperature, prompt)."""
    raw = f"{model_id}:{temperature:.4f}:{prompt}".encode()
    return hashlib.sha256(raw).hexdigest()


class LLMCache:
    """Cache disque SQLite pour inferences LLM. Thread-safe via SQLite locks.

    Usage :

    ```python
    cache = LLMCache('data/saves/<id>/llm_cache.sqlite')
    key = compute_cache_key(prompt, 'qwen3-4b', 0.7)
    cached = cache.get(key)
    if cached is None:
        result = await llama_inference(...)
        cache.set(key, result, model_id='qwen3-4b', temperature=0.7,
                  prompt_chars=len(prompt))
    else:
        result = cached
    ```
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = self._initialize_db()
        self._gets: int = 0
        self._hits: int = 0

    def _initialize_db(self) -> sqlite3.Connection:
        if self._db_path is None:
            conn = sqlite3.connect(":memory:")
        else:
            p = Path(self._db_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(p))
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA_SQL)
        conn.commit()
        return conn

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("LLMCache is closed.")
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> LLMCache:
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

    # --- API ---------------------------------------------------------------

    def get(self, key: str) -> Any | None:
        """Recupere une entree. None si miss. Incremente le compteur hits."""
        self._gets += 1
        row = self.conn.execute(
            "SELECT payload_json FROM llm_cache WHERE cache_key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        self._hits += 1
        # incremente hits pour stats
        self.conn.execute(
            "UPDATE llm_cache SET hits = hits + 1 WHERE cache_key = ?",
            (key,),
        )
        self.conn.commit()
        try:
            return json.loads(row["payload_json"])
        except (json.JSONDecodeError, TypeError):
            return None

    def set(
        self,
        key: str,
        value: Any,
        *,
        model_id: str = "unknown",
        temperature: float = 0.0,
        prompt_chars: int = 0,
    ) -> None:
        """Stocke une entree. ON CONFLICT REPLACE."""
        payload = json.dumps(value, ensure_ascii=False, default=str)
        self.conn.execute(
            """
            INSERT INTO llm_cache (
                cache_key, payload_json, model_id, temperature,
                prompt_chars, created_at_ts, hits
            ) VALUES (?, ?, ?, ?, ?, ?, 0)
            ON CONFLICT(cache_key) DO UPDATE SET
                payload_json = excluded.payload_json,
                model_id = excluded.model_id,
                temperature = excluded.temperature,
                prompt_chars = excluded.prompt_chars
            """,
            (key, payload, model_id, temperature, prompt_chars, time.time()),
        )
        self.conn.commit()

    def has(self, key: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM llm_cache WHERE cache_key = ? LIMIT 1", (key,),
        ).fetchone()
        return row is not None

    def delete(self, key: str) -> bool:
        cur = self.conn.execute(
            "DELETE FROM llm_cache WHERE cache_key = ?", (key,),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def clear_all(self) -> None:
        self.conn.execute("DELETE FROM llm_cache")
        self.conn.commit()

    def size(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM llm_cache",
        ).fetchone()
        return int(row["c"])

    @property
    def hit_rate(self) -> float:
        """Hit rate runtime du process courant (gets vs hits depuis open)."""
        if self._gets == 0:
            return 0.0
        return self._hits / self._gets

    @property
    def stats(self) -> dict[str, int | float]:
        return {
            "gets": self._gets,
            "hits": self._hits,
            "hit_rate": self.hit_rate,
            "size": self.size(),
        }


__all__ = ["LLMCache", "compute_cache_key"]
