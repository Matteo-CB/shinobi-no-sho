"""DialogueLog : historique des dialogues avec rolling window borne.

Garde les N dernieres lignes (default 5000), avec offload optionnel des plus
anciennes vers un fichier d'archive JSON-Lines (jsonl). Permet a l'app VN
future de relire l'historique complet sans saturer la memoire.

Fonctionnalites :
- append(line) : ajout O(1)
- query par speaker, par year range, par event_id, par mission_id
- to_jsonl_file / from_jsonl_file : persistance idempotente
- archive_old() : decharge les lignes les plus anciennes vers fichier disque
- size, max_size, clear()
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from shinobi.dialogue.types import DialogueLine


@dataclass(frozen=True)
class DialogueLogConfig:
    """Parametres du log."""

    max_lines: int = 5000  # rolling window cap memoire
    archive_threshold: int = 4000  # offload des >= N anciennes vers archive
    archive_path: Path | None = None  # None = pas d'archive disque


class DialogueLog:
    """Historique borne des dialogues prononces.

    Implemente comme un deque pour append O(1), avec helpers de query
    sequentiels (pas indexes). Pour des recherches massives, l'app VN
    pourra utiliser le format VN export ou indexer cote SQLite plus tard.
    """

    def __init__(
        self,
        *,
        config: DialogueLogConfig | None = None,
        initial_lines: Iterable[DialogueLine] = (),
    ) -> None:
        self._config = config or DialogueLogConfig()
        self._lines: deque[DialogueLine] = deque(
            initial_lines, maxlen=self._config.max_lines,
        )

    @property
    def config(self) -> DialogueLogConfig:
        return self._config

    @property
    def size(self) -> int:
        return len(self._lines)

    @property
    def max_size(self) -> int:
        return self._config.max_lines

    @property
    def is_full(self) -> bool:
        return len(self._lines) >= self._config.max_lines

    def append(self, line: DialogueLine) -> None:
        """Ajout O(1). Si plein, retire la ligne la plus ancienne. Archive
        prealable si archive_path est configure et que threshold est atteint."""
        if (
            self._config.archive_path is not None
            and len(self._lines) >= self._config.archive_threshold
            and not self.is_full
        ):
            # Pre-archive : on offload pour eviter la perte
            self.archive_old()
        self._lines.append(line)

    def append_many(self, lines: Iterable[DialogueLine]) -> int:
        """Ajout en bulk. Retourne le nombre de lignes ajoutees."""
        n = 0
        for line in lines:
            self.append(line)
            n += 1
        return n

    def clear(self) -> None:
        self._lines.clear()

    def __iter__(self) -> Iterator[DialogueLine]:
        return iter(self._lines)

    def __len__(self) -> int:
        return len(self._lines)

    def all(self) -> list[DialogueLine]:
        return list(self._lines)

    def last_n(self, n: int) -> list[DialogueLine]:
        if n <= 0:
            return []
        return list(self._lines)[-n:]

    # --- Queries -----------------------------------------------------------

    def by_speaker(self, speaker_id: str) -> list[DialogueLine]:
        return [d for d in self._lines if d.speaker_id == speaker_id]

    def by_year_range(
        self, *, year_min: int | None = None, year_max: int | None = None,
    ) -> list[DialogueLine]:
        out: list[DialogueLine] = []
        for d in self._lines:
            if d.in_game_year is None:
                continue
            if year_min is not None and d.in_game_year < year_min:
                continue
            if year_max is not None and d.in_game_year > year_max:
                continue
            out.append(d)
        return out

    def by_event(self, event_id: str) -> list[DialogueLine]:
        return [d for d in self._lines if d.related_event_id == event_id]

    def by_mission(self, mission_id: str) -> list[DialogueLine]:
        return [d for d in self._lines if d.related_mission_id == mission_id]

    def by_location(self, location_id: str) -> list[DialogueLine]:
        return [d for d in self._lines if d.location_id == location_id]

    def by_turn_range(
        self, *, turn_min: int | None = None, turn_max: int | None = None,
    ) -> list[DialogueLine]:
        out: list[DialogueLine] = []
        for d in self._lines:
            if d.turn_number is None:
                continue
            if turn_min is not None and d.turn_number < turn_min:
                continue
            if turn_max is not None and d.turn_number > turn_max:
                continue
            out.append(d)
        return out

    def thoughts_only(self) -> list[DialogueLine]:
        return [d for d in self._lines if d.is_thought]

    def speech_only(self) -> list[DialogueLine]:
        return [d for d in self._lines if not d.is_thought]

    def speakers(self) -> list[str]:
        """Liste deduplique des speakers ayant parle."""
        seen: set[str] = set()
        for d in self._lines:
            seen.add(d.speaker_id)
        return sorted(seen)

    # --- Persistance JSONL -------------------------------------------------

    def to_jsonl_file(self, path: Path | str) -> int:
        """Persiste tout le log dans un fichier JSON-Lines. Retourne nb lignes."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", encoding="utf-8") as fh:
            for line in self._lines:
                fh.write(line.model_dump_json() + "\n")
        return len(self._lines)

    def append_to_jsonl_file(self, path: Path | str) -> int:
        """Append au lieu de remplacer. Pour les sessions longues."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            for line in self._lines:
                fh.write(line.model_dump_json() + "\n")
        return len(self._lines)

    @classmethod
    def from_jsonl_file(
        cls, path: Path | str, *, config: DialogueLogConfig | None = None,
    ) -> DialogueLog:
        """Charge un log depuis un fichier JSON-Lines."""
        p = Path(path)
        if not p.exists():
            return cls(config=config)
        lines: list[DialogueLine] = []
        with p.open("r", encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                lines.append(DialogueLine.model_validate_json(raw))
        return cls(config=config, initial_lines=lines)

    # --- Archive -----------------------------------------------------------

    def archive_old(self, n: int | None = None) -> int:
        """Decharge les n lignes les plus anciennes vers le fichier d'archive.

        Default : archive_threshold lignes. Retourne le nombre archivees.
        Si archive_path est None, no-op.
        """
        if self._config.archive_path is None or len(self._lines) == 0:
            return 0
        n = n or min(self._config.archive_threshold, len(self._lines))
        n = max(0, min(n, len(self._lines)))
        if n == 0:
            return 0
        old = [self._lines.popleft() for _ in range(n)]
        # Append to archive file
        ap = self._config.archive_path
        ap.parent.mkdir(parents=True, exist_ok=True)
        with ap.open("a", encoding="utf-8") as fh:
            for line in old:
                fh.write(line.model_dump_json() + "\n")
        return n


__all__ = ["DialogueLog", "DialogueLogConfig"]
