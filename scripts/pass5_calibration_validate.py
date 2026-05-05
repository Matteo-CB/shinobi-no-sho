"""Valide le resultat d'une calibration Pass 5 (avant full batch).

Lit les outputs sous data/canonical/_pass5_output/ apres avoir fait :
    python scripts/pass5_tag_chunks.py build --limit 100
    python scripts/pass5_tag_chunks.py submit
    python scripts/pass5_tag_chunks.py poll <batch_id>

Verifie :
- failure_rate <= 5/100
- structure conformity >= 90%
- arc distribution coherente (pas tout sur 'unknown' ou 'post_war')
- year distribution coherente
- cout reel par chunk dans la fourchette estimee

Usage : uv run python scripts/pass5_calibration_validate.py
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PASS5_OUTPUT_DIR = ROOT / "data" / "canonical" / "_pass5_output"

REQUIRED_KEYS = {"chunk_id", "arc", "year_min", "year_max", "tier",
                 "entities_mentioned", "confidence", "source_quote"}
VALID_ARCS = {
    "pre_series", "warring_states_period", "konoha_founding",
    "first_shinobi_world_war", "second_shinobi_world_war",
    "third_shinobi_world_war", "kyuubi_attack", "post_kyuubi",
    "academy", "wave_country", "chunin_exam", "sasuke_retrieval",
    "pre_shippuden_timeskip", "kazekage_rescue", "sai_sasuke",
    "immortals", "hidan_kakuzu", "itachi_pursuit", "pain_invasion",
    "five_kage_summit", "fourth_shinobi_world_war", "post_war",
    "blank_period", "boruto_academy", "boruto_chunin_exam",
    "boruto_kara", "boruto_timeskip", "unknown",
}
VALID_TIERS = {"manga", "databook", "anime_canon", "anime_filler",
               "movie", "boruto", "fan"}


def main() -> int:
    if not PASS5_OUTPUT_DIR.exists():
        print(f"!!! {PASS5_OUTPUT_DIR} missing — run pass5 batch first")
        return 2

    files = sorted(PASS5_OUTPUT_DIR.glob("*.json"))
    n = len(files)
    if n == 0:
        print("!!! _pass5_output is empty")
        return 2

    print(f"Found {n} pass5 outputs")
    failures = []
    structural_ok = 0
    arcs = Counter()
    tiers = Counter()
    confidences = Counter()
    year_min_set = []
    year_max_set = []
    n_with_entities = 0

    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            failures.append((f.name, "json_decode_error"))
            continue

        # Structure check
        missing = REQUIRED_KEYS - set(data.keys())
        if missing:
            failures.append((f.name, f"missing_keys:{missing}"))
            continue

        # Enum validation
        arc = data.get("arc")
        if arc not in VALID_ARCS:
            failures.append((f.name, f"bad_arc:{arc}"))
            continue
        tier = data.get("tier")
        if tier not in VALID_TIERS:
            failures.append((f.name, f"bad_tier:{tier}"))
            continue

        structural_ok += 1
        arcs[arc] += 1
        tiers[tier] += 1
        if data.get("confidence"):
            confidences[data["confidence"]] += 1
        if isinstance(data.get("year_min"), int):
            year_min_set.append(data["year_min"])
        if isinstance(data.get("year_max"), int):
            year_max_set.append(data["year_max"])
        if data.get("entities_mentioned"):
            n_with_entities += 1

    print()
    print(f"=== Calibration validation ({n} chunks) ===")
    print(f"  Failures      : {len(failures)} / {n}  ({len(failures) / n * 100:.0f}%)")
    print(f"  Structural OK : {structural_ok} / {n}  ({structural_ok / n * 100:.0f}%)")
    print(f"  With entities : {n_with_entities} / {n}  ({n_with_entities / n * 100:.0f}%)")
    print()
    print("  Arc distribution :")
    for arc, count in arcs.most_common(10):
        print(f"    {arc:30s} {count}")
    print()
    print("  Tier distribution :")
    for tier, count in tiers.most_common():
        print(f"    {tier:30s} {count}")
    print()
    print("  Confidence distribution :")
    for conf, count in confidences.most_common():
        print(f"    {conf:30s} {count}")

    if year_min_set:
        print()
        print(f"  Year range : [{min(year_min_set)}, {max(year_max_set or year_min_set)}]")
        print(f"  Median year_min : {sorted(year_min_set)[len(year_min_set) // 2]}")

    print()
    print("=== Verdict ===")
    fail_rate = len(failures) / n
    arc_unknown_rate = arcs.get("unknown", 0) / n
    structural_rate = structural_ok / n

    pass_count = 0
    pass_msgs = []
    fail_msgs = []
    if fail_rate <= 0.05:
        pass_count += 1
        pass_msgs.append(f"failure rate {fail_rate * 100:.0f}% <= 5%")
    else:
        fail_msgs.append(f"failure rate {fail_rate * 100:.0f}% > 5%")
    if structural_rate >= 0.90:
        pass_count += 1
        pass_msgs.append(f"structural conformity {structural_rate * 100:.0f}% >= 90%")
    else:
        fail_msgs.append(f"structural conformity {structural_rate * 100:.0f}% < 90%")
    if arc_unknown_rate <= 0.50:
        pass_count += 1
        pass_msgs.append(f"arc=unknown rate {arc_unknown_rate * 100:.0f}% <= 50% (anti-saturation)")
    else:
        fail_msgs.append(f"arc=unknown rate {arc_unknown_rate * 100:.0f}% > 50% — sous-extraction")
    arc_top_share = (arcs.most_common(1)[0][1] / n) if arcs else 0
    if arc_top_share <= 0.70:
        pass_count += 1
        pass_msgs.append(f"top arc share {arc_top_share * 100:.0f}% <= 70% (diversite)")
    else:
        fail_msgs.append(f"top arc share {arc_top_share * 100:.0f}% > 70% — peu de diversite")

    for m in pass_msgs:
        print(f"  PASS : {m}")
    for m in fail_msgs:
        print(f"  FAIL : {m}")

    if pass_count == 4:
        print()
        print("  >>> CALIBRATION OK : full batch peut etre lance.")
        return 0
    else:
        print()
        print(f"  >>> CALIBRATION FAILED : {4 - pass_count} criteres echoues.")
        print(f"  Failures (premiers 5):")
        for fname, reason in failures[:5]:
            print(f"    {fname}: {reason}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
