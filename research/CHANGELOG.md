# Changelog

Toutes les modifications notables du sous-projet anti-hallucination de
Shinobi no Sho. Chaque section liste : ce qui a ete livre, ce qui marche,
ce qui reste open. Format inspire de Keep a Changelog.

## [Unreleased] — Phase 1 (en cours, 2026-05-05 nuit)

### Added (en cours)
- `research/scraping-pipeline-audit.md` : audit decouvrant que le scraping
  est deja fait (wiki_sections dans le canon JSON, 7.5M chars de prose)
- `scripts/build_bm25_index.py` : indexation BM25 sparse via bm25s
  (15940 chunks indexes en 1.7s)
- `src/shinobi/retrieval/bm25_adapter.py` : BM25Adapter wrapping bm25s
  satisfaisant le Protocol BM25Index
- `src/shinobi/retrieval/chroma_adapter.py` : ChromaDenseAdapter wrapping
  ChromaStore + embed_query satisfaisant le Protocol DenseIndex
- `tests/anti_hallu/test_retrieval_adapters_unit.py` : 8 tests unitaires
  fixture-only (pas de dependance index ni de chargement BGE-M3)
- `tests/anti_hallu/test_retrieval_adapters.py` : 8 tests d'integration
  (skipped quand index reels absents)
- `tests/anti_hallu/test_end_to_end_scenarios.py` : 12 scenarios narratifs
  (academy, wave, chunin_exam, sasuke_retrieval, pain_invasion,
  fourth_great_war, boruto_era + 5 edge cases adversariaux). 17 tests
  passent (state setup, OOU/jailbreak intents, coverage), 9 skipped
  pending pipeline_ready flag.
- `scripts/finalize_pipeline.py` : verifie embedding + BM25 OK puis cree
  `data/.pipeline_ready` flag pour debloquer les tests E2E
- `scripts/pass5_calibration_validate.py` : valide le resultat d'une
  calibration Pass 5 (failure rate, distribution arc/year, conformity)
- `scripts/update_chroma_with_pass5_tags.py` : injecte les tags Pass 5
  (arc, year_min, year_max, tier, entities) dans Chroma metadata sans
  re-embedder
- `scripts/test_e2e_retrieval.py` : 10 scenarios end-to-end retrieval
  (BM25 + Chroma + RRF) executable manuel

### Changed (en cours)
- `scripts/pass5_tag_chunks.py` : refactor pour lire les chunks via
  `chunk_all(canon)` au lieu de `data/rag_chunks/*.json` (pas de fichier
  sidecar requis). Ajout flags `--limit N` et `--offset N`. Auto-load
  `.env` pour GROQ_API_KEY.
- `src/shinobi/retrieval/__init__.py` : exporte BM25Adapter,
  ChromaDenseAdapter, build_bm25_index

### Termine
- Phase 4 : embedding 15939 chunks via BGE-M3 sur CPU. Wall time ~3.5h
  (15680 main + 259 resume apres bug-fix dedup). HF_TOKEN configure,
  bypass rate limit OK, download 2.27 GB en ~14 min.
- Phase 4bis : index BM25 sparse 15940 chunks en 1.7s
- Phase 5 : tagging temporel Pass 5 via Groq Llama-3.3-70b Batch API.
  Calibration 100/100 OK ($0.05). Full batch 15837/15839 OK (0.013% fail, ~$8).
  31882 metadata records updates dans Chroma (collections + crossdomain).
  TEMPORAL_SENTINEL=9999 sur les chunks orphelins.
- Phase 6 : adapters bm25 + chroma branches avec filtre narrative_year
  optionnel ($or year_max <= year OR year_max == sentinel). Tests
  integration verts.
- Phase 7 : 15 scenarios E2E (12 narratifs + 3 temporal-filter) + script
  test_e2e_retrieval.py (hybrid 10/10 = 100% sur 10 queries reference,
  vs BM25 80% + Dense 80% isoles).

### Bug-fixes livres
- `src/shinobi/rag/chunker.py:chunk_all()` : dedup par chunk.id pour
  eviter le DuplicateIDError ChromaDB (cas `kekkei:tenseigan` present
  dans kekkei_genkai.json ET kekkei_mora.json)
- `scripts/pass5_tag_chunks.py:cmd_submit` : retry exponential backoff
  sur ReadError 10053/10054 (upload 52 MB sur ligne instable Windows)

### Stats finales Phase 1 (2026-05-05 fin matinee)
| Metrique | Valeur |
|---|---:|
| Tests anti-hallu | **236 / 236 verts** |
| Tests projet total | **359 / 359 verts** |
| Test e2e retrieval (script) | hybrid 10/10 (100%), BM25 8/10, Dense 8/10 |
| Cout Groq Phase 1 | $8 (calibration $0.05 + full $7.95) |
| Cout Groq cumule projet | **$10.30** ($2.30 Pass 2 + $8 Pass 5) |
| Chunks BM25 indexes | 15940 |
| Chunks Chroma indexes | 15939 (+31882 metadata updates avec tags) |
| Pass 5 outputs parses | 15937 / 15939 (99.99%) |
| Pass 5 failure rate | 0.013% (2 chunks) |
| Wall time Phase 4 | ~3.6h |
| Wall time Phase 5 (calibration + full) | ~30 min |
| Lignes ajoutees src/ | ~400 (adapters + chunker dedup + temporal filter) |
| Lignes ajoutees tests/ | ~1000 (unit + e2e + temporal + scenarios) |
| Lignes ajoutees scripts/ | ~700 (build + finalize + resume + update_chroma) |
| Lignes ajoutees research/ | ~500 (audit + runbook + diagrams + status) |

## [0.5.0] — 2026-05-04 — Piliers §6A + §7 + §8 + §6B livres

### Added
- **Pilier §6 phase A** — `scripts/pass6_extract_enums.py` extrait
  les enums canon depuis `data/canonical/` vers `data/canon/` :
  character_list (1360), jutsu_list (3025 avec canonical_users),
  location_list (154), village_list (40), clan_list (52), 
  kekkei_genkai_list (32), nature_list (18). Integrity check croise
  (0 jutsu user orphelin, 0 KG carrier orphelin).
- **Pilier §7** — `src/shinobi/validation/risk_tagger.py` :
  decoupe NarrativeOutput en segments (prose/dialogue/factual_claim/
  action) et tag chacun avec risk_level (low/medium/high/very_high).
  `required_layers_for_risk()` map vers les couches de validation a
  activer. Detection d'entites canon avec word boundaries
  underscore-aware.
- **Pilier §8** — `src/shinobi/retrieval/` (4 fichiers) :
  - `types.py` : Protocols BM25Index, DenseIndex, Reranker
  - `rrf.py` : Reciprocal Rank Fusion pure (algorithme Cormack 2009)
  - `hybrid_search.py` : HybridSearcher composable BM25 + dense + RRF
  - `reranker.py` : CrossEncoderReranker (bge-reranker-v2-m3 lazy load)
    et FakeReranker pour tests
  Pas branche au vector store reel (corpus chunks RAG pas encore
  scrape, cf. pilier 5 differe).
- **Pilier §6 phase B** — `src/shinobi/generation/structured_output.py`
  (Pydantic-based, pas Outlines) + `src/shinobi/validation/triplet_check.py`
  (couche B). Branchement Narrator avec flag `enable_anti_hallu_validation`
  dans `Settings` (default True).
- 8 cas adversariaux dans `scripts/demo_anti_hallu.py` (auparavant 6),
  ajout : triplet check (Itachi+Chidori) et risk-tagger sur action
  actor+jutsu.
- Tests adversariaux : 9 (canon_enums) + 12 (risk_tagger) + 12
  (hybrid_retrieval) + 16 (triplet_check) = 49 nouveaux tests.

### Changed
- `src/shinobi/llm/narration.py` : `Narrator.__init__` accepte
  `enable_anti_hallu_validation: bool | None = None` (default
  `settings.enable_anti_hallu_validation`). Le validator A+B+C tourne
  dans la boucle de retry et ses violations sont ajoutees au
  retry_correction prompt si reject.
- `src/shinobi/config.py` : ajout `enable_anti_hallu_validation: bool = True`.

### Stats
| Metrique | Valeur |
|---|---:|
| Tests anti-hallu | 182 / 182 verts (auparavant 132) |
| Tests projet total | 305 / 305 verts |
| Cout Groq cette release | $0.00 |
| Wall time release | ~3h |
| Lignes de code ajoutees (src/) | ~700 |
| Lignes de tests ajoutees | ~600 |
| Cas demo fonctionnels | 8 / 8 en 39 ms (zero LLM) |

### Limitations connues
- §8 hybrid retrieval testable seulement sur fakes (corpus chunks RAG
  pas encore scrape). Le vrai branchement attend que le pilier 5 soit
  execute.
- Couche C age_coherence ne fire que pour les personnages avec
  `birth_year` connu (1% du canon). Quand le scraping des chunks RAG
  enrichira la pipeline, on pourra ameliorer la couverture via le
  pilier 5 et le re-tagging.
- Outlines non utilise pour structured generation : Pydantic
  post-validation suffit pour le contrat actuel. Si on veut du token-
  level constrained decoding plus tard, basculer sur XGrammar.

## [0.4.0] — 2026-05-04 — Sous-projet canon completion

### Added
- Pipeline `pass2_extract_canon.py` (Groq Batch API, llama-3.3-70b-versatile)
  pour extraction structuree de 1359 personnages
- Pipeline `pass2_5_derive.py` pour derivation deterministe des
  birth_year via age_at_event + relative_age_to (chainage transitif)
- Pipeline `pass2_aggregate.py` avec classification 3-tier
  (key_* / available_* / individual_mutation)
- Anchors temporels canon `data/canonical/arc_temporal_anchors.json`
- Squelette pipeline §5 `scripts/pass5_tag_chunks.py` (en attente
  corpus chunks RAG)
- Reports : `canon-completion-report.md`, `scraper-corruption-report.md`,
  `pass3-comparative-report.md`, `canon-cleanup-handoff.md`,
  `pillar5-instructions.md`

### Stats
| Metrique | Valeur |
|---|---:|
| Personnages extraits | 1359 / 1359 (100%) |
| Quote validation post-extraction | 94.3% exact match (NFKD + edit_distance <= 5) |
| Clans avec attestation canon | 14 / 52 (27%) |
| Clans avec key_* | 4 (uchiha, hyuga, sarutobi, hozuki) |
| Clans avec available_* | 12 |
| Mutations individuelles taggees | 232 per-character |
| Corruptions scraper detectees | 13 |
| Birth_year explicit | 14 / 1359 (1%) |
| Cout total Groq | $2.30 |

### Limitations connues
- Sous-extraction moderee de Llama-3.3-70b sur le top-50 (-5.6 fields/perso
  vs Claude). Voir `research/pass2-batch-postmortem.md`.
- 35% des wikis < 1500 caracteres limitent l'extraction.
- 4 grands clans sous-attestes : senju+mokuton et kaguya+shikotsumyaku
  correctement classes individual_mutation ; uzumaki+fuinjutsu (10%)
  et otsutsuki+byakugan (14%) restent en-dessous des seuils.

## [0.3.0] — 2026-05-04 — Pilier §3 Validator A + C

### Added
- `src/shinobi/validation/validator.py` : orchestrateur central des
  couches via Protocol `ValidationLayer`. Mode short-circuit ou full-pass.
- `src/shinobi/validation/sherlock_rules.py` : couche A (regles
  deterministes : dead actor, destroyed location, ubiquite).
- `src/shinobi/validation/age_coherence.py` : couche C (signaux d'age
  discordants type "Naruto adolescent" alors qu'il a 5 ans).
- `src/shinobi/validation/regen_loop.py` : feedback structure pour
  re-generation (max 2 tentatives).
- Modeles Pydantic `NarrativeOutput`, `NarrativeDialogue`,
  `NarrativeAction`, `ValidationResult`.

### Stats
- 30+ tests adversariaux dans `tests/anti_hallu/test_validator.py`
- Latence sherlock_rules : < 1 ms / output
- Latence age_coherence : < 5 ms / output (regex sur prose)

## [0.2.0] — 2026-05-04 — Pilier §4 State tracker + age calculator

### Added
- `src/shinobi/state/world_state.py` : `RuntimeState` Pydantic v2 avec
  `narrative_time`, `player_character`, `world_state`, `scene_context`,
  `dialogue_history`. Implemente le Protocol `StateView` par duck typing.
- `src/shinobi/state/age_calculator.py` : `get_age()`, `is_alive()`,
  `get_canon_status()`, decouples du `CanonBundle` reel via Protocol
  `CanonView`.
- `src/shinobi/preprocessing/reference_resolver.py` : resolve_references
  pour pronoms et ellipses sur StateView (pattern PANGeA).
- `src/shinobi/preprocessing/query_rewriter.py` : EnrichedQuery
  consolidant resolution + intent + state snapshot.

### Stats
- ~30 tests dans `tests/anti_hallu/test_state.py` et `test_understanding.py`
- 0 dependances externes pour la logique pure
- Latence calcul age : < 0.1 ms

## [0.1.0] — 2026-05-04 — Pilier §2 Garde-fous I/O + persona

### Added
- `src/shinobi/guards/blacklist.py` : 100+ termes hors-univers (programmation,
  tech moderne, IA, autres oeuvres) en regex compilee.
- `src/shinobi/guards/intent_classifier.py` : classification regex en
  5 intents (in_universe_action, in_universe_question, meta_command,
  out_of_universe, ambiguous).
- `src/shinobi/guards/output_filter.py` : detection meta-phrases
  ("en tant qu'IA", "voici ma reponse", "vous le joueur") +
  `log_leakage_if_any` pour identifier les inputs qui contournent
  le pre-filter.
- `src/shinobi/prompts/system_prompt.txt` + `few_shot_redirections.json` :
  consolidation persona narrateur depuis l'ancien `llm/prompts.py`.
- `src/shinobi/prompts/__init__.py` : `build_system_prompt(PersonaContext)`.

### Stats
- ~40 tests adversariaux dans `tests/anti_hallu/test_persona.py` et
  `test_understanding.py` couvrant jailbreak, blacklist, ellipse
- Latence pre-filter : < 1 ms / query
- 0 appel LLM dans tout ce pilier

## Recap final cumule

Mis a jour a chaque release.

| Metrique | Valeur (releases 0.1 -> 0.5) |
|---|---:|
| Tests anti-hallu | 182 / 182 verts |
| Tests projet total | 305 / 305 verts |
| Modules Python livres | 25 (guards 3, state 2, preprocessing 2, validation 6, prompts 1, retrieval 4, generation 1, scripts pass2-6 7) |
| Lignes de code Python (src/) | ~2200 |
| Lignes de tests | ~1900 |
| Documents de spec / handoff | 12 (research/, dont 3 diagrammes Mermaid) |
| Cout API total | $2.30 (Groq Llama-3.3-70b Batch — pilier 5 differe) |
| Sessions Claude Code utilisees | 1 longue session continue |
| Modele Claude utilise | Opus 4.7 (1M context) |
| Piliers livres | 7 / 8 (§5 differe sur scraping corpus) |
