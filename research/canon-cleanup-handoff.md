# Sous-projet canon completion — handoff

Document de passation du sous-projet « canon completion ». Sous-projet
clos, base canon utilisable pour la suite des piliers anti-hallucination
(§5 et au-delà).

## Ce qui a été fait

**Pass 2 — extraction LLM** (`scripts/pass2_extract_canon.py`,
`scripts/pass2_batch.py`)
Extraction structurée de 1359 personnages via Groq Batch API
(Llama-3.3-70b-versatile, mode JSON schema). 11 champs ciblés :
`birth_year`, `death_year`, `clan`, `village`, `rank`, `kekkei_genkai_possessed`,
`natures_possessed`, `key_techniques`, `relationships`, `age_at_event`,
`relative_age_to`. Chaque valeur est accompagnée d'une `source_quote`
verbatim citée du wiki source pour validation a posteriori.

**Pass 2.5 — dérivation déterministe** (`scripts/pass2_5_derive.py`)
Comble les `birth_year=null` quand on dispose d'un `age_at_event` ancré
sur un arc daté (chunin_exam = an 12, fourth_shinobi_war = an 16, etc.)
ou d'une chaîne `relative_age_to` transitive. Pure logique Python, zéro
appel LLM, idempotent.

**Pass 3 — agrégation 3-tier** (`scripts/pass2_aggregate.py`)
Pour chaque clan, comptage des KG/natures/techniques attestées par ses
membres. Trois seuils :

- **KEY** : ≥ 50% des membres + ≥ 3 membres → signature obligatoire du
  clan (Byakugan-Hyuga, Sharingan-Uchiha)
- **AVAILABLE** : ≥ 30% + ≥ 3 membres (hors KEY) → éligibilité (un Hyuga
  peut développer x, un Uchiha peut éveiller Mangekyo)
- **INDIVIDUAL_MUTATION** : 1-2 membres seulement → mutation isolée
  taggée par-personnage, pas clan-wide (Mokuton-Hashirama,
  Shikotsumyaku-Kimimaro)

Génère `clans.json`, `kekkei_genkai.json`, `scraper-corruption-report.md`,
`canon-completion-report.md`.

## Couverture finale chiffrée

Source : `data/canonical/clans.json`, `_pass2_output/*.json` (1359 fichiers).

**Personnages** : 1359 / 1359 extractions OK (100%).

**Birth year** :
- 14 / 1359 (1.0%) avec source explicite (canon_hard 3, derived 3,
  llm_extracted 8)
- 1345 / 1359 (99.0%) `unknown` — Llama-3.3-70b est très conservateur sur
  la règle NEVER GUESS, et le canon Naruto donne rarement des dates
  absolues. Le moteur de jeu fera de l'estimation à la volée si besoin.

**Clans (52 au total)** :
- 14 / 52 clans (27%) avec au moins un attribut canon attesté
- 4 / 52 (8%) avec un `key_*` (Uchiha+sharingan, Hyuga+byakugan,
  Sarutobi+fuuton/katon, Hozuki+suiton)
- 12 / 52 (23%) avec un `available_*` (mangekyo, ice_release, hidens
  Nara/Akimichi/Yamanaka/Aburame/Inuzuka, etc.)
- **232 mutations individuelles** taggées sur des personnages spécifiques
  (Hashirama-mokuton, Kimimaro-shikotsumyaku, Naruto-kurama, etc.)

**Corruptions du scraper détectées** : 13 attributions clan→attribut
présentes dans l'ancien `clans.json` mais non attestées par Pass 2
(ex. `akimichi+doton`, `kazekage+jiton` — kazekage n'est pas un clan,
c'est un titre). Voir `research/scraper-corruption-report.md`.

## Limitations connues

**Sous-extraction modérée par Llama-3.3-70b sur le top-50.**
Comparaison 50 personnages CC (Claude-extracted) vs Llama : delta moyen
de **−5.6 fields/perso** côté Llama. Llama est conservateur sur la règle
verbatim quote (rejette quand le quote est paraphrasé) et sur NEVER
GUESS. Détail dans `research/pass2-batch-postmortem.md`.

**Wikis pauvres pour PNJ secondaires.**
35% des wikis de personnages secondaires font moins de 1500 caractères.
Sur ces sources, l'extraction LLM est mécaniquement limitée à 1-3
fields/perso maximum.

**4 grands clans sous-attestés**, par ordre de gravité :
- *senju+mokuton* — correctement classé `individual_mutation` car
  Hashirama est canoniquement le seul Senju à le maîtriser (Tsunade ne
  l'utilise pas en combat). OK.
- *kaguya+shikotsumyaku* — idem, Kimimaro est canoniquement la seule
  occurrence documentée. OK.
- *uzumaki+fuinjutsu* — sous-attesté (2/20 = 10%). Le canon associe les
  Uzumaki au sealing mais la majorité des fiches Uzumaki secondaires ne
  le mentionnent pas explicitement. À compléter à la main si besoin
  gameplay.
- *otsutsuki+byakugan/karma* — dispersion attendue, Otsutsuki est une
  méta-clan dont les branches ont essaimé. À tagger via la table de
  lignages plutôt que par attribut clan-wide.

## Coût total brûlé

| Étape | Coût |
|---|---:|
| Tests CC dryrun (50 persos, Claude API) | $0.15 |
| Pass 2 batch principal (1359 persos, Groq Llama-3.3-70b) | $2.15 |
| **Total** | **$2.30** |

Budget initial $5-10 prévu, sous-utilisé.

## Pistes futures (sans urgence)

1. **Option B : re-run ciblé top-100** wikis riches avec prompt agressif
   (Llama ou Sonnet 4.6). Estimation $0.50 (Groq) ou $3-5 (Sonnet). Gain
   attendu : récupérer uzumaki+fuinjutsu et 1-2 autres cas limites.
2. **Bascule sur Claude Sonnet 4.6** pour l'extraction qualité max sur le
   top-50 « légendaires » (Hokages, Sannin, Akatsuki, jinchūriki).
   Estimation $5-8 pour 50 persos. Gain : couverture canon ~ 95%.
3. **Complétion manuelle des grands clans en gameplay réel.** Le moteur
   peut détecter quand le joueur appartient à Uzumaki et propose
   d'apprendre le fuinjutsu via une quête in-character — l'attribut est
   alors injecté dans le `runtime_state` même s'il n'est pas dans
   `clans.json`. Approche pragmatique, zéro coût LLM, résout le problème
   au moment où il devient pertinent.

## Comment réutiliser les scripts

Les trois scripts sont idempotents et écrivent dans
`data/canonical/_pass2_output/`. Ordre d'exécution :

```bash
# 1. Extraire (long, batch ~6h, $2-3)
python scripts/pass2_extract_canon.py        # build batch JSONL
python scripts/pass2_batch.py submit         # upload Groq + create batch
python scripts/pass2_batch.py poll           # block jusqu'à completion
python scripts/pass2_batch.py parse          # download + dispatch JSON

# 2. Dériver (rapide, pure Python)
python scripts/pass2_5_derive.py             # ajoute birth_year déduits

# 3. Agréger (rapide, pure Python)
python scripts/pass2_aggregate.py            # dry-run preview
python scripts/pass2_aggregate.py --apply    # écrit clans.json + reports
```

Le script `pass2_batch.py parse` est utile pour reprendre une exécution
batch : il re-parse le JSONL local sans appel API si les fichiers ont
été corrompus ou supprimés par accident.

## Fichiers de référence

- `data/canonical/clans.json` — 52 clans avec `key_*` / `available_*`
- `data/canonical/kekkei_genkai.json` — KGs avec `carrier_clans` calculé
- `data/canonical/_pass2_output/*.json` — 1359 extractions brutes
- `data/canonical/_pass2_output/*.flags.json` — individual_mutation tags
- `research/canon-completion-report.md` — couverture par source/confidence
- `research/scraper-corruption-report.md` — 13 corruptions détectées
- `research/pass3-comparative-report.md` — comparaison avant/après seuils
- `research/pass2-batch-postmortem.md` — diagnostic delta CC vs Llama
