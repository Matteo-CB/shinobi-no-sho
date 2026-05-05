"""Investigation post-batch Pass 2 : pourquoi le filled rate est si bas.

Compare wiki_sections originaux vs outputs Llama pour mesurer combien de
data extractible a ete laissee sur la table par le modele.

Sample :
- 20 unknowns au hasard
- 10 Uchiha sans Sharingan attestes
- 5 Senju sans Mokuton attestes
- 5 cross-check CC dryrun vs Llama batch
- diagnostic 33 not_run_through_pass2_5

Pas d'API. Pure analyse locale. Output sur stdout + fichier markdown.
"""

from __future__ import annotations

import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PASS2_OUTPUT_DIR = ROOT / "data" / "canonical" / "_pass2_output"
DRYRUN_DIR = ROOT / "data" / "canonical" / "_pass2_output_dryrun"
CHARACTERS_PATH = ROOT / "data" / "canonical" / "characters.json"
REPORT_PATH = ROOT / "data" / "canonical" / "_pass2_5_derivation_report.json"
POSTMORTEM_MD = ROOT / "research" / "pass2-batch-postmortem.md"


def load_extraction(cid: str) -> dict | None:
    f = PASS2_OUTPUT_DIR / f"{cid}.json"
    if not f.exists():
        return None
    return json.loads(f.read_text(encoding="utf-8"))


def assemble_wiki(char: dict) -> str:
    return "\n\n".join((char.get("wiki_sections") or {}).values())


def has_birth_indicator(text: str) -> tuple[bool, list[str]]:
    """Detect if the wiki text contains explicit birth_year clues.

    Heuristics :
    - "born" / "birth" near a year-relative phrase
    - "X years old when" + identifiable arc
    - explicit age statements
    """
    if not text:
        return False, []
    hits = []
    patterns = [
        r"born\s+(?:on|in|during)\s+[^.]{0,80}",
        r"\b(?:age|aged)\s+\d{1,2}\s+(?:when|at the time)",
        r"\bat\s+(?:age\s+)?\d{1,2}\s*(?:,|\.|year)",
        r"(?:younger|older)\s+(?:than|brother|sister|sibling)",
        r"\bbirth(?:day|date)\s+",
        r"born\s+during\s+the\s+\w+\s+(?:War|Period|Era)",
    ]
    for p in patterns:
        for m in re.finditer(p, text, re.IGNORECASE):
            hits.append(m.group(0)[:100])
    return bool(hits), hits


def has_sharingan_possession(text: str, char_name: str) -> tuple[bool, list[str]]:
    """Detect explicit Sharingan possession statements about THIS character."""
    if not text:
        return False, []
    fname = char_name.split()[0] if char_name else "this"
    patterns = [
        rf"\b{re.escape(fname)}\b[^.]*?\b(?:awakened|possesses|has|gained|acquired|developed|using|used|with|wielded|own)[^.]*?\bSharingan\b",
        rf"\b(?:his|her)\s+(?:own\s+)?Sharingan\b",
        rf"\bSharingan\b[^.]*?{re.escape(fname)}",
    ]
    hits = []
    for p in patterns:
        for m in re.finditer(p, text, re.IGNORECASE):
            hits.append(m.group(0)[:120])
    return bool(hits), hits


def has_mokuton_possession(text: str, char_name: str) -> tuple[bool, list[str]]:
    if not text:
        return False, []
    fname = char_name.split()[0] if char_name else "this"
    patterns = [
        rf"\b(?:Wood Release|Mokuton)\b",
        rf"\bWood\s+(?:Release|Style|Element)\b",
    ]
    hits = []
    for p in patterns:
        for m in re.finditer(p, text, re.IGNORECASE):
            hits.append(m.group(0)[:80])
    return bool(hits), hits


def count_filled_fields(extraction: dict) -> tuple[int, list[str]]:
    """Returns (n_filled, list_of_filled_fields)."""
    fields = (extraction or {}).get("fields", {}) or {}
    filled = []
    for fname, val in fields.items():
        if isinstance(val, dict) and val.get("value") not in (None, ""):
            filled.append(fname)
        elif isinstance(val, list) and len(val) > 0:
            filled.append(f"{fname}({len(val)})")
    return len(filled), filled


def main() -> int:
    random.seed(42)
    chars = json.loads(CHARACTERS_PATH.read_text(encoding="utf-8"))
    char_index = {c["id"]: c for c in chars}
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    unknowns = report["still_unknown"]

    md_lines = ["# Pass 2 batch postmortem",
                "",
                f"Total characters in batch : {len(chars)}, "
                f"with extractions : {len(unknowns) + 11}",
                "",
                "## Bilan glassois",
                "",
                "| Status | Count |",
                "|---|---:|"]

    # === 1. Sample 20 unknowns ===
    sample_unknowns = random.sample(unknowns, min(20, len(unknowns)))
    md_lines.extend(["",
                     "## 1. Sample 20 unknowns : data extractible non extraite ?",
                     "",
                     "Pour chaque perso : taille wiki, indicators birth_year detectes "
                     "par regex, fields filled par Llama. Si la wiki contient des "
                     "indicators (age explicite, 'born during X', 'younger than Y') "
                     "et que Llama a extrait 0 ou peu de fields, c'est de la sous-extraction.",
                     "",
                     "| char_id | wiki_chars | birth_indicators | n_fields_filled |"
                     "  fields_filled |",
                     "|---|---:|---:|---:|---|"])

    n_with_indicators = 0
    n_with_indicators_unfilled = 0
    for cid in sample_unknowns:
        char = char_index.get(cid)
        if not char:
            continue
        wiki = assemble_wiki(char)
        has_ind, hits = has_birth_indicator(wiki)
        ext = load_extraction(cid)
        n_filled, filled = count_filled_fields(ext) if ext else (0, [])
        if has_ind:
            n_with_indicators += 1
            if n_filled <= 3:
                n_with_indicators_unfilled += 1
        ind_str = f"YES ({len(hits)})" if has_ind else "no"
        sample_filled = ",".join(filled[:6]) + ("..." if len(filled) > 6 else "")
        md_lines.append(f"| `{cid}` | {len(wiki):,} | {ind_str} | {n_filled} | {sample_filled} |")

    md_lines.extend(["",
                     f"**Sur 20 sampled** : {n_with_indicators}/20 ont des birth indicators "
                     f"detectes par regex, dont **{n_with_indicators_unfilled} avec <= 3 "
                     f"fields filled** (potentielle sous-extraction)."])

    # === 2. Uchiha sans Sharingan ===
    uchihas = [c for c in chars if c.get("clan") == "uchiha"]
    uchiha_no_sharingan = []
    for c in uchihas:
        ext = load_extraction(c["id"])
        if not ext:
            continue
        kgs = (ext.get("fields", {}) or {}).get("kekkei_genkai_possessed") or []
        kg_values = {kg.get("value") for kg in kgs if isinstance(kg, dict)}
        if "sharingan" not in kg_values:
            uchiha_no_sharingan.append(c["id"])

    md_lines.extend(["",
                     "## 2. Uchiha sans Sharingan attestes",
                     "",
                     f"{len(uchiha_no_sharingan)}/{len(uchihas)} Uchiha sans Sharingan "
                     "dans `kekkei_genkai_possessed`. Sample 10 :",
                     "",
                     "| char_id | wiki mentions Sharingan | regex match exemple |",
                     "|---|---:|---|"])

    sample_uchiha = random.sample(uchiha_no_sharingan, min(10, len(uchiha_no_sharingan)))
    n_uchiha_should_have = 0
    for cid in sample_uchiha:
        char = char_index.get(cid)
        wiki = assemble_wiki(char)
        has_p, hits = has_sharingan_possession(wiki, char.get("name_romaji", ""))
        # Aussi : check "Sharingan" tout court
        sharingan_count = wiki.lower().count("sharingan")
        sample_hit = hits[0][:80] if hits else "n/a"
        if sharingan_count >= 3 or has_p:
            n_uchiha_should_have += 1
        md_lines.append(f"| `{cid}` | {sharingan_count}× | `{sample_hit}` |")

    md_lines.append(f"\n**Sur 10 Uchiha sans Sharingan** : {n_uchiha_should_have}/10 ont "
                    f">= 3 mentions Sharingan dans leur wiki (sous-extraction probable).")

    # === 3. Senju sans Mokuton ===
    senjus = [c for c in chars if c.get("clan") == "senju"]
    senju_no_mokuton = []
    for c in senjus:
        ext = load_extraction(c["id"])
        if not ext:
            continue
        kgs = (ext.get("fields", {}) or {}).get("kekkei_genkai_possessed") or []
        kg_values = {kg.get("value") for kg in kgs if isinstance(kg, dict)}
        # Llama produit "wood_release" via normalizer, OU peut-etre "mokuton" non normalize
        if not any(v in kg_values for v in ("wood_release", "mokuton", "Wood Release")):
            senju_no_mokuton.append(c["id"])

    md_lines.extend(["",
                     "## 3. Senju sans Mokuton attestes",
                     "",
                     f"{len(senju_no_mokuton)}/{len(senjus)} Senju sans Mokuton.",
                     "",
                     "| char_id | wiki mentions Mokuton/Wood | extraction kg | regex match |",
                     "|---|---:|---|---|"])

    sample_senju = random.sample(senju_no_mokuton, min(5, len(senju_no_mokuton)))
    n_senju_should_have = 0
    for cid in sample_senju:
        char = char_index.get(cid)
        wiki = assemble_wiki(char)
        has_p, hits = has_mokuton_possession(wiki, char.get("name_romaji", ""))
        wood_count = wiki.lower().count("wood release") + wiki.lower().count("mokuton")
        ext = load_extraction(cid) or {}
        kgs = (ext.get("fields", {}) or {}).get("kekkei_genkai_possessed") or []
        kg_values = [kg.get("value") for kg in kgs if isinstance(kg, dict)]
        sample_hit = hits[0][:80] if hits else "n/a"
        if wood_count >= 1:
            n_senju_should_have += 1
        md_lines.append(f"| `{cid}` | {wood_count}× | {kg_values} | `{sample_hit}` |")
    md_lines.append(f"\n**Sur 5 Senju sans Mokuton** : {n_senju_should_have}/5 ont au "
                    f"moins 1 mention Wood Release/Mokuton (sous-extraction probable).")

    # === 4. Cross-check CC dryrun vs Llama batch ===
    md_lines.extend(["",
                     "## 4. Cross-check CC dryrun vs Llama batch",
                     "",
                     "Sur les persos extraits manuellement par CC dans le dryrun "
                     "(qualite 100% grep), comparaison du nombre de fields filled.",
                     "",
                     "| char_id | CC fields | Llama fields | Delta | CC fields uniques |",
                     "|---|---:|---:|---:|---|"])

    common = [f.stem for f in DRYRUN_DIR.glob("*.json") if (PASS2_OUTPUT_DIR / f.name).exists()]
    common = sorted(common)[:5] if len(common) >= 5 else sorted(common)
    deltas = []
    for cid in common:
        cc = json.loads((DRYRUN_DIR / f"{cid}.json").read_text(encoding="utf-8"))
        llama = load_extraction(cid)
        cc_n, cc_filled = count_filled_fields(cc)
        ll_n, ll_filled = count_filled_fields(llama)
        cc_only = sorted(set(f.split("(")[0] for f in cc_filled)
                         - set(f.split("(")[0] for f in ll_filled))
        deltas.append(cc_n - ll_n)
        md_lines.append(f"| `{cid}` | {cc_n} | {ll_n} | {cc_n - ll_n:+d} | "
                        f"{','.join(cc_only[:5])} |")

    if deltas:
        avg_delta = sum(deltas) / len(deltas)
        md_lines.append(f"\n**Delta moyen CC - Llama** : {avg_delta:+.1f} fields "
                        f"({len(deltas)} persos compares).")

    # === 5. 33 not_run_through_pass2_5 ===
    md_lines.extend(["",
                     "## 5. 33 'not_run_through_pass2_5'",
                     "",
                     "Ces persos n'ont pas de birth_year_source dans extraction_metadata.",
                     "Causes possibles : char_id du JSON different du filename, ou "
                     "extraction sans champ birth_year, etc.",
                     ""])

    extractions_loaded = list(PASS2_OUTPUT_DIR.glob("*.json"))
    extractions_loaded = [f for f in extractions_loaded if not f.name.endswith(".flags.json")]
    not_run = []
    for f in extractions_loaded:
        ext = json.loads(f.read_text(encoding="utf-8"))
        meta = ext.get("extraction_metadata") or {}
        if "birth_year_source" not in meta:
            cid_in_json = ext.get("character_id")
            cid_filename = f.stem
            mismatch = cid_in_json != cid_filename
            not_run.append((cid_filename, cid_in_json, mismatch))
    md_lines.append(f"**Total not_run** : {len(not_run)}")
    md_lines.append("")
    md_lines.append("| filename_id | json_character_id | mismatch ? |")
    md_lines.append("|---|---|---|")
    for fname, jname, mismatch in not_run[:30]:
        md_lines.append(f"| `{fname}` | `{jname}` | {'YES' if mismatch else 'no'} |")

    md_lines.extend(["",
                     "## Conclusions preliminaires",
                     "",
                     "TODO : a remplir apres lecture des sections ci-dessus.",
                     ""])

    POSTMORTEM_MD.parent.mkdir(parents=True, exist_ok=True)
    POSTMORTEM_MD.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    print(f"Wrote {POSTMORTEM_MD.relative_to(ROOT)}")
    print()
    print("Quick summary:")
    print(f"  unknowns sampled : 20, with indicators : {n_with_indicators}, "
          f"with indicators AND <= 3 fields filled : {n_with_indicators_unfilled}")
    print(f"  uchiha no_sharingan : {len(uchiha_no_sharingan)}/{len(uchihas)}, "
          f"with >=3 mentions sample : {n_uchiha_should_have}/10")
    print(f"  senju no_mokuton : {len(senju_no_mokuton)}/{len(senjus)}, "
          f"with mention sample : {n_senju_should_have}/5")
    print(f"  not_run_through_pass2_5 : {len(not_run)}")
    if deltas:
        print(f"  CC vs Llama delta avg : {avg_delta:+.1f} fields")
    return 0


if __name__ == "__main__":
    sys.exit(main())
