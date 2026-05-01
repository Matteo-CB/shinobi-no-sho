# 13. Roadmap

Phases sequentielles pour amener le projet de zero a une partie jouable. Chaque phase a un objectif concret et des criteres de sortie clairs.

## Phase 0 : Mise en place

Objectif : environnement de developpement fonctionnel, structure de projet en place.

### Phase 0.A : Prerequis manuels (a faire AVANT que Claude Code commence)

Ces taches sont effectuees par l'utilisateur, pas par Claude Code :

```
0.A.1  Verifier driver NVIDIA R570+ avec nvidia-smi (CUDA Version >= 12.4)
0.A.2  Installer Python 3.11 ou superieur avec PATH coche
0.A.3  Verifier git installe et configure (user.name, user.email)
0.A.4  Telecharger llama.cpp Windows CUDA 12 (binaire + cudart) depuis github.com/ggml-org/llama.cpp/releases
0.A.5  Decompresser les deux ZIP dans un meme dossier (par exemple C:\Users\matte\llama.cpp\)
0.A.6  Ajouter ce dossier au PATH systeme
0.A.7  Telecharger Qwen3-8B-UD-Q5_K_XL.gguf depuis huggingface.co/unsloth/Qwen3-8B-GGUF
0.A.8  Placer le .gguf dans <projet>/models/llm/Qwen3-8B-UD-Q5_K_XL.gguf
0.A.9  Tester llama-server : la 5060 Ti doit etre detectee, port 8080 doit repondre
0.A.10 Initialiser le repo git distant sur GitHub (sans co-author Claude)
```

### Phase 0.B : Setup applicatif (Claude Code)

```
0.B.1  Creer un environnement virtuel Python (.venv) et l'activer
0.B.2  Installer les dependances dev avec pip install -e ".[dev]"
0.B.3  Configurer ruff, mypy, pytest si pas encore en place dans pyproject.toml
0.B.4  Creer la structure de dossiers complete sous src/shinobi/ et data/
0.B.5  Ecrire src/shinobi/config.py avec pydantic-settings et lecture du .env
0.B.6  Ecrire src/shinobi/errors.py avec les classes d'exception du projet
0.B.7  Configurer le logging structlog (sortie console rich + fichier logs/)
0.B.8  Ecrire un client LLM minimal src/shinobi/llm/client.py
0.B.9  Ecrire un script scripts/test_llm.py qui pingue llama-server et fait un appel test
0.B.10 Pre-telecharger le modele BGE-M3 (sentence-transformers le fait au premier import)
0.B.11 Initialiser une base ChromaDB de test
0.B.12 Ecrire un test pytest bidon qui valide l'import des modules
0.B.13 Verifier que ruff format et ruff check passent
```

Criteres de sortie :
- `python -c "from shinobi.config import settings; print(settings.llm_backend_url)"` affiche l'URL
- `python scripts/test_llm.py` recoit une reponse du serveur llama.cpp local
- `pytest tests/` passe avec 1 test bidon
- `ruff check src/` ne renvoie aucune erreur

## Phase 1 : Donnees canoniques

Objectif : datasets canoniques exhaustifs et valides, embeddings indexes.

Sequencee :

```
1.1   Ecrire les modeles pydantic complets de tous les datasets dans src/shinobi/canon/models.py
1.2   Ecrire le loader src/shinobi/canon/loader.py avec validation pydantic stricte
1.3   Ecrire scripts/scrape_narutopedia.py respectueux (delais, robots.txt)
1.4   Lancer le scraping des categories cibles et stocker dans data/raw/
1.5   Ecrire scripts/parse_narutopedia.py pour produire le JSON intermediaire
1.6   Ecrire les modules d'enrichissement par type (characters, techniques, ...)
1.7   Lancer scripts/build_canonical_jsons.py qui orchestre tout
1.8   Ecrire scripts/validate_canon.py avec les regles de coherence
1.9   Ecrire scripts/audit_canonicity.py et generer le premier rapport
1.10  Session de revue manuelle pour fixer les cas critiques (Naruto, Sasuke, Itachi, etc.)
1.11  Ecrire src/shinobi/canon/queries.py avec les requetes structurees
1.12  Ecrire src/shinobi/canon/profiles.py pour gerer les profils de canonicite
1.13  Ecrire src/shinobi/rag/chunker.py
1.14  Ecrire src/shinobi/rag/embedder.py
1.15  Ecrire src/shinobi/rag/store.py pour ChromaDB
1.16  Ecrire scripts/rebuild_embeddings.py
1.17  Lancer le rebuild complet et verifier les collections
1.18  Ecrire src/shinobi/rag/retriever.py avec query_for_turn et helpers structures
1.19  Tests unitaires sur retriever (vrais embeddings sur dataset minimal)
```

Criteres de sortie : tous les datasets canoniques chargent sans erreur. Le rapport d'audit montre une couverture acceptable (au moins 90 pour cent des perso majeurs canon present, idem techniques). La query "trouve toutes les techniques katon de rang B" retourne des resultats sensés.

Estimation d'effort : 80 a 150 heures, dont 50 a 100h de revue manuelle.

## Phase 2 : RAG et integration LLM

Objectif : pouvoir narrer un tour fictif avec contexte canon recupere.

Sequencee :

```
2.1   Ecrire src/shinobi/llm/client.py avec retry, timeout, validation JSON
2.2   Ecrire les system prompts dans src/shinobi/llm/prompts.py
2.3   Ecrire les schemas JSON de sortie dans src/shinobi/llm/schema.py
2.4   Ecrire src/shinobi/rag/formatter.py pour le formattage du contexte
2.5   Ecrire src/shinobi/rag/contextualize.py qui orchestre la selection
2.6   Ecrire src/shinobi/llm/voices.py pour l'application des voice profiles
2.7   Ecrire src/shinobi/llm/narration.py avec le role NARRATOR
2.8   Tester un tour fictif (input mock TurnContext, sortie narration JSON validee)
2.9   Ajouter le streaming dans src/shinobi/llm/streaming.py
2.10  Ajouter la role GOAL_PATHFINDER dans narration.py
2.11  Tests d'integration marker requires_llm pour narration et pathfinder
2.12  Mesurer la latence d'un tour realiste sur 14B et sur 8B
```

Criteres de sortie : on peut faire `python -m shinobi.llm.narration --mock-context fixtures/turn1.json` et obtenir une narration JSON valide qui respecte le voice profile et utilise le contexte fourni.

## Phase 3 : Moteur de jeu

Objectif : moteur deterministe complet, testable sans LLM.

Sequencee :

```
3.1   Ecrire src/shinobi/engine/character.py avec le modele Character complet
3.2   Ecrire src/shinobi/engine/world.py avec le modele WorldState
3.3   Ecrire src/shinobi/engine/stats.py avec les formules de derive
3.4   Ecrire src/shinobi/engine/rng.py avec generation seedable
3.5   Ecrire src/shinobi/engine/time.py
3.6   Ecrire src/shinobi/engine/actions.py avec le pipeline de resolution
3.7   Ecrire src/shinobi/engine/learning.py
3.8   Ecrire src/shinobi/engine/combat.py
3.9   Ecrire src/shinobi/engine/economy.py
3.10  Ecrire src/shinobi/engine/relations.py
3.11  Ecrire src/shinobi/engine/locations.py
3.12  Ecrire src/shinobi/engine/events.py (scheduler d'evenements canon)
3.13  Ecrire src/shinobi/engine/progression.py
3.14  Tests unitaires extensifs sur chaque module
3.15  Test d'integration : simuler une vie passive de 1 an a partir d'un perso fixe
3.16  Test d'integration : simuler une divergence (tuer un perso canon avant un evenement)
```

Criteres de sortie : couverture de tests >= 70 pour cent sur engine. Une vie passive de 5 ans simule correctement les evenements canon attendus dans le profil par defaut.

## Phase 4 : Persistance

Objectif : sauvegardes robustes et reprises fideles.

Sequencee :

```
4.1   Ecrire le schema SQL de save dans src/shinobi/persistence/schema.sql
4.2   Configurer Alembic
4.3   Ecrire src/shinobi/persistence/database.py
4.4   Ecrire src/shinobi/persistence/saves.py avec CRUD complet
4.5   Ecrire src/shinobi/persistence/serialize.py pour la serialisation des modeles
4.6   Tests unitaires : create, save, load, delete, duplicate
4.7   Test d'integration : sauvegarder 50 tours, recharger, comparer etat
4.8   Implementer l'export et l'import (.shinosave tar.gz)
4.9   Tester export -> delete -> import -> verifier identite
```

Criteres de sortie : on peut sauvegarder, charger, dupliquer, exporter, importer une partie sans perte de donnees.

## Phase 5 : Goals system

Objectif : declaration d'objectifs et generation de breadcrumbs payants.

Sequencee :

```
5.1   Ecrire src/shinobi/goals/declaration.py
5.2   Ecrire src/shinobi/goals/pricing.py
5.3   Ecrire src/shinobi/goals/breadcrumbs.py
5.4   Ecrire src/shinobi/goals/completion.py
5.5   Ecrire src/shinobi/goals/pathfinder.py qui appelle le LLM
5.6   Integration avec le moteur : action request_objective_path declenche le pathfinder
5.7   Integration avec le moteur : detection de completion sur action resolue
5.8   Tests unitaires
5.9   Test d'integration : declarer un objectif simple, demander un indice, executer le sous-objectif, verifier completion
```

Criteres de sortie : un joueur peut declarer "apprendre Edo Tensei", payer un indice, recevoir une etape, l'accomplir, et obtenir l'etape suivante.

## Phase 6 : CLI et boucle de jeu

Objectif : jouer une partie complete en CLI.

Sequencee :

```
6.1   Ecrire src/shinobi/cli/app.py avec Typer racine et sous-commandes
6.2   Ecrire src/shinobi/cli/menu.py avec le menu principal
6.3   Ecrire src/shinobi/cli/character_creation.py avec le flux complet
6.4   Ecrire src/shinobi/cli/display.py pour les panels rich
6.5   Ecrire src/shinobi/cli/streaming_display.py pour le streaming token par token
6.6   Ecrire src/shinobi/cli/play.py avec la boucle principale
6.7   Implementer toutes les commandes meta (/status, /inventory, etc.)
6.8   Test manuel sur Linux : creer un perso, jouer 30 tours, sauvegarder, recharger
6.9   Test manuel sur Windows : meme parcours
6.10  Polish des panneaux et de la lisibilite
```

Criteres de sortie : `shinobi new` puis `shinobi play` permet une session complete sans bug fonctionnel.

## Phase 7 : World simulation et propagation

Objectif : monde vivant convaincant.

Sequencee :

```
7.1   Ecrire src/shinobi/engine/rumors.py avec propagation distance et fidelite
7.2   Ecrire la hierarchie d'attention dans engine/events.py
7.3   Implementer la mise a jour paresseuse des PNJ MEDIUM/LOW
7.4   Implementer le mode passif (digest sur sejour long)
7.5   Implementer la resolution narrative LLM pour les divergences complexes
7.6   Tests d'integration sur scenarios de divergence (tuer Itachi avant le massacre)
7.7   Tests sur partie passive de 10 ans (verifier le plausible deroulement de l'arc)
```

Criteres de sortie : une partie passive de 10 ans produit une chronologie coherente. Une divergence majeure provoque les cascades attendues.

## Phase 8 : Polissage et performance

Objectif : experience fluide, latence acceptable.

Sequencee :

```
8.1   Profiler les tours longs et identifier les hotspots
8.2   Optimiser le retrieval RAG si necessaire
8.3   Optimiser la sauvegarde si necessaire
8.4   Ajouter des indicateurs visuels pendant les attentes longues
8.5   Tester avec le modele 8B en backup et documenter le tradeoff qualite/vitesse
8.6   Compression de l'historique narratif pour les longues parties
8.7   Verifier l'absence de patterns interdits sur 100 tours generes
```

Criteres de sortie : tour standard en moins de 60 secondes wall clock, tous les patterns de style respectes, partie de 100 tours stable.

## Phase 9 : API FastAPI (optionnel pour version CLI seule)

Objectif : exposer le moteur via une API pour permettre une UI future.

Sequencee :

```
9.1   Ecrire src/shinobi/api/server.py
9.2   Ecrire les routes saves, play, canon, health
9.3   Tests unitaires des endpoints
9.4   Documentation OpenAPI auto-generee
```

Criteres de sortie : l'API peut servir une partie complete via HTTP avec la meme logique que la CLI.

## Phase 10 : UI graphique (long terme, non prioritaire)

Choix possibles :

- Tauri + frontend React (leger, multiplateforme, cohabite avec l'API FastAPI)
- Electron (plus lourd mais ecosysteme connu)
- PySide6 (UI native Python)

Decision differee. La CLI doit rester complete et utilisable independamment de toute UI graphique.

## Ordre de priorite

Phases 0, 1, 2, 3, 4 sont sequentielles et obligatoires.
Phase 5 peut commencer en parallele de la fin de Phase 3.
Phase 6 depend de 4, 5.
Phase 7 depend de 3, 6.
Phase 8 depend du reste.
Phases 9 et 10 sont optionnelles et peuvent etre repoussees.

## Estimation globale

```
Phase 0       1 jour
Phase 1       3 a 4 semaines (data heavy)
Phase 2       1 a 2 semaines
Phase 3       2 a 3 semaines
Phase 4       1 semaine
Phase 5       1 a 2 semaines
Phase 6       1 a 2 semaines
Phase 7       1 a 2 semaines
Phase 8       1 semaine
Phase 9       1 semaine
Phase 10      hors scope initial
```

Total pour une version CLI jouable de bout en bout : 12 a 18 semaines de travail soutenu, dont la majorite passe sur le dataset canonique en Phase 1 et le polish sur les divergences en Phase 7.
