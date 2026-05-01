"""Gestion des profils de canonicite (filtrage des sources actives)."""

from __future__ import annotations

from dataclasses import dataclass

from shinobi.canon.models import CanonBundle
from shinobi.constants import CANONICITY_ORDER
from shinobi.types import Canonicity


@dataclass(frozen=True)
class CanonicityProfile:
    """Profil de canonicite : sources autorisees et exclusions explicites."""

    sources: frozenset[str]
    label: str = "default"

    @classmethod
    def default(cls) -> CanonicityProfile:
        return cls(
            sources=frozenset({"manga", "boruto_manga", "tbv", "databook", "movie_canon"}),
            label="default",
        )

    @classmethod
    def manga_only(cls) -> CanonicityProfile:
        return cls(sources=frozenset({"manga", "databook"}), label="manga_only")

    @classmethod
    def all_sources(cls) -> CanonicityProfile:
        return cls(sources=frozenset(CANONICITY_ORDER), label="all_sources")

    @classmethod
    def from_csv(cls, csv: str, label: str = "custom") -> CanonicityProfile:
        items = {s.strip() for s in csv.split(",") if s.strip()}
        return cls(sources=frozenset(items), label=label)

    def accepts(self, canonicity: Canonicity | str) -> bool:
        return str(canonicity) in self.sources


def filter_canon(bundle: CanonBundle, profile: CanonicityProfile) -> CanonBundle:
    """Retourne un bundle filtre selon le profil de canonicite."""
    return CanonBundle(
        world_rules=bundle.world_rules,
        natures={k: v for k, v in bundle.natures.items() if profile.accepts(v.canonicity)},
        ranks={k: v for k, v in bundle.ranks.items() if profile.accepts(v.canonicity)},
        eras={k: v for k, v in bundle.eras.items() if profile.accepts(v.canonicity)},
        villages={k: v for k, v in bundle.villages.items() if profile.accepts(v.canonicity)},
        clans={k: v for k, v in bundle.clans.items() if profile.accepts(v.canonicity)},
        organizations={
            k: v for k, v in bundle.organizations.items() if profile.accepts(v.canonicity)
        },
        characters={k: v for k, v in bundle.characters.items() if profile.accepts(v.canonicity)},
        tailed_beasts={
            k: v for k, v in bundle.tailed_beasts.items() if profile.accepts(v.canonicity)
        },
        kekkei_genkai={
            k: v for k, v in bundle.kekkei_genkai.items() if profile.accepts(v.canonicity)
        },
        kekkei_mora={k: v for k, v in bundle.kekkei_mora.items() if profile.accepts(v.canonicity)},
        hiden={k: v for k, v in bundle.hiden.items() if profile.accepts(v.canonicity)},
        techniques={k: v for k, v in bundle.techniques.items() if profile.accepts(v.canonicity)},
        weapons_tools={
            k: v for k, v in bundle.weapons_tools.items() if profile.accepts(v.canonicity)
        },
        locations={k: v for k, v in bundle.locations.items() if profile.accepts(v.canonicity)},
        timeline_events={
            k: v for k, v in bundle.timeline_events.items() if profile.accepts(v.canonicity)
        },
        voice_profiles=dict(bundle.voice_profiles),
    )
