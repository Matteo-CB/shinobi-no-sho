"""HybridSubstituteValidator : validation hybride canon_strict + alternate_timeline.

Spec doc 02 §8.3 :
- Mode `canon_strict` : check triplet rigide (canonical_users de jutsu/event)
- Mode `alternate_timeline` : check assoupli, plausibilite contextuelle.

Le mode est decide par la pipeline en fonction du flag config + arc/year :
- Avant divergence majeure -> canon_strict
- Apres divergence majeure -> alternate_timeline
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from shinobi.canon.models import CanonBundle
from shinobi.engine.world import WorldState
from shinobi.kg.store import KnowledgeGraphStore
from shinobi.logging_setup import get_logger
from shinobi.world_resolver.types import (
    SubstituteEvent,
    ValidationMode,
    ValidationOutcome,
    ValidationReport,
)

# Round 34 : preconditions handled par engine.events.evaluate_precondition.
# Une precondition de type inconnu retourne True (fall-through) -> le LLM
# pense bloquer mais l'engine ignore. On whitelist ici pour catch ces cas
# avant injection. Synchronise avec engine.events.evaluate_precondition.
# Round 69 : map des params requis par chaque type. Sans ces params, l'engine
# evalue a False (perso None / event None) -> substitute jamais triggered ->
# cancel silencieux. Ce check force le LLM a fournir les bons params.
_PRECONDITION_REQUIRED_PARAMS: dict[str, tuple[str, ...]] = {
    "character_alive": ("character_id",),
    "no_event_triggered": ("event_id",),
    "clan_active": ("clan_id",),
    "jinchuuriki_held_by": ("beast", "jinchuuriki_id"),
}
_KNOWN_PRECONDITION_TYPES: frozenset[str] = frozenset(_PRECONDITION_REQUIRED_PARAMS)

# Round 38 : drift temporel max entre substitute.year et cancelled_event.year.
# 30 ans permet : substitute legerement avance (player intervient en amont) ou
# retarde (effet domino). Au-dela, c'est probablement une erreur du LLM.
_MAX_TEMPORAL_DRIFT: int = 30

# Round 44 : style guard. Le system prompt + CLAUDE.md interdisent tirets
# cadratins et emoji dans la voix narrative. Ni Pydantic ni le LLM (qui peut
# desobeir) ne l'enforcent. Le validator catch ces caracteres dans les champs
# narratifs avant injection KG / propagation rumeur.
# Round 62 : couvre toute la famille Unicode des dashes typographiques pour
# eviter qu'un LLM contourne R44 en utilisant un variant moins evident.
# - U+2012 figure dash, U+2013 en dash, U+2014 em dash, U+2015 horizontal bar
# - U+FE58 small em dash, U+FE63 small hyphen-minus (CJK compat)
# - U+FF0D fullwidth hyphen-minus (Japanese typography)
# - emoji ranges (couverture commune : Misc Symbols, Pictographs, Transport,
#   Regional, Misc Tech, Dingbats)
_FORBIDDEN_CHARS_PATTERN = (
    "[‒-―"  # figure/en/em dash + horizontal bar
    "﹘﹣－"  # CJK compat dashes + fullwidth
    "\U0001F300-\U0001F9FF"  # emoji ranges
    "☀-➿"  # misc symbols + dingbats
    "]"
)

logger = get_logger(__name__)


class HybridSubstituteValidator:
    """Valide un SubstituteEvent selon le mode (strict ou alternate).

    Spec doc 02 §8.3 :
    - canon_strict : refs canon strictes + perso non mort (canon DB)
    - alternate_timeline : KG-based plausibility (perso peut etre 'vivant'
      via fact divergent), runtime WorldState consulte si fourni.
    """

    def __init__(
        self,
        canon: CanonBundle,
        kg: KnowledgeGraphStore,
        *,
        enforce_phase_h_actor_overlap: bool = False,
    ) -> None:
        """Construit le validator.

        Phase H 9.1 wiring : `enforce_phase_h_actor_overlap` active la verif
        de coherence entre `substitute.involved_characters` et les protagonistes
        canon de `cancelled_canon_event_id` (extraits des preconditions
        structurees 9.1). Default False pour ne pas casser les tests legacy
        qui utilisaient `cancelled_canon_event_id` comme placeholder generique.
        Le pipeline production active explicitement (cf pipeline.py).
        """
        self.canon = canon
        self.kg = kg
        self.enforce_phase_h_actor_overlap = enforce_phase_h_actor_overlap
        # Phase H wiring 9.1 : pre-build deux index par event_id pour lookup
        # O(1) au lieu de scan a chaque validate(). Defensive : tolere absence
        # ou format inattendu.
        # - _enriched_invariants : narrative_invariants pour soft check de
        #   coherence narrative (logging only, pas de reject).
        # - _enriched_subjects : ensemble des entity_id (canon characters)
        #   apparaissant dans les preconditions structurees du canon event.
        #   Sert au _check_enriched_actor_overlap : si un substitute remplace
        #   un canon event mais n'implique AUCUN des protagonistes canoniques,
        #   c'est probablement une hallucination LLM.
        self._enriched_invariants: dict[str, list[str]] = {}
        self._enriched_subjects: dict[str, set[str]] = {}
        for eid, payload in (canon.timeline_events_enriched or {}).items():
            if not isinstance(payload, dict):
                continue
            invs = payload.get("narrative_invariants")
            if isinstance(invs, list):
                self._enriched_invariants[eid] = [
                    str(x) for x in invs if isinstance(x, str) and x
                ]
            subjects: set[str] = set()
            for fact in payload.get("preconditions", []) or []:
                if not isinstance(fact, dict):
                    continue
                key = fact.get("fact")
                if not isinstance(key, str) or "." not in key:
                    continue
                subj = key.split(".", 1)[0]
                # On ne garde que les subjects qui sont des characters canon
                # (les autres = villages, organisations, evenements, faits
                # abstraits comme 'humanity' ou 'shinju' qui n'apparaitraient
                # pas dans involved_characters).
                if subj in canon.characters:
                    subjects.add(subj)
            if subjects:
                self._enriched_subjects[eid] = subjects

        # Phase H 9.3 wiring : index faction_id -> set(member_ids) pour
        # validation que substitute concernant une faction implique au
        # moins 1 member canon (ex outcome avec clan_id=uchiha_clan + 0
        # involved_characters Uchiha = probable hallucination).
        self._faction_members: dict[str, set[str]] = {}
        for fac in (canon.political_forces or {}).get("factions", []):
            if not isinstance(fac, dict):
                continue
            fid = fac.get("id")
            members = fac.get("members")
            if (
                isinstance(fid, str) and fid
                and isinstance(members, list)
            ):
                self._faction_members[fid] = {
                    m for m in members if isinstance(m, str) and m
                }

    def validate(
        self,
        substitute: SubstituteEvent,
        *,
        mode: ValidationMode = ValidationMode.canon_strict,
        world: WorldState | None = None,
    ) -> ValidationReport:
        """Effectue la validation hybride.

        Tests communs aux 2 modes :
        - involved_characters existent dans le canon (pas inventes)
        - aucun personnage mort avant l'annee de l'event
        - location existe (si fournie)
        - year coherent (>= -1000, <= +50 typiquement)

        Mode canon_strict :
        - Toutes les references entity dans outcomes/preconditions sont
          dans le canon (pas de personnages/villes inventes)

        Mode alternate_timeline :
        - Plausibilite contextuelle : les outcomes doivent etre derivables
          de l'etat actuel du KG (qui est vivant, qui a quel role).
        """
        # Year coherent (commun aux 2 modes)
        if substitute.year < -1000 or substitute.year > 200:
            return ValidationReport(
                outcome=ValidationOutcome.invalid_temporal,
                mode=mode,
                is_valid=False,
                reason=f"year {substitute.year} hors plage [-1000, 200]",
            )

        # Round 38 : drift temporel max vs cancelled_event.year. Un substitut
        # remplace un event canon dans la timeline ; il doit se produire pres
        # du moment canonique. Avant : LLM pouvait placer substitute.year=200
        # alors que cancelled_event etait year=10 (190 ans de drift) -> event
        # quasi-jamais triggered dans la vie utile du jeu.
        cancelled_ev = self.canon.timeline_events.get(
            substitute.cancelled_canon_event_id,
        )
        if cancelled_ev is not None:
            drift = abs(substitute.year - cancelled_ev.year)
            if drift > _MAX_TEMPORAL_DRIFT:
                return ValidationReport(
                    outcome=ValidationOutcome.invalid_temporal,
                    mode=mode,
                    is_valid=False,
                    reason=(
                        f"drift temporel {drift} ans entre substitute.year="
                        f"{substitute.year} et cancelled_event.year="
                        f"{cancelled_ev.year} (max {_MAX_TEMPORAL_DRIFT})"
                    ),
                    failing_facts=[
                        f"substitute.year={substitute.year}",
                        f"cancelled_event.year={cancelled_ev.year}",
                        f"drift={drift} > max={_MAX_TEMPORAL_DRIFT}",
                    ],
                )

        # Round 44 : style guard - tirets cadratins / emoji interdits dans
        # les champs narratifs. Le system prompt l'interdit mais le LLM peut
        # desobeir ; les tirets cadratins polluent rumeurs et belief propagation.
        style_failure = self._check_narrative_style(substitute, mode)
        if style_failure is not None:
            return style_failure

        # Runtime WorldState check : si on a un world, refuser un perso
        # marque is_alive=False dans world.npc_states (sherlock-equivalent).
        # Spec §8.3 : valider la 'chaine d'evenements vecus' (= world runtime).
        if world is not None:
            world_failure = self._check_world_runtime(substitute, world, mode)
            if world_failure is not None:
                return world_failure

        # Personnages morts (canon DB) : check seulement en canon_strict.
        # En alternate_timeline, on delegue au KG-based check.
        if mode == ValidationMode.canon_strict:
            dead_failure = self._check_canon_deaths(substitute)
            if dead_failure is not None:
                return dead_failure

        if mode == ValidationMode.canon_strict:
            structural_report = self._validate_strict(substitute)
        else:
            structural_report = self._validate_alternate(substitute)

        # Phase H wiring 9.1 : si la validation structurelle passe, layer un
        # check de coherence avec les preconditions structurees du canon event.
        # On ne run ce check QU'APRES les checks structurels pour deux raisons :
        # 1) Une erreur structurelle (perso invente, triplet non-canon) est plus
        #    specifique et utile au LLM regenerator que "actor overlap".
        # 2) Le check overlap suppose involved_characters tous canon (sinon les
        #    erreurs de _validate_strict/alternate sur les inventions priment).
        if structural_report.is_valid and self.enforce_phase_h_actor_overlap:
            actor_failure = self._check_enriched_actor_overlap(substitute, mode)
            if actor_failure is not None:
                return actor_failure
            # Phase H 9.3 wiring : si un outcome cite un clan_id qui est dans
            # 9.3 political_forces, verifier qu'au moins 1 involved_character
            # est member canon de cette faction. Bloque les substituts qui
            # claim 'event impacte clan Uchiha' avec 0 Uchiha dans involved.
            faction_failure = self._check_faction_members_overlap(
                substitute, mode,
            )
            if faction_failure is not None:
                return faction_failure
        return structural_report

    # --- Phase H 9.1 enriched coherence check ----------------------------

    def _check_enriched_actor_overlap(
        self,
        substitute: SubstituteEvent,
        mode: ValidationMode,
    ) -> ValidationReport | None:
        """Check overlap entre involved_characters et subjects canoniques.

        Phase H 9.1 wiring : timeline_events_enriched fournit pour chaque
        event canon ses preconditions structurees (fact key = `<entity>.<prop>`).
        Les entites qui sont des characters canon definissent les protagonistes
        du canon event. Si le substitute n'inclut aucun de ces protagonistes,
        on a probablement une hallucination LLM : substitute qui parle d'un
        evenement different de celui qu'il pretend remplacer.

        Defensive :
        - Skip si pas d'enriched data pour cet event (90% du canon n'est pas
          encore enrichi en debut de Phase H).
        - Skip si subjects vide (event purement abstrait, ex kaguya_eats_fruit).
        - Skip si involved_characters vide (deja flag par autre check).
        - Le check log warning au lieu de reject pour le moment, pour ne pas
          bloquer les substitutes legitimes pendant que la couverture 9.1
          monte. R74 : passe a reject quand >80% du canon est enrichi.

        Round courant : reject seulement si overlap = 0 ET au moins 2 subjects
        canoniques (sinon trop de faux positifs sur les events a 1 acteur).
        """
        canon_subjects = self._enriched_subjects.get(
            substitute.cancelled_canon_event_id,
        )
        if not canon_subjects or len(canon_subjects) < 2:
            return None
        if not substitute.involved_characters:
            return None
        # Si AU MOINS un involved_character n'est pas dans canon, on laisse
        # _validate_strict / _validate_alternate emettre leur propre erreur
        # plus specifique (invalid_triplet en strict, invalid_plausibility en
        # alternate avec mention "perso invente"). Sinon on doublonne l'erreur
        # et le LLM en regen reçoit un message ambigu.
        if any(
            cid not in self.canon.characters
            for cid in substitute.involved_characters
        ):
            return None
        overlap = canon_subjects.intersection(substitute.involved_characters)
        if overlap:
            return None
        # 0 overlap entre N>=2 protagonistes canoniques et le substitute :
        # tres probable hallucination.
        logger.warning(
            "phase_h_9_1_no_actor_overlap",
            cancelled_event=substitute.cancelled_canon_event_id,
            canon_subjects=sorted(canon_subjects),
            substitute_involved=substitute.involved_characters,
        )
        return ValidationReport(
            outcome=ValidationOutcome.invalid_plausibility,
            mode=mode,
            is_valid=False,
            reason=(
                f"substitute pour {substitute.cancelled_canon_event_id} "
                f"n'implique aucun des protagonistes canoniques "
                f"(canon: {sorted(canon_subjects)}, "
                f"substitute: {substitute.involved_characters})"
            ),
            failing_facts=[
                f"canon_subjects={sorted(canon_subjects)}",
                f"substitute_involved={substitute.involved_characters}",
                "overlap=0",
            ],
        )

    # --- Phase H 9.3 faction members overlap -----------------------------

    def _check_faction_members_overlap(
        self,
        substitute: SubstituteEvent,
        mode: ValidationMode,
    ) -> ValidationReport | None:
        """Check : si un outcome cite un clan_id (ou faction equivalent),
        au moins 1 involved_character doit etre member canon de cette faction.

        Phase H 9.3 wiring : political_forces.factions[*].members fournit
        la liste canonique des membres. Sans ce check, le LLM produisait
        des substitutes 'event impacte clan_uchiha' avec 0 Uchiha dans
        involved_characters -> impossible canoniquement.

        Defensive :
        - Skip si pas d'outcome avec clan_id ou faction id en 9.3.
        - Skip si la faction n'a pas de members listes.
        - Skip si involved_characters vide (deja flag par autre check).
        """
        if not self._faction_members:
            return None
        if not substitute.involved_characters:
            return None

        # Collecte les faction_ids cites par les outcomes
        cited_factions: set[str] = set()
        for outcome in substitute.outcomes:
            for key in ("clan_id", "village_id", "organization_id", "org_id"):
                fid = outcome.parameters.get(key)
                if (
                    isinstance(fid, str) and fid
                    and fid in self._faction_members
                ):
                    cited_factions.add(fid)

        if not cited_factions:
            return None

        involved_set = set(substitute.involved_characters)
        for fid in cited_factions:
            members = self._faction_members.get(fid, set())
            if not members:
                continue
            if involved_set.intersection(members):
                continue  # OK : au moins 1 member dans involved
            # 0 overlap : faction citee sans aucun de ses members impliques
            return ValidationReport(
                outcome=ValidationOutcome.invalid_plausibility,
                mode=mode,
                is_valid=False,
                reason=(
                    f"substitute cite faction '{fid}' dans outcomes mais "
                    f"aucun de ses members canon n'est dans "
                    f"involved_characters"
                ),
                failing_facts=[
                    f"faction_id={fid}",
                    f"faction_members_sample="
                    f"{sorted(members)[:3]}",
                    f"substitute_involved={substitute.involved_characters}",
                ],
            )
        return None

    # --- style narratif (round 44) ---------------------------------------

    def _check_narrative_style(
        self,
        substitute: SubstituteEvent,
        mode: ValidationMode,
    ) -> ValidationReport | None:
        """Refuse tirets cadratins (—/–) et emoji dans les champs narratifs.

        Round 44 : CLAUDE.md + system prompt interdisent ces caracteres.
        Le LLM peut desobeir ; sans ce check, name_fr / narrative_summary_fr /
        rumor_template polluent les rumeurs + belief propagation NPC.
        """
        import re
        forbidden = re.compile(_FORBIDDEN_CHARS_PATTERN)
        violations: list[str] = []
        for field_name in ("name_fr", "narrative_summary_fr", "rumor_template"):
            value = getattr(substitute, field_name, None)
            if not value:
                continue
            match = forbidden.search(value)
            if match is not None:
                # Localise le caractere fautif pour le feedback regen
                ch = match.group(0)
                violations.append(
                    f"{field_name} contient char interdit U+{ord(ch):04X} "
                    f"(em/en dash ou emoji)"
                )
        if violations:
            return ValidationReport(
                # Round 45 : invalid_style distinct de invalid_schema pour que
                # le feedback regen pointe le bon probleme.
                outcome=ValidationOutcome.invalid_style,
                mode=mode,
                is_valid=False,
                reason=(
                    f"{len(violations)} violation(s) de style narratif "
                    f"(tirets cadratins / emoji interdits)"
                ),
                failing_facts=violations,
            )
        return None

    # --- WorldState runtime ----------------------------------------------

    def _check_world_runtime(
        self,
        substitute: SubstituteEvent,
        world: WorldState,
        mode: ValidationMode,
    ) -> ValidationReport | None:
        """Sherlock-equivalent sur le WorldState runtime.

        Si world.npc_states[cid].is_alive == False, refuse meme si canon
        canonical disait vivant (le runtime a evolue). Les NPCs absents de
        npc_states sont laisses passer (canon-fallback).

        Round 12 : le `mode` est propage au ValidationReport pour traceability
        (avant fix, hardcoded `canon_strict`).
        Round 27 : on batch TOUS les morts au lieu de retourner au premier,
        pour que le feedback de regen LLM les liste tous d'un coup au lieu
        de les decouvrir un par un sur 3 regens.
        """
        dead: list[str] = []
        for cid in substitute.involved_characters:
            npc = world.npc_states.get(cid)
            if npc is not None and not npc.is_alive:
                dead.append(cid)
        if dead:
            return ValidationReport(
                outcome=ValidationOutcome.invalid_dead_character,
                mode=mode,
                is_valid=False,
                reason=f"{len(dead)} perso(s) marque(s) mort(s) dans WorldState runtime",
                failing_facts=[
                    f"world.npc_states[{cid}].is_alive=False" for cid in dead
                ],
            )
        return None

    # --- communs ----------------------------------------------------------

    def _check_canon_deaths(
        self, substitute: SubstituteEvent,
    ) -> ValidationReport | None:
        """Mode canon_strict : refuse si un perso canon est mort avant year
        OU pas encore ne (birth_year > year).

        Round 27 : batch tous les morts au lieu d'early-return. Avant, si
        3 persos etaient morts, seul le 1er etait reporte au LLM ; sur regen,
        le 2eme apparaissait, fix, 3eme... 3 regens brules au lieu d'1.
        Round 70 : check aussi birth_year (perso pas encore ne). Boruto
        avec birth_year=16 apparaissant dans un substitute year=8 passait
        silencieusement avant. Symetrique du death check.
        """
        invalid: list[str] = []
        for cid in substitute.involved_characters:
            char = self.canon.characters.get(cid)
            if char is None:
                continue  # tolere si pas dans canon (pourrait etre alternate)
            if char.death_year is not None and char.death_year < substitute.year:
                invalid.append(f"{cid}.death_year={char.death_year}")
            if char.birth_year is not None and char.birth_year > substitute.year:
                invalid.append(
                    f"{cid}.birth_year={char.birth_year} (pas encore ne)"
                )
        if invalid:
            return ValidationReport(
                outcome=ValidationOutcome.invalid_dead_character,
                mode=ValidationMode.canon_strict,
                is_valid=False,
                reason=(
                    f"{len(invalid)} perso(s) canon temporellement invalide(s) "
                    f"a year={substitute.year}"
                ),
                failing_facts=invalid,
            )
        return None

    # --- precondition params check (R69) ---------------------------------

    @staticmethod
    def _missing_precondition_params(
        pre: "SubstitutePrecondition",
    ) -> list[str]:
        """Retourne les params requis manquants (ou empty/non-string).

        Round 69 : sans ces params, evaluate_precondition.get(key) retourne
        None, le lookup canon.X.get(None) retourne None, return False ->
        precondition jamais satisfaite -> substitute jamais trigger.
        """
        required = _PRECONDITION_REQUIRED_PARAMS.get(pre.type, ())
        missing: list[str] = []
        for key in required:
            value = pre.parameters.get(key)
            if not (isinstance(value, str) and value.strip()):
                missing.append(key)
        return missing

    # --- triplet check (R17 + R50 + R56) ---------------------------------

    @staticmethod
    def _iter_outcome_powers(params: dict[str, Any]) -> Iterator[str]:
        """Yield tous les power_id presents dans les outcome parameters.

        Round 56 : avant, _validate_strict ne lisait que `technique_id` ou
        `power` (singulier). Le canon utilise aussi `techniques: list[str]`
        (cf naruto_training_with_jiraiya). Sans cet helper, un LLM pouvait
        cacher un jutsu invente dans la liste a cote de jutsu reels.
        Round 60 : dedup. LLM peut produire technique_id=power='rasengan'
        et techniques=['rasengan'] -> 3 yields identiques -> failing_facts
        avec 3 entrees doublons (pollue le feedback regen).
        """
        seen: set[str] = set()
        for key in ("technique_id", "power"):
            value = params.get(key)
            if isinstance(value, str) and value and value not in seen:
                seen.add(value)
                yield value
        techs = params.get("techniques")
        if isinstance(techs, list):
            for t in techs:
                if isinstance(t, str) and t and t not in seen:
                    seen.add(t)
                    yield t



    def _check_triplet(
        self,
        *,
        cid: str,
        power_id: str,
        outcome_type: str,
        kg_fallback: bool = False,
        year: int | None = None,
    ) -> str | None:
        """Triplet check (character, power). Retourne raison-failure ou None.

        Round 17 : technique vs canonical_users.
        Round 50 : etend a Kekkei Genkai (Character.kekkei_genkai) et Hiden
        (Character.clan == HidenTechnique.owning_clan).
        Round 53 : kekkei_mora.
        Round 54 : pouvoir non-canon = reject (etait skip silencieux).
        Round 55 : kg_fallback=True (mode alternate) : accepte un pouvoir
        introduit en KG par player_action (post-divergence). Sans ce flag,
        alternate mode rejetait aussi les jutsu legitimement post-divergence.
        Round 63 : si cid n'est pas dans canon, on skip - l'entity-check
        precedent (R33/R41/R47) a deja flag le perso invente. Sinon le LLM
        recoit 2 messages pour la meme cause racine ("fix le perso" ET
        "fix le triplet") -> regen perdue en confusion.
        """
        char = self.canon.characters.get(cid)
        if char is None:
            return None
        # Round 72 : un power peut etre dans plusieurs taxonomies canon
        # (ex daikokuten = technique + kekkei_mora ; tenseigan = kekkei_genkai
        # + kekkei_mora). Avant : first-match -> false positive si la 1ere
        # taxo dit "non" mais une autre dirait "oui". Maintenant : OR-logic,
        # accept si AU MOINS une taxo valide le triplet.
        in_any_taxo = False
        details: list[str] = []
        if power_id in self.canon.techniques:
            in_any_taxo = True
            tech = self.canon.techniques[power_id]
            if cid in tech.canonical_users:
                return None
            details.append(
                f"non dans canonical_users (sample={list(tech.canonical_users)[:3]})"
            )
        if power_id in self.canon.kekkei_genkai:
            in_any_taxo = True
            if power_id in char.kekkei_genkai:
                return None
            details.append(
                f"perso.kekkei_genkai={char.kekkei_genkai[:3]} ne contient pas ce kekkei_genkai"
            )
        if power_id in self.canon.kekkei_mora:
            in_any_taxo = True
            if power_id in char.kekkei_mora:
                return None
            details.append(
                f"perso.kekkei_mora={char.kekkei_mora[:3]} ne contient pas ce kekkei_mora"
            )
        if power_id in self.canon.hiden:
            in_any_taxo = True
            hiden = self.canon.hiden[power_id]
            char_clan = char.clan
            if (
                hiden.owning_clan is None
                or char_clan == hiden.owning_clan
                or hiden.shareable_outside_clan
            ):
                return None
            details.append(
                f"hiden owning_clan={hiden.owning_clan}, char clan={char_clan} "
                f"(shareable={hiden.shareable_outside_clan})"
            )
        if in_any_taxo:
            return (
                f"outcome:{outcome_type} triplet ({cid}, {power_id}) "
                f"non canon dans aucune taxonomie : {' ; '.join(details)}"
            )
        # Round 54 : power_id n'est dans AUCUNE taxonomie canon
        # (techniques / kekkei_genkai / kekkei_mora / hiden) -> pouvoir
        # invente. Avant : skip silencieux -> hallucination jutsu passait.
        # Round 55 : en mode alternate (kg_fallback=True), tolere si KG
        # contient un fact `(power_id, type, X)` indiquant un jutsu introduit
        # post-divergence par player_action.
        if kg_fallback:
            for kg_type in ("technique", "kekkei_genkai", "kekkei_mora", "hiden"):
                if self._kg_entity_exists(power_id, kg_type, year=year):
                    return None
        return (
            f"outcome:{outcome_type} power={power_id} pas dans canon "
            f"(ni techniques, ni kekkei_genkai, ni kekkei_mora, ni hiden)"
        )

    # --- canon_strict ------------------------------------------------------

    def _validate_strict(
        self, substitute: SubstituteEvent,
    ) -> ValidationReport:
        """Mode strict : toutes les refs entity doivent etre dans canon."""
        failing: list[str] = []
        for involved in substitute.involved_characters:
            if involved not in self.canon.characters:
                failing.append(f"involved_character:{involved} pas dans canon")

        # Outcomes : verifier toutes les references entity contre canon.
        # Round 41 : char/village/org/location.
        # Round 47 : etendu - clan, beast, jinchuuriki, era, sensei, new_kage
        # (cf canon load_canon timeline_events outcome param keys).
        # `org_id` est un alias canon de `organization_id`.
        for outcome in substitute.outcomes:
            for key, canon_set in (
                ("character_id", self.canon.characters),
                ("village_id", self.canon.villages),
                ("organization_id", self.canon.organizations),
                ("org_id", self.canon.organizations),
                ("clan_id", self.canon.clans),
                ("beast", self.canon.tailed_beasts),
                ("jinchuuriki_id", self.canon.characters),
                ("era_id", self.canon.eras),
                ("sensei", self.canon.characters),
                ("new_kage", self.canon.characters),
            ):
                value = outcome.parameters.get(key)
                if (
                    value and isinstance(value, str)
                    and value not in canon_set
                ):
                    failing.append(
                        f"outcome:{outcome.type} {key}={value} pas dans canon"
                    )
            # location_id : peut etre village OR location
            loc_id = outcome.parameters.get("location_id")
            if (
                loc_id and isinstance(loc_id, str)
                and loc_id not in self.canon.locations
                and loc_id not in self.canon.villages
            ):
                failing.append(
                    f"outcome:{outcome.type} location_id={loc_id} pas dans canon"
                )
            # Spec §8.3 round 17 : triplet_check sur (character, technique).
            # Exemple spec : '(itachi_vivant, rasengan)' rejete car Itachi
            # pas dans canonical_users de Rasengan. S'applique aux outcomes
            # 'character_acquired_power' / 'character_trained' avec
            # parameters incluant un technique_id.
            # Round 47 : recharge cid depuis parameters (refactor de la
            # boucle entity-check au-dessus l'a fait sortir du scope).
            # Round 50 : etend le triplet check aux Kekkei Genkai et Hiden.
            # Avant, `power=sharingan` pour un non-Uchiha passait car sharingan
            # n'est pas dans canon.techniques (c'est un kekkei_genkai). Maintenant,
            # check dans techniques OU kekkei_genkai OU hiden avec le bon
            # rapport canonique (canonical_users / kekkei_genkai sur Character /
            # owning_clan).
            # Round 56 : check aussi `techniques: list[str]` (pluriel) utilise
            # par character_trained (e.g. naruto_training_with_jiraiya =
            # ['rasengan', 'kuchiyose_toad']). Avant : liste ignoree, LLM pouvait
            # injecter un jutsu invente parmi des reels.
            triplet_cid = outcome.parameters.get("character_id")
            for tech_id in self._iter_outcome_powers(outcome.parameters):
                if triplet_cid and isinstance(triplet_cid, str):
                    triplet_failure = self._check_triplet(
                        cid=triplet_cid, power_id=tech_id,
                        outcome_type=outcome.type,
                    )
                    if triplet_failure:
                        failing.append(triplet_failure)

        # Location
        if substitute.location:
            if (
                substitute.location not in self.canon.locations
                and substitute.location not in self.canon.villages
            ):
                failing.append(
                    f"location:{substitute.location} pas dans canon"
                )

        # Round 33 : valide aussi les references entity dans les preconditions.
        # Avant : seuls les outcomes etaient checkes ; un precondition avec
        # `character_id="ghost_invente"` passait, le substitute s'injectait,
        # puis evaluate_precondition retournait False (perso inconnu) et le
        # substitute ne triggerait jamais -> cancel silencieux. Pipeline
        # rapportait status='injected' a tort.
        # Round 34 : whitelist le precondition.type. evaluate_precondition
        # retourne True (!) pour les types inconnus -> LLM pense bloquer, engine
        # ignore. Le substitute triggerait quand meme. Inversion semantique.
        # Round 69 : check les params requis par chaque type.
        for pre in substitute.preconditions:
            if pre.type and pre.type not in _KNOWN_PRECONDITION_TYPES:
                failing.append(
                    f"precondition:{pre.type} type non gere par l'engine "
                    f"(types valides: {sorted(_KNOWN_PRECONDITION_TYPES)})"
                )
            else:
                missing = self._missing_precondition_params(pre)
                if missing:
                    failing.append(
                        f"precondition:{pre.type} manque params requis: "
                        f"{missing} (sinon engine evalue toujours False)"
                    )
            cid = pre.parameters.get("character_id")
            if cid and isinstance(cid, str) and cid not in self.canon.characters:
                failing.append(
                    f"precondition:{pre.type} character_id={cid} pas dans canon"
                )
            clan_id = pre.parameters.get("clan_id")
            if (
                clan_id and isinstance(clan_id, str)
                and clan_id not in self.canon.clans
            ):
                failing.append(
                    f"precondition:{pre.type} clan_id={clan_id} pas dans canon"
                )
            event_id = pre.parameters.get("event_id")
            # event_id peut pointer un canon event OU un substitute prealablement
            # injecte. On accepte les deux.
            if (
                event_id and isinstance(event_id, str)
                and event_id not in self.canon.timeline_events
                and not event_id.startswith("substitute_")
            ):
                failing.append(
                    f"precondition:{pre.type} event_id={event_id} pas dans canon"
                )
            # Round 41 : jinchuuriki_held_by precondition prend `beast` et
            # `jinchuuriki_id` (cf engine.events.evaluate_precondition).
            # `beast` doit etre dans canon.tailed_beasts ; jinchuuriki_id
            # est un character_id.
            beast_id = pre.parameters.get("beast")
            if (
                beast_id and isinstance(beast_id, str)
                and beast_id not in self.canon.tailed_beasts
            ):
                failing.append(
                    f"precondition:{pre.type} beast={beast_id} pas dans canon"
                )
            jinch_id = pre.parameters.get("jinchuuriki_id")
            if (
                jinch_id and isinstance(jinch_id, str)
                and jinch_id not in self.canon.characters
            ):
                failing.append(
                    f"precondition:{pre.type} jinchuuriki_id={jinch_id} pas dans canon"
                )

        if failing:
            return ValidationReport(
                outcome=ValidationOutcome.invalid_triplet,
                mode=ValidationMode.canon_strict,
                is_valid=False,
                reason=f"{len(failing)} refs canon non resolues",
                failing_facts=failing,
            )
        return ValidationReport(
            outcome=ValidationOutcome.valid,
            mode=ValidationMode.canon_strict,
            is_valid=True,
        )

    # --- alternate_timeline -----------------------------------------------

    def _kg_entity_exists(
        self, eid: str, kg_type: str, *, year: int | None = None,
    ) -> bool:
        """True si KG contient `(eid, type, kg_type)` (post-divergence entity).

        Round 42 : helper pour _validate_alternate. Permet d'accepter une
        entity introduite par player action (ex: enfant ne post-divergence,
        nouveau village fonde) sans etre dans canon.
        Round 67 : filtre par year. Sans ca, un player_action qui fonde un
        village en year=50 (`valid_from_year=50`) etait reconnu pour un
        substitute en year=10 -> temporal paradox (village pas encore existant).
        Canon type facts n'ont pas valid_from_year (always active) donc le
        filtre les laisse passer.
        """
        return bool(self.kg.get_facts(
            subject=eid, relation="type",
            object_value=kg_type, year=year, limit=1,
        ))

    def _validate_alternate(
        self, substitute: SubstituteEvent,
    ) -> ValidationReport:
        """Mode alternate : plausibilite via KG.

        Spec §8.3 : 'l'autre validation : plausibilite par mecaniques canon'.
        Implementation :
        - Personnages canon doivent exister (pas d'invention !) - meme en
          alternate mode, on ne tolere pas qu'un perso invente apparaisse.
        - Mort canon : tolere SI fact divergent (joueur a sauve)
        - Sinon : check via KG.
        Round 42 : mirror les checks entity de _validate_strict (round 33/41)
        avec fallback KG : un perso/village/etc invente n'est plus tolere.
        """
        failing: list[str] = []
        # Check existence : tolere la fois canon ET KG (un perso peut etre
        # ajoute en KG sans etre canon, ex: enfant ne post-divergence)
        for cid in substitute.involved_characters:
            in_canon = cid in self.canon.characters
            if not in_canon and not self._kg_entity_exists(
                cid, "character", year=substitute.year,
            ):
                failing.append(
                    f"{cid} ni dans canon, ni dans KG (perso invente)"
                )

        # Round 42 : entity refs dans outcomes (canon OR KG).
        # Round 48 : etendu pour mirror R47 strict (clan_id, beast,
        # jinchuuriki_id, era_id, sensei, new_kage, org_id).
        for outcome in substitute.outcomes:
            for key, kg_type, canon_set in (
                ("character_id", "character", self.canon.characters),
                ("village_id", "village", self.canon.villages),
                ("organization_id", "organization", self.canon.organizations),
                ("org_id", "organization", self.canon.organizations),
                ("clan_id", "clan", self.canon.clans),
                ("beast", "tailed_beast", self.canon.tailed_beasts),
                ("jinchuuriki_id", "character", self.canon.characters),
                ("era_id", "era", self.canon.eras),
                ("sensei", "character", self.canon.characters),
                ("new_kage", "character", self.canon.characters),
            ):
                eid = outcome.parameters.get(key)
                if not (eid and isinstance(eid, str)):
                    continue
                if eid in canon_set:
                    continue
                if self._kg_entity_exists(eid, kg_type, year=substitute.year):
                    continue
                failing.append(
                    f"outcome:{outcome.type} {key}={eid} ni canon ni KG"
                )
            # location_id peut etre village OR location
            loc_id = outcome.parameters.get("location_id")
            if (
                loc_id and isinstance(loc_id, str)
                and loc_id not in self.canon.locations
                and loc_id not in self.canon.villages
                and not self._kg_entity_exists(loc_id, "location", year=substitute.year)
                and not self._kg_entity_exists(loc_id, "village", year=substitute.year)
            ):
                failing.append(
                    f"outcome:{outcome.type} location_id={loc_id} ni canon ni KG"
                )
            # Round 51 : triplet check (cid, power) mirror R50 strict.
            # Round 55 : kg_fallback=True permet d'accepter un pouvoir
            # introduit en KG post-divergence (cas d'usage alternate).
            # Round 56 : itere `techniques: list[str]` aussi.
            triplet_cid = outcome.parameters.get("character_id")
            for tech_id in self._iter_outcome_powers(outcome.parameters):
                if triplet_cid and isinstance(triplet_cid, str):
                    triplet_failure = self._check_triplet(
                        cid=triplet_cid, power_id=tech_id,
                        outcome_type=outcome.type,
                        kg_fallback=True,
                        year=substitute.year,
                    )
                    if triplet_failure:
                        failing.append(triplet_failure)

        # Round 65 : mirror le check substitute.location de R47 strict, avec
        # KG fallback. Avant, alternate ne checkait pas le location -> un
        # `location='atlantis'` passait silencieusement en alternate (auto
        # active via R29 select_validation_mode).
        if substitute.location:
            loc = substitute.location
            if (
                loc not in self.canon.locations
                and loc not in self.canon.villages
                and not self._kg_entity_exists(loc, "location", year=substitute.year)
                and not self._kg_entity_exists(loc, "village", year=substitute.year)
            ):
                failing.append(
                    f"location:{loc} ni canon ni KG"
                )

        # Round 42 : entity refs dans preconditions (canon OR KG)
        # Round 58 : whitelist `pre.type` aussi (mirror R34 strict). Sans ca,
        # alternate (qui kicks in tres vite via R29) acceptait silencieusement
        # un precondition.type='weather_is_sunny' que l'engine
        # evaluate_precondition retourne True par fall-through.
        for pre in substitute.preconditions:
            if pre.type and pre.type not in _KNOWN_PRECONDITION_TYPES:
                failing.append(
                    f"precondition:{pre.type} type non gere par l'engine "
                    f"(types valides: {sorted(_KNOWN_PRECONDITION_TYPES)})"
                )
            else:
                # Round 69 : params requis aussi enforce en alternate.
                missing = self._missing_precondition_params(pre)
                if missing:
                    failing.append(
                        f"precondition:{pre.type} manque params requis: "
                        f"{missing} (sinon engine evalue toujours False)"
                    )
            for key, kg_type, canon_set in (
                ("character_id", "character", self.canon.characters),
                ("clan_id", "clan", self.canon.clans),
                ("jinchuuriki_id", "character", self.canon.characters),
                ("beast", "tailed_beast", self.canon.tailed_beasts),
            ):
                eid = pre.parameters.get(key)
                if not (eid and isinstance(eid, str)):
                    continue
                if eid in canon_set:
                    continue
                if self._kg_entity_exists(eid, kg_type, year=substitute.year):
                    continue
                failing.append(
                    f"precondition:{pre.type} {key}={eid} ni canon ni KG"
                )

        if failing:
            return ValidationReport(
                outcome=ValidationOutcome.invalid_plausibility,
                mode=ValidationMode.alternate_timeline,
                is_valid=False,
                reason=f"{len(failing)} perso invente(s) (regle 'pas d'invention')",
                failing_facts=failing,
            )

        # Spec §8.3 : alternate mode respecte canon SAUF si une divergence
        # explicite est enregistree en KG. Verifie d'abord canon death_year.
        # Round 64 : avant, n'importe quel fact divergent death_year cancelait
        # la mort canon, meme si la valeur du fact disait aussi "mort en X".
        # Maintenant on parse la valeur : un divergent death_year >= year
        # (ou non parseable - sentinel "alive") cancel ; sinon, mort confirmee
        # en branche -> reject.
        for cid in substitute.involved_characters:
            char = self.canon.characters.get(cid)
            if char is None or char.death_year is None:
                continue
            if char.death_year >= substitute.year:
                continue  # mort canon apres l'event = OK
            # Mort canon avant l'event -> on cherche un fact divergent qui annule
            divergent_death = [
                f for f in self.kg.get_facts(
                    subject=cid, relation="death_year",
                )
                if f.canonicity.value == "divergent"
            ]
            if not divergent_death:
                # Mort canon non-annulee par divergence -> reject
                failing.append(
                    f"{cid} mort canon en {char.death_year}, "
                    f"pas de divergence KG enregistree"
                )
                continue
            # Round 64 : check si la divergence elle-meme dit "mort avant year"
            divergent_dies_before = False
            for f in divergent_death:
                if f.object is None or not str(f.object).strip():
                    # sentinel non-parseable -> on assume alive
                    continue
                try:
                    div_dy = int(f.object)
                    if div_dy <= substitute.year:
                        divergent_dies_before = True
                        break
                except (ValueError, TypeError):
                    continue  # non-numeric -> skip
            if divergent_dies_before:
                failing.append(
                    f"{cid} mort canon en {char.death_year}, "
                    f"divergence KG dit aussi mort avant year={substitute.year}"
                )

        # Round 71 : symetrique birth check en alternate. Mirror R70 strict.
        # Tolere si KG a un divergent birth_year qui avance la naissance.
        for cid in substitute.involved_characters:
            char = self.canon.characters.get(cid)
            if char is None or char.birth_year is None:
                continue
            if char.birth_year <= substitute.year:
                continue  # canon naissance OK
            # Canon dit pas encore ne -> cherche divergent birth qui avance
            divergent_births = [
                f for f in self.kg.get_facts(
                    subject=cid, relation="birth_year",
                )
                if f.canonicity.value == "divergent"
            ]
            if not divergent_births:
                failing.append(
                    f"{cid} pas encore ne (canon birth={char.birth_year}, "
                    f"substitute year={substitute.year}), pas de divergence KG"
                )
                continue
            # Si divergent_birth > year aussi -> toujours pas ne en branche
            divergent_still_unborn = True
            for f in divergent_births:
                if f.object is None or not str(f.object).strip():
                    continue
                try:
                    div_by = int(f.object)
                    if div_by <= substitute.year:
                        divergent_still_unborn = False
                        break
                except (ValueError, TypeError):
                    continue
            if divergent_still_unborn:
                failing.append(
                    f"{cid} canon birth={char.birth_year}, divergence KG "
                    f"laisse encore non-ne a year={substitute.year}"
                )

        if failing:
            return ValidationReport(
                outcome=ValidationOutcome.invalid_plausibility,
                mode=ValidationMode.alternate_timeline,
                is_valid=False,
                reason=(
                    f"{len(failing)} contrainte(s) temporelle(s) "
                    f"(mort/naissance) non-annulee(s) par divergence"
                ),
                failing_facts=failing,
            )

        # Death checks via KG (avec exception divergent)
        for cid in substitute.involved_characters:
            # Cherche fact (cid, alive, ?) actif a year
            alive_facts = self.kg.get_facts(
                subject=cid, relation="alive", year=substitute.year,
            )
            death_facts = self.kg.get_facts(
                subject=cid, relation="death_year",
            )
            if death_facts:
                # Si death_year defini ET <= substitute.year, perso mort
                # ... sauf si la mort est un fact 'divergent' (annulee par joueur)
                non_divergent = [
                    f for f in death_facts
                    if f.canonicity.value != "divergent"
                ]
                if non_divergent:
                    # Round 24 : avant, `object or "0"` defaultait a 0 si le
                    # fact KG avait `object is None` (fact corrompu) -> tout
                    # year >= 0 declarait le perso mort. Maintenant on skip
                    # si l'object n'est pas un int parseable.
                    raw_obj = non_divergent[0].object
                    if raw_obj is not None and str(raw_obj).strip():
                        try:
                            death_year = int(raw_obj)
                            if death_year <= substitute.year:
                                failing.append(
                                    f"{cid} mort en {death_year} (KG canon)"
                                )
                                continue
                        except (ValueError, TypeError):
                            pass
            if not alive_facts:
                # Tolere : on suppose vivant si pas de death info
                pass

        if failing:
            return ValidationReport(
                outcome=ValidationOutcome.invalid_plausibility,
                mode=ValidationMode.alternate_timeline,
                is_valid=False,
                reason=f"{len(failing)} contraintes plausibilite KG",
                failing_facts=failing,
            )
        return ValidationReport(
            outcome=ValidationOutcome.valid,
            mode=ValidationMode.alternate_timeline,
            is_valid=True,
        )


__all__ = ["HybridSubstituteValidator"]
