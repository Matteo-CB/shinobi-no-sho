"""Phase G — Director / Drama Manager.

Spec doc 02 §7 : auteur invisible qui oriente le monde emergent vers
"narrativement interessant et Naruto-esque". Pas de prescription d'event ;
nudges via contexte LLM uniquement.

Composants :
- types.py        : AbstractAct, NarrativeInvariant, NudgeContext, DirectorReport
- invariants.py   : 9 invariants Naruto (5 centraux + 4 secondaires)
- act_composer.py : TensionList -> list[AbstractAct] (deterministe)
- nudge_builder.py: NudgeContext -> string prompt LLM
- compactor.py    : NarrativeCompactor (LLM ou offline fallback) NexusSum-style
- scheduler.py    : DirectorState, is_compaction_due
- core.py         : Director (orchestrator central)

Usage typique (boucle CLI) :

    from shinobi.director import Director, DirectorState, build_nudge_text
    director = Director(canon, llm_client=llm)
    state = DirectorState()
    # ... in tick loop ...
    report = await director.tick(
        tensions=tension_scheduler_output.tensions,
        world=world,
        state=state,
        current_year=world.current_year,
    )
    if report.nudge is not None:
        nudge_text = build_nudge_text(report.nudge)
        # passer nudge_text au narrator/agents prompt LLM
"""

from __future__ import annotations

from shinobi.director.act_composer import compose_acts, merge_with_existing
from shinobi.director.compactor import (
    DEFAULT_COMPACTION_INTERVAL_MONTHS,
    NarrativeCompactor,
)
from shinobi.director.core import Director
from shinobi.director.invariants import (
    NARUTO_INVARIANTS,
    NARUTO_INVARIANTS_CENTRAL,
    NARUTO_INVARIANTS_SECONDARY,
    select_relevant_invariants,
)
from shinobi.director.nudge_builder import (
    build_director_nudge_text,
    build_nudge,
    build_nudge_text,
)
from shinobi.director.scheduler import DirectorState, is_compaction_due
from shinobi.director.types import (
    AbstractAct,
    DirectorReport,
    NarrativeInvariant,
    NudgeContext,
)

__all__ = [
    "DEFAULT_COMPACTION_INTERVAL_MONTHS",
    "AbstractAct",
    "Director",
    "DirectorReport",
    "DirectorState",
    "NARUTO_INVARIANTS",
    "NARUTO_INVARIANTS_CENTRAL",
    "NARUTO_INVARIANTS_SECONDARY",
    "NarrativeCompactor",
    "NarrativeInvariant",
    "NudgeContext",
    "build_director_nudge_text",
    "build_nudge",
    "build_nudge_text",
    "compose_acts",
    "is_compaction_due",
    "merge_with_existing",
    "select_relevant_invariants",
]
