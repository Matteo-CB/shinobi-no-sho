# Phase i18n.5 — Rapport de traduction batch (final)

Date : 2026-05-09T08:30:00+00:00
Modele : `claude-sonnet-4-6` (sync API, prix plein)
Glossary : 115 termes preserves (chakra, jutsu, kekkei genkai, ranks, bijuu, organisations, villages, kekkei genkai, honorifics)

## Resume global

| Composant | Source | Cibles | Fichiers produits | Cles/Prompts | Cout |
|-----------|--------|--------|-------------------|--------------|------|
| **Catalogs UI/engine/agents** | `en.json` (713 cles) | 6 langues (es, ja, zh, ko, pt-BR, de) | 6 | 713 × 6 = 4 278 | $2.34 |
| **Prompts LLM systeme** | `prompts/fr/*.txt` (6 prompts, 11.5 KB) | 7 langues (en, es, ja, zh, ko, pt-BR, de) | 42 | 6 × 7 = 42 | $0.71 |
| **TOTAL** | | | **48 fichiers** | **4 320 unites** | **$3.05** |

Couverture finale : 100% sur les 6 langues cibles (catalogs) + 100% sur les 7 langues cibles (prompts).
Budget approuve : $25 — utilise : $3.05 (12% du budget).

## Detail catalogs UI/engine/agents

| Lang | Native | Status | Cles | In tok | Out tok | Cost USD | Duration | Issues |
|------|--------|--------|------|--------|---------|----------|----------|--------|
| es | Español | OK | 713/713 | 22,073 | 20,254 | $0.370 | 328.9s | — |
| ja | 日本語 | OK | 713/713 | 22,055 | 22,295 | $0.401 | 353.6s | — |
| zh | 中文 | OK | 713/713 | 22,559 | 21,001 | $0.383 | 328.1s | — |
| ko | 한국어 | OK | 713/713 | 22,055 | 23,793 | $0.423 | 375.9s | — |
| pt-BR | Português (Brasil) | OK | 713/713 | 22,064 | 20,182 | $0.369 | 309.0s | — |
| de | Deutsch | OK | 713/713 | 22,055 | 21,683 | $0.391 | 323.4s | — |

## Detail prompts LLM systeme (6 prompts × 7 langues)

Source : `data/i18n/prompts/fr/*.txt` (6 fichiers, 11 452 chars / ~2 863 tokens)

| Prompt | Source FR | Usage |
|--------|-----------|-------|
| `narrator.txt` | 6 892 chars | system prompt narrateur (`shinobi/prompts/system_prompt.txt`) |
| `goal_pathfinder.txt` | 1 158 chars | strategiste objectifs (`llm/prompts.py`) |
| `character_interpreter.txt` | 827 chars | parsing intentions joueur LLM (`llm/prompts.py`) |
| `world_resolver.txt` | 710 chars | resolveur evenements canon annules (`llm/prompts.py`) |
| `director_compactor.txt` | 326 chars | archiviste narratif (`director/compactor.py`) |
| `tension_analyst.txt` | 1 539 chars | analyste opportunites dramatiques (`tension/llm_analyst.py`) |

| Lang | Status | Prompts | In tok | Out tok | Cost USD | Duration | Issues |
|------|--------|---------|--------|---------|----------|----------|--------|
| en | OK | 6/6 | 5 520 | 3 078 | $0.063 | 68.6s | — |
| es | OK | 6/6 | 5 527 | 3 816 | $0.074 | 80.4s | — |
| ja | OK | 6/6 | 5 520 | 4 782 | $0.088 | 94.0s | — |
| zh | OK | 6/6 | 10 678 | 8 182 | $0.155 | 145.2s | — (1 repair JSON) |
| ko | OK | 6/6 | 5 520 | 5 468 | $0.099 | 90.6s | — |
| pt-BR | OK | 6/6 | 5 523 | 3 909 | $0.075 | 80.1s | — |
| de | OK | 6/6 | 5 520 | 4 636 | $0.086 | 97.3s | — |

## Validation

**Aucune anomalie detectee.** Toutes les langues respectent :
- Couverture des 713 cles a 100%
- Glossary preserve (avec acceptation des synonymes intra-categorie : ex. `nukenin` <-> `missing-nin` dans `ranks`)
- Placeholders `{var_name}` intacts
- Markup `[bold]`, `[dim]`, etc. preserve

## Notes operationnelles

### Mode batch vs sync

Le script utilise l'API sync Sonnet 4.6 par defaut (rapide, ~5 min/lang). Pour reduire 
le cout d'environ 50%, l'API Batch Anthropic est disponible (~24h turnaround). Pour ce projet 
(7 langues x ~30K tokens), le sync mode reste sous le budget de 25$ approuve par l'utilisateur.

### Concurrence

Tests de concurrence :
- 5 workers parallels : timeouts/rate limits sur 5 langues simultanees
- 2 workers parallels : 4/5 langues OK, 1 echec (zh) sur JSON malforme
- 1 worker (zh re-run avec prompt JSON-escape renforce) : OK

Recommandation : `--workers 2` pour les 5 dernieres langues, sequentiel pour zh.

### Pieges JSON

Le modele a tendance a generer des guillemets ASCII non echappes (`"..."`) dans les 
valeurs Mandarin/Japonais/Coreen. Le system prompt v2 ajoute une section `JSON ESCAPING` 
explicite et propose les guillemets natifs (「」, 『』, « ») qui resolvent le probleme.

### Validateur intelligent

Le validateur accepte 3 niveaux de tolerance pour eviter les faux-positifs :

1. **Synonymes intra-categorie** : `nukenin` <-> `missing-nin` (categorie `ranks`) tous deux valides.
2. **Variations de casse pour termes longs** : `chakra` <-> `Chakra` <-> `CHAKRA` (>= 4 chars), tous valides.
3. **Casse stricte pour termes courts** : `Ne` (org) ne match PAS `ne` (negation FR) — evite les faux-positifs sur les particules grammaticales.
4. **Ratio length language-aware** : pour les prompts, CJK (`ja`, `zh`, `ko`) toleres a 0.20-1.5 ratio target/source ; europeennes 0.5-3.0.

### JSON repair via re-prompt

Le modele Sonnet 4.6 produit occasionnellement du JSON invalide pour les langues CJK (guillemets ASCII non echappes). Le script `i18n_translate_prompts.py` integre un loop de repair : sur parse fail, re-prompt avec l'erreur exacte + le texte casse, demande au LLM de corriger uniquement le JSON. 1 attempt suffit pour zh.

## Critere de sortie Phase 5 (docs/14_i18n.md L440-444)

### Catalogs (UI/engine/agents)
- [x] Aucun fichier `<lang>.json` ne manque de cle (713 cles x 6 langues = 100%)
- [x] Glossary preserve dans 100% des entrees
- [x] Cout reel ($2.34) <= budget approuve ($25)
- [x] Tous les fichiers `data/i18n/{es,ja,zh,ko,pt-BR,de}.json` populates et valides

### Prompts LLM systeme
- [x] 6 prompts source extraits a `data/i18n/prompts/fr/*.txt`
- [x] 42 fichiers `data/i18n/prompts/<lang>/<name>.txt` produits (7 langues x 6 prompts)
- [x] Glossary preserve dans 100% des prompts
- [x] Placeholders `{var_name}` intacts dans tous les prompts
- [x] Cout reel ($0.71) <= budget restant

### Total Phase 5

- [x] **48 fichiers** produits (6 catalogs + 42 prompts) couvrant 7 langues
- [x] **$3.05 / $25** budget utilise (12%)
- [x] **0 issues** sur la validation finale (apres refinements validateur)
- [x] Scripts reutilisables pour Phase 6 (wikis) et Phase 7 (Phase H datasets)

**Phase 5 : 100% complete.**
