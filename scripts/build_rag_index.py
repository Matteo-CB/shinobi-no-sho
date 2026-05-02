"""Build l'index RAG complet et le packagise en tar.gz pour GitHub Releases.

Workflow auteur :
1. python scripts/build_rag_index.py
   -> reconstruit l'index si necessaire et cree dist/rag_index.tar.gz
   -> ecrit aussi data/embeddings/.canon_fingerprint
2. gh release create vX.Y --title "..." --notes "..." dist/rag_index.tar.gz
   ou via la web UI : https://github.com/Matteo-CB/shinobi-no-sho/releases/new

L'archive contient le repertoire data/embeddings/ entier (chroma.sqlite3 +
sous-dossiers HNSW + .canon_fingerprint).

Usage :
  python scripts/build_rag_index.py
  python scripts/build_rag_index.py --reset       # repart de zero
  python scripts/build_rag_index.py --output PATH # archive ailleurs que dist/
"""

from __future__ import annotations

import sys
import tarfile
from pathlib import Path

import typer

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from shinobi.canon.loader import load_canon  # noqa: E402
from shinobi.config import settings  # noqa: E402
from shinobi.logging_setup import configure_logging, get_logger  # noqa: E402
from shinobi.rag.bootstrap import (  # noqa: E402
    compute_canon_fingerprint,
    write_stored_fingerprint,
)
from shinobi.rag.chunker import chunk_all  # noqa: E402
from shinobi.rag.embedder import embed_texts  # noqa: E402
from shinobi.rag.store import ChromaStore  # noqa: E402

configure_logging()
logger = get_logger("build_rag_index")
cli = typer.Typer(add_completion=False, no_args_is_help=False)

DEFAULT_OUTPUT = ROOT / "dist" / "rag_index.tar.gz"


def _archive_index(output: Path) -> None:
    """Tar.gz le contenu de chroma_persist_dir vers output."""
    output.parent.mkdir(parents=True, exist_ok=True)
    embeddings_dir = settings.chroma_persist_dir
    print(f"Archivage de {embeddings_dir} -> {output} ...")
    with tarfile.open(output, "w:gz") as tar:
        for child in sorted(embeddings_dir.rglob("*")):
            if child.is_file():
                tar.add(child, arcname=child.relative_to(embeddings_dir))
    size_mb = output.stat().st_size / (1024 * 1024)
    print(f"Archive creee : {output} ({size_mb:.1f} Mo)")
    print()
    print("Prochaine etape : publier sur GitHub Releases :")
    print(f"  gh release create vX.Y --title 'RAG index vX.Y' {output}")
    print("  ou via https://github.com/Matteo-CB/shinobi-no-sho/releases/new")


@cli.command()
def build(
    reset: bool = typer.Option(
        False, "--reset", help="Reset des collections avant build"
    ),
    output: Path = typer.Option(
        DEFAULT_OUTPUT,
        "--output",
        help="Chemin de sortie pour l'archive .tar.gz",
    ),
    skip_archive: bool = typer.Option(
        False, "--skip-archive", help="Build l'index mais ne cree pas l'archive"
    ),
    batch_size: int = typer.Option(64, help="Taille des batches d'embedding"),
) -> None:
    """Build l'index RAG + packagise pour distribution."""
    canon = load_canon()
    chunks = chunk_all(canon)
    total = len(chunks)
    logger.info("build_start", total_chunks=total)

    store = ChromaStore()
    if reset:
        for col in (
            "character",
            "technique",
            "clan",
            "village",
            "event",
            "lore",
            "dialogue",
            "crossdomain",
        ):
            store.reset_collection(col)
        logger.info("build_reset_done")

    # Embeddings par batch (avec progression visible)
    for i in range(0, total, batch_size):
        batch = chunks[i : i + batch_size]
        texts = [c.text for c in batch]
        vecs = embed_texts(texts, batch_size=batch_size)
        store.add_chunks(batch, vecs)
        done = min(i + batch_size, total)
        print(f"  embedded {done}/{total} chunks")

    fingerprint = compute_canon_fingerprint()
    write_stored_fingerprint(fingerprint)
    print(f"Fingerprint canon : {fingerprint[:16]}...")

    if skip_archive:
        print(f"Index pret dans {settings.chroma_persist_dir}")
        return

    _archive_index(output)


@cli.command()
def archive_only(
    output: Path = typer.Option(
        DEFAULT_OUTPUT,
        "--output",
        help="Chemin de sortie pour l'archive .tar.gz",
    ),
) -> None:
    """Suppose l'index deja construit. Ecrit le fingerprint courant + tar.gz l'index."""
    if not (settings.chroma_persist_dir / "chroma.sqlite3").exists():
        print(
            f"[ERREUR] Aucun index trouve dans {settings.chroma_persist_dir}. "
            "Lance d'abord: python scripts/build_rag_index.py build"
        )
        raise typer.Exit(code=1)
    fingerprint = compute_canon_fingerprint()
    write_stored_fingerprint(fingerprint)
    print(f"Fingerprint canon : {fingerprint[:16]}...")
    _archive_index(output)


@cli.command()
def fingerprint() -> None:
    """Affiche juste le fingerprint courant du canon (sans build)."""
    fp = compute_canon_fingerprint()
    print(fp)


if __name__ == "__main__":
    cli()
