"""Phase H 9.1 : enrichissement des 60 timeline events canon via Batch API.

Spec doc 02 §9.1 : preconditions/outcomes structures + narrative_invariants
+ alternative_seeds. Le format actuel est basique (texte libre) ; on enrichit
chaque event avec une structure consommable par Phase F validator.

Strategy :
- 60 batch requests (1 par event canon).
- Chaque request : event raw + system prompt -> JSON enrichi.
- Validate via EnrichedTimelineEvent Pydantic.
- Output : data/canon/timeline_events_enriched.json (dict {event_id: enriched}).

Coût estime batch : 60 × 4K input + 60 × 2K output
  = 240K * $1.5/M + 120K * $7.5/M = $0.36 + $0.90 = $1.26
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

SYSTEM_PROMPT = """Tu es l'archiviste structurel de la timeline canon Naruto.
Ta tache : enrichir un event canon avec des PRECONDITIONS et OUTCOMES
structures (consommables par un moteur de simulation), des INVARIANTS
NARRATIFS, et des ALTERNATIVE SEEDS (questions ouvertes pour la creativite).

Format de chaque fact (precondition ou outcome) :
  {"fact": "subject.attribute", "value": ...}
Exemples :
  {"fact": "uchiha_itachi.alive", "value": true}
  {"fact": "shimura_danzo.position", "value": "foundation_leader"}
  {"fact": "uchiha_clan.coup_d_etat_planned", "value": true}

Champs requis :
- 'preconditions' : 0-15 facts requis pour que l'event ait pu se produire
  canon. Reference les acteurs cles, leurs etats, leurs intentions.
- 'outcomes' : 1-15 facts directement causes par l'event. Inclure les
  morts, changements de role, traumas psychologiques, etc.
- 'narrative_invariants' : 1-5 verites narratives que l'event etablit
  ('Sasuke devient orphelin obsede par la vengeance').
- 'alternative_seeds' : 1-5 questions ouvertes (pas de reponses) sur ce
  qui se passerait si certaines conditions etaient changees.
  Exemple : 'Si itachi.alive=true mais coup_d_etat=exposed, alors ?'

Contraintes :
- Pas de tirets cadratins, pas d'emoji, pas d'argot otaku.
- Reste FIDELE au canon. Pas d'invention de personnages ou faits.
- Ids canon en snake_case (uchiha_itachi, shimura_danzo, etc.).
- 'fact' field : 3-100 chars. 'value' : str/int/bool/null.
- Reponds en JSON STRICT conforme au schema EnrichedTimelineEvent,
  sans markdown wrap.
"""


def _build_event_prompt(event: dict) -> str:
    eid = event.get("id", "?")
    name = event.get("name_fr") or event.get("name_romaji") or "?"
    year = event.get("year", "?")
    narrative = event.get("narrative_summary_fr") or ""
    involved = event.get("involved_characters", [])
    location = event.get("location", "?")
    canonicity = event.get("canonicity", "?")
    raw_outcomes = event.get("outcomes", []) or []
    raw_preconditions = event.get("preconditions", []) or []

    parts = [
        f"Event canon a enrichir : {eid}",
        f"Annee : {year}",
        f"Nom : {name}",
        f"Lieu : {location}",
        f"Canonicite : {canonicity}",
        f"Personnages impliques : {', '.join(involved[:10])}",
        "",
        "Narration canon :",
        narrative[:1500] if narrative else "(non specifiee)",
        "",
    ]
    if raw_preconditions:
        parts.append("Preconditions canon brutes (a structurer) :")
        for p in raw_preconditions[:5]:
            parts.append(f"  - {p}")
        parts.append("")
    if raw_outcomes:
        parts.append("Outcomes canon bruts (a structurer) :")
        for o in raw_outcomes[:5]:
            parts.append(f"  - {o}")
        parts.append("")

    parts.extend([
        f"Produis le JSON enrichi pour event_id='{eid}', year={year}.",
        "Format :",
        '  {"id": "' + str(eid) + '", "year": ' + str(year) + ', "name_fr": "...",',
        '   "preconditions": [{"fact": "...", "value": ...}, ...],',
        '   "outcomes": [{"fact": "...", "value": ...}, ...],',
        '   "narrative_invariants": ["...", ...],',
        '   "alternative_seeds": ["Si ..., alors ?", ...]}',
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
    raise TimeoutError(f"Batch {batch_id} timeout 1h")


def main() -> int:
    print(">>> Phase H 9.1 : timeline events enrichis (Batch API) <<<")

    events = json.loads(
        (CANONICAL_PATH / "timeline_events.json").read_text(encoding="utf-8"),
    )
    if isinstance(events, dict):
        events_list = list(events.values())
    else:
        events_list = events
    print(f"Canon timeline_events : {len(events_list)}")

    requests = []
    for ev in events_list:
        eid = ev.get("id")
        if not eid:
            continue
        user_msg = _build_event_prompt(ev)
        requests.append({
            "custom_id": eid,
            "params": {
                "model": "claude-sonnet-4-6",
                "max_tokens": 4000,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": user_msg}],
            },
        })
    print(f"Requests : {len(requests)}")

    # Cost estimate
    avg_input = sum(
        len(r["params"]["messages"][0]["content"])
        for r in requests
    ) // 4 // len(requests)
    total_input_estimate = (
        avg_input * len(requests)
        + (len(SYSTEM_PROMPT) // 4) * len(requests)
    )
    total_output_estimate = 2500 * len(requests)

    tracker = CostTracker.load()
    estimated_cost = tracker.estimate(
        input_tokens=total_input_estimate,
        output_tokens=total_output_estimate,
        batch=True,
    )
    print(f"Estimated cost (batch) : ${estimated_cost:.4f}")
    print(f"Current total : ${tracker.total_usd:.4f}")
    if not tracker.can_afford(estimated_cost):
        print("REFUSED : would exceed hard budget")
        return 1

    client = get_anthropic_client()
    print("Submitting batch...")
    batch = client.messages.batches.create(requests=requests)
    print(f"Batch id : {batch.id}")

    _wait_for_batch(client, batch.id)

    # Fetch results
    print("Fetching results...")
    results: dict[str, EnrichedTimelineEvent] = {}
    actual_input_total = 0
    actual_output_total = 0
    errors: list[str] = []

    for entry in client.messages.batches.results(batch.id):
        eid = entry.custom_id
        if entry.result.type != "succeeded":
            errors.append(f"{eid} : {entry.result.type}")
            continue
        msg = entry.result.message
        if hasattr(msg, "usage"):
            actual_input_total += msg.usage.input_tokens
            actual_output_total += msg.usage.output_tokens
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
            enriched = EnrichedTimelineEvent(**parsed)
            results[eid] = enriched
        except Exception as e:
            errors.append(f"{eid} : {str(e)[:120]}")

    print(f"Successful : {len(results)} / {len(requests)}")
    if errors:
        print(f"Errors ({len(errors)}) :")
        for err in errors[:15]:
            print(f"  - {err}")

    entry = tracker.record(
        dataset="9.1_timeline_enriched", mode="batch",
        input_tokens=actual_input_total,
        output_tokens=actual_output_total,
    )
    print(f"Actual cost : ${entry.cost_usd:.4f}")
    print(f"Total : ${tracker.total_usd:.4f}")

    output = {
        eid: enriched.model_dump(mode="json")
        for eid, enriched in results.items()
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"OK saved {len(results)} enriched events to {OUTPUT_PATH}")
    print(f"\n{tracker.summary()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
