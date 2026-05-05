# Timeline engine — roadmap d'implementation

Document de design pour l'evolution du moteur temporel apres les
piliers anti-hallucination. Ne livre PAS de code, juste la trajectoire
architecturale a respecter pour rester coherent avec ce qui existe deja.

A jour au 2026-05-05, post-Phase 1.

## 1. Vision

Le monde de Shinobi no Sho doit tourner autour du joueur sans lui. Le
joueur peut naitre en l'an 0 et rester passif : le canon se deroule
quand meme. S'il intervient (ex. tuer Itachi avant le massacre), le
canon doit recalculer ce qui suit, en cascade.

Trois piliers fondamentaux a respecter :

1. **Causalite stricte** : chaque evenement canon a des preconditions
   testables sur l'etat du monde. Si une precondition tombe, l'evenement
   ne se declenche pas (ou se declenche autrement).
2. **Determinisme inspectable** : le scheduler est purement deterministe,
   inspectable via SQL/CLI, reproduisible apres reload.
3. **Decouplage strict** entre scheduler (logique pure) et narration LLM
   (qui decrit les evenements au joueur). Le scheduler n'appelle JAMAIS
   le LLM.

## 2. Composants a livrer (ordre)

### Phase A — Scheduler MVP (pure Python, ~2 semaines)

`src/shinobi/engine/scheduler/`

```
scheduler.py        # boucle de tick, file d'evenements
event_evaluator.py  # evalue les preconditions sur world_state
outcome_applier.py  # applique les outcomes structures
event_log.py        # historique persistant en SQLite
```

**Input** : `data/canonical/timeline_events.json` (existant, 60 events)
**State source** : `RuntimeState` etendu avec `WorldStateData` complet
**Output** : `EventLog` SQLite avec triggered/cancelled/modified events

Tests adversariaux requis :
- 100% des events canon se declenchent dans une partie passive
- Tuer un perso prerequis annule la chaine d'events qui en dependent
- Re-loader un save reproduit exactement le meme state

### Phase B — KG dual canon + world (~1 semaine)

```
src/shinobi/engine/kg/
  kg_canon.py       # KG immuable du canon (Hashirama est mort)
  kg_world.py       # KG mutable de l'etat actuel (joueur a sauve Hashirama)
  divergence.py     # diff(canon, world) pour detecter les ecarts
```

Le KG canon est rempli au demarrage depuis les JSON canoniques. Le KG
world est une copie mutable. Le scheduler consulte `kg_world` pour ses
preconditions ; les rumeurs et le narrator LLM consultent les deux pour
formuler des descriptions du type "tu entends que dans une autre version
des choses, Hashirama serait deja mort".

Le KG est stocke en SQLite avec un schema:

```sql
CREATE TABLE kg_facts (
    id INTEGER PRIMARY KEY,
    subject TEXT NOT NULL,
    relation TEXT NOT NULL,
    object TEXT,
    object_type TEXT,        -- entity / value / null
    valid_from_year INTEGER,
    valid_to_year INTEGER,    -- NULL = en cours
    source TEXT,              -- canon | event_<id> | player_action
    canonicity TEXT,
    created_at_ts INTEGER
);
CREATE INDEX idx_kg_subject ON kg_facts(subject, relation);
CREATE INDEX idx_kg_valid ON kg_facts(valid_from_year, valid_to_year);
```

### Phase C — Propagation de contraintes (~2 semaines)

`src/shinobi/engine/scheduler/cascade.py`

Quand un event est `cancelled` ou `modified`, le scheduler doit propager :

- Tous les events qui dependaient de ses outcomes deviennent `at_risk`
- Le `world_resolver` LLM (existe deja en stub `src/shinobi/llm/narration.py`)
  est appele pour generer des substituts narratifs (rumeurs, reactions,
  events alternatifs)
- Les outcomes alternatifs sont ajoutes au KG world avec `source=alternate`

Algorithme propose (CSP forward-checking simplifie) :

```python
def propagate(cancelled_event, world_state):
    queue = [cancelled_event]
    visited = set()
    while queue:
        ev = queue.pop()
        if ev.id in visited:
            continue
        visited.add(ev.id)
        for next_ev in events_depending_on(ev):
            if violates_precondition(next_ev, world_state):
                next_ev.status = "cancelled"
                queue.append(next_ev)
            elif precondition_partial(next_ev, world_state):
                next_ev.status = "at_risk"
                # World resolver LLM appelle ici pour proposer une variante
```

Latence cible : < 100 ms par cascade dans un scenario typique (10 events
affectes max).

### Phase D — Behavior profiles per-perso (~1 semaine)

`data/canonical/behavior_profiles.json` (existe en partiel via
`voice_profiles.json` mais minimaliste)

Pour les 12-15 grands persos canon (Naruto, Sasuke, Sakura, Kakashi,
Itachi, Madara, Hashirama, Tobirama, Hiruzen, Tsunade, Jiraiya, Pain,
Obito, Minato, Kushina), profiler :

```json
{
  "id": "uchiha_itachi",
  "voice_signature": {
    "register": "formal_distant",
    "verbal_tics": ["foolish little brother"],
    "rare_word_freq": "high",
    "sentence_length_avg": 18,
    "emotion_baseline": "stoic_melancholic"
  },
  "decision_priors": {
    "loyalty_to": ["konohagakure", "uchiha_sasuke"],
    "secrecy_baseline": 0.95,
    "violence_threshold_for_kin": "spare_unless_no_choice",
    "violence_threshold_for_others": "kill_if_mission_requires"
  },
  "interaction_modifiers": {
    "with_uchiha_sasuke": "protective_facade_of_cruelty",
    "with_uchiha_madara": "respectful_distance",
    "with_sarutobi_hiruzen": "loyal_subordinate",
    "with_unknown_npc": "polite_minimal"
  },
  "evolution_by_arc": {
    "pre_massacre": "cold_strategist",
    "akatsuki_active": "tortured_double_agent",
    "post_resurrection_edo_tensei": "freed_truth_seeker"
  }
}
```

Branchement : le narrator LLM injecte le `voice_signature` + le
`interaction_modifiers` du perso avec qui le joueur parle, dans le
prompt. Le validator couche C (age_coherence) peut s'enrichir avec
`evolution_by_arc` pour tagger les regressions stylistiques.

### Phase E — Inference engine sur le KG (~3-4 semaines)

Au-dela de la propagation simple, on veut pouvoir resoudre des
questions du type "est-ce que Naruto sait que Itachi est mort ?".
Necessite :

- Tracking de `who_knows_what_when` dans le KG (relation
  `knows_about`)
- Propagation des news selon les rayons d'information
- Resolution causale en arriere ("comment en sommes-nous arrives la ?")

C'est le morceau le plus ambitieux et le plus risque. A ne demarrer
qu'apres validation de la phase D en jeu reel.

## 3. Integration avec ce qui existe

| Composant existant | Integration future |
|---|---|
| `RuntimeState` (pilier 4) | Etendre avec `world_state.kg_world_db_path` |
| `Validator A+B+C` (pilier 3+6B) | Couche C pourra utiliser behavior_profiles |
| `HybridSearcher` (pilier 8) | Filtrer par `narrative_year` cohere avec scheduler tick |
| `pass5_tag_chunks.py` (pilier 5) | Les arcs taggees alimentent le scheduler |
| `Narrator.narrate()` (pilier 6B) | Injection des voice_signatures + interaction_modifiers |
| `WorldResolver` (stub existant `src/shinobi/llm/narration.py:567`) | A activer sur cascade events |

## 4. Risques et mitigations

| Risque | Mitigation |
|---|---|
| Cascade infinie de propagations | `max_depth=10` dans cascade.py, fallback sur `cancelled_silent` |
| KG world divergent du KG canon trop vite | Periodic snapshot + diff report dans CLI |
| Behavior profiles trop figes | `evolution_by_arc` permet l'evolution. Fallback sur voice neutre si arc inconnu |
| Latence narrator > 700ms cible | KG en SQLite avec indices, queries sub-10ms |
| Reproductibilite saves | Test "save -> tick 100x -> load -> tick 100x : KG identique" |

## 5. Ordre de priorisation suggere

```
1. Phase A (Scheduler MVP) : debloque tout, tests faciles
2. Phase D (Behavior profiles 12-15 persos) : impact narratif immediat
3. Phase B (KG dual) : prereq de C, peut etre fait en parallele de D
4. Phase C (Propagation contraintes) : la valeur ajoutee principale
5. Phase E (Inference engine) : ambitieux, a evaluer selon experience reelle
```

Total estime : 2-3 mois si solo, 1-2 mois si parallelisable.

## 6. Pre-requis avant de demarrer

- Tests anti-hallu : 236/236 verts (PRESENT)
- Pipeline retrieval branche : OK (PRESENT)
- Pass 5 tagging temporel : OK (PRESENT)
- Behavior profiles existants minimalistes a etendre :
  `data/canonical/voice_profiles.json` (cf. CHANGELOG release 0.4)
- Spec docs/08_world_simulation.md a relire en complet avant phase A

## 7. Question ouverte

**Faut-il une couche de "narrative_compaction" ?** Au bout de N tours
in-game, le journal narratif devient trop long pour rentrer dans le
contexte du LLM. Une compaction structuree (resume par arc, conserve
les events `triggered` / `cancelled` cles) serait utile, mais c'est un
sous-projet a part. A discuter quand le scheduler fonctionne.

## 8. Document NON couvert ici

- Implementation concrete des methodes
- Tests adversariaux specifiques
- API publique du scheduler
- Schema SQLite final
- Configuration du tick rate

Tous ces details viendront avec la Phase A quand on demarrera. Le but
de ce document est de fixer la trajectoire et eviter les contradictions
architecturales avec l'existant.
