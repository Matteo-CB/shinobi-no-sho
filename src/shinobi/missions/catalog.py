"""Catalogue des missions canon : load, query, persistance.

Le fichier source est `data/canonical/missions.json`. Format :
{
  "_meta": {"version": 1, "schema": "mission_v1"},
  "missions": [<Mission dict>, <Mission dict>, ...]
}

Le catalogue propose des helpers :
- by_id, by_year_range, by_rank, by_type, by_participant, by_arc
- count, all
- save_to_file (extension future)
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from shinobi.missions.types import Mission, MissionRank, MissionType


class MissionCatalog:
    """Catalogue mission canon. Stateless apres load."""

    def __init__(self, missions: Iterable[Mission] = ()) -> None:
        self._missions: dict[str, Mission] = {}
        for m in missions:
            if m.id in self._missions:
                raise ValueError(f"Mission id duplique : {m.id}")
            self._missions[m.id] = m

    @classmethod
    def from_json_file(cls, path: Path | str) -> MissionCatalog:
        """Charge depuis un JSON 'missions': [...]."""
        p = Path(path)
        if not p.exists():
            return cls()
        data = json.loads(p.read_text(encoding="utf-8"))
        items = data.get("missions", []) if isinstance(data, dict) else data
        if not isinstance(items, list):
            return cls()
        missions = [Mission.model_validate(item) for item in items]
        return cls(missions)

    def to_json_file(self, path: Path | str, *, version: int = 1) -> int:
        """Persiste le catalogue. Retourne le nombre de missions ecrites."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "_meta": {"version": version, "schema": "mission_v1"},
            "missions": [
                m.model_dump(mode="json", exclude_unset=False)
                for m in self._missions.values()
            ],
        }
        p.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False),
            encoding="utf-8",
        )
        return len(self._missions)

    @property
    def count(self) -> int:
        return len(self._missions)

    def all(self) -> list[Mission]:
        return list(self._missions.values())

    def by_id(self, mission_id: str) -> Mission | None:
        return self._missions.get(mission_id)

    def by_year_range(
        self, *, year_min: int | None = None, year_max: int | None = None,
    ) -> list[Mission]:
        out: list[Mission] = []
        for m in self._missions.values():
            if year_min is not None and m.year < year_min:
                continue
            if year_max is not None and m.year > year_max:
                continue
            out.append(m)
        return sorted(out, key=lambda m: (m.year, m.month or 0, m.day or 0))

    def by_rank(self, rank: MissionRank | str) -> list[Mission]:
        rval = rank.value if isinstance(rank, MissionRank) else rank
        return [m for m in self._missions.values() if m.rank.value == rval]

    def by_type(self, mtype: MissionType | str) -> list[Mission]:
        tval = mtype.value if isinstance(mtype, MissionType) else mtype
        return [m for m in self._missions.values() if m.type.value == tval]

    def by_participant(self, character_id: str) -> list[Mission]:
        return [m for m in self._missions.values() if m.has_participant(character_id)]

    def by_arc(self, arc: str) -> list[Mission]:
        return [m for m in self._missions.values() if m.canonical_arc == arc]

    def by_location(self, location_id: str) -> list[Mission]:
        return [m for m in self._missions.values() if m.location_id == location_id]

    def add(self, mission: Mission) -> None:
        if mission.id in self._missions:
            raise ValueError(f"Mission deja presente : {mission.id}")
        self._missions[mission.id] = mission

    def __len__(self) -> int:
        return len(self._missions)

    def __iter__(self):
        return iter(self._missions.values())

    def __contains__(self, mission_id: str) -> bool:
        return mission_id in self._missions


__all__ = ["MissionCatalog"]
