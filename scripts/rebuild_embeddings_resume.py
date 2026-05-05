"""Resume une indexation Chroma incomplete sans re-embedder les chunks deja indexes.

Identifie les chunks manquants en comparant chunk_all(canon) avec les ids
deja presents dans Chroma collection 'crossdomain', puis embedde et upsert
uniquement les manquants.

Usage : uv run python scripts/rebuild_embeddings_resume.py [--batch-size 32]
"""

from __future__ import annotations

import sys
from pathlib import Path

import typer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from shinobi.canon.loader import load_canon  # noqa: E402
from shinobi.logging_setup import configure_logging, get_logger  # noqa: E402
from shinobi.rag.chunker import chunk_all  # noqa: E402
from shinobi.rag.embedder import embed_texts  # noqa: E402
from shinobi.rag.store import ChromaStore  # noqa: E402

configure_logging()
logger = get_logger("rebuild_embeddings_resume")
cli = typer.Typer(add_completion=False, no_args_is_help=False)


@cli.command()
def resume(
    batch_size: int = typer.Option(32, help="Taille des batches d'embedding"),
) -> None:
    """Embedde et upsert les chunks manquants dans Chroma."""
    canon = load_canon()
    chunks = chunk_all(canon)
    print(f"chunks attendus : {len(chunks)}")

    store = ChromaStore()
    cross = store.collection("crossdomain")
    # Recupere les ids deja presents (en blocs de 1000 pour eviter
    # les requetes trop grosses).
    existing: set[str] = set()
    n_total = cross.count()
    print(f"chunks deja indexes (crossdomain) : {n_total}")

    offset = 0
    while offset < n_total:
        chunk_size = min(2000, n_total - offset)
        result = cross.get(limit=chunk_size, offset=offset, include=[])
        existing.update(result.get("ids") or [])
        offset += chunk_size

    print(f"unique ids deja en chroma : {len(existing)}")

    missing = [c for c in chunks if c.id not in existing]
    print(f"chunks a indexer : {len(missing)}")

    if not missing:
        print("rien a faire, tous les chunks sont deja indexes.")
        return

    total = len(missing)
    for i in range(0, total, batch_size):
        batch = missing[i:i + batch_size]
        texts = [c.text for c in batch]
        vecs = embed_texts(texts, batch_size=batch_size)
        store.add_chunks(batch, vecs)
        logger.info("resume_batch", done=min(i + batch_size, total), total=total)

    logger.info("resume_complete", total=total)
    print(f"Resume termine : {total} chunks ajoutes.")


if __name__ == "__main__":
    cli()
