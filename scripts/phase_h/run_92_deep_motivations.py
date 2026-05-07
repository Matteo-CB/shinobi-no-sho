"""Phase H 9.2 : motivations profondes top-50 PNJ via Batch API (50% off).

Spec doc 02 §9.2 : profil psycho profond pour Phase E agents et Phase D
drift.

Strategy :
1. Selection des top-50 PNJ par "narrative weight" (presence dans canon
   timeline_events + voice_profile + canonicity).
2. Pour chaque PNJ : 1 batch request avec son contexte canon (wiki section,
   stats, clan, role).
3. Submit batch -> poll jusqu'a end -> fetch results.
4. Validate via Pydantic CharacterDeepProfile par PNJ.
5. Persist data/canon/deep_motivations.json (dict {char_id: profile}).

Coût estime : 50 calls × ~6K input + ~1.5K output (batch 50% off)
  = 300K input × $1.5/M + 75K output × $7.5/M = $0.45 + $0.56 = ~$1.01
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from phase_h.common import CostTracker, get_anthropic_client  # noqa: E402
from phase_h.schemas import CharacterDeepProfile  # noqa: E402

OUTPUT_PATH = (
    Path(__file__).parent.parent.parent / "data" / "canon" / "deep_motivations.json"
)
CANONICAL_PATH = Path(__file__).parent.parent.parent / "data" / "canonical"

SYSTEM_PROMPT = """Tu es psycho-narratif specialise dans Naruto.
Ta tache : extraire le PROFIL PSYCHOLOGIQUE PROFOND d'un personnage canon
en analysant son arc, ses actes, ses relations.

Champs requis :
- 'deep_motivations.primary' : motivation centrale qui explique 80% des actes.
  Exemple : 'protect_konoha_at_any_cost', 'become_strongest_to_prove_existence'.
- 'deep_motivations.secondary' / 'tertiary' : motivations sous-jacentes (optionnel).
- 'moral_red_lines' : 0-5 actes que ce perso REFUSERAIT meme sous contrainte.
- 'secret_ambitions' : 0-3 desirs caches non avoues publiquement.
- 'deepest_fear' : la peur centrale (5-200 chars). Concrete, pas abstraite.
- 'self_image' : comment le perso se voit (3-100 chars).
- 'what_others_dont_know' : 0-5 verites sur lui que personne ne soupconne.

Contraintes :
- Pas de tirets cadratins, pas d'emoji.
- Reponse en francais, mais identifiers en snake_case anglais.
- Reste fidele au canon. Pas d'invention.
- Reponds JSON STRICT, sans markdown wrap.
"""


def _select_top_50_chars(canon_chars: dict, canon_events: list[dict]) -> list[str]:
    """Selectionne les 50 PNJ avec le plus de narrative weight.

    Heuristique :
    - +5 pour chaque mention dans timeline_events.involved_characters
    - +3 si voice_profile_id non-null
    - +2 si death_year non-null (perso important narrativement)
    - +5 si clan dans (uchiha, senju, hyuga, uzumaki, otsutsuki, hozuki, sarutobi)
    - +1 par technique canonical_users (proxy pour importance technique)
    """
    notable_clans = {
        "uchiha", "senju", "hyuga", "uzumaki", "otsutsuki",
        "hozuki", "sarutobi", "akimichi", "nara", "yamanaka",
        "aburame", "inuzuka", "kaguya", "namikaze",
    }
    scores: dict[str, float] = {}

    # Score from timeline events
    for ev in canon_events:
        for cid in ev.get("involved_characters", []) or []:
            scores[cid] = scores.get(cid, 0) + 5.0

    # Score from char traits
    for cid, char in canon_chars.items():
        score = scores.get(cid, 0.0)
        if char.get("voice_profile_id"):
            score += 3.0
        if char.get("death_year") is not None:
            score += 2.0
        if char.get("clan") in notable_clans:
            score += 5.0
        scores[cid] = score

    # Sort, return top 50
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    return [cid for cid, _ in ranked[:50]]


def _build_char_prompt(char: dict) -> str:
    """Compose le user message pour 1 PNJ."""
    cid = char.get("id", "?")
    name = char.get("name_fr") or char.get("name_romaji") or "?"
    clan = char.get("clan", "?")
    village = char.get("village_of_origin", "?")
    birth = char.get("birth_year", "?")
    death = char.get("death_year", "vivant ou inconnu")
    personality = (char.get("personality_fr") or "")[:1500]
    aliases = ", ".join(char.get("aliases", [])[:5])

    parts = [
        f"Personnage canon : {cid}",
        f"Nom : {name}",
        f"Aliases : {aliases or '(aucun)'}",
        f"Clan : {clan}",
        f"Village d'origine : {village}",
        f"Naissance / mort canon : year {birth} / {death}",
        "",
        f"Personnalite (extrait) :",
        f"{personality}" if personality else "(profil personnalite non extrait)",
        "",
        "Produis le profil deep_motivations JSON conforme au schema, en respectant",
        "la psychologie canon de ce personnage. Pas d'invention.",
        "",
        "Format JSON :",
        '  {"id": "' + cid + '",',
        '   "deep_motivations": {"primary": "...", "secondary": null, "tertiary": null},',
        '   "moral_red_lines": [...], "secret_ambitions": [...],',
        '   "deepest_fear": "...", "self_image": "...",',
        '   "what_others_dont_know": [...]}',
        "",
        "Reponds UNIQUEMENT le JSON.",
    ]
    return "\n".join(parts)


def _wait_for_batch(client, batch_id: str) -> None:
    """Poll jusqu'a ce que le batch soit ended (max 1h, fail si timeout)."""
    print(f"Polling batch {batch_id}...")
    start = time.time()
    while time.time() - start < 3600:  # 1h max
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
        time.sleep(15)
    raise TimeoutError(f"Batch {batch_id} did not complete within 1h")


def main() -> int:
    print(">>> Phase H 9.2 : motivations top-50 PNJ (Batch API) <<<")

    # 1. Load canon characters + events
    chars_raw = json.loads(
        (CANONICAL_PATH / "characters.json").read_text(encoding="utf-8"),
    )
    if isinstance(chars_raw, dict):
        chars_dict = chars_raw
    else:
        chars_dict = {c.get("id"): c for c in chars_raw if c.get("id")}
    print(f"Canon characters loaded : {len(chars_dict)}")

    events = json.loads(
        (CANONICAL_PATH / "timeline_events.json").read_text(encoding="utf-8"),
    )

    # 2. Select top-50
    top_50_ids = _select_top_50_chars(chars_dict, events)
    print(f"Top-50 selected : {top_50_ids[:10]} ... {top_50_ids[-3:]}")

    # 3. Build batch requests
    requests = []
    for cid in top_50_ids:
        char = chars_dict.get(cid)
        if not char:
            print(f"  WARN : {cid} not in canon dict, skipping")
            continue
        user_msg = _build_char_prompt(char)
        requests.append({
            "custom_id": cid,
            "params": {
                "model": "claude-sonnet-4-6",
                "max_tokens": 1500,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": user_msg}],
            },
        })
    print(f"Batch requests prepared : {len(requests)}")

    # 4. Cost guard estimate
    avg_input = sum(len(r["params"]["messages"][0]["content"]) for r in requests) // 4 // len(requests)
    total_input_estimate = avg_input * len(requests) + (len(SYSTEM_PROMPT) // 4) * len(requests)
    total_output_estimate = 1200 * len(requests)
    tracker = CostTracker.load()
    estimated_cost = tracker.estimate(
        input_tokens=total_input_estimate,
        output_tokens=total_output_estimate,
        batch=True,
    )
    print(f"Estimated cost (batch) : ${estimated_cost:.4f}")
    if not tracker.can_afford(estimated_cost):
        print("REFUSED")
        return 1

    # 5. Submit batch
    client = get_anthropic_client()
    print("Submitting batch to Anthropic...")
    batch = client.messages.batches.create(requests=requests)
    print(f"Batch id : {batch.id}, status : {batch.processing_status}")

    # 6. Poll
    _wait_for_batch(client, batch.id)

    # 7. Fetch results
    print("Fetching results...")
    results: dict[str, CharacterDeepProfile] = {}
    actual_input_total = 0
    actual_output_total = 0
    errors: list[str] = []

    for entry in client.messages.batches.results(batch.id):
        cid = entry.custom_id
        if entry.result.type != "succeeded":
            errors.append(f"{cid} : {entry.result.type}")
            continue
        msg = entry.result.message
        if hasattr(msg, "usage"):
            actual_input_total += msg.usage.input_tokens
            actual_output_total += msg.usage.output_tokens
        text = msg.content[0].text.strip()
        # strip markdown wrap
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
            errors.append(f"{cid} : parse/validate fail : {str(e)[:100]}")

    print(f"Successful profiles : {len(results)} / {len(requests)}")
    if errors:
        print(f"Errors ({len(errors)}) :")
        for err in errors[:10]:
            print(f"  - {err}")

    # 8. Record actual cost
    entry = tracker.record(
        dataset="9.2_deep_motivations", mode="batch",
        input_tokens=actual_input_total, output_tokens=actual_output_total,
    )
    print(f"Actual cost : ${entry.cost_usd:.4f}")
    print(f"Total : ${tracker.total_usd:.4f}")

    # 9. Save
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
    print(f"\n{tracker.summary()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
