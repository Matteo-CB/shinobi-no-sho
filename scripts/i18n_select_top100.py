"""Phase i18n.6.A : selectionne les 100 chars top a pre-traduire.

Selection per docs/14_i18n.md L452-457 :
1. Tous les chars dans `data/canon/deep_motivations.json` (~50)
2. Tous les `political_forces.factions[].leader_id` (~30, chevauchement)
3. Tous les `divergence_points.involved_canon_ids` (~40, chevauchement)
4. Completer jusqu'a 100 par notoriete : `kekkei_genkai` non-vide OU `tailed_beast` non-null

Filtre supplementaire : seuls les chars ayant > 50 chars dans au moins une des
3 sections wiki (`Background`, `Personality`, `Abilities`) sont retenus.

Usage :
    python scripts/i18n_select_top100.py [--out data/i18n/wiki/_top100.json]

Output : JSON sorted dict {char_id: {sources: [...], wiki_section_chars: int}}
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CANONICAL = ROOT / "data" / "canonical"
CANON = ROOT / "data" / "canon"
DEFAULT_OUT = ROOT / "data" / "i18n" / "wiki" / "_top100.json"

WIKI_SECTIONS = ["Background", "Personality", "Abilities"]
MIN_SECTION_CHARS = 50  # Filtre : au moins une section avec contenu utile


def load_characters() -> dict[str, dict[str, Any]]:
    data = json.loads((CANONICAL / "characters.json").read_text(encoding="utf-8"))
    return {c["id"]: c for c in data}


def load_deep_motivations() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((CANON / "deep_motivations.json").read_text(encoding="utf-8"))
    return data


def load_political_forces() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((CANON / "political_forces.json").read_text(encoding="utf-8"))
    return data


def load_divergence_points() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((CANON / "divergence_points.json").read_text(encoding="utf-8"))
    return data


def has_wiki_content(char: dict[str, Any]) -> tuple[bool, int]:
    ws = char.get("wiki_sections", {}) or {}
    total = 0
    has_any = False
    for section in WIKI_SECTIONS:
        v = ws.get(section, "")
        sz = len(str(v))
        total += sz
        if sz > MIN_SECTION_CHARS:
            has_any = True
    return (has_any, total)


def select_top_100() -> dict[str, dict[str, Any]]:
    chars = load_characters()
    dm = load_deep_motivations()
    pf = load_political_forces()
    dp = load_divergence_points()

    sources: dict[str, list[str]] = {}

    def add(cid: str, source: str) -> None:
        if cid not in chars:
            return
        ok, _ = has_wiki_content(chars[cid])
        if not ok:
            return
        sources.setdefault(cid, []).append(source)

    # 1. deep_motivations
    for cid in dm:
        add(cid, "deep_motivations")

    # 2. faction leaders
    for f in pf.get("factions", []):
        lid = f.get("leader_id")
        if lid:
            add(lid, "faction_leader")

    # 3. divergence_points involved
    for ev in dp.get("divergence_points", []):
        for k, v in ev.items():
            if "involved" in k.lower() and isinstance(v, list):
                for cid in v:
                    if isinstance(cid, str):
                        add(cid, "divergence_involved")

    # 4. Notoriety completion (kekkei_genkai non-empty OR tailed_beast non-null)
    notoriety: list[tuple[str, int]] = []
    for cid, c in chars.items():
        if cid in sources:
            continue
        if c.get("kekkei_genkai") or c.get("tailed_beast"):
            ok, total = has_wiki_content(c)
            if ok:
                notoriety.append((cid, total))
    # Plus le total wiki est gros, plus le perso est important
    notoriety.sort(key=lambda kv: -kv[1])

    for cid, _ in notoriety:
        if len(sources) >= 100:
            break
        sources[cid] = ["notoriety"]

    # Build sorted output
    out: dict[str, dict[str, Any]] = {}
    for cid in sorted(sources):
        c = chars[cid]
        _, total = has_wiki_content(c)
        out[cid] = {
            "sources": sources[cid],
            "name_romaji": c.get("name_romaji", ""),
            "wiki_section_chars": total,
        }
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"Output JSON path (default: {DEFAULT_OUT.relative_to(ROOT)})",
    )
    args = parser.parse_args()

    selection = select_top_100()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(selection, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    # Stats par source
    src_counts: dict[str, int] = {}
    for entry in selection.values():
        for s in entry["sources"]:
            src_counts[s] = src_counts.get(s, 0) + 1
    print(f"Selected {len(selection)} chars:", file=sys.stderr)
    for s, c in sorted(src_counts.items(), key=lambda x: -x[1]):
        print(f"  {s}: {c}", file=sys.stderr)
    avg_chars = sum(e["wiki_section_chars"] for e in selection.values()) // max(1, len(selection))
    total_chars = sum(e["wiki_section_chars"] for e in selection.values())
    print(f"Wiki content : avg {avg_chars:,} chars/char, total {total_chars:,} chars", file=sys.stderr)
    print(f"Output: {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
