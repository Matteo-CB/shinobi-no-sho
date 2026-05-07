"""Post-process Phase H : normalise les character_id refs LLM vers canon ids.

L'LLM produit parfois des ids en convention 'given_clan' (mei_terumi) au
lieu de canon 'clan_given' (terumi_mei). Ce script :

1. Charge canon characters.json -> canonical ids set
2. Pour chaque ref dans 9.3 (leader_id, members, allies, enemies)
   et dans 9.2 keys :
   - Si l'id n'est pas canon mais que reversed_id l'est, swap.
   - Sinon, retire silencieusement de la liste members/allies/enemies.
3. Reecrit les fichiers normalises.

Idempotent (run multiple fois safe).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


CANONICAL_CHARS = (
    Path(__file__).parent.parent.parent
    / "data" / "canonical" / "characters.json"
)
POLITICAL_FORCES = (
    Path(__file__).parent.parent.parent
    / "data" / "canon" / "political_forces.json"
)


def _normalize_id(raw: str | None, canon_ids: set[str]) -> str | None:
    """Si raw n'est pas dans canon_ids, essaie 1 reversed_id swap.

    Retourne canon id si trouve, sinon None.
    """
    if not raw or not isinstance(raw, str):
        return None
    if raw in canon_ids:
        return raw
    parts = raw.split("_", 1)
    if len(parts) == 2:
        reversed_id = f"{parts[1]}_{parts[0]}"
        if reversed_id in canon_ids:
            return reversed_id
    return None


def main() -> int:
    canon_chars = json.loads(CANONICAL_CHARS.read_text(encoding="utf-8"))
    if isinstance(canon_chars, dict):
        canon_ids = set(canon_chars.keys())
    else:
        canon_ids = {c["id"] for c in canon_chars if c.get("id")}
    print(f"Canon character_ids : {len(canon_ids)}")

    forces = json.loads(POLITICAL_FORCES.read_text(encoding="utf-8"))
    factions = forces["factions"]

    leaders_swapped = 0
    leaders_removed = 0
    members_swapped = 0
    members_removed = 0

    for f in factions:
        # leader_id
        leader = f.get("leader_id")
        if leader and leader not in canon_ids:
            normalized = _normalize_id(leader, canon_ids)
            if normalized:
                f["leader_id"] = normalized
                leaders_swapped += 1
            else:
                f["leader_id"] = None
                leaders_removed += 1
        # members
        new_members = []
        for m in f.get("members", []):
            if m in canon_ids:
                new_members.append(m)
                continue
            normalized = _normalize_id(m, canon_ids)
            if normalized:
                new_members.append(normalized)
                members_swapped += 1
            else:
                members_removed += 1
        f["members"] = new_members
        # allies / enemies (faction ids OR character ids - reference cross-faction)
        # On garde tels quels (sub-villages, factions ids non-character).

    POLITICAL_FORCES.write_text(
        json.dumps(forces, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Leaders swapped : {leaders_swapped}")
    print(f"Leaders set None : {leaders_removed}")
    print(f"Members swapped : {members_swapped}")
    print(f"Members removed : {members_removed}")
    print(f"Saved to {POLITICAL_FORCES}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
