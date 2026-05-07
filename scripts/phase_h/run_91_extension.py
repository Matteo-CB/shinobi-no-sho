"""Phase H 9.1 EXTENSION : extrait 140-440 events nouveaux pour atteindre
le target spec 200-500 events. Strategy par-personnage (top-50).

Pour chaque PNJ top-50 :
- Prompt Claude avec : info canon perso + liste ids des 60 events deja extraits
- Demande : 3-5 events canon NOTABLES non couverts ci-dessus
- Output : list[EnrichedTimelineEvent] par perso

Validation cross :
- Dedup par event_id (unique)
- Merge avec timeline_events_enriched.json existant
- Reject events deja dans la liste 60 originelle

Coût estime : 50 batch × ~5K in + ~3K out = 250K + 150K = $1.50 batch
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from phase_h.common import CostTracker, get_anthropic_client  # noqa: E402
from phase_h.schemas import EnrichedTimelineEvent  # noqa: E402

OUTPUT_PATH = (
    Path(__file__).parent.parent.parent
    / "data" / "canon" / "timeline_events_enriched.json"
)
CANONICAL_PATH = Path(__file__).parent.parent.parent / "data" / "canonical"
DEEP_MOTIVATIONS_PATH = (
    Path(__file__).parent.parent.parent
    / "data" / "canon" / "deep_motivations.json"
)


SYSTEM_PROMPT = """Tu es l'archiviste de la timeline canon Naruto.
Ta tache : pour le personnage donne, identifier 3-5 evenements canon
NOTABLES dans son arc qui ne sont PAS dans la liste fournie.

Format de chaque event (JSON) :
- 'id' : snake_case unique (ex 'kakashi_anbu_init', 'naruto_pain_speech_year16')
- 'year' : annee in-game (year 0 = naissance Naruto)
- 'name_fr' : titre court 5-120 chars
- 'preconditions' : 0-15 facts {fact: "subject.attr", value: ...}
- 'outcomes' : 1-15 facts (idem format)
- 'narrative_invariants' : 1-5 verites narratives etablies par l'event
- 'alternative_seeds' : 1-5 questions ouvertes 'Si X alors ?'

Contraintes :
- Pas de tirets cadratins, pas d'emoji.
- Reste FIDELE au canon. Pas d'invention.
- IDs en snake_case strict.
- Cible : 3 a 5 events par perso.
- Reponds JSON STRICT : {"events": [event1, event2, ...]}
- Sans markdown wrap.
"""


def _build_char_prompt(
    char: dict, existing_event_ids: list[str],
) -> str:
    cid = char.get("id", "?")
    name = char.get("name_fr") or char.get("name_romaji") or "?"
    clan = char.get("clan", "?")
    village = char.get("village_of_origin", "?")
    birth = char.get("birth_year", "?")
    death = char.get("death_year", "vivant ou inconnu")
    personality = (char.get("personality_fr") or "")[:1000]

    parts = [
        f"Personnage : {cid}",
        f"Nom : {name} | Clan : {clan} | Village : {village}",
        f"Naissance / mort : year {birth} / {death}",
        "",
        "Personnalite (extrait) :",
        personality if personality else "(non specifiee)",
        "",
        "EVENTS CANON DEJA EXTRAITS (ne pas re-lister) :",
    ]
    parts.extend(f"  - {eid}" for eid in existing_event_ids)
    parts.extend([
        "",
        f"Identifie 3 a 5 EVENTS CANON NOTABLES impliquant {cid}",
        "qui ne sont PAS dans la liste ci-dessus.",
        "",
        "Critere notable : impact dramatique sur l'arc du perso ou sur",
        "d'autres persos ; formation/dissolution de team ; mission majeure ;",
        "mort ou perte importante ; revelation ; transformation de pouvoir.",
        "",
        "Format JSON strict :",
        '  {"events": [{"id": "...", "year": ..., "name_fr": "...",',
        '     "preconditions": [...], "outcomes": [...],',
        '     "narrative_invariants": [...], "alternative_seeds": [...]}, ...]}',
        "",
        "Reponds UNIQUEMENT le JSON.",
    ])
    return "\n".join(parts)


def _wait_for_batch(client, batch_id: str) -> None:
    print(f"Polling batch {batch_id}...")
    start = time.time()
    while time.time() - start < 3600:
        batch = client.messages.batches.retrieve(batch_id)
        status = batch.processing_status
        counts = batch.request_counts
        elapsed = int(time.time() - start)
        print(
            f"  [{elapsed}s] status={status}, "
            f"succeeded={counts.succeeded}, errored={counts.errored}, "
            f"processing={counts.processing}",
        )
        if status == "ended":
            return
        time.sleep(20)
    raise TimeoutError("Batch timeout")


def _extract_first_json(text: str) -> dict | None:
    """Tolerant parser : trouve le 1er objet JSON balance dans text."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
        if text.endswith("```"):
            text = text[:-3].strip()
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
    print(">>> Phase H 9.1 EXTENSION : events nouveaux par-perso top-50 <<<")

    # 1. Load canon characters + existing events + top-50 from deep_motivations
    chars_raw = json.loads(
        (CANONICAL_PATH / "characters.json").read_text(encoding="utf-8"),
    )
    chars_dict = (
        chars_raw if isinstance(chars_raw, dict)
        else {c.get("id"): c for c in chars_raw if c.get("id")}
    )
    deep = json.loads(DEEP_MOTIVATIONS_PATH.read_text(encoding="utf-8"))
    top_50_ids = list(deep.keys())
    print(f"Top-50 PNJ from 9.2 : {len(top_50_ids)}")

    # Existing enriched events
    existing_enriched = json.loads(
        OUTPUT_PATH.read_text(encoding="utf-8"),
    )
    existing_event_ids = list(existing_enriched.keys())
    print(f"Events deja enrichis : {len(existing_event_ids)}")

    # 2. Build batch
    requests = []
    for cid in top_50_ids:
        char = chars_dict.get(cid)
        if not char:
            continue
        user_msg = _build_char_prompt(char, existing_event_ids)
        requests.append({
            "custom_id": cid,
            "params": {
                "model": "claude-sonnet-4-6",
                "max_tokens": 4000,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": user_msg}],
            },
        })
    print(f"Batch requests : {len(requests)}")

    # Cost estimate
    avg_input = sum(
        len(r["params"]["messages"][0]["content"]) for r in requests
    ) // 4 // len(requests)
    total_in = avg_input * len(requests) + (len(SYSTEM_PROMPT) // 4) * len(requests)
    total_out = 2500 * len(requests)
    tracker = CostTracker.load()
    estimated = tracker.estimate(
        input_tokens=total_in, output_tokens=total_out, batch=True,
    )
    print(f"Estimated batch cost : ${estimated:.4f}")
    print(f"Current total : ${tracker.total_usd:.4f}")
    if not tracker.can_afford(estimated):
        print("REFUSED")
        return 1

    client = get_anthropic_client()
    print("Submitting batch...")
    batch = client.messages.batches.create(requests=requests)
    print(f"Batch id : {batch.id}")
    _wait_for_batch(client, batch.id)

    # Fetch + validate + dedup
    print("Fetching results...")
    new_events: dict[str, EnrichedTimelineEvent] = {}
    actual_in = 0
    actual_out = 0
    skipped_already_existing = 0
    skipped_dup_in_batch = 0
    parse_errors: list[str] = []
    validate_errors: list[str] = []

    for entry in client.messages.batches.results(batch.id):
        cid = entry.custom_id
        if entry.result.type != "succeeded":
            parse_errors.append(f"{cid} : {entry.result.type}")
            continue
        msg = entry.result.message
        if hasattr(msg, "usage"):
            actual_in += msg.usage.input_tokens
            actual_out += msg.usage.output_tokens
        text = msg.content[0].text
        parsed = _extract_first_json(text)
        if parsed is None:
            parse_errors.append(f"{cid} : no JSON")
            continue
        events_list = parsed.get("events") if isinstance(parsed, dict) else None
        if not isinstance(events_list, list):
            parse_errors.append(f"{cid} : 'events' not a list")
            continue
        for ev_dict in events_list:
            if not isinstance(ev_dict, dict):
                continue
            ev_id = ev_dict.get("id", "")
            if not ev_id:
                continue
            if ev_id in existing_event_ids:
                skipped_already_existing += 1
                continue
            if ev_id in new_events:
                skipped_dup_in_batch += 1
                continue
            try:
                enriched = EnrichedTimelineEvent(**ev_dict)
                new_events[ev_id] = enriched
            except Exception as e:
                validate_errors.append(f"{cid}::{ev_id} : {str(e)[:100]}")

    print(f"New events validated : {len(new_events)}")
    print(f"Skipped (already in existing 60) : {skipped_already_existing}")
    print(f"Skipped (dup intra-batch) : {skipped_dup_in_batch}")
    if parse_errors:
        print(f"Parse errors ({len(parse_errors)}) : {parse_errors[:5]}")
    if validate_errors:
        print(f"Validate errors ({len(validate_errors)}) : {validate_errors[:5]}")

    # Record cost
    entry = tracker.record(
        dataset="9.1_timeline_extension", mode="batch",
        input_tokens=actual_in, output_tokens=actual_out,
    )
    print(f"Actual cost : ${entry.cost_usd:.4f}")
    print(f"Total Phase H : ${tracker.total_usd:.4f} / $25")

    # Merge into existing
    merged = dict(existing_enriched)
    for eid, enriched in new_events.items():
        merged[eid] = enriched.model_dump(mode="json")
    OUTPUT_PATH.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"OK saved {len(merged)} events ({len(new_events)} new) to {OUTPUT_PATH}")
    print(f"\n{tracker.summary()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
