"""Couche A : sherlock rules deterministes sur le state runtime.

Verifie :
1. Aucun PNJ canoniquement mort ou pas encore ne ne parle ni n'agit
2. Aucun PNJ enregistre dans world_state.characters_dead ne parle ni n'agit
3. Aucune scene ni action ne se deroule dans un lieu detruit
   (state.world_state.destroyed_locations)
4. Aucun PNJ dans deux lieux differents au sein de la meme sortie

Les PNJ inconnus du canon (roles generiques type 'marchand_taverne') sont
laisses passer : `get_canon_status` retourne `unknown` pour eux, et seuls
`dead` et `not_yet_born` declenchent un reject.
"""

from __future__ import annotations

from shinobi.state.age_calculator import CanonStatus, CanonView, get_canon_status
from shinobi.state.world_state import RuntimeState
from shinobi.validation.validator import (
    NarrativeOutput,
    ValidationResult,
)


class SherlockRulesLayer:
    """Couche A du validator (regles deterministes)."""

    name = "sherlock_rules"

    def validate(
        self,
        *,
        narrative_output: NarrativeOutput,
        state: RuntimeState,
        canon: CanonView,
    ) -> ValidationResult:
        details: list[str] = []
        year = state.narrative_time.approximate_year
        runtime_dead = {d.name.lower() for d in state.world_state.characters_dead}
        destroyed = {loc.lower() for loc in state.world_state.destroyed_locations}

        # 1 + 2. PNJ morts qui parlent
        for d in narrative_output.npc_dialogue:
            actor = d.character_id
            if not actor:
                continue
            reason = self._dead_reason(actor, year, canon, runtime_dead)
            if reason:
                details.append(f"Le PNJ {actor} parle alors qu'il est {reason}.")

        # 1 + 2. PNJ morts qui agissent
        for a in narrative_output.actions:
            actor = a.actor
            if not actor:
                continue
            reason = self._dead_reason(actor, year, canon, runtime_dead)
            if reason:
                action_label = a.type or "action"
                details.append(f"Le PNJ {actor} effectue une {action_label} alors qu'il est {reason}.")

        # 3. Lieu detruit
        scene_loc = state.scene_context.location
        if scene_loc and scene_loc.lower() in destroyed:
            details.append(
                f"La scène se déroule dans {scene_loc}, qui est détruit selon le state runtime."
            )
        for a in narrative_output.actions:
            if a.location and a.location.lower() in destroyed:
                details.append(
                    f"Une action est attribuée dans le lieu détruit {a.location}."
                )

        # 4. Ubiquite intra-sortie
        actor_locations: dict[str, set[str]] = {}
        for a in narrative_output.actions:
            if a.actor and a.location:
                actor_locations.setdefault(a.actor.lower(), set()).add(a.location.lower())
        for actor, locations in actor_locations.items():
            if len(locations) >= 2:
                joined = ", ".join(sorted(locations))
                details.append(
                    f"Le PNJ {actor} apparaît dans plusieurs lieux dans la même sortie : {joined}."
                )

        if details:
            return ValidationResult(
                is_valid=False,
                layer=self.name,
                reason=f"{len(details)} violation(s) detectée(s) par les sherlock rules.",
                details=details,
            )
        return ValidationResult(is_valid=True, layer=self.name)

    @staticmethod
    def _dead_reason(
        actor: str,
        year: int,
        canon: CanonView,
        runtime_dead: set[str],
    ) -> str | None:
        """Retourne une chaine descriptive si actor est mort, None sinon.

        Priorite a la divergence runtime, puis canon.
        """
        if actor.lower() in runtime_dead:
            return "mort dans le state runtime"
        status = get_canon_status(actor, year, canon)
        if status == CanonStatus.dead:
            return f"mort canoniquement avant l'an {year}"
        if status == CanonStatus.not_yet_born:
            return f"pas encore né en l'an {year}"
        return None
