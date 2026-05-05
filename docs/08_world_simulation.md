# 08. Simulation du monde et timeline autonome

Le monde de Shinobi no Sho est vivant. Il evolue avec ou sans le joueur. La timeline canonique se deroule selon un scheduler deterministe, et les actions du joueur peuvent en alterer le cours.

## 1. Modele du monde

### 1.1 Etats simules en parallele

A tout instant, le moteur suit l'etat de :

- chaque PNJ canon actif a la date courante (au minimum les figures majeures de l'ere)
- chaque village actif et son climat politique
- chaque organisation active
- les bijuu et leurs jinchuuriki
- les rumeurs en circulation
- l'economie globale

Pour des raisons de performance, l'etat detaille des PNJ est paresseux : un PNJ n'a un etat detaille que s'il est dans un rayon d'interaction avec le joueur, ou s'il est implique dans un evenement de timeline imminent. Les autres PNJ ont un etat resume qui se met a jour sur evenements ponctuels.

### 1.2 Hierarchie d'attention

```
HIGH       le joueur, sa famille, son equipe, son sensei, ses rivaux declares
MEDIUM     les figures politiques de son village et villages allies, ses ennemis directs
LOW        les figures canon majeures actives mais distantes
DORMANT    les figures canon non actives a cette ere (pas nees, mortes, en retraite)
```

Le moteur recalcule la hierarchie au demarrage de partie et a chaque deplacement majeur du joueur. Les PNJ HIGH ont un etat detaille mis a jour a chaque tour. Les MEDIUM toutes les semaines simulees. Les LOW au mois. Les DORMANT seulement sur evenement.

## 2. Scheduler d'evenements canon

### 2.1 Lecture initiale

Au demarrage d'une partie, le moteur charge `timeline_events.json` filtre par le profil de canonicite actif, et installe un scheduler avec tous les evenements de date posterieure ou egale a la date de naissance du joueur (ou date de creation de partie).

Chaque evenement entre dans la file dans l'etat `scheduled` avec ses preconditions et sa date prevue.

### 2.2 Tick de scheduler

A chaque avancement du temps in-game (peut etre plusieurs heures, jours, ou mois selon l'action du joueur), le scheduler :

```
1. Liste les evenements scheduled dont la date prevue est inferieure ou egale a la nouvelle date.
2. Pour chacun, evalue les preconditions sur l'etat courant du monde.
3. Si toutes preconditions sont satisfaites :
   3.1 Marque l'evenement triggered.
   3.2 Applique les outcomes a l'etat du monde (morts, transferts de bijuu, changements politiques, etc.).
   3.3 Genere des rumeurs propagees vers le joueur s'il est dans un perimetre d'information.
4. Si une precondition est violee :
   4.1 Marque l'evenement cancelled ou modified selon une logique decrite plus bas.
   4.2 Genere des consequences en cascade (voir 4.4).
```

### 2.3 Format d'un evenement

Voir `04_canonical_data.md` pour le schema complet. En resume :

```json
{
  "id": "uchiha_clan_massacre",
  "year": 4,
  "preconditions": [
    {"type": "character_alive", "character_id": "uchiha_itachi"},
    {"type": "character_alive", "character_id": "uchiha_sasuke"},
    {"type": "clan_active", "clan_id": "uchiha", "min_members": 30},
    {"type": "no_event_triggered", "event_id": "uchiha_coup_succeeds"}
  ],
  "outcomes": [
    {"type": "clan_decimated", "clan_id": "uchiha"},
    {"type": "character_becomes_missing_nin", "character_id": "uchiha_itachi"},
    {"type": "character_traumatized", "character_id": "uchiha_sasuke"}
  ]
}
```

### 2.4 Logique de cancellation

Quand une precondition est violee, le scheduler doit decider quoi faire de l'evenement. Plusieurs strategies selon le type d'evenement :

```
HARD_CANCEL          l'evenement ne peut tout simplement pas avoir lieu
                     ex : si Itachi est mort, le massacre du clan Uchiha n'a pas lieu

SUBSTITUTE           l'evenement a lieu mais avec des acteurs differents
                     ex : si Naruto est mort, l'attaque du Kyuubi peut quand meme avoir lieu
                          mais le bijuu finit dans un autre receptacle

DELAY                l'evenement est repousse jusqu'a ce que les preconditions soient reunies
                     ex : si Konan n'est pas encore allee a Konoha, sa mort est decalee

CASCADE_CANCEL       l'evenement annule en annule d'autres en cascade
                     ex : si l'attaque du Kyuubi n'a pas lieu, les morts de Minato et Kushina
                          sont annulees, ce qui modifie tous les evenements posterieurs ou ils
                          sont impliques
```

Chaque evenement de `timeline_events.json` declare sa strategie de cancellation dans un champ `cancellation_strategy`. Les valeurs possibles :

```json
{
  "cancellation_strategy": {
    "type": "substitute",
    "substitute_logic": "if jinchuuriki dies, bijuu seeks nearest compatible host within 1 year"
  }
}
```

Pour les cas ou la strategie est trop complexe a structurer, le champ `cancellation_strategy.type` est `narrative_resolution` et le LLM est sollicite pour decider de la suite via un prompt dedie. Ce cas reste exceptionnel.

## 3. Propagation de l'information

### 3.1 Le joueur ne sait pas tout

Un evenement qui se produit a l'autre bout du monde ne parvient pas immediatement au joueur. La propagation suit des regles :

```
distance_proximite      diffusion en heures (meme village)
distance_regionale      diffusion en jours (meme pays)
distance_internationale diffusion en semaines (autre nation)
distance_secrete        diffusion uniquement aux inities (Anbu, Kage, Akatsuki)
```

Les rumeurs sont injectees dans le monde via le module `engine/rumors.py`. Une rumeur a :

- un evenement source
- une fiabilite (distortion possible)
- un canal (taverne, mission breifing, journal ninja, conversation entre Anbu)
- un perimetre de diffusion

### 3.2 Recevoir une rumeur

A chaque tour, le moteur evalue si le joueur peut recevoir une rumeur :

- presence dans une taverne
- mission de reconnaissance
- briefing par le Hokage
- conversation avec un PNJ informateur
- lecture d'un journal

Si oui, le LLM integre la rumeur dans la narration courante. Le joueur n'a pas necessairement la confirmation de la verite : une rumeur peut etre fausse, exageree, ou tronquee.

### 3.3 Connaissance du joueur

L'etat `KnowledgeState` du perso liste ce qu'il sait du monde :

```python
class KnowledgeState(BaseModel):
    known_events: dict[str, KnowledgeLevel]  # event_id -> level (rumor, confirmed, witnessed)
    known_techniques_existence: list[str]
    known_characters: dict[str, CharacterKnowledge]
    known_locations: list[str]
    secrets_uncovered: list[str]
```

Le LLM consulte ce state pour ne pas spoiler le joueur. Si le joueur ne sait rien d'Akatsuki, le LLM ne mentionne pas l'organisation par son nom dans la narration (sauf si un PNJ qui sait la mentionne devant lui).

## 4. PNJ canoniques actifs

### 4.1 Etat d'un PNJ

```python
class NPCState(BaseModel):
    character_id: str
    is_alive: bool
    current_location: str
    current_year: int
    current_age: int
    current_rank: str
    current_affiliations: list[str]
    current_relationships_status: dict[str, RelationshipStatus]
    psychological_state: str
    canonical_arc_progress: CanonicalArcProgress | None
```

`canonical_arc_progress` indique ou en est ce PNJ dans son arc canonique. Si le joueur n'intervient pas, le PNJ progresse normalement. Si le joueur intervient, l'arc peut diverger.

### 4.2 Activite autonome

Entre deux apparitions devant le joueur, les PNJ ne sont pas figes. Le moteur fait avancer leur etat selon leur arc canonique :

```
Sasuke a 12 ans : il est a l'academie, il etudie, il evite les autres.
Sasuke a 13 ans : il a deserte avec Orochimaru.
Sasuke a 16 ans : il erre apres avoir tue Itachi.
Sasuke a 17 ans : il est sauve par Naruto a la fin de la guerre.
```

Le scheduler met a jour ces etapes en arriere-plan, meme si le joueur n'est pas present pour les voir.

### 4.3 Interaction joueur-PNJ

Quand le joueur interagit avec un PNJ canon, le LLM consulte :

- l'etat courant du PNJ (lieu, age, rang, etat psychologique)
- son voice_profile
- les precedentes interactions avec ce joueur (si stockees dans `relationships`)
- son arc canonique a cette date (pour eviter qu'il dise des choses qu'il ne sait pas encore)

Le LLM produit alors un dialogue fidele.

## 5. Divergence accumulative

### 5.1 La timeline diverge mais le moteur tient un journal

Chaque modification du canon par le joueur est enregistree dans une structure `divergence_log` :

```json
[
  {
    "year": 8,
    "type": "character_killed_off_canon",
    "subject": "uchiha_itachi",
    "by_player": true,
    "consequences_propagated": [
      "uchiha_clan_massacre cancelled (Itachi was the actor)",
      "uchiha_sasuke arc altered : remains in Konoha",
      "akatsuki_membership_change : no Itachi recruitment"
    ]
  }
]
```

### 5.2 Resolution narrative des divergences

Pour les divergences complexes, le moteur peut solliciter le LLM avec un prompt special "world resolution" qui demande :

- decrire ce qui se passe a la place de l'evenement annule
- proposer des consequences en cascade plausibles
- generer des rumeurs adaptees

Le resultat est applique a l'etat du monde et marque dans le `divergence_log`.

## 6. Mode passif

Si le joueur reste inactif (action `wait` repetee, voyage long, sejour prolonge en formation), le moteur fait avancer le temps en passant les ticks de scheduler en mode rapide. Les evenements canon se produisent. Les rumeurs s'accumulent. Le joueur reprend la main avec une narration de digest qui resume ce qui s'est passe pendant son absence.

## 7. Persistance

L'etat du monde complet est serialise dans la save. La taille peut etre significative pour les longues parties (centaines de milliers de tours possibles si le joueur vit longtemps). Strategies :

- compression des etats anciens
- sauvegardes incrementales par tick
- snapshot complet toutes les 100 actions, deltas entre snapshots

Voir `11_persistence.md`.

## 8. Tests

Tests unitaires :

- evaluation des preconditions sur etats simulees
- application des outcomes
- propagation de rumeurs sur la base de distances

Tests d'integration :

- partie passive sur 5 ans, verifier que les evenements canon attendus se produisent
- partie active avec divergence sur 1 evenement, verifier les cascades attendues
- partie avec profil de canonicite restreint (manga only), verifier que les events de jeux video sont absents
