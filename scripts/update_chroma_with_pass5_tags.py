"""Injecte les tags Pass 5 (arc, year_min, year_max, tier, entities)
dans les metadata des chunks ChromaDB.

Lit data/canonical/_pass5_output/<safe_chunk_id>.json (output de
pass5_tag_chunks.py parse) et update les metadata des chunks
correspondants dans ChromaDB sans re-embedder.

Egalement applique un sentinel `year_max=TEMPORAL_SENTINEL` (9999) aux
chunks NON taggees, pour que le filtre temporel du retriever (`$lte`)
les laisse passer comme lore generique. Sans ca, ChromaDB exclurait les
chunks sans la metadata 'year_max'.

Le mapping safe_chunk_id <-> chunk_id (avec :) suit la convention de
pass5_tag_chunks._safe_filename(): ':' -> '__'.

Usage : uv run python scripts/update_chroma_with_pass5_tags.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from shinobi.canon.loader import load_canon
from shinobi.rag.chunker import chunk_all
from shinobi.rag.store import ChromaStore
from shinobi.retrieval.chroma_adapter import TEMPORAL_SENTINEL

PASS5_OUTPUT_DIR = ROOT / "data" / "canonical" / "_pass5_output"


def _restore_chunk_id(safe_filename: str) -> str:
    """Inverse de _safe_filename : '__' -> ':'."""
    return safe_filename.replace("__", ":")


def main() -> int:
    if not PASS5_OUTPUT_DIR.exists():
        print(f"!!! {PASS5_OUTPUT_DIR} missing — run pass5_tag_chunks.py parse first")
        return 2

    files = sorted(PASS5_OUTPUT_DIR.glob("*.json"))
    print(f"Loading {len(files)} pass5 outputs...")

    tag_map: dict[str, dict] = {}
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        cid = data.get("chunk_id") or _restore_chunk_id(f.stem)
        meta_update: dict[str, str | int | None] = {}
        for k in ("arc", "tier"):
            v = data.get(k)
            if v is not None:
                meta_update[k] = str(v)
        for k in ("year_min", "year_max"):
            v = data.get(k)
            if isinstance(v, int):
                meta_update[k] = v
        ents = data.get("entities_mentioned") or []
        if isinstance(ents, list):
            # Chroma metadata accepte str/int/float/bool, donc serialize en CSV.
            meta_update["entities_mentioned"] = ",".join(
                e for e in ents if isinstance(e, str)
            )
        if meta_update:
            tag_map[cid] = meta_update

    print(f"  {len(tag_map)} chunks ont des tags Pass 5 a injecter")

    # Charge tous les chunks via chunk_all pour appliquer le sentinel
    # aux chunks non tagges.
    canon = load_canon()
    all_chunks = chunk_all(canon)
    n_with_tags = 0
    for chunk in all_chunks:
        if chunk.id in tag_map:
            n_with_tags += 1
            # Si Pass 5 n'a pas attribue year_max, on met le sentinel
            if "year_max" not in tag_map[chunk.id]:
                tag_map[chunk.id]["year_max"] = TEMPORAL_SENTINEL
        else:
            tag_map[chunk.id] = {
                "arc": "unknown",
                "year_max": TEMPORAL_SENTINEL,
                "tier": "manga",
            }
    print(f"  {n_with_tags} chunks avec tags Pass 5, "
          f"{len(all_chunks) - n_with_tags} chunks recevant le sentinel")

    store = ChromaStore()
    # On update via upsert avec id seulement et metadata. Chroma garde le
    # vector et le document existants si on ne les fournit pas.
    by_collection: dict[str, list[tuple[str, dict]]] = {}
    for cid, meta in tag_map.items():
        # cid format : "<type>:<source_id>[:wiki:<section>]"
        ctype = cid.split(":", 1)[0] if ":" in cid else "crossdomain"
        by_collection.setdefault(ctype, []).append((cid, meta))
        by_collection.setdefault("crossdomain", []).append((cid, meta))

    n_updated = 0
    for col_name, items in by_collection.items():
        col = store.collection(col_name)
        # Update par batch de 200
        for i in range(0, len(items), 200):
            batch = items[i:i + 200]
            ids = [x[0] for x in batch]
            metadatas = [x[1] for x in batch]
            try:
                col.update(ids=ids, metadatas=metadatas)
                n_updated += len(batch)
            except Exception as exc:
                print(f"  WARN col={col_name} batch update failed: {exc}")
        print(f"  collection {col_name}: {len(items)} updates queued")

    print(f"Done. {n_updated} chunk metadata records updated in ChromaDB.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
