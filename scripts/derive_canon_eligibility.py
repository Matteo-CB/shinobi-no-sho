"""Pass 1 du plan de completion canon (cf. research/canon-completion-plan.md).

Derivation deterministe des champs eligibility/carrier_clans a partir des
couples (clan, kekkei_genkai) et (clan, natures) deja connus dans
characters.json. Ne touche AUCUN birth_year ni death_year. Pas d'appel LLM.

Robustesse :
- Whitelist : ne traite que les clan_ids presents dans clans.json. Tout
  clan_id mentionne par un perso mais absent de clans.json est un artefact
  de scraping (file_..., wikipedia_..., es_patriarca_..., etc.).
- Threshold : un KG ou une nature n'est propage comme "key" du clan que
  si au moins MIN_MEMBERS_FOR_PROPAGATION membres du clan l'ont, ET
  au moins MIN_RATIO_FOR_PROPAGATION du clan. Ca evite de propager le
  Sharingan du clan Hatake sous pretexte que Kakashi (1 membre sur ~5)
  l'a greffe d'Obito.

Sorties :
- modifie data/canonical/clans.json in-place : remplit les key_kekkei_genkai
  et key_natures vides avec les KG/natures qui passent le threshold
- modifie data/canonical/kekkei_genkai.json in-place : remplit les
  carrier_clans vides avec l'inversion (filtree par le threshold)
- ecrit data/canonical/character_eligibility_patch.json : pour chaque perso
  ayant un clan canonical mais pas de KG/natures, ajoute eligible_kekkei_genkai
  et eligible_natures (les ensembles qui passent le threshold pour son clan)
- ecrit data/canonical/_orphan_patches.md : liste des entrees
  character_birth_years_patch.json sans match dans characters.json

Mode dry-run par defaut. Utiliser --apply pour persister les changements.

Usage :
    python scripts/derive_canon_eligibility.py            # dry-run
    python scripts/derive_canon_eligibility.py --apply    # ecrit les fichiers
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANONICAL_DIR = ROOT / "data" / "canonical"

CHARACTERS_PATH = CANONICAL_DIR / "characters.json"
CLANS_PATH = CANONICAL_DIR / "clans.json"
KG_PATH = CANONICAL_DIR / "kekkei_genkai.json"
ELIGIBILITY_PATCH_PATH = CANONICAL_DIR / "character_eligibility_patch.json"
ORPHAN_PATCHES_REPORT_PATH = CANONICAL_DIR / "_orphan_patches.md"
BIRTH_YEAR_PATCH_PATH = CANONICAL_DIR / "character_birth_years_patch.json"

# Threshold pour la propagation d'un KG/nature comme "key" du clan.
# Un trait n'est attribue au clan dans son ensemble que si :
#   - >= MIN_MEMBERS_FOR_PROPAGATION membres du clan le possedent, ET
#   - >= MIN_RATIO_FOR_PROPAGATION du clan le possede
# Ca evite de propager le Sharingan du clan Hatake parce que Kakashi (1/5)
# l'a recu d'Obito Uchiha.
MIN_MEMBERS_FOR_PROPAGATION = 3
MIN_RATIO_FOR_PROPAGATION = 0.30


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persiste les modifications. Sans ce flag, dry-run (rien n'est ecrit).",
    )
    args = parser.parse_args()

    chars = json.loads(CHARACTERS_PATH.read_text(encoding="utf-8"))
    clans = json.loads(CLANS_PATH.read_text(encoding="utf-8"))
    kg_entries = json.loads(KG_PATH.read_text(encoding="utf-8"))
    char_ids = {c["id"] for c in chars}
    canonical_clan_ids = {c["id"] for c in clans}

    print(f"Loaded {len(chars)} characters, {len(clans)} clans, {len(kg_entries)} KG entries.")
    print(f"Threshold for clan-wide propagation: >= {MIN_MEMBERS_FOR_PROPAGATION} members "
          f"AND >= {MIN_RATIO_FOR_PROPAGATION:.0%} ratio.")
    print()

    # Identify orphan clan_ids referenced by characters but not in clans.json.
    referenced_clans: set[str] = {c["clan"] for c in chars if c.get("clan")}
    orphan_clan_ids = sorted(referenced_clans - canonical_clan_ids)
    print(f"=== Orphan clan_ids (referenced by characters but absent from clans.json) ===")
    print(f"  {len(orphan_clan_ids)} entries: {orphan_clan_ids}")
    print(f"  These are filtered out of all derivations (likely scraping artifacts).")
    print()

    # Build empirical counts per clan, with whitelist filter
    clan_size: dict[str, int] = defaultdict(int)
    clan_kg_count: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    clan_nature_count: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for c in chars:
        clan = c.get("clan")
        if not clan or clan not in canonical_clan_ids:
            continue  # whitelist: skip non-canonical clan refs
        clan_size[clan] += 1
        for kg in c.get("kekkei_genkai") or []:
            clan_kg_count[clan][kg] += 1
        for nature in c.get("natures") or []:
            clan_nature_count[clan][nature] += 1

    def passes_threshold(observed: int, total: int) -> bool:
        if observed < MIN_MEMBERS_FOR_PROPAGATION:
            return False
        if total > 0 and (observed / total) < MIN_RATIO_FOR_PROPAGATION:
            return False
        return True

    clan_to_kgs: dict[str, set[str]] = defaultdict(set)
    clan_to_natures: dict[str, set[str]] = defaultdict(set)
    kg_to_clans: dict[str, set[str]] = defaultdict(set)

    for clan_id in canonical_clan_ids:
        size = clan_size.get(clan_id, 0)
        for kg, n in clan_kg_count.get(clan_id, {}).items():
            if passes_threshold(n, size):
                clan_to_kgs[clan_id].add(kg)
                kg_to_clans[kg].add(clan_id)
        for nature, n in clan_nature_count.get(clan_id, {}).items():
            if passes_threshold(n, size):
                clan_to_natures[clan_id].add(nature)

    # Print mapping summary (filtered)
    print("=== Filtered clan -> KG mappings (passes threshold) ===")
    for clan_id in sorted(clan_to_kgs.keys()):
        kgs = sorted(clan_to_kgs[clan_id])
        size = clan_size[clan_id]
        observed_str = ", ".join(
            f"{kg}({clan_kg_count[clan_id][kg]}/{size})" for kg in kgs
        )
        print(f"  {clan_id:25s} -> {observed_str}")
    print()

    print("=== Filtered clan -> natures mappings (passes threshold) ===")
    for clan_id in sorted(clan_to_natures.keys()):
        natures = sorted(clan_to_natures[clan_id])
        size = clan_size[clan_id]
        observed_str = ", ".join(
            f"{nat}({clan_nature_count[clan_id][nat]}/{size})" for nat in natures
        )
        print(f"  {clan_id:25s} -> {observed_str}")
    print()

    # Filtered-out cases (informative)
    print("=== Filtered-out (below threshold, NOT propagated) ===")
    print(f"{'clan':25s} {'trait':25s} {'count':>10s}")
    filtered_out: list[tuple[str, str, str, int, int]] = []
    for clan_id in canonical_clan_ids:
        size = clan_size.get(clan_id, 0)
        for kg, n in clan_kg_count.get(clan_id, {}).items():
            if not passes_threshold(n, size):
                filtered_out.append((clan_id, "KG", kg, n, size))
        for nature, n in clan_nature_count.get(clan_id, {}).items():
            if not passes_threshold(n, size):
                filtered_out.append((clan_id, "nature", nature, n, size))
    filtered_out.sort()
    for clan_id, kind, trait, n, size in filtered_out[:25]:
        print(f"  {clan_id:25s} {kind:8s} {trait:18s} {n}/{size}")
    if len(filtered_out) > 25:
        print(f"  ... and {len(filtered_out) - 25} more filtered out")
    print()

    # Plan modifications on clans.json
    clans_changes: list[tuple[str, str, list[str], list[str]]] = []
    for clan in clans:
        clan_id = clan["id"]
        # key_kekkei_genkai
        existing_kg = list(clan.get("key_kekkei_genkai") or [])
        observed_kg = sorted(clan_to_kgs.get(clan_id, set()))
        if observed_kg:
            merged_kg = sorted(set(existing_kg) | set(observed_kg))
            if merged_kg != existing_kg:
                clans_changes.append(
                    (clan_id, "key_kekkei_genkai", existing_kg, merged_kg)
                )
        # key_natures
        existing_nat = list(clan.get("key_natures") or [])
        observed_nat = sorted(clan_to_natures.get(clan_id, set()))
        if observed_nat:
            merged_nat = sorted(set(existing_nat) | set(observed_nat))
            if merged_nat != existing_nat:
                clans_changes.append((clan_id, "key_natures", existing_nat, merged_nat))

    print(f"=== Planned clans.json modifications ({len(clans_changes)} field updates) ===")
    for clan_id, field, before, after in clans_changes[:30]:
        diff = sorted(set(after) - set(before))
        print(f"  {clan_id:25s} {field:25s} +{diff}")
    if len(clans_changes) > 30:
        print(f"  ... and {len(clans_changes) - 30} more")
    print()

    # Plan modifications on kekkei_genkai.json (carrier_clans)
    kg_changes: list[tuple[str, list[str], list[str]]] = []
    for kg in kg_entries:
        kg_id = kg["id"]
        existing_carriers = list(kg.get("carrier_clans") or [])
        observed_carriers = sorted(kg_to_clans.get(kg_id, set()))
        if observed_carriers:
            merged_carriers = sorted(set(existing_carriers) | set(observed_carriers))
            if merged_carriers != existing_carriers:
                kg_changes.append((kg_id, existing_carriers, merged_carriers))

    print(f"=== Planned kekkei_genkai.json modifications ({len(kg_changes)} entries) ===")
    for kg_id, before, after in kg_changes:
        diff = sorted(set(after) - set(before))
        print(f"  {kg_id:30s} carrier_clans += {diff}")
    print()

    # Plan eligibility patch on characters (only for canonical clan refs)
    eligibility_patches: dict[str, dict[str, list[str]]] = {}
    for c in chars:
        cid = c["id"]
        clan = c.get("clan")
        if not clan or clan not in canonical_clan_ids:
            continue  # whitelist: skip non-canonical clan refs
        patch_entry: dict[str, list[str]] = {}
        if not (c.get("kekkei_genkai") or []):
            eligible_kg = sorted(clan_to_kgs.get(clan, set()))
            if eligible_kg:
                patch_entry["eligible_kekkei_genkai"] = eligible_kg
        if not (c.get("natures") or []):
            eligible_nat = sorted(clan_to_natures.get(clan, set()))
            if eligible_nat:
                patch_entry["eligible_natures"] = eligible_nat
        if patch_entry:
            eligibility_patches[cid] = patch_entry

    print(f"=== Planned character_eligibility_patch.json ({len(eligibility_patches)} characters) ===")
    sample = list(eligibility_patches.items())[:15]
    for cid, entry in sample:
        kg_list = entry.get("eligible_kekkei_genkai", [])
        nat_list = entry.get("eligible_natures", [])
        kg_str = f"KG={kg_list}" if kg_list else ""
        nat_str = f"natures={nat_list}" if nat_list else ""
        print(f"  {cid:30s} {kg_str} {nat_str}")
    if len(eligibility_patches) > 15:
        print(f"  ... and {len(eligibility_patches) - 15} more")
    print()

    # Orphan patches report
    birth_patch = json.loads(BIRTH_YEAR_PATCH_PATH.read_text(encoding="utf-8"))
    patches = birth_patch.get("patches", {})
    orphans = sorted(k for k in patches if k not in char_ids)
    print(f"=== Orphan birth_year patches ({len(orphans)} entries) ===")
    for cid in orphans[:10]:
        print(f"  {cid}: {patches[cid]}")
    if len(orphans) > 10:
        print(f"  ... and {len(orphans) - 10} more")
    print()

    if not args.apply:
        print(">>> DRY-RUN. No file written. Re-run with --apply to persist.")
        return 0

    # Apply: clans.json
    clan_index = {c["id"]: c for c in clans}
    for clan_id, field, _before, after in clans_changes:
        clan_index[clan_id][field] = after
    CLANS_PATH.write_text(
        json.dumps(clans, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # Apply: kekkei_genkai.json
    kg_index = {k["id"]: k for k in kg_entries}
    for kg_id, _before, after in kg_changes:
        kg_index[kg_id]["carrier_clans"] = after
    KG_PATH.write_text(
        json.dumps(kg_entries, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # Apply: character_eligibility_patch.json (new file)
    payload = {
        "_meta": {
            "schema_version": "1.0",
            "description": (
                "Pass 1 deterministic eligibility derivation. eligible_kekkei_genkai "
                "and eligible_natures are SETS of KG/nature ids that members of the "
                "character's clan are CANONICALLY ABLE to develop. NOT a possession: "
                "a character with eligible_kekkei_genkai=['sharingan'] does not have "
                "the Sharingan, the Uchiha clan can develop it."
            ),
            "source": "scripts/derive_canon_eligibility.py",
        },
        "patches": eligibility_patches,
    }
    ELIGIBILITY_PATCH_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # Apply: orphan patches report
    orphan_lines = [
        "# Orphan patches report",
        "",
        f"Generated by {Path(__file__).name}.",
        "",
        "These entries in `character_birth_years_patch.json` reference character ids "
        "that do not exist in `characters.json`. They are not currently applied at runtime "
        "(silently dropped during patch merge). To resolve: either fix the id, add the "
        "missing character to `characters.json`, or remove the patch entry.",
        "",
        f"Total orphans: {len(orphans)}",
        "",
        "| character_id (orphan) | patch content |",
        "|---|---|",
    ]
    for cid in orphans:
        orphan_lines.append(f"| `{cid}` | `{json.dumps(patches[cid], ensure_ascii=False)}` |")
    ORPHAN_PATCHES_REPORT_PATH.write_text("\n".join(orphan_lines) + "\n", encoding="utf-8")

    print(">>> Applied. Files written:")
    print(f"  - {CLANS_PATH.relative_to(ROOT)}")
    print(f"  - {KG_PATH.relative_to(ROOT)}")
    print(f"  - {ELIGIBILITY_PATCH_PATH.relative_to(ROOT)}")
    print(f"  - {ORPHAN_PATCHES_REPORT_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
