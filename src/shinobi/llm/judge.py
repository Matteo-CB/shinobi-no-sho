"""LLM-as-judge : valide une narration generee contre les fact sheets.

Second appel LLM court qui repere les nuances que le claim_validator deterministe
ne capte pas (ex: contradiction de personnalite, anachronisme implicite).

Output : JSON {ok: bool, violations: [{type, description, involved_npcs}], summary}.
"""

from __future__ import annotations

from dataclasses import dataclass

from shinobi.errors import LLMSchemaError
from shinobi.llm.client import LLMClient, Message
from shinobi.llm.schema import JUDGE_SCHEMA

JUDGE_SYSTEM_PROMPT = (
    "Tu es un juge canonique strict pour un simulateur de vie dans l'univers de Naruto. "
    "On te donne :\n"
    "1. Des FAITS CANONIQUES NPC (verite absolue : age, statut, relations autorisees/interdites).\n"
    "2. Une narration generee par un autre LLM (texte + observations + dialogues + actions proposees).\n\n"
    "Ta tache : reperer toute violation des faits canoniques. Tu es PARANO et STRICT.\n"
    "Types de violations possibles :\n"
    "- forbidden_relation : interaction sociale entre NPCs explicitement interdite a leur age\n"
    "- non_existent_npc : mention d'un NPC qui n'est pas ne ou deja mort\n"
    "- wrong_age : NPC presente avec un comportement incompatible avec son age canon\n"
    "- wrong_location : NPC localise dans un mauvais village/lieu\n"
    "- anachronism : evenement, technique, organisation pas encore apparu(e)\n"
    "- contradiction_personality : NPC agit contre sa personnalite canonique\n"
    "- other : autre incoherence narrative grave\n\n"
    "Si TOUT est conforme, retourne ok=true et violations=[] (vide).\n"
    "Si tu doutes, prefere flagger en violation pour qu'un humain decide. "
    "Reponds en JSON conforme au schema, en francais.\n\n"
    "/no_think"
)


@dataclass
class JudgeViolation:
    """Une violation reperee par le juge."""

    type: str
    description: str
    involved_npcs: list[str]


@dataclass
class JudgeVerdict:
    """Verdict global du juge."""

    ok: bool
    violations: list[JudgeViolation]
    summary: str | None = None


class CanonJudge:
    """LLM-as-judge qui valide la narration contre les fact sheets injectes."""

    def __init__(self, client: LLMClient) -> None:
        self.client = client

    async def judge(
        self,
        *,
        fact_sheets: str,
        narrative: str,
        observations: list[str],
        npc_dialogue: list[dict],
        proposed_actions: list[dict],
    ) -> JudgeVerdict:
        """Demande au LLM de valider la coherence canonique. Retourne JudgeVerdict."""
        # Compose la narration pour la juger
        sections: list[str] = []
        sections.append("[NARRATIVE A EVALUER]")
        sections.append(narrative)
        if observations:
            sections.append("\n[OBSERVATIONS]")
            for o in observations:
                sections.append(f"- {o}")
        if npc_dialogue:
            sections.append("\n[DIALOGUES NPC]")
            for d in npc_dialogue:
                sections.append(f"- {d.get('character_id', '?')}: {d.get('line', '')}")
        if proposed_actions:
            sections.append("\n[ACTIONS PROPOSEES]")
            for a in proposed_actions:
                sections.append(f"- {a.get('label_fr', '?')}")
        narration_block = "\n".join(sections)

        user_msg = (
            f"{fact_sheets}\n\n{narration_block}\n\n"
            "[INSTRUCTION] Liste toutes les violations des FAITS CANONIQUES NPC dans la "
            "narration ci-dessus. Reponds en JSON conforme au schema."
        )
        try:
            response = await self.client.generate(
                messages=[
                    Message(role="system", content=JUDGE_SYSTEM_PROMPT),
                    Message(role="user", content=user_msg),
                ],
                schema=JUDGE_SCHEMA,
                max_tokens=400,  # courte reponse
            )
        except (LLMSchemaError, Exception):
            # En cas d'echec du juge, on suppose OK (failsafe : ne pas bloquer le jeu)
            return JudgeVerdict(ok=True, violations=[], summary=None)

        if response.parsed_json is None:
            return JudgeVerdict(ok=True, violations=[], summary=None)
        data = response.parsed_json
        raw_violations = data.get("violations", []) or []
        violations: list[JudgeViolation] = []
        for rv in raw_violations:
            try:
                violations.append(
                    JudgeViolation(
                        type=str(rv.get("type", "other")),
                        description=str(rv.get("description", "")),
                        involved_npcs=list(rv.get("involved_npcs", []) or []),
                    )
                )
            except Exception:
                continue
        return JudgeVerdict(
            ok=bool(data.get("ok", True)) and not violations,
            violations=violations,
            summary=data.get("summary"),
        )


def format_judge_violations_for_retry(violations: list[JudgeViolation]) -> str:
    """Formate les violations du juge pour le prompt de retry."""
    if not violations:
        return ""
    lines = ["Le juge canonique a detecte les VIOLATIONS suivantes dans ta narration precedente :"]
    for v in violations:
        npcs = f" (impliques: {', '.join(v.involved_npcs)})" if v.involved_npcs else ""
        lines.append(f"  - [{v.type}] {v.description}{npcs}")
    lines.append(
        "\nReformule la narration en CORRIGEANT chacun de ces points. Respecte STRICTEMENT "
        "les FAITS CANONIQUES NPC fournis. Si une scene devient impossible, narre une "
        "scene solitaire ou avec un PNJ generique (sensei_academie, marchand_taverne, ...) "
        "plutot qu'avec un NPC canon non documente."
    )
    return "\n".join(lines)
