# Canon completion plan

> Audit prérequis avant le pilier §5 (re-tagging temporel des 17k chunks).
> Le validator §3 et le state tracker §4 reposent sur des champs canon
> qui sont en grande partie vides. Ce plan décrit comment combler par
> dérivation symbolique puis extraction LLM ciblée, avant de lancer
> quoi que ce soit de coûteux en aval.

---

## TL;DR

**Le canon est plus creux que prévu.** Sur 1360 personnages, seulement 8.2%
ont un `birth_year`, 3.5% un `death_year`, 24% un `clan`, 13.5% un
`kekkei_genkai`. Trois champs sont définis dans le schema mais à 0% :
`rank_progression`, `key_relationships`, `stats_by_era`.

**Les ponts symboliques sont rares.** Seulement 4/52 clans ont leur
`key_kekkei_genkai` renseigné directement dans `clans.json`, et 0/32
KG ont leurs `carrier_clans`. Le mapping clan→KG existe en théorie
(canon dit que Uchiha → Sharingan), mais il n'est pas dans les fields :
il vit dans `wiki_sections` en texte libre. La dérivation pure-règles
ne donnera donc que des **éligibilités** (le clan X a accès à ces KG),
pas de **possessions** (perso Y a le KG Z).

**La grosse richesse est dans `wiki_sections`** (rempli à 99.9%). 776
personnages ont une section Background (médiane 524 chars, p90 2363
chars), 802 ont Part I ou Part II. C'est de là que viendront 90% des
gains de complétion, via extraction LLM ciblée par perso.

**Trois passes proposées** :
1. **Pass 1 (déterministe)** : enrichir 269 persos d'une éligibilité KG
   et 291 d'une éligibilité nature, par observation empirique des
   couples (clan, kekkei_genkai) et (clan, nature) connus. Aucun nouveau
   `birth_year`.
2. **Pass 2 (LLM ciblé)** : 1 appel Haiku 4.5 par perso unknown sur ses
   propres `wiki_sections` (Background + Part I/II/New Era + Abilities).
   Coût estimé ~$6 pour ~1250 persos. Extrait birth_year, death_year,
   team, sensei, parents, rank_progression, KG/natures explicites.
3. **Pass 3 (unknowns résiduels)** : flag explicite `birth_year_known`,
   skip des checks d'âge, fallback éligibilité clan. Estimé 200-400 cas
   après Pass 2.

**Implication critique pour le §5** : sans Pass 2, le tagging temporel
des 17k chunks ne pourra pas correctement attribuer `entities_mentioned`
à des persos canon datés. On taggerait des chunks "année 6" en se basant
sur la mention de Sasuke (birth_year connu = 0) mais on ne pourrait pas
faire pareil pour le 92% restant. Pass 2 doit donc précéder le §5.

---

## 1. Audit chiffré

### 1.1 Volume

- **Total personnages dans `data/canonical/characters.json`** : 1360
- **Patches dans `character_birth_years_patch.json`** : 212 entries dont
  seulement 112 matchent un `id` valide (les 100 autres sont des
  artefacts de noms anciens ou non-mergés, à investiguer ou nettoyer)

### 1.2 Champs renseignés (après application des patches)

| Field | Filled | Missing | % |
|---|---:|---:|---:|
| `village_of_origin` | 1360 | 0 | 100.0% |
| `gender` | 1360 | 0 | 100.0% |
| `personality_fr` | 1360 | 0 | 100.0% |
| `wiki_sections` | 1359 | 1 | 99.9% |
| `techniques_known_by_era` | 839 | 521 | 61.7% |
| `natures` | 341 | 1019 | 25.1% |
| `clan` | 326 | 1034 | 24.0% |
| `kekkei_genkai` | 184 | 1176 | 13.5% |
| **`birth_year` (raw)** | **0** | **1360** | **0.0%** |
| **`birth_year` (post-patch)** | **112** | **1248** | **8.2%** |
| **`death_year` (raw)** | **0** | **1360** | **0.0%** |
| **`death_year` (post-patch)** | **47** | **1313** | **3.5%** |
| `secondary_clan` | 0 | 1360 | 0.0% |
| `kekkei_mora` | 0 | 1360 | 0.0% |
| `tailed_beast` | 0 | 1360 | 0.0% |
| `rank_progression` | 0 | 1360 | 0.0% |
| `stats_by_era` | 0 | 1360 | 0.0% |
| `key_relationships` | 0 | 1360 | 0.0% |
| `voice_profile_id` | 0 | 1360 | 0.0% |
| `speech_patterns` | 0 | 1360 | 0.0% |

> Les trois champs définis dans le schema Pydantic mais vides à 100%
> (`rank_progression`, `stats_by_era`, `key_relationships`) sont
> alarmants : ils devaient porter une part importante de la
> structuration canon. Tout doit donc être extrait par Pass 2.

### 1.3 Distribution des sections wiki disponibles

Les wiki_sections sont la source brute de vérité non extraite. Top
sections par couverture :

| Section | Persos | % |
|---|---:|---:|
| Appearance | 1237 | 91% |
| Personality | 1024 | 75% |
| Trivia | 992 | 73% |
| Abilities | 954 | 70% |
| Background | 776 | 57% |
| Part II | 493 | 36% |
| New Era | 318 | 23% |
| Part I | 309 | 23% |
| Quotes | 188 | 14% |
| Blank Period | 119 | 9% |
| Plot Overview | 101 | 7% |
| Legacy | 85 | 6% |

Sections clés pour notre extraction :
- **Background** (57%) : famille, naissance, sensei, événements précoces
- **Abilities** (70%) : techniques, KG, natures explicites
- **Part I + Part II** (combiné 802 persos uniques) : activité narrative datée
- **Personality** (75%) : voice_profile / speech_patterns

### 1.4 Patches existants : qualité de l'information

Sur 112 entries valides, le patch couvre :
- Les 9 personnages de la Konoha 11 (an 0)
- Les 4 de la team Guy (an -1)
- Konohamaru (an 4)
- Itachi (-7), Shisui (-8), Kakashi (-14)
- Sensei Genin de Part I (Iruka, Kurenai, Asuma, Gai, Kakashi)
- Génération Yondaime (Minato, Kushina, Obito, Rin)
- Quelques `death_year` clés (Itachi an 16, Asuma an 13, Minato an 0)

C'est un bon point de départ "main characters", mais s'arrête là. Aucun
des Sannin, Kage, Akatsuki secondaires, anciens ou Boruto-era n'est
patché. La complétion à 8.2% reflète qu'on a un noyau de ~100 persos
centraux et 1248 persos creux.

### 1.5 Le piège `techniques_known_by_era`

61.7% des persos en ont au moins une entry, ce qui semble une bonne
base. Mais après inspection : **toutes les 839 entries ont `year=18`**
(min=18, max=18). Le champ est donc utilisé comme un snapshot "à la fin
de Boruto" et **ne fournit aucune information temporelle exploitable**
pour le tagging temporel ou pour fenêtrer un birth_year. À retraiter
en Pass 2 (extraire les rangs et techniques par phase narrative).

---

## 2. Ponts de dérivation identifiés

### 2.1 Ponts qui marchent (déterministe pur)

#### Clan → kekkei_genkai (par observation empirique)

`clans.json` a un champ `key_kekkei_genkai` mais seulement 4/52 clans
le remplissent. Stratégie alternative : observer les couples
`(clan, kekkei_genkai)` connus dans `characters.json`, agréger par clan,
et propager comme **éligibilité**.

Distribution des persos par clan (top 10) avec compte des persos qui
ont un KG renseigné :

| Clan | Members | with KG | KG observés |
|---|---:|---:|---|
| uchiha | 46 | 28 | sharingan, mangekyo, izanami, izanagi |
| funato | 26 | 3 | (à confirmer Pass 1) |
| otsutsuki | 21 | 9 | byakugan, jogan, tenseigan, kekkei_mora |
| uzumaki | 20 | 8 | adamantine_sealing, longevity |
| nara | 18 | 3 | shadow techniques (kekkei? plutôt hiden) |
| hyuga | 17 | 15 | byakugan |
| inuzuka | 14 | 2 | beast mimicry (hiden) |

**Potentiel total** : 269 personnages dans 33 clans dont au moins un
membre a un KG → on peut leur attribuer un set d'**éligibilité** KG
(pas une possession, juste "ce clan peut potentiellement développer
ces KG").

#### Clan → natures de chakra (idem)

Pareil pour les natures :
- 38 clans ont au moins un membre avec une nature renseignée
- 291 personnages dans ces clans peuvent hériter de l'éligibilité

Ex. observé : Akimichi → doton, Uchiha → katon (souvent), Senju → mokuton.

#### kekkei_genkai → carrier_clans (inversion)

`kekkei_genkai.json` n'a pas le champ `carrier_clans` rempli
(0/32). Mais on peut l'inverser depuis les couples observés. Construit
en Pass 1 et écrit en patch sur `kekkei_genkai.json`.

### 2.2 Ponts qui ne marchent pas (et pourquoi)

#### "Même academy class" → birth_year

L'idée intuitive : Naruto, Sasuke, Sakura, Hinata, Ino, Shikamaru,
Choji, Shino, Kiba sont la même class de l'académie de Konoha (an 12),
donc tous nés en l'an 0. C'est exact, et le patch couvre déjà cette
class. Mais pour propager à d'autres "classes" canoniques, il faudrait
une donnée structurée `academy_class_year` ou `team_id` sur chaque
perso. Cette donnée n'existe que dans `wiki_sections` en prose libre
(« Team 7 », « Team Guy », « Team Asuma »).

→ Inexploitable en Pass 1 déterministe. Reporté à Pass 2 LLM.

#### Sensei lineage → âge approximatif

Si Iruka enseigne à un academy_student, son élève a 6-12 ans. Mais
`key_relationships` est à 0% rempli. Le sensei est dans `Background`
en prose. Inexploitable en Pass 1. Reporté à Pass 2.

#### techniques_known_by_era → fenêtre d'activité

Tous à `year=18`. Inutilisable pour fenêtrer.

#### Parents/enfants → fenêtre d'âge

Pareil, `key_relationships` vide. Reporté à Pass 2.

### 2.3 Bilan ponts

| Pont | Déterministe ? | Estimation gain |
|---|---|---|
| clan → KG (éligibilité) | Oui | ~269 persos enrichis |
| clan → natures (éligibilité) | Oui | ~291 persos enrichis |
| KG → carrier_clans (inversion) | Oui | 32 entrées KG complétées |
| 4 clans déjà patchés sur `key_kekkei_genkai` | Oui | propagation aux autres membres |
| academy class → birth_year | Non, prose | ~50-100 persos via Pass 2 |
| sensei lineage → birth_year window | Non, prose | ~50-100 via Pass 2 |
| parents/enfants → birth_year window | Non, prose | ~30-80 via Pass 2 |
| `Background` Part I/II datés → birth_year exact | Non, prose | ~600-800 via Pass 2 |

Verdict : le déterministe pur n'apporte que de l'éligibilité KG/nature.
Aucun nouveau `birth_year` ni `death_year`. Pour combler le creux
8.2% → cible 70-90%, il faut Pass 2 LLM.

---

## 3. Pass 1 (dérivation symbolique pure)

### 3.1 Architecture proposée

Un script `scripts/derive_canon_eligibility.py` qui :

1. Charge `characters.json` + `character_birth_years_patch.json`
2. Construit deux dictionnaires empiriques :
   - `clan_to_eligible_kgs: dict[str, set[str]]` depuis tous les
     `(clan, kekkei_genkai)` connus
   - `clan_to_eligible_natures: dict[str, set[str]]` depuis tous les
     `(clan, natures)` connus
3. Construit l'inversion :
   - `kg_to_carrier_clans: dict[str, set[str]]`
4. Pour chaque perso avec un `clan` mais sans `kekkei_genkai` :
   ajoute un champ `eligible_kekkei_genkai: list[str]` (lecture seule,
   séparé de `kekkei_genkai` qui reste la possession effective)
5. Pour chaque perso avec un `clan` mais sans `natures` :
   ajoute un champ `eligible_natures: list[str]`
6. Pour chaque KG dans `kekkei_genkai.json` : remplit `carrier_clans`
   depuis l'inversion observée
7. Patch direct dans `data/canonical/` (sortie commitable)

### 3.2 Règles concrètes par champ

#### Règle KG.1 : éligibilité KG par clan

```
Pour chaque (clan_id, kg_id) observé dans characters.json :
  clan_to_eligible_kgs[clan_id].add(kg_id)

Pour chaque perso ayant clan_id mais pas de kekkei_genkai :
  perso.eligible_kekkei_genkai = list(clan_to_eligible_kgs[clan_id])
```

#### Règle KG.2 : carrier_clans par inversion

```
Pour chaque (clan_id, kg_id) observé :
  kg_to_carrier_clans[kg_id].add(clan_id)

Pour chaque KG dans kekkei_genkai.json :
  if kg_to_carrier_clans[kg.id]:
    kg.carrier_clans = list(kg_to_carrier_clans[kg.id])
```

#### Règle Nature.1 : éligibilité nature par clan

```
Identique à KG.1 sur le champ natures.
```

#### Règle Clan.1 : `key_kekkei_genkai` propagé sur clans.json

```
Pour chaque clan dans clans.json où key_kekkei_genkai est vide :
  if clan_to_eligible_kgs[clan.id]:
    clan.key_kekkei_genkai = list(clan_to_eligible_kgs[clan.id])
```

#### Règle Clan.2 : `key_natures` propagé

```
Identique sur key_natures.
```

### 3.3 Estimation des gains Pass 1

| Sortie | Gain |
|---|---|
| `eligible_kekkei_genkai` ajouté | ~85 persos (= 269 - 184 déjà avec KG) |
| `eligible_natures` ajouté | ~278 persos (= 291 - 13 sans nature) |
| `clans.json.key_kekkei_genkai` complété | ~30 clans (33 observés - 4 déjà remplis) |
| `clans.json.key_natures` complété | ~27 clans |
| `kekkei_genkai.json.carrier_clans` complété | ~32 entrées |

Pas de nouveau `birth_year` ni `death_year` au Pass 1.

### 3.4 Ce que Pass 1 ne résout pas

- 1248 persos sans `birth_year`
- 1313 persos sans `death_year`
- 0 perso avec un `rank_progression` ou `key_relationships` réel

→ Tout cela passe en Pass 2.

---

## 4. Pass 2 (extraction LLM ciblée par perso)

### 4.1 Stratégie

**Pas un batch global sur les 17k chunks RAG.** L'info pertinente pour
compléter un perso vit principalement dans **ses propres
`wiki_sections`** (déjà localisées par perso). On évite ainsi le coût
d'un retrieval global et la pollution par d'autres entités.

Pour chaque perso avec birth_year manquant :
1. Concaténer ses sections `Background`, `Part I`, `Part II`, `New Era`,
   `Abilities`, `Personality`, `Quotes`
2. Limiter à ~6KB (les très longs comme Naruto auront leur Background
   tronqué proprement, sufficient pour extraire les facts)
3. Run un appel LLM avec un schema d'extraction structuré
4. Stocker le résultat dans un fichier patch séparé
   `data/canonical/character_full_patch.json` (similaire au
   birth_years_patch existant mais sur tous les champs)

### 4.2 Schema d'extraction (JSON Schema constraint)

```json
{
  "extracted": {
    "birth_year": "int | null (relative à an 0 = naissance Naruto)",
    "death_year": "int | null",
    "death_arc": "string | null",
    "academy_class_year_approx": "int | null",
    "team_name": "string | null (Team 7, Team Guy, Team Hebi, etc.)",
    "sensei_id": "string | null (slug)",
    "sensei_team_lead": "string | null",
    "parents": "list[string] (slugs ou names)",
    "children": "list[string]",
    "siblings": "list[string]",
    "spouse": "string | null",
    "rank_progression": "list[{rank, year_approx, source_chunk}]",
    "kekkei_genkai_explicit": "list[string]",
    "natures_explicit": "list[string]",
    "first_appearance_arc": "string | null",
    "confidence": "low | medium | high"
  },
  "extractor_notes": "1-2 phrases de justification ou de doute"
}
```

### 4.3 Choix du modèle : Haiku 4.5

**Décision** : Haiku 4.5 (`claude-haiku-4-5-20251001`).

**Justification** :
1. **La tâche est de l'extraction d'info explicite**, pas du raisonnement
   profond. Le LLM lit "Itachi was born when Sasuke was 5 years younger"
   et extrait `birth_year = -7`. C'est en grande partie copier-coller
   structuré, terrain de jeu naturel pour Haiku.
2. **Volume** : ~1250 calls, chaque ~6KB input + ~500 tokens output.
   Coût estimé Haiku 4.5 : ~$6 total. Sonnet 4.6 serait ~$23, soit 4x
   pour un gain marginal sur cette tâche.
3. **Mode dégradé acceptable** : si Haiku rate certains cas tordus, le
   field sort en `null` avec confidence=`low` et le perso retombe en
   Pass 3 (skip checks). Pas de pollution par hallucination si on tient
   le schema strict.
4. **Iteration rapide** : à $6 le batch, on peut relancer plusieurs
   fois en ajustant les prompts.

**Escalade Sonnet 4.6** : sur le sous-ensemble des 50 persos centraux
(tous les Hokage, Sannin, Akatsuki principaux, Kage des autres villages,
top 50 par taille de wiki_sections). On revérifie leur extraction Haiku
avec Sonnet et on garde la version la plus complète. Coût ~$2 supplémentaires.

### 4.4 Ordre d'exécution Pass 2

1. **Run Haiku** sur les 1248 persos sans `birth_year` post-patch
2. **Validation manuelle spot-checks** : 30 persos tirés aléatoirement,
   vérifier les extractions vs leurs wiki Narutopedia
3. **Run Sonnet** sur les top-50 persos centraux pour cross-check
4. **Merge** dans `character_full_patch.json` avec confidence flags
5. **Apply patch** au runtime (loader Pydantic peut ingérer le patch)

### 4.5 Estimation gains Pass 2

| Champ | Couverture estimée post-Pass 2 |
|---|---|
| birth_year (high+medium confidence) | 70-85% (~950-1150) |
| death_year | 30-45% (très souvent non explicite) |
| team / sensei | 40-55% (~600 persos sont des élèves nommés) |
| rank_progression | 50-65% (Background + Part I généralement clairs) |
| kekkei_genkai_explicit | + 10-15% au-dessus de Pass 1 |
| natures_explicit | + 15-20% |

### 4.6 Coût et durée estimés

- Haiku 4.5 input : 1250 × 6000 tokens × $0.80/M = **$6.0**
- Haiku 4.5 output : 1250 × 500 tokens × $4.0/M = **$2.5**
- Sonnet 4.6 sur top-50 : 50 × 6000 × $3/M + 50 × 500 × $15/M ≈ **$1.3**
- **Total estimé : ~$10**
- Durée : ~30-45 min en parallèle sur l'API Claude

---

## 5. Pass 3 (gestion des unknowns résiduels)

Estimé 200-400 persos même après Pass 2 :
- Persos très mineurs (filler), où Background est court ou absent
- Persos pré-existence avec age impossible à dater (Six Path Sage, etc.)
- Persos Boruto-era récents avec timeline encore floue dans le canon

### 5.1 Flag explicite

Ajouter au schema `Character` :
```python
class Character(_Frozen):
    ...
    birth_year_known: bool = False  # True si birth_year vient de canon ou patch
    birth_year_source: Literal["canon", "patch", "llm_high", "llm_medium", "llm_low", "unknown"] = "unknown"
```

### 5.2 Comportement attendu côté validators

Pour les piliers déjà implémentés (§3 et §4), le comportement actuel
est **déjà compatible** avec les unknowns :

- `get_canon_status` retourne `unknown` quand `birth_year is None`
- Couche A (sherlock_rules) : skip silencieux (ne reject pas)
- Couche C (age_coherence) : skip silencieux (`get_age` raise puis caught)
- Le validator les laisse passer comme des PNJ génériques

C'est défensif et correct. Le seul vrai trade-off : un perso canon mort
(ex: Hashirama) qui n'aurait pas de `death_year` extractible passerait
comme alive à n'importe quelle date. Mitigation :
- Pass 2 capture la majorité de ces cas
- Pour les persos clairement morts à toutes les époques jouables (an 0+),
  ajouter un `is_legacy_dead: bool` flag manuel (~30 cas, table petite)

### 5.3 Fallback éligibilité clan

Pour les checks indirects, on s'appuie sur les `eligible_*` du Pass 1.
Ex : "ce perso a-t-il accès au Sharingan ?" → check Pass 1
`eligible_kekkei_genkai`. Plus nuancé que le binaire possession/non.

### 5.4 Stratégie d'évolution dans le temps

Plutôt que de viser la perfection en un coup, accepter que la
complétion canon est itérative. Chaque playtest qui révèle un perso
mal géré → ajout au patch ciblé manuellement, ou re-run Pass 2 ciblé
sur ce perso avec un meilleur prompt.

---

## 6. Implications pour les piliers existants

### 6.1 Validator §3 (couches A et C)

Comportement avant complétion canon :
- Reject seulement les ~112 persos patches connus s'ils sont morts
- Laisse passer les 1248 inconnus comme s'ils étaient vivants
- C'est **défensif** : moins de faux-positifs (ne reject pas un PNJ par
  ignorance), mais beaucoup de **faux-négatifs** (laisse passer un
  Hashirama qui parle en l'an 12, parce qu'on ne sait pas qu'il est
  mort).

Comportement attendu après Pass 1+2+3 :
- Reject sur ~70-85% des persos sur birth/death (Pass 2)
- Skip silencieux sur les 15-30% résiduels (Pass 3 flagge explicitement)
- Toujours pas de hallucination par excès de zèle

### 6.2 State tracker §4 (`get_age`, `get_canon_status`)

Pas de changement d'API requis. La fonction `get_canon_status` retourne
déjà `unknown` quand `birth_year is None`, ce qui est le comportement
attendu post-complétion. Le seul ajout : possibilité de lire
`birth_year_source` pour distinguer "canon hard fact" de "LLM-derived
medium confidence" si on veut un mode validator strict-only-canon.

### 6.3 Pilier §5 (re-tagging temporel) : prérequis confirmé

Le tagging des 17k chunks suppose qu'on peut attribuer un
`year_min`/`year_max` à un chunk en se basant sur :
- arc explicite mentionné
- mention de personnages avec birth_year connu

À 8.2% de couverture birth_year, le second levier est cassé : un chunk
qui mentionne uniquement des persos secondaires (la majorité) n'a aucun
ancrage temporel. **Pass 2 minimum requis avant §5**.

---

## 7. Plan d'exécution proposé

| Étape | Output | Coût | Durée |
|---|---|---:|---:|
| Pass 1 (dérivation déterministe) | patch sur clans.json, kekkei_genkai.json, characters.json (eligible_*) | $0 | <1 min run |
| Spot-check Pass 1 | rapport markdown | $0 | 15 min lecture |
| Pass 2 Haiku 4.5 sur 1248 persos | character_full_patch.json | ~$8.5 | ~45 min |
| Spot-check Pass 2 (30 persos random) | rapport markdown + corrections de prompts | $0 | 30 min lecture |
| Pass 2 Sonnet 4.6 sur top-50 centraux | merge dans patch | ~$1.3 | ~5 min |
| Mise à jour schema Pydantic (eligible_*, birth_year_source) | code | $0 | 10 min |
| Tests de non-régression sur §3 et §4 | tests verts | $0 | 15 min |
| **Total** | canon enrichi de ~70-85% sur birth_year | **~$10** | **~2h** |

Une fois validé, démarrage du **§5** (re-tagging temporel des 17k
chunks) sur la base canon enrichie.

---

## 8. Cas non-résolus estimés

Sur 1360 persos, après Pass 1+2 :

| Catégorie | Estimation |
|---|---:|
| Persos avec birth_year canon ou high-confidence LLM | ~950-1150 |
| Persos avec birth_year medium-confidence LLM | ~100-150 |
| Persos sans birth_year exploitable (filler, very minor) | ~80-150 |
| Persos atemporels (Six Paths Sage, divinités) | ~20-30 |

Soit 100-180 persos en **Pass 3** (skip silencieux). Acceptable pour un
playtest, calibrable au fil des sessions.

---

## 9. Questions ouvertes pour validation

Avant d'implémenter Pass 1 puis Pass 2, je veux ton aval sur :

1. **Le choix Haiku 4.5 vs Sonnet 4.6** pour Pass 2. Tu veux que je
   parte sur Haiku partout (pas de top-50 Sonnet), Haiku + cross-check
   Sonnet (mon plan), ou Sonnet partout ?
2. **Le schema d'extraction Pass 2** : ai-je oublié des champs que tu
   veux capturer ? (ex: `personality_archetype`, `key_traits`,
   `signature_jutsu`)
3. **Le flag `birth_year_source`** : utile pour toi en mode strict
   (validator qui ne fait confiance qu'au canon hard) ? Ou over-engineered ?
4. **Les patches existants à 100 entrées orphelines** : on investigue
   pourquoi ils ne matchent pas et on les nettoie maintenant, ou on
   reporte ?
5. **Le `is_legacy_dead`** : OK pour cette table manuelle de ~30 persos
   morts pré-an-0 (Hashirama, Madara avant Edo Tensei, Senju and co) ?
   Ou on attend que Pass 2 les capture ?
