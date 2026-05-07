# Shinobi no Sho — Récapitulatif complet du projet

Document de passation. Lis ça en premier. Ensuite ouvre le doc 02
pour ce qu'il reste à faire.

---

## 0. Contexte en 30 secondes

**Shinobi no Sho** = simulateur de vie narratif dans l'univers Naruto,
piloté par un LLM local (llama.cpp) avec un pipeline RAG sur le canon
Narutopedia complet.

Le joueur naît à l'année qu'il choisit dans le canon, vit une vie où
aucune action n'est interdite, mais où **la cohérence canon est la
seule contrainte**.

Tout tourne en local. **Zéro coût d'API par tour de jeu.** Les coûts
mentionnés dans ce doc (~$10 cumulés) correspondent à des batchs
**offline one-shot** de préparation de données (extraction canon,
tagging temporel des chunks RAG). Le résultat de ces batchs (JSON,
metadata) est ensuite consulté localement pendant le jeu.

---

## 1. État final du projet

### 1.1 Stats globales

| Métrique | Valeur |
|---|---|
| Tests projet total | 359 / 359 verts |
| Tests anti-hallu | 236 / 236 verts |
| Piliers anti-hallu livrés | 7 / 8 (le 8e optionnel) |
| Sous-projet canon completion | 1359 / 1360 persos extraits |
| Chunks RAG indexés | 15939 (Chroma BGE-M3 dim 1024) |
| Chunks RAG indexés BM25 | 15940 (bm25s sparse) |
| Chunks taggés temporellement | 15937 / 15939 (99.99%) |
| Coût LLM cumulé (batchs offline one-shot) | $10.30 |
| Lignes code Python (src/) | ~700 ajoutées cette session |
| Lignes tests | ~600 ajoutées cette session |

### 1.2 Architecture en couches

```
┌─────────────────────────────────────────────────────────────┐
│ COUCHE PRÉSENTATION                                         │
│  src/shinobi/cli/play.py (1691 lignes)                       │
│  Boucle de jeu : missions, shop, pathfinder, travel,         │
│  desertion, scheduler tick, narration                        │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│ COUCHE ANTI-HALLUCINATION (7 piliers)                       │
│                                                              │
│  §2 Guards I/O ────────► blacklist + intent + output filter │
│  §3 Validator A+B+C ───► sherlock + triplet + age + risk    │
│  §4 State tracker ─────► RuntimeState + age_calculator       │
│  §5 Re-tag temporel ───► 16k chunks taggés arc/year/tier    │
│  §6 Enums + struct ────► 7 enums canon + Pydantic strict    │
│  §7 Risk-tagger ───────► 4 niveaux, route vers couches      │
│  §8 Hybrid retrieval ──► BM25 + Chroma + RRF + reranker     │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│ COUCHE MOTEUR DE JEU                                         │
│  src/shinobi/engine/ (3985 lignes, 21 modules)              │
│  scheduler events, world state, missions, combat,           │
│  économie, rumeurs, relations, progression                  │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│ COUCHE DONNÉES                                               │
│  data/canonical/   1359 chars + 3025 jutsus + 52 clans      │
│  data/canon/       7 enums extraits                         │
│  data/embeddings/  ChromaDB 15939 vecteurs BGE-M3 dim 1024  │
│  data/bm25/        bm25s index 15940 chunks                 │
└─────────────────────────────────────────────────────────────┘
```

### 1.3 Stack technique

**Modèles LLM en runtime (tous locaux)** :
- **Narration** : Qwen3-4B GGUF Q4_K_M via llama.cpp (~1-3 sec/inference CPU)
- **Embedding** : BGE-M3 dim 1024 via sentence-transformers CPU
- **Reranker** : BAAI/bge-reranker-v2-m3 via sentence-transformers
- **Vector store** : ChromaDB persistent local
- **BM25** : bm25s in-memory

**Modèles LLM offline (one-shot pour préparation données)** :
- **Llama 3.3 70B** via Groq Batch API ($0.59/$0.79 par M tokens)
- Utilisé pour Pass 2 (extraction canon perso) et Pass 5 (tagging temporel chunks)
- Batchs one-shot, résultats stockés en JSON

**Stack Python** :
- Python 3.11+ (uv pour gestion env)
- Pydantic v2 (validation stricte schemas)
- pytest pour tests
- httpx pour API clients
- Pas de framework lourd, libs minimales

**Hardware cible joueur** :
- CPU 8+ cores recommandé
- 16 GB RAM minimum
- 5 GB disque (modèles + index + canon)
- GPU optionnel (accélère mais pas requis)

---

## 2. Détail des 7 piliers anti-hallucination livrés

### 2.1 Pilier §2 — Garde-fous I/O et persona enforcement

**Fichiers** : `src/shinobi/guards/` (3 fichiers, ~540 lignes)

**Ce qu'il fait** :
- Blacklist regex 50+ termes hors-univers (Python, ChatGPT, Marvel, etc.)
- Intent classifier en 5 catégories (in-character action, query, jailbreak, OOC, ambiguous)
- Output filter : détecte méta-phrases ("en tant qu'IA"), casse 4e mur, redirige in-character
- Reference resolver pour pronoms/ellipses ("je vais le voir" → résolu via state)
- System prompt durci avec 6 few-shot redirections in-character

**Latence** : sub-milliseconde, **zéro appel LLM**

**Tests** : 56 adversariaux, 100% pass

**Service à la vision finale** : protection minimale, pas de rôle créatif

---

### 2.2 Pilier §3 — Validator multi-couches

**Fichiers** : `src/shinobi/validation/` (6 fichiers, ~815 lignes)

**Architecture** : 5 couches conçues, 3 implémentées (A, B, C). Couches D (NLI) et E (LLM judge) prévues mais reportées.

**Couche A — Sherlock rules** (déterministe) :
- `sherlock_rules.py` : aucune action attribuée à un perso mort dans le state
- Aucune scène dans un lieu détruit
- Aucun perso dans deux lieux simultanés
- Hard reject par règles, pas de LLM

**Couche B — Triplet check** (déterministe) :
- `triplet_check.py` : pour chaque action `(actor, jutsu)`, vérifie `actor in jutsu.canonical_users`
- Skip généreux : jutsu inconnu, generic role, actor manquant
- ⚠️ Anti-emergence en mode strict — voir doc 02 pour la solution

**Couche C — Age coherence** (déterministe + heuristiques) :
- `age_coherence.py` : Naruto à 5 ans peut pas dire "stratégie diplomatique optimale"
- Vocabulaire vs âge calculé via `get_age(char, year)`

**Risk-tagger** :
- `risk_tagger.py` : décompose NarrativeOutput en segments tagués low/medium/high/very_high
- `required_layers_for_risk()` map vers couches à activer
- Optimise latence : skip validation lourde sur prose descriptive

**Mode** : short-circuit (premier reject stop) ou cumulative (tous checks).

**Tests** : 27 + 12 + 16 + 12 = 67 tests, 100% pass

---

### 2.3 Pilier §4 — State tracker runtime

**Fichiers** : `src/shinobi/state/` (2 fichiers, ~316 lignes)

**Ce qu'il fait** :
- `RuntimeState` Pydantic : current_year, location, present_chars, last_mentioned_character, characters_dead, destroyed_locations, key_events_resolved
- `age_calculator.py` : `get_age(char_id, current_year)` déterministe, gère les cas Edo Tensei (perso mort mais réincarné dans certains arcs)
- `CanonView` Protocol pour découpler des fichiers JSON canon

**Pattern** : ask vs tell. `is_alive(char, year)` déterministe au lieu d'appeler le LLM. `get_age` calcul déterministe au lieu d'inférer.

**Tests** : 34 + 15 (migration prompt) = 49 tests, 100% pass

---

### 2.4 Pilier §5 — Re-tagging temporel des chunks RAG

**Scripts** : `scripts/pass5_tag_chunks.py` (CLI build/submit/poll/parse)

**Ce qui a été fait** :
- 15939 chunks RAG extraits du canon (wiki_sections déjà scrapés)
- Embedding BGE-M3 dim 1024 via sentence-transformers CPU (~3.6h)
- Index BM25 via bm25s (1.7s)
- Calibration tagging sur 100 chunks ($0.05) → 100/100 OK
- Full batch tagging sur 15839 chunks ($7.95) → 15837/15839 OK (99.99%)
- Application metadata : 31882 updates dans Chroma (multi-collections)

**Metadata ajoutées par chunk** :
- `arc` : id de l'arc canon (wave, chunin_exam, pain_invasion, boruto_chunin_exam, etc.)
- `year_min`, `year_max` : fenêtre temporelle
- `tier` : manga / databook / anime canon / filler / boruto
- `entities_mentioned` : persos/lieux apparents

**Coût Groq** : $8 (one-shot, jamais répété)

**Service à la vision** : permet au retrieval de **filtrer temporellement** au gameplay. À year 12 (arc Wave), exclusion automatique des chunks year_min > 12. Plus de Boruto qui apparaît pendant Wave.

---

### 2.5 Pilier §6 — Enums canon + structured generation

**Phase A — Extraction enums** :
- Script `scripts/pass6_extract_enums.py`
- Lecture des fichiers `data/canonical/*.json`
- Extraction vers `data/canon/` :
  - `character_list.json` (1360 ids)
  - `jutsu_list.json` (3025 ids + canonical_users)
  - `location_list.json` (154 ids)
  - `village_list.json` (40 ids)
  - `clan_list.json` (52 ids + key_*/available_*)
  - `kekkei_genkai_list.json` (32 ids + eligible_clans)
  - `nature_list.json` (18 ids)
- **Integrity check** : 0 jutsu user orphelin sur 2712, 0 KG carrier orphelin, 0 clan key orphelin

**Phase B — Structured output** :
- `src/shinobi/generation/structured_output.py`
- Décision : Pydantic post-validation (pas Outlines, pas de dépendance lourde)
- `parse_narrative_output(dict) → NarrativeOutput | StructuredOutputError`
- Schéma `NarrativeOutput` contraint sur les enums canon
- Le LLM ne peut pas inventer "Mille Oiseaux Glaciaux", il est borné aux ids existants

**Branchement réel** :
- `src/shinobi/llm/narration.py` : `Narrator.__init__(enable_anti_hallu_validation=None)` (default = settings.enable_anti_hallu_validation = True)
- Validator A+B+C tourne dans la boucle de retry
- Violations injectées dans le `retry_correction` prompt
- Max 2 regens, fallback message in-character si échec
- **Flag opt-in pour rollback rapide en cas de bug**

**Tests** : 9 + 16 = 25 tests, 100% pass

---

### 2.6 Pilier §7 — Risk-tagger (déjà détaillé dans §3)

Voir section 2.2.

---

### 2.7 Pilier §8 — Hybrid retrieval

**Fichiers** : `src/shinobi/retrieval/` (4 fichiers + adapters)

**Architecture** :
- `types.py` : Protocols `BM25Index` / `DenseIndex` / `Reranker` (découplage stricte)
- `rrf.py` : Reciprocal Rank Fusion (Cormack 2009) pour combiner les rankings
- `hybrid_search.py` : `HybridSearcher` composable, prend un BM25 + un Dense
- `reranker.py` : `CrossEncoderReranker` (lazy-load bge-reranker-v2-m3) + `FakeReranker` pour tests
- `chroma_store.py` : adapter ChromaDB → DenseIndex Protocol
- `bm25_store.py` : adapter bm25s → BM25Index Protocol

**Pipeline runtime** :
1. Query joueur enrichie via reference resolver
2. BM25 cherche par mots exacts (idéal pour noms japonais : "Hatake Kakashi", "Tsukuyomi")
3. Dense ChromaDB cherche par sens sémantique (idéal pour paraphrase : "le ninja copieur")
4. RRF combine top-50 BM25 + top-50 dense → top-100 fusionné
5. Reranker bge-v2-m3 reorder top-100 → top-5 ou top-10 final
6. **Filtre `narrative_year`** appliqué : exclut chunks year_min > current_year

**Validation** : 10 scénarios end-to-end. **BM25 isolé : 8/10. Dense isolé : 8/10. Hybrid RRF : 10/10**. Les deux sont complémentaires.

**Tests** : 12 algorithmiques + 10 e2e = 22 tests, 100% pass

---

## 3. Sous-projet canon completion (imprévu mais critique)

### 3.1 Le problème découvert

L'ancien scraper Narutopedia (fait avant ce projet) avait corrompu la base canon par cooccurrence : Sarutobi avec Sharingan (Hiruzen affronte Orochimaru qui utilise Sharingan → cooccurrence → tagué possesseur), Senju avec Sharingan, Shukaku catalogué Otsutsuki, etc. **13 corruptions confirmées sur 16 cas vérifiés**.

Sans correction, les piliers §5 et §6 auraient tourné sur de la fausse data.

### 3.2 La solution livrée

**Pass 2 — Extraction LLM ciblée** (Groq Llama 3.3 70B, Batch API) :
- 1359 personnages → wiki sections de chacun → extraction structurée stricte
- Consigne : "extrait uniquement ce qui est ATTESTÉ TEXTUELLEMENT, distingue possession vs cooccurrence"
- Validation par grep des `source_quote` (NFKD normalisation, edit_distance ≤ 5)
- Coût : $2.30

**Pass 2.5 — Dérivation déterministe** :
- Pour chaque perso sans birth_year explicite mais avec age_at_event ou relative_age_to
- Chaînage transitif via `arc_temporal_anchors.json`
- Itération jusqu'à convergence

**Pass 3 — Agrégation par clan avec seuils différenciés** :
- `key_kekkei_genkai` (signature obligatoire) : 50%+ ET 3+ membres attestés
- `available_kekkei_genkai` (éligibilité) : 30%+ ET 3+ membres attestés
- `individual_mutation` (1-2 membres seulement, ex: Mokuton/Hashirama)
- 10/14 grands clans canon validés correctement
- Reconstruction `clans.json` et `kekkei_genkai.json` depuis stats agrégées
- Backups `*.pre_pass2_backup` préservés

**Output** :
- `research/scraper-corruption-report.md` : 13 corruptions taggées
- `research/canon-completion-report.md` : couverture par source/confidence
- `research/canon-cleanup-handoff.md` : doc passation

---

## 4. Pipeline de batchs offline (one-shot, pas en gameplay)

Tous les batchs LLM ont été faits **une seule fois** lors de la session de prep. Les résultats sont stockés en JSON et **consultés localement** pendant le jeu.

| Batch | Modèle | Coût | Output |
|---|---|---|---|
| Pass 2 (canon completion) | Llama 3.3 70B Groq | $2.30 | data/canonical/_pass2_output/*.json |
| Pass 5 (tagging temporel) | Llama 3.3 70B Groq | $8.00 | metadata Chroma (15937 chunks taggés) |
| **Total** | | **$10.30** | |

**Aucun de ces batchs n'est répété en gameplay.** Si tu modifies des données canon, tu peux les relancer, mais c'est rare et toujours offline.

---

## 5. Documents produits dans `research/`

Tous les rapports techniques de la session :

**Rapports de phase** :
- `phase1-runbook.md` — runbook Phase 1 complet
- `scraping-pipeline-audit.md` — découverte que scraping déjà fait
- `RELEASE_NOTES.md` — notes de release Phase 1

**Architecture et roadmap** :
- `anti-hallucination-rag-narratif-v2.md` — doc principale (référence)
- `timeline-engine-roadmap.md` — Phase A→E pour scheduler/KG dual/propagation (à reconcilier avec doc 02)
- `world-simulation-gap-analysis.md` — audit créativité émergente (entrée pour doc 02)

**Sous-projet canon** :
- `pass2-extraction-spec.md` — consigne d'extraction stricte
- `scraper-corruption-report.md` — corruptions identifiées
- `canon-completion-report.md` — couverture finale
- `canon-cleanup-handoff.md` — doc passation
- `pass2-batch-postmortem.md` — postmortem du batch test 50

**Diagrammes Mermaid** :
- `diagrams/pipeline-overview.md`
- `diagrams/validator-layers.md`
- `diagrams/canon-completion.md`

**Statut projet** :
- `PROJECT_STATUS.md` — vision + archi + avancement
- `CHANGELOG.md` — releases 0.1 à 0.6

---

## 6. Comment lancer le projet en local

### 6.1 Prérequis

```bash
# Python 3.11+, uv installé
git clone <repo>
cd shinobi-no-sho-main
uv venv
uv pip install -r requirements.txt  # ou uv sync si pyproject

# Modèles à télécharger une fois :
# - Qwen3-4B GGUF (~3 GB) via huggingface_hub
# - BGE-M3 (~2.3 GB) auto-téléchargé au premier run sentence-transformers
# - bge-reranker-v2-m3 (~600 MB) idem
```

### 6.2 Variables d'environnement

```bash
# .env à créer (gitignored)
# GROQ_API_KEY=gsk_... (uniquement pour batchs offline, pas en gameplay)
# Pas de clé API requise pour jouer
```

### 6.3 Lancer la démo anti-hallu

```bash
uv run python scripts/demo_anti_hallu.py
```

8 cas adversariaux en ~32 ms, zéro LLM externe. Démontre les piliers à l'œuvre.

### 6.4 Lancer le jeu (boucle complète)

```bash
uv run python -m shinobi.cli.play
```

Boucle de jeu complète. À configurer le path Qwen3-4B GGUF dans `src/shinobi/config.py`.

### 6.5 Lancer les tests

```bash
uv run pytest tests/ -q
```

Doit afficher `359 passed`.

---

## 7. Ce qui marche déjà aujourd'hui

À la fin de cette session, le projet a **un pipeline narratif anti-hallucination complet et opérationnel** :

✅ Le joueur peut taper une action  
✅ Les guards filtrent les inputs hors-univers  
✅ Le state tracker maintient l'état du monde (year, location, vivants/morts)  
✅ La query est enrichie via reference resolver  
✅ Le retrieval hybride ramène les chunks RAG pertinents (filtrés temporellement)  
✅ Le LLM Qwen3-4B local génère une narration  
✅ Le validator catch les incohérences (perso mort, jutsu impossible, âge)  
✅ Si reject, regen avec feedback structuré (max 2 fois)  
✅ Output filter post-génération (anti meta-phrases)  
✅ Le scheduler avance le temps et déclenche les events canon dont les preconditions sont OK  
✅ Les rumeurs propagent l'info avec deformation par radius  
✅ Le worldresolver génère du texte narratif quand un event canon est annulé  

**C'est déjà jouable** sur des scénarios simples. Le prochain niveau (créativité émergente, monde qui invente des événements alternatifs cohérents) est documenté dans le doc 02.

---

## 8. Ce qui ne marche pas encore (transition vers doc 02)

Le système actuel est **réactif** : il filtre, valide, narre, mais il n'**invente pas** spontanément.

3 manques critiques identifiés par l'audit :

1. **Pas de tension detector** — le système ne génère des événements qu'en réaction aux actions joueur ou aux preconditions canon. Aucune émergence spontanée.
2. **Pas de profile vectoriel PNJ + drift** — Sasuke-sans-massacre n'a aucune base structurelle pour devenir un autre Sasuke.
3. **Boucle créative pas fermée** — quand un event canon est annulé, le worldresolver génère du texte mais ne crée pas de TimelineEvent structuré réinjecté dans le scheduler.

Le doc 02 (`02-PROJET-ROADMAP-SUITE.md`) décrit précisément l'architecture pour combler ces manques sans tomber dans le hard-code.

---

## 9. Stack et dépendances clés

### 9.1 Python (`pyproject.toml`)

```toml
dependencies = [
    "pydantic>=2.0",
    "httpx>=0.25",
    "chromadb>=0.4",
    "sentence-transformers>=2.5",
    "bm25s>=0.1",
    "llama-cpp-python>=0.2",  # binding llama.cpp
    "tiktoken>=0.5",
    "structlog>=23",
    # Pas de framework lourd (langchain, llamaindex absents volontairement)
]

dev-dependencies = [
    "pytest>=7",
    "ruff>=0.1",
    "mypy>=1.7",
]
```

### 9.2 Modèles utilisés en runtime (tout local)

| Composant | Modèle | Taille | Latence CPU | Rôle |
|---|---|---|---|---|
| Narration | Qwen3-4B Q4_K_M | ~2.5 GB | ~1-3 sec / 500 tokens output | Narration prose |
| Embeddings | BGE-M3 | ~2.3 GB | ~50ms / chunk | Vecteurs RAG |
| Reranker | bge-reranker-v2-m3 | ~570 MB | ~100ms / 10 paires | Tri final retrieval |

### 9.3 Modèles utilisés offline (jamais en runtime)

| Composant | Modèle | Coût total | Quand |
|---|---|---|---|
| Pass 2 canon | Llama 3.3 70B Groq | $2.30 | Une fois, déjà fait |
| Pass 5 tagging | Llama 3.3 70B Groq | $8.00 | Une fois, déjà fait |

---

## 10. Limites connues à transmettre

- Behavior profiles per-perso : minimaliste (`voice_profiles.json` avec sample_lines/registre/tics, pas de modèle psycho profond)
- Couches D (NLI) et E (LLM judge) du validator : non livrées, prévues optionnelles
- KG dual canon/world_state : documenté mais non implémenté (cf doc 02)
- Tension detector : absent (cf doc 02)
- Multi-agent simulation : absente (cf doc 02)
- Couverture canon : 1359/1360 persos (1 manquant non identifié, sans impact)
- Top-50 sous-extraction modérée par Llama 3.3 70B (vs Claude qui faisait mieux mais ne pouvait pas être utilisé en batch via Groq)
- 4 grands clans avec sous-attestation : senju+mokuton et kaguya+shikotsumyaku correctement marqués `individual_mutation`. uzumaki+fuinjutsu et otsutsuki+byakugan restent sous-attestés (re-run ciblé top-100 ~$0.50 si nécessaire)

---

## 11. Pour démarrer demain matin

1. Ouvre `research/PROJECT_STATUS.md` pour la vision projet
2. Lance `uv run python scripts/demo_anti_hallu.py` pour voir les piliers en action
3. Lance `uv run pytest tests/ -q` pour vérifier que tout est vert chez toi
4. Lance `uv run python -m shinobi.cli.play` pour expérimenter la boucle de jeu
5. Lis le doc 02 pour la roadmap de la suite

Bon courage. Le plus dur est derrière nous.
