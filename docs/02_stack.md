# 02. Stack technique et hardware

## 1. Machine cible

```
GPU         NVIDIA GeForce RTX 5060 Ti, 8 Go VRAM (architecture Blackwell, compute 12.0)
CPU         recent
RAM         32 Go DDR systeme
Stockage    SSD, prevoir 30 Go libres minimum pour modeles, embeddings, datasets, saves
OS primaire Windows 11
OS secondaire Arch Linux avec Hyprland (compatibilite assuree)
```

Driver NVIDIA requis : R570 minimum pour la prise en charge complete de Blackwell. CUDA Runtime 12.4 ou superieur (les binaires llama.cpp Windows CUDA 12 incluent leurs propres DLL).

Les choix de stack sont contraints par cette configuration. Toute modification matérielle future justifierait une revision de ce document.

## 2. LLM local

### 2.1 Runtime

**llama.cpp en mode serveur HTTP.** Expose une API compatible OpenAI sur localhost. Permet l'offload partiel GPU/CPU. Compatible Linux et Windows. Quantization GGUF native.

Alternative supportee : **Ollama**. Wrapper haut niveau autour de llama.cpp avec gestion des modeles simplifiee. Le code applicatif communique avec l'un ou l'autre via la meme interface HTTP, configuree par variable d'environnement `LLM_BACKEND_URL`.

### 2.2 Modele primaire

**Qwen3 8B Instruct en quantization UD-Q5_K_XL (variante dynamique d'Unsloth).**

Le 8B a ete prefere au 14B parce qu'il tient entierement dans les 8 Go de VRAM de la 5060 Ti, ce qui donne une vitesse 3 fois superieure au 14B en offload partiel. La qualite reste excellente avec la quantization dynamique d'Unsloth qui upcaste les couches critiques.

```
Source                   unsloth/Qwen3-8B-GGUF (Hugging Face)
Fichier                  Qwen3-8B-UD-Q5_K_XL.gguf
Taille sur disque        environ 6.0 Go
Layers totaux            36
GPU offload cible        99 layers (toutes, full GPU)
VRAM utilisee            environ 6.5 Go (modele) + 1 Go (kv cache 16k) = 7.5 Go
RAM systeme utilisee     minimale, KV cache reste sur GPU
Context window           16384 tokens en mode normal, 32768 possible avec YaRN
Temperature              0.7 par defaut, 0.3 pour les sorties JSON structurees
Top-p                    0.95
Top-k                    20
Min-p                    0.0
Repeat penalty           1.1
```

Le serveur llama.cpp est lance avec une commande type sous Windows :
```
llama-server -m models\llm\Qwen3-8B-UD-Q5_K_XL.gguf ^
             -ngl 99 ^
             -c 16384 ^
             --port 8080 ^
             --host 127.0.0.1
```

Sur Linux ou Mac, remplacer les `^` par `\`.

Note importante sur le mode reasoning de Qwen3 : par defaut Qwen3 produit un bloc `<think>...</think>` avant la reponse. Pour Shinobi no Sho, on desactive ce mode en injectant `/no_think` dans les system prompts. Cela accelere les reponses et evite que le moteur de jeu doive parser le bloc de raisonnement.

### 2.3 Modele de fallback rapide (optionnel)

**Qwen3 4B Instruct UD-Q4_K_XL** pour les operations qui n'ont pas besoin de qualite narrative :

- interpretation d'action libre du joueur (classification simple)
- detection de completion de breadcrumb
- compression de l'historique narratif

Tient en 3 Go de VRAM, optionnel.

### 2.4 Modele d'experimentation 14B (optionnel)

**Qwen3 14B UD-Q4_K_XL** reste documente comme option pour des tests de qualite comparative. Sur la 5060 Ti 8 Go, il est utilisable mais lent (offload partiel 28 layers, vitesse 8-15 tokens/s, tour narratif 25-60 secondes). Non installe par defaut.

### 2.5 Strategie de selection

La config par defaut utilise le 8B pour tout. Une variable d'environnement `LLM_MODEL_PATH` pointe vers le fichier GGUF actif. Pas de switching automatique en runtime, le serveur est relance manuellement si l'utilisateur veut changer de modele.

### 2.6 Performances attendues

```
Modele 8B UD-Q5_K_XL, 99 layers GPU, contexte 16k, RTX 5060 Ti :
  Temps de prefill            2 a 5 secondes sur 4k tokens d'entree
  Vitesse de generation       30 a 50 tokens par seconde
  Tour narratif type          8 a 20 secondes
  Tour court (dialogue)       3 a 8 secondes

Modele 14B UD-Q4_K_XL (offload partiel 28 layers, optionnel) :
  Temps de prefill            8 a 15 secondes sur 4k tokens d'entree
  Vitesse de generation       8 a 15 tokens par seconde
  Tour narratif type          25 a 60 secondes
```

Le streaming de la sortie vers la CLI est obligatoire pour masquer la latence percue, meme avec le 8B rapide.

## 3. Embeddings

**BGE-M3 multilingue.** Genere des embeddings denses de dimension 1024. Multilingue, gere correctement le francais et le japonais romaji. Environ 2.3 Go sur disque.

Execution sur CPU via `sentence-transformers`. Une instance est partagee entre tous les retrievers. Generation en lot pour les phases d'indexation initiale (batch size 32).

Alternative supportee : `intfloat/multilingual-e5-large`. Switch via `EMBEDDINGS_MODEL_NAME` dans `.env`.

## 4. Vector store

**ChromaDB en mode persistent local.** Une seule instance, plusieurs collections. Pas de serveur separe, librairie embarquee.

Collections :
```
techniques            embeddings des techniques (nom + description)
characters            embeddings des personnages (nom + bio + role)
clans                 embeddings des clans
villages              embeddings des villages
events                embeddings des evenements de timeline
lore                  chunks de lore generaux scrapes
dialogue_examples     extraits de dialogue par personnage pour few-shot
crossdomain           collection unifiee pour recherches transversales
```

Distance par defaut : cosine.

## 5. Stockage structure

**SQLite** pour :
- mirror des JSON canoniques pour requetes filtrees rapides
- etat des parties en cours
- historique narratif compresse
- index des sauvegardes

Une base SQLite par partie pour isoler les saves. Plus une base globale pour le mirror canonique (lecture seule durant le gameplay).

ORM : `SQLAlchemy 2.x` avec migrations Alembic.

## 6. Application

### 6.1 Langage et runtime

Python 3.11 ou superieur.

### 6.2 Dependances principales

```
fastapi              api interne pour future UI
uvicorn              serveur ASGI
pydantic >= 2        validation et settings
pydantic-settings    config depuis .env
sqlalchemy >= 2      orm
alembic              migrations
chromadb             vector store
sentence-transformers embeddings
httpx                client http vers llama.cpp
structlog            logging json
rich                 rendering CLI
typer                framework CLI
beautifulsoup4       scraping
httpx pour scraping
trafilatura          extraction de texte propre
pytest               tests
pytest-asyncio       tests async
ruff                 lint et format
mypy                 type checking
```

### 6.3 Structure d'execution

```
serveur llama.cpp (process separe, port 8080)
        ^
        |
        | HTTP
        |
application Python (un seul process en developpement)
        |
        +-- module CLI (typer)
        |
        +-- moteur de jeu (pure logic)
        |
        +-- module RAG (lit chromadb)
        |
        +-- module persistence (lit/ecrit sqlite)
        |
        +-- api FastAPI (optionnelle, pour UI future)
```

## 7. Configuration

Toutes les config via variables d'environnement, lues par `pydantic-settings` depuis un fichier `.env` a la racine.

```
# .env.example

LLM_BACKEND_URL=http://127.0.0.1:8080
LLM_MODEL_NAME=qwen3-14b-instruct
LLM_TEMPERATURE=0.7
LLM_MAX_TOKENS=2048
LLM_CONTEXT_SIZE=16384

EMBEDDINGS_MODEL_NAME=BAAI/bge-m3
EMBEDDINGS_DEVICE=cpu

CHROMA_PERSIST_PATH=./data/embeddings
SAVES_PATH=./data/saves
CANONICAL_DATA_PATH=./data/canonical
RAW_DATA_PATH=./data/raw
MODELS_PATH=./data/models

LOG_LEVEL=INFO
LOG_FILE_PATH=./logs/shinobi.log

CANONICITY_PROFILE=default
```

Le profil de canonicite par defaut inclut `manga`, `boruto_manga`, `tbv`, `databook`, `movie_canon`. Le joueur peut le redefinir au lancement d'une partie.

## 8. Logging

`structlog` configure pour produire deux sorties simultanees :

- stdout en plain text colore (via `rich`) pour le developpeur
- fichier rotatif `logs/shinobi.log` en JSON ligne par ligne

Niveaux utilises :
```
DEBUG    details de tour, prompts envoyes au LLM, retrieval rag complet
INFO     evenements de gameplay (tour resolu, technique apprise, etc.)
WARNING  comportements degrades (timeout LLM, retry, modele de secours active)
ERROR    erreurs metier rattrapees
CRITICAL erreurs fatales non rattrapables
```

Aucun log ne contient de donnees sensibles, le projet n'en manipule pas.

## 9. Scripts d'installation

```
scripts/setup_environment.sh    cree venv, installe deps, prepare dossiers
scripts/download_models.py      telecharge qwen3 et bge-m3
scripts/start_llm_server.sh     lance llama.cpp avec les bons params
scripts/start_llm_server.bat    equivalent Windows
```

Le `scripts/setup_environment.sh` est idempotent et peut etre relance sans risque.

## 10. Performances cibles globales

```
Demarrage application                  moins de 5 secondes (hors LLM)
Demarrage llama.cpp + chargement 14B   30 a 90 secondes
Tour de jeu narratif                   moins de 60 secondes wall clock
Sauvegarde de partie                   moins de 1 seconde
Chargement de partie                   moins de 3 secondes
Recherche RAG                          moins de 500 ms par requete
```

Si une de ces cibles n'est pas atteinte en pratique, voir la roadmap pour les optimisations prevues phase 5.
