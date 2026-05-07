"""Types Phase G : Director / Drama Manager.

Spec doc 02 §7 :
- AbstractAct : direction narrative haute-niveau (StoryVerse 2024).
  Exemple : "Tension Konoha-Suna doit s'elever vers conflit ouvert dans
  les 6 prochains mois". Le Director compose des actes abstraits ; les
  agents (couche 3) decident comment les incarner.
- NarrativeInvariant : motif recurrent Naruto-esque qui survit a la
  divergence joueur ("Le pouvoir s'accompagne d'un cout", "Les liens
  humains transforment plus que la force"). Pas prescriptif : passe en
  contexte au LLM createur comme style guide.
- NudgeContext : payload assemble pour le narrator LLM. Acts + invariants
  actifs + summary recent (compaction NexusSum 2025).
- DirectorReport : sortie d'un tick Director.
- DirectorState : etat persistant entre ticks (acts actifs, derniere
  compaction).

Distinction critique avec Phase F WorldResolver :
- Phase F resout un event canon annule (reactif).
- Phase G compose des directions narratives a long terme (proactif).
- Phase F injecte des SubstituteEvent structures dans le scheduler.
- Phase G injecte des nudges dans le contexte LLM, JAMAIS d'events.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Round G29 : single source of truth pour les bornes year/month Phase G.
# Avant : R G18 dans types.py (Pydantic constraints), R G21 dans core.py
# (_YEAR_MIN/MAX), R G22 dans scheduler.py (_YEAR_MIN/MAX, _MONTH_MIN/MAX).
# Triple definition -> drift possible. Maintenant : declare ici, importe.
YEAR_MIN: int = -10000
YEAR_MAX: int = 10000
MONTH_MIN: int = 1
MONTH_MAX: int = 12


class AbstractAct(BaseModel):
    """Acte abstrait StoryVerse : direction narrative non-prescriptive.

    Le Director ne dit PAS "char X doit faire Y". Il dit "tension Z doit
    monter dans les N prochains mois". Les agents et le narrator decident
    comment l'incarner.

    Distinction avec SubstituteEvent (Phase F) : un acte abstrait n'est
    PAS scheduled, il n'a pas de preconditions/outcomes engine, il ne
    declenche aucune fact KG. C'est un guide narratif, pas un event.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(..., min_length=1, pattern=r"^act_[a-z0-9_]+$")
    description_fr: str = Field(..., min_length=10, max_length=500)
    # Tension types qui ont inspire cet acte (cf shinobi.tension.types).
    # On stocke comme strings pour eviter le couplage cyclique.
    related_tension_types: list[str] = Field(default_factory=list, max_length=10)
    involved_entities: list[str] = Field(default_factory=list, max_length=20)
    # Fenetre temporelle de manifestation (year inclusif).
    # Round G18 : bornes elargies a [YEAR_MIN, YEAR_MAX] (etait [-1000, 200]
    # qui clonaient Phase F canon range). Phase G est forward-looking ;
    # ses directives ne doivent pas etre contraintes par le range canon.
    # Si world.current_year=300 (extension future / alternate timeline),
    # compose_acts crashait a la construction de AbstractAct -> Director
    # degradait silencieusement (0 acts produits). Bornes preservees pour
    # catcher les typos extremes (year=99999) mais permissives.
    # Round G29 : utilise YEAR_MIN/MAX module-level constants (ex 3 endroits).
    target_year_start: int = Field(..., ge=YEAR_MIN, le=YEAR_MAX)
    target_year_end: int = Field(..., ge=YEAR_MIN, le=YEAR_MAX)
    # Round G31 : granularite mois pour vraie differenciation severity.
    # Avant : target_year_end seul -> R G16 collapsait critical (3 mois)
    # et high (6 mois) au meme target_year=current+0. Maintenant :
    # target_month_start/end permet 'critical = 3 mois' sans changer year.
    # Default month=1 pour back-compat (les acts pre-G31 charges depuis
    # save legacy ont month=1).
    target_month_start: int = Field(default=MONTH_MIN, ge=MONTH_MIN, le=MONTH_MAX)
    target_month_end: int = Field(default=MONTH_MAX, ge=MONTH_MIN, le=MONTH_MAX)
    urgency: float = Field(default=0.5, ge=0.0, le=1.0)
    # Snapshot des descriptions des tensions sources (debug + traceability).
    source_tension_descriptions: list[str] = Field(
        default_factory=list, max_length=10,
    )
    status: Literal["proposed", "active", "fulfilled", "expired"] = "proposed"
    created_at_year: int = Field(..., ge=YEAR_MIN, le=YEAR_MAX)

    @field_validator("target_year_end")
    @classmethod
    def _check_year_range(cls, v: int, info: Any) -> int:
        start = info.data.get("target_year_start")
        if start is not None and v < start:
            raise ValueError(
                f"target_year_end={v} < target_year_start={start}"
            )
        return v

    @field_validator("target_month_end")
    @classmethod
    def _check_month_tuple_range(cls, v: int, info: Any) -> int:
        """Round G31 : (year_end, month_end) >= (year_start, month_start)
        tuple-wise. year_end > year_start OK quel que soit month_end.
        year_end == year_start exige month_end >= month_start.
        """
        year_start = info.data.get("target_year_start")
        year_end = info.data.get("target_year_end")
        month_start = info.data.get("target_month_start", MONTH_MIN)
        if year_start is not None and year_end is not None:
            if (year_end, v) < (year_start, month_start):
                raise ValueError(
                    f"(target_year_end={year_end}, target_month_end={v}) < "
                    f"(target_year_start={year_start}, "
                    f"target_month_start={month_start})"
                )
        return v

    @field_validator("involved_entities")
    @classmethod
    def _dedupe_involved_entities(cls, v: list[str]) -> list[str]:
        """Round G11 : dedupe avec ordre preserve (mirror R46 Phase F).

        Tension upstream peut produire des doublons (LLM analyst emphatic
        ou bug detector). Sans dedup, le template
        '{entities}'.join(involved_entities[:3]) produit 'konoha, konoha,
        suna' dans le nudge, le narrator LLM voit l'entite repetee et
        peut la mimer dans son output.
        """
        return list(dict.fromkeys(v))

    @field_validator("related_tension_types")
    @classmethod
    def _dedupe_related_tension_types(cls, v: list[str]) -> list[str]:
        """Round G11 : dedupe (R G5 collision peut accumuler des types).

        Apres l'escalation R G5, source_tension_descriptions accumulent.
        Si la meme tension reapparait avec le meme type, related_tension_types
        accumulerait sans dedup. Pas critique aujourd'hui (compose_acts
        n'append qu'1 type par act) mais lock pour future-proof.
        """
        return list(dict.fromkeys(v))


class NarrativeInvariant(BaseModel):
    """Motif recurrent Naruto-esque. Style guide pour le LLM createur.

    Spec §7.3 : ces invariants ne prescrivent PAS d'evenements. Ils sont
    passes en contexte au LLM quand il genere une narration, pour qu'il
    reste dans le ton.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(..., min_length=1, pattern=r"^invariant_[a-z0-9_]+$")
    principle_fr: str = Field(..., min_length=10, max_length=200)
    # Exemples canon qui illustrent ce principe (cite-able dans le nudge).
    examples_canon: list[str] = Field(default_factory=list, max_length=5)
    # Contextes ou cet invariant s'applique le plus fortement.
    # 'death', 'training', 'rivalry', 'hokage', 'jinchuuriki', 'clan', ...
    applies_to_contexts: list[str] = Field(default_factory=list, max_length=10)
    # Force du principe (1.0 = canon central, 0.5 = thematique secondaire).
    weight: float = Field(default=1.0, ge=0.0, le=1.0)


class NudgeContext(BaseModel):
    """Payload assemble par le Director pour les narrator/agents prompts.

    Le narrator LLM lit ce payload comme contexte 'directives narratives' :
    - acts_active : ce qui doit monter en tension cette periode
    - invariants : ton et motifs Naruto a respecter
    - recent_summary : compaction des N derniers mois (memoire long-terme)

    Spec §7.4 : sans recent_summary, le contexte LLM explose au-dela de
    ~100 turns. Compaction NexusSum periodique indispensable.
    """

    model_config = ConfigDict(frozen=True)

    active_acts: list[AbstractAct] = Field(default_factory=list, max_length=10)
    active_invariants: list[NarrativeInvariant] = Field(
        default_factory=list, max_length=10,
    )
    recent_summary: str | None = None
    # Round G18 : meme rationale que AbstractAct year bounds.
    composed_at_year: int = Field(..., ge=YEAR_MIN, le=YEAR_MAX)
    # Phase H wiring : narrative patterns (9.5) cites par le narrator pour
    # style. List de dicts pour eviter cyclic dependency avec phase_h
    # schemas. Cap a 3 patterns max (build_nudge_text ne lit que les 2
    # premiers de toute facon).
    narrative_patterns: list[dict] = Field(
        default_factory=list, max_length=3,
    )


class DirectorReport(BaseModel):
    """Resultat d'un tick Director. Ce qui a change ce tick."""

    model_config = ConfigDict(frozen=True)

    new_acts: list[AbstractAct] = Field(default_factory=list)
    retired_acts: list[AbstractAct] = Field(default_factory=list)  # expired/fulfilled
    active_acts: list[AbstractAct] = Field(default_factory=list)
    nudge: NudgeContext | None = None
    compaction_ran: bool = False
    compaction_summary: str | None = None
    tick_year: int
    tick_month: int = 1


__all__ = [
    "AbstractAct",
    "DirectorReport",
    "NarrativeInvariant",
    "NudgeContext",
]
