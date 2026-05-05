"""Mesure la distribution des tokens des wiki_sections par perso.

Pre-requis du Pass 2 (cf. research/canon-completion-plan.md). Permet de
recalculer le cout reel sur Groq gpt-oss-120b avant de lancer le batch.

Sections incluses (les plus susceptibles de porter des facts a extraire) :
- Background
- Personality
- Abilities
- Part I, Part II, Blank Period, New Era, New Era: Part I, New Era: Part II
- Quotes (occasionnel mais utile pour speech_patterns)

Sortie :
- research/wiki-sections-token-stats.md : stats globales + top 20 plus gros
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

import tiktoken

ROOT = Path(__file__).resolve().parents[1]
CHARACTERS_PATH = ROOT / "data" / "canonical" / "characters.json"
OUTPUT_PATH = ROOT / "research" / "wiki-sections-token-stats.md"

# cl100k_base est un proxy raisonnable pour les modeles BPE-like.
# gpt-oss-120b utilise un tokenizer Llama-like (legerement different) mais
# les ratios chars/tokens sont tres proches sur de l'anglais courant
# (~3.7 chars/token).
ENCODER_NAME = "cl100k_base"

SECTIONS_TO_INCLUDE = [
    "Background",
    "Personality",
    "Abilities",
    "Part I",
    "Part II",
    "Blank Period",
    "New Era",
    "New Era: Part I",
    "New Era: Part II",
    "Quotes",
    "Plot Overview",
    "Legacy",
]


def main() -> None:
    enc = tiktoken.get_encoding(ENCODER_NAME)
    chars = json.loads(CHARACTERS_PATH.read_text(encoding="utf-8"))
    print(f"Loaded {len(chars)} characters. Encoder: {ENCODER_NAME}.")
    print()

    per_char: list[tuple[str, str, int, int, list[str]]] = []
    skipped = 0

    for c in chars:
        wiki = c.get("wiki_sections") or {}
        if not wiki:
            skipped += 1
            continue
        included_sections: list[str] = []
        chunks: list[str] = []
        for s in SECTIONS_TO_INCLUDE:
            text = wiki.get(s)
            if text:
                included_sections.append(s)
                chunks.append(f"[{s}]\n{text}")
        if not chunks:
            # Fallback : prendre les sections existantes (Appearance, Trivia, ...)
            for s, text in wiki.items():
                if text:
                    chunks.append(f"[{s}]\n{text}")
                    included_sections.append(s)
        full_text = "\n\n".join(chunks)
        char_count = len(full_text)
        token_count = len(enc.encode(full_text)) if full_text else 0
        per_char.append(
            (c["id"], c.get("name_romaji", ""), char_count, token_count, included_sections)
        )

    print(f"Characters with at least one wiki section: {len(per_char)}.")
    print(f"Skipped (no wiki_sections at all): {skipped}.")
    print()

    tokens = [t for _, _, _, t, _ in per_char if t > 0]
    char_counts = [c for _, _, c, _, _ in per_char if c > 0]

    if not tokens:
        print("No tokens to analyze. Aborting.")
        return

    tokens_sorted = sorted(tokens)
    n = len(tokens)

    def pct(values: list[int], p: float) -> int:
        if not values:
            return 0
        idx = int(p * (len(values) - 1))
        return values[idx]

    stats = {
        "n_chars_with_wiki": n,
        "tokens_total": sum(tokens),
        "tokens_mean": int(statistics.mean(tokens)),
        "tokens_median": int(statistics.median(tokens)),
        "tokens_p25": pct(tokens_sorted, 0.25),
        "tokens_p50": pct(tokens_sorted, 0.50),
        "tokens_p75": pct(tokens_sorted, 0.75),
        "tokens_p90": pct(tokens_sorted, 0.90),
        "tokens_p95": pct(tokens_sorted, 0.95),
        "tokens_p99": pct(tokens_sorted, 0.99),
        "tokens_max": max(tokens),
        "tokens_min": min(tokens),
        "chars_mean": int(statistics.mean(char_counts)),
        "chars_max": max(char_counts),
        "ratio_chars_per_token": round(sum(char_counts) / sum(tokens), 2),
    }
    print("=== Stats globales ===")
    for k, v in stats.items():
        print(f"  {k:32s} {v}")
    print()

    # Histogramme par buckets
    buckets = [
        (0, 500),
        (500, 1000),
        (1000, 2000),
        (2000, 4000),
        (4000, 6000),
        (6000, 8000),
        (8000, 12000),
        (12000, 20000),
        (20000, 1_000_000),
    ]
    histogram: list[tuple[str, int]] = []
    for lo, hi in buckets:
        count = sum(1 for t in tokens if lo <= t < hi)
        label = f"{lo:>5} - {hi:>6}" if hi < 1_000_000 else f"{lo:>5}+"
        histogram.append((label, count))
    print("=== Histogramme ===")
    for label, count in histogram:
        bar = "#" * min(60, count * 60 // max(1, max(c for _, c in histogram)))
        print(f"  {label} tokens : {count:>4d}  {bar}")
    print()

    # Top 20 most expensive
    sorted_by_tokens = sorted(per_char, key=lambda x: -x[3])
    print("=== Top 20 most expensive characters ===")
    for cid, name, ch, tok, sections in sorted_by_tokens[:20]:
        print(f"  {cid:30s} {name[:25]:25s} {tok:>6d} tokens  {ch:>7d} chars  ({len(sections)} sections)")
    print()

    # Cost projection on Groq gpt-oss-120b
    # Pricing : $0.15/M input, $0.60/M output
    # Output cap : 2000 tokens (per spec). Average expected output ~600.
    # System prompt + schema overhead : estimated ~1500 tokens, sent on every call (no cache).
    SYSTEM_OVERHEAD_TOKENS = 1500
    OUTPUT_AVG_TOKENS = 600
    INPUT_PRICE_PER_M = 0.15
    OUTPUT_PRICE_PER_M = 0.60

    total_input_tokens = sum(tokens) + SYSTEM_OVERHEAD_TOKENS * n
    total_output_tokens = OUTPUT_AVG_TOKENS * n
    cost_input = total_input_tokens * INPUT_PRICE_PER_M / 1_000_000
    cost_output = total_output_tokens * OUTPUT_PRICE_PER_M / 1_000_000
    cost_total = cost_input + cost_output

    print("=== Cost projection (Groq gpt-oss-120b, full batch) ===")
    print(f"  N characters processed       : {n}")
    print(f"  Total input tokens (incl. system overhead) : {total_input_tokens:,}")
    print(f"  Total output tokens (cap {OUTPUT_AVG_TOKENS}) : {total_output_tokens:,}")
    print(f"  Cost input  ({INPUT_PRICE_PER_M}/M) : ${cost_input:.3f}")
    print(f"  Cost output ({OUTPUT_PRICE_PER_M}/M) : ${cost_output:.3f}")
    print(f"  Cost TOTAL                   : ${cost_total:.3f}")
    print()

    # Worst-case if we hit token cap on outputs
    OUTPUT_CAP_TOKENS = 2000
    cost_worst_output = OUTPUT_CAP_TOKENS * n * OUTPUT_PRICE_PER_M / 1_000_000
    cost_worst = cost_input + cost_worst_output
    print(f"  WORST CASE (output cap {OUTPUT_CAP_TOKENS} hit on every call) : ${cost_worst:.3f}")
    print()

    # Wall time projection
    # 250K TPM rate limit on gpt-oss-120b (Developer plan)
    TPM_LIMIT = 250_000
    minutes_minimum = total_input_tokens / TPM_LIMIT
    print(f"  Wall-time floor (TPM bound)  : {minutes_minimum:.1f} min")

    # Write markdown report
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        f.write("# Wiki sections token statistics\n\n")
        f.write(f"Generated by `{Path(__file__).name}`. Encoder: `{ENCODER_NAME}` "
                f"(proxy for gpt-oss-120b BPE).\n\n")
        f.write(f"Sections included: {', '.join(SECTIONS_TO_INCLUDE)}.\n\n")
        f.write("If none of these sections are present, falls back to all sections "
                "(Appearance, Trivia, etc.).\n\n")

        f.write("## Coverage\n\n")
        f.write(f"- Characters total : {len(chars)}\n")
        f.write(f"- Characters with at least one wiki section : {n}\n")
        f.write(f"- Skipped (no wiki_sections at all) : {skipped}\n\n")

        f.write("## Token statistics\n\n")
        f.write("| Metric | Value |\n|---|---:|\n")
        for k, v in stats.items():
            f.write(f"| {k} | {v:,} |\n" if isinstance(v, int) else f"| {k} | {v} |\n")
        f.write("\n")

        f.write("## Histogram\n\n")
        f.write("| Bucket (tokens) | Count |\n|---|---:|\n")
        for label, count in histogram:
            f.write(f"| `{label.strip()}` | {count} |\n")
        f.write("\n")

        f.write("## Top 20 most expensive characters\n\n")
        f.write("| Rank | id | name | tokens | chars | sections |\n|---|---|---|---:|---:|---:|\n")
        for i, (cid, name, ch, tok, sections) in enumerate(sorted_by_tokens[:20], 1):
            f.write(f"| {i} | `{cid}` | {name} | {tok:,} | {ch:,} | {len(sections)} |\n")
        f.write("\n")

        f.write("## Cost projection on Groq `openai/gpt-oss-120b`\n\n")
        f.write(f"Pricing: ${INPUT_PRICE_PER_M}/M input, ${OUTPUT_PRICE_PER_M}/M output. "
                f"No native batch API on Groq, no prompt caching.\n\n")
        f.write(f"- N characters to process : {n}\n")
        f.write(f"- System+schema overhead per call : {SYSTEM_OVERHEAD_TOKENS} tokens "
                f"(no cache, sent on every call)\n")
        f.write(f"- Output cap per call : {OUTPUT_CAP_TOKENS} tokens\n")
        f.write(f"- Output expected average : {OUTPUT_AVG_TOKENS} tokens\n")
        f.write(f"- Total input tokens : {total_input_tokens:,}\n")
        f.write(f"- Total output tokens (avg) : {total_output_tokens:,}\n\n")
        f.write("| Component | Cost |\n|---|---:|\n")
        f.write(f"| Input | ${cost_input:.3f} |\n")
        f.write(f"| Output (avg) | ${cost_output:.3f} |\n")
        f.write(f"| **Total expected** | **${cost_total:.3f}** |\n")
        f.write(f"| Worst case (output cap hit on every call) | ${cost_worst:.3f} |\n\n")

        f.write("## Wall-time projection\n\n")
        f.write(f"- Groq Developer plan TPM limit on gpt-oss-120b : {TPM_LIMIT:,} TPM\n")
        f.write(f"- Total input tokens : {total_input_tokens:,}\n")
        f.write(f"- Minimum wall time (TPM bound, single stream) : {minutes_minimum:.1f} min\n")
        f.write(f"- With 10 concurrent workers, throughput is bounded by TPM, "
                f"not RPM (1K RPM is plenty). Same floor.\n")
        f.write(f"- Realistic estimate including per-call latency : 60-120 min total wall time.\n\n")

        f.write("## Notes\n\n")
        f.write("- The ratio chars/token is ~"
                f"{stats['ratio_chars_per_token']}, consistent with English text "
                "(typical 3.5-4.0 for cl100k_base).\n")
        f.write("- For perso who exceeds 6000 tokens (unusual but happens for top chars "
                "like Naruto, Sasuke, Madara), the prompt should truncate Background+Part I "
                "first if it would exceed a soft cap of ~7000 input tokens.\n")
        f.write("- The hard limit `cost_cumulative > $5` in the orchestrator script kicks "
                "in only if outputs exceed the average projection by ~3x, which is unlikely.\n")

    print(f"Wrote {OUTPUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
