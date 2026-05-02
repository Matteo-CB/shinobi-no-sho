"""Enrichit les datasets canon manuels (tailed_beasts, organizations, hiden) avec
wiki_sections en cherchant la page Narutopedia correspondante par titre.

Pour chaque entree dans le JSON canonical :
1. Construit une liste de noms candidats (name_romaji, name_fr, id, etc.)
2. Cherche la page meta correspondante dans data/raw/narutopedia/meta/
3. Charge le wikitext, parse les sections via shinobi.canon.wikitext
4. Ajoute wiki_sections au dataset

Usage : python scripts/enrich_with_wiki_sections.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from shinobi.canon.wikitext import parse_wikitext, strip_wiki_markup  # noqa: E402

CANON_DIR = ROOT / "data" / "canonical"
RAW_DIR = ROOT / "data" / "raw" / "narutopedia"
META_DIR = RAW_DIR / "meta"
PAGES_DIR = RAW_DIR / "pages"

# Sections a ignorer
_BORING = {"see also", "references", "external links", "navigation", "in other media", "gallery"}
_MAX_CHARS_PER_SECTION = 4000
_MAX_SECTIONS = 20


def _build_title_index() -> dict[str, int]:
    """Construit un index lowercase_title -> pageid."""
    index: dict[str, int] = {}
    for meta_file in META_DIR.glob("*.json"):
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            title = meta.get("title", "").strip().lower()
            pageid = meta.get("pageid")
            if title and pageid:
                index[title] = pageid
        except Exception:
            continue
    return index


def _find_page(title_index: dict[str, int], candidates: list[str]) -> int | None:
    """Cherche la pageid pour le premier candidat qui matche."""
    for c in candidates:
        if not c:
            continue
        cl = c.lower().strip()
        if cl in title_index:
            return title_index[cl]
    return None


def _extract_sections_from_pageid(pageid: int) -> dict[str, str]:
    """Charge le wikitext + extrait les sections cleaned."""
    files = list(PAGES_DIR.glob(f"{pageid}_*.wikitext"))
    if not files:
        return {}
    wikitext = files[0].read_text(encoding="utf-8")
    parsed = parse_wikitext(wikitext)
    out: dict[str, list[str]] = {}
    current_h2: str | None = None
    for sec in parsed.sections:
        title = (sec.title or "").strip()
        text = (sec.body or "").strip()
        level = sec.level
        if not title or title == "(intro)":
            continue
        if title.lower() in _BORING:
            continue
        if level <= 2:
            current_h2 = title
            out.setdefault(current_h2, [])
            if text:
                out[current_h2].append(strip_wiki_markup(text))
        else:
            target = current_h2 or title
            out.setdefault(target, [])
            if text:
                out[target].append(f"[{title}] " + strip_wiki_markup(text))
    final: dict[str, str] = {}
    for title, parts in out.items():
        joined = "\n".join(p for p in parts if p).strip()
        if not joined:
            continue
        if len(joined) > _MAX_CHARS_PER_SECTION:
            joined = joined[: _MAX_CHARS_PER_SECTION - 3] + "..."
        final[title] = joined
        if len(final) >= _MAX_SECTIONS:
            break
    return final


def _enrich_dataset(json_filename: str, candidate_keys: list[str]) -> None:
    """Enrichit chaque entree du dataset avec wiki_sections."""
    path = CANON_DIR / json_filename
    if not path.exists():
        print(f"  SKIP {json_filename} (introuvable)")
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        print(f"  SKIP {json_filename} (pas une liste)")
        return
    title_index = _build_title_index()
    enriched = 0
    skipped = 0
    for entry in data:
        candidates: list[str] = []
        for key in candidate_keys:
            v = entry.get(key)
            if isinstance(v, str) and v.strip():
                candidates.append(v)
        # Pour les ids snake_case, on tente aussi de les remettre en title case
        for c in list(candidates):
            if "_" in c:
                candidates.append(c.replace("_", " ").title())
        pageid = _find_page(title_index, candidates)
        if pageid is None:
            skipped += 1
            continue
        sections = _extract_sections_from_pageid(pageid)
        if sections:
            entry["wiki_sections"] = sections
            enriched += 1
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(f"  {json_filename}: {enriched} enrichis, {skipped} non trouves")


def main() -> None:
    print("Enrichissement des datasets manuels avec wiki_sections...")
    _enrich_dataset(
        "tailed_beasts.json",
        candidate_keys=["name_romaji", "id"],
    )
    _enrich_dataset(
        "organizations.json",
        candidate_keys=["name_fr", "name_romaji", "id"],
    )
    _enrich_dataset(
        "hiden.json",
        candidate_keys=["name_fr", "name_romaji", "id"],
    )
    print("Fait.")


if __name__ == "__main__":
    main()
