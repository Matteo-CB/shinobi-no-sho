# Audit pipeline scraping + RAG (Phase 1)

Date : 2026-05-05
Objet : avant de lancer le scraping de 17k pages, verifier ce qui existe
deja dans le repo. Decouvertes majeures changent le plan d'execution.

## TL;DR — le scraping est deja fait

**Le scraping Narutopedia a deja ete execute en amont du projet.** Le
texte wiki est present dans les JSON canoniques sous le champ
`wiki_sections`. Total ~7.5M caracteres de prose deja extraits.

**Le chunker existe et tourne en 0.1 seconde sur les donnees actuelles,
produisant 15940 chunks** — tres proche du "17k" cible.

Les 3 vraies briques manquantes :
1. **Embeddings** (~30-60 min CPU) : code pret, jamais execute
2. **§5 batch tagging temporel** ($5-10 Groq, ~1-2h) : adaptation
   mineure de `scripts/pass5_tag_chunks.py` pour lire depuis chunk_all
3. **§8 branchement reel** (~1h) : adapters BM25Index/DenseIndex au
   ChromaStore + bm25s

**Plan revise : ~3-5h wall time (vs 6-12h initial), pas $0-15 (vs 5-10)
selon arbitrage §5.**

## 1. Etat actuel du pipeline scraping

### 1.1 Scraper documente et code

`scripts/scrape_narutopedia.py` (11 KB) existe et est fonctionnel :

- Backend : MediaWiki API (`https://naruto.fandom.com/api.php`), pas
  scraping HTML brut. Plus propre que `trafilatura`.
- Politesse : `MAX_PARALLEL_REQUESTS = 1`, delay 1.5s entre requetes,
  User-Agent identifie. Respect strict des conventions Fandom.
- Cache : `data/raw/narutopedia/pages/<pageid>_<title>.wikitext` +
  `data/raw/narutopedia/meta/<pageid>.json` + `_trace.jsonl`.
- Strategie : iterate sur `list=allpages` namespace 0, fetch en batch
  de 50 pageids via `prop=revisions|categories`.
- Reprise : skip automatique si cache existe, `--force` pour invalider.

### 1.2 Etat du cache local

```
data/raw/narutopedia       MISS (n'existe pas)
data/raw/_trace.jsonl       MISS
```

Le cache local de scraping n'existe pas. Cependant le contenu est
PRESENT dans les JSON canoniques (cf. 1.4).

### 1.3 Parser

`scripts/parse_narutopedia.py` (4.6 KB) existe : il parse le wikitext
cache vers JSON intermediaire. Combine avec
`scripts/build_canonical_jsons.py` (39 KB, gros pipeline LLM-augmented)
pour produire les datasets canoniques.

Ces scripts ne sont pas a relancer pour notre objectif (RAG operationnel).

### 1.4 Decouverte critique : wiki_sections dans le canon

Les datasets canoniques contiennent deja le texte wiki extrait, structure
par section, dans le champ `wiki_sections: dict[str, str]`.

| Fichier | Entites avec wiki | Caracteres de prose |
|---|---:|---:|
| characters.json | 1359 / 1360 | 6_247_739 |
| techniques.json | 1144 / 3025 | 751_165 |
| clans.json | 49 / 52 | 82_595 |
| villages.json | 29 / 40 | 59_228 |
| locations.json | 74 / 154 | 161_519 |
| kekkei_genkai.json | 22 / 32 | 72_386 |
| tailed_beasts.json | 10 / 10 | 135_042 |
| organizations.json | 9 / 9 | 56_767 |
| timeline_events.json | 0 / 60 | 0 |
| **Total** | **2696** | **~7.57M** |

Sample de Naruto (uzumaki_naruto) : 14 sections (Background,
Personality, Abilities, Part I, Part II, etc.), 4000 chars max chacune,
~50 KB total pour le perso.

Cela confirme que le scraping a ete fait et integre dans le canon. Pas
besoin de relancer.

## 2. Ce qui manque pour atteindre un RAG indexe

### 2.1 Code chunking : DEJA EXISTANT

`src/shinobi/rag/chunker.py` (22 KB) avec `chunk_all(CanonBundle) ->
list[Chunk]` produit en 0.1 seconde **15940 chunks** :

| Type | Nombre |
|---|---:|
| character | 9645 |
| technique | 4543 |
| lore (KG, weapons, hiden) | 1194 |
| dialogue | 190 |
| clan | 184 |
| village | 124 |
| event | 60 |
| **Total** | **15940** |

Caracteristiques :
- Moyenne : 684 chars / chunk
- Total : 10.9M chars de chunk text
- Strategie : 1 chunk header + N chunks section pour chaque entite
- Metadata riche : `character_id`, `village`, `clan`, `canonicity`,
  `alive_until`, `born_year`, `section`
- Identifiants stables : `character:uzumaki_naruto:wiki:abilities`

Le chunker chunke a partir des JSON canoniques (pas du wikitext brut),
ce qui fait que **les 17k chunks pre-existaient en code mais n'etaient
jamais materialise en index**.

### 2.2 Code embedding : EXISTANT, jamais execute en bulk

`src/shinobi/rag/embedder.py` (65 lignes) :
- Modele : `BAAI/bge-m3`, dim 1024, multilingue (FR/EN/JP romaji)
- Backend : sentence-transformers, device CPU (config `Settings.embeddings_device`)
- `embed_texts(texts, batch_size=32)` operationnel
- Lazy load du modele au premier appel

**CUDA non disponible** sur cette machine, donc tout sur CPU.

### 2.3 Code vector store : EXISTANT

`src/shinobi/rag/store.py` (144 lignes) :
- `ChromaStore` wrapper persistent local sous `data/embeddings/`
- 8 collections : `character`, `technique`, `clan`, `village`, `event`,
  `lore`, `dialogue`, `crossdomain`
- Distance cosine, metadata pass-through
- `add_chunks()`, `query()`, `count()`, `reset_collection()` exposes
- Utilise `chromadb.PersistentClient`

`scripts/rebuild_embeddings.py` orchestre le tout :
```
python scripts/rebuild_embeddings.py [--reset]
```

`data/embeddings/` n'existe pas → l'index n'a jamais ete construit.

### 2.4 Pipeline d'orchestration : EXISTANT

L'enchainement `load_canon → chunk_all → embed_texts → ChromaStore.add_chunks`
est deja cable dans `rebuild_embeddings.py`. Une commande suffit pour
materialiser les 15940 embeddings.

## 3. Risques techniques

### 3.1 Rate limits Fandom

**Non applicable** : pas de scraping a faire.

### 3.2 Parsing HTML

**Non applicable** : pas de parsing a faire.

### 3.3 Volume disque

| Asset | Estimation |
|---|---:|
| Embeddings raw (16k × 1024 floats × 4B) | ~65 MB |
| ChromaDB persistent (HNSW + metadata) | ~200-400 MB |
| BGE-M3 modele cache (~/.cache/huggingface) | ~2 GB (si pas deja telecharge) |

Total disque a prevoir : ~3 GB max. OK sur tout PC moderne.

### 3.4 Wall time embedding

Estimation conservative pour 15940 chunks sur CPU avec BGE-M3 et
batch_size=32 :

- Optimiste (CPU recent, batch sans I/O bloquant) : ~10-15 chunks/s →
  18-27 min
- Realiste : ~5-10 chunks/s → 27-55 min
- Pessimiste (CPU lent, swap) : ~3 chunks/s → 90 min

Hard limit propose : **90 min** sur cette etape. Au-dela, soit on
diminue batch_size, soit on skip et on construit l'index en arriere-plan.

### 3.5 §5 batch tagging temporel

- Volume : 15940 chunks
- Modele : llama-3.3-70b-versatile (Groq Batch API, 50% off)
- Tokens estimes : ~1200 input + 400 output / chunk = 1600 tokens
- Cout estime : 15940 × 1600 / 1e6 × 0.59 × 0.5 (input) +
  15940 × 400 / 1e6 × 0.79 × 0.5 (output) ≈ **$10**

C'est dans le hard limit $10 fixe par l'utilisateur, mais sans marge.
Risque de depassement si le prompt grossit. **Recommandation : tester
sur 100 chunks d'abord (~$0.05) pour calibrer le cout reel.**

### 3.6 §8 branchement reel

- bm25s n'est pas installe (`pip install bm25s` requis)
- Pas de risque autre que des bugs de wiring (Protocols deja codes)

## 4. Plan d'execution revise

### Phase 2 (scraping) : SUPPRIMEE

Le scraping est deja fait. 0 min, 0 cout.

### Phase 3 (chunking) : DEJA EXISTANT

`chunk_all(canon)` produit 15940 chunks en 0.1s. Pas d'action requise.

### Phase 4 (embedding + ChromaDB indexation) : 30-90 min, $0

Action : run `scripts/rebuild_embeddings.py`.

Checkpoints : ChromaStore upsert par batch de 64. Si crash a mi-chemin,
les chunks deja inseres sont persistes (les ids sont deterministes,
upsert idempotent au re-run).

Hard limit : 90 min. Si depasse, ralentir batch_size et reprendre.

### Phase 5 (§5 batch tagging temporel) : ~1-2h Groq, $5-10

Action :
1. Adapter `scripts/pass5_tag_chunks.py` pour lire depuis `chunk_all(canon)`
   au lieu de `data/rag_chunks/*.json`. Ecrire les targets (15940 chunk_ids)
   dans `_pass5_targets.txt`.
2. Test calibration sur 100 chunks (~$0.05). Verifier la qualite des
   tags retournes par Llama-3.3-70b.
3. Si OK : full run sur 15940 chunks.
4. Parse + valide + persiste les tags en metadata supplementaire.

Hard limit : $10 (budget user). Si calibration projette > $10, baisser
le tokens ou ne tagger qu'un sous-ensemble (techniques + characters
seulement → ~13k chunks).

### Phase 6 (§8 branchement reel) : ~1h, $0

Action :
1. `uv pip install bm25s`
2. Ecrire `src/shinobi/retrieval/bm25_adapter.py` qui wrappe bm25s en
   `BM25Index`
3. Ecrire `src/shinobi/retrieval/chroma_adapter.py` qui wrappe
   `ChromaStore` en `DenseIndex`
4. Brancher dans le narrator si voulu (ou laisser dispo en lib pour
   plus tard)
5. Tests d'integration : 5-10 queries de reference

### Phase 7 (tests end-to-end) : ~30 min, $0

Action : 5-10 scenarios narratifs realistes contre le pipeline complet :
- "Qui sont les utilisateurs canoniques de Chidori ?" → triplet check OK
- "Naruto a 5 ans, peut-il maitriser le Rasenshuriken ?" → reject A+C
- "Le marchand de Konoha vend des shuriken" → pass
- ...

## 5. Decisions a arbitrer

### Decision 1 : §5 batch tagging — vraiment necessaire ?

**Pour** :
- Filtrage retrieval pre-narrative selon le `narrative_time` courant
- Eviter les chunks d'arcs futurs en mode strict
- Gain qualitatif sur les anachronismes

**Contre** :
- Cout : $5-10 (proche du budget total)
- Wall time : 1-2h
- Le metadata existe deja partiellement : `alive_until`, `born_year`
  donnent deja un signal temporel sur les character chunks
- Les techniques et lore n'ont pas de signal temporel sans tagging

**Recommandation** : faire le tagging sur **techniques + character
chunks seulement** (~14k chunks au lieu de 16k), ce qui ramene a $5-7 et
~1.5h. Les village/clan/event/dialogue chunks sont moins critiques pour
les anachronismes.

### Decision 2 : §8 BM25 — vraiment necessaire ?

**Pour** :
- BM25 est tres bon pour les noms propres japonais translitteres
  ("Tsukuyomi", "Mangekyou"), la ou le dense peut deriver vers des
  concepts proches
- Hybrid (BM25 + dense + RRF) est l'etat de l'art en 2025
- Code deja ecrit, juste un adapter a faire

**Contre** :
- Une dependance supplementaire (bm25s)
- Ajoute ~30 min wall time

**Recommandation** : faire les deux adapters (bm25s + Chroma) et le
HybridSearcher. C'est code propre et ca evite d'avoir a refaire les
tests d'integration plus tard.

### Decision 3 : ordre Phase 4 vs Phase 5

**Option A** : Embedding (4) avant tagging (5).
- Avantage : on peut deja tester le retrieval pendant que le batch
  Groq tourne en arriere-plan (1-2h)
- Inconvenient : il faut re-injecter les tags en metadata apres

**Option B** : Tagging (5) avant embedding (4).
- Avantage : un seul ChromaDB upsert avec les tags inclus
- Inconvenient : 1-2h d'attente avant de pouvoir tester quoi que ce soit

**Recommandation** : Option A. On lance l'embedding (30-90 min CPU),
pendant qu'il tourne on prepare la calibration §5 sur 100 chunks. Une
fois le batch §5 fini, on update les metadata Chroma sans re-embedder.

### Decision 4 : Hard limit budget Groq

User a fixe $10 sur les $13 restants. Avec la calibration sur 100
chunks d'abord, on a un proxy fiable. **Recommandation : confirmer le
hard limit a $10 et accepter de tagger seulement N_max chunks tels que
N_max × cost_par_chunk ≤ $10 — meme si N_max < 16k.**

### Decision 5 : Quoi mettre dans le retry / reprise

L'embedding est idempotent grace au upsert. Donc reprise apres crash =
re-run la commande, ChromaDB upsert ignore les chunks deja indexes.

Le batch §5 est idempotent grace au `parse` subcommand qui re-parse le
JSONL local sans rappeler l'API.

**Pas de strategie de checkpoint specifique a coder.**

## 6. Plan recommande final

```
Phase 4   embedding + ChromaDB             45-90 min   $0
Phase 5   §5 batch (calibration + run)     1-2h        $5-10
Phase 6   §8 adapters + tests              60-90 min   $0
Phase 7   tests end-to-end                 30 min      $0
                                                      
TOTAL                                      3-5h        $5-10
```

Phases 4 et 5 peuvent partiellement se chevaucher (embedding pendant
que la calibration §5 tourne).

Hard limits :
- Phase 4 : 90 min wall time
- Phase 5 : $10 cost (sur $10 budget restant)
- Total : si Phase 4 + 5 + 6 cumule > 5h sans compter Phase 7, stop et
  livre l'etat partiel.

## 7. Question pour arbitrage

1. **Decision 1** : tu veux tagger les 16k chunks complets (~$10) ou
   limiter aux 14k characters + techniques (~$5-7) ?
2. **Decision 2** : valide-tu le branchement BM25 en plus du Chroma
   dense pour §8 ?
3. **Ordre Phase 4 vs 5** : tu valides Option A (embedding d'abord,
   tagging ensuite avec metadata update) ?
4. **Calibration §5** : tu veux que je test d'abord sur 100 chunks
   (~$0.05) avant le full run pour controler le cost reel ?

Tu valides le plan revise et je demarre Phase 4.
