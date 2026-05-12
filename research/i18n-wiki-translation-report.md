# Phase i18n.6.A — Rapport de traduction wiki sections (top-100 × 7 langs)

Date : 2026-05-09
Modeles utilises : `claude-sonnet-4-6` (initial + zh repair) + `claude-haiku-4-5` (bulk)
Selection : `data/i18n/wiki/_top100.json` (100 chars top via `i18n_select_top100.py`)

## Resume global

| Metrique | Valeur |
|----------|-------:|
| Top-100 chars selectionnes | 100 |
| Langues cibles (incl EN source) | 8 |
| Fichiers produits | **800 / 800 (100%)** |
| Sections par fichier | 3 (Background, Personality, Abilities) |
| Total sections traduites | 2 100 (100 chars × 3 sections × 7 langs cibles) |
| Cap chars/section (post-Haiku switch) | 1 500 chars |
| Cout cumule estime | ~$15 |
| Budget Phase 6 (spec) | $13 |
| Budget Phase 5+6 cumule | ~$18 / $25 (72%) |

## Selection top-100 chars

Algorithme `scripts/i18n_select_top100.py` selon spec L452-457 :

| Source | Chars contributes |
|--------|------------------:|
| `deep_motivations.json` (50 entries) | 50 |
| `political_forces.factions[].leader_id` | 32 (chevauchement) |
| `divergence_points.involved_canon_ids` | 0 (chevauchement total) |
| Notoriete (`kekkei_genkai` non-vide ou `tailed_beast`) | 35 (padding jusqu'a 100) |

Filtre : seuls les chars avec au moins une section wiki > 50 chars sont retenus. Tous les 100 selectionnes ont du contenu utile.

## Detail par langue

| Lang | Native | Files | Status | Modele utilise |
|------|--------|------:|--------|----------------|
| en | English | 100 | OK (source EN copiee depuis `canonical/characters.json`) | — |
| fr | Français | 100 | OK | Sonnet 4.6 + Haiku 4.5 |
| es | Español | 100 | OK | Sonnet 4.6 + Haiku 4.5 |
| ja | 日本語 | 100 | OK | Sonnet 4.6 + Haiku 4.5 |
| zh | 中文 | 100 | OK | Haiku 4.5 + Sonnet 4.6 (10 retries via JSON repair) |
| ko | 한국어 | 100 | OK | Sonnet 4.6 + Haiku 4.5 |
| pt-BR | Português (BR) | 100 | OK | Sonnet 4.6 + Haiku 4.5 |
| de | Deutsch | 100 | OK | Sonnet 4.6 + Haiku 4.5 |

## Strategie d'execution

### Round 1 — Sonnet 4.6 sync (initial)

- Cap : 2500 chars/section
- Workers : 4
- Resultat : ~340/700 tasks completees avant interruption budget
- Cout : ~$11.50
- Probleme : rate-limits frequents, ETA estime 112 min restant

### Round 2 — Switch Haiku 4.5 + cap reduit (user request : 6× faster, 10× cheaper)

- Modele : `claude-haiku-4-5-20251001` (3× moins cher que Sonnet)
- Cap : 1500 chars/section (au lieu de 2500, perte tolerable pour le wiki)
- Workers : 32 → 16 → 8 (descente progressive face aux 429)
- Resultat : 688/700 tasks (98%)
- Cout : ~$3

### Round 3 — Sonnet 4.6 + JSON repair pour 10 zh restants

- Modele : `claude-sonnet-4-6` (meilleur respect JSON sur CJK)
- Workers : 2
- Cle : **JSON repair via re-prompt** (`call_with_repair`) — sur parse fail,
  re-call avec `[BROKEN OUTPUT]` + instruction "PREFER 「 」 instead of \"".
- Resultat : 10/10 OK (avec 1-2 repair attempts par fichier)
- Cout : ~$0.50

### Total

- **800 fichiers livres**, **0 manquants**
- Cout cumule Phase 6 : ~$15 (Sonnet $12 + Haiku $3)

## Validation

- ✓ Tous les 800 fichiers sont du JSON valide (testes via `json.loads`)
- ✓ Chaque fichier contient les 3 keys `Background`, `Personality`, `Abilities`
- ✓ Schema commun : `_schema=i18n_wiki_v1`, `_language`, `_char_id`, `_translated_at`
- ✓ Glossary preserve sur les sections (validateur lenient case + synonymes)
- ✓ 78 tests pass : 16 `test_wiki_translator.py` + 62 i18n existants

## Phase 6.B — Runtime fallback Qwen

Module : `src/shinobi/i18n/wiki_translator.py`

Strategie a 3 niveaux (cf. docs/14_i18n.md L479-497) :

1. **Cache hit** : lit `data/i18n/wiki/<lang>/<id>.json`. Latence < 50ms.
2. **Cache miss + backend disponible** : delegue a `TranslatorBackend.translate()`
   (Qwen3-4B local typiquement). Cache le resultat.
3. **Backend down/erreur** : retourne source EN avec marqueur
   `_translation_pending: true`. Le jeu reste fonctionnel.

API publique :

```python
from shinobi.i18n.wiki_translator import get_wiki_sections, TranslatorBackend

class QwenBackend(TranslatorBackend):
    def translate(self, sections, lang):
        # ... call llama.cpp HTTP API
        return translated_sections

sections = get_wiki_sections(
    char_id="uchiha_sasuke",
    lang="ja",
    canon_characters=canon.characters,
    backend=QwenBackend(),  # optional
)
# {"Background": "...", "Personality": "...", "Abilities": "..."}
```

## Critere de sortie Phase 6 (docs/14_i18n.md L494-497)

- [x] **Top-100 chars** : `data/i18n/wiki/<lang>/<id>.json` existe pour les 7 langs cibles
  → en + fr + es + ja + zh + ko + pt-BR + de = 8 dossiers à 100/100 = **800/800 fichiers**
- [x] **Fallback Qwen** : `wiki_translator.py` cache + delegate (test_cache_hit_skips_backend, test_cache_miss_calls_backend_then_caches)
- [x] **Hors-ligne** : `_translation_pending: True` marker (test_no_backend_returns_source_with_pending_marker, test_backend_failure_returns_source_with_pending_marker, test_pending_cache_is_retried)

**Phase 6 : 100% complete.**

## Lecons apprises

1. **Sonnet 4.6 sync trop cher pour le bulk de wiki sections** (~$26 estime).
   Switch Haiku 4.5 = 3× moins cher, qualite acceptable pour wiki narratif.
2. **CJK + JSON = piege classique** : le modele genere souvent guillemets ASCII
   non echappes en zh/ja/ko. JSON repair via re-prompt resout 100% des cas.
3. **Rate-limits Anthropic agressifs au-dela de 8 workers concurrents** sur Haiku.
   Compromis optimal observe : 8 workers sustained + retry exponentiel (5/10/20s).
4. **Section cap trade-off** : 2500 chars conserve plus de contenu narratif,
   1500 chars reduit cost ~40%. Pour le wiki (deja capte a 4000 par le scraper),
   1500 reste informatif. Recommandation : 2000 chars si budget le permet.
5. **Job background fragile sur grandes runs** : le job sync original a fini
   silencieux apres ~6h sans avancee. Workers reduits + retry court evitent
   les hangs persistents.
