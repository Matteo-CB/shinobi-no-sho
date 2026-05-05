"""Validation referentielle inter-datasets canoniques.

Detecte les references cassees entre Character/Technique/Clan/Village/etc.
Permet de :
1. Logger les anomalies au load (audit)
2. Generer un rapport detaille pour reparation
3. Reparer automatiquement les ids inverses (sakura_haruno vs haruno_sakura)
   et autres patterns connus.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from shinobi.canon.models import CanonBundle
from shinobi.logging_setup import get_logger

logger = get_logger(__name__)


@dataclass
class IntegrityReport:
    """Rapport detaille d'integrite referentielle."""

    broken_technique_users: dict[str, list[str]] = field(default_factory=dict)
    broken_clan_kekkei: dict[str, list[str]] = field(default_factory=dict)
    broken_clan_techniques: dict[str, list[str]] = field(default_factory=dict)
    broken_village_clans: dict[str, list[str]] = field(default_factory=dict)
    broken_village_kages: dict[str, list[str]] = field(default_factory=dict)
    broken_character_relationships: dict[str, list[str]] = field(default_factory=dict)
    broken_event_locations: dict[str, list[str]] = field(default_factory=dict)
    broken_event_characters: dict[str, list[str]] = field(default_factory=dict)
    broken_organization_members: dict[str, list[str]] = field(default_factory=dict)
    broken_tailed_beast_jinchuuriki: dict[str, list[str]] = field(default_factory=dict)
    auto_fixable: dict[str, str] = field(default_factory=dict)  # broken_id -> fixed_id

    @property
    def total_broken(self) -> int:
        return sum(
            sum(len(v) for v in d.values())
            for d in [
                self.broken_technique_users,
                self.broken_clan_kekkei,
                self.broken_clan_techniques,
                self.broken_village_clans,
                self.broken_village_kages,
                self.broken_character_relationships,
                self.broken_event_locations,
                self.broken_event_characters,
                self.broken_organization_members,
                self.broken_tailed_beast_jinchuuriki,
            ]
        )

    def summary(self) -> dict[str, int]:
        return {
            "technique_users": sum(len(v) for v in self.broken_technique_users.values()),
            "clan_kekkei": sum(len(v) for v in self.broken_clan_kekkei.values()),
            "clan_techniques": sum(len(v) for v in self.broken_clan_techniques.values()),
            "village_clans": sum(len(v) for v in self.broken_village_clans.values()),
            "village_kages": sum(len(v) for v in self.broken_village_kages.values()),
            "character_relationships": sum(
                len(v) for v in self.broken_character_relationships.values()
            ),
            "event_locations": sum(len(v) for v in self.broken_event_locations.values()),
            "event_characters": sum(len(v) for v in self.broken_event_characters.values()),
            "organization_members": sum(
                len(v) for v in self.broken_organization_members.values()
            ),
            "tailed_beast_jinchuuriki": sum(
                len(v) for v in self.broken_tailed_beast_jinchuuriki.values()
            ),
            "auto_fixable": len(self.auto_fixable),
        }


def _try_fix_inverted_id(broken_id: str, valid_ids: set[str]) -> str | None:
    """Tente de reparer un id 'family_given' inverse en 'given_family' ou vice versa.

    Ex: 'sakura_haruno' (broken) -> 'haruno_sakura' (valide).
    """
    parts = broken_id.split("_")
    if len(parts) >= 2:
        # Inverse seulement les deux extremes
        inverted = "_".join(parts[::-1])
        if inverted in valid_ids:
            return inverted
        # Inverse premier+dernier seulement (cas 3 mots+)
        if len(parts) >= 3:
            inverted2 = parts[-1] + "_" + "_".join(parts[1:-1]) + "_" + parts[0]
            if inverted2 in valid_ids:
                return inverted2
    return None


def _try_fix_substring_match(broken_id: str, valid_ids: set[str]) -> str | None:
    """Cherche un id valide qui contient broken_id en substring (ex: 'kakashi' -> 'hatake_kakashi')."""
    if len(broken_id) < 4:
        return None
    candidates = [v for v in valid_ids if broken_id in v]
    if len(candidates) == 1:
        return candidates[0]
    return None


def validate_canon_integrity(bundle: CanonBundle, *, auto_fix: bool = True) -> IntegrityReport:
    """Audit complet des references inter-datasets. Retourne rapport detaille.

    Si auto_fix=True, tente de proposer des corrections automatiques pour les
    cas reparables (ids inverses, prefixe missing).
    """
    report = IntegrityReport()
    char_ids = set(bundle.characters)
    clan_ids = set(bundle.clans)
    tech_ids = set(bundle.techniques)
    village_ids = set(bundle.villages)
    location_ids = set(bundle.locations)
    kekkei_ids = set(bundle.kekkei_genkai) | set(bundle.kekkei_mora)

    # Helper
    def _check_refs(refs: list[str], valid: set[str], collector: dict, key: str) -> None:
        for ref in refs:
            if ref not in valid:
                collector.setdefault(key, []).append(ref)
                if auto_fix:
                    fix = _try_fix_inverted_id(ref, valid) or _try_fix_substring_match(ref, valid)
                    if fix:
                        report.auto_fixable[ref] = fix

    # 1. Techniques.canonical_users -> characters
    for tid, t in bundle.techniques.items():
        _check_refs(t.canonical_users, char_ids, report.broken_technique_users, tid)

    # 2. Clans.key_kekkei_genkai -> kekkei_genkai
    for cid, c in bundle.clans.items():
        _check_refs(c.key_kekkei_genkai, kekkei_ids, report.broken_clan_kekkei, cid)
        _check_refs(c.key_techniques, tech_ids, report.broken_clan_techniques, cid)
        _check_refs(c.exclusive_techniques, tech_ids, report.broken_clan_techniques, cid)

    # 3. Villages.main_clans -> clans, kage_lineage.character_id -> characters
    for vid, v in bundle.villages.items():
        _check_refs(v.main_clans, clan_ids, report.broken_village_clans, vid)
        for kage in v.kage_lineage:
            if kage.character_id not in char_ids:
                report.broken_village_kages.setdefault(vid, []).append(kage.character_id)
                if auto_fix:
                    fix = _try_fix_inverted_id(kage.character_id, char_ids)
                    if fix:
                        report.auto_fixable[kage.character_id] = fix

    # 4. Characters.key_relationships.with_character -> characters
    for cid, char in bundle.characters.items():
        for rel in char.key_relationships:
            if rel.with_character not in char_ids:
                report.broken_character_relationships.setdefault(cid, []).append(rel.with_character)

    # 5. TimelineEvents.location -> locations|villages, involved_characters -> characters
    valid_locations_or_villages = location_ids | village_ids
    for eid, e in bundle.timeline_events.items():
        if e.location and e.location not in valid_locations_or_villages:
            report.broken_event_locations.setdefault(eid, []).append(e.location)
        _check_refs(e.involved_characters, char_ids, report.broken_event_characters, eid)

    # 6. Organizations.founders/leaders/members -> characters
    for oid, org in bundle.organizations.items():
        _check_refs(org.founders, char_ids, report.broken_organization_members, oid)
        for le in org.leaders_by_era:
            if le.leader not in char_ids:
                report.broken_organization_members.setdefault(oid, []).append(le.leader)
        for me in org.members_by_era:
            for m in me.members:
                if m not in char_ids:
                    report.broken_organization_members.setdefault(oid, []).append(m)

    # 7. TailedBeasts.jinchuuriki -> characters
    for bid, beast in bundle.tailed_beasts.items():
        for je in beast.current_jinchuuriki_by_era:
            if je.jinchuuriki and je.jinchuuriki not in char_ids:
                report.broken_tailed_beast_jinchuuriki.setdefault(bid, []).append(je.jinchuuriki)

    if report.total_broken > 0:
        # On enleve auto_fixable de summary() pour eviter conflit kwarg
        summary_no_auto = {k: v for k, v in report.summary().items() if k != "auto_fixable"}
        logger.warning(
            "canon_integrity_broken_refs",
            total=report.total_broken,
            auto_fixable=len(report.auto_fixable),
            **summary_no_auto,
        )
    return report


def format_report(report: IntegrityReport) -> str:
    """Formate le rapport en texte lisible."""
    lines = ["=== Rapport d'integrite canonique ==="]
    summary = report.summary()
    total = report.total_broken
    lines.append(f"Total refs cassees : {total}")
    lines.append(f"Auto-reparables : {summary['auto_fixable']}")
    lines.append("")
    lines.append("Par categorie :")
    for k, v in summary.items():
        if k == "auto_fixable":
            continue
        if v > 0:
            lines.append(f"  {k}: {v}")
    if report.auto_fixable:
        lines.append("")
        lines.append("Suggestions de reparation auto (15 premieres) :")
        for broken, fix in list(report.auto_fixable.items())[:15]:
            lines.append(f"  {broken}  ->  {fix}")
    return "\n".join(lines)


def aggregate_id_substitutions(report: IntegrityReport) -> dict[str, str]:
    """Retourne un dict {broken_id: fixed_id} pour appliquer en bulk."""
    return dict(report.auto_fixable)


def collect_unfixable_broken_refs(report: IntegrityReport) -> set[str]:
    """Liste tous les ids casses pour lesquels on n'a PAS de reparation auto."""
    all_broken: set[str] = set()
    for collector in (
        report.broken_technique_users,
        report.broken_clan_kekkei,
        report.broken_clan_techniques,
        report.broken_village_clans,
        report.broken_village_kages,
        report.broken_event_locations,
        report.broken_event_characters,
        report.broken_organization_members,
        report.broken_tailed_beast_jinchuuriki,
    ):
        for refs in collector.values():
            all_broken.update(refs)
    return all_broken - set(report.auto_fixable)
