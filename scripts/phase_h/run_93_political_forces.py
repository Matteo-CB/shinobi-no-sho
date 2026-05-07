"""Phase H 9.3 : forces politiques (factions, leaders, alliances).

Spec doc 02 §9.3 : cartographie qui permet au tension detector et au
Director d'identifier les configurations geopolitiques instables.

Strategy : 1 sync call, contexte = villages + clans + organizations
canon. Output : ~30-50 factions structurees.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from phase_h.common import CostTracker, get_anthropic_client  # noqa: E402
from phase_h.schemas import PoliticalForcesDataset  # noqa: E402

OUTPUT_PATH = Path(__file__).parent.parent.parent / "data" / "canon" / "political_forces.json"
CANONICAL_PATH = Path(__file__).parent.parent.parent / "data" / "canonical"

SYSTEM_PROMPT = """Tu es l'archiviste politique de l'univers Naruto.
Ta tache : cartographier les FORCES POLITIQUES canon (villages caches,
clans majeurs, organisations, factions) avec leurs alliances et tensions.

Contraintes :
- Pas de tirets cadratins, pas d'emoji, pas d'argot otaku.
- 'type' : 'village' | 'clan' | 'organization' | 'country' | 'guild' | 'rebellion'.
- 'description_fr' : 1-3 phrases (20-400 chars).
- 'leader_id' : reference au character_id canon (snake_case) ou null.
- 'members' / 'allies' / 'enemies' : referencent character_id ou faction_id.
- Cible : 25 a 50 factions, focus sur celles ayant role politique notable.
- 'active_year_start' / 'active_year_end' : annees in-game (year=0 = naissance Naruto).
- Reponds en JSON STRICT, sans markdown wrap.
"""


def load_canon_lists() -> tuple[list[dict], list[dict], list[dict]]:
    villages = json.loads(
        (CANONICAL_PATH / "villages.json").read_text(encoding="utf-8"),
    )
    clans = json.loads(
        (CANONICAL_PATH / "clans.json").read_text(encoding="utf-8"),
    )
    orgs = json.loads(
        (CANONICAL_PATH / "organizations.json").read_text(encoding="utf-8"),
    )
    return villages, clans, orgs


def build_user_message(
    villages: list[dict], clans: list[dict], orgs: list[dict],
) -> str:
    lines = [
        "Voici les ENTITES politiques canon Shinobi no Sho :",
        "",
        f"== VILLAGES ({len(villages)}) ==",
    ]
    for v in villages:
        vid = v.get("id", "?")
        name = v.get("name_fr") or v.get("name_romaji") or "?"
        country = v.get("country", "")
        lines.append(f"  - {vid} : {name} ({country})")

    lines.append("")
    lines.append(f"== CLANS ({len(clans)}) ==")
    for c in clans[:80]:  # cap pour eviter prompt bloat
        cid = c.get("id", "?")
        name = c.get("name_fr") or c.get("name_romaji") or "?"
        village = c.get("village_of_origin", "")
        lines.append(f"  - {cid} : {name} (origine: {village})")

    lines.append("")
    lines.append(f"== ORGANIZATIONS ({len(orgs)}) ==")
    for o in orgs:
        oid = o.get("id", "?")
        name = o.get("name_fr") or o.get("name_romaji") or "?"
        descr = (o.get("description_fr") or "")[:100]
        lines.append(f"  - {oid} : {name} - {descr}")

    lines.extend([
        "",
        "Cartographie 25-50 factions politiques canon. Output JSON :",
        "",
        '  {"factions": [',
        '    {"id": "konohagakure", "name_fr": "Konoha", "type": "village",',
        '     "leader_id": "senju_hashirama", "members": [...], "allies": [...],',
        '     "enemies": [...], "active_year_start": -65, "active_year_end": null,',
        '     "description_fr": "Le Village Cache de la Feuille..."}, ...]}',
        "",
        "Reponds UNIQUEMENT le JSON.",
    ])
    return "\n".join(lines)


def main() -> int:
    print(">>> Phase H 9.3 : forces politiques <<<")
    villages, clans, orgs = load_canon_lists()
    user_msg = build_user_message(villages, clans, orgs)

    input_estimate = len(user_msg) // 4 + len(SYSTEM_PROMPT) // 4
    output_estimate = 12000  # 25-50 factions
    print(f"Input estimate : {input_estimate} tokens")

    tracker = CostTracker.load()
    estimated_cost = tracker.estimate(
        input_tokens=input_estimate, output_tokens=output_estimate, batch=False,
    )
    print(f"Estimated cost (sync) : ${estimated_cost:.4f}")
    if not tracker.can_afford(estimated_cost):
        print("REFUSED : would exceed hard budget")
        return 1

    client = get_anthropic_client()
    print("Calling Sonnet 4.6...")
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=20000,  # cap haut, factions peuvent etre verbeux
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )

    actual_input = response.usage.input_tokens
    actual_output = response.usage.output_tokens
    text = response.content[0].text.strip()
    print(f"tokens : in={actual_input}, out={actual_output}")

    entry = tracker.record(
        dataset="9.3_political_forces", mode="sync",
        input_tokens=actual_input, output_tokens=actual_output,
    )
    print(f"Actual cost : ${entry.cost_usd:.4f}")
    print(f"Total : ${tracker.total_usd:.4f}")

    # Strip markdown wrap if any
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
        if text.endswith("```"):
            text = text[:-3].strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"ERROR : JSON parse failed : {e}")
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        (OUTPUT_PATH.with_suffix(".raw.txt")).write_text(text, encoding="utf-8")
        return 1

    try:
        dataset = PoliticalForcesDataset(**parsed)
    except Exception as e:
        print(f"ERROR : Pydantic validation failed : {e}")
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        (OUTPUT_PATH.with_suffix(".raw.json")).write_text(
            json.dumps(parsed, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return 1

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(dataset.model_dump_json(indent=2), encoding="utf-8")
    print(f"OK saved {len(dataset.factions)} factions to {OUTPUT_PATH}")
    print(f"\n{tracker.summary()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
