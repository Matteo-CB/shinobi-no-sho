"""Pass 3 : agregation et reconstruction du canon.

Pure logique Python, zero appel LLM. Lit les outputs Pass 2 (+ Pass 2.5)
dans data/canonical/_pass2_output/ et :

a) Agrege par clan les attributs reellement attestes par ses membres
   (kekkei_genkai, natures, key_techniques) avec stats X/N attestations.

b) Reconstruit clans.json et kekkei_genkai.json depuis ces stats avec la
   regle : un attribut Y est associe a un clan X si :
     >= MIN_RATIO_ATTESTATION (default 50%) des membres extraits de X
     ATTESTENT explicitement Y, ET >= MIN_MEMBERS_ATTESTATION (default 3)
     membres l'ont atteste.

c) Genere research/scraper-corruption-report.md : pour chaque couple
   (clan, attribut) present dans l'ANCIEN characters.json mais NON
   atteste par Pass 2, le liste comme corruption probable du scraper.

d) Genere research/canon-completion-report.md : couverture, distribution
   par birth_year_source, top-20 confidence-low a verifier, etc.

e) Renomme les anciens clans.json et kekkei_genkai.json en *.pre_pass2_backup.

Usage:
    python scripts/pass2_aggregate.py            # dry-run par defaut
    python scripts/pass2_aggregate.py --apply    # ecrit les fichiers reels
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PASS2_OUTPUT_DIR = ROOT / "data" / "canonical" / "_pass2_output"
CHARACTERS_PATH = ROOT / "data" / "canonical" / "characters.json"
CLANS_PATH = ROOT / "data" / "canonical" / "clans.json"
KG_PATH = ROOT / "data" / "canonical" / "kekkei_genkai.json"
SCRAPER_REPORT_PATH = ROOT / "research" / "scraper-corruption-report.md"
COMPLETION_REPORT_PATH = ROOT / "research" / "canon-completion-report.md"

MIN_RATIO_KEY = 0.50          # signature obligatoire du clan (Byakugan-Hyuga)
MIN_MEMBERS_KEY = 3
MIN_RATIO_AVAILABLE = 0.30    # eligibilite (Sharingan-Uchiha)
MIN_MEMBERS_AVAILABLE = 3
MIN_MEMBERS_INDIVIDUAL = 1    # mutation isolee (Mokuton-Hashirama)
MAX_MEMBERS_INDIVIDUAL = 2    # 1-2 membres = individuelle, pas clan-wide


def load_pass2_outputs() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for f in PASS2_OUTPUT_DIR.glob("*.json"):
        if f.name.endswith(".flags.json") or f.name.startswith("_"):
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            out[data.get("character_id", f.stem)] = data
        except json.JSONDecodeError:
            continue
    return out


import re as _re
import unicodedata as _ud
def _normalize_technique_slug(value: str) -> str:
    """Normalize 'Shadow Imitation Technique' -> 'shadow_imitation_technique'.

    NFKD-decompose first so 'kikaichu' (ū) -> 'kikaichu', not 'kikaich'.
    """
    if not value:
        return ""
    n = _ud.normalize("NFKD", value)
    n = "".join(c for c in n if not _ud.combining(c))
    n = n.lower().strip()
    n = _re.sub(r"[^a-z0-9]+", "_", n)
    n = n.strip("_")
    return n


def collect_attested_per_clan(
    extractions: dict[str, dict], characters: list[dict],
) -> tuple[dict, dict, dict, dict]:
    """For each clan, count members and attested attributes.

    Returns (clan_members, clan_kg_count, clan_nature_count, clan_tech_count).
    """
    char_index = {c["id"]: c for c in characters}
    clan_members: dict[str, set[str]] = defaultdict(set)
    clan_kg_count: dict[str, Counter] = defaultdict(Counter)
    clan_nature_count: dict[str, Counter] = defaultdict(Counter)
    clan_tech_count: dict[str, Counter] = defaultdict(Counter)

    for cid, char in char_index.items():
        clan = char.get("clan")
        if clan:
            clan_members[clan].add(cid)

    for cid, ext in extractions.items():
        char = char_index.get(cid)
        if char is None:
            continue
        clan = char.get("clan")
        if not clan:
            continue
        fields = ext.get("fields") or {}
        for kg_entry in fields.get("kekkei_genkai_possessed") or []:
            if isinstance(kg_entry, dict):
                v = kg_entry.get("value")
                if v:
                    clan_kg_count[clan][v] += 1
        for nat_entry in fields.get("natures_possessed") or []:
            if isinstance(nat_entry, dict):
                v = nat_entry.get("value")
                if v:
                    clan_nature_count[clan][v] += 1
        for tech_entry in fields.get("key_techniques") or []:
            if isinstance(tech_entry, dict):
                v = tech_entry.get("value")
                if v:
                    slug = _normalize_technique_slug(v)
                    if slug:
                        clan_tech_count[clan][slug] += 1

    return clan_members, clan_kg_count, clan_nature_count, clan_tech_count


def derive_canon_attributes_3tier(
    clan_members: dict[str, set[str]],
    clan_kg_count: dict[str, Counter],
    clan_nature_count: dict[str, Counter],
    clan_tech_count: dict[str, Counter],
    extractions: dict[str, dict],
    characters: list[dict],
) -> dict:
    """3-tier classification:

    - key_*       : >= MIN_RATIO_KEY + >= MIN_MEMBERS_KEY (signature clan)
    - available_* : >= MIN_RATIO_AVAILABLE + >= MIN_MEMBERS_AVAILABLE (eligibility)
                    minus the key_* attributes (set difference)
    - individual_mutation : 1-2 members only attest the attribute. Tagged
                            per-character, not per-clan.

    Returns dict with keys :
      clan_key_kgs, clan_available_kgs, clan_key_natures, clan_available_natures,
      clan_key_techniques, clan_available_techniques,
      individual_mutations (list of {char_id, clan_id, attribute_type, attribute})
    """
    clan_key_kgs: dict[str, list[str]] = {}
    clan_available_kgs: dict[str, list[str]] = {}
    clan_key_natures: dict[str, list[str]] = {}
    clan_available_natures: dict[str, list[str]] = {}
    clan_key_techniques: dict[str, list[str]] = {}
    clan_available_techniques: dict[str, list[str]] = {}
    individual_mutations: list[dict] = []

    char_index = {c["id"]: c for c in characters}

    for clan_id, members in clan_members.items():
        n_members = len(members)
        if n_members == 0:
            continue
        # KG classification
        key_kgs, avail_kgs = [], []
        for kg_id, count in clan_kg_count.get(clan_id, {}).items():
            ratio = count / n_members
            if count >= MIN_MEMBERS_KEY and ratio >= MIN_RATIO_KEY:
                key_kgs.append(kg_id)
            elif count >= MIN_MEMBERS_AVAILABLE and ratio >= MIN_RATIO_AVAILABLE:
                avail_kgs.append(kg_id)
            elif MIN_MEMBERS_INDIVIDUAL <= count <= MAX_MEMBERS_INDIVIDUAL:
                # Tag each member individually
                for cid in members:
                    ext = extractions.get(cid)
                    if ext is None:
                        continue
                    fields = ext.get("fields") or {}
                    kgs = fields.get("kekkei_genkai_possessed") or []
                    if any(isinstance(k, dict) and k.get("value") == kg_id for k in kgs):
                        individual_mutations.append({
                            "char_id": cid,
                            "clan_id": clan_id,
                            "type": "kekkei_genkai",
                            "attribute": kg_id,
                            "n_clan_members_attesting": count,
                            "clan_size": n_members,
                        })

        # Nature classification (same logic)
        key_nats, avail_nats = [], []
        for nat_id, count in clan_nature_count.get(clan_id, {}).items():
            ratio = count / n_members
            if count >= MIN_MEMBERS_KEY and ratio >= MIN_RATIO_KEY:
                key_nats.append(nat_id)
            elif count >= MIN_MEMBERS_AVAILABLE and ratio >= MIN_RATIO_AVAILABLE:
                avail_nats.append(nat_id)
            elif MIN_MEMBERS_INDIVIDUAL <= count <= MAX_MEMBERS_INDIVIDUAL:
                for cid in members:
                    ext = extractions.get(cid)
                    if ext is None:
                        continue
                    fields = ext.get("fields") or {}
                    nats = fields.get("natures_possessed") or []
                    if any(isinstance(n, dict) and n.get("value") == nat_id for n in nats):
                        individual_mutations.append({
                            "char_id": cid,
                            "clan_id": clan_id,
                            "type": "nature",
                            "attribute": nat_id,
                            "n_clan_members_attesting": count,
                            "clan_size": n_members,
                        })

        # Technique classification (same logic, no individual_mutation tagging
        # for technique tier 3 : a 1-2 member technique is just a personal jutsu,
        # noise at clan level)
        key_techs, avail_techs = [], []
        for tech_id, count in clan_tech_count.get(clan_id, {}).items():
            ratio = count / n_members
            if count >= MIN_MEMBERS_KEY and ratio >= MIN_RATIO_KEY:
                key_techs.append(tech_id)
            elif count >= MIN_MEMBERS_AVAILABLE and ratio >= MIN_RATIO_AVAILABLE:
                avail_techs.append(tech_id)

        if key_kgs:
            clan_key_kgs[clan_id] = sorted(key_kgs)
        if avail_kgs:
            clan_available_kgs[clan_id] = sorted(avail_kgs)
        if key_nats:
            clan_key_natures[clan_id] = sorted(key_nats)
        if avail_nats:
            clan_available_natures[clan_id] = sorted(avail_nats)
        if key_techs:
            clan_key_techniques[clan_id] = sorted(key_techs)
        if avail_techs:
            clan_available_techniques[clan_id] = sorted(avail_techs)

    return {
        "clan_key_kgs": clan_key_kgs,
        "clan_available_kgs": clan_available_kgs,
        "clan_key_natures": clan_key_natures,
        "clan_available_natures": clan_available_natures,
        "clan_key_techniques": clan_key_techniques,
        "clan_available_techniques": clan_available_techniques,
        "individual_mutations": individual_mutations,
    }


def detect_corruption(
    clans_old: list[dict],
    clan_to_kgs: dict[str, list[str]],
    clan_to_natures: dict[str, list[str]],
) -> list[dict]:
    """Detect attributes claimed in old clans.json that are NOT attested by Pass 2."""
    flags = []
    for clan in clans_old:
        cid = clan["id"]
        old_kgs = set(clan.get("key_kekkei_genkai") or [])
        old_natures = set(clan.get("key_natures") or [])
        attested_kgs = set(clan_to_kgs.get(cid, []))
        attested_natures = set(clan_to_natures.get(cid, []))

        for kg in old_kgs - attested_kgs:
            flags.append({
                "clan_id": cid,
                "type": "kekkei_genkai",
                "attribute": kg,
                "reason": "not attested by Pass 2 extractions",
            })
        for nat in old_natures - attested_natures:
            flags.append({
                "clan_id": cid,
                "type": "nature",
                "attribute": nat,
                "reason": "not attested by Pass 2 extractions",
            })
    return flags


def build_completion_report(extractions: dict[str, dict]) -> dict:
    """Compute coverage stats per birth_year_source and confidence."""
    sources = Counter()
    confidences = Counter()
    low_confidence_chars: list[tuple[str, str]] = []  # (cid, reason)
    for cid, ext in extractions.items():
        meta = ext.get("extraction_metadata") or {}
        src = meta.get("birth_year_source", "not_run_through_pass2_5")
        sources[src] += 1
        fields = ext.get("fields") or {}
        by = fields.get("birth_year") or {}
        conf = by.get("confidence")
        if conf:
            confidences[conf] += 1
        if conf == "low":
            low_confidence_chars.append((cid, str(by.get("source_quote"))[:80]))

    return {
        "n_total": len(extractions),
        "by_source": dict(sources),
        "by_confidence": dict(confidences),
        "low_confidence_top20": low_confidence_chars[:20],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Persist files (clans.json, kekkei_genkai.json, reports). "
                             "Without this, dry-run + reports only.")
    args = parser.parse_args()

    print("Loading Pass 2 extractions...")
    extractions = load_pass2_outputs()
    print(f"  {len(extractions)} extractions loaded.")

    print("Loading characters.json + clans.json + kekkei_genkai.json...")
    characters = json.loads(CHARACTERS_PATH.read_text(encoding="utf-8"))
    clans_old = json.loads(CLANS_PATH.read_text(encoding="utf-8"))
    kg_old = json.loads(KG_PATH.read_text(encoding="utf-8"))

    # === a) Aggregate per clan
    clan_members, clan_kg_count, clan_nature_count, clan_tech_count = (
        collect_attested_per_clan(extractions, characters)
    )
    print("\n=== Aggregation ===")
    print(f"  Clans with members : {len(clan_members)}")
    top10 = sorted(clan_members.items(), key=lambda kv: -len(kv[1]))[:10]
    for cid, members in top10:
        kgs_obs = clan_kg_count.get(cid, Counter())
        techs_obs = clan_tech_count.get(cid, Counter())
        print(f"  {cid:20s} N={len(members):>3} "
              f"kg_obs={dict(kgs_obs.most_common(3))} "
              f"tech_obs={dict(techs_obs.most_common(3))}")

    # === b) Derive canon attributes with 3-tier classification
    derivation = derive_canon_attributes_3tier(
        clan_members, clan_kg_count, clan_nature_count, clan_tech_count,
        extractions, characters,
    )
    clan_key_kgs = derivation["clan_key_kgs"]
    clan_available_kgs = derivation["clan_available_kgs"]
    clan_key_natures = derivation["clan_key_natures"]
    clan_available_natures = derivation["clan_available_natures"]
    clan_key_techniques = derivation["clan_key_techniques"]
    clan_available_techniques = derivation["clan_available_techniques"]
    individual_mutations = derivation["individual_mutations"]

    # For backward-compat with detect_corruption
    clan_to_kgs = {cid: list(set(clan_key_kgs.get(cid, []) + clan_available_kgs.get(cid, [])))
                   for cid in set(list(clan_key_kgs.keys()) + list(clan_available_kgs.keys()))}
    clan_to_natures = {cid: list(set(clan_key_natures.get(cid, []) + clan_available_natures.get(cid, [])))
                       for cid in set(list(clan_key_natures.keys()) + list(clan_available_natures.keys()))}

    print(f"\n=== Canon attributes derivation (3-tier) ===")
    print(f"  Tier KEY      ({MIN_RATIO_KEY:.0%}+ AND >= {MIN_MEMBERS_KEY} members) : "
          f"KGs={len(clan_key_kgs)} clans, natures={len(clan_key_natures)} clans, "
          f"techs={len(clan_key_techniques)} clans")
    print(f"  Tier AVAILABLE ({MIN_RATIO_AVAILABLE:.0%}+ AND >= {MIN_MEMBERS_AVAILABLE} members, excl key) : "
          f"KGs={len(clan_available_kgs)} clans, natures={len(clan_available_natures)} clans, "
          f"techs={len(clan_available_techniques)} clans")
    print(f"  Tier INDIVIDUAL_MUTATION (1-2 members only) : "
          f"{len(individual_mutations)} per-char tags")
    print()
    print("=== Big clans review ===")
    big_clans = ["uchiha", "hyuga", "senju", "sarutobi", "nara", "akimichi",
                 "yamanaka", "aburame", "inuzuka", "hozuki", "kaguya", "yuki",
                 "uzumaki", "otsutsuki"]
    for cid in big_clans:
        n = len(clan_members.get(cid, set()))
        key_kg = clan_key_kgs.get(cid, [])
        avail_kg = clan_available_kgs.get(cid, [])
        key_nat = clan_key_natures.get(cid, [])
        avail_nat = clan_available_natures.get(cid, [])
        key_tech = clan_key_techniques.get(cid, [])
        avail_tech = clan_available_techniques.get(cid, [])
        if n == 0:
            continue
        print(f"  {cid:12s} (N={n:>2}) "
              f"key_kg={key_kg} avail_kg={avail_kg} "
              f"key_nat={key_nat} avail_nat={avail_nat}")
        if key_tech or avail_tech:
            print(f"  {' ':12s}        "
                  f"key_tech={key_tech} avail_tech={avail_tech}")

    # === c) Corruption flags
    corruption_flags = detect_corruption(clans_old, clan_to_kgs, clan_to_natures)
    print(f"\n=== Scraper corruption flags : {len(corruption_flags)} ===")
    for flag in corruption_flags[:5]:
        print(f"  {flag}")

    # === d) Completion stats
    completion = build_completion_report(extractions)
    print("\n=== Completion report ===")
    print(f"  Total : {completion['n_total']}")
    print(f"  By source     : {completion['by_source']}")
    print(f"  By confidence : {completion['by_confidence']}")

    if not args.apply:
        print("\n>>> DRY-RUN. Re-run with --apply to persist files.")
        return 0

    # === e) Write outputs
    # Backup old files. If a .pre_pass2_backup already exists (previous
    # --apply attempt before rollback), keep it as the authoritative
    # original and just remove the current generated file.
    for path in (CLANS_PATH, KG_PATH):
        backup = path.with_suffix(".json.pre_pass2_backup")
        if backup.exists():
            path.unlink()
            print(f"  Backup {backup.name} already exists, kept as original. "
                  f"Removed current {path.name}.")
        else:
            path.rename(backup)
            print(f"  Backup created : {backup.name}.")

    # Reconstruct clans.json with 3-tier classification
    new_clans = []
    for clan in clans_old:
        cid = clan["id"]
        new_clan = dict(clan)
        new_clan["key_kekkei_genkai"] = clan_key_kgs.get(cid, [])
        new_clan["available_kekkei_genkai"] = clan_available_kgs.get(cid, [])
        new_clan["key_natures"] = clan_key_natures.get(cid, [])
        new_clan["available_natures"] = clan_available_natures.get(cid, [])
        new_clan["key_techniques"] = clan_key_techniques.get(cid, [])
        new_clan["available_techniques"] = clan_available_techniques.get(cid, [])
        new_clans.append(new_clan)
    CLANS_PATH.write_text(
        json.dumps(new_clans, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # Reconstruct kekkei_genkai.json
    # carrier_clans = inverse of clan_to_kgs
    kg_to_clans: dict[str, list[str]] = defaultdict(list)
    for clan_id, kgs in clan_to_kgs.items():
        for kg_id in kgs:
            kg_to_clans[kg_id].append(clan_id)
    new_kg = []
    for kg in kg_old:
        new_kg_entry = dict(kg)
        new_kg_entry["carrier_clans"] = sorted(kg_to_clans.get(kg["id"], []))
        new_kg.append(new_kg_entry)
    KG_PATH.write_text(
        json.dumps(new_kg, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote new {CLANS_PATH.name} and {KG_PATH.name}")

    # Write scraper-corruption-report.md
    SCRAPER_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Scraper corruption report",
             "",
             f"Generated by `scripts/pass2_aggregate.py`. {len(corruption_flags)} flags.",
             "",
             ("These are clan-attribute couples present in the ORIGINAL "
              "`characters.json`/`clans.json` but NOT attested by any Pass 2 "
              "extraction. Most likely they are scraper artifacts (Narutopedia "
              "infobox parsing errors)."),
             "",
             "| Clan | Type | Attribute | Reason |",
             "|---|---|---|---|"]
    for f in corruption_flags:
        lines.append(f"| `{f['clan_id']}` | {f['type']} | `{f['attribute']}` | {f['reason']} |")
    SCRAPER_REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {SCRAPER_REPORT_PATH.relative_to(ROOT)}")

    # Write canon-completion-report.md
    lines = ["# Canon completion report (Pass 2 + Pass 2.5)",
             "",
             f"Generated by `scripts/pass2_aggregate.py`. "
             f"{completion['n_total']} characters processed.",
             "",
             "## Coverage by birth_year_source",
             "",
             "| Source | Count |",
             "|---|---:|"]
    for src, n in sorted(completion["by_source"].items(), key=lambda kv: -kv[1]):
        lines.append(f"| `{src}` | {n} |")
    lines.append("")
    lines.append("## Coverage by confidence")
    lines.append("")
    lines.append("| Confidence | Count |")
    lines.append("|---|---:|")
    for conf, n in sorted(completion["by_confidence"].items(), key=lambda kv: -kv[1]):
        lines.append(f"| `{conf}` | {n} |")
    lines.append("")
    lines.append("## Top 20 low-confidence characters (manual review recommended)")
    lines.append("")
    lines.append("| char_id | source_quote (truncated) |")
    lines.append("|---|---|")
    for cid, q in completion["low_confidence_top20"]:
        lines.append(f"| `{cid}` | `{q}` |")
    COMPLETION_REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {COMPLETION_REPORT_PATH.relative_to(ROOT)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
