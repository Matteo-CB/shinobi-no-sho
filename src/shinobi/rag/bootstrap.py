"""Bootstrap de l'index RAG : fingerprint + telechargement depuis GitHub Releases.

Au demarrage du jeu :
1. Si data/embeddings/chroma.sqlite3 existe ET fingerprint correspond au canon -> rien a faire.
2. Sinon, tente de telecharger l'index pre-build depuis la derniere GitHub Release.
3. Si le telechargement echoue (offline, asset absent), fallback sur build local.

Fingerprint = SHA256 du contenu trie de tous les fichiers data/canonical/*.json.
Permet d'invalider automatiquement si le canon est modifie.
"""

from __future__ import annotations

import hashlib
import shutil
import tarfile
import urllib.error
import urllib.request

from shinobi.config import settings
from shinobi.logging_setup import get_logger

logger = get_logger(__name__)

# URL de la release qui contient l'index pre-build (rag_index.tar.gz).
# Le launcher utilise /latest pour rester aligne avec la derniere version publiee.
RELEASE_INDEX_URL = (
    "https://github.com/Matteo-CB/shinobi-no-sho/releases/latest/download/rag_index.tar.gz"
)
FINGERPRINT_FILENAME = ".canon_fingerprint"
DOWNLOAD_TIMEOUT_SECONDS = 60
EXPECTED_DB_FILE = "chroma.sqlite3"


def compute_canon_fingerprint() -> str:
    """Hash deterministe du contenu canon (tri des fichiers, contenu binaire)."""
    canon_dir = settings.canonical_data_dir
    if not canon_dir.exists():
        return "no-canon"
    h = hashlib.sha256()
    for path in sorted(canon_dir.rglob("*.json")):
        rel = path.relative_to(canon_dir).as_posix()
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def read_stored_fingerprint() -> str | None:
    """Lit le fingerprint enregistre avec l'index courant (None si absent)."""
    fp_path = settings.chroma_persist_dir / FINGERPRINT_FILENAME
    if not fp_path.exists():
        return None
    try:
        return fp_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def write_stored_fingerprint(fingerprint: str) -> None:
    """Ecrit le fingerprint courant dans le repertoire chroma."""
    settings.chroma_persist_dir.mkdir(parents=True, exist_ok=True)
    fp_path = settings.chroma_persist_dir / FINGERPRINT_FILENAME
    fp_path.write_text(fingerprint, encoding="utf-8")


def index_is_present() -> bool:
    """Verifie qu'un fichier ChromaDB existe et n'est pas vide."""
    db_path = settings.chroma_persist_dir / EXPECTED_DB_FILE
    return db_path.exists() and db_path.stat().st_size > 0


def index_is_up_to_date() -> bool:
    """L'index est present ET son fingerprint matche le canon courant."""
    if not index_is_present():
        return False
    stored = read_stored_fingerprint()
    if stored is None:
        # Index existant sans fingerprint : on lui fait confiance pour ne pas
        # casser des installs existants. Le user peut forcer un rebuild via
        # python scripts/rebuild_embeddings.py rebuild --reset.
        return True
    current = compute_canon_fingerprint()
    return stored == current


def download_index_from_release(*, url: str = RELEASE_INDEX_URL) -> bool:
    """Telecharge l'index pre-build depuis GitHub Releases. Retourne True si succes."""
    target_dir = settings.chroma_persist_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    tmp_archive = target_dir.parent / "_rag_index_download.tar.gz"
    try:
        logger.info("rag_download_start", url=url)
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "ShinobiNoSho-Bootstrap/1.0"},
        )
        with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT_SECONDS) as resp:
            tmp_archive.write_bytes(resp.read())
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError) as exc:
        logger.warning("rag_download_failed", error=str(exc))
        if tmp_archive.exists():
            try:
                tmp_archive.unlink()
            except OSError:
                pass
        return False

    # Nettoie l'index existant pour eviter les conflits
    for item in target_dir.iterdir():
        if item.name == FINGERPRINT_FILENAME:
            continue
        try:
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                item.unlink()
        except OSError:
            pass

    try:
        with tarfile.open(tmp_archive, "r:gz") as tar:
            tar.extractall(target_dir, filter="data")
        logger.info("rag_download_complete", target_dir=str(target_dir))
        return True
    except (tarfile.TarError, OSError) as exc:
        logger.warning("rag_extract_failed", error=str(exc))
        return False
    finally:
        try:
            tmp_archive.unlink()
        except OSError:
            pass


def bootstrap_index(*, console=None, allow_local_build: bool = True) -> str:
    """Garantit qu'un index RAG est pret avant de lancer le jeu.

    Strategie :
    1. Si l'index est deja a jour (fingerprint match), retourne 'ok'.
    2. Sinon, tente de telecharger depuis GitHub Releases.
    3. Si echec et allow_local_build, lance auto_rebuild_if_empty() (build local).
    4. Sinon retourne 'failed'.

    Le parametre console est un rich.console.Console optionnel pour le feedback UX.
    """

    def _say(msg: str) -> None:
        if console is not None:
            console.print(msg)
        else:
            print(msg)

    if index_is_up_to_date():
        return "ok"

    current_fp = compute_canon_fingerprint()

    if not index_is_present():
        _say("[dim]Index RAG absent : telechargement de l'index pre-build...[/dim]")
    else:
        _say("[dim]Canon modifie depuis le dernier index : remise a jour...[/dim]")

    if download_index_from_release():
        write_stored_fingerprint(current_fp)
        _say("[green]Index RAG telecharge.[/green]")
        return "downloaded"

    if not allow_local_build:
        _say("[red]Index RAG indisponible et build local desactive.[/red]")
        return "failed"

    _say(
        "[yellow]Telechargement impossible, fallback sur indexation locale "
        "(peut prendre 1-3 minutes la premiere fois)...[/yellow]"
    )
    try:
        from shinobi.canon.loader import load_canon
        from shinobi.rag.chunker import chunk_all
        from shinobi.rag.embedder import embed_texts
        from shinobi.rag.store import ChromaStore

        canon = load_canon()
        chunks = chunk_all(canon)
        store = ChromaStore()
        for i in range(0, len(chunks), 64):
            batch = chunks[i : i + 64]
            vecs = embed_texts([c.text for c in batch], batch_size=64)
            store.add_chunks(batch, vecs)
        write_stored_fingerprint(current_fp)
        _say(f"[green]Index RAG construit localement ({len(chunks)} chunks).[/green]")
        return "built"
    except Exception as exc:
        logger.error("rag_local_build_failed", error=str(exc))
        _say(f"[red]Echec du build local : {exc}[/red]")
        return "failed"
