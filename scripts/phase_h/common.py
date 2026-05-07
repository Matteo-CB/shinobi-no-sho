"""Common utilities Phase H : env loader, cost tracker, Anthropic client.

Hard budget cap a $25 (sur les $30 disponibles, 5$ buffer pour erreurs).
Si le total cumule depasse $25, raise PermissionError - aucune nouvelle
requete n'est envoyee.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

# Tarifs Sonnet 4.6 batch API ($/M tokens). Cf docs.anthropic.com/pricing.
SONNET_4_6_BATCH_INPUT_PER_M: float = 1.50
SONNET_4_6_BATCH_OUTPUT_PER_M: float = 7.50
SONNET_4_6_SYNC_INPUT_PER_M: float = 3.00
SONNET_4_6_SYNC_OUTPUT_PER_M: float = 15.00

# Budget global Phase H. Hard cap a $25, $30 disponibles -> $5 buffer.
HARD_BUDGET_USD: float = 25.00

# Cost tracker file persiste entre scripts.
COST_TRACKER_FILE: Path = Path(__file__).parent.parent.parent / "data" / "phase_h_cost.json"


def load_env() -> str:
    """Lit .env, retourne API_CLAUDE_KEY. Set ANTHROPIC_API_KEY pour le SDK."""
    env_path = Path(__file__).parent.parent.parent / ".env"
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("API_CLAUDE_KEY="):
            key = line.split("=", 1)[1].strip().strip('"').strip("'")
            os.environ["ANTHROPIC_API_KEY"] = key
            return key
    raise RuntimeError("API_CLAUDE_KEY pas trouvee dans .env")


@dataclass
class CostEntry:
    """Une depense LLM enregistree."""

    timestamp: float
    dataset: str
    mode: str  # 'batch' ou 'sync'
    input_tokens: int
    output_tokens: int
    cost_usd: float


@dataclass
class CostTracker:
    """Tracker persistent du coût Phase H. Hard cap a HARD_BUDGET_USD."""

    entries: list[CostEntry] = field(default_factory=list)
    total_usd: float = 0.0

    @classmethod
    def load(cls) -> CostTracker:
        if not COST_TRACKER_FILE.exists():
            COST_TRACKER_FILE.parent.mkdir(parents=True, exist_ok=True)
            return cls()
        try:
            data = json.loads(COST_TRACKER_FILE.read_text())
            entries = [CostEntry(**e) for e in data.get("entries", [])]
            return cls(entries=entries, total_usd=data.get("total_usd", 0.0))
        except (json.JSONDecodeError, OSError):
            return cls()

    def save(self) -> None:
        COST_TRACKER_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "entries": [
                {
                    "timestamp": e.timestamp,
                    "dataset": e.dataset,
                    "mode": e.mode,
                    "input_tokens": e.input_tokens,
                    "output_tokens": e.output_tokens,
                    "cost_usd": e.cost_usd,
                }
                for e in self.entries
            ],
            "total_usd": self.total_usd,
        }
        COST_TRACKER_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def estimate(
        self, *, input_tokens: int, output_tokens: int, batch: bool,
    ) -> float:
        """Estime le coût d'un appel."""
        if batch:
            return (
                input_tokens / 1_000_000 * SONNET_4_6_BATCH_INPUT_PER_M
                + output_tokens / 1_000_000 * SONNET_4_6_BATCH_OUTPUT_PER_M
            )
        return (
            input_tokens / 1_000_000 * SONNET_4_6_SYNC_INPUT_PER_M
            + output_tokens / 1_000_000 * SONNET_4_6_SYNC_OUTPUT_PER_M
        )

    def can_afford(self, cost: float) -> bool:
        return self.total_usd + cost <= HARD_BUDGET_USD

    def record(
        self,
        *,
        dataset: str,
        mode: str,
        input_tokens: int,
        output_tokens: int,
    ) -> CostEntry:
        cost = self.estimate(
            input_tokens=input_tokens, output_tokens=output_tokens,
            batch=(mode == "batch"),
        )
        if not self.can_afford(cost):
            raise PermissionError(
                f"Hard budget cap depasse : "
                f"total {self.total_usd:.4f}$ + {cost:.4f}$ > {HARD_BUDGET_USD}$"
            )
        entry = CostEntry(
            timestamp=time.time(),
            dataset=dataset, mode=mode,
            input_tokens=input_tokens, output_tokens=output_tokens,
            cost_usd=cost,
        )
        self.entries.append(entry)
        self.total_usd += cost
        self.save()
        return entry

    def summary(self) -> str:
        lines = [f"Total Phase H : ${self.total_usd:.4f} / ${HARD_BUDGET_USD}"]
        per_dataset: dict[str, float] = {}
        for e in self.entries:
            per_dataset[e.dataset] = per_dataset.get(e.dataset, 0.0) + e.cost_usd
        for ds, cost in sorted(per_dataset.items()):
            lines.append(f"  {ds}: ${cost:.4f}")
        return "\n".join(lines)


def get_anthropic_client():
    """Lazy import + load env."""
    load_env()
    from anthropic import Anthropic
    return Anthropic()


__all__ = [
    "HARD_BUDGET_USD",
    "SONNET_4_6_BATCH_INPUT_PER_M",
    "SONNET_4_6_BATCH_OUTPUT_PER_M",
    "CostEntry",
    "CostTracker",
    "get_anthropic_client",
    "load_env",
]
