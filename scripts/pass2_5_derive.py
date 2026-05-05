"""Pass 2.5 : derivation deterministe de birth_year manquants.

Pure logique Python, zero appel LLM. Lit les outputs Pass 2 dans
data/canonical/_pass2_output/ et tente de deriver birth_year pour les
persos sans valeur explicite, en chainant :

1. age_at_event vs arc_temporal_anchors.json :
     "Itachi was 13 in arc anbu_promotion" + arc_anchor[anbu_promotion].year_min=4
     => birth_year(Itachi) = 4 - 13 = -9 (via year_min) ou -10 (via year_max)
     On prend le median (year_min + year_max) / 2 si l'arc a un range.

2. relative_age_to vs birth_year(other_char) deja connu :
     "Sasuke is 5 years younger than Itachi" + birth_year(Itachi)=-7
     => birth_year(Sasuke) = -7 + 5 = -2

3. Iteration jusqu'a convergence (les ancres se propagent transitivement).

Tagge chaque birth_year derive avec :
- birth_year_source = "canon_hard" si deja dans Pass 2 avec confidence high explicit
- birth_year_source = "llm_extracted" si Pass 2 avec confidence high mais source_quote LLM
- birth_year_source = "derived" si calcule par ce script
- birth_year_source = "unknown" si toujours null apres derivation

Sortie :
- Met a jour les fichiers data/canonical/_pass2_output/<id>.json en place
  (champ fields.birth_year.value + ajoute birth_year_source au extraction_metadata)
- Genere data/canonical/_pass2_5_derivation_report.json avec le detail des derivations

Usage:
    python scripts/pass2_5_derive.py
    python scripts/pass2_5_derive.py --dry-run    # n'ecrit rien, affiche le rapport
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "data" / "canonical" / "_pass2_output"
ARC_ANCHORS_PATH = ROOT / "data" / "canonical" / "arc_temporal_anchors.json"
REPORT_PATH = ROOT / "data" / "canonical" / "_pass2_5_derivation_report.json"

# Mapping des labels d'arc en title-case anglais (Llama-style) vers les
# slugs canonisés de arc_temporal_anchors.json. None = non exploitable
# (arc trop generique pour donner un year exact).
ARC_ALIASES: dict[str, str | None] = {
    # Pre-Konoha
    "warring states period": "warring_states_period",
    "warring states era": "warring_states_period",
    "war-torn era before the creation of the shinobi villages": "warring_states_period",
    "konoha founding": "konoha_founding",
    "konoha foundation": "konoha_founding",
    # Wars
    "first shinobi world war": "first_shinobi_world_war",
    "second shinobi world war": "second_shinobi_world_war",
    "third shinobi world war": "third_shinobi_world_war",
    "fourth shinobi world war": "shinobi_world_war_4",
    "shinobi world war": "shinobi_world_war_4",  # default to most narratively used
    "fourth great shinobi war": "shinobi_world_war_4",
    "fourth shinobi war": "shinobi_world_war_4",
    "great shinobi war": "shinobi_world_war_4",
    # Pre-series Naruto
    "kyuubi attack": "kyuubi_attack",
    "nine tails attack": "kyuubi_attack",
    "nine-tails attack": "kyuubi_attack",
    "nine-tails' attack": "kyuubi_attack",
    "nine-tails attack on konoha": "kyuubi_attack",
    "kyuubi attack on konoha": "kyuubi_attack",
    "uchiha massacre": "uchiha_massacre",
    "uchiha clan downfall": "uchiha_massacre",
    "uchiha clan massacre": "uchiha_massacre",
    "anbu promotion": "anbu_promotion",
    # Academy / Part I
    "academy": "academy",
    "academy entry": "academy",
    "academy entrance": "academy",
    "academy entrance ceremony": "academy",
    "academy entrance arc": "academy",
    "graduation": "academy",
    "academy graduation": "academy",
    "graduation from the academy": "academy",
    # Land of Waves / Wave
    "land of waves": "land_of_waves",
    "wave arc": "land_of_waves",
    "land of waves arc": "land_of_waves",
    "prologue": "land_of_waves",
    "prologue land of waves": "land_of_waves",
    # Chunin
    "chunin exam": "chunin_exam",
    "chunin exams": "chunin_exam",
    "chuunin exam": "chunin_exam",
    "chuunin exams": "chunin_exam",
    "konoha chunin exam": "chunin_exam",
    # Konoha Crush
    "konoha crush": "konoha_crush",
    "konoha invasion": "konoha_crush",
    "invasion of konoha": "konoha_crush",
    # Search for Tsunade
    "search for tsunade": "search_for_tsunade",
    "search for the fifth": "search_for_tsunade",
    # Sasuke Defection
    "sasuke retrieval": "sasuke_defection",
    "sasuke recovery mission": "sasuke_defection",
    "sasuke defection": "sasuke_defection",
    # Part II (Shippuden)
    "kazekage rescue": "kazekage_rescue",
    "kazekage rescue mission": "kazekage_rescue",
    "rescue of the kazekage": "kazekage_rescue",
    "tenchi bridge": "tenchi_bridge",
    "tenchi bridge reconnaissance mission": "tenchi_bridge",
    "akatsuki suppression": "akatsuki_suppression",
    "hidan and kakuzu": "hidan_kakuzu",
    "hidan-kakuzu": "hidan_kakuzu",
    "hidan kakuzu": "hidan_kakuzu",
    "itachi pursuit": "itachi_pursuit",
    "itachi pursuit mission": "itachi_pursuit",
    "pain invasion": "pain_invasion",
    "pain's assault": "pain_invasion",
    "pain assault": "pain_invasion",
    "pains assault": "pain_invasion",
    "five kage summit": "five_kage_summit",
    "kage summit": "five_kage_summit",
    "post war": "post_war",
    # Blank Period
    "blank period": "blank_period",
    "the last": "the_last",
    "the last: naruto the movie": "the_last",
    "the last naruto the movie": "the_last",
    # Hokage inauguration
    "hokage inauguration": "naruto_hokage_inauguration",
    "naruto hokage inauguration": "naruto_hokage_inauguration",
    # Boruto era
    "boruto academy": "boruto_academy",
    "academy entrance arc (boruto)": "boruto_academy",
    "boruto chunin exam": "boruto_chunin_exam",
    "chunin exam boruto": "boruto_chunin_exam",
    "boruto's return arc": "boruto_timeskip",
    "borutos return arc": "boruto_timeskip",
    "boruto return arc": "boruto_timeskip",
    "boruto timeskip": "boruto_timeskip",
    "kawaki arrival": "kawaki_arrival",
    "kawaki arrival arc": "kawaki_arrival",
    "new era": "new_era_part_2",
    "new era part 2": "new_era_part_2",
    "boruto two blue vortex": "new_era_part_2",
    # Generic / not exploitable -> None
    "fifth birthday": None,
    "promotion to jonin": None,
    "promotion to chunin": None,
    "konoha plans recapture mission": None,
    "time slip arc": None,
    "graduation from shuku academy": None,
    "shuku academy": None,
}


def load_arc_anchors() -> dict[str, dict[str, int]]:
    raw = json.loads(ARC_ANCHORS_PATH.read_text(encoding="utf-8"))
    return raw.get("arcs", {})


def _strip_diacritics(text: str) -> str:
    import unicodedata as _ud
    n = _ud.normalize("NFKD", text)
    return "".join(c for c in n if not _ud.combining(c))


def _normalize_arc_label(arc_id: str) -> str:
    """ASCII lowercase, collapse whitespace, strip trailing punctuation."""
    n = _strip_diacritics(arc_id).lower().strip()
    n = " ".join(n.split())
    return n.rstrip(":.,;").strip()


def arc_year_estimate(arc_id: str, anchors: dict[str, dict]) -> int | None:
    """Returns the median year for an arc, or None if not exploitable.

    Tries direct match, snake_case variant, ASCII lowercase, and the
    ARC_ALIASES mapping for human-form titles.
    """
    if not arc_id:
        return None
    candidates: list[str] = [
        arc_id,
        arc_id.lower(),
        arc_id.lower().replace(" ", "_").replace("-", "_"),
        _normalize_arc_label(arc_id),
        _normalize_arc_label(arc_id).replace(" ", "_"),
    ]
    for c in candidates:
        if c in anchors:
            a = anchors[c]
            ymin = a.get("year_min")
            ymax = a.get("year_max")
            if ymin is not None and ymax is not None:
                return (ymin + ymax) // 2
            if ymin is not None:
                return ymin
            if ymax is not None:
                return ymax

    # Try alias mapping for human-form titles ("Chunin Exams", "Pain's Assault")
    norm = _normalize_arc_label(arc_id)
    if norm in ARC_ALIASES:
        target = ARC_ALIASES[norm]
        if target is None:
            return None  # arc explicitly marked as not exploitable
        if target in anchors:
            a = anchors[target]
            ymin = a.get("year_min")
            ymax = a.get("year_max")
            if ymin is not None and ymax is not None:
                return (ymin + ymax) // 2
            if ymin is not None:
                return ymin
            if ymax is not None:
                return ymax
    return None


@dataclass
class DerivedBirthYear:
    char_id: str
    value: int
    method: str  # "from_age_at_event" | "from_relative_age_to"
    detail: str
    confidence: str  # "medium" or "low"


@dataclass
class DerivationReport:
    derived: list[DerivedBirthYear] = field(default_factory=list)
    canon_hard: list[str] = field(default_factory=list)
    llm_extracted: list[str] = field(default_factory=list)
    still_unknown: list[str] = field(default_factory=list)


def get_existing_birth_year(extraction: dict) -> tuple[int | None, str | None]:
    """Returns (value, confidence) from fields.birth_year.value."""
    by = (extraction.get("fields") or {}).get("birth_year") or {}
    return by.get("value"), by.get("confidence")


def try_derive_from_age_events(
    extraction: dict, anchors: dict[str, dict],
) -> tuple[int, str] | None:
    """Returns (birth_year, detail_message) if derivable, None otherwise."""
    fields = extraction.get("fields") or {}
    events = fields.get("age_at_event") or []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        arc_id = ev.get("arc")
        age = ev.get("age")
        if not isinstance(age, int) or not arc_id:
            continue
        arc_year = arc_year_estimate(arc_id, anchors)
        if arc_year is None:
            continue
        return (arc_year - age,
                f"arc='{arc_id}' year~{arc_year}, age={age} => birth={arc_year - age}")
    return None


def try_derive_from_relative_ages(
    extraction: dict, known_birth_years: dict[str, int],
) -> tuple[int, str] | None:
    fields = extraction.get("fields") or {}
    rels = fields.get("relative_age_to") or []
    for rel in rels:
        if not isinstance(rel, dict):
            continue
        other = rel.get("other_char")
        delta = rel.get("delta_years")
        if not isinstance(delta, int) or not other:
            continue
        other_by = known_birth_years.get(other)
        if other_by is None:
            continue
        # delta > 0 means THIS char is older than other.
        # birth(this) = birth(other) - delta
        return (other_by - delta,
                f"relative_to='{other}' (birth={other_by}) delta={delta} => birth={other_by - delta}")
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Do not write files, only print report")
    parser.add_argument("--max-iterations", type=int, default=10,
                        help="Max passes for transitive derivation (default 10)")
    args = parser.parse_args()

    if not ARC_ANCHORS_PATH.exists():
        print(f"ERROR: arc anchors file not found: {ARC_ANCHORS_PATH}", file=sys.stderr)
        return 1

    anchors = load_arc_anchors()
    print(f"Loaded {len(anchors)} arc anchors from {ARC_ANCHORS_PATH.relative_to(ROOT)}")

    # Load all extractions
    files = sorted(p for p in OUTPUT_DIR.glob("*.json")
                   if not p.name.endswith(".flags.json")
                   and not p.name.startswith("_"))
    print(f"Loaded {len(files)} extractions from {OUTPUT_DIR.relative_to(ROOT)}")

    extractions: dict[str, dict] = {}
    mismatches_fixed = 0
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            json_cid = data.get("character_id")
            file_cid = f.stem
            # Filename is source of truth (LLM sometimes writes a swapped
            # clan_first id different from the canonical filename id).
            if json_cid != file_cid:
                data["character_id"] = file_cid
                mismatches_fixed += 1
            extractions[file_cid] = data
        except json.JSONDecodeError as exc:
            print(f"  WARN: bad JSON in {f.name}: {exc}")
    if mismatches_fixed:
        print(f"  Fixed {mismatches_fixed} character_id mismatches "
              f"(LLM-swapped vs filename).")

    # Initial known birth_years (from canon_hard or llm_extracted in Pass 2)
    report = DerivationReport()
    known_birth_years: dict[str, int] = {}
    for cid, ext in extractions.items():
        val, conf = get_existing_birth_year(ext)
        if val is not None:
            known_birth_years[cid] = val
            tag = "canon_hard" if conf == "high" else "llm_extracted"
            (report.canon_hard if tag == "canon_hard" else report.llm_extracted).append(cid)

    print("\nInitial state:")
    print(f"  birth_year already set : {len(known_birth_years)}")
    print(f"    canon_hard (high)    : {len(report.canon_hard)}")
    print(f"    llm_extracted (med)  : {len(report.llm_extracted)}")
    print(f"  to derive              : {len(extractions) - len(known_birth_years)}")
    print()

    # Iterative derivation
    for iteration in range(1, args.max_iterations + 1):
        new_derivations = 0
        for cid, ext in extractions.items():
            if cid in known_birth_years:
                continue
            # Try age_at_event first (more reliable)
            r = try_derive_from_age_events(ext, anchors)
            method = "from_age_at_event"
            if r is None:
                r = try_derive_from_relative_ages(ext, known_birth_years)
                method = "from_relative_age_to"
            if r is None:
                continue
            value, detail = r
            known_birth_years[cid] = value
            report.derived.append(DerivedBirthYear(
                char_id=cid, value=value, method=method,
                detail=detail, confidence="medium",
            ))
            new_derivations += 1
        print(f"  Iteration {iteration}: +{new_derivations} new derivations "
              f"(total derived: {len(report.derived)})")
        if new_derivations == 0:
            break

    # Final unknowns
    for cid in extractions:
        if cid not in known_birth_years:
            report.still_unknown.append(cid)

    print()
    print("=" * 60)
    print("Final state:")
    print(f"  Total persos          : {len(extractions)}")
    print(f"  canon_hard (Pass 2)   : {len(report.canon_hard)}")
    print(f"  llm_extracted (Pass 2): {len(report.llm_extracted)}")
    print(f"  derived (Pass 2.5)    : {len(report.derived)}")
    print(f"  still unknown         : {len(report.still_unknown)}")
    coverage = (len(known_birth_years) / len(extractions) * 100) if extractions else 0
    print(f"  birth_year coverage   : {coverage:.1f}%")

    # Apply derivations to extraction files (mark birth_year_source)
    if not args.dry_run:
        applied = 0
        # tag canon_hard
        for cid in report.canon_hard:
            ext = extractions[cid]
            ext.setdefault("extraction_metadata", {})["birth_year_source"] = "canon_hard"
        for cid in report.llm_extracted:
            ext = extractions[cid]
            ext.setdefault("extraction_metadata", {})["birth_year_source"] = "llm_extracted"
        for cid in report.still_unknown:
            ext = extractions[cid]
            ext.setdefault("extraction_metadata", {})["birth_year_source"] = "unknown"
        # apply derived
        for d in report.derived:
            ext = extractions[d.char_id]
            ext.setdefault("extraction_metadata", {})["birth_year_source"] = "derived"
            fields = ext.setdefault("fields", {})
            fields["birth_year"] = {
                "value": d.value,
                "source_quote": None,
                "confidence": d.confidence,
                "derivation_method": d.method,
                "_pass2_5_detail": d.detail,
            }
            applied += 1

        for cid, ext in extractions.items():
            (OUTPUT_DIR / f"{cid}.json").write_text(
                json.dumps(ext, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        print(f"\nApplied {applied} derivations to extraction files.")

    # Write report
    REPORT_PATH.write_text(json.dumps({
        "summary": {
            "total": len(extractions),
            "canon_hard": len(report.canon_hard),
            "llm_extracted": len(report.llm_extracted),
            "derived": len(report.derived),
            "still_unknown": len(report.still_unknown),
            "coverage_pct": round(coverage, 1),
        },
        "derived": [
            {"char_id": d.char_id, "value": d.value, "method": d.method,
             "detail": d.detail, "confidence": d.confidence}
            for d in report.derived
        ],
        "still_unknown": report.still_unknown,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Report : {REPORT_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
