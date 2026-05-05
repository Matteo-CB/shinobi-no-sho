"""State tracker runtime (pilier 4 du plan anti-hallucination).

Distinct de `shinobi.engine.world` qui gere la simulation canonique complete.
Le state tracker capture une snapshot focalisee sur le tour courant pour le
pipeline anti-hallucination : narrative_time, player_character, world_state
divergent, scene_context, dialogue_history.

Modules :
- `age_calculator` : `get_age(name, year, canon)` deterministe pour eviter le
  drift d'un champ `age` stocke. Pas de behavior_profiles ici, ils viendront
  avec le pilier 3 (validator couche C).
- `world_state` : schema Pydantic du `RuntimeState`, qui implemente le Protocol
  `StateView` du resolver via duck typing (last_mentioned_character,
  present_characters, current_location).

Source de la convention temporelle : an 0 = naissance Naruto = attaque du
Kyuubi sur Konoha. Identique a la convention deja en place dans
`data/canonical/character_birth_years_patch.json`.
"""

from __future__ import annotations
