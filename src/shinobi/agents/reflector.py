"""Reflector : synthese periodique d'observations en reflections.

docs/02 §6.1 :
> reflect() : tous les N ticks, synthetiser observations en reflexions
>   de plus haut niveau via 1 inference Qwen3-4B

Pattern Generative Agents :
- N derniers obs avec importance > seuil -> 1 prompt LLM
- LLM produit 1-3 reflections (insights de plus haut niveau)
- Chaque reflection cite les obs sources

Comme `selector.py`, on accepte un callable `llm_call` injecte (mockable).
Le reflector a aussi un fallback deterministe : extraction de gist
(premiers mots de chaque obs) sans LLM. Permet la simulation passive
fonctionnelle meme sans modele.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable

from shinobi.logging_setup import get_logger

logger = get_logger(__name__)

from shinobi.agents.cache import LLMCache, compute_cache_key
from shinobi.agents.types import Observation, Reflection
from shinobi.i18n import t

REFLECT_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "reflections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "minLength": 5, "maxLength": 500},
                    "gist": {"type": "string", "maxLength": 100},
                    "importance": {
                        "type": "number", "minimum": 0.0, "maximum": 1.0,
                    },
                    "source_observation_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["text"],
                "additionalProperties": False,
            },
            "minItems": 1,
            "maxItems": 5,
        },
    },
    "required": ["reflections"],
}


def default_reflector_system_prompt() -> str:
    """Resolve le system prompt reflector localise via i18n."""
    return t("agents.reflector.system_prompt")


def __getattr__(name: str) -> str:
    """Compat pour `from shinobi.agents.reflector import REFLECTOR_SYSTEM_PROMPT`."""
    if name == "REFLECTOR_SYSTEM_PROMPT":
        return default_reflector_system_prompt()
    raise AttributeError(name)


# (system, user, schema, model_id, temperature) -> dict | None
LLMCall = Callable[[str, str, dict, str, float], Awaitable[dict | None]]


def build_reflect_prompt(
    npc_id: str, observations: list[Observation], year: int,
) -> str:
    """Compose le user prompt pour la reflection."""
    lines = [
        t("agents.reflector.npc_line", npc_id=npc_id),
        t("agents.reflector.year_line", year=year),
        t("agents.reflector.observations_header"),
    ]
    for o in observations:
        lines.append(f"  ({o.id}) [imp={o.importance:.2f}] {o.text}")
    lines.append("\n" + t("agents.reflector.instruction"))
    return "\n".join(lines)


def deterministic_fallback_reflections(
    npc_id: str, observations: list[Observation], year: int,
) -> list[Reflection]:
    """Fallback deterministe : groupe les obs en 1 reflection synthetique.

    Pas d'IA : juste un resume textuel deterministe pour que la pipeline
    fonctionne en mode CPU-only / offline / sans modele.
    """
    if not observations:
        return []
    # 1 seule reflection : "N obs, themes recurrents : <token1, token2, token3>"
    from collections import Counter

    from shinobi.agents.memory import _tokenize

    tokens: Counter[str] = Counter()
    for o in observations:
        tokens.update(_tokenize(o.text))
    common = [tk for tk, _c in tokens.most_common(5) if len(tk) >= 4]
    gist = (
        t("agents.reflector.gist_with_themes", count=len(observations), themes=", ".join(common))
        if common
        else t("agents.reflector.gist_no_themes", count=len(observations))
    )
    text = t(
        "agents.reflector.synthesis_text",
        count=len(observations),
        year_min=min(o.year for o in observations),
        year_max=max(o.year for o in observations),
        themes=", ".join(common) or t("agents.reflector.themes_default"),
    )
    avg_imp = sum(o.importance for o in observations) / len(observations)
    return [Reflection(
        npc_id=npc_id,
        text=text,
        year=year,
        importance=min(1.0, avg_imp + 0.1),
        gist=gist,
        source_observation_ids=tuple(o.id for o in observations),
    )]


class Reflector:
    """Produit des Reflections a partir d'observations recentes.

    Strategie :
    - Selection des N derniers obs avec importance >= threshold
    - Prompt LLM constrained -> reflections JSON
    - Cache hit possible
    - Fallback deterministe si LLM unavailable
    """

    def __init__(
        self,
        *,
        llm_call: LLMCall | None = None,
        cache: LLMCache | None = None,
        model_id: str = "qwen3-4b",
        temperature: float = 0.5,
        importance_threshold: float = 0.4,
        max_obs: int = 20,
        system_prompt: str | None = None,
    ) -> None:
        self._llm_call = llm_call
        self._cache = cache
        self._model_id = model_id
        self._temperature = temperature
        self._importance_threshold = importance_threshold
        self._max_obs = max_obs
        # None = resoudre le default localise au moment de l'usage.
        self._system_prompt = system_prompt

    @property
    def importance_threshold(self) -> float:
        return self._importance_threshold

    def filter_observations(
        self, observations: Iterable[Observation],
    ) -> list[Observation]:
        """Retient les N derniers obs avec importance >= threshold."""
        keep = [
            o for o in observations
            if o.importance >= self._importance_threshold
        ]
        # Tri par year asc (les plus recents en derniere position pour LLM)
        keep.sort(key=lambda o: (o.year, o.created_at_ts))
        return keep[-self._max_obs:]

    async def reflect(
        self,
        npc_id: str,
        year: int,
        observations: Iterable[Observation],
    ) -> list[Reflection]:
        """Genere des reflections depuis observations. Retourne [] si rien."""
        obs_list = self.filter_observations(observations)
        if not obs_list:
            return []

        user_prompt = build_reflect_prompt(npc_id, obs_list, year)
        system_prompt = self._system_prompt or default_reflector_system_prompt()
        cache_key = compute_cache_key(
            f"{system_prompt}\n###\n{user_prompt}",
            self._model_id,
            self._temperature,
        )

        # Cache hit
        if self._cache is not None:
            cached = self._cache.get(cache_key)
            if cached is not None:
                parsed = self._parse_reflections(cached, npc_id, year, obs_list)
                if parsed:
                    return parsed

        # LLM call
        if self._llm_call is not None:
            try:
                raw = await self._llm_call(
                    system_prompt,
                    user_prompt,
                    REFLECT_JSON_SCHEMA,
                    self._model_id,
                    self._temperature,
                )
                if raw is not None:
                    parsed = self._parse_reflections(raw, npc_id, year, obs_list)
                    if parsed:
                        if self._cache is not None:
                            self._cache.set(
                                cache_key, raw,
                                model_id=self._model_id,
                                temperature=self._temperature,
                                prompt_chars=len(user_prompt),
                            )
                        return parsed
            except Exception as exc:  # noqa: BLE001
                # Audit anti-silent : un bug de signature LLM client ou un
                # parsing JSON casse retombait silencieusement sur le fallback
                # deterministe sans alerte. On log pour visibilite.
                logger.warning(
                    "reflector_llm_call_failed",
                    npc_id=npc_id, year=year,
                    error=type(exc).__name__, msg=str(exc)[:200],
                )

        # Fallback deterministe
        return deterministic_fallback_reflections(npc_id, obs_list, year)

    def _parse_reflections(
        self,
        raw: dict,
        npc_id: str,
        year: int,
        obs_list: list[Observation],
    ) -> list[Reflection]:
        """Parse le JSON LLM en list[Reflection]. [] si invalide."""
        try:
            items = raw.get("reflections") or []
            valid_obs_ids = {o.id for o in obs_list}
            out: list[Reflection] = []
            for it in items:
                if not isinstance(it, dict):
                    continue
                text = (it.get("text") or "").strip()
                if not text:
                    continue
                src_ids = tuple(
                    sid for sid in (it.get("source_observation_ids") or [])
                    if sid in valid_obs_ids
                )
                out.append(Reflection(
                    npc_id=npc_id,
                    text=text,
                    year=year,
                    importance=float(it.get("importance", 0.7)),
                    gist=(it.get("gist") or "")[:100],
                    source_observation_ids=src_ids,
                ))
            return out
        except (ValueError, TypeError):
            return []


__all__ = [
    "REFLECTOR_SYSTEM_PROMPT",
    "REFLECT_JSON_SCHEMA",
    "Reflector",
    "build_reflect_prompt",
    "deterministic_fallback_reflections",
]
