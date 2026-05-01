# 03. Structure du projet

Arborescence complete du projet et role de chaque composant.

## 1. Arborescence racine

```
shinobi_no_sho/
  CLAUDE.md
  README.md
  pyproject.toml
  .env.example
  .gitignore
  .python-version
  ruff.toml
  mypy.ini
  alembic.ini

  docs/                        documentation du projet (ce pack)
  data/                        toutes les donnees
  scripts/                     scripts utilitaires
  src/shinobi/                 code source applicatif
  tests/                       tests pytest
  logs/                        sortie logs (gitignored)
  .venv/                       environnement virtuel python (gitignored)
```

## 2. Dossier data

```
data/
  raw/                         donnees brutes scrapees (gitignored)
    narutopedia/
      pages/                   html brut par page
      cache/                   cache requests
    databooks/
      ocr/                     texte ocr extrait de scans
      structured/              donnees parsees par sources externes
    transcripts/               transcripts d'episodes pour fillers
    _trace.jsonl               log de scraping pour audit

  canonical/                   datasets versionnes, source de verite
    techniques.json
    characters.json
    clans.json
    villages.json
    tailed_beasts.json
    kekkei_genkai.json
    kekkei_mora.json
    hiden.json
    timeline_events.json
    organizations.json
    weapons_tools.json
    world_rules.json
    jutsu_categories.json
    ranks.json
    natures.json
    locations.json
    eras.json
    voice_profiles.json

  embeddings/                  persistance chromadb (gitignored)
    chroma.sqlite3
    [collections internes]

  saves/                       sauvegardes de parties (gitignored)
    [save_id]/
      state.sqlite
      meta.json
      narrative_log.jsonl
      thumbnail.txt

  models/                      modeles telecharges (gitignored)
    llm/
      qwen3-14b-instruct-q4_k_m.gguf
      qwen3-8b-instruct-q5_k_m.gguf
    embeddings/
      bge-m3/
```

## 3. Dossier scripts

```
scripts/
  setup_environment.sh             init complete sur unix
  setup_environment.bat            init complete sur windows
  download_models.py               telechargement gguf et embeddings
  start_llm_server.sh              lance llama.cpp avec les bons params
  start_llm_server.bat
  scrape_narutopedia.py            scraping respectueux narutopedia
  parse_databooks.py               extraction depuis sources databook
  build_canonical_jsons.py         construction des JSON canoniques depuis raw
  validate_canon.py                validation pydantic + checks de coherence
  rebuild_embeddings.py            recompute toutes les collections chroma
  audit_canonicity.py              rapport sur la couverture des sources
  generate_voice_profiles.py       extraction de patterns de dialogue par perso
  export_save.py                   export d'une save vers un format portable
  import_save.py                   import depuis export
```

Tous les scripts sont des modules Python executables avec une interface Typer, sauf les wrappers shell.

## 4. Code source

```
src/shinobi/
  __init__.py
  __main__.py                  entree CLI principale
  config.py                    settings pydantic
  constants.py                 constantes globales
  errors.py                    classes d'exception du projet
  types.py                     type aliases et enums

  canon/                       acces aux donnees canoniques
    __init__.py
    loader.py                  chargement JSON et mirror SQLite
    models.py                  modeles pydantic des datasets
    queries.py                 requetes structurees (par village, par rang, etc.)
    profiles.py                gestion des profils de canonicite
    validation.py              regles de coherence inter-datasets

  rag/                         retrieval augmente
    __init__.py
    chunker.py                 strategies de chunking par type
    embedder.py                wrapper sentence-transformers
    store.py                   wrapper chromadb
    retriever.py               retrieval hybride semantique + filtres
    formatter.py               formattage des chunks pour le prompt
    contextualize.py           selection du contexte pertinent par tour

  engine/                      moteur deterministe
    __init__.py
    character.py               etat du personnage joueur
    stats.py                   systeme de stats et derives
    world.py                   etat global du monde
    actions.py                 resolution d'actions
    learning.py                logique d'apprentissage de techniques
    combat.py                  moteur de combat
    progression.py             vieillissement, rangs, relations
    events.py                  scheduler des evenements canon
    time.py                    temps in-game, calendrier
    economy.py                 ryos, prix, marche
    relations.py               graphe relationnel PNJ
    locations.py               geographie et deplacement
    rng.py                     generateur seed-able

  goals/                       systeme d'objectifs
    __init__.py
    declaration.py             declaration d'objectifs par le joueur
    pathfinder.py              construction des graphes d'objectifs
    breadcrumbs.py             gestion des indices et sous-objectifs
    pricing.py                 calcul du prix d'un indice
    completion.py              detection de completion de sous-objectifs

  llm/
    __init__.py
    client.py                  client http openai-compatible
    prompts.py                 templates de prompts
    schema.py                  schemas json de sortie
    streaming.py               streaming de la generation
    narration.py               orchestrateur de tour narratif
    voices.py                  application des voice_profile

  persistence/
    __init__.py
    database.py                connection sqlite par save
    saves.py                   crud des saves
    schema.sql                 schema sqlite des saves
    migrations/                migrations alembic
    serialize.py               serialisation de l'etat de partie

  cli/
    __init__.py
    app.py                     application typer racine
    character_creation.py      flux de creation de perso
    play.py                    boucle de jeu principale
    menu.py                    menu principal et gestion des saves
    display.py                 rendering rich
    streaming_display.py       affichage progressif des tokens

  api/                         api fastapi pour ui future
    __init__.py
    server.py
    routes/
      __init__.py
      saves.py
      play.py
      canon.py
      health.py

  utils/
    __init__.py
    text.py                    helpers texte (sans em dash, etc.)
    time_utils.py              calcul d'annees signed, conversion
    paths.py                   resolution de chemins relatifs au projet
    json_utils.py              chargement json strict
    hashing.py                 hash deterministes
```

## 5. Tests

```
tests/
  conftest.py                  fixtures pytest
  fixtures/
    canonical_minimal/         dataset reduit pour tests rapides
    saves/                     saves de test
  unit/
    test_canon_loader.py
    test_canon_validation.py
    test_engine_stats.py
    test_engine_actions.py
    test_engine_learning.py
    test_engine_combat.py
    test_engine_time.py
    test_goals_pathfinder.py
    test_persistence_saves.py
    test_rag_chunker.py
    test_rag_retriever.py
  integration/
    test_full_turn_no_llm.py   tour complet avec mock LLM
    test_full_turn_with_llm.py marker requires_llm
    test_save_load_cycle.py
  e2e/
    test_first_year_passive.py simule un perso passif sur 1 an
```

## 6. pyproject.toml minimal attendu

```toml
[project]
name = "shinobi-no-sho"
version = "0.0.0"
requires-python = ">=3.11"
dependencies = [
  "fastapi>=0.115",
  "uvicorn[standard]>=0.30",
  "pydantic>=2.8",
  "pydantic-settings>=2.4",
  "sqlalchemy>=2.0",
  "alembic>=1.13",
  "chromadb>=0.5",
  "sentence-transformers>=3.0",
  "httpx>=0.27",
  "structlog>=24.0",
  "rich>=13.7",
  "typer>=0.12",
  "beautifulsoup4>=4.12",
  "trafilatura>=1.12",
]

[project.optional-dependencies]
dev = [
  "pytest>=8.3",
  "pytest-asyncio>=0.24",
  "pytest-cov>=5.0",
  "ruff>=0.6",
  "mypy>=1.11",
]

[project.scripts]
shinobi = "shinobi.__main__:main"

[tool.ruff]
line-length = 100
target-version = "py311"
```

## 7. .gitignore minimum

```
.venv/
__pycache__/
*.pyc
.pytest_cache/
.mypy_cache/
.ruff_cache/

data/raw/
data/embeddings/
data/saves/
data/models/
logs/

.env
.env.local

*.gguf
*.bin
```

## 8. Conventions internes au code

### 8.1 Frontiere LLM/moteur

Le moteur deterministe ne fait jamais d'appel LLM. Tous les appels LLM passent par `src/shinobi/llm/`. Le moteur expose des objets etat et des fonctions de resolution. Le module `llm.narration` lit l'etat, recupere du contexte RAG, appelle le LLM, recoit du JSON structure, et applique les changements d'etat retournes via le moteur.

Cette separation permet de tester le moteur sans LLM, et de remplacer le modele sans changer la logique de jeu.

### 8.2 Immuabilite par defaut

Les modeles pydantic des donnees canoniques sont immuables (`model_config = ConfigDict(frozen=True)`). Les etats du moteur sont mutables via methodes nommees, jamais par mutation directe d'attributs publics depuis l'exterieur.

### 8.3 Pas d'etat global mutable

Pas de singleton, pas de variable globale mutable. Tout etat passe par injection de dependances. Les seules constantes globales sont dans `constants.py` et sont des immuables.

### 8.4 Async pertinent

Le LLM client est async (httpx async). Le retrieval RAG est sync (chromadb est sync). La CLI est principalement sync, avec des points async aux frontieres LLM. L'API FastAPI est async.

### 8.5 Logs sans accents

Pour eviter tout souci de console Windows, les messages de log sont rediges sans accents. Les chaines visibles dans la narration et dans la CLI peuvent contenir des accents puisque la sortie passe par rich qui gere correctement l'UTF-8.
