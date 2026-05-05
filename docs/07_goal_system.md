# 07. Systeme d'objectifs et breadcrumbs

Ce systeme est ce qui differencie Shinobi no Sho d'un BitLife classique. Toute reussite extreme est atteignable, mais elle exige de trouver et d'executer un chemin canonique. Le LLM ne donne pas le succes, il donne le prochain pas du chemin.

## 1. Philosophie

### 1.1 Le chemin est l'objectif

Quand un joueur veut apprendre une technique impossible pour son niveau actuel, ou rencontrer un personnage canon, ou changer le destin d'un evenement, il ne fait pas un jet de des qui decide. Il decouvre un chemin compose de sous-objectifs concrets, paye un prix en information, et execute chaque sous-objectif par ses propres actions dans le monde simule.

### 1.2 L'information est une ressource economique

Personne ne donne d'information gratuitement. Chaque revelation a un cout :

```
ryos en taverne               petits indices de surface
faveurs                       indices modeles
missions accomplies           indices decisifs
risques personnels            indices critiques
chantage et leverage          indices que l'autre ne donnerait jamais autrement
```

Le moteur calcule le prix d'un indice selon la valeur strategique de l'information et selon qui la detient.

### 1.3 L'indice est partiel

Le LLM ne dit jamais "fais X et tu auras Y". Il dit "pour atteindre Y, voici un point de depart". Le joueur doit alors utiliser ce point de depart, agir, et eventuellement payer un nouvel indice pour la suite. Une chaine d'indices se met en place, qui forme le chemin.

### 1.4 Aucun objectif n'est obligatoire

Le joueur peut ne jamais declarer d'objectif. Sa partie sera alors purement situationnelle, dirigee par les opportunites qui se presentent et par sa curiosite. Le monde tourne autour de lui sans le forcer a viser quoi que ce soit.

## 2. Modele de donnees

### 2.1 Goal

Un objectif declare par le joueur, soit a la creation du perso, soit pendant la partie.

```python
class Goal(BaseModel):
    id: str                          # uuid genere
    declared_at_year: int
    declared_at_age: int
    description_player: str          # texte libre du joueur
    interpretation_canonical: str    # interpretation du LLM via RAG
    target_type: GoalTargetType
    target_id: str | None            # ex: technique_id, character_id, location_id
    status: GoalStatus
    declared_priority: int           # 1 a 10, selon le joueur
    breadcrumbs: list[BreadcrumbRef]
    completed_at_year: int | None
    abandoned_at_year: int | None
```

```python
class GoalTargetType(StrEnum):
    learn_technique = "learn_technique"
    achieve_rank = "achieve_rank"
    kill_character = "kill_character"
    befriend_character = "befriend_character"
    marry_character = "marry_character"
    join_organization = "join_organization"
    leave_village = "leave_village"
    found_organization = "found_organization"
    obtain_object = "obtain_object"
    survive_event = "survive_event"
    prevent_event = "prevent_event"
    cause_event = "cause_event"
    master_kekkei_genkai = "master_kekkei_genkai"
    master_nature = "master_nature"
    revive_character = "revive_character"
    transcend_humanity = "transcend_humanity"   # devenir un dieu, otsutsuki, etc.
    free_form = "free_form"                     # interprete par le LLM
```

### 2.2 Breadcrumb

Un sous-objectif concret deduit du chemin canonique vers l'objectif declare.

```python
class Breadcrumb(BaseModel):
    id: str
    parent_goal_id: str
    sequence_index: int              # ordre dans le chemin
    description: str                 # ce que le joueur doit faire
    canonical_basis: str             # explication LLM avec citations RAG
    completion_conditions: list[CompletionCondition]
    optional: bool                   # certains breadcrumbs sont alternatifs
    revealed: bool                   # le joueur a-t-il pris connaissance de cet indice
    revealed_at_year: int | None
    revealed_by_npc_id: str | None
    price_paid: BreadcrumbPrice | None
    completed: bool
    completed_at_year: int | None
    next_breadcrumbs: list[str]      # ids des breadcrumbs debloques par celui-ci
```

```python
class CompletionCondition(BaseModel):
    type: ConditionType
    parameters: dict[str, Any]
    
class ConditionType(StrEnum):
    visit_location = "visit_location"
    talk_to_npc = "talk_to_npc"
    befriend_npc = "befriend_npc"
    obtain_item = "obtain_item"
    learn_technique = "learn_technique"
    reach_stat_threshold = "reach_stat_threshold"
    survive_event = "survive_event"
    accomplish_action = "accomplish_action"  # generique, evalue par LLM
```

### 2.3 BreadcrumbPrice

Prix paye par le joueur pour debloquer un breadcrumb.

```python
class BreadcrumbPrice(BaseModel):
    type: PriceType
    description: str
    paid: bool
    paid_at_year: int | None

class PriceType(StrEnum):
    money = "money"
    favor = "favor"                  # promesse a tenir envers un PNJ
    sub_mission = "sub_mission"      # accomplir une tache prealable
    reputation = "reputation"        # accepter une perte de reputation
    secret = "secret"                # reveler une information
    physical = "physical"            # blessure, sacrifice physique
    moral = "moral"                  # commettre un acte moralement difficile
    political = "political"          # prendre parti
    none = "none"                    # rare, info gratuite
```

## 3. Flux d'utilisation

### 3.1 Declaration d'objectif a la creation

A la creation du perso, le joueur peut declarer 0 a N objectifs initiaux. Pour chacun :

```
1. Le joueur ecrit son objectif en langage naturel.
2. Le LLM interprete et propose une ou plusieurs interpretations canoniques.
3. Le joueur valide l'interpretation.
4. Le moteur cree un Goal avec status="declared", aucun breadcrumb encore.
```

Aucun breadcrumb n'est genere a cette etape. Les breadcrumbs ne sont generes que sur demande explicite, contre paiement, pendant la partie.

### 3.2 Demande d'indice pendant la partie

A tout moment, le joueur peut faire une action `request_objective_path`, soit pour un de ses objectifs declares, soit pour une nouvelle ambition formulee dans le moment.

Sequence :

```
1. Joueur exprime l'objectif ou cite un objectif declare.
2. Le moteur identifie le contexte (ou est le perso, qui peut detenir l'info, etc.).
3. Le moteur passe la requete au module narration.
4. Le LLM, augmente par RAG, propose un ou plusieurs PNJ ou lieux ou methodes pour obtenir un premier indice.
5. Pour chaque source d'indice, le LLM precise le prix.
6. Le joueur choisit une source d'indice et paye.
7. Le moteur valide la transaction (argent disponible, faveur acceptable, etc.).
8. Le LLM revele un indice de premier niveau, c'est a dire un breadcrumb avec ses conditions de completion.
9. Le moteur enregistre le breadcrumb dans le Goal correspondant ou dans un Goal nouvellement cree.
```

### 3.3 Progression sur un breadcrumb

Le joueur execute des actions normales. A chaque action resolue, le moteur verifie si la condition de completion d'un breadcrumb actif est satisfaite. Si oui :

```
1. Le breadcrumb passe a completed=True.
2. Si next_breadcrumbs sont preetablis, ils deviennent disponibles.
3. Si pas de next_breadcrumbs preetablis, l'objectif retombe dans un etat ou le joueur peut redemander un indice (potentiellement gratuit ou moins couteux maintenant qu'il a progresse).
```

### 3.4 Achevement d'un objectif

Quand toutes les conditions sont reunies (techniques apprises, lieux visites, alliances faites, etc.), l'objectif passe en `status="completed"`. Le LLM produit une narration de cloture.

### 3.5 Abandon

Le joueur peut declarer abandonner un objectif. Cela libere des breadcrumbs et peut avoir des consequences narratives (PNJ a qui une faveur a ete promise s'attend a la voir tenue, etc.).

## 4. Construction d'un chemin

### 4.1 Approche generative augmentee par RAG

Le LLM ne genere pas un chemin a la volee uniquement depuis sa memoire. Il consulte le RAG pour trouver :

- comment l'objectif a ete atteint canoniquement (qui l'a fait, comment)
- quels PNJ sont des sources d'information ou de formation pertinentes
- quels obstacles sont attendus
- quelles preconditions le joueur n'a pas encore

Avec ces informations, le LLM compose un breadcrumb plausible.

### 4.2 Exemple : apprendre Edo Tensei

Contexte : joueur de 8 ans, genin, village de Konoha, an 9.

```
Le joueur demande : "Je veux apprendre Edo Tensei."

Le moteur interroge le RAG :
- technique : kuchiyose_edo_tensei
- canonical_users : tobirama_senju (createur), orochimaru, yakushi_kabuto
- learning_difficulty : 9 sur 10
- prerequisites : maitrise avancee du fuinjutsu, comprehension de l'ame, sacrifice humain
- forbidden_reason : interdiction du Nidaime apres son utilisation

Le moteur identifie qui peut savoir quelque chose en l'an 9 :
- Tobirama est mort
- Orochimaru a ete chassé de Konoha en l'an -4 environ, est en cavale
- Kabuto est un sous-fifre d'Orochimaru, encore inconnu
- Sarutobi Hiruzen, Hokage actuel, connait l'existence et l'interdiction

Premier breadcrumb propose :
  description: "Trouver une trace ecrite de l'existence d'Edo Tensei"
  options de price :
    - 5000 ryos a un informateur du quartier marchand pour entendre une rumeur
    - faveur a un Anbu pour un acces partiel aux archives
    - vol direct des archives interdites du Hokage (haut risque)
  
Le joueur paye 5000 ryos.

Indice revele :
  "Tu apprends qu'une telle technique existe, qu'elle a ete inventee par le Nidaime
   Hokage, et qu'elle a ete formellement interdite. Une rumeur insistante mentionne
   qu'un sannin disgracie aurait poursuivi des recherches similaires en marge de Konoha."

Conditions de completion : information acquise, marquee comme connue.
Next breadcrumbs : "trouver le sannin disgracie" -> debloque sur demande nouvelle.
```

Le joueur peut alors demander un nouvel indice pour la suite, et ainsi de suite.

### 4.3 Variabilite des chemins

Pour un meme objectif, plusieurs chemins peuvent exister selon le contexte du perso. Un Uchiha qui veut apprendre Amaterasu a un chemin different d'un non-Uchiha qui veut acquerir un Sharingan transplante. Le LLM tient compte des origines du perso pour proposer le chemin le plus naturel.

### 4.4 Echec et impasse

Un breadcrumb peut echouer. Si le joueur ne parvient pas a accomplir la sous-mission demandee (PNJ tue, lieu inaccessible, info perdue), le breadcrumb passe en `status="failed"`. Le LLM peut alors proposer un chemin alternatif moyennant paiement, ou l'objectif peut devenir impossible si toutes les voies ont echoue.

## 5. Generation des objectifs implicites

### 5.1 Pas de detection automatique

Sur ta demande, les objectifs sont strictement explicites. Le moteur ne devine jamais un objectif a partir des actions du joueur. Si le joueur agit dans une certaine direction sans declarer d'objectif, le LLM peut suggerer "On dirait que tu cherches a X, veux-tu en faire un objectif explicite ?" mais c'est une suggestion, jamais une creation automatique.

### 5.2 Hooks narratifs

Le LLM peut, dans sa narration de tour normal, suggerer des opportunites :

```
"Tu remarques l'affiche de recrutement des Anbu a la sortie de la tour Hokage."
"Un voyageur etranger murmure des choses interessantes sur Otogakure dans l'auberge."
"Une silhouette familiere te suit depuis trois jours."
```

Ces hooks ne creent pas d'objectifs. Ils signalent des opportunites que le joueur peut decider de poursuivre.

## 6. Pricing

### 6.1 Calcul du prix d'un indice

Le module `goals/pricing.py` calcule le prix d'un breadcrumb selon :

```
valeur_strategique = facteur_rang_objectif * facteur_rarete_info * facteur_difficulty
acceptabilite_morale = penalite_morale_du_demandeur (Orochimaru ne fait pas payer en argent)
position_du_detenteur = facteur_proximite (un Anbu fait payer plus en faveurs qu'en argent)

prix_ryos = base_ryos * valeur_strategique
prix_faveur = nombre_de_faveurs en fonction de valeur_strategique
prix_mission = nombre_de_missions equivalent
```

Le LLM choisit le ou les types de prix selon le contexte du PNJ qui detient l'info. Orochimaru demandera des sujets d'experience. Jiraiya demandera de la loyaute envers Konoha. Un yakuza demandera de l'intimidation politique.

### 6.2 Negociation

Le joueur peut tenter de negocier. Une action `negotiate` est resolue par stat de social_charisma + intelligence + d20 contre la difficulte fixee par le PNJ. La negociation peut reduire le prix de 20 a 50 pourcent en cas de succes, ou aggraver la situation en cas d'echec critique.

## 7. Ecran d'objectifs (CLI puis UI)

A tout moment le joueur peut consulter ses objectifs :

```
[Objectifs declares]

1. Devenir le meilleur ninjutsuiste de ma generation
   Statut : en cours, declare a l'age de 6 ans
   Breadcrumbs actifs :
     - Atteindre Ninjutsu 4.0 (progression : 2.5 / 4.0)
     - Apprendre une technique S rang
     - Vaincre un jonin reconnu en duel public
   Breadcrumbs completes : 2

2. Apprendre Edo Tensei
   Statut : en cours, declare a l'age de 8 ans
   Breadcrumbs actifs :
     - Trouver et approcher Orochimaru sans etre tue
   Breadcrumbs completes : 1

3. Empecher la destruction du clan Uchiha
   Statut : en cours, declare a l'age de 7 ans
   Breadcrumbs actifs :
     - Decouvrir la nature du complot Uchiha
   Note : evenement canonique prevu en l'an 4, il te reste 4 ans.
```

## 8. Persistance

Tous les goals et breadcrumbs sont serialises dans la base SQLite de la save. Voir `11_persistence.md` pour le schema.

## 9. Tests

Tests unitaires :

- creation et serialisation d'objectifs
- calcul de prix selon contexte
- detection de completion d'un breadcrumb apres action
- chainage next_breadcrumbs

Tests d'integration :

- declaration d'objectif a la creation et persistence
- demande d'indice et facturation
- chemin complet d'un objectif simple sur plusieurs tours
