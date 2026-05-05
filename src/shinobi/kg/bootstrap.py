"""Bootstrap du reseau social et des beliefs canon depuis les datasets.

Strategie pour la Phase B (donnees canon limitees : key_relationships vide
pour les 1360 personnages) :

A. Liens sociaux derives de signaux indirects
1. Meme clan -> link 'family', strength 0.7-0.9 selon clan
2. psycho_notes.json `allowed_relations` -> parsing du parenthese
   ('frere', 'mere', 'pere', 'mentor', 'ami', 'rival', 'ennemi') -> link_type
3. kage_lineage successifs -> link 'mentor'/'student' (tobirama -> hiruzen, etc.)

B. Beliefs canon (par defaut)
Tous les NPCs canon connaissent les facts publiquement attestes (canonicity =
canon_strict, source = canon). Fidelity 1.0 par convention.

Le bootstrap est idempotent : on clear_all() les tables avant de remplir.
Les liens et beliefs ajoutes manuellement en jeu sont preserves uniquement
via les facts non-canon.
"""

from __future__ import annotations

import itertools
import json
import re
from pathlib import Path
from typing import Any

from shinobi.kg.belief import BeliefPropagator
from shinobi.kg.schema import Belief, SocialLink
from shinobi.kg.social import SocialNetwork
from shinobi.kg.store import KnowledgeGraphStore
from shinobi.logging_setup import get_logger

logger = get_logger(__name__)


# Mapping mot-cle FR (du psycho_notes 'allowed_relations') -> link_type + strength
_RELATION_KEYWORDS: list[tuple[str, str, float]] = [
    # mot-cle dans (parenthese), link_type, strength
    ("frere", "family", 0.95),
    ("frère", "family", 0.95),
    ("soeur", "family", 0.95),
    ("sœur", "family", 0.95),
    ("mere", "family", 0.95),
    ("mère", "family", 0.95),
    ("pere", "family", 0.9),
    ("père", "family", 0.9),
    ("grand-pere", "family", 0.85),
    ("grand-père", "family", 0.85),
    ("oncle", "family", 0.7),
    ("tante", "family", 0.7),
    ("cousin", "family", 0.65),
    ("cousine", "family", 0.65),
    ("femme", "family", 0.95),
    ("mari", "family", 0.95),
    ("epoux", "family", 0.95),
    ("epouse", "family", 0.95),
    ("petit-fils", "family", 0.85),
    ("petite-fille", "family", 0.85),
    ("filleul", "family", 0.7),
    ("famille", "family", 0.7),
    # mentorat
    ("sensei", "mentor", 0.85),
    ("maitre", "mentor", 0.85),
    ("maître", "mentor", 0.85),
    ("mentor", "mentor", 0.85),
    ("eleve", "student", 0.85),
    ("élève", "student", 0.85),
    ("disciple", "student", 0.8),
    ("filleul", "student", 0.7),
    # amitie
    ("ami", "friend", 0.75),
    ("amie", "friend", 0.75),
    ("amis", "friend", 0.75),
    ("camarade", "friend", 0.6),
    ("equipier", "ally", 0.7),
    ("équipier", "ally", 0.7),
    ("equipiere", "ally", 0.7),
    ("compagnon", "ally", 0.7),
    # rivalite/ennemi
    ("rival", "rival", 0.6),
    ("ennemi", "enemy", 0.5),
    ("adversaire", "enemy", 0.5),
    ("ennemie", "enemy", 0.5),
]


def _parse_relation(annotation: str) -> tuple[str, float] | None:
    """Extrait (link_type, strength) du commentaire entre parentheses.

    Ex: 'sarutobi_hiruzen (Sandaime, le surveille de loin)' ->
        chercher mot-cle dans 'Sandaime, le surveille de loin' (case insensitive)
    """
    m = re.search(r"\(([^)]+)\)", annotation)
    if not m:
        return None
    body = m.group(1).lower()
    for keyword, link_type, strength in _RELATION_KEYWORDS:
        if keyword in body:
            return (link_type, strength)
    return None


def _split_id_and_annotation(item: str) -> tuple[str, str]:
    """Separe 'sarutobi_hiruzen (Sandaime, le surveille)' en (id, body)."""
    m = re.match(r"^([a-z_][a-z0-9_]*)", item.lower())
    if not m:
        return item.strip(), ""
    return m.group(1), item[m.end():].strip()


def derive_links_from_clans(
    social: SocialNetwork, characters: list[dict[str, Any]],
) -> int:
    """Cree des liens family entre membres d'un meme clan.

    Strategie conservatrice : on ne cree de liens automatiques que pour les
    clans avec moins de 8 membres (sinon explosion combinatoire). Pour les
    grands clans (Uchiha, Hyuga, Senju), les liens precis seront ajoutes
    manuellement ou via psycho_notes.
    """
    by_clan: dict[str, list[str]] = {}
    for c in characters:
        cid = c.get("id")
        clan = c.get("clan")
        if cid and clan:
            by_clan.setdefault(clan, []).append(cid)
    n = 0
    for clan, members in by_clan.items():
        if len(members) > 8:
            continue  # eviter explosion
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                social.add_link(SocialLink(
                    npc_a=a, npc_b=b,
                    link_type="family",
                    strength=0.7,
                    notes=f"derived_from_clan:{clan}",
                ))
                n += 1
    return n


def derive_links_from_psycho_notes(
    social: SocialNetwork, psycho_notes: dict[str, Any], characters_ids: set[str],
) -> int:
    """Cree des liens depuis allowed_relations (psycho_notes.json)."""
    n = 0
    notes = psycho_notes.get("notes", {})
    for npc_id, entries in notes.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            for rel_str in entry.get("allowed_relations", []) or []:
                if not isinstance(rel_str, str):
                    continue
                target_id, _annot = _split_id_and_annotation(rel_str)
                if target_id == npc_id or target_id not in characters_ids:
                    continue
                parsed = _parse_relation(rel_str)
                if parsed is None:
                    parsed = ("acquaintance", 0.4)
                link_type, strength = parsed
                social.add_link(SocialLink(
                    npc_a=npc_id, npc_b=target_id,
                    link_type=link_type,
                    strength=strength,
                    valid_from_year=int(entry.get("from_age", 0))
                    if isinstance(entry.get("from_age"), int) else None,
                    notes="derived_from_psycho_notes",
                ))
                n += 1
    return n


def derive_links_from_kage_lineage(
    social: SocialNetwork, villages: list[dict[str, Any]],
) -> int:
    """Cree des liens mentor/student entre kages successifs d'un village."""
    n = 0
    for v in villages:
        lineage = v.get("kage_lineage") or []
        if not isinstance(lineage, list):
            continue
        sorted_lineage = sorted(
            (k for k in lineage if k.get("character_id") and k.get("from_year") is not None),
            key=lambda k: k["from_year"],
        )
        for prev, curr in itertools.pairwise(sorted_lineage):
            a = prev.get("character_id")
            b = curr.get("character_id")
            if a and b and a != b:
                social.add_link(SocialLink(
                    npc_a=a, npc_b=b,
                    link_type="mentor",
                    strength=0.6,
                    valid_from_year=curr.get("from_year"),
                    notes=f"kage_succession:{v.get('id')}",
                ))
                n += 1
    return n


def bootstrap_social_network_from_canon(
    store: KnowledgeGraphStore,
    canon_dir: Path | str,
    *,
    clear_first: bool = True,
) -> dict[str, int]:
    """Construit le reseau social initial depuis les donnees canon disponibles.

    Strategies appliquees :
    - clans (membres < 8) : liens family
    - psycho_notes.allowed_relations : parse mot-cle -> link_type
    - kage_lineage : liens mentor entre kages successifs

    Retourne stats : {clans, psycho, kage_lineage, total}.
    """
    canon = Path(canon_dir)
    social = SocialNetwork(store.conn)
    if clear_first:
        social.clear_all()

    chars_path = canon / "characters.json"
    villages_path = canon / "villages.json"
    psycho_path = canon / "psycho_notes.json"

    characters = json.loads(chars_path.read_text(encoding="utf-8")) if chars_path.exists() else []
    villages = json.loads(villages_path.read_text(encoding="utf-8")) if villages_path.exists() else []
    psycho = json.loads(psycho_path.read_text(encoding="utf-8")) if psycho_path.exists() else {}

    char_ids = {c["id"] for c in characters if c.get("id")}

    stats = {
        "clans": derive_links_from_clans(social, characters),
        "psycho": derive_links_from_psycho_notes(social, psycho, char_ids),
        "kage_lineage": derive_links_from_kage_lineage(social, villages),
    }
    stats["total"] = social.count()
    logger.info("kg_social_bootstrap", **stats)
    return stats


def bootstrap_canon_beliefs(
    store: KnowledgeGraphStore, *, clear_first: bool = True,
) -> dict[str, int]:
    """Pour chaque fact canon (canonicity='canon_strict'), enregistre un belief
    par NPC implique (subject ou object si entity).

    Approche minimaliste : on ne propage qu'aux NPCs DIRECTEMENT mentionnes
    par le fact. Le belief propagator etendra plus tard.
    """
    propagator = BeliefPropagator(store.conn)
    if clear_first:
        propagator.clear_all()

    facts = store.get_facts(canonicity="canon_strict", limit=None)
    inserted = 0
    for f in facts:
        # NPC implique = subject + object (si object_type=entity)
        npcs = {f.subject}
        if f.object_type.value == "entity" and f.object:
            npcs.add(f.object)
        for npc in npcs:
            # Ne stocker que si le NPC est lui-meme un character (autres types ignorables)
            type_facts = store.get_facts(subject=npc, relation="type", limit=1)
            if not type_facts or type_facts[0].object != "character":
                continue
            propagator.add_belief(Belief(
                fact_id=f.id,  # type: ignore[arg-type]
                npc_id=npc,
                fidelity=1.0,
                learned_at_year=f.valid_from_year,
                learned_via_channel="canon_default",
            ))
            inserted += 1
    return {"beliefs_inserted": inserted}


__all__ = [
    "bootstrap_canon_beliefs",
    "bootstrap_social_network_from_canon",
]
