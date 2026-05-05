"""Validation post-extraction des source_quotes Pass 2.

Pour chaque champ extrait avec une `source_quote`, verifie que la quote
apparait textuellement dans les wiki_sections du perso.

Strategie (cf. research/pass2-extraction-spec.md section 7) :
1. Normalisation Unicode NFKD + lowercase sur source_text et quote.
2. Substring exact : si quote in source -> EXACT match.
3. Sinon, edit_distance Levenshtein sur la fenetre la plus proche :
   - <= 5 -> NEAR match (warning, accept)
   - > 5 -> MISS (flag hallucination_probable, downgrade confidence)

Sortie : rapport stdout + JSON par char_id pour audit.
"""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHARACTERS_PATH = ROOT / "data" / "canonical" / "characters.json"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "canonical" / "_pass2_output_dryrun"


# Map typographic punctuation to ASCII before NFKD (NFKD does NOT do this on its own).
_PUNCT_MAP = str.maketrans({
    "‘": "'",  # left single quotation
    "’": "'",  # right single quotation (typographic apostrophe)
    "‚": "'",  # single low-9 quotation
    "‛": "'",  # single high-reversed-9 quotation
    "“": '"',  # left double quotation
    "”": '"',  # right double quotation
    "„": '"',  # double low-9 quotation
    "–": "-",  # en dash
    "—": "-",  # em dash
    "…": "...",  # horizontal ellipsis (NFKD already maps but be safe)
    " ": " ",   # non-breaking space
})


def normalize(text: str) -> str:
    """Map typographic punctuation, then NFKD, lowercase, collapse whitespace."""
    if not text:
        return ""
    n = text.translate(_PUNCT_MAP)
    n = unicodedata.normalize("NFKD", n)
    n = n.lower()
    n = " ".join(n.split())
    return n


def levenshtein(a: str, b: str) -> int:
    """Edit distance (Levenshtein). O(len(a) * len(b))."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            cur[j] = min(cur[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[len(b)]


def best_window_distance(quote: str, source: str) -> int:
    """Trouve la fenetre de meme longueur que quote dans source la plus proche.

    O(len(source) * len(quote)). Acceptable pour ~5KB sources.
    """
    if not quote or not source:
        return max(len(quote), len(source))
    n = len(source)
    m = len(quote)
    if m > n:
        return levenshtein(quote, source)
    best = m  # max possible distance
    # On scanne par fenetres glissantes de taille m. Optimisation : on saute par
    # paquets de m // 4 d'abord pour eviter le pire cas O(n*m^2).
    step = max(1, m // 4)
    candidates: list[int] = list(range(0, n - m + 1, step))
    if (n - m) not in candidates:
        candidates.append(n - m)
    for start in candidates:
        window = source[start:start + m]
        d = levenshtein(quote, window)
        if d < best:
            best = d
            if d == 0:
                return 0
    return best


@dataclass
class QuoteCheck:
    char_id: str
    field_path: str
    quote: str
    status: str  # exact, near, miss, no_quote
    edit_distance: int | None
    matched_window: str | None


def collect_quotes(extraction: dict) -> list[tuple[str, str | None]]:
    """Yield all (field_path, quote) pairs from an extraction JSON."""
    quotes: list[tuple[str, str | None]] = []
    fields = extraction.get("fields", {})
    for field_name, value in fields.items():
        if isinstance(value, dict) and "source_quote" in value:
            quotes.append((field_name, value.get("source_quote")))
        elif isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, dict) and "source_quote" in item:
                    quotes.append((f"{field_name}[{i}].{item.get('value', item.get('rank', '?'))}",
                                   item.get("source_quote")))
    return quotes


def assemble_source(char: dict) -> str:
    """Concat all wiki_sections for a character."""
    sections = char.get("wiki_sections") or {}
    return "\n\n".join(sections.values())


def check_one_extraction(extraction: dict, char: dict) -> list[QuoteCheck]:
    char_id = extraction.get("character_id", char["id"])
    source_text = assemble_source(char)
    norm_source = normalize(source_text)

    checks: list[QuoteCheck] = []
    for field_path, quote in collect_quotes(extraction):
        if quote is None:
            checks.append(QuoteCheck(char_id, field_path, "", "no_quote", None, None))
            continue
        norm_quote = normalize(quote)
        if norm_quote in norm_source:
            checks.append(QuoteCheck(char_id, field_path, quote, "exact", 0, None))
            continue
        d = best_window_distance(norm_quote, norm_source)
        if d <= 5:
            # capture context for debugging
            idx = norm_source.find(norm_quote[:20]) if len(norm_quote) >= 20 else -1
            window = norm_source[max(0, idx):idx + len(norm_quote)] if idx >= 0 else None
            checks.append(QuoteCheck(char_id, field_path, quote, "near", d, window))
        else:
            checks.append(QuoteCheck(char_id, field_path, quote, "miss", d, None))
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory containing the *.json extraction files",
    )
    parser.add_argument(
        "--write-report",
        action="store_true",
        help="Write a JSON report next to the output dir",
    )
    args = parser.parse_args()

    chars = json.loads(CHARACTERS_PATH.read_text(encoding="utf-8"))
    char_index = {c["id"]: c for c in chars}

    output_files = sorted(args.output_dir.glob("*.json"))
    if not output_files:
        print(f"No JSON found in {args.output_dir}.", file=sys.stderr)
        return 1

    all_checks: list[QuoteCheck] = []
    summary: dict[str, dict[str, int]] = {}

    for path in output_files:
        if path.name.startswith("_"):
            continue
        extraction = json.loads(path.read_text(encoding="utf-8"))
        cid = extraction.get("character_id", path.stem)
        char = char_index.get(cid)
        if char is None:
            print(f"WARN: unknown char_id {cid}, skipping")
            continue
        checks = check_one_extraction(extraction, char)
        all_checks.extend(checks)
        s = {"exact": 0, "near": 0, "miss": 0, "no_quote": 0, "total_with_quote": 0}
        for c in checks:
            s[c.status] += 1
            if c.quote:
                s["total_with_quote"] += 1
        summary[cid] = s

    # Print per-char summary
    print(f"{'char_id':30s} {'exact':>6s} {'near':>6s} {'miss':>6s} {'no_quote':>9s} {'total_q':>8s}")
    print("-" * 70)
    for cid, s in summary.items():
        print(f"{cid:30s} {s['exact']:>6d} {s['near']:>6d} {s['miss']:>6d} "
              f"{s['no_quote']:>9d} {s['total_with_quote']:>8d}")
    print()

    # Print misses in detail
    misses = [c for c in all_checks if c.status == "miss"]
    if misses:
        print(f"=== MISSES ({len(misses)}) ===")
        for c in misses:
            quote_preview = c.quote[:80] + ("..." if len(c.quote) > 80 else "")
            print(f"  [{c.char_id}] {c.field_path}: ed={c.edit_distance}")
            print(f"    quote: {quote_preview!r}")
        print()
    else:
        print("=== NO MISSES ===")
        print()

    # Print near matches in detail
    nears = [c for c in all_checks if c.status == "near"]
    if nears:
        print(f"=== NEAR MATCHES ({len(nears)}) ===")
        for c in nears:
            quote_preview = c.quote[:80] + ("..." if len(c.quote) > 80 else "")
            print(f"  [{c.char_id}] {c.field_path}: ed={c.edit_distance}")
            print(f"    quote: {quote_preview!r}")
        print()

    # Aggregate stats
    total_q = sum(s["total_with_quote"] for s in summary.values())
    total_exact = sum(s["exact"] for s in summary.values())
    total_near = sum(s["near"] for s in summary.values())
    total_miss = sum(s["miss"] for s in summary.values())
    print("=== AGGREGATE ===")
    print(f"  Total quotes checked : {total_q}")
    if total_q > 0:
        print(f"  Exact matches : {total_exact} ({100 * total_exact / total_q:.1f}%)")
        print(f"  Near matches  : {total_near} ({100 * total_near / total_q:.1f}%)")
        print(f"  Misses        : {total_miss} ({100 * total_miss / total_q:.1f}%)")

    if args.write_report:
        report_path = args.output_dir.parent / "_pass2_validation_report.json"
        report_path.write_text(
            json.dumps(
                {"summary": summary, "checks": [asdict(c) for c in all_checks]},
                indent=2, ensure_ascii=False,
            ) + "\n",
            encoding="utf-8",
        )
        print(f"Wrote {report_path.relative_to(ROOT)}")

    return 0 if total_miss == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
