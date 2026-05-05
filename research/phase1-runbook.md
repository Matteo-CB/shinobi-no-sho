# Phase 1 — runbook reprise

Document de reference pour reprendre Phase 1 si la session est
interrompue. A jour au 2026-05-05 ~08:30 local.

## Etat actuel

- Phase 4 (embedding 15939 chunks BGE-M3 sur CPU) : **TERMINEE**
  (15680 lors du run principal + 259 via resume apres dedup fix)
- Phase 4bis (BM25 sparse) : **TERMINEE** (15940 chunks indexes)
- Pipeline finalize : `data/.pipeline_ready` cree
- Tests anti-hallu : **224/224 verts** (1 skipped : filtrage temporel
  pending Phase 5)
- Test E2E retrieval : **hybrid 10/10 (100%)**, BM25 8/10, Dense 8/10
- Phase 5 (tagging temporel Groq) : **bloquee** — manque GROQ_API_KEY
  dans .env
- Phase 6 (adapters bm25 + chroma) : **TERMINEE**
- Phase 7 (scenarios E2E) : **TERMINEE**

## Etapes restantes (ordre)

### 1. Attendre la fin de Phase 4

Le process Python tourne en background (task `bv83hixfd`). Il ecrit
les batches dans Chroma et logue `rebuild_batch done=N total=15940`.
A la fin, log `rebuild_complete total=15940` et `Indexation terminee`.

Si plante en cours : relancer avec
```
uv run python scripts/rebuild_embeddings.py --batch-size 32
```
(SANS `--reset` pour ne pas perdre les chunks deja indexes — chunk_id
deterministes, upsert idempotent).

### 2. Verifier integrite + creer flag pipeline_ready

```
uv run python scripts/finalize_pipeline.py
```

Verifie :
- BM25 dir existe avec index loadable
- Chroma collection 'crossdomain' >= 95% de 15940 chunks attendus
- Sanity check BM25 search retourne des resultats

Cree `data/.pipeline_ready` qui debloque les 9 tests E2E skipped
dans `tests/anti_hallu/test_end_to_end_scenarios.py`.

### 3. Lancer les tests integration

```
uv run pytest tests/anti_hallu/test_retrieval_adapters.py -v
uv run pytest tests/anti_hallu/test_end_to_end_scenarios.py -v
uv run python scripts/test_e2e_retrieval.py
```

### 4. Phase 5 — tagging temporel Groq

Pre-requis : ajouter dans `.env` (a la racine du projet) :
```
GROQ_API_KEY=gsk_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

Le `.env` est lu automatiquement par `pass5_tag_chunks.py`.

#### Etape 4.1 : Calibration sur 100 chunks

JSONL deja pre-buildee :
`data/canonical/_pass5_batches/input_1777958805_limit100.jsonl`

```
uv run python scripts/pass5_tag_chunks.py submit
# note le batch_id affiche
uv run python scripts/pass5_tag_chunks.py poll <batch_id>
uv run python scripts/pass5_calibration_validate.py
```

Criteres de validation calibration (cf. dernier prompt user) :
- Failures = 0 ou max 5/100
- Structure conformity >= 90%
- Distribution arc/year coherente (pas tout sur "post_war" ou "academy")

Si calibration **OK** : passer en 4.2.
Si calibration **FAILED** : examiner les outputs, ajuster le system
prompt dans `pass5_tag_chunks.py`, relancer.

#### Etape 4.2 : Full batch sur les 15840 chunks restants

```
uv run python scripts/pass5_tag_chunks.py build --offset 100
uv run python scripts/pass5_tag_chunks.py submit
uv run python scripts/pass5_tag_chunks.py poll <batch_id>
```

Hard limit budget Groq : $12 (dont ~$0.05 calibration).

#### Etape 4.3 : Injection des tags dans Chroma metadata

```
uv run python scripts/update_chroma_with_pass5_tags.py
```

Update les metadata Chroma avec arc/year_min/year_max/tier/entities
sans re-embedder.

### 5. Tests end-to-end

```
uv run pytest tests/anti_hallu/test_end_to_end_scenarios.py -v
uv run python scripts/test_e2e_retrieval.py
```

Le scenario `anachronism_pain_in_academy` peut maintenant valider que
le filtrage temporel fonctionne (chunk Pain ne sort PAS en year 5).

### 6. Demo finale

```
uv run python scripts/demo_anti_hallu.py
```

8 cas adversariaux sans LLM externe.

```
# Et eventuellement
uv run python scripts/play_session.py
```

Pour tester dans une session de jeu reelle.

## En cas d'interruption

Tous les artefacts intermediaires sont persistes :

- `data/embeddings/` : ChromaDB (resume au upsert)
- `data/bm25/` : index bm25s (regenerable en 1.7s via build_bm25_index.py)
- `data/canonical/_pass2_output/` : 1359 extractions Pass 2 (deja fini)
- `data/canonical/_pass5_batches/` : JSONL batches Pass 5
- `data/canonical/_pass5_output/` : outputs Pass 5 parses
- `data/canon/` : enums extraits par Pass 6 phase A

Aucun de ces fichiers n'est ecrasable par accident — les scripts sont
idempotents.

## Logs critiques pour diagnostic

- Embedding live : `tail -f` du fichier de sortie du task bg actuel
- Rebuild log final : ecrit en `logs/shinobi.log`
- Tests : `uv run pytest tests/anti_hallu/ -v`

## Numeros importants

- Total tests anti-hallu apres Phase 1 : 207 + 9 (E2E pending) = **216**
- Cout Groq budget restant : **$12**
- Wall time Phase 4 (embedding) : ~5h sur CPU (au final, pas 60 min)
- Coverage retrieval : 15940 chunks BM25 + Chroma dim 1024
