"""Enrichit les datasets canon manuels avec wiki_sections.

Strategie multi-langue robuste :
1. Construit un INDEX INVERSE : pour chaque page Narutopedia, extrait TOUS
   ses noms (titre, romaji via templates translation, kanji, anglais, alias
   detectes dans le wikitext).
2. Charge les redirects MediaWiki : "Shintenshin no Jutsu" est un redirect
   vers la vraie page (ex: "Mind Body Switch Technique").
3. Pour chaque entree dataset, tente le matching dans cet index avec :
   - name_romaji (japonais romanise)
   - name_fr (peut etre traduction litterale qui matche aussi)
   - name_en (futur, anglais)
   - id (slug, peut matcher aussi)
   - epithets, aliases
4. Recherche fuzzy (lower, sans 'no jutsu'/'technique', etc.) en dernier recours.

Permet l'extension multi-langue : ajoute des champs name_<lang> au canon ->
le matching marche tout seul.

Usage : python scripts/enrich_with_wiki_sections.py
"""

from __future__ import annotations

import json
import re
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

_BORING = {"see also", "references", "external links", "navigation", "in other media", "gallery"}
_MAX_CHARS_PER_SECTION = 4000
_MAX_SECTIONS = 20

# Suffixes a strip pour le fuzzy matching (jutsu/technique sont des suffixes courants).
_STRIP_SUFFIXES = [
    " no jutsu", " jutsu", " technique", " art", " release", " style",
    " ryu", " ryū", " release", "'s", " of",
]


def _normalize(s: str) -> str:
    """Normalisation aggressive pour matching cross-lang."""
    if not s:
        return ""
    out = s.lower().strip()
    # Strip kanji/hiragana entre parentheses
    out = re.sub(r"[　-鿿＀-￯]+", "", out)
    # Strip ponctuation
    out = re.sub(r"[^\w\s-]", " ", out)
    # Strip diacritics simples (n'importe quel caractere accentue -> sans accent)
    out = re.sub(r"[āáàâ]", "a", out)
    out = re.sub(r"[ēéèê]", "e", out)
    out = re.sub(r"[īíìî]", "i", out)
    out = re.sub(r"[ōóòô]", "o", out)
    out = re.sub(r"[ūúùû]", "u", out)
    # Multiple spaces
    out = re.sub(r"\s+", " ", out).strip()
    return out


def _strip_suffixes(s: str) -> str:
    """Retire les suffixes communs pour matching tolerant."""
    n = _normalize(s)
    for suf in _STRIP_SUFFIXES:
        if n.endswith(suf):
            n = n[: -len(suf)].strip()
    return n


def _extract_names_from_wikitext(wikitext: str) -> set[str]:
    """Extrait tous les noms candidats d'un wikitext : translation templates,
    redirects, infobox unnamed, etc."""
    names: set[str] = set()
    # Templates {{translation|english|kanji|romaji}}
    for m in re.finditer(r"\{\{translation\|([^}|]+)\|([^}|]+)\|([^}|]+)", wikitext, re.IGNORECASE):
        eng, _kanji, romaji = m.group(1), m.group(2), m.group(3)
        names.add(eng.strip().strip("'"))
        names.add(romaji.strip().strip("'"))
    # Templates {{translation|english|kanji}}
    for m in re.finditer(r"\{\{translation\|([^}|]+)\|([^}|]+)\}\}", wikitext, re.IGNORECASE):
        eng = m.group(1).strip().strip("'")
        names.add(eng)
    # |unnamed= et |romaji= dans infobox
    for m in re.finditer(r"\|\s*(?:unnamed|romaji|english|name)\s*=\s*([^|\n]+)", wikitext, re.IGNORECASE):
        names.add(m.group(1).strip().strip("'"))
    # Names mentionnes dans la premiere ligne en gras '''Name'''
    for m in re.finditer(r"'''([^']+)'''", wikitext[:2000]):
        names.add(m.group(1).strip())
    return {n for n in names if 2 < len(n) < 100}


def _build_alias_index() -> tuple[dict[str, int], dict[str, int]]:
    """Construit deux index :
    - exact_index: name_normalized -> pageid (matching strict)
    - fuzzy_index: name_stripped_suffixes -> pageid (matching tolerant)
    """
    exact: dict[str, int] = {}
    fuzzy: dict[str, int] = {}
    n_pages = 0
    for meta_file in META_DIR.glob("*.json"):
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            pageid = meta.get("pageid")
            title = (meta.get("title") or "").strip()
            if not pageid or not title:
                continue
            # Skip les pages non-content (Help, Category, File, etc.)
            if ":" in title and not title.startswith("List"):
                continue
            n_pages += 1
            # Index par titre
            exact.setdefault(_normalize(title), pageid)
            fuzzy.setdefault(_strip_suffixes(title), pageid)
            # Lit le wikitext pour extraire les noms alternatifs (couteux mais one-shot)
            files = list(PAGES_DIR.glob(f"{pageid}_*.wikitext"))
            if not files:
                continue
            try:
                wikitext = files[0].read_text(encoding="utf-8", errors="replace")[:5000]
            except OSError:
                continue
            for alt in _extract_names_from_wikitext(wikitext):
                exact.setdefault(_normalize(alt), pageid)
                fuzzy.setdefault(_strip_suffixes(alt), pageid)
        except Exception:
            continue
    print(f"  Index construit : {n_pages} pages, {len(exact)} alias exacts, {len(fuzzy)} alias fuzzy")
    return exact, fuzzy


def _find_page(
    candidates: list[str], exact_index: dict[str, int], fuzzy_index: dict[str, int]
) -> int | None:
    """Cherche la pageid pour le premier candidat qui matche (exact > fuzzy)."""
    for c in candidates:
        if not c:
            continue
        n = _normalize(c)
        if n in exact_index:
            return exact_index[n]
    # Fallback fuzzy
    for c in candidates:
        if not c:
            continue
        f = _strip_suffixes(c)
        if f in fuzzy_index:
            return fuzzy_index[f]
    return None


def _extract_sections_from_pageid(pageid: int) -> dict[str, str]:
    files = list(PAGES_DIR.glob(f"{pageid}_*.wikitext"))
    if not files:
        return {}
    try:
        wikitext = files[0].read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
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


# Mapping id -> noms anglais Narutopedia connus pour les entites dont le
# romaji/fr ne matche pas le titre de page anglais. Etendable a l'infini.
# Pour multi-langue futur : remplacer par aliases_by_lang dans le data canon.
_KNOWN_ENGLISH_NAMES: dict[str, list[str]] = {
    # Tailed beasts
    "juubi": ["Ten-Tails", "Ten Tails", "Jubi"],
    # Organizations
    "mount_myoboku_sages": ["Mount Myoboku", "Toad Sages"],
    # Hidens
    "shintenshin_no_jutsu": ["Mind Body Switch Technique"],
    "kage_mane_no_jutsu": ["Shadow Imitation Technique", "Shadow Possession Jutsu"],
    "baika_no_jutsu": ["Multi-Size Technique", "Expansion Jutsu"],
    "mushi_yose_no_jutsu": ["Insect Gathering Technique"],
    "jujin_bunshin": ["Beast Human Clone", "Man-Beast Clone"],
    "juuken": ["Gentle Fist"],
    "kugutsu_no_jutsu": ["Puppet Technique"],
    "suika_no_jutsu": ["Hydrification Technique"],
    "ougon_no_kuro_kogane_no_seishin": ["Spirit of the Black Gold"],
    "irezumi_fuin": ["Cursed Tongue Eradication", "Tattoo Seal"],
    "kurama_genjutsu": ["Kurama Clan", "Genjutsu of the Kurama Clan"],
    "doku_kakou": ["Poison Manufacture"],
    "hagoromo_kenjutsu": ["Sword of the Thunder God", "Hagoromo Style Sword"],
    "ito_no_jutsu": ["String Reeling Technique", "Wire Strings"],
    "shouten_no_jutsu": ["Body Elevation Technique", "Ascension Technique"],
}


def _candidates_for_entry(entry: dict, candidate_keys: list[str]) -> list[str]:
    """Genere une liste etendue de noms candidats pour une entree."""
    names: list[str] = []
    for key in candidate_keys:
        v = entry.get(key)
        if isinstance(v, str) and v.strip():
            names.append(v)
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, str):
                    names.append(item)
    # Aliases connus pour cette entree
    eid = entry.get("id")
    if eid in _KNOWN_ENGLISH_NAMES:
        names.extend(_KNOWN_ENGLISH_NAMES[eid])
    # Variations de l'id (snake_case -> Title Case et words)
    for n in list(names):
        if "_" in n:
            names.append(n.replace("_", " ").title())
            names.append(n.replace("_", " "))
    return names


def _enrich_dataset(
    json_filename: str,
    candidate_keys: list[str],
    exact_index: dict[str, int],
    fuzzy_index: dict[str, int],
) -> None:
    path = CANON_DIR / json_filename
    if not path.exists():
        print(f"  SKIP {json_filename} (introuvable)")
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        print(f"  SKIP {json_filename} (pas une liste)")
        return
    enriched = 0
    skipped_names: list[str] = []
    for entry in data:
        if entry.get("wiki_sections"):
            enriched += 1
            continue
        candidates = _candidates_for_entry(entry, candidate_keys)
        pageid = _find_page(candidates, exact_index, fuzzy_index)
        if pageid is None:
            skipped_names.append(entry.get("id", "?"))
            continue
        sections = _extract_sections_from_pageid(pageid)
        if sections:
            entry["wiki_sections"] = sections
            enriched += 1
        else:
            skipped_names.append(entry.get("id", "?"))
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(f"  {json_filename}: {enriched}/{len(data)} enrichis")
    if skipped_names:
        print(f"    non trouves: {', '.join(skipped_names)}")


def main() -> None:
    print("Construction de l'index alias multi-source (peut prendre 1-2 min)...")
    exact_index, fuzzy_index = _build_alias_index()
    print()
    print("Enrichissement des datasets manuels...")
    _enrich_dataset(
        "tailed_beasts.json",
        candidate_keys=["name_romaji", "name_fr", "id", "epithets"],
        exact_index=exact_index,
        fuzzy_index=fuzzy_index,
    )
    _enrich_dataset(
        "organizations.json",
        candidate_keys=["name_fr", "name_romaji", "id"],
        exact_index=exact_index,
        fuzzy_index=fuzzy_index,
    )
    _enrich_dataset(
        "hiden.json",
        candidate_keys=["name_romaji", "name_fr", "id"],
        exact_index=exact_index,
        fuzzy_index=fuzzy_index,
    )
    print("Fait.")


if __name__ == "__main__":
    main()
