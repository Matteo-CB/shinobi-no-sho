# Shinobi no Sho

Simulateur de vie narratif dans l'univers de Naruto, pilote par un LLM local et augmente par RAG. Strictement local, sans aucune API payante.

## Vision

Tu nais dans le monde de Naruto, a une annee que tu choisis. Avec les origines que tu choisis ou que le hasard te donne. Tu vis, tu agis, le monde tourne autour de toi en suivant son cours canonique, et tu peux le modifier en intervenant.

Aucune action n'est interdite. Tout est realisable, mais le succes vient du chemin canonique, pas de la chance. Pour atteindre un objectif extreme, tu dois trouver le chemin, payer le prix de l'information, et accomplir les sous-objectifs un a un.

## Demarrage rapide (Windows, n'importe quel utilisateur)

### Pre-requis (verifies automatiquement par setup)

| Outil | Comment l'installer |
|-------|---------------------|
| Windows 10 / 11 | systeme |
| Python 3.11+ | `winget install Python.Python.3.13` ou https://www.python.org/downloads |
| Git | `winget install Git.Git` ou https://git-scm.com/download/win |
| GPU NVIDIA recommande (8 Go VRAM+) | drivers R570+ |

Sans GPU NVIDIA, le LLM tourne en mode CPU (1-3 tok/s, jouable mais lent).

### Bootstrap automatique

```cmd
git clone https://github.com/Matteo-CB/shinobi-no-sho.git
cd shinobi-no-sho
.\scripts\setup.bat
```

`setup.bat` (wrapper de `setup.ps1`) fait, en idempotent :

1. Verifie Python 3.11+, git, GPU NVIDIA, espace disque (~12 Go)
2. Cree le `.venv`
3. Installe toutes les dependances Python + le projet en mode editable
4. Telecharge **llama.cpp** dans `%USERPROFILE%\llama.cpp\` (build CUDA 12 si NVIDIA, sinon CPU)
5. Telecharge le modele LLM adapte a ta GPU dans `models/llm/` :
   - **Qwen3-1.7B Q4** (~1.1 Go) si <3 Go VRAM ou CPU only -- ultra rapide
   - **Qwen3-4B UD-Q4_K_XL** (~2.5 Go) si 3-9 Go VRAM (RTX 3060/3070/4060/5060) -- equilibre **defaut**
   - **Qwen3-8B UD-Q5_K_XL** (~5.5 Go) si 10-15 Go VRAM -- qualite max
   - **Qwen3-14B Q5_K_M** (~10 Go) si 16-23 Go VRAM
   - **Qwen3-32B Q4_K_M** (~19 Go) si 24+ Go VRAM
   Force un modele specifique avec `-ModelSize tiny|small|medium|large|xlarge`
6. Cree des launchers globaux dans `bin/` et les ajoute au PATH utilisateur
7. Cree `.env` depuis `.env.example`
8. Configure git (user.name, user.email)
9. Lance les tests pour verifier

### Options

```powershell
.\scripts\setup.bat -SkipModel              # zero telechargement modele (4 Go au lieu de 12)
.\scripts\setup.bat -SkipLlama              # ne pas reinstaller llama.cpp
.\scripts\setup.bat -CpuOnly                # forcer mode CPU
.\scripts\setup.bat -Quiet                  # zero prompt interactif
.\scripts\setup.bat -GitRemote <url>        # configurer un remote git
```

### Jouer (le plus simple : double-clic)

Apres setup, **double-clic sur `play.bat`** depuis l'Explorateur. Le launcher :
- Lance setup automatiquement si le venv n'existe pas
- Repare le package si besoin
- Telecharge l'index RAG depuis GitHub Releases au premier lancement (~30 Mo)
- Affiche le menu

Sur Linux/macOS : `./play.sh` au lieu de `play.bat`.

### Jouer (en ligne de commande)

```powershell
# Terminal 1 : serveur LLM
.\scripts\start_llm_server.ps1

# Terminal 2 : jeu (depuis n'importe quel dossier)
shinobi
```

La commande `shinobi` est disponible globalement apres setup, sans activer le venv.

### Index RAG : telechargement automatique

L'index vectoriel ChromaDB (~30 Mo) est **pre-build et publie sur GitHub Releases**. Au premier lancement, le launcher le telecharge automatiquement. Le fingerprint du canon est verifie a chaque demarrage : si tu modifies les datasets, l'index sera invalide et reconstruit en local (1-3 min).

Pour forcer un rebuild local sans telechargement :
```powershell
.\.venv\Scripts\python.exe scripts\rebuild_embeddings.py rebuild --reset
```

Pour les contributeurs qui veulent **publier une nouvelle version de l'index** :
```powershell
# Build + cree dist/rag_index.tar.gz
.\.venv\Scripts\python.exe scripts\build_rag_index.py build --reset

# Upload sur GitHub Releases
gh release create vX.Y --title "RAG index vX.Y" dist/rag_index.tar.gz
```

## Demarrage rapide (Linux / macOS)

```bash
git clone https://github.com/Matteo-CB/shinobi-no-sho.git
cd shinobi-no-sho
chmod +x scripts/setup.sh bin/shinobi
./scripts/setup.sh

# Terminal 1 : LLM
llama-server -m models/llm/Qwen3-8B-UD-Q5_K_XL.gguf -ngl 99 -c 16384 --port 8080 --jinja

# Terminal 2 : jeu
source .venv/bin/activate && shinobi
```

llama.cpp doit etre installe a part : `pacman -S llama.cpp` sur Arch, ou compilation depuis https://github.com/ggml-org/llama.cpp.

## Hardware recommande

```
GPU       NVIDIA 4-8 Go VRAM (RTX 3060/3070/4060/5060 ou superieur)
CPU       recent (4 coeurs+)
RAM       16 Go (32 Go ideal)
Stockage  ~6 Go libres (3 Go modele 4B + 1 Go RAG + setup)
OS        Windows 10/11 ou Linux (Arch valide, autres devraient marcher)
```

Modele defaut : Qwen3-4B (~50 tok/s sur 8 Go VRAM, equilibre vitesse/qualite).
Le setup auto-detecte la VRAM et choisit la bonne taille (1.7B / 4B / 8B / 14B / 32B).
Tout-CPU fonctionne via Qwen3-1.7B mais limite a 1-3 tok/s.

## Documentation technique

Specs detaillees dans `docs/`. Point d'entree : `CLAUDE.md`.

```
CLAUDE.md                    vision et index
docs/01_constraints.md       contraintes de style + conventions
docs/02_stack.md             hardware + stack
docs/03_project_structure.md arborescence du code
docs/04_canonical_data.md    schemas JSON canoniques
docs/05_data_pipeline.md     scraping + pipeline d'enrichissement
docs/06_game_engine.md       moteur deterministe
docs/07_goal_system.md       objectifs + breadcrumbs
docs/08_world_simulation.md  timeline canon autonome
docs/09_rag.md               retrieval augmente
docs/10_llm_integration.md   prompts + schemas LLM
docs/11_persistence.md       sauvegardes
docs/12_cli.md               interface CLI
docs/13_roadmap.md           phases d'execution
```

## Donnees canoniques

Le repo inclut **4 954 entites canoniques** (1 360 personnages, 3 025 techniques, 52 clans, 40 villages, 247 armes, 154 lieux, 32 kekkei genkai, 18 natures, 13 rangs, 13 eres) extraites depuis Narutopedia (Fandom, CC-BY-SA) avec mappings + cross-linking entre clans/personnages/techniques/villages.

## Licence

Code : usage personnel, projet non commercial. Le contenu canon de l'univers de Naruto appartient a ses ayants droit (Masashi Kishimoto, Shueisha). Les donnees canoniques scrapees sont sous licence CC-BY-SA (Fandom). Ce projet est une oeuvre transformee a usage prive.
