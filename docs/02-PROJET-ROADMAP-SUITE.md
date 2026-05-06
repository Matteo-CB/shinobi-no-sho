# Shinobi no Sho — Roadmap pour la suite

Document de passation pour la deuxième vague de développement.
Lis le doc 01 d'abord pour comprendre l'état actuel.

Ce document décrit comment faire passer le projet de "pipeline
narratif anti-hallucination" à "simulateur de vie créatif et
émergent dans l'univers Naruto".

---

## 0. Mise au point sur les coûts

**Le jeu tourne 100% en local.** Llama.cpp + Qwen3-4B GGUF + BGE-M3
CPU + ChromaDB persistent. **Zéro coût d'API par tour de jeu.**

Les coûts mentionnés ($25-40 d'extraction one-shot dans cette
roadmap) sont **uniquement** des batchs offline de préparation de
données. Le résultat (JSON, metadata) est ensuite consulté
localement pendant le jeu.

**La vraie contrainte au runtime : la latence per-turn sur le
hardware local.**

Avec 5-10 inférences LLM par tick (multi-agent + tension detector +
narrator + validator), à ~1-3 sec/inference Qwen3-4B CPU, on est
naïvement à 5-30 sec ressenti par turn. C'est ça le risque, pas le
$$.

Mitigations à mettre en place dès le départ (détaillées dans la
roadmap) :

- Caching agressif des inférences (hash prompt → output disque)
- Sampling top-5 PNJ dans le rayon, le reste en différé
- Distillation : petit modèle (Phi-3-mini, Qwen3-0.5B) pour les
  checks rapides, Qwen3-4B uniquement pour la narration
- GPU optionnel via llama.cpp (CUDA, Metal, Vulkan) pour les joueurs
  qui en ont
- Speculative decoding (Qwen3-4B + Qwen3-0.5B draft) si le hardware
  le permet : 1.5-2.5x sur le narrator
- Tick rate adaptatif : moins d'inférences en phase tranquille, plus
  en action critique

---

## 1. Vision créative émergente — rappel

Un monde où :

- La chronologie canon se déroule par défaut (tournée par le
  scheduler existant)
- Les actions du joueur peuvent annuler, modifier ou déclencher des
  événements canon
- Quand le canon est cassé, le système **génère de manière créative
  et cohérente** ce qui se passe à la place — sans qu'aucun dev n'ait
  pré-écrit ces alternatives
- Les personnalités, motivations, relations et trajectoires des PNJ
  s'adaptent à ce qu'ils ont vécu dans la branche actuelle
- Chaque partie est unique. Aucune répétition même avec actions
  similaires
- L'esprit du lore canon est préservé même dans les branches très
  éloignées

Cf. `research/world-simulation-gap-analysis.md` pour 6 scénarios
illustratifs détaillés (alliance Itachi+joueur expose Danzō, sauver
Rin change Obito, jouer contre Naruto pendant Pain, infiltrer
Akatsuki, fonder village dissident, épargner Zabuza).

---

## 2. Les 3 manques critiques à combler

Identifiés par l'audit, validés par la recherche.

### 2.1 Tension Detector
Le système actuel est purement réactif. Aucune émergence spontanée.
Sans tension detector, le monde ne crée jamais d'événements de son
propre chef.

### 2.2 Profile vectoriel PNJ + drift par events vécus
`NPCState.psychological_state` est un Literal à 4 valeurs. Trop
grossier. Sasuke-sans-massacre n'a aucune base structurelle pour
devenir un autre Sasuke.

### 2.3 Boucle créative fermée
`WorldResolver._world_resolve_cancellation` génère du texte narratif
mais ne crée pas de `TimelineEvent` structuré qui rentrerait dans le
scheduler. L'événement substitut reste un message au joueur, pas un
fait du monde simulé.

---

## 3. Architecture cible — 4 couches

```
┌───────────────────────────────────────────────────────────┐
│ Couche 4 : DIRECTOR (drama manager hybride)                │
│  Détecte tensions, oriente sans contraindre,               │
│  compose la dramaturgie globale                            │
└───────────────────┬───────────────────────────────────────┘
                    │
┌───────────────────▼───────────────────────────────────────┐
│ Couche 3 : MULTI-AGENT SIMULATION (top-15 PNJ)             │
│  Chaque PNJ majeur agit selon ses motivations,             │
│  sa mémoire, sa personnalité évolutive                     │
│  Inspiré Generative Agents (Park et al. Stanford 2023)    │
└───────────────────┬───────────────────────────────────────┘
                    │
┌───────────────────▼───────────────────────────────────────┐
│ Couche 2 : KNOWLEDGE GRAPH dynamique (canon + world)       │
│  Faits, relations, croyances, propagation d'info           │
│  Forces politiques, lignes de tension                      │
└───────────────────┬───────────────────────────────────────┘
                    │
┌───────────────────▼───────────────────────────────────────┐
│ Couche 1 : EXISTANT (engine + anti-hallu pipeline)         │
│  Scheduler, world_state, retrieval, validator              │
│  ✅ Déjà à 70% (3985 lignes engine + 7 piliers)            │
└───────────────────────────────────────────────────────────┘
```

La créativité émerge des interactions entre couches, pas d'un seul
module créatif.

---

## 4. État de l'art applicable (recherche 2024-2026)

Travaux récents qui valident l'approche :

**Generative Agents (Park et al, Stanford 2023)** — 25 PNJ avec
mémoire + réflexion + planning produisent du comportement émergent
crédible. Code open-source. Notre référence directe pour la couche 3.

**StoryVerse (2024)** — architecture "abstract acts" qui médie entre
intention auteur et émergence multi-agent. Pertinent pour garder
l'esprit Naruto sans brider la créativité.

**Drama Llama (2025)** — storylets + LLM avec triggers en langage
naturel. Mode hybride contrôle auteur + émergence. Modèle pour la
couche 4 Director.

**Co-DIRECT (2025)** — ontology-driven KG + Writer/Actor/Critic
agents. Combine constraint et créativité. Pattern pour le KG de
couche 2.

**StoryBox (2025)** — multi-agent simulation hybride bottom-up génère
stories cohérentes >10 000 mots. Confirme la viabilité long-terme.

**Memory in LLM-MAS (survey 2025)** — mémoire épisodique + réflexive
+ hiérarchique indispensable pour cohérence longue durée.

---

## 5. Composant 1 — Knowledge Graph dynamique (Couche 2)

### 5.1 Schéma SQLite

Un graphe RDF-like où chaque fait est un triplet `(subject, relation,
object)` avec timestamps et provenance.

```sql
CREATE TABLE kg_facts (
    id INTEGER PRIMARY KEY,
    subject TEXT NOT NULL,
    relation TEXT NOT NULL,
    object TEXT,
    object_type TEXT,        -- entity / value / belief
    valid_from_year INTEGER,
    valid_to_year INTEGER,
    source TEXT,              -- canon | event_<id> | player_action | inferred
    confidence REAL,           -- 0.0 - 1.0
    canonicity TEXT,           -- canon_strict | canon_modified | divergent
    known_by_npc_ids TEXT,    -- JSON array
    created_at_ts INTEGER
);
CREATE INDEX idx_kg_subject ON kg_facts(subject, relation);
CREATE INDEX idx_kg_valid ON kg_facts(valid_from_year, valid_to_year);
CREATE INDEX idx_kg_known_by ON kg_facts(known_by_npc_ids);
```

### 5.2 Pourquoi pas seulement les JSON canon actuels

Les JSON sont **statiques et positivistes**. Ils disent "Itachi a tué
le clan Uchiha". Mais notre système doit gérer "Itachi a aidé à
exposer le complot Uchiha" (post-divergence).

Le KG permet :

- Versions concurrentes de la vérité (canon dit X, world dit Y)
- Faits avec source (canon, action joueur, déduction)
- Faits avec validité temporelle
- Croyances ("Sasuke croit que Itachi a tué le clan" — peut être vrai
  ou faux selon la branche)

### 5.3 Tension Detector (résout le manque critique #1)

Approche hybride sans hard-code de patterns.

**A) Invariants abstraits de physique sociale Naruto** (~20 règles) :

- "Un kage doit être en place dans chaque grand village" (si vide →
  tension)
- "Une ressource jinchūriki est convoitée par les forces extérieures"
- "Un perso obsessionnel ne reste pas passif longtemps"
- "Une faction lésée cherche à se venger"
- "L'absence de leader charismatique crée du vide politique"
- "Les liens de sang non résolus reviennent toujours hanter"

Ce sont des **principes**, pas des scripts d'événements. Ils signalent
une tension, le LLM créatif décide comment la résoudre.

**B) LLM analyste périodique** :

Tous les 3 mois in-game, un Qwen3-4B reçoit un snapshot synthétique
du KG actuel (top-50 PNJ + leurs états + relations + events récents)
et identifie :

- Les fils narratifs en suspens (Chekhov's gun introduits sans payoff)
- Les configurations qui appellent une réponse
- Les anniversaires d'événements

Le LLM analyste **n'invente pas l'événement**. Il identifie
l'**opportunité dramatique**. La couche 4 Director décidera si on
l'exploite et la couche 3 multi-agent l'incarnera.

**Latence estimée** : 1 inférence Qwen3-4B tous les 3 mois in-game,
soit toutes les 30-90 turns. Coût latence négligeable.

### 5.4 Belief Propagator

Comment l'information se propage entre PNJ.

- Chaque PNJ a son propre sous-KG des faits qu'il connaît
- Les rumeurs (système existant `engine/rumors.py`) propagent les
  faits entre sous-KG selon les liens sociaux
- **Distorsion par chaîne de transmission** : à chaque saut, le fait
  peut perdre en fidélité (paramètre `fidelity` à étendre)

Cas d'usage : le joueur sauve Itachi en year 8. Cette info ne se
propage pas instantanément. Sasuke ne sait peut-être pas en year 9.
Madara apprend en year 12 par espionnage. Pain l'apprend en year 14
par Zetsu. Chaque agent agit selon ce qu'il croit savoir.

C'est ce qui permet aux trahisons, découvertes et retournements
d'émerger naturellement.

---

## 6. Composant 2 — Multi-Agent Simulation (Couche 3)

### 6.1 Architecture inspirée Park et al

Pour les **top-15 PNJ majeurs** (Naruto, Sasuke, Sakura, Kakashi,
Itachi, Madara, Hashirama, Tsunade, Jiraiya, Orochimaru, Pain, Konan,
Obito, Minato, Hiruzen + dynamique selon arc), chacun est un agent.

**Mémoire à 3 niveaux** :

```python
class NPCMemory:
    observations: List[Observation]      # tous les faits perçus
    reflections: List[Reflection]         # synthèses périodiques
    plans: List[Plan]                    # intentions court/long terme

    def reflect(self):
        # Tous les N ticks : synthétiser observations en réflexions
        # de plus haut niveau via 1 inférence Qwen3-4B
        pass

    def retrieve(self, query, top_k=5):
        # Récupère les memories selon recency + importance + relevance
        # (similaire au pattern Generative Agents)
        pass
```

**Stockage** : SQLite par PNJ + embeddings BGE-M3 pour le retrieval
sémantique des memories.

### 6.2 Personnalité vectorielle évolutive (résout le manque #2)

```python
class NPCPersonality:
    # 20 dimensions continues normalisées 0-1
    aggression: float
    loyalty: float
    secrecy: float
    ambition: float
    fear: float
    idealism: float
    pragmatism: float
    empathy: float
    confidence: float
    paranoia: float
    # ... 10 autres

    canon_baseline: Dict[str, float]
    drift_history: List[PersonalityDrift]

    def apply_event(self, event: ExperiencedEvent):
        """Modifie le vecteur selon l'event vécu, déterministe."""

    def divergence_from_canon(self) -> float:
        """Distance euclidienne du vecteur au baseline canon."""
```

**Drift par règles de physique sociale abstraite** (~30 règles
génériques) :

```python
class DriftRules:
    def trauma_event(npc, event):
        # Tout event qualifié "traumatic" pour le NPC :
        # → fear +0.1-0.3, paranoia +0.05-0.15
        # Cumulatif mais saturant (sigmoid)

    def betrayal_witnessed(npc, betrayer):
        # Si NPC voit X trahir Y, et NPC est proche de Y :
        # → loyalty envers betrayer -0.1, paranoia +0.1

    def long_term_companionship(npc, other, years):
        # Plus de N années avec autre PNJ :
        # → loyalty envers lui +log(years)*0.05
```

Pas de templates. Ce sont des règles de **psychologie générique**
applicables à n'importe quel NPC dans n'importe quel contexte. C'est
le drift qui produit la divergence, pas un script "if Sasuke + no
massacre then ...".

### 6.3 Action selection par LLM sous contraintes

Quand un PNJ majeur doit agir, Qwen3-4B reçoit en contexte :

- Sa mémoire pertinente (top-5 retrieved)
- Son vecteur de personnalité actuel
- Sa relation avec les autres PNJ présents
- L'état du monde local (KG filtré sur ce qu'il sait)
- Ses plans en cours

Et il génère une action structurée parmi un espace contraint mais
ouvert : déclarer une intention, parler, voyager, attaquer, chercher
information, méditer, comploter.

Ces actions modifient le KG, qui à son tour change ce que les autres
PNJ peuvent observer.

### 6.4 Pourquoi top-15 et pas tous les PNJ

Latence. 15 PNJ × 1 inférence/tick = 15-45 sec de latence par tick.
Trop.

Solution : **simulation différée par couches de profondeur**.

- **Top-15 PNJ majeurs** : simulation active à chaque tick
- **PNJ secondaires (~50)** : simulation par lot toutes les 10 ticks
  (1 inférence batchée pour le groupe via prompt batched)
- **Tous les autres PNJ** : comportements canoniques par défaut, élevés
  au statut d'agent uniquement si le joueur interagit avec eux ou
  s'ils sont impactés par un event majeur

C'est l'approche StoryVerse : **profondeur narrative ciblée**.

### 6.5 Tick autonome du monde

Aujourd'hui le scheduler tick s'appelle quand le joueur agit.

Pour que **le monde tourne sans le joueur** (rejouabilité massive) :

- Mode "fast-forward" où on tick N mois sans le joueur
- Pendant ce mode : top-15 PNJ continuent leur simulation, events
  canon se déclenchent ou s'annulent selon les actions agents
- À la fin du fast-forward, le joueur reçoit un **digest** des
  événements importants

Optimisation latence en fast-forward : on peut accepter 5-10 sec par
"mois simulé" puisque le joueur attend volontairement.

---

## 7. Composant 3 — Director / Drama Manager (Couche 4)

### 7.1 Le rôle

L'**auteur invisible** qui s'assure que le monde émergent reste
narrativement intéressant et "Naruto-esque".

Inspiré de l'**Automated Story Director** (Riedl 2003) et **Drama
Llama** (2025).

Pas un metteur en scène autoritaire. Un orchestrateur qui :

- Identifie les opportunités dramatiques signalées par le tension
  detector
- Décide si on les active maintenant ou plus tard (selon le rythme
  narratif global)
- Influence les agents subtilement via des "nudges" passés en
  contexte LLM, pas des ordres directs
- Préserve les invariants narratifs Naruto

### 7.2 Les "abstract acts" (StoryVerse)

Au lieu de scripter "événement spécifique X arrive en year 13", le
Director compose des **actes abstraits** :

```
Acte abstrait : "Tension Konoha-Suna doit s'élever vers conflit
ouvert dans les 6 prochains mois"
```

Le Director ne dit PAS qui fait quoi. Les agents (couche 3) savent
qu'il y a une tension à incarner. Chaque agent décide s'il y
participe selon sa personnalité et son contexte.

L'acte abstrait reste, l'incarnation diffère. C'est ça qui empêche la
répétition entre parties.

### 7.3 Préservation de l'esprit Naruto

Le Director maintient une liste d'**invariants narratifs** :

- "Le pouvoir s'accompagne toujours d'un coût" (motif récurrent
  Kishimoto)
- "Les liens humains transforment plus que la force"
- "Les conflits politiques ont des racines historiques profondes"
- "La haine engendre la haine, mais peut être brisée"

Ces invariants ne **prescrivent pas** d'événements. Ils sont passés
en contexte au LLM créatif quand il génère, pour qu'il reste dans le
ton.

### 7.4 Compaction narrative périodique

Tous les N mois in-game, le Director génère un résumé de ce qui s'est
passé dans le monde. Approche **NexusSum (2025)**.

Ce résumé :

- Va dans le KG comme fait "histoire récente"
- Est utilisé pour le contexte futur (le monde se souvient
  globalement)
- Permet au joueur d'avoir un journal de bord

Sans ça, le contexte explose. Indispensable pour parties >100 turns.

---

## 8. Composant 4 — Boucle créative fermée

### 8.1 Le problème actuel

`WorldResolver._world_resolve_cancellation` génère du texte. Ce
texte n'est pas réinjecté comme **événement structuré** dans le
scheduler. L'événement substitut reste un message au joueur, pas un
fait du monde.

### 8.2 La solution

Étendre WorldResolver pour qu'il génère un `TimelineEvent` structuré
quand un canon event est cancelled.

```python
def resolve_cancellation(cancelled_event, world_state, kg):
    # 1. LLM analyse le cancelled + état actuel du KG
    # 2. LLM génère un nouvel TimelineEvent qui prend la place,
    #    avec preconditions / outcomes / cancellation_strategy
    # 3. Validation par triplet_check + sherlock + canon_invariants
    # 4. Si valide, injection dans scheduler
    # 5. Si invalide, regen avec feedback (max 2 fois)
    # 6. Si toujours invalide, fallback en silent_cancel
    new_event = generate_replacement_event(cancelled_event, world_state, kg)
    if validator.validate(new_event):
        scheduler.schedule(new_event)
        kg.add_facts(new_event.outcomes)
    return new_event
```

Boucle fermée : event annulé → event substitut généré → KG mis à
jour → nouveau monde → tick continue.

### 8.3 Le triplet_check à revisiter (sortie du mode anti-emergence)

Aujourd'hui, le triplet_check rejette `(itachi_vivant, rasengan)`
parce qu'Itachi n'est pas dans `canonical_users` de Rasengan.

Solution :

- **Mode `canon_strict`** : le check actuel
- **Mode `alternate_timeline`** : le check est désactivé, mais une
  **autre validation** entre en jeu

L'autre validation : **plausibilité par mécaniques canon**.

- Itachi peut-il apprendre Rasengan ? Il faut un prof. Naruto le sait,
  Jiraiya le savait, Minato l'a inventé.
- Si Itachi a interagi avec Naruto dans cette branche, l'apprentissage
  est plausible.
- Le LLM (Qwen3-4B) valide la plausibilité par rapport à la chaîne
  d'événements vécus dans cette branche.

Pas de hard-code, c'est de la **vérification de plausibilité
contextuelle**.

Le mode est sélectionné via flag config + arc/year. Mode strict par
défaut sur les arcs pré-divergence joueur, mode alternate après que
le joueur ait causé une divergence majeure.

---

## 9. Pipeline de données à extraire (offline, one-shot)

### 9.1 Timeline canon enrichie (200-500 événements)

Au-delà des 60 events actuels. Format :

```json
{
  "id": "uchiha_massacre_year9",
  "year": 9,
  "preconditions": [
    {"fact": "itachi.alive", "value": true},
    {"fact": "danzo.position", "value": "foundation_leader"},
    {"fact": "uchiha_clan.coup_d_etat_planned", "value": true}
  ],
  "outcomes": [
    {"fact": "uchiha_clan.dead", "value": true},
    {"fact": "itachi.role", "value": "missing_nin"},
    {"fact": "sasuke.psychological_state", "value": "trauma_obsession"}
  ],
  "narrative_invariants": [
    "Sasuke devient orphelin et obsédé par la vengeance",
    "Itachi rejoint Akatsuki sous couverture"
  ],
  "alternative_seeds": [
    "Si itachi.alive=true mais coup_d_etat exposed → ?",
    "Si danzo destitué avant year9 → ?"
  ]
}
```

Les `alternative_seeds` sont des **questions ouvertes**, pas des
réponses pré-écrites. Elles guident le LLM créatif sans le brider.

**Coût estimé** : $5-10 via Groq Batch API (Llama 3.3 70B). Comme
Pass 2 du sous-projet canon.

### 9.2 Réseau de motivations profondes des PNJ (top-50)

```json
{
  "id": "uchiha_itachi",
  "deep_motivations": {
    "primary": "protect_konoha_at_any_cost",
    "secondary": "preserve_sasuke_innocence",
    "tertiary": "atone_for_past_deeds"
  },
  "moral_red_lines": [
    "kill_civilian_uchiha_children_unprovoked",
    "let_sasuke_become_pawn_of_madara"
  ],
  "secret_ambitions": [],
  "deepest_fear": "sasuke_corrupted_by_revenge",
  "self_image": "necessary_evil",
  "what_others_dont_know": ["true_reason_for_massacre"]
}
```

**Coût estimé** : $3-5

### 9.3 Cartographie des forces politiques

Forces, factions, leaders, alliances, tensions, ressources. Permet au
tension detector et au Director d'identifier les configurations
instables géopolitiques.

**Coût estimé** : $2-3

### 9.4 Index des "moments charnières" du canon

Quels événements sont des **points de divergence majeurs** ?

- Massacre Uchiha (year 9)
- Kannabi Bridge / mort Rin (year 4)
- Sceau Kyuubi (year 0)
- Mort Yondaime (year 0)
- Pain Invasion (year 16)

Permet au système de comprendre quels actes joueur ont des
conséquences cascade massives.

**Coût estimé** : $1-2

### 9.5 Patterns canoniques d'écriture Kishimoto

Pas pour copier, mais pour **imiter le style** :

- Comment Kishimoto fait ses retournements (révélations en plusieurs
  couches)
- Comment il construit ses trahisons (depuis longtemps en germe)
- Comment il fait ses rédemptions (par les liens humains)

Le Director utilise ces patterns comme *style guide* pour le LLM
créatif quand il narre.

**Coût estimé** : $3-5

### 9.6 Total extraction one-shot

| Dataset | Coût | Output |
|---|---|---|
| Timeline events enrichis | $5-10 | data/canon/timeline_events_enriched.json |
| Motivations top-50 | $3-5 | data/canon/deep_motivations.json |
| Forces politiques | $2-3 | data/canon/political_forces.json |
| Moments charnières | $1-2 | data/canon/divergence_points.json |
| Patterns Kishimoto | $3-5 | data/canon/narrative_patterns.json |
| **Total** | **$14-25** | |

Une seule fois, jamais répété.

---

## 10. Plan d'implémentation phasé

### Phase A — Knowledge Graph (2-3 semaines)
- Schéma SQLite, migrations
- Imports depuis JSON canoniques actuels
- API CRUD avec filtres temporels
- Tests : 100% des facts canon importés sans perte

### Phase B — Belief Propagator + sous-KG par PNJ (1 semaine)
- Extension du système Rumor existant
- Liens sociaux explicites
- Distorsion en chaîne
- Tests adversariaux

### Phase C — Tension Detector (1-2 semaines)
- Module invariants statiques (~20 règles abstraites)
- LLM analyste périodique (Qwen3-4B local, 1 inf/3 mois in-game)
- Output : liste d'opportunités dramatiques avec scores
- Tests : sur scénarios canon, identifier les tensions canoniques
  comme témoignage de validité

### Phase D — Personnalité vectorielle + drift (1-2 semaines)
- Schéma personality vector pour top-50 PNJ
- Règles de drift déterministes (~30 règles)
- Extraction baseline canon depuis wiki_sections (offline batch)
- Tests : sur cas connus (Sasuke pre/post massacre), drift cohérent

### Phase E — Multi-agent simulation top-15 (3-4 semaines)
- Architecture mémoire 3-niveaux
- Action selection par LLM avec contraintes
- Tick autonome
- Optimisations latence : caching agressif, batch inferences,
  speculative decoding
- Tests : 30 jours simulation passive, output cohérent

### Phase F — Boucle créative fermée WorldResolver (1-2 semaines)
- Génération TimelineEvent structuré
- Validation hybride (triplet relâché + plausibilité)
- Réinjection scheduler

### Phase G — Director / Drama Manager (2-3 semaines)
- Abstract acts model
- Invariants narratifs Naruto
- Compaction narrative périodique
- Tests : sur scénarios divergents, qualité narrative subjective

### Phase H — Pipeline data extraction (parallèle, 1-2 semaines)
- Timeline events enrichis
- Motivations PNJ top-50
- Forces politiques
- Moments charnières
- Patterns Kishimoto
- Total : $14-25 one-shot

**Total : 4-5 mois solo full-time. 2-3 mois si parallélisable.**

---

## 10bis. Sprints structurels intercalaires (livres)

Avant la Phase D, deux sprints structurels ont ete ajoutes a la roadmap pour
preparer le terrain (decision technique justifiee, voir commit history) :

### Sprint VN — Systeme de dialogues style visual novel (livre)

**Module : `src/shinobi/dialogue/`**

- `types.py` : `DialogueLine` (speaker_id, text, emotion, expression, tone,
  in_game_year/date, location, turn_number, related_event_id, related_mission_id,
  is_thought, voice_profile_id, stage_directions). 20 emotions, 14 expressions
  faciales, 10 tons vocaux (enums controles).
- `log.py` : `DialogueLog` rolling window (default 5000 lignes) avec offload
  optionnel JSON-Lines pour archives longues sessions. Queries par speaker,
  year_range, event, mission, location, turn_range, thoughts vs speech.
- `formatter.py` : `DialogueFormatter` convertit `NarrationResponse` en
  `list[DialogueLine]`. Extrait les discours rapportes ('X dit : "Y"' ->
  attribue a X), les pensees inline (*...*), detecte emotion/tone par mots-cles.
- `vn_export.py` : payload JSON canonique versionne pour application VN
  externe (Ren'Py, web, custom). Grouping en scenes par (year, date, location,
  mood). Support resolver de noms personnalise.

**Tests : 39, 100% pass.**

**Pourquoi avant Phase D :** Phase D consomme les events vecus pour drift de
personnalite (`trauma_event` apres dialogue traumatique). Sans dialogues
structures maintenant, la Phase D devrait refactor le narrator a posteriori.

### Sprint MISSIONS — Mass event capture (livre)

**Module : `src/shinobi/missions/`**

- `types.py` : `Mission` Pydantic avec `id`, `name_fr/romaji`, `rank` (D/C/B/A/S/
  forbidden/unranked), `type` (18 categories : escort, assassination,
  retrieval, capture, rescue, protection, sabotage, spy, chunin_exam,
  special_operation, etc.), `outcome` (success/partial/failure/abandoned/
  in_progress/canceled/unknown), `year/month/day`, `participants` avec roles,
  `assigning_authority`, `target_subject`, `location_id`, `objectives`,
  `consequences`, `canonical_arc`, `canonicity` (manga/databook/anime_canon/
  filler/boruto), `related_event_ids/mission_ids`.
- `catalog.py` : `MissionCatalog` avec load/save JSON, queries by_id,
  by_year_range, by_rank, by_type, by_participant, by_arc, by_location.
- `kg_integration.py` : `import_missions_to_kg` cree N facts par mission
  (type, name, rank, occurs_in_year [valid_from], occurs_at, assigned_by,
  involves [pour chaque participant, double-direction], objective_i,
  consequence_i, related_event/mission). Idempotent par `source = 'mission:<id>'`.

**Dataset : `data/canonical/missions.json` (~26 missions canon)**

Couverture initiale : Wave Country, Chunin Exam, Search for Tsunade,
Sasuke Recovery, Kazekage Rescue, Twelve Guardians Bridge, Hidan/Kakuzu,
Jiraiya Pain, Pain Invasion, 5 Kage Summit, 4e Guerre, Kannabi Bridge,
Massacre Uchiha (operation noire), D-rank Tora cat, Bikochu, Land of Tea,
Three Tails Sealing, Orochimaru Lair Infiltration, Mizuki Strikes Back,
Kakashi Anbu Recruit, Boruto Chunin Exam, Mitsuki Search, Kawaki Arc,
Obito Redemption, 7 Swordsmen of Kiri, Anbu Root Purge.

**Extension future (Phase H, $14-25 offline)** : extraction LLM massive sur
Narutopedia mission pages pour atteindre couverture canon complete (>500
missions estimees).

**Tests : 29, 100% pass.**

**Pourquoi avant Phase D :** Phase D drift les vecteurs de personnalite selon
les events vecus. Avec 60 events canon, Sasuke a quasi rien a drifter sur.
Avec des centaines de missions enrichies, le drift est precis et emergent.

---

## 11. Performance et optimisations latence

### 11.1 Budget latence par tick

Cible : **<5 sec ressenti par tick** sur CPU moyen (8 cores, 16 GB
RAM, pas de GPU).

Ventilation idéale par tick :

| Composant | Inférences | Latence cible | Cible totale |
|---|---|---|---|
| Narrator (Qwen3-4B) | 1 | 1-3 sec | 1-3 sec |
| Validator (déterministe) | 0 | <100 ms | <100 ms |
| Multi-agent top-15 | 0-5 | 0-3 sec/agent | 0-5 sec |
| Tension detector | 0 | (1 fois/30 ticks) | ~0 |
| Director | 0 | (1 fois/10 ticks) | ~0 |
| **Total typique** | | | **2-5 sec** |
| **Total pic** (5 agents actifs) | | | **5-10 sec** |

Le multi-agent est le facteur dominant. Stratégies :

- Sampling top-K agents (5 sur 15) actifs ce tick
- Batch d'agents en un seul prompt (5 PNJ → 1 inférence Qwen3-4B
  multi-output)
- Caching agressif des actions répétitives
- Decision déterministe simplifiée si le PNJ est dans un état
  "trivial" (sleeping, traveling, training routine)

### 11.2 Cache disque pour inférences

```python
import hashlib
import diskcache

cache = diskcache.Cache("data/llm_cache")

def cached_inference(prompt, model_id, temperature):
    key = hashlib.sha256(
        f"{model_id}:{temperature}:{prompt}".encode()
    ).hexdigest()
    if key in cache:
        return cache[key]
    result = llama_inference(prompt, model_id, temperature)
    cache[key] = result
    return result
```

Hit rate attendu sur partie longue : 30-50% des inférences.

### 11.3 Distillation (modèles secondaires)

Pour les checks rapides (validation plausibilité, classification
intent), Qwen3-4B est overkill. Utiliser des modèles plus petits :

- **Qwen3-0.5B** ou **Phi-3-mini-4k** pour classifications
- **Qwen3-1.5B** pour validations simples
- **Qwen3-4B** uniquement pour narration et décisions complexes
  d'agents

Latence Qwen3-0.5B sur CPU : ~200-500 ms par inférence courte.

### 11.4 GPU optionnel

llama.cpp supporte CUDA / Metal / Vulkan out-of-the-box.

Joueurs avec GPU : speedup 5-20x sur Qwen3-4B selon hardware.

Joueurs CPU only : tout reste fonctionnel, juste plus lent.

Speculative decoding (Qwen3-4B + Qwen3-0.5B draft) : 1.5-2.5x sur
GPU, neutre voire négatif sur CPU pur.

---

## 12. Risques majeurs et mitigations

### R1 — Dérive narrative dans branches lointaines
**Mitigation** : invariants Naruto dans le Director + compaction
narrative périodique + ancrage canon dans drift rules + tests
réguliers de cohérence (le LLM analyste peut détecter les
incohérences globales)

### R2 — Latence runtime qui dépasse 10 sec/tick
**Mitigation** : sampling top-K agents, batch inferences, caching,
distillation modèles secondaires, GPU optionnel, tick rate adaptatif

### R3 — Convergence vers patterns répétitifs
**Mitigation** : variabilité par seed sauvegardable, température LLM
0.7-0.9, exploration de branches non-canon, multi-agent maximise
variabilité naturellement, le Director varie ses abstract acts

### R4 — Joueur perdu dans monde trop dynamique
**Mitigation** : digest narratif périodique (compaction), journal de
bord, breadcrumbs (existant), Director qui maintient lisibilité

### R5 — Debug d'un monde émergent
**Mitigation** : logs structurés de chaque décision agent, KG
inspectable via CLI, tests sur scénarios déterministes (seed fixe),
mode replay des parties

### R6 — Perte de l'essence Naruto
**Mitigation** : invariants narratifs + patterns Kishimoto comme
style guide + tests subjectifs périodiques avec le créateur

### R7 — triplet_check anti-emergence (déjà identifié)
**Mitigation** : modes canon_strict / alternate_timeline + validation
de plausibilité contextuelle

### R8 — Cascade infinie de propagations dans le KG
**Mitigation** : `max_depth=10` dans cascade.py, fallback sur
`cancelled_silent`, monitoring runtime

### R9 — Reproductibilité des saves
**Mitigation** : seed sauvegardé dans le save, test "save → tick 100x
→ load → tick 100x : KG identique", règles de drift déterministes
sans random

---

## 13. Quick wins en 1-2 semaines avant gros chantier

Si tu veux une démo qui montre la promesse avant 4-5 mois :

### Quick win 1 — Tension detector minimal (1 semaine)
- 5 invariants hard-codés (juste pour proof of concept)
- 1 LLM analyste sur snapshot du world_state existant tous les
  N turns
- Output : opportunités dramatiques affichées au joueur (sans encore
  être incarnées par les agents)

### Quick win 2 — Personality drift simplifié (1 semaine)
- Vecteur de 5 dimensions sur 5 PNJ majeurs (Naruto, Sasuke, Itachi,
  Kakashi, Sakura)
- 5 règles de drift simples
- Affichage dans la fiche PNJ : "Sasuke a divergé de 40% de son
  baseline canon"

### Quick win 3 — WorldResolver fermé (1 semaine)
- Sur cancelled events seulement (pas spontané)
- Génération TimelineEvent structuré pour le substitut
- Réinjection dans le scheduler

**Coût** : 0 (juste du code), 1-2 semaines de dev.

**Résultat** : proof of concept que le monde réagit créativement.
Pas le système complet, mais une démo qui valide l'architecture.

---

## 14. Décisions architecturales clés à respecter

### 14.1 Pas de framework lourd
Continuer la philosophie du projet : pas de langchain, pas de
llamaindex, pas d'autogen. Juste Pydantic + httpx + bibliothèques
ciblées.

Raison : maîtrise totale du flux, pas de magie cachée, debug facile.

### 14.2 Tests adversariaux pour chaque composant
Comme les piliers anti-hallu actuels. Pour chaque module nouveau,
écrire des tests qui essaient de le casser.

### 14.3 Logs structurés (structlog déjà présent)
Chaque décision agent, chaque tension détectée, chaque event généré
doit être loggé avec suffisamment de contexte pour un replay/debug.

### 14.4 Découplage strict scheduler / LLM
Le scheduler doit rester **purement déterministe et inspectable**.
Le LLM est appelé pour la créativité, pas pour la logique du monde.

### 14.5 Pydantic strict partout
Chaque output LLM passe par une validation Pydantic. Si un champ
manque ou est mal typé, regen ou fallback.

### 14.6 Pas de hard-code de templates d'événements
**Règle d'or de la vision créative.** Tout événement alternatif doit
être généré, jamais pré-écrit. Si tu te surprends à écrire "if
massacre_uchiha cancelled then schedule event_X", arrête-toi : tu
casses la promesse créative.

---

## 15. Conclusion

Le projet a un fondement solide :
- 7 piliers anti-hallu livrés et testés
- Engine de jeu à 70%
- Pipeline RAG complet et opérationnel
- 359 tests verts, 0 fail

La vision créative émergente est **techniquement viable** à condition
de bien architecturer en 4 couches.

Les 3 manques critiques (tension detector, profile vectoriel + drift,
boucle créative fermée) sont tous adressables sans hard-code de
templates.

L'état de l'art 2024-2026 (Generative Agents, StoryVerse, Drama
Llama, Co-DIRECT, StoryBox) confirme la viabilité et fournit des
patterns éprouvés.

**Estimation totale pour atteindre la vision** :
- Effort : 4-5 mois solo full-time, 2-3 mois si parallélisable
- Coût extraction one-shot : $14-25 sur Groq
- Coût en gameplay : **$0 par tour** (tout local)

C'est ambitieux mais **précisément spécifié**. Plus de flou.

Pour démarrer, commence par les Quick wins (1-3 semaines) pour
valider l'architecture en proof of concept, puis attaque Phase A
(Knowledge Graph) qui débloque tout le reste.

Bon courage. Ce que vous allez construire est rare.
