"""AgentRoster : gestion top-15 + secondary 50 + dynamique par arc.

docs/02 §6.1 + 6.4 :
> Pour les top-15 PNJ majeurs (Naruto, Sasuke, Sakura, Kakashi, Itachi,
> Madara, Hashirama, Tsunade, Jiraiya, Orochimaru, Pain, Konan, Obito,
> Minato, Hiruzen + dynamique selon arc), chacun est un agent.
>
> PNJ secondaires (~50) : simulation par lot toutes les 10 ticks
> Tous les autres : comportements canoniques par defaut, eleves au statut
>   d'agent uniquement si le joueur interagit avec eux ou s'ils sont
>   impactes par un event majeur

Le roster est :
- Initialise au demarrage (top-15 statique + 50 secondary derives)
- Dynamique selon arc : `arc_relevant_npcs(year, eras_data)` retourne les
  key_figures de l'ere courante (eras.json) pour eleves automatiquement.
- Auto-promotion : `on_player_interaction` (joueur cite un PNJ) +
  `on_event_impact` (PNJ implique dans event canon firing).
- Persiste dans `agent_roster` SQLite (per-save)
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from shinobi.agents.store import AgentMemoryStore
from shinobi.agents.types import AgentTier, RosterEntry

# Top-15 statique selon docs/02 §6.1
DEFAULT_TOP_15: tuple[str, ...] = (
    "uzumaki_naruto",
    "uchiha_sasuke",
    "haruno_sakura",
    "hatake_kakashi",
    "uchiha_itachi",
    "uchiha_madara",
    "senju_hashirama",
    "tsunade",
    "jiraiya",
    "orochimaru",
    "pain_nagato",
    "konan",
    "uchiha_obito",
    "namikaze_minato",
    "sarutobi_hiruzen",
)


# Secondary pool : ~50 NPCs canon importants (extraits de psycho_notes
# + characters majeurs absents du top-15). Le caller peut etendre.
DEFAULT_SECONDARY_50: tuple[str, ...] = (
    "umino_iruka", "uzumaki_kushina", "subaku_no_gaara", "killer_bee",
    "yamanaka_ino", "nara_shikamaru", "akimichi_choji", "hyuga_hinata",
    "hyuga_neji", "inuzuka_kiba", "aburame_shino", "lee_rock",
    "tenten", "haku", "momochi_zabuza", "yakushi_kabuto", "deidara",
    "sasori", "shimura_danzo", "uchiha_fugaku", "uchiha_mikoto",
    "sarutobi_konohamaru", "uzumaki_boruto", "uchiha_sarada",
    "mitsuki", "kawaki", "uzumaki_himawari", "kakashi_anbu",
    "uchiha_shisui", "kurama_kyuubi", "shukaku", "matatabi",
    "isobu", "son_goku", "kokuou", "saiken", "chomei", "gyuki",
    "tobirama_senju", "uchiha_izuna", "uzumaki_mito",
    "namikaze_minato_father", "uchiha_kagami", "uchiha_naori",
    "ootsutsuki_kaguya", "ootsutsuki_hagoromo", "ootsutsuki_hamura",
    "indra_otsutsuki", "asura_otsutsuki", "ay_yondaime_raikage",
    "onoki_sandaime_tsuchikage", "mei_terumi_godaime_mizukage",
)


class AgentRoster:
    """Roster d'agents : tier par npc_id + helpers d'elevation/retrogradation.

    Le roster est stocke en SQLite (`agent_roster` table) + cache en memoire
    pour lectures rapides.
    """

    def __init__(self, store: AgentMemoryStore) -> None:
        self._store = store
        self._cache: dict[str, RosterEntry] = {}
        self._load_cache()

    def _load_cache(self) -> None:
        self._cache.clear()
        for entry in self._store.list_roster():
            self._cache[entry.npc_id] = entry

    @property
    def store(self) -> AgentMemoryStore:
        return self._store

    @property
    def all_entries(self) -> list[RosterEntry]:
        return list(self._cache.values())

    @property
    def major_count(self) -> int:
        return sum(1 for e in self._cache.values() if e.tier == AgentTier.major)

    @property
    def secondary_count(self) -> int:
        return sum(
            1 for e in self._cache.values() if e.tier == AgentTier.secondary
        )

    def tier_for(self, npc_id: str) -> AgentTier:
        """Tier d'un PNJ. background si non present dans le roster."""
        entry = self._cache.get(npc_id)
        return entry.tier if entry else AgentTier.background

    def get(self, npc_id: str) -> RosterEntry | None:
        return self._cache.get(npc_id)

    def major_npc_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                e.npc_id for e in self._cache.values()
                if e.tier == AgentTier.major
            )
        )

    def secondary_npc_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                e.npc_id for e in self._cache.values()
                if e.tier == AgentTier.secondary
            )
        )

    # --- mutations ---------------------------------------------------------

    def add(
        self, npc_id: str, tier: AgentTier,
        *,
        included_since_year: int | None = None,
        notes: str = "",
    ) -> RosterEntry:
        """Ajoute ou met a jour un PNJ dans le roster avec un tier donne."""
        entry = RosterEntry(
            npc_id=npc_id, tier=tier,
            included_since_year=included_since_year,
            notes=notes,
        )
        self._store.upsert_roster(entry)
        self._cache[npc_id] = entry
        return entry

    def promote(
        self, npc_id: str, *, included_since_year: int | None = None,
        reason: str = "",
    ) -> RosterEntry:
        """Eleve un PNJ background -> secondary -> major.

        Logique stricte :
        - background -> secondary
        - secondary -> major
        - major reste major
        """
        current = self.tier_for(npc_id)
        if current == AgentTier.background:
            new_tier = AgentTier.secondary
        elif current == AgentTier.secondary:
            new_tier = AgentTier.major
        else:
            new_tier = AgentTier.major
        notes = f"promoted from {current.value}: {reason}" if reason else ""
        return self.add(
            npc_id, new_tier,
            included_since_year=included_since_year,
            notes=notes,
        )

    def demote(self, npc_id: str, *, reason: str = "") -> RosterEntry | None:
        """Retrograde major -> secondary -> background (suppression)."""
        current = self.tier_for(npc_id)
        if current == AgentTier.background:
            return None
        if current == AgentTier.major:
            return self.add(
                npc_id, AgentTier.secondary,
                notes=f"demoted from major: {reason}",
            )
        # secondary -> background : delete
        self._store.delete_roster_entry(npc_id)
        self._cache.pop(npc_id, None)
        return None

    def mark_active(
        self, npc_id: str, *, year: int, tick: int,
    ) -> None:
        """Met a jour last_active_year + last_active_tick."""
        entry = self._cache.get(npc_id)
        if entry is None:
            return
        updated = entry.model_copy(update={
            "last_active_year": year,
            "last_active_tick": tick,
        })
        self._store.upsert_roster(updated)
        self._cache[npc_id] = updated

    def should_simulate_this_tick(
        self, npc_id: str, *, tick: int, secondary_period: int = 10,
    ) -> bool:
        """Decide si un PNJ doit etre simule a ce tick.

        - major : oui chaque tick
        - secondary : oui tous les `secondary_period` ticks
        - background : non
        """
        tier = self.tier_for(npc_id)
        if tier == AgentTier.major:
            return True
        if tier == AgentTier.secondary:
            return tick % secondary_period == 0
        return False

    # --- dynamique arc + auto-promote (spec §6.1 + §6.4) -------------------

    def arc_relevant_npcs(
        self, year: int, eras_data: list[dict] | None = None,
    ) -> list[str]:
        """Retourne les key_figures de l'ere canonique contenant `year`.

        Spec §6.1 : 'top-15 majeurs ... + dynamique selon arc'. Les key_figures
        de eras.json correspondent aux personnages dramatiquement importants
        pour cette ere (ex: Hashirama+Madara pour warring_states, Naruto+Sasuke
        pour part_2, Boruto+Kawaki pour boruto era).

        Si `eras_data` est None, retourne []. Le caller doit charger eras.json.
        """
        if not eras_data:
            return []
        for era in eras_data:
            if not isinstance(era, dict):
                continue
            ys = era.get("year_start")
            ye = era.get("year_end")
            if ys is None or ye is None:
                continue
            if ys <= year <= ye:
                key_figs = era.get("key_figures") or []
                return [
                    nid for nid in key_figs
                    if isinstance(nid, str) and nid
                ]
        return []

    def promote_arc_relevant(
        self,
        year: int,
        eras_data: list[dict],
        *,
        target_tier: AgentTier = AgentTier.secondary,
    ) -> list[str]:
        """Eleve les key_figures de l'ere courante au tier cible (secondary
        par defaut). Les NPCs deja major restent major. Retourne les NPCs
        nouvellement promus.
        """
        relevant = self.arc_relevant_npcs(year, eras_data)
        promoted: list[str] = []
        for npc_id in relevant:
            current = self.tier_for(npc_id)
            if current == AgentTier.major:
                continue  # deja au top
            if current == AgentTier.secondary and target_tier != AgentTier.major:
                continue  # deja secondary
            self.add(
                npc_id, target_tier,
                included_since_year=year,
                notes=f"arc_relevant year={year}",
            )
            promoted.append(npc_id)
        return promoted

    def on_player_interaction(
        self,
        npc_id: str,
        *,
        year: int | None = None,
        tick: int | None = None,
    ) -> RosterEntry | None:
        """Spec §6.4 : 'eleves au statut d'agent uniquement si le joueur
        interagit avec eux'. Promote background -> secondary.

        Si le PNJ est deja major ou secondary, marque just `last_active`.
        Retourne le RosterEntry resultant (avec last_active a jour) ou None
        si invalide.
        """
        if not npc_id:
            return None
        tier = self.tier_for(npc_id)
        if tier == AgentTier.background:
            self.add(
                npc_id, AgentTier.secondary,
                included_since_year=year,
                notes="player_interaction",
            )
        if year is not None and tick is not None:
            self.mark_active(npc_id, year=year, tick=tick)
        return self._cache.get(npc_id)

    def on_event_impact(
        self,
        involved_npc_ids: Iterable[str],
        *,
        year: int,
        tick: int | None = None,
    ) -> list[str]:
        """Spec §6.4 : 'eleves au statut d'agent ... ou s'ils sont impactes
        par un event majeur'. Pour chaque NPC implique dans un event canon
        firing : promote background -> secondary.

        Retourne la liste des NPCs nouvellement promus.
        """
        promoted: list[str] = []
        for npc_id in involved_npc_ids:
            if not npc_id:
                continue
            tier = self.tier_for(npc_id)
            if tier == AgentTier.background:
                self.add(
                    npc_id, AgentTier.secondary,
                    included_since_year=year,
                    notes="event_impact",
                )
                promoted.append(npc_id)
            if tick is not None:
                self.mark_active(npc_id, year=year, tick=tick)
        return promoted


def load_eras_data(path: Path | str) -> list[dict]:
    """Charge eras.json (helper). Retourne [] si absent ou invalide."""
    p = Path(path)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return []


def initialize_roster(
    store: AgentMemoryStore,
    *,
    top_15: Iterable[str] = DEFAULT_TOP_15,
    secondary_50: Iterable[str] = DEFAULT_SECONDARY_50,
    included_since_year: int | None = None,
) -> AgentRoster:
    """Initialise le roster en bulk : top-15 + secondary-50.

    Idempotent : si une entry existe deja, on garde son tier.
    """
    existing = {e.npc_id for e in store.list_roster()}
    for npc_id in top_15:
        if npc_id in existing:
            continue
        store.upsert_roster(RosterEntry(
            npc_id=npc_id, tier=AgentTier.major,
            included_since_year=included_since_year,
        ))
    for npc_id in secondary_50:
        if npc_id in existing:
            continue
        store.upsert_roster(RosterEntry(
            npc_id=npc_id, tier=AgentTier.secondary,
            included_since_year=included_since_year,
        ))
    return AgentRoster(store)


__all__ = [
    "DEFAULT_SECONDARY_50",
    "DEFAULT_TOP_15",
    "AgentRoster",
    "initialize_roster",
    "load_eras_data",
]
