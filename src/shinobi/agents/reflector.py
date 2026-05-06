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

from shinobi.agents.cache import LLMCache, compute_cache_key
from shinobi.agents.types import Observation, Reflection

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


REFLECTOR_SYSTEM_PROMPT = """Tu es un agent narratif Naruto. On te montre N observations recentes
d'un PNJ. Distille-les en 1 a 3 reflections de plus haut niveau (insights, patterns, conclusions).
Chaque reflection cite les obs sources par id. Reponse JSON conforme au schema. Pas de markdown."""


# (system, user, schema, model_id, temperature) -> dict | None
LLMCall = Callable[[str, str, dict, str, float], Awaitable[dict | None]]


def build_reflect_prompt(
    npc_id: str, observations: list[Observation], year: int,
) -> str:
    """Compose le user prompt pour la reflection."""
    lines = [
        f"[NPC] {npc_id}", f"[ANNEE] {year}",
        "[OBSERVATIONS RECENTES]",
    ]
    for o in observations:
        lines.append(f"  ({o.id}) [imp={o.importance:.2f}] {o.text}")
    lines.append(
        "\n[INSTRUCTION] Distille en 1-3 reflections (text, gist, importance, "
        "source_observation_ids). JSON only.",
    )
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
    common = [t for t, _c in tokens.most_common(5) if len(t) >= 4]
    gist = (
        f"{len(observations)} obs recentes : themes {', '.join(common)}"
        if common
        else f"{len(observations)} obs recentes"
    )
    text = (
        f"Synthese auto : {len(observations)} observations entre an "
        f"{min(o.year for o in observations)} et {max(o.year for o in observations)}. "
        f"Themes : {', '.join(common) or 'divers'}."
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
        system_prompt: str = REFLECTOR_SYSTEM_PROMPT,
    ) -> None:
        self._llm_call = llm_call
        self._cache = cache
        self._model_id = model_id
        self._temperature = temperature
        self._importance_threshold = importance_threshold
        self._max_obs = max_obs
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
        cache_key = compute_cache_key(
            f"{self._system_prompt}\n###\n{user_prompt}",
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
                    self._system_prompt,
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
            except Exception:
                pass

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
