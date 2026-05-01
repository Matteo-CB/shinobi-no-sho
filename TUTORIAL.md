# Tutoriel de mise en place

Ce document est ton guide pas a pas pour preparer ton environnement avant de lancer Claude Code sur le projet. Suis les etapes dans l'ordre. Coche au fur et a mesure.

Toutes les commandes sont a executer dans **PowerShell** (pas cmd, pas Git Bash). Pour ouvrir PowerShell : touche Windows, taper "powershell", clic droit sur "Windows PowerShell" et "Executer en tant qu'administrateur" pour les premieres etapes.

---

## Etape 1 : Verifier le driver NVIDIA

Tu dois avoir un driver R570 ou superieur pour que ta RTX 5060 Ti fonctionne avec llama.cpp.

```powershell
nvidia-smi
```

Tu dois voir :
- Une ligne avec "NVIDIA GeForce RTX 5060 Ti"
- Une ligne tout en haut a droite avec "CUDA Version: 12.4" ou plus

Si la commande ne marche pas ou si la version CUDA est inferieure a 12.4 :
- Va sur https://www.nvidia.com/Download/index.aspx
- Selectionne RTX 5060 Ti, Windows 11, Game Ready ou Studio Driver
- Telecharge et installe la derniere version
- Redemarre le PC

**Verification** : `nvidia-smi` doit afficher CUDA Version 12.4+ et lister la 5060 Ti.

---

## Etape 2 : Installer Python 3.13

Va sur https://www.python.org/downloads/windows/

Telecharge "Windows installer (64-bit)" pour Python 3.13.x (la derniere version stable, pas la 3.14 qui est trop recente).

Pendant l'installation, **TRES IMPORTANT** :
- Sur le premier ecran, COCHE "Add python.exe to PATH" en bas
- Coche aussi "Use admin privileges when installing py.exe"
- Clique sur "Customize installation"
- Garde toutes les options par defaut
- Coche "Install Python 3.13 for all users"
- Clique sur "Install"

**Verification dans un nouveau PowerShell** :

```powershell
python --version
pip --version
```

Tu dois voir Python 3.13.x et pip 24.x ou 25.x.

---

## Etape 3 : Verifier Git

Git est probablement deja installe (puisque tu utilises Claude Code et GitHub).

```powershell
git --version
git config --global user.name
git config --global user.email
```

Si user.name ou user.email ne renvoient rien :

```powershell
git config --global user.name "Matteo"
git config --global user.email "ton.email@exemple.com"
```

(Remplace par tes vraies infos.)

---

## Etape 4 : Telecharger llama.cpp

Va sur https://github.com/ggml-org/llama.cpp/releases

Prends la **derniere release tout en haut** (par exemple b8995 ou plus recent).

Dans la section "Assets" (en bas, faut cliquer pour deplier), telecharge ces deux fichiers :

1. `llama-bXXXX-bin-win-cuda-12.4-x64.zip` (environ 30 Mo)
2. `cudart-llama-bin-win-cuda-12.4-x64.zip` (environ 370 Mo)

**Important** : prends bien la version CUDA 12 (pas la 13, pas la Vulkan, pas la SYCL).

### Decompresser dans le bon dossier

Cree un dossier `C:\Users\matte\llama.cpp\` (ou ailleurs si tu preferes).

Decompresse les **deux** ZIP dans ce **meme** dossier. Tu dois te retrouver avec un seul dossier qui contient :
- `llama-server.exe`
- `llama-cli.exe`
- Plein de fichiers `.dll` (les CUDA runtime DLL viennent du second ZIP)

### Ajouter au PATH

Touche Windows, taper "variables d'environnement", clic sur "Modifier les variables d'environnement systeme".

Dans la fenetre qui s'ouvre, clic sur "Variables d'environnement..." en bas.

Dans la section du haut (variables utilisateur de matte) ou du bas (variables systeme), trouve la variable `Path`, double-clic dessus.

Clic sur "Nouveau" et tape le chemin du dossier llama.cpp :
```
C:\Users\matte\llama.cpp
```

Valide tout avec OK partout.

**Verification dans un NOUVEAU PowerShell** (les anciens ne voient pas le changement de PATH) :

```powershell
llama-server --version
```

Tu dois voir une ligne avec la version de llama.cpp.

---

## Etape 5 : Telecharger le modele Qwen3 8B

Va sur https://huggingface.co/unsloth/Qwen3-8B-GGUF

Clique sur l'onglet "Files and versions" en haut.

Cherche le fichier `Qwen3-8B-UD-Q5_K_XL.gguf` (environ 6 Go).

Clique sur l'icone de telechargement a droite du fichier (la fleche vers le bas).

Le telechargement va prendre un moment selon ta connexion (c'est 6 Go).

### Placement du fichier

Pour l'instant, mets-le n'importe ou (par exemple `C:\Users\matte\Downloads\`). On le deplacera dans le projet apres.

---

## Etape 6 : Test que llama-server fonctionne avec ta GPU

C'est le test critique avant de continuer. Si ca echoue ici, rien ne marchera apres.

Ouvre PowerShell et lance :

```powershell
llama-server -m C:\Users\matte\Downloads\Qwen3-8B-UD-Q5_K_XL.gguf -ngl 99 -c 16384 --port 8080 --host 127.0.0.1
```

(Adapte le chemin si tu as mis le .gguf ailleurs.)

Tu dois voir defiler des lignes. Repere ces lignes :

```
ggml_cuda_init: found 1 CUDA devices:
  Device 0: NVIDIA GeForce RTX 5060 Ti, compute capability 12.0, VMM: yes
```

Si tu vois ca, ta GPU est bien detectee.

Le modele se charge ensuite (30 a 60 secondes), tu vois beaucoup de lignes "load_tensors". A la fin, tu dois voir :

```
main: HTTP server listening on 127.0.0.1:8080
all slots are idle
```

A ce moment, le serveur fonctionne. Laisse cette fenetre ouverte.

### Test API

Dans un AUTRE PowerShell :

```powershell
curl http://127.0.0.1:8080/health
```

Tu dois recevoir une reponse JSON `{"status":"ok"}` ou similaire.

```powershell
curl http://127.0.0.1:8080/v1/models
```

Tu dois recevoir un JSON avec le modele charge.

Si tout marche : retour dans la premiere fenetre, **Ctrl+C** pour arreter le serveur. On le relancera plus tard depuis le projet.

---

## Etape 7 : Preparer le projet sur ton bureau

Ton projet est deja sur le bureau a `C:\Users\matte\Desktop\shinobi_no_sho\`.

```powershell
cd C:\Users\matte\Desktop\shinobi_no_sho
```

### Creer le dossier models et y mettre le .gguf

```powershell
mkdir models\llm
move C:\Users\matte\Downloads\Qwen3-8B-UD-Q5_K_XL.gguf models\llm\
```

(Adapte si ton .gguf est ailleurs.)

### Creer l'environnement virtuel Python

```powershell
python -m venv .venv
.venv\Scripts\activate
```

Tu dois voir `(.venv)` apparaitre au debut de ta ligne de commande.

### Copier le fichier .env

```powershell
copy .env.example .env
```

Tu peux ouvrir `.env` dans VS Code ou Notepad pour verifier les chemins, mais les valeurs par defaut devraient etre bonnes.

---

## Etape 8 : Configurer le repo GitHub distant

Tu as deja Git configure et un compte GitHub. Cree un nouveau repo prive sur GitHub :

1. Va sur https://github.com/new
2. Repository name : `shinobi-no-sho`
3. Description : "Simulateur de vie narratif Naruto, LLM local + RAG"
4. **Coche "Private"**
5. Ne coche AUCUNE des options "Add README", "Add .gitignore", "Choose a license" (on a deja ces fichiers)
6. Clic "Create repository"

Sur la page suivante, copie l'URL HTTPS ou SSH du repo. Elle ressemble a :
```
https://github.com/tonpseudo/shinobi-no-sho.git
ou
git@github.com:tonpseudo/shinobi-no-sho.git
```

Dans PowerShell, depuis le dossier du projet :

```powershell
cd C:\Users\matte\Desktop\shinobi_no_sho
git init
git add .
git commit -m "initial documentation pack"
git branch -M main
git remote add origin https://github.com/tonpseudo/shinobi-no-sho.git
git push -u origin main
```

(Remplace l'URL par la tienne.)

**Important** : Le commit ne doit pas mentionner Claude. Le message ci-dessus est correct.

---

## Etape 9 : Lancer Claude Code dans le projet

Depuis VS Code, ouvre le dossier `C:\Users\matte\Desktop\shinobi_no_sho`.

Lance Claude Code dans ce dossier. La commande exacte depend de ta config (probablement `claude` dans le terminal integre de VS Code).

Avant de lui donner le prompt, **VERIFIE** que les fichiers suivants sont bien presents :

```
shinobi_no_sho/
  .env                         (copie de .env.example)
  .env.example
  .gitignore
  CLAUDE.md
  README.md
  TUTORIAL.md                  (ce fichier)
  pyproject.toml
  scripts/
    start_llm_server.ps1
  docs/
    01_constraints.md
    02_stack.md
    03_project_structure.md
    04_canonical_data.md
    05_data_pipeline.md
    06_game_engine.md
    07_goal_system.md
    08_world_simulation.md
    09_rag.md
    10_llm_integration.md
    11_persistence.md
    12_cli.md
    13_roadmap.md
  models/
    llm/
      Qwen3-8B-UD-Q5_K_XL.gguf
  .venv/
```

---

## Etape 10 : Le prompt initial pour Claude Code

Voici le prompt exact a passer a Claude Code pour lancer la Phase 0.B. Copie-le tel quel :

```
Lis CLAUDE.md a la racine du projet pour la vision globale, puis lis dans l'ordre
docs/01_constraints.md, docs/02_stack.md, docs/03_project_structure.md, et
docs/13_roadmap.md.

Une fois lu, execute la Phase 0.B (setup applicatif) decrite dans 13_roadmap.md.
Les prerequis manuels (Phase 0.A) ont deja ete faits par moi : Python 3.13 est
installe, llama.cpp est dans le PATH, le modele Qwen3-8B-UD-Q5_K_XL.gguf est dans
models/llm/, et l'environnement virtuel .venv est cree et active.

Contraintes a respecter strictement :
- Aucun em dash dans le code, les commentaires, les commits
- Aucun emoji
- Pas de mention de Claude, Claude Code, Anthropic, ou AI dans les commits
- Pas de Co-Authored-By trailer dans les commits
- Tout en francais sans accents pour les identifiants, francais avec accents
  autorise pour les chaines visibles utilisateur

A la fin de la phase 0.B, fais un commit avec un message du style :
"chore: project bootstrap and configuration"

Si tu rencontres des problemes (par exemple un import qui echoue), arrete-toi et
demande-moi avant de tenter des contournements.
```

---

## Etape 11 : Quand la phase 0.B est terminee

Pour les phases suivantes, le prompt ressemble a :

```
La phase 0.B est terminee. Lis docs/13_roadmap.md pour la phase suivante.
Lis aussi le ou les documents specifiques mentionnes dans cette phase.
Execute les taches dans l'ordre, en faisant des commits intermediaires
quand une sous-section est terminee.
```

La Phase 1 va etre la plus longue (datasets canoniques). Elle implique du scraping,
de l'enrichissement LLM, et beaucoup de revue manuelle. Compte plusieurs sessions.

---

## En cas de probleme

### Le serveur llama.cpp ne demarre pas

Verifie :
- Le driver NVIDIA est bien R570+ (`nvidia-smi`)
- La version CUDA des binaires llama.cpp est bien 12.4 (pas 13)
- Les DLL CUDA runtime sont bien dans le meme dossier que llama-server.exe
- Le chemin du .gguf est correct

### Le serveur demarre mais n'utilise pas la GPU

- Verifie le flag `-ngl 99` (toutes les couches sur GPU)
- Si le modele est trop gros pour la VRAM, baisse a `-ngl 28` ou moins

### pip install echoue

- Verifie que tu es bien dans le venv (`(.venv)` au debut de la ligne)
- Mets a jour pip : `python -m pip install --upgrade pip`
- Si ChromaDB pose probleme, install avec `--no-build-isolation`

### Claude Code ne respecte pas les contraintes

- Rappelle-lui de lire `docs/01_constraints.md` avant de coder
- Verifie ses commits avec `git log` et `git show HEAD` avant de pousser
- En cas de doute, fais `git reset --soft HEAD~1` pour reformer le commit

### Le repo GitHub refuse le push

- Verifie que tu as bien cree le repo en prive (pas public par accident)
- Si l'auth echoue, configure un Personal Access Token GitHub ou une cle SSH
