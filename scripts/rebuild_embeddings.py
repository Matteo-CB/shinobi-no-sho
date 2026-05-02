"""Indexe tous les datasets canoniques dans ChromaDB.

Usage : python scripts/rebuild_embeddings.py [--reset]
"""

from __future__ import annotations

import sys
from pathlib import Path

import typer

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from shinobi.canon.loader import load_canon  # noqa: E402
from shinobi.logging_setup import configure_logging, get_logger  # noqa: E402
from shinobi.rag.chunker import chunk_all  # noqa: E402
from shinobi.rag.embedder import embed_texts  # noqa: E402
from shinobi.rag.store import ChromaStore  # noqa: E402

configure_logging()
logger = get_logger("rebuild_embeddings")
cli = typer.Typer(add_completion=False, no_args_is_help=False)


@cli.command()
def rebuild(
    reset: bool = typer.Option(False, "--reset", help="Vide ChromaDB d'abord"),
    batch_size: int = typer.Option(64, help="Taille des batches d'embedding"),
) -> None:
    """Reconstruit toutes les collections RAG."""
    canon = load_canon()
    chunks = chunk_all(canon)
    logger.info("rebuild_start", total_chunks=len(chunks))

    store = ChromaStore()
    if reset:
        for col in (
            "character", "technique", "clan", "village", "event",
            "lore", "dialogue", "crossdomain",
        ):
            store.reset_collection(col)
        logger.info("rebuild_reset_done")

    # Embeddings par batch
    total = len(chunks)
    for i in range(0, total, batch_size):
        batch = chunks[i : i + batch_size]
        texts = [c.text for c in batch]
        vecs = embed_texts(texts, batch_size=batch_size)
        store.add_chunks(batch, vecs)
        logger.info("rebuild_batch", done=min(i + batch_size, total), total=total)

    logger.info("rebuild_complete", total=total)
    print(f"Indexation terminee : {total} chunks dans ChromaDB")


def is_index_empty(store: ChromaStore | None = None) -> bool:
    """Verifie si l'index est vide (utilise par play_session pour auto-build)."""
    if store is None:
        store = ChromaStore()
    try:
        return store.count("crossdomain") == 0
    except Exception:
        return True


def auto_rebuild_if_empty() -> None:
    """Appele par play_session : reindexe automatiquement si vide."""
    store = ChromaStore()
    if not is_index_empty(store):
        return
    logger.info("rag_index_empty_auto_rebuild")
    print("Premiere utilisation : indexation des donnees canoniques (peut prendre 1-3 min)...")
    canon = load_canon()
    chunks = chunk_all(canon)
    total = len(chunks)
    for i in range(0, total, 64):
        batch = chunks[i : i + 64]
        texts = [c.text for c in batch]
        vecs = embed_texts(texts, batch_size=64)
        store.add_chunks(batch, vecs)
    print(f"  Indexation terminee : {total} chunks.")


if __name__ == "__main__":
    cli()
