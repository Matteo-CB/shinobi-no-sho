# 06. Moteur de jeu deterministe

Le moteur deterministe est la composante centrale du jeu. Il maintient l'etat du personnage et du monde, resout les actions selon des regles claires, et expose une API pure que le module LLM consomme pour la narration.

## 1. Principes du moteur

### 1.1 Pure logique, aucun appel LLM

Le moteur ne fait jamais d'appel reseau, jamais d'appel LLM. Toutes ses fonctions sont deterministes a seed connu. Cela permet :

- des tests unitaires complets sans LLM
- la reproductibilite des resolutions pour debug
- la possibilite future d'un mode "rejouer un tour avec un seed different"

### 1.2 Etat immutable a chaque tick

Chaque tour produit un nouvel etat plutot que de muter l'ancien. Les transitions sont representees par des fonctions `apply_action(state, action) -> (new_state, action_result)`. L'historique d'etats est conserve pour permettre les fonctionnalites de retro-analyse.

### 1.3 Aucune action n'est refusee

Le moteur ne rejette jamais une intention exprimee. Il la resout. Une intention impossible dans le contexte produit un `ActionResult` de type `contextual_impossibility` motive narrativement (la cible n'existe pas a cette epoque, l'objet n'est pas accessible, etc.).

## 2. Etat du personnage

### 2.1 Modele Character

```python
class Character(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    gender: Literal["male", "female", "non_binary"]
    birth_year: int
    birth_date: str  # MM-DD
    age_years: int
    village_of_origin: str
    current_village: str
    current_location: str
    clan: str | None
    secondary_clan: str | None
    family: FamilyState
    rank: str
    affiliations: list[str]
    is_missing_nin: bool
    is_dead: bool
    death_circumstances: str | None

    stats: CoreStats
    extended_stats: ExtendedStats
    chakra: ChakraState
    health: HealthState
    natures: list[str]
    kekkei_genkai: list[str]
    kekkei_mora: list[str]
    tailed_beast: str | None

    techniques_known: list[KnownTechnique]
    techniques_in_progress: list[LearningTechnique]
    weapons: list[OwnedWeapon]
    summons: list[str]
    inventory: Inventory
    money: int  # ryos

    relationships: list[Relationship]
    reputation: ReputationState
    knowledge: KnowledgeState  # ce que le perso connait du monde

    declared_goals: list[GoalRef]
    active_breadcrumbs: list[BreadcrumbRef]
    completed_breadcrumbs: list[str]

    biography_log: list[BiographyEvent]
```

### 2.2 Stats principales

Inspirees des databooks (echelle 1 a 5) mais etendues :

```python
class CoreStats(BaseModel):
    ninjutsu: float       # 1.0 a 5.0
    taijutsu: float
    genjutsu: float
    intelligence: float
    strength: float
    speed: float
    stamina: float
    hand_seals: float

class ExtendedStats(BaseModel):
    chakra_pool_max: int        # echelle 0 a 10000+
    chakra_control: float       # 1.0 a 5.0
    chakra_reserves: float      # facteur multiplicateur du pool
    learning_genius: float      # 1.0 a 5.0
    social_charisma: float
    leadership: float
    luck: float
    beauty: float
    lineage_value: float        # purete de sang (Uzumaki, Senju, etc.)
    willpower: float
    perception: float
    medical_knowledge: float
    fuinjutsu_knowledge: float
    senjutsu_aptitude: float
```

Toutes les stats sauf chakra_pool_max sont des float entre 0.0 et 5.0 (ou plus pour des perso legendaires comme Hashirama, Madara, Kaguya). Les valeurs au-dessus de 5.0 sont reservees a des etats temporaires (mode senjutsu, manteau de bijuu, etc.) ou a des persos canoniques exceptionnels.

### 2.3 Chakra et sante

```python
class ChakraState(BaseModel):
    current: int
    max: int
    natures_unlocked: list[str]      # natures reellement maitrisees
    natures_partial: list[str]       # natures en cours d'apprentissage
    has_yin_yang_release: bool
    senjutsu_charged: int            # 0 si pas en mode sage

class HealthState(BaseModel):
    hp_current: int
    hp_max: int
    fatigue: int                     # 0 a 100
    injuries: list[Injury]
    permanent_disabilities: list[str]
    mental_state: str                # stable, traumatized, broken, etc.
    poison_status: list[Poison]
```

### 2.4 Techniques

```python
class KnownTechnique(BaseModel):
    technique_id: str
    mastery_level: float             # 0.0 a 5.0
    learned_year: int
    learned_from: str | None         # character_id ou "scroll" ou "self_taught"
    times_used: int

class LearningTechnique(BaseModel):
    technique_id: str
    progress_hours: int              # heures d'entrainement cumulees
    progress_required: int           # calcule depuis difficulty et stats
    started_year: int
    teacher_id: str | None
    quality_modifier: float          # vient de mentor_quality_modifiers
```

### 2.5 Relationships

```python
class Relationship(BaseModel):
    with_character_id: str
    type: RelationshipType
    affinity: int                    # -100 a 100
    trust: int                       # -100 a 100
    history: list[RelationshipEvent]
    secrets_shared: list[str]
    debts_owed: list[Debt]
```

Le graphe relationnel complet est important pour les enseignements et les opportunites narratives.

## 3. Etat du monde

```python
class WorldState(BaseModel):
    model_config = ConfigDict(frozen=True)

    current_year: int
    current_date: str                # MM-DD
    current_hour: int                # 0 a 23

    canonicity_profile: CanonicityProfile
    seed: int                        # pour rng deterministe

    npc_states: dict[str, NPCState]  # etat de chaque PNJ canon actif
    village_states: dict[str, VillageState]
    organization_states: dict[str, OrganizationState]

    scheduled_events: list[ScheduledEvent]
    completed_events: list[CompletedEvent]
    cancelled_events: list[CancelledEvent]
    modified_events: list[ModifiedEvent]

    rumors: list[Rumor]              # rumeurs en circulation
    political_climate: PoliticalClimate

    economy: EconomyState
```

## 4. Resolution d'actions

### 4.1 Pipeline de resolution

Toute action passe par le pipeline suivant :

```
1. CLASSIFY        determiner le type d'action (combat, social, learning, travel, etc.)
2. CONTEXTUALIZE   collecter le contexte (etat perso, etat monde, RAG)
3. CHECK_FEASIBILITY  verifier la faisabilite physique/contextuelle
4. ROLL            executer les jets de des necessaires
5. APPLY_OUTCOME   appliquer les changements d'etat
6. EMIT_EVENT      enregistrer l'evenement dans l'historique
7. PROPAGATE       propager les consequences au monde (relations, reputation, evenements futurs)
```

### 4.2 Types d'actions

```python
class ActionType(StrEnum):
    move = "move"
    talk = "talk"
    train_stat = "train_stat"
    train_technique = "train_technique"
    use_technique = "use_technique"
    fight = "fight"
    spy = "spy"
    steal = "steal"
    buy = "buy"
    sell = "sell"
    work = "work"
    rest = "rest"
    meditate = "meditate"
    research = "research"
    declare_goal = "declare_goal"
    request_objective_path = "request_objective_path"
    pay_for_information = "pay_for_information"
    accept_mission = "accept_mission"
    submit_mission = "submit_mission"
    challenge = "challenge"
    seduce = "seduce"
    intimidate = "intimidate"
    bribe = "bribe"
    pray = "pray"
    custom = "custom"  # action libre interpretee par le LLM
```

`custom` est crucial : il permet n'importe quelle intention exprimee en langage naturel par le joueur. Le moteur passe le texte au LLM via le module narration, qui le classifie, calcule des modificateurs, et redirige vers un type concret ou produit une resolution narrative pure.

### 4.3 Jets de des

Le moteur utilise un systeme d20 modifie. Le module `engine/rng.py` expose :

```python
def roll(seed_state: int, dice: str = "1d20", modifier: int = 0) -> RollResult
```

Les jets sont seedables pour reproductibilite. Le `seed_state` est mis a jour apres chaque jet pour que la sequence reste deterministe.

### 4.4 Formules de base

```
Reussite d'une action a difficulte D, avec stat principale S et modificateur M :
  reussite = (S * 4) + d20 + M >= D

Combat hit roll :
  attaquant_bonus = ceil(taijutsu * 4) + speed_modifier
  defenseur_dc = 10 + ceil(taijutsu_defense * 2) + ceil(speed * 2)
  hit = attaquant_bonus + d20 >= defenseur_dc

Damage roll :
  base_damage = technique_power
  scaling = 1 + (relevant_stat / 10)
  reduction = defender_resistance + armor_value
  damage = max(0, base_damage * scaling - reduction)

Resistance au genjutsu :
  willpower_dc = (utilisateur_genjutsu - cible_genjutsu) * 2 + 10
  resist = cible_willpower + d20 >= willpower_dc
```

Les formules sont calibrables et regrouper dans `engine/combat.py` et `engine/actions.py`. Toute formule a une fonction nommee, un docstring, et des tests unitaires sur des cas connus.

### 4.5 Difficultes types

```
DIFFICULTY_TRIVIAL         5
DIFFICULTY_EASY           10
DIFFICULTY_MODERATE       15
DIFFICULTY_HARD           20
DIFFICULTY_VERY_HARD      25
DIFFICULTY_EXTREME        30
DIFFICULTY_LEGENDARY      40
```

## 5. Apprentissage de techniques

### 5.1 Verification des prerequis

Avant qu'une tentative d'apprentissage commence, le moteur verifie les `prerequisites` de la technique :

```python
def can_attempt_learning(character: Character, technique: Technique) -> LearningEligibility:
    reasons = []
    prereq = technique.prerequisites
    if character.chakra.max < prereq.min_chakra_pool:
        reasons.append(IncompatibleReason.insufficient_chakra)
    if character.extended_stats.chakra_control < prereq.min_chakra_control:
        reasons.append(IncompatibleReason.insufficient_chakra_control)
    for nature in prereq.required_natures:
        if nature not in character.chakra.natures_unlocked:
            reasons.append(IncompatibleReason.missing_nature(nature))
    for tech in prereq.required_techniques:
        if not character_knows(character, tech):
            reasons.append(IncompatibleReason.missing_technique(tech))
    if prereq.kekkei_genkai_restriction and prereq.kekkei_genkai_restriction not in character.kekkei_genkai:
        reasons.append(IncompatibleReason.missing_kekkei_genkai)
    if prereq.clan_restriction and character.clan != prereq.clan_restriction:
        reasons.append(IncompatibleReason.wrong_clan)
    return LearningEligibility(eligible=not reasons, reasons=reasons)
```

Note : meme si le perso n'est pas eligible, l'apprentissage peut etre tente. La fonction renvoie une eligibilite, pas un blocage. Le module `goals` peut transformer un manque en sous-objectif (par exemple, "il te faut d'abord debloquer le Sharingan, voici comment").

### 5.2 Calcul du temps d'apprentissage

```python
def compute_learning_hours_required(character: Character, technique: Technique, mentor_id: str | None) -> int:
    base = world_rules.learning.difficulty_to_hours_baseline[str(technique.learning_difficulty)]
    int_modifier = 1.0 + (character.stats.intelligence - 3.0) * world_rules.learning.stat_modifiers.intelligence_per_point
    cc_modifier = 1.0 + (character.extended_stats.chakra_control - 3.0) * world_rules.learning.stat_modifiers.chakra_control_per_point
    genius_modifier = 1.0 + (character.extended_stats.learning_genius - 3.0) * world_rules.learning.stat_modifiers.talent_genius_per_point
    mentor_modifier = compute_mentor_quality(mentor_id, technique)
    total_modifier = int_modifier * cc_modifier * genius_modifier * mentor_modifier
    return max(1, int(base * total_modifier))
```

### 5.3 Progression

A chaque action `train_technique`, le perso accumule des heures d'entrainement. Quand `progress_hours >= progress_required`, la technique passe de `techniques_in_progress` a `techniques_known` avec `mastery_level = 1.0`.

L'usage repetie de la technique apres apprentissage augmente progressivement le `mastery_level` jusqu'a 5.0.

## 6. Combat

### 6.1 Modele de tour de combat

```python
class CombatRound(BaseModel):
    round_number: int
    initiative_order: list[CombatantRef]
    actions: list[CombatAction]
    state_snapshots: dict[str, CombatantState]
```

Le combat se deroule au tour par tour, avec ordre d'initiative. Chaque combattant choisit une action parmi les techniques connues, taijutsu basique, esquive, garde, ou usage d'objet.

### 6.2 Echange canonique

Le moteur reproduit fidelement les regles d'engagement vues dans le manga :

- Genjutsu casse par perturbation de chakra ou interruption physique
- Hand seals interruptibles si l'attaquant est attaque pendant la sequence
- Substitution (kawarimi) consomme une charge limitee par jour
- Bunshin technique cree des copies avec stats partagees
- Chakra exhaustion entraine fatigue, puis perte de connaissance, puis mort

### 6.3 Mort

La mort est definitive (sauf techniques canon de resurrection accessibles) et termine la partie en mode normal. Le mode "post mortem" permet au joueur de visualiser la suite du monde sans personnage jouable.

## 7. Progression du temps

### 7.1 Granularite variable

Une action n'est pas un jour fixe. Elle dure le temps qu'elle prend en realite simulee :

```
saluer un coequipier              5 minutes
acheter du ramen                  30 minutes
mission D rang en ville           4 heures
mission C rang exterieure         2 jours
session d'entrainement intense    8 heures
voyage Konoha vers Suna           5 jours
sejour de formation a Myoboku     6 mois
```

L'action declaree par le joueur a un champ `duration` calcule par le module `engine/time.py`. Le moteur avance `current_year`, `current_date`, `current_hour` de la duree appropriee.

### 7.2 Vieillissement et progressions automatiques

Quand le temps avance, le moteur applique :

- vieillissement physique (les stats evoluent selon une courbe d'age)
- evolution des relations (decay si pas d'interaction)
- evolution de la reputation
- declenchement des evenements de timeline dont la date est franchie

## 8. Reputation et politique

### 8.1 Reputation par village

Le perso a un score de reputation par village qu'il a visite ou avec qui il a interagi indirectement. Ce score evolue selon :

- missions reussies pour le village
- crimes commis
- alliances ou trahisons
- visibilite (un meurtre en plein jour a plus d'impact qu'un en cachette)

### 8.2 Climat politique global

Le module `engine/relations.py` maintient un graphe de relations entre villages et organisations. Les actions du joueur modifient ce graphe quand elles ont une portee politique.

## 9. Economie

### 9.1 Sources de revenu

```
missions de village          rate par rang depuis world_rules
travail civil                petits revenus stables
butin de combat              variable
vol et crime                 risque de reputation
dons et heritages            evenements rares
investissements              optionnel, complexe
```

### 9.2 Couts

```
nourriture quotidienne       50 ryos par jour minimum
logement modeste             3000 ryos par mois
parchemins de techniques     selon rank, voir world_rules
formation aupres d'expert    10000 a 1000000 ryos selon le rang
informations en taverne      100 a 10000 ryos
dons aux Hokage              variables
```

## 10. Goal completion detection

Quand une action est resolue, le moteur consulte les `active_breadcrumbs` du perso et verifie si l'action complete une condition. Voir `07_goal_system.md` pour les details.

## 11. Frontiere avec le LLM

Le moteur expose au module narration les structures suivantes a chaque tour :

```python
class TurnContext(BaseModel):
    character_state: Character
    world_state_excerpt: WorldStateExcerpt
    last_actions: list[ActionResult]
    available_actions_hints: list[ActionHint]
    nearby_npcs: list[NPCSnapshot]
    active_breadcrumbs: list[Breadcrumb]
    triggered_events: list[CompletedEvent]
```

Le module narration enrichit ce contexte avec du RAG, l'envoie au LLM, recupere une narration et un JSON structure de proposition de tour suivant. Si le joueur valide, un nouvel objet `Action` est cree et passe au moteur.

Le moteur ne lit jamais la narration. Le LLM n'ecrit jamais directement dans l'etat. Tout passage entre les deux mondes est mediatise par les structures pydantic.

## 12. Tests

Tests unitaires obligatoires sur :

- toutes les formules de combat avec cas limites
- calcul de temps d'apprentissage avec mentors connus
- transitions d'etat de tour
- declenchement et annulation d'evenements de timeline
- resolutions d'actions par type
- progression d'age et stats avec le temps

Tests de propriete (hypothesis) sur :

- la stabilite de l'etat sous serialisation/deserialisation
- la commutativite de l'application d'actions independantes
- la conservation du chakra (pas de creation ex nihilo)
