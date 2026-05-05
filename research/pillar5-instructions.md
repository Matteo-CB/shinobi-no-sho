# Pilier §5 — Re-tagging temporel des chunks RAG : runbook

Document destine a la personne qui prend le relais sur le pilier §5 de
`research/anti-hallucination-rag-narratif-v2.md`.

## Objectif

Tagger chaque chunk du corpus RAG (~17000 chunks) avec :
- `arc` : enum 30+ valeurs (cf. `data/canonical/arc_temporal_anchors.json`)
- `year_min`, `year_max` : bornes annee, year 0 = naissance Naruto
- `tier` : `manga` / `databook` / `anime_canon` / `anime_filler` / `movie` / `boruto` / `fan`
- `entities_mentioned` : ids canoniques des personnages, lieux, jutsus cites

Les tags sont stockes en sidecar JSON dans
`data/canonical/_pass5_output/<chunk_id>.json` et seront ensuite utilises
par le filtre pre-retrieval pour exclure les chunks anachroniques selon
le `narrative_time` courant du joueur (cf. v2.md §5.2).

## Pre-requis

1. **Le corpus de chunks doit exister.** Regarder
   `docs/05_data_pipeline.md` pour le scraping/chunking. Format attendu
   par chunk : un fichier JSON `{"chunk_id", "text", "source_url", "section"}`
   sous `data/rag_chunks/` (ajuster `CHUNKS_DIR` dans
   `scripts/pass5_tag_chunks.py` si chemin different).
2. **Cle Groq.** `export GROQ_API_KEY=gsk_...` (le batch reutilise la
   meme infra que Pass 2, voir `scripts/pass2_batch.py` pour reference).

## Commandes

### 1. Generer la liste des cibles

```bash
python -c "import pathlib; \
  [print(f.stem) for f in sorted(pathlib.Path('data/rag_chunks').glob('*.json'))]" \
  > data/canonical/_pass5_targets.txt

wc -l data/canonical/_pass5_targets.txt  # verifier ~17000
```

### 2. Construire le JSONL de batch

```bash
python scripts/pass5_tag_chunks.py build
```

Sortie : `data/canonical/_pass5_batches/input_<timestamp>.jsonl`.

### 3. Soumettre le batch a Groq

```bash
python scripts/pass5_tag_chunks.py submit
```

Affiche un `batch_id` (forme `batch_01...`). **Le noter**, il est
necessaire pour les etapes suivantes.

### 4. Poll + parse

```bash
python scripts/pass5_tag_chunks.py poll <batch_id>
```

Bloque jusqu'a completion (typiquement 1-2h pour 17k chunks), telecharge
l'output JSONL, dispatch en fichiers individuels sous
`data/canonical/_pass5_output/`.

Si la connexion meurt en cours de poll, on peut relancer la meme
commande : elle re-poll et re-parse (idempotent).

Si l'output JSONL a ete telecharge mais le parse a foire :

```bash
python scripts/pass5_tag_chunks.py parse <batch_id>
```

## Cout et duree attendus

- Volume : ~17000 chunks
- Modele : `llama-3.3-70b-versatile` (Groq, $0.59/1M input, $0.79/1M output)
- Tarif Batch API : 50% off
- Tokens moyen estime : 800 input + 400 output = 1200 / chunk
- Cout total estime : **~$5-10** (worst case $15 si le prompt grossit)
- Duree : **1-2h** en batch processing cote Groq

Le hard limit `HARD_COST_LIMIT_USD = 15.0` dans le script bloque a $15.

## Ce qui peut foirer

1. **Llama-3.3-70b sur-conservateur.** Comme observe sur Pass 2, Llama
   peut renvoyer beaucoup de `arc=unknown` et `year_min=null` quand il
   doute. Si la couverture finale est < 70% en `arc!=unknown`, prevoir
   un re-run cible sur les chunks `unknown` avec un prompt agressif.
2. **JSON invalide.** Llama-3.3-70b respecte mieux `response_format=json_object`
   que gpt-oss-120b (voir post-mortem Pass 2). Si > 5% d'erreurs JSON,
   relancer juste les `custom_id` qui ont foire (parser le JSONL output
   et filtrer).
3. **Entites_mentioned hallucinees.** Le LLM pourrait inventer des
   `character_id`. Validation post-batch recommandee : cross-check chaque
   id contre `data/canonical/characters.json`, `locations.json`,
   `techniques.json`. Garder uniquement les ids existants.

## Apres le batch : filtrage pre-retrieval

Une fois `_pass5_output/` rempli, les tags sont prets a etre injectes
dans le metadata du vector store (cf. v2.md §5.2). Implementation a
prevoir dans `src/retrieval/temporal_filter.py` (a ecrire). Pas inclus
dans ce runbook, c'est l'etape suivante.

## Reference rapide

- Script : `scripts/pass5_tag_chunks.py`
- Cibles : `data/canonical/_pass5_targets.txt`
- Output : `data/canonical/_pass5_output/<chunk_id>.json`
- Batch artifacts : `data/canonical/_pass5_batches/`
- System prompt : edite directement dans `pass5_tag_chunks.py` (constante
  `SYSTEM_PROMPT`). Ajuster apres premier dry-run de 50-100 chunks pour
  calibrer.
