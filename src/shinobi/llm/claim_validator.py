"""Validateur deterministe des 'claims' d'une narration LLM.

Apres generation, on extrait les paires (NPC_X, action_sociale, NPC_Y) du texte
narratif + observations + dialogues, et on verifie chaque paire contre les
forbidden_relations declarees dans les fact sheets.

Ne fait AUCUN appel LLM (deterministe, ms). Premier filet anti-incoherence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from shinobi.canon.fact_sheet import PRIMARY_NPC_NAMES, _psycho_entry_at
from shinobi.canon.models import CanonBundle


@dataclass(frozen=True)
class ClaimViolation:
    """Une violation detectee dans la narration."""

    type: str  # forbidden_relation, anachronism, contradiction
    description: str
    involved_npcs: tuple[str, ...]


# Verbes / patterns d'interaction sociale a surveiller : si on trouve
# "Naruto verbe Konohamaru", on extrait la paire (Naruto, Konohamaru).
# Les patterns acceptent toutes les conjugaisons (infinitif, present, imparfait, etc.)
_SOCIAL_VERBS = [
    r"jou(?:e|ent|er|ait|aient|es|ons|ez)\s*(?:avec|ensemble|a)",
    r"discut(?:e|ent|er|ait|aient|es|ons|ez)\s+avec",
    r"parl(?:e|ent|er|ait|aient|es|ons|ez)\s+(?:a|avec)",
    r"ecout(?:e|ent|er|ait|aient|es|ons|ez)\b",
    r"regard(?:e|ent|er|ait|aient|es|ons|ez)\b",
    r"rejoin(?:t|s|drait|dre|dront|dre)\b",
    r"accompagn(?:e|ent|er|ait|aient|es|ons|ez)\b",
    r"sour(?:it|ient|ire|iait|iaient|is|ions|iez)\s+a",
    r"salu(?:e|ent|er|ait|aient|es|ons|ez)\b",
    r"appel(?:le|lent|er|lait|laient|les|ons|ez)\b",
    r"se\s+confie\s+a", r"explique[rnts]?\s+a", r"montre[rnts]?\s+a",
    r"propos(?:e|ent|er|ait|aient|es|ons|ez)\s+a",
    r"demand(?:e|ent|er|ait|aient|es|ons|ez)\s+a",
    r"defi(?:e|ent|er|ait|aient|es|ons|ez)\b",
    r"affront(?:e|ent|er|ait|aient|es|ons|ez)\b",
    r"combat(?:s|tre|tu|tons|ent)?\b",
    r"(?:s[\'’ ])?entrain(?:e|ent|er|ait|aient|es|ons|ez)\s+avec",
    # 'ami(e) (proche/de) (avec/de)' tres permissif
    r"(?:est|sont|reste|restent)\s+(?:un|une|de\s+bons?|des|des?\s+(?:tres\s+)?bons?)?\s*(?:ami|amie|amis|amies|copain|copine|allies|allies)\s*(?:proche|proches|de|avec|du|d')?",
    r"\b(?:ami|amie|amis|amies|copain|copine)\s+(?:proche|de|avec|du|d')\b",
    r"l[\'’ ]ami(?:e|s|es)?\s+de\b",
    r"\bdes\s+amis\s+comme\b",
    r"donn(?:e|ent|er|ait|aient|es|ons|ez)\s+(?:une|la)\s+main\s+a",
    r"prend\s+(?:la\s+)?main\s+de",
    r"embrass(?:e|ent|er|ait|aient|es|ons|ez)\b",
    r"prend\s+dans\s+ses\s+bras",
    r"intimid(?:e|ent|er|ait|aient|es|ons|ez)\b",
    r"menac(?:e|ent|er|ait|aient|es|ons|ez)\b",
    # Coordination : 'NPC1 et NPC2' suivi d'un verbe d'interaction
    r"\bet\b",  # naive : 'Naruto et Konohamaru' / 'Sakura et Sasuke' = paire implicite
]


def _build_npc_alias_map() -> dict[str, str]:
    """Mappe les noms usuels (lowercase) vers les ids canon. Reutilise PRIMARY_NPC_NAMES."""
    return dict(PRIMARY_NPC_NAMES)


def _resolve_npc_id(token: str, alias_map: dict[str, str]) -> str | None:
    """Convertit un token court (ex: 'naruto') en id canon principal."""
    return alias_map.get(token.lower())


def _scan_npc_pairs(text: str, alias_map: dict[str, str]) -> set[tuple[str, str]]:
    """Trouve toutes les paires (NPC_X, NPC_Y) en interaction sociale dans le texte.

    Une paire est ordonnee : (X, Y) signifie X interagit avec Y.
    Match heuristique : 'X verbe_social Y' avec X et Y dans alias_map.
    """
    if not text:
        return set()
    lower = text.lower()
    pairs: set[tuple[str, str]] = set()
    # Patterns : (npc_x) (verbe) (npc_y)
    # On compose une regex qui capture deux NPCs avec un verbe social entre eux,
    # avec une fenetre de jusqu'a 60 chars entre les deux noms.
    npc_alt = "|".join(re.escape(n) for n in sorted(alias_map.keys(), key=len, reverse=True))
    if not npc_alt:
        return pairs
    verbs_alt = "|".join(_SOCIAL_VERBS)
    pattern = re.compile(
        rf"\b({npc_alt})\b[^.!?\n]*?\b(?:{verbs_alt})\b[^.!?\n]*?\b({npc_alt})\b",
        re.IGNORECASE,
    )
    for m in pattern.finditer(lower):
        x_token = m.group(1).lower()
        y_token = m.group(2).lower()
        x_id = _resolve_npc_id(x_token, alias_map)
        y_id = _resolve_npc_id(y_token, alias_map)
        if x_id and y_id and x_id != y_id:
            pairs.add((x_id, y_id))
    return pairs


def _check_forbidden_pair(
    canon: CanonBundle, x_id: str, y_id: str, current_year: int
) -> str | None:
    """Verifie si la paire (x_id, y_id) viole les forbidden_relations canon.

    Retourne une raison textuelle si violation, None si OK.
    """
    x = canon.characters.get(x_id)
    y = canon.characters.get(y_id)
    if x is None or y is None:
        return None
    x_age = current_year - x.birth_year if x.birth_year is not None else None
    y_age = current_year - y.birth_year if y.birth_year is not None else None
    # Check du cote de X
    if x_age is not None:
        x_entry = _psycho_entry_at(x_id, x_age)
        if x_entry:
            forbidden = x_entry.get("forbidden_relations") or []
            for forb in forbidden:
                if y_id in forb.lower() or (y.name_romaji and y.name_romaji.lower() in forb.lower()):
                    return (
                        f"{x_id} (age {x_age}) en interaction sociale avec {y_id} : "
                        f"interdit canoniquement [{forb}]"
                    )
    # Check du cote de Y (symetrique)
    if y_age is not None:
        y_entry = _psycho_entry_at(y_id, y_age)
        if y_entry:
            forbidden = y_entry.get("forbidden_relations") or []
            for forb in forbidden:
                if x_id in forb.lower() or (x.name_romaji and x.name_romaji.lower() in forb.lower()):
                    return (
                        f"{y_id} (age {y_age}) en interaction sociale avec {x_id} : "
                        f"interdit canoniquement [{forb}]"
                    )
    return None


# Patterns pour detecter "X, age N ans" ou "X, N ans" ou "X est un ninja de N ans"
_AGE_NEAR_NPC = re.compile(
    r"\b(?P<name>[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?)\b"
    r"[^.!?\n]{0,80}?"
    r"\b(?:age\s+de\s+|de\s+|agé\s+de\s+|âgé\s+de\s+|qui\s+a\s+|de\s+ses\s+)"
    r"(?P<age>\d{1,3})\s*(?:ans|annees|année)\b",
    re.IGNORECASE,
)
_NINJA_OF_N = re.compile(
    r"\b(?P<name>[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?)\s*,?\s*"
    r"(?:un|une|jeune)\s+(?:ninja|shinobi|enfant|garçon|fille)\s+"
    r"de\s+(?P<age>\d{1,3})\s*ans",
    re.IGNORECASE,
)


# Liste de NPCs cites en serie : 'des amis comme X, Y, Z et W' / 'avec X, Y et Z'
_COORDINATION_LIST = re.compile(
    r"(?:amis?|allies|compagnons|coequipiers|camarades)\s+"
    r"(?:comme|tels?\s+que|incluant|notamment|sont|dont|:|avec)?\s*"
    r"((?:[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?(?:\s*[,;]\s*|\s+et\s+)){1,5}"
    r"[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?)",
)


def _scan_coordination_friends(
    canon: CanonBundle,
    text: str,
    current_year: int,
    alias_map: dict[str, str],
) -> list[ClaimViolation]:
    """Detecte 'des amis comme X, Y, Z et W' : produit une paire pour chaque NPC liste."""
    out: list[ClaimViolation] = []
    if not text:
        return out
    for m in _COORDINATION_LIST.finditer(text):
        chunk = m.group(1)
        names = re.split(r"\s*,\s*|\s+et\s+", chunk)
        listed_ids: list[str] = []
        for name in names:
            n = name.strip().lower()
            cid = alias_map.get(n)
            if cid is None and " " in n:
                cid = alias_map.get(n.split()[0])
            if cid:
                listed_ids.append(cid)
        # Pour chaque paire dans la liste, check forbidden_relations
        for i, x_id in enumerate(listed_ids):
            for y_id in listed_ids[i + 1 :]:
                reason = _check_forbidden_pair(canon, x_id, y_id, current_year)
                if reason:
                    out.append(ClaimViolation(
                        type="forbidden_relation",
                        description=f"[liste-amis] {reason}",
                        involved_npcs=(x_id, y_id),
                    ))
                # Aussi : si l'un d'eux a une psycho_note "sans amis" a cet age,
                # toute mention d'amis est suspecte
        # Check additionel : si le contexte du match contient un NPC avec
        # psycho note "sans amis", flag comme violation
        for npc_id in listed_ids:
            from shinobi.canon.fact_sheet import _psycho_entry_at

            char = canon.characters.get(npc_id)
            if char is None or char.birth_year is None:
                continue
            age = current_year - char.birth_year
            entry = _psycho_entry_at(npc_id, age)
            if entry:
                note = (entry.get("note") or "").lower()
                if "pas d'amis" in note or "sans ami" in note or "ostracise" in note:
                    out.append(ClaimViolation(
                        type="forbidden_relation",
                        description=(
                            f"{npc_id} (age {age}) listee dans une enumeration d'amis "
                            "alors que sa fact sheet dit explicitement qu'il/elle n'a "
                            "pas d'amis a cet age (ostracise/sans ami)."
                        ),
                        involved_npcs=(npc_id,),
                    ))
    return out


# Roles/titres canon : 'X, le 5e Hokage' / 'X, sensei de Y' / 'X, l'Anbu'
_ROLE_PATTERNS = [
    (re.compile(
        r"\b(?P<name>[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?)\s*,?\s*"
        r"(?:la?|le)?\s*(?P<rank>premier|deuxieme|troisieme|quatrieme|cinquieme|sixieme|septieme|huitieme"
        r"|1er|2[ee]me|3[ee]me|4[ee]me|5[ee]me|6[ee]me|7[ee]me|8[ee]me)\s+"
        r"(?P<role>Hokage|Kazekage|Mizukage|Raikage|Tsuchikage|Otokage)\b",
        re.IGNORECASE,
    ), "kage"),
]


# Mapping role canon -> annee d'investiture (debut) -> annee de fin
_KAGE_TIMELINE: dict[str, list[tuple[int, int, int | None]]] = {
    # role -> [(num_kage, from_year, to_year)]
    "hokage": [
        (1, -100, -40),  # Hashirama Senju (Premier Hokage)
        (2, -40, -20),   # Tobirama Senju (Deuxieme Hokage)
        (3, -20, 12),    # Hiruzen Sarutobi (Troisieme, mort an 12)
        (4, -5, 0),      # Minato Namikaze (Quatrieme, mort an 0)
        (3, 0, 12),      # Hiruzen reprend apres la mort de Minato
        (5, 12, 17),     # Tsunade (Cinquieme Hokage des l'an 12)
        (6, 17, 30),     # Kakashi (Sixieme apres 4e guerre)
        (7, 30, 9999),   # Naruto (Septieme)
    ],
}

_RANK_TO_NUM = {
    "premier": 1, "1er": 1,
    "deuxieme": 2, "2eme": 2, "2ème": 2,
    "troisieme": 3, "3eme": 3, "3ème": 3,
    "quatrieme": 4, "4eme": 4, "4ème": 4,
    "cinquieme": 5, "5eme": 5, "5ème": 5,
    "sixieme": 6, "6eme": 6, "6ème": 6,
    "septieme": 7, "7eme": 7, "7ème": 7,
    "huitieme": 8, "8eme": 8, "8ème": 8,
}


def _scan_role_anachronisms(text: str, current_year: int) -> list[ClaimViolation]:
    """Detecte 'X, la 5e Hokage' alors qu'a current_year ce n'est pas le bon kage."""
    out: list[ClaimViolation] = []
    if not text:
        return out
    for pattern, _kind in _ROLE_PATTERNS:
        for m in pattern.finditer(text):
            rank_str = m.group("rank").lower()
            role = m.group("role").lower()
            name = m.group("name")
            num = _RANK_TO_NUM.get(rank_str)
            if num is None:
                continue
            timeline = _KAGE_TIMELINE.get(role)
            if not timeline:
                continue
            # Quel num_kage est en fonction a current_year ?
            active_num = None
            for kage_num, from_y, to_y in timeline:
                if from_y <= current_year and (to_y is None or current_year < to_y):
                    active_num = kage_num
                    break
            if active_num is not None and num != active_num:
                out.append(ClaimViolation(
                    type="anachronism",
                    description=(
                        f"'{name}, le {rank_str} {role.capitalize()}' en l'an {current_year} "
                        f"est un anachronisme : le {active_num}e {role.capitalize()} est en fonction "
                        f"a cette date, pas le {num}e."
                    ),
                    involved_npcs=(),
                ))
    return out


def _scan_age_mismatches(
    canon: CanonBundle,
    text: str,
    current_year: int,
    alias_map: dict[str, str],
) -> list[ClaimViolation]:
    """Detecte les phrases du type 'Naruto, age de 10 ans' contradictoires
    avec le fact sheet canon (qui dirait 6 ans en l'an 6)."""
    if not text or current_year is None:
        return []
    out: list[ClaimViolation] = []
    seen_pairs: set[tuple[str, int]] = set()
    for pattern in (_AGE_NEAR_NPC, _NINJA_OF_N):
        for m in pattern.finditer(text):
            name_token = m.group("name").lower().strip()
            try:
                claimed_age = int(m.group("age"))
            except ValueError:
                continue
            # Resout le nom vers un id canonique via PRIMARY_NPC_NAMES
            cid = alias_map.get(name_token)
            if cid is None:
                # Essai sur premier mot seulement (prenom)
                first = name_token.split()[0] if " " in name_token else name_token
                cid = alias_map.get(first)
            if cid is None:
                continue
            char = canon.characters.get(cid)
            if char is None or char.birth_year is None:
                continue
            true_age = current_year - char.birth_year
            if (cid, claimed_age) in seen_pairs:
                continue
            seen_pairs.add((cid, claimed_age))
            if abs(true_age - claimed_age) >= 2:  # tolerance 1 an
                out.append(ClaimViolation(
                    type="wrong_age",
                    description=(
                        f"{cid} a {true_age} ans en l'an {current_year} "
                        f"(canon), mais la narration dit {claimed_age} ans."
                    ),
                    involved_npcs=(cid,),
                ))
    return out


def _scan_invented_player_friendships(
    text: str,
    *,
    player_name: str | None,
    established_npc_friend_ids: set[str],
    alias_map: dict[str, str],
) -> list[ClaimViolation]:
    """Detecte 'X est ami(e) (proche) de <player_name>' / '<player_name> est ami avec X'
    alors que X n'a PAS de relation amicale etablie dans character.relationships."""
    if not text or not player_name:
        return []
    out: list[ClaimViolation] = []
    player_first = player_name.split()[0]
    # Patterns familier joueur : 'X, ami proche d'Endo' / 'Endo est ami avec X' / 'salua chaleureusement'
    patterns = [
        # 'X, (qui est) (un/une) ami(e) (proche/de/d') (de) Endo'
        re.compile(
            rf"\b(?P<name>[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?)"
            rf"[^.!?\n]{{0,40}}?"
            rf"\bami(?:e|s|es)?\s+(?:proche\s+)?(?:de|d['’]|avec)\s*(?:{re.escape(player_first)}|{re.escape(player_name)})",
            re.IGNORECASE,
        ),
        # 'Endo, ami (proche/de) X' / 'Endo est ami avec X'
        re.compile(
            rf"\b(?:{re.escape(player_first)}|{re.escape(player_name)})"
            rf"[^.!?\n]{{0,40}}?"
            rf"\bami(?:e|s|es)?\s+(?:proche\s+)?(?:avec|de|d['’])\s+"
            rf"(?P<name>[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?)",
            re.IGNORECASE,
        ),
        # 'X salue/accueille Endo chaleureusement' (familier inattendu)
        re.compile(
            rf"\b(?P<name>[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?)\s+(?:salu(?:e|a|er)|accueill(?:e|i|ir))\s+"
            rf"(?:{re.escape(player_first)}|{re.escape(player_name)})\s+chaleureusement",
            re.IGNORECASE,
        ),
        # 'X et Endo (sont) (de bons) amis'
        re.compile(
            rf"\b(?P<name>[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?)\s+et\s+"
            rf"(?:{re.escape(player_first)}|{re.escape(player_name)})"
            rf"[^.!?\n]{{0,30}}?\bami(?:e|s|es)?\b",
            re.IGNORECASE,
        ),
    ]
    seen: set[str] = set()
    for pattern in patterns:
        for m in pattern.finditer(text):
            name_token = m.group("name").lower().strip()
            cid = alias_map.get(name_token)
            if cid is None and " " in name_token:
                cid = alias_map.get(name_token.split()[0])
            if cid is None or cid in seen:
                continue
            if cid in established_npc_friend_ids:
                continue  # relation deja etablie, OK
            seen.add(cid)
            out.append(ClaimViolation(
                type="forbidden_relation",
                description=(
                    f"La narration affirme que {cid} est ami(e) (proche) de {player_name} "
                    f"alors que cette relation n'a PAS ete etablie en jeu (le joueur vient "
                    f"juste de le rencontrer ou ne l'a jamais vu). Les amities se construisent."
                ),
                involved_npcs=(cid,),
            ))
    return out


def validate_narration_claims(
    canon: CanonBundle,
    *,
    narrative: str,
    observations: list[str],
    npc_dialogue: list[dict],
    proposed_actions: list[dict],
    current_year: int,
    player_name: str | None = None,
    established_friendships: set[str] | None = None,
) -> list[ClaimViolation]:
    """Scan complet de la sortie LLM pour detecter les violations canon.

    Combine narrative + observations + dialogues + labels d'actions.
    Retourne la liste des violations (vide si tout est OK).
    """
    alias_map = _build_npc_alias_map()
    # Reunit tout le texte a scanner
    all_texts = [narrative]
    all_texts.extend(observations or [])
    for d in npc_dialogue or []:
        line = d.get("line", "")
        if line:
            all_texts.append(line)
    for a in proposed_actions or []:
        label = a.get("label_fr", "") or a.get("label", "")
        if label:
            all_texts.append(label)

    violations: list[ClaimViolation] = []
    seen: set[tuple[str, str, str]] = set()  # deduplique
    friends_set = established_friendships or set()
    for text in all_texts:
        pairs = _scan_npc_pairs(text, alias_map)
        for x_id, y_id in pairs:
            key = ("forbidden_relation", x_id, y_id)
            if key in seen:
                continue
            reason = _check_forbidden_pair(canon, x_id, y_id, current_year)
            if reason:
                seen.add(key)
                violations.append(
                    ClaimViolation(
                        type="forbidden_relation",
                        description=reason,
                        involved_npcs=(x_id, y_id),
                    )
                )
        # Detection contradictions d'age
        violations.extend(_scan_age_mismatches(canon, text, current_year, alias_map))
        # Detection coordination 'amis comme X, Y, Z'
        violations.extend(_scan_coordination_friends(canon, text, current_year, alias_map))
        # Detection role anachronique 'X, le 5e Hokage'
        violations.extend(_scan_role_anachronisms(text, current_year))
        # Detection amitie inventee joueur-NPC
        violations.extend(_scan_invented_player_friendships(
            text, player_name=player_name, established_npc_friend_ids=friends_set,
            alias_map=alias_map,
        ))
    return violations


def format_violations_for_retry(violations: list[ClaimViolation]) -> str:
    """Formate les violations pour les injecter dans le prompt de retry."""
    if not violations:
        return ""
    lines = ["Ta narration precedente contient les VIOLATIONS CANON suivantes :"]
    for v in violations:
        lines.append(f"  - [{v.type}] {v.description}")
    lines.append(
        "\nReformule la narration en respectant STRICTEMENT les FAITS CANONIQUES NPC. "
        "N'inclus AUCUNE des paires NPC interdites. Si une scene devient impossible, "
        "remplace-la par une narration solitaire ou un PNJ generique (sensei_academie, "
        "marchand_taverne, etc.)."
    )
    return "\n".join(lines)
