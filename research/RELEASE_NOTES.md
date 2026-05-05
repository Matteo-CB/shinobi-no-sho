# Shinobi no Sho — Release notes (anti-hallucination)

Date : 2026-05-04
Releases : 0.1 -> 0.5

Ce document est destine a la personne qui prend le relais sur le projet.
Il resume ce qui marche, ce qui reste a faire, et comment lancer les
demos / tests / outils.

## TL;DR

7 des 8 piliers du plan anti-hallucination de
`research/anti-hallucination-rag-narratif-v2.md` sont livres et testes.

| Pilier | Statut |
|---|---|
| §2 Garde-fous I/O + persona | OK |
| §3 Validator couches A + B + C | OK |
| §4 State tracker + age calculator | OK |
| §5 Re-tagging temporel des chunks RAG | DIFFERE - bloque par scraping corpus |
| §6 phase A : enums canon | OK |
| §6 phase B : structured output + triplet check + branchement Narrator | OK |
| §7 Risk-tagger | OK |
| §8 Hybrid retrieval (RRF + bge-reranker) | OK algorithmique - vector store en attente §5 |
| §9 KG canon | OPTIONNEL - non commence |

305 / 305 tests verts. Cout API total brule sur tout le sous-projet :
$2.30 (Groq Llama-3.3-70b Batch API).

## Comment verifier que tout marche

```bash
# 1. Installer les deps
uv sync
uv pip install pytest

# 2. Lancer la suite anti-hallu (182 tests)
uv run pytest tests/anti_hallu/ -q

# 3. Lancer la demo runnable (8 cas adversariaux, sans LLM)
uv run python scripts/demo_anti_hallu.py

# 4. Inspecter les enums canon extraits
uv run python -c "
import json, pathlib
for f in pathlib.Path('data/canon').glob('*.json'):
    data = json.load(f.open(encoding='utf-8'))
    n = len(data) if isinstance(data, list) else len(data.get('counts', {}))
    print(f'{f.name}: {n} entrees')
"
```

Resultat attendu :
- Suite anti-hallu : 182 passed
- Demo : 8/8 cas correctement geres en moins de 50 ms
- Enums : 1360 characters, 3025 jutsus, 154 locations, 40 villages,
  52 clans, 32 kekkei_genkai, 18 natures

## Comment debrancher le validator (debug / mesure de latence)

Le validator anti-hallu A+B+C tourne par defaut dans le Narrator.
Pour le couper :

Option 1 : variable d'environnement / `.env`
```bash
ENABLE_ANTI_HALLU_VALIDATION=false uv run python -m shinobi
```

Option 2 : per-instance Narrator
```python
from shinobi.llm.narration import Narrator
n = Narrator(client, canon, retriever, enable_anti_hallu_validation=False)
```

Option 3 : modifier `src/shinobi/config.py` (default field).

Le validator existant (claim_validator + judge legacy) reste actif
independamment, c'est uniquement la couche A+B+C de v2 qui est togglee.

## Ce qui reste a faire pour terminer le projet

### Critique : debloquer le pilier 5

Le re-tagging temporel des chunks RAG est le seul gros morceau qui
manque. Il est differe parce que le **corpus de chunks RAG n'a pas ete
scrape**. Une fois le scraping fait :

1. Lire `research/pillar5-instructions.md` pour le runbook complet
2. Generer la liste des chunk_ids :
   ```bash
   python -c "import pathlib; \
     [print(f.stem) for f in sorted(pathlib.Path('data/rag_chunks').glob('*.json'))]" \
     > data/canonical/_pass5_targets.txt
   ```
3. Lancer le pipeline :
   ```bash
   export GROQ_API_KEY=gsk_...
   uv run python scripts/pass5_tag_chunks.py build
   uv run python scripts/pass5_tag_chunks.py submit
   uv run python scripts/pass5_tag_chunks.py poll <batch_id>
   ```
   Cout estime : $5-10 sur Groq Batch API. Wall time : 1-2h.
4. Apres le batch, brancher les tags au filtre pre-retrieval (cf.
   v2.md §5.2, fichier `src/shinobi/retrieval/temporal_filter.py` a
   creer).

Le scraping lui-meme est decrit dans `docs/05_data_pipeline.md`. Volume
attendu : ~17000 chunks. Wall time : 4-12h selon les rate limits Fandom.

### Branchement vector store reel pour §8

`src/shinobi/retrieval/` est code avec des Protocols. Une fois le
corpus chunks scrape, il faut :

1. Indexer le corpus en BM25 :
   ```bash
   uv pip install bm25s  # absent du venv actuel
   # script a ecrire : scripts/build_bm25_index.py
   ```
2. Indexer le corpus en dense :
   - ChromaDB est deja installe et configure dans `Settings.chroma_persist_dir`
   - sentence-transformers est deja installe (modele `BAAI/bge-m3`)
   - Script a ecrire : `scripts/build_dense_index.py`
3. Wrapper en `BM25Index` et `DenseIndex` adapters :
   - `src/shinobi/retrieval/bm25_adapter.py` (TODO)
   - `src/shinobi/retrieval/chroma_adapter.py` (TODO)
4. Instancier `HybridSearcher` dans le pipeline narrateur

Cout : $0 (tout local). Wall time : ~1h.

### Couches D et E (NLI + LLM judge) du Validator

Les couches A + B + C du Validator sont livrees. Le pilier 7.3 du v2.md
prevoit deux couches supplementaires :

- Couche D : NLI domain-specific fine-tune sur DeBERTa-v3-base avec
  paires auto-generees par perturbation du KG canon.
- Couche E : LLM-as-judge (cf. `src/shinobi/llm/judge.py` legacy qui
  pourrait etre adapte).

A activer seulement si on observe en jeu reel des hallucinations
residuelles que A+B+C ne capturent pas.

## Inventaire des fichiers livres cette release

### Code source

```
src/shinobi/guards/                        (livre release 0.1)
src/shinobi/state/                         (livre release 0.2)
src/shinobi/preprocessing/                 (livre release 0.2)
src/shinobi/prompts/                       (livre release 0.1)
src/shinobi/validation/
  validator.py                             (release 0.3)
  sherlock_rules.py                        (release 0.3)
  age_coherence.py                         (release 0.3)
  regen_loop.py                            (release 0.3)
  risk_tagger.py                           (release 0.5, pilier 7)
  triplet_check.py                         (release 0.5, pilier 6B)
src/shinobi/generation/                    (release 0.5, pilier 6B)
src/shinobi/retrieval/                     (release 0.5, pilier 8)
src/shinobi/llm/narration.py               (modifie release 0.5 : hook)
src/shinobi/config.py                      (modifie release 0.5 : flag)
```

### Scripts

```
scripts/pass2_extract_canon.py             (canon : extraction Groq)
scripts/pass2_normalize.py                 (canon : normalize ids)
scripts/pass2_batch.py                     (canon : Groq Batch API)
scripts/pass2_5_derive.py                  (canon : birth_year derivation)
scripts/pass2_aggregate.py                 (canon : 3-tier classification)
scripts/pass2_postmortem.py                (canon : diagnostic delta)
scripts/pass5_tag_chunks.py                (pilier 5 : squelette)
scripts/pass6_extract_enums.py             (pilier 6A : enums)
scripts/demo_anti_hallu.py                 (demo runnable 8 cas)
```

### Donnees

```
data/canonical/clans.json                  (regenerated 3-tier, $2.30)
data/canonical/kekkei_genkai.json          (regenerated)
data/canonical/_pass2_output/*.json        (1359 extractions Llama)
data/canonical/arc_temporal_anchors.json   (ancres canon par arc)
data/canon/character_list.json             (1360 ids)
data/canon/jutsu_list.json                 (3025 ids + canonical_users)
data/canon/location_list.json              (154 ids)
data/canon/village_list.json               (40 ids)
data/canon/clan_list.json                  (52 ids + key/available)
data/canon/kekkei_genkai_list.json         (32 ids + eligible_clans)
data/canon/nature_list.json                (18 ids)
data/canon/enums_summary.json              (counts + integrity flags)
```

### Documentation

```
research/PROJECT_STATUS.md                 (vue d'ensemble)
research/CHANGELOG.md                      (releases 0.1 -> 0.5)
research/RELEASE_NOTES.md                  (ce fichier)
research/anti-hallucination-rag-narratif-v2.md  (spec source)
research/canon-cleanup-handoff.md          (passation sous-projet canon)
research/pass3-comparative-report.md       (validation seuils 3-tier)
research/canon-completion-report.md        (couverture extraction)
research/scraper-corruption-report.md      (13 corruptions detectees)
research/pass2-batch-postmortem.md         (delta CC vs Llama)
research/pillar5-instructions.md           (runbook §5 quand corpus dispo)
research/diagrams/pipeline-overview.md     (Mermaid end-to-end)
research/diagrams/validator-layers.md      (Mermaid 5 couches)
research/diagrams/canon-completion.md      (Mermaid Pass 2/2.5/3)
```

### Tests

```
tests/anti_hallu/test_persona.py           (release 0.1)
tests/anti_hallu/test_prompt_migration.py  (release 0.1)
tests/anti_hallu/test_understanding.py     (release 0.2)
tests/anti_hallu/test_state.py             (release 0.2)
tests/anti_hallu/test_validator.py         (release 0.3)
tests/anti_hallu/test_canon_enums.py       (release 0.5)
tests/anti_hallu/test_risk_tagger.py       (release 0.5)
tests/anti_hallu/test_hybrid_retrieval.py  (release 0.5)
tests/anti_hallu/test_triplet_check.py     (release 0.5)
```

## Conventions a respecter pour la suite

- **Pas de tirets cadratins, pas d'emoji, pas d'argot otaku** dans les
  strings affichees au joueur.
- **Code identifiers** en francais sans accents.
- **Strings visibles** en francais avec accents.
- **Pydantic v2 partout**, `extra='forbid'` sur les schemas critiques.
- **Tests adversariaux** pour chaque nouveau pilier, dans
  `tests/anti_hallu/`.
- **Pas de commit, pas de push** par les agents Claude. C'est l'humain
  qui gere git.
- **Pas de "Generated with Claude Code"** dans les commit messages.

## Questions ouvertes a debriefer avec Matteo

1. Quand scraper le corpus chunks RAG ? C'est le bloqueur principal
   pour finir §5 et §8 reel.
2. Faut-il un budget API supplementaire pour §5 ($5-10 estime) ?
3. Les 4 grands clans sous-attestes (uzumaki+fuinjutsu, otsutsuki+
   byakugan) doivent-ils etre completes manuellement, ou on accepte
   le statu quo ?
4. Le flag `enable_anti_hallu_validation` doit-il rester True par
   defaut en prod, ou bascule a False en attendant plus de tests reels ?
