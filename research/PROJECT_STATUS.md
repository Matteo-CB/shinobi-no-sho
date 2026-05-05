# Shinobi no Sho — Project status

État synthétique pour reprise rapide. Date de génération : 2026-05-04.

## Vision

**Simulateur de vie narratif dans l'univers de Naruto, piloté par un LLM
local + RAG, strictement local et open-source.** Le joueur naît à une
année qu'il choisit, vit une existence où aucune action n'est interdite
mais où la cohérence canon filtre les résolutions. Le monde simulé tourne
en autonomie autour de lui selon la timeline canon.

## Architecture (vue ASCII)

```
                  +--------------------------------+
                  |        Joueur (CLI)            |
                  +---------------+----------------+
                                  | input francais
                                  v
+-----------+   +-----------------+----------------+   +--------------+
| State     |-->| Pilier 2 : guards (pre-filter)   |   | Canon data   |
| (runtime) |   | - blacklist termes hors-univers  |   | - 1359 chars |
+-----^-----+   | - intent_classifier (regex)      |   | - 52 clans   |
      |         | - reject in-character si OOU     |   | - jutsus...  |
      |         +-----------------+----------------+   +------+-------+
      |                           |                           |
      |                           v                           |
      |         +-----------------+----------------+          |
      |         | Pilier 4 (preproc) :             |          |
      |         | - resolve_references (pronoms,   |          |
      |         |   ellipses) sur StateView        |          |
      |         | - rewrite_query (HyDE-like)      |          |
      |         +-----------------+----------------+          |
      |                           |                           |
      |                           v                           |
      |         +-----------------+----------------+          |
      |         | Pilier 8 : hybrid retrieval      |          |
      |         | (a venir : BM25 + dense + RRF +  |<---------+ chunks
      |         |  reranker bge-v2-m3)             |   17k    | RAG
      |         +-----------------+----------------+ (a       | (a scrap
      |                           |                  scraper) |  + chunk)
      |                           v                           |
      |         +-----------------+----------------+          |
      |         | Pilier 6 phase B : structured    |          |
      |         | LLM gen (Outlines) sur enums     |<---------+ enums
      |         | canon : NarrativeOutput contraint|          |
      |         +-----------------+----------------+          |
      |                           |                           |
      |                           v                           |
      |         +-----------------+----------------+          |
      |         | Pilier 3 + 6B + 7 : Validator    |          |
      |         | Layer A : sherlock_rules         |<---------+ canon
      |         | Layer B : triplet_check          |          | view
      |         | Layer C : age_coherence          |<---------+
      |         | Layer D/E (NLI, judge) : reporte |
      |         +-----------------+----------------+
      |                           |
      |                           v
      |         +-----------------+----------------+
      |         | Pilier 2 : output_filter         |
      |         | - meta-phrases reject            |
      |         | - log leakage if any             |
      |         +-----------------+----------------+
      |                           |
      +<--- update state          v
                  +---------------+----------------+
                  |        Narration -> joueur     |
                  +--------------------------------+
```

## Avancement par pilier

| Pilier | Statut | Tests | Notes |
|---|---|---|---|
| §2 Garde-fous I/O + persona | ✅ 100% | inclus | blacklist + intent + output_filter livres |
| §4 State tracker + age | ✅ 100% | inclus | RuntimeState + age_calculator + StateView |
| §3 Validator A + C | ✅ 100% | inclus | sherlock_rules, age_coherence, regen_loop |
| Sous-projet canon | ✅ 100% | n/a | 1359 persos extraits, 14/52 clans attestes, $2.30 |
| §6 phase A (enums) | ✅ 100% | 9 tests | 1360 chars + 3025 jutsus + 154 locs + 40 villages + 52 clans + 32 KGs + 18 natures |
| §7 risk-tagger | ✅ 100% | 12 tests | 4 niveaux risk, 4 segment types, lazy-load enums canon |
| §6 phase B (struct gen + triplet) | ✅ 100% | 16 tests | Pydantic structured_output + TripletCheckLayer. Branchement Narrator avec flag |
| §8 hybrid retrieval | ✅ algos + adapters | 24 tests + integration skipped | RRF + HybridSearcher + bge-reranker + bm25_adapter + chroma_adapter. Index BM25 build OK (1.7s). Index dense en cours d'embedding. |
| §5 re-tagging temporel | ⏳ pending | n/a | Pipeline pass5_tag_chunks.py adapte pour lire chunk_all(canon). Calibration 100 chunks pretes. Phase 5 lance apres Phase 4. |
| §9 KG canon | 🔵 optionnel | n/a | a activer si hallucinations residuelles |

**Progression globale : 7 piliers livres + 1 en cours (§8 indexes en build) + §5 ready a lancer = 7.5/8 ~= 94%**.

### Phase 1 (2026-05-05, COMPLETE)

| Phase | Statut | Notes |
|---|---|---|
| Audit pipeline scraping | ✅ | research/scraping-pipeline-audit.md. Decouverte : scraping deja fait via wiki_sections, 7.5M chars de prose dans canon JSON |
| Phase 4 — embedding + Chroma | ✅ | 15939 chunks indexes (15680 main + 259 resume apres dedup fix). HF_TOKEN configure. ~3.6h wall time. |
| Phase 4bis — BM25 index | ✅ | 15940 chunks indexes en 1.7s via bm25s |
| Pipeline finalize | ✅ | `data/.pipeline_ready` cree, integrity check OK |
| Phase 5 — tagging temporel Groq | ✅ | Calibration 100/100 OK. Full batch 15837/15839 OK (0.013% fail). $8 Groq. |
| Pass 5 metadata injection | ✅ | 31882 records updates (multi-collections + crossdomain). Sentinel TEMPORAL_SENTINEL=9999 sur 4 chunks orphelins. |
| Phase 6 — adapters BM25 + Chroma | ✅ | bm25_adapter.py + chroma_adapter.py (avec narrative_year filter). Tests integration verts. |
| Phase 7 — tests E2E scenarios | ✅ | 12 scenarios narratifs + 3 temporal-filter, hybrid retrieval 10/10 (100%) |

**Tests cumulés : 236 / 236 anti-hallu verts** (359 / 359 projet total).

**Bug-fixes Phase 1** :
- `chunker.chunk_all()` : dedup par chunk.id (collision `kekkei:tenseigan` entre kg + mora)
- `pass5_tag_chunks.py:cmd_submit` : retry exponential backoff sur ReadError 10053/10054 (uploads 50+ MB sur ligne instable)

**Cout Groq Phase 1** : ~$8 (sur budget $12)
**Cout Groq cumulé projet** : $2.30 (Pass 2) + $8 (Pass 5) = **~$10.30**

## Ce qui marche, démontrable

- `python scripts/demo_anti_hallu.py` : 8 cas adversariaux (rejet
  programmation, jailbreak, ellipse résolue, dead actor, age
  incoherence, meta-phrase, triplet check Itachi+Chidori, risk-tagger
  very_high) en ~40 ms total, sans appel LLM externe.
- `uv run pytest tests/anti_hallu/` : 182 tests verts couvrant guards,
  state, validator A+B+C, preprocessing, canon enums, risk-tagger,
  hybrid retrieval, structured output + triplet check.
- `python scripts/pass2_aggregate.py` : dry-run de l'agrégation 3-tier
  sur les 1359 persos extraits, sortie en moins d'une seconde.
- `python scripts/pass6_extract_enums.py` : extrait les 7 enums canon
  vers `data/canon/`, integrity check croise (0 jutsu user orphelin).

## Ce qui reste avec temps estimés

| Tache | Statut | Temps estime | Cout LLM |
|---|---|---:|---:|
| Scraping corpus chunks RAG | ✅ deja fait (decouverte audit) | 0 | 0 |
| Embedding BGE-M3 16k chunks Chroma | ⏳ en cours, ~83% | reste 60-90 min | 0 |
| Build BM25 index | ✅ fait (1.7s) | 0 | 0 |
| §5 batch tagging temporel | ⏳ pret a lancer post-Phase 4 | 1-2h batch | $5-10 |
| Update Chroma metadata avec tags §5 | ⏳ post §5 | 5 min | 0 |
| Tests E2E avec pipeline reel | ⏳ post Phase 4 + Phase 5 | 30 min | 0 |
| §3 couches D (NLI) + E (LLM judge) si necessaire | 🔵 optionnel | 4h | $1-2 par run |
| §9 KG canon (optionnel) | 🔵 optionnel | 8h | $5-10 |

## Lancement local en 5 commandes

```bash
# 1. Install deps (uv recommande)
uv sync

# 2. Verifier les tests anti-hallu
uv run pytest tests/anti_hallu/ -q

# 3. Demo runnable des garde-fous + validator (sans LLM)
uv run python scripts/demo_anti_hallu.py

# 4. (Re)agreger le canon depuis Pass 2 outputs
uv run python scripts/pass2_aggregate.py

# 5. Inspecter le canon
uv run python -c "import json; \
  print(len(json.load(open('data/canonical/clans.json'))), 'clans')"
```

## Conventions du projet

- **Code** : Python 3.11+, Pydantic v2 (`extra='forbid'`), ruff-clean,
  identifiants `snake_case` sans accents.
- **Strings visibles joueur** : francais avec accents.
- **Typing** : strict, Protocol-based pour le decouplage (StateView,
  CanonView).
- **Tests** : `tests/anti_hallu/` pour le pipeline anti-hallucination.
- **Pas de tirets cadratins, pas d'emoji, pas d'argot otaku** dans les
  strings affichees au joueur.
- **Commits** : pas de "Generated with Claude Code" markers, pas
  d'auto-commit (le user gere git).
- **Fichiers data** : `data/canonical/*.json` est la verite, regenerable
  via les scripts `pass2_*` et `pass5_*`.

## Documents de reference

- `CLAUDE.md` : invariants projet
- `research/anti-hallucination-rag-narratif-v2.md` : spec piliers 1-9
- `research/canon-cleanup-handoff.md` : passation sous-projet canon
- `research/pass3-comparative-report.md` : seuils 3-tier validation
- `research/pillar5-instructions.md` : runbook §5 quand corpus dispo
- `docs/01_constraints.md` ... `docs/13_roadmap.md` : spec produit
