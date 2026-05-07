"""Phase H 9.4 : extraction des moments charnieres canon.

Spec doc 02 §9.4 : evenements dont l'alteration produit cascade massive.
Exemples canon : Massacre Uchiha (year 9), Mort Rin (year 4), Sceau Kyuubi
(year 0), Mort Yondaime (year 0), Pain Invasion (year 16).

Strategy :
- 1 seul appel sync (volume tres faible : 10-30 outputs structures)
- Input : la liste complete des canon timeline_events comme contexte
- Output : DivergencePointsDataset Pydantic
- Coût estime : ~$0.10-0.20 (1 call sync, ~15K input + 5K output)

Output : data/canon/divergence_points.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from phase_h.common import CostTracker, get_anthropic_client  # noqa: E402
from phase_h.schemas import DivergencePointsDataset  # noqa: E402

OUTPUT_PATH = Path(__file__).parent.parent.parent / "data" / "canon" / "divergence_points.json"

SYSTEM_PROMPT = """Tu es l'archiviste narratif canonique de l'univers Naruto.
Ta tache : identifier les MOMENTS CHARNIERES de la timeline canon - les
evenements dont l'alteration produirait une cascade massive sur la suite.

Contraintes :
- Pas de tirets cadratins, pas d'emoji, pas d'argot otaku.
- Reste factuel. Reference les events par leur event_id canon (snake_case).
- 'cascade_severity' : 'fundamental' (univers entier change), 'very_high'
  (arc majeur change), 'high' (consequences notables sur 1-2 arcs).
- 'why_pivotal_fr' : 1 a 2 phrases courtes (50-200 chars), pas de paragraphe.
- 'if_altered_consequences' : 2 a 4 hypotheses CONCISES (chacune <150 chars).
- Cible : 12 a 18 moments charnieres MAJEURS (pas tous les events canon).
- Privilegie les events 'fundamental' et 'very_high' ; 'high' uniquement
  si le moment est exceptionnellement charniere.
- Reponds en JSON STRICT conforme au schema, sans markdown wrap.
"""


def build_user_message(canon_events: list[dict]) -> str:
    """Compose le user message avec contexte canon."""
    lines = [
        "Voici la timeline canon Shinobi no Sho (events deja extraits) :",
        "",
    ]
    for ev in canon_events:
        eid = ev.get("id", "?")
        year = ev.get("year", "?")
        name = ev.get("name_fr") or ev.get("name_romaji") or "(sans nom)"
        narrative = (ev.get("narrative_summary_fr") or "")[:150]
        lines.append(f"- year {year} : {eid} = {name}")
        if narrative:
            lines.append(f"    {narrative}")
    lines.extend([
        "",
        "Identifie 10 a 30 moments charnieres. Output : objet JSON conforme",
        "au schema DivergencePointsDataset (champ 'divergence_points' liste",
        "d'objets DivergencePoint).",
        "",
        "Schema DivergencePoint :",
        '  {"event_id": str, "year": int, "name_fr": str,',
        '   "cascade_severity": "high"|"very_high"|"fundamental",',
        '   "why_pivotal_fr": str (20-500 chars),',
        '   "if_altered_consequences": [str, str, ...] (2-10 entries)}',
        "",
        "Reponds UNIQUEMENT le JSON, pas de prose.",
    ])
    return "\n".join(lines)


def load_canon_events() -> list[dict]:
    """Lit data/canonical/timeline_events.json."""
    canon_path = (
        Path(__file__).parent.parent.parent
        / "data" / "canonical" / "timeline_events.json"
    )
    return json.loads(canon_path.read_text(encoding="utf-8"))


def main() -> int:
    print(">>> Phase H 9.4 : moments charnieres <<<")
    print(f"Output : {OUTPUT_PATH}")

    # 1. Load canon
    events = load_canon_events()
    print(f"Canon timeline_events charges : {len(events)}")

    # 2. Build prompt
    user_msg = build_user_message(events)
    input_estimate_tokens = len(user_msg) // 4 + len(SYSTEM_PROMPT) // 4
    output_estimate_tokens = 8000  # 12-18 entries concises
    print(f"Input estimate : {input_estimate_tokens} tokens")
    print(f"Output estimate : {output_estimate_tokens} tokens")

    # 3. Cost guard pre-call
    tracker = CostTracker.load()
    estimated_cost = tracker.estimate(
        input_tokens=input_estimate_tokens,
        output_tokens=output_estimate_tokens,
        batch=False,
    )
    print(f"Estimated cost (sync) : ${estimated_cost:.4f}")
    if not tracker.can_afford(estimated_cost):
        print(f"REFUSED : would exceed hard budget {tracker.total_usd:.4f}$")
        return 1

    # 4. LLM call (sync, on est petit volume)
    client = get_anthropic_client()
    print("Calling Sonnet 4.6...")
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )

    actual_input = response.usage.input_tokens
    actual_output = response.usage.output_tokens
    text = response.content[0].text.strip()
    print(f"Done. tokens : in={actual_input}, out={actual_output}")

    # 5. Record actual cost
    entry = tracker.record(
        dataset="9.4_divergence_points",
        mode="sync",
        input_tokens=actual_input,
        output_tokens=actual_output,
    )
    print(f"Actual cost : ${entry.cost_usd:.4f}")
    print(f"Total Phase H so far : ${tracker.total_usd:.4f}")

    # 6. Parse + validate
    # Strip markdown fences if present
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
        # Save raw for inspection
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        (OUTPUT_PATH.with_suffix(".raw.txt")).write_text(text, encoding="utf-8")
        print(f"Raw saved to {OUTPUT_PATH.with_suffix('.raw.txt')}")
        return 1

    try:
        dataset = DivergencePointsDataset(**parsed)
    except Exception as e:
        print(f"ERROR : Pydantic validation failed : {e}")
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        (OUTPUT_PATH.with_suffix(".raw.json")).write_text(
            json.dumps(parsed, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"Raw saved to {OUTPUT_PATH.with_suffix('.raw.json')}")
        return 1

    # 7. Save validated output
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        dataset.model_dump_json(indent=2),
        encoding="utf-8",
    )
    print(
        f"OK saved {len(dataset.divergence_points)} divergence points to "
        f"{OUTPUT_PATH}"
    )
    print(f"\n{tracker.summary()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
