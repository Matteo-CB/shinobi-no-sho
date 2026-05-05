# 12. Interface CLI et boucle de jeu

L'interface initiale est en CLI. Une UI graphique pourra etre ajoutee plus tard mais n'est pas prioritaire. La CLI doit etre fluide, lisible, et complete : tout ce qui sera dans une UI future doit deja etre faisable en CLI.

## 1. Stack CLI

```
typer           framework de commandes
rich            rendering colore, panels, tables, prompts
prompt_toolkit  inputs interactifs avec autocompletion (optionnel)
```

Le module `src/shinobi/cli/app.py` definit l'application Typer racine. Les sous-commandes sont :

```
shinobi play                  lance ou reprend une partie
shinobi new                   creation de personnage et nouvelle partie
shinobi list                  liste des saves
shinobi load [save_id]        charge une save specifique
shinobi delete [save_id]      supprime une save (avec confirmation)
shinobi export [save_id]      exporte une save vers fichier
shinobi import [path]         importe une save
shinobi config                gestion de la config
shinobi server                lance le serveur API FastAPI (pour future UI)
```

## 2. Demarrage

Lancer `shinobi` sans argument affiche le menu principal :

```
========================================
    Shinobi no Sho
    Le livre du shinobi
========================================

1. Nouvelle partie
2. Continuer la derniere partie  (Kano Uchiha, an 15, genin)
3. Choisir une partie
4. Importer une save
5. Configuration
6. Quitter
```

Le rendu utilise rich avec un panel encadre. Pas d'emoji, pas de couleurs criardes.

## 3. Creation de personnage

Le flux de creation est sequentiel mais permet de revenir en arriere a chaque etape avec une option dediee.

### 3.1 Mode

```
Mode de creation :
  1. Aleatoire (tout est tire au hasard)
  2. Aleatoire avec contraintes (tu choisis quelques elements, le reste est tire)
  3. Manuel (tu choisis tout)
```

### 3.2 Annee de naissance

```
A quelle annee veux-tu naitre ?

L'an 1 correspond a la naissance canonique de Naruto Uzumaki.
Une valeur negative te place avant cette date (an -55 = fondation des villages).

Annee de naissance [defaut : 1] : 
```

Le moteur valide la coherence (pas avant la fondation des villages, sauf options speciales pour ere des Royaumes Combattants si voulu).

### 3.3 Profil de canonicite

```
Quels univers veux-tu inclure dans ton monde ?

Profil par defaut : manga + boruto manga + tbv + databook + films canon

Personnalisation :
  [x] manga
  [x] boruto manga
  [x] two blue vortex
  [x] databook
  [x] films canon
  [ ] films non canon
  [ ] arcs filler de l'anime
  [ ] novels officiels
  [ ] jeux video (Storm Connections inclus, donc Hikari Uchiha possible)
```

Le joueur navigue avec les fleches, espace pour cocher, entree pour valider.

### 3.4 Origines

```
Village d'origine :
  1. Konohagakure (le pays du feu)
  2. Sunagakure (le pays du vent)
  3. Kirigakure (le pays de l'eau)
  4. Kumogakure (le pays de la foudre)
  5. Iwagakure (le pays de la terre)
  6. Autre village mineur (Otogakure, Amegakure, etc.)
  7. Hors village (clan errant, civil, samurai)
```

Apres choix du village :

```
Clan d'appartenance :
  1. Aleatoire selon les clans actifs a Konohagakure en l'an 7
  2. Choisir manuellement
  3. Civil (pas de clan)
```

Si choix manuel, liste filtree des clans disponibles a cette ere et dans ce village. Avec pour chacun un resume rapide (kekkei genkai, statut, taille).

### 3.5 Famille

```
Statut familial :
  1. Famille typique du clan (deux parents, fratrie aleatoire)
  2. Orphelin
  3. Parent unique
  4. Lignee notable (par exemple branche principale Hyuuga, descendant direct du chef de clan)
  5. Personnaliser
```

Si lignee notable, le moteur propose des options canon (par exemple branche principale Hyuuga, branche secondaire Hyuuga). L'option lignee notable peut etre incompatible avec certains clans : non disponible pour les Uchiha apres l'an 4 sauf si le joueur nait avant l'an 4.

### 3.6 Apparence et genre

```
Genre : male, female, non binary
Apparence : 
  beaute (1 a 5)              tirage aleatoire ou manuel
  taille                      tirage selon age et clan
  couleur cheveux             selon clan ou choix
  couleur yeux                selon clan ou choix
  marques particulieres       optionnel
```

### 3.7 Stats initiales

Dependent de l'age de depart (le joueur peut commencer plus vieux que naissance).

Si depart a la naissance : tirage de stats genetiques selon clan, parents, lineage.
Si depart plus tard : tirage selon age, plus stats acquises.

Le joueur peut choisir un mode :

```
Mode de stats :
  1. Aleatoire equilibre (selon clan et age)
  2. Aleatoire avec une orientation (genie, robuste, charismatique, etc.)
  3. Point buy (manuel, total fixe)
```

### 3.8 Objectifs initiaux

```
Veux-tu declarer un ou plusieurs objectifs de vie maintenant ?

Tu peux en ajouter ou en abandonner pendant ta partie. Si tu n'en declares pas,
tu joueras en mode situationnel sans cap fixe.

[ Saisir un objectif libre ] : 
[ Voir des exemples ]        : 
[ Continuer sans objectif ]  : 
```

Si le joueur saisit un objectif :

```
Objectif : "Devenir le plus puissant ninjutsu user de ma generation"

Interpretations canoniques possibles :
  1. Atteindre un niveau de Ninjutsu de 5.0 dans le databook avant tes 25 ans, en l'emportant
     contre tous les ninjutsu users de ta generation reconnus.
  2. Maitriser au moins une technique de rang S originale.
  3. Etre reconnu publiquement par les Kage des cinq grands villages comme tel.

Choisis l'interpretation, ou ecris une autre formulation : 
```

Une fois confirme, l'objectif est enregistre. Le joueur peut en ajouter d'autres. Aucun breadcrumb n'est genere a ce stade.

### 3.9 Validation

Recap de tout. Le joueur confirme. La save est creee, le moteur initialise l'etat du monde a l'annee de naissance choisie, les PNJ canon sont positionnes a leur etat de cette annee, et le scheduler d'evenements est arme.

## 4. Boucle de jeu principale

```
+---------------------------------------------------------------+
|  Kano Uchiha, 14 ans, genin de Konoha                         |
|  An 15, jour 06-22, 14h                                       |
|  Lieu : terrain d'entrainement numero 3                       |
|  Chakra : 320 / 500    HP : 92 / 100    Ryos : 12 450         |
+---------------------------------------------------------------+

[Narration du tour]

Tu te tiens face au troisieme poteau. Itachi t'observe, distant. Apres un long
silence, il finit par parler.

  Itachi : "Ton pere n'a pas tort sur ton apprentissage du Sharingan. Mais sa
           methode est trop frontale. Le Sharingan ne se forge pas, il s'eveille."

[Actions proposees]

  1. Demander des precisions a Itachi sur sa methode personnelle
  2. Repliquer en defendant la methode de ton pere
  3. Quitter le terrain et te diriger vers le quartier Uchiha
  4. Lancer une discussion sur le statut du clan
  5. Saluer et partir mediter seul
  6. Action libre (ecrire ton intention)

Choix : 
```

### 4.1 Actions libres

L'option `Action libre` ouvre un input ou le joueur ecrit en francais ce qu'il veut faire. Le LLM interprete via `ROLE_CHARACTER_INTERPRETER` et propose une intention structuree. Le joueur valide ou clarifie.

### 4.2 Affichage progressif (streaming)

La narration et le dialogue sont streames token par token via rich Live :

```python
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

with Live(refresh_per_second=20) as live:
    accumulated = Text()
    async for chunk in llm_client.generate_streaming(...):
        accumulated.append(chunk.token)
        live.update(Panel(accumulated, title="Narration"))
```

A la fin du stream, les actions proposees apparaissent (qui ne sont pas streamees car elles dependent du JSON final).

### 4.3 Commandes meta pendant la boucle

A tout moment, le joueur peut taper :

```
/status         affiche les stats detaillees
/inventory      affiche l'inventaire
/techniques     affiche les techniques connues et en cours
/relationships  affiche le graphe relationnel
/objectives     affiche les objectifs et breadcrumbs
/world          affiche un resume du climat politique mondial
/journal        affiche le journal narratif (les N derniers tours)
/divergences    affiche le journal des divergences canoniques
/help           affiche l'aide
/save           force un snapshot complet
/quit           sauvegarde et quitte
```

Ces commandes sont detectees avant l'envoi au LLM. Elles ne consomment pas de temps in-game.

### 4.4 Cloturer un tour

Apres l'action choisie :

```
1. Le moteur resout l'action -> ActionResult.
2. Le moteur applique les changements d'etat.
3. Le moteur fait avancer le temps.
4. Le scheduler d'evenements est tickee.
5. Les rumeurs sont propagees.
6. Le moteur sauvegarde le tour dans la base.
7. Le module narration prepare le prochain TurnContext.
8. Le LLM est appele pour narrer le resultat de l'action et proposer le prochain tour.
9. La narration est streamee a l'ecran.
10. Le joueur fait son choix suivant.
```

## 5. Affichage des stats

Le panneau status detaille :

```
========================================
  Kano Uchiha
========================================
  Age : 14 ans, ne l'an 1, jour 03-15
  Rang : genin
  Village : Konohagakure
  Clan : Uchiha (membre actif)
  Famille : pere Fugaku Uchiha vivant, mere Mikoto Uchiha vivante,
            frere aine Itachi Uchiha vivant

  Stats databook
    Ninjutsu      2.0    Genjutsu      1.5    Taijutsu       2.5
    Intelligence  3.5    Strength      2.0    Speed          3.0
    Stamina       3.0    Hand seals    2.5
    Total         20.0

  Stats etendues
    Chakra pool      500    Chakra control  3.0    Reserves     1.0
    Learning genius  3.5    Charisma        2.5    Leadership   2.0
    Luck             3.0    Beauty          3.5    Lineage      4.5
    Willpower        4.0    Perception      3.0
    Medical          0.5    Fuinjutsu       0.5    Senjutsu     0.0

  Chakra natures
    Maitrisees   katon
    En cours     fuuton

  Kekkei genkai
    Sharingan (stage 1, 1 tomoe)

  Sante
    HP 92 / 100   Fatigue 15   Aucune blessure persistante
```

## 6. Fin de partie

La mort du perso ferme la partie en mode normal. Le journal narratif final est genere comme une biographie. Le joueur peut :

- consulter le monde post-mortem (mode lecture)
- exporter la biographie en .md
- creer un nouveau perso heritant des memoires (mode legendes : un nouveau perso peut entendre des rumeurs sur le precedent)

## 7. Performances

Cibles :

```
demarrage de la CLI         < 1 seconde
ouverture menu principal    < 200 ms
affichage status            < 100 ms
debut d'un tour standard    moins de 1 seconde apres choix
narration complete          fonction du LLM, idealement moins de 60 secondes
```

## 8. Tests

Tests unitaires sur :

- parsing des inputs joueur
- detection des commandes meta
- formattage des panels rich

Tests d'integration :

- creation de perso complete en mode aleatoire
- charger une save et faire 5 tours
- declencher une commande meta pendant un tour

Tests manuels :

- experience utilisateur du flux complet sur Linux et Windows
- verifier la lisibilite des couleurs sur fonds clairs et fonds sombres
