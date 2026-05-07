"""Phase H 9.5 : patterns d'ecriture Kishimoto.

Spec doc 02 §9.5 : style guide pour le Director / narrator LLM.
Pas pour copier - pour imiter le ton et les ressorts narratifs.

Strategy : 1 sync call analytique. Output : ~8-15 patterns structures.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from phase_h.common import CostTracker, get_anthropic_client  # noqa: E402
from phase_h.schemas import KishimotoPatternsDataset  # noqa: E402

OUTPUT_PATH = Path(__file__).parent.parent.parent / "data" / "canon" / "narrative_patterns.json"

SYSTEM_PROMPT = """Tu es un analyste litteraire specialise dans Naruto de
Masashi Kishimoto. Ta tache : extraire les PATTERNS D'ECRITURE recurrents
de l'oeuvre canon - revelations en couches, trahisons preparees longtemps
en germe, redemptions par les liens humains, etc.

Contraintes :
- Pas de tirets cadratins, pas d'emoji, pas d'argot otaku.
- 'description_fr' : 50-500 chars, explique le mecanisme narratif.
- 'canon_examples' : 2-6 references concretes (event, arc, moment).
- 'when_to_apply_fr' : 1-2 phrases sur quand un narrator/Director devrait
  utiliser ce pattern (50-250 chars).
- Cible : 8 a 15 patterns, focus sur ceux verifiables dans le canon.
- Reponds en JSON STRICT, sans markdown wrap.
"""


def build_user_message() -> str:
    return """Analyse les patterns d'ecriture recurrents dans le canon Naruto.

Identifie 8 a 15 motifs structurants utilises par Kishimoto. Quelques
exemples (a etoffer/preciser) :

- Revelations en couches successives (Itachi, Tobi/Obito, Madara/Hashirama)
- Trahisons preparees longtemps en germe (Itachi, Sasuke, Tobi)
- Redemption par dialogue de bataille (Gaara, Nagato, Obito, Sasuke)
- Power-up via tragedy (Sharingan, Mangekyo, Six Paths)
- Mentor mort qui revient en flashback (Hiruzen, Jiraiya, Itachi)
- Cycle de haine clan vs clan (Senju-Uchiha)
- ...

Pour chaque pattern : titre court, description du mecanisme (50-500 chars),
2-6 exemples canon, conseil d'usage pour un narrateur (50-250 chars).

Format JSON :
  {"patterns": [
    {"id": "snake_case_id", "title_fr": "Titre", "description_fr": "...",
     "canon_examples": ["Naruto rallie Gaara par...", ...],
     "when_to_apply_fr": "Quand le narrator..."}, ...]}

Reponds UNIQUEMENT le JSON."""


def main() -> int:
    print(">>> Phase H 9.5 : patterns Kishimoto <<<")
    user_msg = build_user_message()
    input_estimate = len(user_msg) // 4 + len(SYSTEM_PROMPT) // 4
    output_estimate = 6000

    tracker = CostTracker.load()
    estimated_cost = tracker.estimate(
        input_tokens=input_estimate, output_tokens=output_estimate, batch=False,
    )
    print(f"Estimated cost (sync) : ${estimated_cost:.4f}")
    if not tracker.can_afford(estimated_cost):
        print("REFUSED")
        return 1

    client = get_anthropic_client()
    print("Calling Sonnet 4.6...")
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=12000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )

    text = response.content[0].text.strip()
    print(f"tokens : in={response.usage.input_tokens}, out={response.usage.output_tokens}")

    entry = tracker.record(
        dataset="9.5_narrative_patterns", mode="sync",
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
    )
    print(f"Actual cost : ${entry.cost_usd:.4f}")
    print(f"Total : ${tracker.total_usd:.4f}")

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
        print(f"ERROR : {e}")
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        (OUTPUT_PATH.with_suffix(".raw.txt")).write_text(text, encoding="utf-8")
        return 1

    try:
        dataset = KishimotoPatternsDataset(**parsed)
    except Exception as e:
        print(f"ERROR Pydantic : {e}")
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        (OUTPUT_PATH.with_suffix(".raw.json")).write_text(
            json.dumps(parsed, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return 1

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(dataset.model_dump_json(indent=2), encoding="utf-8")
    print(f"OK saved {len(dataset.patterns)} patterns to {OUTPUT_PATH}")
    print(f"\n{tracker.summary()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
