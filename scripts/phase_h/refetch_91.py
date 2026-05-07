"""Refetch le batch 9.1 avec schema relaxe + parsing plus tolerant.

Reuse les results batch deja payes (gratuit a refetcher 29 jours).
- Strip trailing data apres le 1er JSON object (gere 'Extra data' errors)
- Schema StructuredFact.value accepte list maintenant
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from phase_h.common import get_anthropic_client  # noqa: E402
from phase_h.schemas import EnrichedTimelineEvent  # noqa: E402

OUTPUT_PATH = (
    Path(__file__).parent.parent.parent
    / "data" / "canon" / "timeline_events_enriched.json"
)


def _extract_first_json(text: str) -> dict | None:
    """Trouve le 1er objet JSON complet dans text. Tolere trailing data."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
        if text.endswith("```"):
            text = text[:-3].strip()

    # Find balanced braces
    if not text.startswith("{"):
        return None
    depth = 0
    in_str = False
    escape = False
    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_str:
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[: i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def main() -> int:
    client = get_anthropic_client()
    # Trouve le batch 9.1 le plus recent (60 requests)
    batches = list(client.messages.batches.list(limit=10))
    target = None
    for b in batches:
        if (
            b.processing_status == "ended"
            and b.request_counts.succeeded == 60
        ):
            target = b
            break
    if not target:
        print("Batch 9.1 (60 requests) introuvable")
        return 1
    print(f"Batch : {target.id}")

    results: dict[str, EnrichedTimelineEvent] = {}
    errors: list[str] = []

    for entry in client.messages.batches.results(target.id):
        eid = entry.custom_id
        if entry.result.type != "succeeded":
            errors.append(f"{eid} : {entry.result.type}")
            continue
        msg = entry.result.message
        text = msg.content[0].text
        parsed = _extract_first_json(text)
        if parsed is None:
            errors.append(f"{eid} : no valid JSON object found")
            continue
        try:
            enriched = EnrichedTimelineEvent(**parsed)
            results[eid] = enriched
        except Exception as e:
            errors.append(f"{eid} : {str(e)[:100]}")

    print(f"Successful : {len(results)} / 60")
    if errors:
        print(f"Still erroring ({len(errors)}) :")
        for err in errors:
            print(f"  - {err}")

    output = {
        eid: enriched.model_dump(mode="json")
        for eid, enriched in results.items()
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"OK saved {len(results)} to {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
