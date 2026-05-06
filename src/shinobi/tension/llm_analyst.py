"""LLM Tension Analyst (Phase C, doc 02 §5.3.B).

Tous les ~3 mois in-game (= 30-90 turns selon rythme), un Qwen3-4B local
recoit un snapshot synthetique du KG et identifie :
- Les fils narratifs en suspens (Chekhov's gun introduits sans payoff)
- Les configurations qui appellent une reponse
- Les anniversaires d'evenements

Le LLM analyste **ne genere PAS d'evenements**. Il identifie une opportunite
dramatique (= Tension), que la couche Director (Phase G) decide d'exploiter
et la couche multi-agent (Phase E) incarne.

Latence estimee : 1 inference Qwen3-4B / 30-90 turns. Couts negligeable.

Le LLM peut ne pas etre disponible (mode offline / serveur down). Dans ce
cas, l'analyst retourne TensionList vide sans planter le pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

from shinobi.errors import LLMSchemaError, LLMUnavailableError
from shinobi.kg.store import KnowledgeGraphStore
from shinobi.logging_setup import get_logger
from shinobi.tension.types import Tension, TensionList, TensionSeverity, TensionType

logger = get_logger(__name__)


@dataclass(frozen=True)
class LLMAnalystConfig:
    """Parametres du LLM analyste."""

    interval_months_in_game: int = 3
    snapshot_top_npcs: int = 50
    snapshot_recent_events: int = 20
    max_tensions_per_call: int = 8


# Schema JSON Pydantic strict pour parser la sortie LLM
class _LLMTension(BaseModel):
    """Une tension detectee par le LLM, pre-validation Pydantic."""

    type: str = Field(..., min_length=3)
    description: str = Field(..., min_length=10)
    severity: str = Field(default="medium")
    involved_entities: list[str] = Field(default_factory=list)
    notes: str | None = None
    suggested_resolution_hint: str | None = None


class _LLMAnalystOutput(BaseModel):
    """Sortie attendue du LLM analyste."""

    tensions: list[_LLMTension] = Field(default_factory=list)
    summary: str | None = None


# Schema JSON pour structured output
TENSION_ANALYST_SCHEMA: dict = {
    "type": "object",
    "required": ["tensions"],
    "additionalProperties": False,
    "properties": {
        "tensions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["type", "description"],
                "additionalProperties": False,
                "properties": {
                    "type": {"type": "string"},
                    "description": {"type": "string"},
                    "severity": {
                        "enum": ["low", "medium", "high", "critical"],
                    },
                    "involved_entities": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "notes": {"type": "string"},
                    "suggested_resolution_hint": {"type": "string"},
                },
            },
        },
        "summary": {"type": "string"},
    },
}


TENSION_ANALYST_SYSTEM_PROMPT = (
    "Tu es un analyste narratif specialise dans l'univers Naruto. On te "
    "donne un snapshot synthetique du monde simule a une annee donnee : "
    "top PNJ avec leurs etats, relations, evenements recents, secrets "
    "connus, alliances, conflits.\n\n"
    "Ta tache : identifier les OPPORTUNITES DRAMATIQUES emergentes. "
    "Tu N'INVENTES PAS d'evenements. Tu signales des configurations "
    "narrativement chargees.\n\n"
    "Categories possibles :\n"
    " - power_vacuum : leadership absent\n"
    " - factional_revenge : faction lesee non vengee\n"
    " - bloodline_unresolved : lien de sang en suspens\n"
    " - obsessive_npc_idle : perso obsede mais passif\n"
    " - jinchuuriki_unprotected : hote vulnerable\n"
    " - tailed_beast_uncontrolled : bijuu libre\n"
    " - hidden_truth_pending : secret en risque de fuite\n"
    " - chekhovs_gun_unfired : element introduit sans payoff\n"
    " - prophecy_unfulfilled : prophetie en attente\n"
    " - death_anniversary : commemoration narrativement chargee\n"
    " - cursed_hatred : haine cumulative\n"
    " - alliance_breakdown : alliance fragile\n"
    " - succession_dispute : qui prend la suite\n"
    " - student_surpasses_master : eleve depasse maitre\n"
    " - lone_survivor_obsessed : seul survivant obsede\n"
    " - clan_extinction_threat : clan menace\n"
    " - kekkei_carrier_isolated : dernier porteur kekkei\n"
    " - forbidden_jutsu_threat : kinjutsu en circulation\n"
    " - border_conflict : conflit frontalier\n"
    " - canon_event_pending : event canon a echeance\n"
    " - other : autre fil narratif\n\n"
    "Severity : low (signal faible) | medium (interessant) | high (potent) "
    "| critical (explosif).\n\n"
    "Reponds en JSON conforme au schema. PAS de tirets cadratins, PAS d'emoji."
)


def _safe_tension_type(raw: str) -> TensionType:
    """Convertit un type LLM (string) en TensionType valide. Fallback 'other'."""
    try:
        return TensionType(raw)
    except ValueError:
        return TensionType.other


def _safe_severity(raw: str) -> TensionSeverity:
    try:
        return TensionSeverity(raw.lower())
    except (ValueError, AttributeError):
        return TensionSeverity.medium


class SnapshotBuilder:
    """Construit un snapshot synthetique du KG pour le LLM.

    Format compact (texte structure) : top NPCs vivants, relations clefs,
    events recents, secrets en circulation. Le LLM consommera ce snapshot
    en input.
    """

    def __init__(
        self,
        store: KnowledgeGraphStore,
        *,
        config: LLMAnalystConfig | None = None,
    ) -> None:
        self._store = store
        self._config = config or LLMAnalystConfig()

    def build(self, year: int) -> str:
        """Genere un snapshot textuel du monde a year."""
        sections: list[str] = [f"# Snapshot du monde - an {year}", ""]

        # Top NPCs vivants : ceux qui ont le plus de facts (proxy d'importance)
        top = self._top_npcs_by_fact_count(year)
        if top:
            sections.append("## Top PNJ vivants")
            for npc, count in top[: self._config.snapshot_top_npcs]:
                sections.append(self._npc_summary(npc, year, count))
            sections.append("")

        # Events recents
        events = self._recent_events(year, window=10)
        if events:
            sections.append("## Evenements recents (10 dernieres annees)")
            for ev in events[: self._config.snapshot_recent_events]:
                sections.append(f"- an {ev[0]}: {ev[1]} ({ev[2]})")
            sections.append("")

        # Secrets en circulation
        secrets = self._store.get_facts(relation="hidden_secret")
        if secrets:
            sections.append("## Secrets connus de plusieurs NPCs")
            for f in secrets[:5]:
                if len(f.known_by_npc_ids) >= 2:
                    sections.append(
                        f"- '{f.object}' connu de {len(f.known_by_npc_ids)} NPCs"
                    )
            sections.append("")

        # Anniversaires d'events de la decennie
        anniversaries = self._notable_anniversaries(year)
        if anniversaries:
            sections.append("## Anniversaires majeurs cette annee")
            for ann in anniversaries:
                sections.append(f"- {ann}")
            sections.append("")

        return "\n".join(sections).strip()

    def _top_npcs_by_fact_count(
        self, year: int,
    ) -> list[tuple[str, int]]:
        """NPCs ordonnes par nombre de facts dont ils sont sujet (proxy)."""
        facts = self._store.get_facts(relation="type", object_value="character")
        ids = [f.subject for f in facts]
        counts: list[tuple[str, int]] = []
        for cid in ids:
            n = len(self._store.get_facts(subject=cid))
            counts.append((cid, n))
        counts.sort(key=lambda x: -x[1])
        return counts

    def _npc_summary(self, npc_id: str, year: int, fact_count: int) -> str:
        """Une ligne resume pour un NPC."""
        clan_f = self._store.get_facts(subject=npc_id, relation="clan", limit=1)
        clan = clan_f[0].object if clan_f else "?"
        rank_f = self._store.get_facts(subject=npc_id, relation="rank", year=year, limit=1)
        rank = rank_f[0].object if rank_f else "?"
        village_f = self._store.get_facts(subject=npc_id, relation="village_of_origin", limit=1)
        village = village_f[0].object if village_f else "?"
        return (
            f"- {npc_id} (clan={clan}, rang={rank}, village={village}, "
            f"facts={fact_count})"
        )

    def _recent_events(
        self, year: int, *, window: int = 10,
    ) -> list[tuple[int, str, str]]:
        """Liste des events dans (year - window, year]."""
        events = self._store.get_facts(relation="occurs_in_year")
        out: list[tuple[int, str, str]] = []
        for f in events:
            try:
                y = int(f.object or "0")
            except (TypeError, ValueError):
                continue
            if year - window < y <= year:
                # Recupere le name_fr du subject (event id)
                name_f = self._store.get_facts(
                    subject=f.subject, relation="name_fr", limit=1,
                )
                name = name_f[0].object if name_f else f.subject
                out.append((y, name, f.subject))
        out.sort(key=lambda x: -x[0])
        return out

    def _notable_anniversaries(self, year: int) -> list[str]:
        deaths = self._store.get_facts(relation="death_year")
        out: list[str] = []
        for f in deaths:
            try:
                dy = int(f.object or "0")
            except (TypeError, ValueError):
                continue
            delta = year - dy
            if delta in (5, 10, 20, 50):
                out.append(f"{delta}e anniversaire mort de {f.subject} (an {dy})")
        return out


class LLMTensionAnalyst:
    """LLM analyste qui detecte des opportunites narratives via un snapshot KG."""

    def __init__(
        self,
        store: KnowledgeGraphStore,
        llm_client=None,
        *,
        config: LLMAnalystConfig | None = None,
    ) -> None:
        self._store = store
        self._client = llm_client  # peut etre None pour mode offline
        self._config = config or LLMAnalystConfig()
        self._snapshot_builder = SnapshotBuilder(store, config=self._config)

    @property
    def config(self) -> LLMAnalystConfig:
        return self._config

    async def analyze(self, year: int) -> TensionList:
        """Lance l'analyse. Retourne TensionList vide si LLM indisponible."""
        if self._client is None:
            logger.info("tension_llm_analyst_skipped", reason="no_client")
            return TensionList(detected_at_year=year)

        snapshot = self._snapshot_builder.build(year)
        try:
            response = await self._invoke_llm(snapshot, year)
        except (LLMUnavailableError, LLMSchemaError) as exc:
            logger.warning(
                "tension_llm_analyst_failed",
                error=type(exc).__name__, msg=str(exc)[:200],
            )
            return TensionList(detected_at_year=year)
        except Exception as exc:  # pragma: no cover
            logger.warning("tension_llm_analyst_unexpected", error=str(exc)[:200])
            return TensionList(detected_at_year=year)

        return self._parse_response(response, year)

    async def _invoke_llm(self, snapshot: str, year: int):
        """Effectue l'appel LLM avec schema strict."""
        from shinobi.llm.client import Message  # import lazy

        messages = [
            Message(role="system", content=TENSION_ANALYST_SYSTEM_PROMPT),
            Message(role="user", content=(
                f"{snapshot}\n\n"
                f"[INSTRUCTION]\n"
                f"Analyse ce snapshot et identifie au plus "
                f"{self._config.max_tensions_per_call} opportunites "
                f"dramatiques. Reponds en JSON conforme au schema."
            )),
        ]
        response = await self._client.generate(
            messages=messages,
            schema=TENSION_ANALYST_SCHEMA,
            max_tokens=600,
        )
        return response

    def _parse_response(self, response, year: int) -> TensionList:
        """Convertit la sortie LLM en TensionList. Tolere les imperfections."""
        if response is None or response.parsed_json is None:
            return TensionList(detected_at_year=year)
        data = response.parsed_json
        try:
            parsed = _LLMAnalystOutput.model_validate(data)
        except Exception as exc:
            logger.warning("tension_llm_parse_failed", error=str(exc)[:200])
            return TensionList(detected_at_year=year)

        tensions: list[Tension] = []
        for raw in parsed.tensions:
            ttype = _safe_tension_type(raw.type)
            severity = _safe_severity(raw.severity)
            tensions.append(Tension.from_severity(
                type=ttype,
                description=raw.description,
                severity=severity,
                involved_entities=raw.involved_entities,
                source_rule="llm_analyst",
                detected_at_year=year,
                notes=raw.notes,
                suggested_resolution_hint=raw.suggested_resolution_hint,
            ))
        return TensionList(tensions=tensions, detected_at_year=year)

    def build_snapshot(self, year: int) -> str:
        """Helper public : compose un snapshot sans appeler le LLM (pour tests/debug)."""
        return self._snapshot_builder.build(year)


__all__ = [
    "TENSION_ANALYST_SCHEMA",
    "TENSION_ANALYST_SYSTEM_PROMPT",
    "LLMAnalystConfig",
    "LLMTensionAnalyst",
    "SnapshotBuilder",
]
