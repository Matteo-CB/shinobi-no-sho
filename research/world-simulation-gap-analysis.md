# World simulation — gap analysis

Audit strategique entre l'etat actuel du code et la vision cible
"simulateur de vie creatif emergent dans l'univers Naruto". Audit
mene apres Phase 1 (pipeline retrieval + tagging temporel branche).

100% honnetete : ce qui suit distingue **FACT** (verifie en lisant
le code) de **HYPOTHESE** (deduction non testee).

## Cadre compute (clarification importante)

Le projet tourne **100% en local** en gameplay. Stack :
- llama.cpp HTTP server (`llm_backend_url=127.0.0.1:8080`)
- Modele primaire : Qwen3-4B GGUF Q4 (`models/llm/Qwen3-4B-UD-Q4_K_XL.gguf`)
- Embeddings : BGE-M3 CPU via sentence-transformers (~2.27 GB)
- Vector store : ChromaDB persistent local

**Aucun appel API payant en gameplay. $0 par turn pour le joueur.**

Les coûts Groq mentionnes dans cet audit ($1-2 ici, $X ailleurs)
concernent **uniquement les batchs offline de prep de donnees**
(comme Pass 2 extraction canon $2.30, Pass 5 tagging chunks $8) :
des operations one-time qui produisent des fichiers JSON consultes
ensuite localement. Le projet a deja brule **$10.30 cumule** sur
ces batchs offline.

La contrainte reelle en gameplay est donc **compute local**
(latence per-turn, RAM, CPU/GPU), pas le cout API. Toute discussion
de "cout LLM" dans ce doc doit etre relue avec ce cadre.

## 1. Vision detaillee et illustree

### 1.1 Reformulation en une phrase

Un monde Naruto ou la chronologie canon se deroule par defaut, ou les
actions du joueur peuvent annuler ou devier des arcs entiers, et ou le
systeme genere de maniere creative et coherente ce qui se passe a la
place — sans qu'aucun developpeur n'ait pre-ecrit ces alternatives.

### 1.2 Six scenarios illustratifs

#### S1. Joueur s'allie a Itachi en year 8 et expose Danzo publiquement

- **Action declenchante** : joueur infiltre l'ANBU, decouvre l'ordre
  de massacre Uchiha donne par Danzo, le revele a Hiruzen + presse
  internationale ninja en year 8.
- **Cascade attendue** :
  - `uchiha_massacre` cancelled → tous les events qui en dependent
    (Sasuke deserter, Itachi missing-nin, Akatsuki recrute Itachi)
    deviennent **at_risk**.
  - Le clan Uchiha reste politiquement actif → tensions internes a
    Konoha s'exacerbent (le coup d'etat Uchiha n'avait pas ete
    annule, juste son massacre).
  - Danzo destitue ou en fuite → Root demantele ou fragmente.
  - Sasuke grandit normalement, son arc obsessionnel n'a pas de
    raison d'etre.
- **Type d'events emergents** :
  - Proces public Danzo (jamais dans le canon)
  - Fugaku tente le coup d'etat → guerre civile ou compromis
  - Itachi reste shinobi de Konoha
  - Sasuke devient un genin sans haine
- **Adaptation des PNJ** :
  - **Sasuke** : pas un Sasuke "tempere" mais un Sasuke OTHER, oriente
    par sa relation avec Itachi-vivant et son contexte d'enfance non
    traumatique. Sa motivation profonde change radicalement.
  - **Itachi** : pas le pacifiste tortue. Une nouvelle persona emerge
    selon les choix qu'il fait dans cette nouvelle realite.
  - **Konoha** : meritocratie ninja ou aristocratie Uchiha-friendly.

#### S2. Joueur sauve Rin de Kakashi en year 4

- Action : joueur intercepte la mission Kannabi Bridge, neutralise les
  Kiri-nin avant qu'ils ne traquent Rin avec la bombe Sanbi.
- Cascade : pas de mort de Rin → Obito ne sombre pas → pas de
  Tsuki no Me plan → pas d'arc Akatsuki-Madara.
  Mais Madara reste mort canoniquement, donc qui dirige les pieces
  qui devaient le ramener ? Personne. La 4e Guerre n'arrive jamais
  sous cette forme.
- Events emergents : Obito devient un jonin discret de Konoha.
  Akatsuki, sans son architecte fanatique, derive vers une organisation
  mercenaire pure. Pain reste son leader visible mais sans la vision
  Tsuki no Me, il poursuit "juste" sa quete de paix par les bijuu —
  qui, sans manipulation derriere, peut etre dialoguee.
- Adaptation : Naruto en grandissant sans Akatsuki structuree pourrait
  ne jamais developper son sage mode (pas d'urgence Pain). Sa trajectoire
  devient celle d'un genin tranquille de Konoha.

#### S3. Joueur s'oppose a Naruto pendant Pain Invasion

- Action : year 16, joueur intervient cote Pain, sabote la pierre
  memoriale, empeche Hinata d'arriver pour proteger Naruto.
- Cascade : Naruto entre en mode 6-queues sans la motivation Hinata,
  Yamato absent, Minato n'est pas re-active dans son subconscient
  car le declencheur emotionnel est different. Naruto perd controle
  → Konoha rasee plus largement → Pain n'est pas convaincu par le
  monologue.
- Events emergents : un Konoha plus petit qui doit se reconstruire
  sans son heros, un Pain qui poursuit son plan.
- Adaptation : Naruto comme personnage devient OTHER — il a vu Pain
  l'emporter face a lui, sa philosophie devient autre.

#### S4. Joueur infiltre Akatsuki en se faisant passer pour missing-nin

- Action : year 14, joueur deserte fictivement Konoha, est recrute par
  Akatsuki sur reputation manipulee.
- Cascade : selon les actions du joueur dans Akatsuki :
  - Si transmet info a Konoha : missions Akatsuki echouent mysterieusement,
    Pain devient parano → purge interne.
  - Si fait carriere : devient le 11e membre, peut diriger des
    captures de bijuu et changer le timing de la 4e Guerre.
- Events emergents : "qui est la taupe?" devient une intrigue active
  dans Akatsuki, Itachi peut decouvrir la verite et choisir camp.
- Adaptation : les membres Akatsuki acquierent des dynamiques de
  mefiance entre eux jamais vues dans le canon.

#### S5. Joueur fonde un nouveau village dissident dans Pays du Fer

- Action : joueur quitte tous les villages, leve une faction de
  missing-nin idealistes (Zabuza vivant, Haku, Yagura sane) dans
  Pays du Fer year 13.
- Cascade : 5 grands villages reagissent (alliance ninja vs nouveau
  village ? acceptation ? infiltration ?). Mifune (samurai leader)
  doit prendre position. Le village existe ou est ecrase selon les
  actions joueur dans la diplomatie.
- Events emergents : nouvelle ere geopolitique. Si succes, le 5-Kage
  Summit a une 6e voix.
- Adaptation : tous les missing-nin canon (Zabuza, Kisame, Itachi)
  ont une alternative que le joueur leur a offerte. Leurs trajectoires
  changent en cascade.

#### S6. Joueur epargne Zabuza et l'aide a fonder une nouvelle Kiri

- Action : year 12, joueur convainc Zabuza de retourner a Kiri pour
  renverser Yagura (le vrai sans Obito → ou avec Obito si le canon
  est suivi avant cet acte).
- Cascade : revolution Kiri precipitee de 2-3 ans, Mei Terumi a
  Mizukage avec un Zabuza vivant comme bras militaire. Bloody Mist
  termine plus tot. Haku peut etre forme officiellement.
- Events emergents : nouvelle politique Kiri pre-Shippuden, alliance
  possible Konoha-Kiri precoce contre Akatsuki.
- Adaptation : Zabuza-vivant a une vie 20+ ans posterieure jamais
  exploree par Kishimoto.

### 1.3 Pattern commun

Dans tous ces scenarios, ce qui doit etre **genere** (pas pre-ecrit) :

- Le **prochain event** logique compte tenu de l'etat divergent
- Les **reactions des PNJ majeurs** non encore impactes
- Les **rumeurs** qui se propagent (avec deformation selon la distance
  geographique et politique)
- Les **dynamiques de faction** (alliances, trahisons, defections)
- Les **trajectoires individuelles long-terme** des PNJ dont l'arc
  canon est annule

---

## 2. Etat actuel du projet (FACT)

### 2.1 Couches anti-hallucination livrees (7 piliers + canon completion)

**FACT — observe dans le code** :

- **Pilier §2 (guards)** : `src/shinobi/guards/` (3 fichiers, ~540
  lignes) — blacklist + intent_classifier + output_filter. Filtre
  les inputs hors-univers et les meta-phrases LLM.
  *Service a la vision creative* : protection minimale, pas de role
  generatif.

- **Pilier §3 (validator)** : `src/shinobi/validation/` (6 fichiers,
  ~815 lignes) — sherlock_rules (couche A, dead actor / location
  destroyed), age_coherence (couche C, language adulte chez enfant),
  triplet_check (couche B, actor in jutsu.canonical_users),
  risk_tagger (4 niveaux, route vers couches a activer).
  *Service a la vision creative* : **goulot d'etranglement potentiel**.
  Le triplet_check rejette tout (actor, jutsu) hors canonical_users.
  Si Sasuke-without-massacre apprend Chidori autrement, le check
  passe (Sasuke est canonical user de Chidori). Mais si Itachi-vivant
  developpe Rasengan via Naruto comme prof, le check rejette parce
  qu'Itachi n'est pas dans canonical_users de Rasengan. **Ce check
  est anti-emergence** au sens strict : il refuse les triplets non
  canon. A revisiter.

- **Pilier §4 (state)** : `src/shinobi/state/` (2 fichiers, ~316
  lignes) — RuntimeState pydantic, age_calculator avec CanonView.
  *Service* : fondation neutre, n'oriente pas la creativite.

- **Pilier §6 (enums + structured gen)** : `src/shinobi/generation/`
  + `data/canon/*.json` (1360 chars, 3025 jutsus, etc.).
  *Service a la vision creative* : **double tranchant**. La
  structured gen contraint les sorties LLM aux ids canon. Si on veut
  que le LLM invente un nouveau jutsu hybride (Sasuke + Naruto =
  technique fusion), il sera REJETE par parse_narrative_output.
  L'enum est un mur. **A revisiter** — peut-etre rendre
  configurable selon le mode (canon strict vs alternate timeline).

- **Pilier §7 (risk-tagger)** : 4 niveaux, mapping vers couches.
  *Service* : permet de moduler la rigueur de validation. Pourra
  etre exploite pour relaxer les couches B sur les segments
  marques "alternate_timeline".

- **Pilier §8 (hybrid retrieval)** : BM25 + Chroma + RRF + filtre
  narrative_year. 15940 chunks indexes, 15937 tagges.
  *Service a la vision creative* : **haute valeur**. Permet de
  retrouver le contexte canon pertinent SANS imposer une suite,
  et le filtre temporel evite les anachronismes. C'est la fondation
  du grounding creatif.

- **Sous-projet canon completion** : 1359 persos extraits ($2.30),
  3-tier classification clans, 14 clans avec attestations.
  *Service* : alimente le KG implicite pour la suite.

**Tests** : 359 / 359 verts, demo runnable 8 cas.

### 2.2 Donnees canon disponibles (FACT)

Sous `data/canonical/` :

| Fichier | Volume | Contient |
|---|---:|---|
| characters.json | 1360 entries | id, clan, village, kekkei, natures, wiki_sections (6.2M chars) |
| techniques.json | 3025 entries | canonical_users, natures, rank, prerequisites, wiki_sections (750K chars) |
| clans.json | 52 entries | key/available kekkei + natures + techniques |
| villages.json | 40 entries | main_clans, country, wiki_sections |
| timeline_events.json | 60 entries | preconditions + outcomes structures + cancellation_strategy |
| voice_profiles.json | exists | sample_lines, registre, verbal_tics |
| psycho_notes.json | exists | non audite |
| organizations.json | 9 entries | members, leader, history |
| tailed_beasts.json | 10 entries | jinchuuriki_by_era |
| kekkei_genkai.json | 32 entries | carrier_clans |
| kekkei_mora.json | 6 entries | rare bloodlines |
| hiden.json | exists | techniques de clan |

Sous `data/canon/` (extraits Pass 6 phase A) :
- character_list, jutsu_list (avec canonical_users), village_list,
  clan_list, kekkei_genkai_list, nature_list (~7 fichiers).

Chunks RAG : 15939 chunks (BGE-M3 dim 1024 + BM25 sparse), 15937
tagges arc/year_min/year_max/tier/entities_mentioned.

### 2.3 Code moteur de jeu existant (FACT, surprise majeure)

Au-dela de l'anti-hallu, le projet a deja **un game engine
fonctionnel** sous `src/shinobi/engine/` (21 fichiers, ~3985 lignes).

| Module | Lignes | Role |
|---|---:|---|
| world.py | 192 | NPCState/VillageState/OrganizationState/ScheduledEvent/CompletedEvent/CancelledEvent + WorldState root |
| events.py | 169 | **scheduler operationnel** : initialize_scheduler, evaluate_precondition, tick_scheduler. Gere precondition `character_alive`, `clan_active`, `jinchuuriki_held_by`, `no_event_triggered`. Gere cancellation_strategy `delay` / `cascade_cancel` / `hard_cancel`. |
| consequences.py | 274 | gains de stats emergents par action (ConsequenceRule, apply_action_consequences, mission_consequences) |
| actions.py | 593 | resolve_action, apply_action_to_state — moteur de resolution dice + state delta |
| interpreter.py | 412 | parse les inputs joueur en ParsedIntent (heuristique + fallback LLM) |
| missions.py | 211 | systeme de missions par rang (D-S), durees, recompenses, difficulte |
| relations.py | 131 | affinity, reputation, decay |
| rumors.py | 77 | propagation rumeurs avec radius (proximity/regional/international/secret) + fidelity decay |
| combat.py | 117 | resolution combats |
| economy.py / shop.py / items.py | ~435 | systeme economique |
| progression.py | 269 | leveling stats avec diminishing returns |
| time.py | 48 | advance_time, GameDate |
| scene_context.py | 482 | contextualization scene → prompt |

Sous `src/shinobi/goals/` (492 lignes) :

| Module | Role |
|---|---|
| breadcrumbs.py | Breadcrumb (sub-objectif), CompletionCondition, BreadcrumbPrice |
| declaration.py | Goal model, declare/abandon/complete |
| pathfinder.py | **GoalPathfinder** : LLM genere des breadcrumbs avec contexte RAG |
| pricing.py | calcul des prix d'information |
| completion.py | check si breadcrumb satisfait |

Sous `src/shinobi/cli/play.py` (1691 lignes) : **boucle de jeu
complete** avec missions_flow, shop_flow, pathfinder_flow,
travel_flow, desertion_flow, scheduler tick chaque turn,
narration + validator + world_resolver pour cancellations.

Sous `src/shinobi/persistence/saves.py` (549 lignes) : sauvegarde +
chargement complet avec snapshots.

**Pattern emergent deja en place** :
- `tick_scheduler` declenche les events canon dont preconditions OK
- Si precondition violee → cancelled / delayed / cascade
- `world_resolver` LLM (stub) genere les substituts narratifs
- `GoalPathfinder` LLM genere les paths joueur creativement
- Rumeurs propagees avec deformation par radius

Le moteur n'est pas un squelette, c'est **un MVP de simulation**.

### 2.4 Documentation (FACT)

`docs/` (13 fichiers, specs produits) — couvre la vision generale.
`research/` (17 fichiers, ~9100 lignes) — couvre les implementations
realisees.

Documents qui touchent la vision creative :
- `docs/08_world_simulation.md` — scheduler + cascade design (existe
  partiellement dans le code)
- `docs/06_game_engine.md` — moteur de jeu (existe partiellement)
- `docs/07_goal_system.md` — pathfinder system (existe partiellement)
- `docs/10_llm_integration.md` — narrator + judge + claim_validator
  (existe partiellement)
- `research/timeline-engine-roadmap.md` — roadmap futur Phase A→E
  qui DUPLIQUE en partie ce qui est deja fait

**Contradictions / redondances reperees** :
- `timeline-engine-roadmap.md` Phase A "Scheduler MVP" decrit une
  architecture a faire alors que `engine/events.py` l'a deja
  partiellement. **A reconcilier**.
- `docs/08_world_simulation.md` decrit en detail un KG dual canon
  vs world. Cette dualite n'existe PAS dans le code (FACT : world.py
  contient un seul WorldState mutable, le canon est lu via load_canon
  comme reference immuable mais pas formalise comme KG).

---

## 3. Ce qui manque pour la vision creative

### 3.1 Moteur d'avancement temporel — partiellement la (FACT)

✅ **Existe** : `tick_scheduler` avance le temps, declenche events
canon, propage rumeurs.

❌ **Manque** :
- **Tick autonome sans le joueur** : actuellement le tick est appele
  depuis play.py a chaque action joueur. Pas de "le monde tourne en
  background quand le joueur dort".
- **Granularite** : un seul tick = un appel. Pas de notion de "1 mois
  s'ecoule, 5 ticks de scheduler" sans le joueur.
- **Ticks intermediaires entre events canon** : entre an 8 (Uchiha
  massacre cancelled) et an 12 (Wave country), le monde devrait
  evoluer dans cette fenetre — sans events canon, comment bouge-t-il ?

### 3.2 Representation structurée du monde — partielle (FACT)

✅ **Existe** : NPCState avec psychological_state, current_affiliations,
canonical_arc_progress. VillageState avec political_alignment,
recent_incidents. OrganizationState avec members.

❌ **Manque** :
- **KG dual canon vs world** documente dans `docs/08` mais pas
  implemente.
- **Motivations profondes des PNJ** : NPCState.psychological_state est
  un Literal limite. Pas de modelisation des desires, fears, lignes
  rouges, ambitions secretes.
- **Reseau d'alliances et tensions** : pas de graphe explicite des
  forces politiques. Les VillageState.political_alignment sont
  "neutral", "allied", "hostile" — trop grossier pour faire emerger
  de la diplomatie creative.
- **Exploration algorithmique** : on ne peut pas demander au moteur
  "donne-moi tous les configurations instables actuelles" parce que
  l'instabilite n'est pas representee.

### 3.3 Detection d'opportunites narratives emergentes — absente (FACT)

❌ **N'existe pas** :
- Pas de pattern matching sur configurations causales du monde.
- Pas de "tension detector" qui identifie qu'un etat appelle un
  resolution (ex: "Itachi vivant + Sasuke heureux + Madara mort" →
  configuration stable, mais "Pain leader Akatsuki + Itachi defie
  Pain en public + Konan hesitante" → tension qui appelle resolution).
- Pas de notion de "chekhov's gun" : un fait introduit en year 8
  qui doit etre paye plus tard.

C'est le manque le plus critique pour la vision creative. Sans
detection automatique de tensions, le systeme ne peut que reagir
aux actions joueur, pas FAIRE EMERGER des events spontanement.

### 3.4 Generation creative coherente d'evenements — partielle (FACT)

✅ **Existe** :
- `WorldResolver` LLM dans `engine/play.py:_world_resolve_cancellation`
  qui genere un substitut narratif quand un event canon est cancelled.
- `GoalPathfinder` LLM qui genere des breadcrumbs (chemins
  d'objectifs) creativement.

❌ **Manque** :
- **Generation d'events canon-replacement structures**. Le
  WorldResolver actuel produit du texte narratif ("Le canon est
  devie, mais aucune narration n'a pu etre generee") mais ne fait
  pas un nouvel `TimelineEvent` avec preconditions / outcomes /
  cancellation_strategy. Donc l'event substitut n'est PAS injecte
  dans le scheduler — il reste juste un message au joueur.
- **Generation d'events NON declenchees par cancellation**.
  Actuellement WorldResolver ne s'active que sur cancelled. Aucun
  mecanisme pour generer des events spontanes (S5 : un nouveau
  village dissident, S6 : revolution Kiri precoce).
- **Validation que l'event genere est "Naruto-esque"**. La couche
  triplet_check rejetterait probablement tout event non canon.

### 3.5 Evolution adaptative des PNJ — absente structurelle (FACT)

❌ **N'existe pas** :
- Les behavior_profiles (`voice_profiles.json` minimaliste) sont
  des fact sheets statiques. Pas de modele pour faire evoluer un PNJ
  selon ce qu'il a vecu en jeu.
- `NPCState.psychological_state` est un Literal a 4 valeurs
  ("stable", "stressed", "broken", "purged" — non audite). Trop
  grossier.
- Aucun systeme de "personality emergence" : Sasuke-sans-massacre
  n'aurait pas de fondement structural pour devenir un autre Sasuke.

C'est le 2e manque le plus critique pour la vision creative.

### 3.6 Coherence a long terme dans les branches divergentes — absente (FACT)

❌ **N'existe pas** :
- Pas d'invariants formalises ("Konoha existe", "le chakra suit ces
  regles physiques", "les villages ont leur structure ninja").
- Pas de detecteur d'incoherence ("apres 30 ans de jeu, Konoha a
  disparu mais la fac d'Iruka organise toujours des examens").

### 3.7 Variabilite — absente structurelle (FACT)

❌ **N'existe pas** :
- Pas de seed sauvegardable pour reproduire ou diverger des parties.
- LLM appele a temperature=0.7 (FACT: `Settings.llm_temperature=0.7`)
  donc un peu de variabilite, mais pas de strategie pour garantir
  que 2 parties similaires divergent.

### 3.8 Diffusion d'information — partielle (FACT)

✅ **Existe** : `Rumor` avec `diffusion_radius` (proximity/regional/
international/secret) et `fidelity` (deformation). `propagate_rumors`
ajoute les rumors au world. `player_can_hear` filtre selon la
position joueur.

❌ **Manque** :
- **Propagation par liens sociaux** : actuellement c'est une boolean
  "le joueur peut entendre selon le radius". Pas de "le PNJ X qui
  est ami de PNJ Y a entendu" → graphe social.
- **Distance d'information temporelle** : pas de delai (joueur dans
  Pays de la Foudre apprend events Konoha avec X jours de retard).
- **Deformation par chaine de transmission** : la fidelity est fixe
  par radius, pas degradee a chaque saut.

---

## 4. Approches techniques candidates

### 4.1 Pour generation creative d'events emergents (§3.3 + §3.4)

**Approche A — Multi-agent simulation locale**
Chaque PNJ majeur (top-30) tient un agent LLM minimaliste qui agit
selon ses motivations + relations + recents events vecus. Les
interactions entre agents produisent des events emergents.
- Avantage : maximum d'emergence, naturellement variable.
- Risque : cout LLM massif (30 PNJ * appels par tick), derive si
  pas grounded.
- Effort : tres eleve.
- Risque template-based : faible si on injecte les motivations
  comme contraintes pas comme scripts.

**Approche B — Tension detector + LLM narrator avec contraintes**
Un module symbolique parcourt le world_state, identifie des
"configurations instables" via pattern matching (ex: 2 leaders
hostiles dans le meme pays, kage age >70 ans sans successeur,
clan 1-membre survivant qui revient au pouvoir). Pour chaque
tension, LLM genere un event de resolution avec contraintes
(actor existe, prerequisites canon, etc.).
- Avantage : events ancres dans la logique, debuggable.
- Risque : la liste de patterns devient hard-codee → on tombe
  dans le piege evite. **Mitigation** : utiliser un LLM pour
  identifier les tensions plutot que des regles, mais avec un
  prompt qui demande des principes generaux pas des patterns
  specifiques.
- Effort : moyen.

**Approche C — Constraint satisfaction continue**
Le world_state est traduit en CSP (constraint satisfaction
problem) avec contraintes de coherence Naruto. A chaque tick,
le solver detecte les sur-contraintes (impossibilite logique) et
sous-contraintes (espaces de liberte ou un event peut etre genere).
LLM remplit les sous-contraintes.
- Avantage : rigueur formelle, garantit coherence.
- Risque : complexe a setup, traduire le canon Naruto en CSP est
  un gros chantier.
- Effort : tres eleve.

**Recommandation** : combinaison **A + B**. Multi-agent pour les top-15
PNJ (S1, S2, S3 scenarios), tension detector pour les events
geopolitiques (S5, S6).

### 4.2 Pour evolution adaptative des PNJ (§3.5)

**Approche A — Profile vectoriel + drift par events**
Chaque PNJ a un vecteur `personality` continu (~20 dimensions :
aggressivite, loyaute, secret, ambition, peur, idealisme, etc.).
Chaque event vecu par le PNJ modifie le vecteur via une fonction
deterministe (ex: trahison subie → loyaute -0.3, mefiance +0.2).
Le LLM narrateur consulte le vecteur courant pour parler.
- Avantage : continu, emergent, deterministe-reproductible.
- Risque : reduction de la richesse psychologique a 20 chiffres.
- **Pas template-based** car le vecteur evolue continument.

**Approche B — LLM-driven personality drift avec memoire**
A intervalles reguliers, un LLM analyse l'historique recent du
PNJ et reformule sa personnalite en prose libre (~200 tokens).
Cette nouvelle prose remplace l'ancienne. Le narrateur consulte
la prose courante.
- Avantage : richesse expressive, langage naturel.
- Risque : derive incoherente sur 50 tours ; cout LLM.
- **Mitigation derive** : ancrage canon obligatoire dans le prompt
  (prose initiale = canon, modifications uniquement par events
  vecus).

**Approche C — Graph of beliefs + actions**
Modeliser chaque PNJ comme un graphe de croyances (about world,
about others, about self). Events vecus modifient le graphe. LLM
genere les actions a partir du graphe.
- Avantage : raisonnement causal explicite, debuggable.
- Risque : tres complexe.

**Recommandation** : **A + B**. Vecteur pour les rapides updates
deterministes (event simple → impact mesurable), prose libre LLM
pour les snapshots qualitatifs reguliers (tous les 6 mois in-game).

### 4.3 Pour detection d'opportunites narratives (§3.3)

**Approche A — LLM-as-storyteller avec retrieval**
A intervalles reguliers, un LLM "story analyst" recoit le snapshot
du world_state et cherche les "fils narratifs en suspens" en
s'inspirant des patterns canon (recuperes via RAG).
- Avantage : identifie aussi des fils que des regles ne capturent
  pas (ironies, parallels avec le canon).
- Risque : non-deterministe, peut hallu.
- **Pas template-based** car le LLM recoit le canon comme
  inspiration, pas comme script.

**Approche B — Pattern matching sur structures canon abstraites**
Extraire de timeline_events.json les **patterns** (pas les events)
canoniques : "rivalite entre 2 leaders → guerre", "trahison du
mentor → vengeance", "loi injuste → revolution". Ces patterns
sont des templates **abstraits** (pas des events pre-ecrits) que
le matcher applique au world_state pour detecter les fils en
emergence.
- Avantage : ancre dans la grammaire Naruto.
- Risque : si on ne formalise que ce que Kishimoto a deja fait, on
  perd la creativite "what would have been".

**Recommandation** : **A** comme primaire. **B** comme fallback de
qualite quand A propose des trucs trop bizarres.

### 4.4 Pour cohérence à long terme (§3.6)

**Approche A — Invariants checker recurrent**
Definir 20-30 invariants Naruto ("Konoha existe sous une forme",
"le chakra a 5 natures de base", "les hokages sont elus selon des
regles canon"). A chaque snapshot world, verifier les invariants.
Si violation, LLM genere une retro-explication ou refuse l'evolution.
- Effort : moyen.
- **Pas template-based** car les invariants sont abstraits, pas des
  events.

**Approche B — World state semantic diff vs canon baseline**
Calculer regulierement la "distance" du world_state au canon
baseline. Si la distance depasse un seuil, declencher un "anchor
event" qui ramene le monde a une zone proche du canon (sans
forcer le canon, juste sans laisser deriver).
- Risque : sensation de railroad.

**Recommandation** : **A** uniquement. La distance au canon n'est
pas un mauvais signe — au contraire, c'est le but.

---

## 5. Pipeline de donnees a construire

Pour alimenter la creativite (pas la contraindre) :

### 5.1 Reseau de motivations profondes (top-50 PNJ)

Pour chaque grand PNJ canon : `desires` (ce qu'il veut), `fears`
(ce qu'il fuit), `lignes_rouges` (ce qu'il refusera toujours),
`ambitions_secretes` (ce qu'il poursuivra meme contre le canon),
`relationships_arc` (avec qui il a une dynamique cle).

**Source** : LLM extraction sur wiki_sections ("Personality",
"Background"), validation par humain pour le top-15. **Batch
offline** (comme Pass 2 / Pass 5), donc OK d'utiliser Groq pour
le one-shot, ~$1-2. Le resultat (JSON) est utilise en gameplay
sans appel API.

**Differe de behavior_profiles** : behavior_profiles capture le
"comment il parle", motivations capture le "pourquoi il agit".

### 5.2 Cartographie des forces politiques avec relations

Pour chaque village, organisation, faction, clan :
- Allies / ennemis / neutres avec intensite (-1 a +1)
- Capacite militaire / economique / d'information
- Vulnerabilites
- Ambitions territoriales / ideologiques

Stockage : SQL graphe d'environ 200 noeuds + 1000 aretes.

**Source** : extraction LLM sur villages.json + organizations.json
+ wiki_sections.

### 5.3 Index des "principes du monde" (grammaire physique et sociale)

C'est le plus subtil :
- Comment le chakra se transmet (heredite, entrainement, transplant)
- Comment les villages fonctionnent (recrutement, hierarchie, alliances)
- Comment les missions sont attribuees, payees, declarees ratees
- Comment les promotions de rang fonctionnent
- Quelles sont les regles physiques (vitesse max d'un Kage, regles
  de l'invocation, regles du sealing)
- Quelles sont les regles sociales (deference au sensei, code
  d'honneur shinobi, traitement des missing-nin)

Volume : ~50-100 entries en JSON structure.

**Pourquoi crucial** : ce sont les contraintes que le LLM doit
respecter quand il genere creativement. Sans elles, "Sasuke
apprend Mokuton via une transplantation cellulaire" passe le
triplet_check (les deux existent en canon) mais viole les regles
physiques (Mokuton requiert ADN Senju).

**Source** : extraction LLM sur wiki "Trivia", "Abilities",
"Background" du top-20 chars + lore pages dediees. ~$1-2.

### 5.4 Patterns canoniques (la grammaire de Kishimoto)

Pas une liste d'events. Une liste **d'archetypes de retournements** :
- "Le mentor cache une verite sombre" (Itachi, Madara via Tobi)
- "L'orphelin retrouve un parent inattendu" (Naruto / Kushina,
  Sasuke / Itachi)
- "L'ennemi devient allie face a une menace plus grande" (Akatsuki
  / 5 Kage face a Madara)
- "Le sacrifice du mentor declenche la vengeance" (Jiraiya / Naruto)

Entre 20 et 30 archetypes. Le LLM les utilise comme **inspiration**
pas comme script.

### 5.5 Donnees sur les "espaces possibles" (canon etendu)

Films, light novels, what-ifs : Kishimoto et autres auteurs ont
explore certaines branches alternatives. Les capturer comme exemples
de creativite legitimee.

---

## 6. Questions de recherche ouvertes

1. **Etat de l'art emergent storytelling 2025-2026** : qu'est-ce que
   les recents papers (CHI 2025, NeurIPS 2025) disent sur la
   generation narrative emergente cohérente sur des centaines de
   tours ? Est-ce un domaine actif ou stagnant ?

2. **Dwarf Fortress / Crusader Kings / RimWorld** : leurs systemes
   d'emergent storytelling sont-ils applicables ? DF utilise massive
   simulation procedurale, CK utilise event scripts + traits,
   RimWorld utilise reactive narrators (Cassandra). Lequel est le
   plus proche de notre besoin ? **Hypothese** : CK pour la
   generation politique, DF pour la diversite emergente, RimWorld
   pour la pacing — combinaison hybride.

3. **AI Dungeon / NovelAI / agentic frameworks** : comment gerent-ils
   la coherence sur 100k+ tokens ? Quelles techniques de "world
   memory" et "lorebooks" sont reutilisables ?

4. **Effondrement de coherence** : a partir de combien de tours / quelle
   distance au canon le LLM derive-t-il vraiment ? Y a-t-il des
   methodes pour mesurer ca ? **Hypothese** : faire tourner 100
   parties simulees avec actions joueur generees + mesurer divergence.

5. **Conscience joueur des branches non-explorees** : doit-il y
   avoir un meta-narrateur qui dit "dans le canon, Itachi serait
   mort la, mais ici il vit" ? Ou cette information est-elle taboue
   pour preserver l'immersion ?

6. **Validation humaine in-the-loop** : faut-il accepter qu'un
   nombre minimal de scenarios ait besoin de revue humaine pour
   valider la qualite emergente ? Si oui, lesquels ?

7. **Budget hardware local a long terme** : le projet tourne en local
   (llama.cpp avec qwen3-4b GGUF, BGE-M3 sur CPU, ChromaDB persistent
   local). **PAS d'appels API payants en gameplay**. Mais si chaque
   tick = 5-10 appels LLM (multi-agent, tension detector, narrator,
   validator), une partie de 1000 tours = 10000 inferences locales.
   Sur Qwen3-4B local : ~1-3 sec / inference selon le hardware →
   1000 tours = 3-8h de CPU/GPU pure inference. **Question** :
   acceptable ? Latence per-turn ressentie par le joueur ? Le cout
   Groq cite ailleurs dans ce doc concerne UNIQUEMENT les batchs
   offline (Pass 2 extraction canon, Pass 5 tagging) qui sont des
   one-time data prep, pas le gameplay.

8. **Stochasticite vs determinisme** : pour le replay et le debug,
   il faut que les parties soient reproductibles a partir d'une seed.
   Comment combiner LLM (non-deterministe) avec reproductibilite ?
   Cache des sorties LLM par hash de prompt + seed ?

9. **Branches paralleles** : doit-on permettre au joueur de "back
   en arriere" et explorer une autre branche depuis un point ?
   Si oui, comment partager les calculs deja faits ?

10. **Test adversarial de creativite** : comment ecrire un test qui
    valide que le systeme produit un event "creatif et coherent" et
    pas un event "previsible ou hors-canon" ?

---

## 7. Ordre d'implementation suggere

| Phase | Effort | Pre-requis | Couvre |
|---|---|---|---|
| **P1. Reconcilier scheduler doc vs code** | 2 jours | nada | Documentation |
| **P2. Tick autonome + granularite mensuelle** | 1 sem | P1 | §3.1 |
| **P3. Profile vectoriel PNJ + drift par event** | 1-2 sem | none (donnee 5.1 a construire en parallele) | §3.5 |
| **P4. Tension detector LLM-driven** | 2 sem | P3 | §3.3 |
| **P5. Generation events emergents (couplage tension + LLM + injection scheduler)** | 2 sem | P4 | §3.4 |
| **P6. KG dual canon + world (formalise)** | 1 sem | P3 | §3.2 |
| **P7. Invariants checker** | 3 jours | P6 | §3.6 |
| **P8. Diffusion par graphe social + delai** | 1 sem | P3, P6 | §3.8 |
| **P9. Pipeline donnee : motivations top-50 + principes-monde** | 1 sem | LLM batch | §5.1, §5.3 |
| **P10. Variabilite par seed reproductible** | 3 jours | none | §3.7 |
| **P11. Multi-agent simulation top-15 (stretch)** | 4-6 sem | P3, P5 | scenarios S1-S4 |

Quick wins : **P1 + P9 + P10**. Tres rentables, peu d'effort.

Gros morceaux : **P3 + P4 + P5 + P11**. Le coeur de la vision.

Total estime : **3-4 mois solo full-time** pour P1→P10. P11 est un
optionnel a tres haute valeur narrative mais a haute complexite.

---

## 8. Risques principaux

### R1. Derive narrative — incoherence cumulee

Sur 100 tours, le world_state s'eloigne du canon. Les LLM n'ont
plus de baseline propre, hallucinent.
**Mitigation** : invariants checker P7. Snapshot vs canon diff
report tous les 50 tours.

### R2. Budget compute local explose (NOT cost — gameplay is offline)

Le jeu tourne en local : llama.cpp + qwen3-4b GGUF + BGE-M3 CPU +
ChromaDB persistent local. **PAS de cout API en gameplay**, le cout
mentionne ailleurs concerne uniquement les batchs offline de prep
de donnees (Pass 2, Pass 5). Le risque reel ici est la **latence
per-turn** : multi-agent + tension detector + narrator + validator
→ 5-10 inferences locales par tick. Sur CPU Qwen3-4B : ~1-3 sec /
inference → 5-30 sec per turn ressenti par le joueur. Inacceptable
sans optimisation.
**Mitigation** :
- Caching agressif des inferences (hash prompt → output sur disque)
- Sampling : ne consulter que le top-5 PNJ dans le rayon d'interaction
  du joueur, les autres se mettent a jour de maniere differee
- Distillation : un petit modele (Phi-3, Qwen3-0.5B) pour les checks
  rapides (judge, validator), Qwen3-4B reserve a la narration
- Eventual GPU pour les joueurs qui en disposent (LLM-llamacpp
  supporte CUDA)
- Tick rate adaptatif : moins d'inferences quand le joueur est dans
  des phases tranquilles (entrainement, voyage), plus quand action
  critique (combat, dialogue tendu)

### R3. Convergence vers patterns repetitifs

Malgre la variabilite voulue, les LLM tendent a re-tomber dans les
memes archetypes Kishimoto.
**Mitigation** : injecter regulierement des "wild cards" dans le
prompt (eg. element aleatoire de §5.4 absent du contexte recent).

### R4. Joueur perdu dans un monde trop dynamique

Si trop d'events emergents, le joueur ne suit plus.
**Mitigation** : pacing controle par narrative_time. Tag des
events par importance (rumeur faible → silence vs event majeur →
narration). Le joueur ne recoit que ce qui passe son rayon
d'information ET son seuil d'importance.

### R5. Difficulte de debug d'un monde emergent

Un bug dans le tension detector se manifeste 50 tours plus tard
sous forme d'un event bizarre.
**Mitigation** : event_log SQLite avec full causal chain (event X
cancelled because precondition Y violated by player action Z at
turn N). Replay deterministe via seed.

### R6. Perte de "l'essence Naruto"

Apres 20 ans in-game divergents, le monde ne ressemble plus a Naruto.
**Mitigation** : invariants checker. Et acceptation que c'est OK
pour le joueur de jouer dans un monde qui SE FONDE sur Naruto sans
y rester ancre — la vision exige cette liberte.

### R7. Triplet_check anti-emergence

La couche B du validator rejette tout (actor, jutsu) hors
canonical_users. **Bloque la creativite** des qu'un PNJ apprend
quelque chose hors de son arc canon.
**Mitigation** : ajouter un mode "alternate_timeline" qui relaxe
triplet_check. Activable par metadata du tour (year_max > canon_branch_point).

---

## 9. Recap — les 3 manques critiques

Tout le reste decoule de ces trois points :

1. **Tension detector** (§3.3) : sans lui, pas d'event spontane
   emergent. Le systeme reste reactif.
2. **Profile vectoriel + drift par events vecus** (§3.5) : sans lui,
   les PNJ restent figes dans leur template canon.
3. **Pipeline de donnees motivations + principes-monde** (§5.1, §5.3) :
   sans ces ancrages structurels, le LLM derive ou repete.

Le moteur scheduler + cancellation + rumeurs + pathfinder existant
est solide. Il faut le COMPLETER, pas le refaire. Le piege a eviter
est de hardcoder des branches alternatives — la solution est partout
dans la **modelisation des forces** plutot que des **events
specifiques**.

---

## 10. Conclusion

L'ecart est important mais pas insurmontable. Le projet a deja un
moteur de jeu fonctionnel avec scheduler, rumeurs, pathfinder
creatif. Ce qui manque : la **capacite a faire emerger des events
non-prevus** depuis l'etat du monde, et la **plasticite des PNJ**
qui evoluent selon leur experience.

Trois piliers a construire dans cet ordre : profile vectoriel PNJ
(§3.5), tension detector (§3.3), generation event emergent (§3.4).
Avec le pipeline donnees (§5.1, §5.3) en parallele.

Estimation totale : **3-4 mois full-time solo** pour atteindre une
demo de la vision creative sur les scenarios S1-S6 illustres en §1.
