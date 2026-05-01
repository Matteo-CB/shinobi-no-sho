# Shinobi no Sho

Simulateur de vie narratif dans l'univers de Naruto, pilote par un LLM local et augmente par RAG. Strictement local, sans aucune API payante.

## Vision

Tu nais dans le monde de Naruto, a une annee que tu choisis. Avec les origines que tu choisis ou que le hasard te donne. Tu vis, tu agis, le monde tourne autour de toi en suivant son cours canonique, et tu peux le modifier en intervenant.

Aucune action n'est interdite. Tout est realisable, mais le succes vient du chemin canonique, pas de la chance. Pour atteindre un objectif extreme, tu dois trouver le chemin, payer le prix de l'information, et accomplir les sous-objectifs un a un.

## Documentation

Toute la specification est dans `docs/`. Le point d'entree est `CLAUDE.md` a la racine.

```
CLAUDE.md                  vision et index
docs/01_constraints.md     contraintes dures et conventions
docs/02_stack.md           hardware et stack technique
docs/03_project_structure.md  arborescence
docs/04_canonical_data.md  schemas JSON exhaustifs
docs/05_data_pipeline.md   scraping et construction des datasets
docs/06_game_engine.md     moteur deterministe
docs/07_goal_system.md     systeme d'objectifs et breadcrumbs
docs/08_world_simulation.md  timeline autonome
docs/09_rag.md             systeme RAG
docs/10_llm_integration.md prompts et schemas
docs/11_persistence.md     sauvegardes
docs/12_cli.md             interface CLI
docs/13_roadmap.md         phases d'execution
```

## Hardware cible

```
GPU       NVIDIA RTX 5060 Ti, 8 Go VRAM (Blackwell, compute 12.0)
CPU       recent
RAM       32 Go
Stockage  30 Go libres minimum (modeles + embeddings + datasets + saves)
OS        Windows 11 (primaire)
Driver    NVIDIA R570+ avec CUDA Runtime 12.4+
```

## Demarrage rapide (Windows)

Prerequis manuels (a faire AVANT tout) :

```powershell
# 1. Verifier la GPU
nvidia-smi

# 2. Verifier Python (3.11+ requis)
python --version

# 3. Verifier git
git --version
```

Telechargements manuels :

1. **llama.cpp Windows CUDA 12** depuis https://github.com/ggml-org/llama.cpp/releases
   - `llama-bXXXX-bin-win-cuda-12.4-x64.zip`
   - `cudart-llama-bin-win-cuda-12.4-x64.zip`
   - Decompresser les deux dans le meme dossier (par exemple `C:\Users\matte\llama.cpp\`)
   - Ajouter ce dossier au PATH systeme

2. **Modele Qwen3 8B** depuis https://huggingface.co/unsloth/Qwen3-8B-GGUF
   - Telecharger `Qwen3-8B-UD-Q5_K_XL.gguf` (environ 6 Go)
   - Le placer dans `models/llm/Qwen3-8B-UD-Q5_K_XL.gguf` au sein du projet

Setup applicatif :

```powershell
cd C:\Users\matte\Desktop\shinobi_no_sho
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
copy .env.example .env

# Lancer le serveur LLM dans un terminal separe
llama-server -m models\llm\Qwen3-8B-UD-Q5_K_XL.gguf -ngl 99 -c 16384 --port 8080 --host 127.0.0.1

# Dans un autre terminal, jouer
shinobi new
shinobi play
```

## Etat actuel

Phase 0 : mise en place de l'environnement. Voir `docs/13_roadmap.md` pour le detail des phases.

## Licence

Usage personnel, projet non publie. Le contenu canon de l'univers de Naruto appartient a ses ayants droit. Ce projet est une oeuvre transformee a usage prive.
