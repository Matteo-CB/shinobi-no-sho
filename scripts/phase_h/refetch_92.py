"""Refetch le batch 9.2 + re-validate avec schema relaxe (apres bump bounds).

Les results batch Anthropic sont disponibles pour ~29 jours. On les
re-itere pour valider avec le nouveau schema CharacterDeepProfile (R G18-style
bounds 250 chars au lieu de 100).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from phase_h.common import get_anthropic_client  # noqa: E402
from phase_h.schemas import CharacterDeepProfile  # noqa: E402

OUTPUT_PATH = (
    Path(__file__).parent.parent.parent / "data" / "canon" / "deep_motivations.json"
)


def main() -> int:
    client = get_anthropic_client()

    # Trouve le batch 9.2 le plus recent
    batches = list(client.messages.batches.list(limit=10))
    if not batches:
        print("Aucun batch trouve")
        return 1
    # Pick le 1er ended (most recent)
    batch = next((b for b in batches if b.processing_status == "ended"), None)
    if not batch:
        print("Aucun batch ended")
        return 1
    print(f"Batch : {batch.id} ({batch.created_at})")

    results: dict[str, CharacterDeepProfile] = {}
    errors: list[str] = []

    for entry in client.messages.batches.results(batch.id):
        cid = entry.custom_id
        if entry.result.type != "succeeded":
            errors.append(f"{cid} : {entry.result.type}")
            continue
        msg = entry.result.message
        text = msg.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("```", 2)[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
            if text.endswith("```"):
                text = text[:-3].strip()
        try:
            parsed = json.loads(text)
            profile = CharacterDeepProfile(**parsed)
            results[cid] = profile
        except Exception as e:
            errors.append(f"{cid} : {str(e)[:80]}")

    print(f"Successful : {len(results)} / {len(results) + len(errors)}")
    if errors:
        print(f"Still erroring ({len(errors)}) :")
        for err in errors[:10]:
            print(f"  - {err}")

    output = {
        cid: profile.model_dump(mode="json")
        for cid, profile in results.items()
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"OK saved {len(results)} profiles to {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
