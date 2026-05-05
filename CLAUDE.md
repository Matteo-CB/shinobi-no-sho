# Shinobi no Sho

Simulateur de vie narratif dans l'univers de Naruto, pilote par un LLM local et augmente par RAG. Strictement local, open source, sans aucun appel a une API payante.

Ce document est le point d'entree de la specification du projet. Il donne la vision, les contraintes dures, et l'index des documents detailles. Tout developpement, manuel ou autonome via Claude Code, doit s'appuyer sur ce pack documentaire.

## Vision en une phrase

Le joueur nait dans l'univers de Naruto a une annee qu'il choisit, avec des origines qu'il choisit ou tire au hasard, et vit une existence ou aucune action n'est interdite, ou tout est realisable a condition de trouver le chemin canonique pour y parvenir, et ou le monde continue de tourner autour de lui en suivant la chronologie de l'oeuvre meme s'il ne fait rien.

## Principes fondateurs

**Le canon est la verite mecanique du monde.** Les techniques existantes, les personnages existants, les villages existants, les regles physiques du chakra, tout vient des sources documentees. Rien n'est invente par le moteur.

**La liberte du joueur est totale dans l'intention.** Aucune action ne peut jamais etre refusee a priori par le moteur. Le joueur peut tenter de tuer un Hokage le jour de sa naissance, demander a apprendre Edo Tensei a 5 ans, decider d'abandonner sa carriere ninja pour ouvrir une boulangerie a Iwagakure. Toutes ces intentions sont resolues. Le filtre est la coherence simulationniste, pas une liste d'interdits.

**La reussite vient du chemin, pas de la chance.** Pour atteindre un objectif extreme, le joueur ne lance pas un de et n'espere pas un succes critique. Il demande un chemin, paye un prix d'information, recoit un indice qui le guide, et execute des sous-objectifs concrets. Chaque sous-objectif est lui-meme resolu par les actions du joueur dans le monde simule.

**Le monde est autonome.** La timeline canonique se deroule meme si le joueur n'intervient pas. Si le joueur nait en l'an 8 et reste passif, le massacre du clan Uchiha aura lieu en l'an 9, Naruto entrera a l'academie, Sasuke desertera. Le joueur peut alterer ces evenements en intervenant.

**La timeline est divergente, pas figee.** Tout evenement canon est conditionne par des prerequis. Si le joueur empeche les prerequis, l'evenement ne se produit pas, ou se produit autrement. Le moteur recalcule en permanence l'etat des evenements futurs.

**Aucun cliche stylistique.** Pas de tirets cadratins, pas d'emoji, pas d'argot otaku dans la voix du narrateur. Les personnages parlent selon leur personnalite canonique. Le narrateur decrit. La langue de description est le francais, la terminologie technique est en romaji.

## Organisation de la documentation

```
docs/
  01_constraints.md         contraintes dures, conventions, style
  02_stack.md               hardware cible et stack technique
  03_project_structure.md   arborescence et organisation du code
  04_canonical_data.md      schemas JSON exhaustifs des bases de connaissances
  05_data_pipeline.md       scraping, parsing, construction des datasets
  06_game_engine.md         moteur deterministe, stats, resolution d'actions
  07_goal_system.md         systeme d'objectifs et breadcrumbs
  08_world_simulation.md    timeline autonome, evenements canon
  09_rag.md                 systeme RAG, chunking, embeddings, retrieval
  10_llm_integration.md     prompts, schemas de sortie, narration
  11_persistence.md         sauvegardes, reprises, base SQLite
  12_cli.md                 boucle de jeu CLI, creation de personnage
  13_roadmap.md             phases d'execution sequentielles
```

## Lecture obligatoire avant tout developpement

Lire dans l'ordre :

1. `01_constraints.md` pour internaliser les regles inviolables
2. `02_stack.md` pour comprendre la cible technique
3. `13_roadmap.md` pour situer la phase courante
4. Le document specifique a la phase en cours

Les autres documents sont consultes a la demande.

## Identite du projet

Nom : Shinobi no Sho (le livre du shinobi)
Auteur : Matteo (Hidden Lab)
Licence : usage personnel, non publie
Plateformes : Linux (Arch + Hyprland) et Windows 11
Langue de l'interface joueur : francais
Langue du code et des commentaires : francais sans accents pour les identifiants, francais avec accents pour les chaines visibles, anglais accepte pour les termes techniques universels

## Etat actuel du projet

Phase 0. Aucune ligne de code ecrite. La premiere etape est la mise en place de l'environnement et le scraping des sources, decrits dans `05_data_pipeline.md` et `13_roadmap.md`.
