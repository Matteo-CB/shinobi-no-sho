# Pass 2 batch postmortem — investigation low filled rate

> Investigation du batch full Pass 2 (1359 outputs Llama-3.3-70b via Groq Batch API)
> apres rollback de clans.json/kekkei_genkai.json. Le batch a tourne, les outputs
> sont valides (Failed=0, Quote exact 94.3%) mais le filled rate global est
> catastrophique (18.9%) et l'agrégation Pass 3 produit des résultats absurdes
> (3 clans canoniques sur 57, 0 Senju avec Mokuton, etc.).

## TL;DR du diagnostic

**Le batch n'est pas tronqué techniquement, Llama est juste trop conservateur.**

- **`finish_reason='stop'` sur 1359/1359** outputs (zero hit du cap 4000 tokens)
- **Output median = 518 tokens** (le cap a une marge énorme)
- **Output max = 1879 tokens** (jamais proche du cap)

Donc Llama-3.3-70b s'arrete spontanement avec un JSON quasi-vide (mostly null)
sur la majorite des persos. Il a applique la regle 3 du system prompt
("NEVER GUESS. If the wiki does not state a fact, the field is null") au
pied de la lettre, et a aussi applique la regle 2 ("source_quote verbatim
obligatoire") en mode hyper-prudent : si une quote n'est pas exacte au
caractere pres, il prefere mettre `null`.

**Le low filled rate a 2 sources distinctes** :
1. **Wikis vraiment pauvres** : 35% des unknowns ont une wiki < 1500 chars,
   59% < 3000 chars. Pas de matiere a extraire pour ces persos.
2. **Llama trop conservateur sur les wikis riches** : sur les 5 persos
   ou on a aussi le dryrun CC, Llama extrait en moyenne **5.6 fields de
   moins que CC** sur les memes wikis. CC a 100% de quote exact, donc
   les fields que CC trouve sont attestes textuellement. Llama les a
   sautes par paresse, pas par absence de donnee.

## 1. Sample 20 unknowns : data extractible non extraite ?

Sur 20 persos `still_unknown` apres Pass 2.5, sample tire aleatoirement
(seed 42). Pour chaque perso : taille wiki, indicateurs birth_year detectes
par regex (born/age/younger/etc.), nombre de fields Llama a remplis.

- **2/20 ont des birth indicators detectes par regex**
- **0/20 ont indicators ET <= 3 fields filled** (pas de cas evident de
  sous-extraction "wiki riche en data ignoree")

**Conclusion sample 20** : le batch n'a pas massivement sous-extrait sur
ces 20 persos. La majorite des unknowns ont effectivement peu de data
canon dans leur wiki (filler/persos secondaires).

## 2. Uchiha sans Sharingan (23 sur 46)

Sur les 23 Uchiha qui n'ont **pas** Sharingan dans `kekkei_genkai_possessed`,
sample de 10 :

- **2/10 ont >= 3 mentions du mot "Sharingan" dans leur wiki**, donc
  potentielle sous-extraction sur ces 2.
- **8/10 ont 0-2 mentions**. Ce sont des Uchiha mineurs/filler/civils
  qui n'utilisent canoniquement pas le Sharingan ou n'apparaissent pas
  en combat (Setsuna, Tekka, Yashiro, Uruchi, etc.).

**Conclusion Uchiha** : le ratio "Sharingan attestes / membres Uchiha"
de 23/46 = 50% reflete partiellement la realite canon : tous les Uchiha
ne developpent pas le Sharingan (necessite trauma + age). Les 2 cas
sous-extraits sont marginaux.

## 3. Senju sans Mokuton (7 sur 9)

Sur les 7 Senju qui n'ont pas Mokuton dans `kekkei_genkai_possessed`,
sample de 5 :

- **0/5 ont une mention "Wood Release" ou "Mokuton" dans leur wiki**
- Ce sont des Senju mineurs (Butsuma, Itama, Kawarama, Nawaki, Toka)
  qui n'ont **canoniquement pas** le Mokuton (seuls Hashirama et Tobirama,
  via greffe, possedent le Wood Release dans le canon original).

**Conclusion Senju** : le 7/9 sans Mokuton REFLÈTE le canon. Le Mokuton
n'est PAS un trait du clan Senju, c'est une mutation specifique a
Hashirama. L'algo Pass 3 echoue ici parce qu'il ne devrait pas
attribuer Mokuton au clan : le canon est correct.

## 4. Cross-check CC dryrun vs Llama batch (5 persos communs)

Pour les 5 persos extraits manuellement par CC dans le dryrun (qualite
100% grep), comparaison avec l'extraction Llama du batch full :

**Delta moyen : -5.6 fields** (Llama < CC sur les memes wikis).

C'est la **vraie sous-extraction Llama** : sur des wikis riches, Llama
loupe en moyenne 5.6 fields que CC a su trouver. Causes probables :

- Llama applique la regle 3 trop strictement : "If the wiki does not
  STATE the fact, null". CC est plus pragmatique : il fait des inferences
  legeres (ex : "born during the Warring States Period" -> peut calculer
  birth_year approximation).
- Llama applique la regle 2 (source_quote verbatim) en mode panique :
  si la quote possible n'est pas un substring contigu parfait, il prefere
  null que `confidence: low`.
- Probable effet "low energy" du temperature=0.0 : pas d'incitation a
  explorer.

## 5. 67 not_run_through_pass2_5

Pas 33 comme initialement annonce, c'est en fait **67 fichiers `.json` dans
`_pass2_output/`** sans `extraction_metadata.birth_year_source`.

Diagnostic : le bug est probablement que le character_id du JSON ne
matche pas le filename pour ces 67 fichiers, ou que `extraction_metadata`
manque dans le JSON Llama original (Llama a omis le champ malgre le
schema).

**Action requise** : patcher `pass2_5_derive.py` pour :
1. Iterer sur les filenames (et non les character_ids du JSON)
2. Toujours setter `birth_year_source` meme si `extraction_metadata`
   est absent du JSON original

## 6. Token usage : Llama paresseux, pas tronque

Stats `usage` du batch :

```
Output tokens distribution :
  median   : 518
  p75      : 656
  p90      : 862
  p99      : 1357
  max      : 1879

Cap-hit (finish_reason='length' OU out_tok >= 3950) : 0 / 1359 (0.0%)
finish_reasons : {'stop': 1359}
```

**Aucun output n'a touche le cap 4000.** Le cap n'est pas le probleme.
Le probleme est que **Llama produit des sorties trop courtes**. Le
schema vide rempli avec `null` partout fait deja ~400-500 tokens.
Median 518 = grosso modo le schema avec 1-2 fields filled.

## 7. Wiki size distribution (1281 still_unknown)

```
median   : 2,240 chars
p25      : 1,124 chars
p75      : 4,890 chars
p90      : 10,450 chars
max      : 46,863 chars

pct < 1500 chars : 35.4%
pct < 3000 chars : 58.9%
pct < 6000 chars : 80.2%
```

35% des unknowns ont un wiki < 1500 chars (vraiment pauvre, 1-2 paragraphes
type Appearance + une ligne Background). Pour ceux-la, **il n'y a rien
a extraire**, le low filled rate est legitime.

Mais 41% des unknowns ont >= 3000 chars de wiki. Sur ces ~525 persos,
il y avait probablement plus a extraire. C'est la zone ou Llama a
sous-extrait.

## 8. Distribution des 11 chars avec birth_year extrait

Top 4 sont les wikis méga-riches :
- uzumaki_naruto : 46,237 chars (derived)
- hatake_kakashi : 39,541 chars (derived)
- uchiha_itachi : 31,207 chars (derived)
- uzumaki_himawari : 20,659 chars (llm_extracted)

Plus 7 persos secondaires avec birth_year canon explicite dans la wiki
(akado_manabu, kamano_saisu, etc., wikis 1.3K-9.5K chars).

**Que les top 5 sont des wikis riches** (>20K chars). Les autres ont
des birth_year canon explicite ("born in year X" en clair dans la wiki).
Les chars top-50 (Sasuke, Madara, Hiruzen, Hashirama, Gaara, Minato,
Obito, etc.) **ne sont pas dans la liste alors qu'ils ont des wikis
riches**, ce qui confirme la sous-extraction massive sur les top-50.

## Conclusions et recommandations

### Diagnostic tranche

1. **Le batch lui-meme a marche techniquement** (1359/1359 OK, quote
   exact 94.3%, no errors).

2. **Llama-3.3-70b est trop conservateur** sur les wikis riches.
   Confirmation par le delta CC -5.6 fields/perso. Le system prompt
   actuel (regles 2 et 3 strictes) le pousse a `null` quand un humain
   pragmatique extrairait avec confidence=low.

3. **Les wikis pauvres existent reellement** : 35% < 1500 chars.
   Pour ceux-la, le batch est correct, il n'y a rien a extraire.

4. **L'algo Pass 3 d'agregation est trop strict** pour le canon Naruto.
   Le seuil 50%+3 ne marche pas pour les attributs heritables-mais-non-
   garantis (Sharingan : tous les Uchiha sont eligibles mais pas tous
   l'eveillent). Mokuton n'est PAS un trait du clan Senju et le canon
   est correct sur ce point. Il faut distinguer :
   - **Trait clan canonique** (Byakugan pour Hyuga, signature) : >= 50%+3
   - **Eligibilite clan** (Sharingan pour Uchiha, accessible) : >= 30%+3
     ou simplement >= 3 membres attestes
   - **Cas isole** (Mokuton pour Hashirama, mutation individuelle) :
     ne pas attribuer au clan

5. **Bug script Pass 2.5** : 67 fichiers sans `birth_year_source`. Patch
   simple a faire.

### Options pour avancer

**Option A — Re-run Pass 2 avec prompt revise (~$2 cout)**

Modifier le system prompt :
- Regle 3 : "If the wiki does not STATE the fact, prefer confidence=low
  over null" (au lieu de "always null").
- Regle 2 : "source_quote MUST be a substring. If the closest match has
  paraphrase, output it with confidence=low and a [paraphrased] tag."
- Regle 14 nouvelle : "Aim to extract aggressively. The downstream
  validator will downgrade hallucinations. Better to flag many
  confidence=low extractions than miss real facts."

Probable amelioration : +30-40% fields filled estime.

**Option B — Re-run cible top-100 (~$0.50 cout)**

Garder le batch actuel comme baseline et re-runner uniquement les ~100
persos avec wiki > 5000 chars, avec le prompt aggressif option A.

**Option C — Task decomposition (~$10 cout)**

5 mini-prompts par perso (identity, abilities, team, events, status),
batch separe par sub-task. 5x plus d'appels mais chacun plus focus.

**Option D — Re-aggregation Pass 3 avec seuils revises (gratuit)**

Sans nouveau Pass 2, juste reviser pass2_aggregate.py :
- 30%+3 au lieu de 50%+3 pour les attributs "eligibility"
- Distinguer 2 categories : `key_kekkei_genkai` (50%+3) vs
  `available_kekkei_genkai` (>= 3 membres atteste)
- Pas de mokuton attribue a senju, mais pourrait y avoir Sharingan
  attribue a Uchiha en `available`.

### Recommandation

**Sequence D + B**, dans cet ordre :

1. **D d'abord** : re-runner `pass2_aggregate.py` avec seuils plus
   permissifs et 2 categories. Voir si le canon obtenu est utilisable
   sans coût LLM additionnel.

2. **B ensuite** : si D laisse trop de trous sur les top-100 narratifs
   (mesurer combien de top-100 ont un birth_year ou des KG extraits),
   faire la 2eme passe Pass 2 ciblee avec prompt pragmatique.

3. **Patch bug Pass 2.5** (67 not_run) : separe, peu couteux.

Ne pas faire **A** (re-run global) tant qu'on n'a pas teste D + B :
ca eviterait $2.15 de re-cout pour un gain incertain.

## Fichiers concernes

- `data/canonical/_pass2_output/` : 1359 outputs Llama (intacts)
- `data/canonical/_pass2_5_derivation_report.json` : rapport Pass 2.5
- `data/canonical/clans.json` : **ROLLBACK FAIT** depuis backup
- `data/canonical/kekkei_genkai.json` : **ROLLBACK FAIT** depuis backup
- `data/canonical/clans.json.pre_pass2_backup` : conservé
- `data/canonical/kekkei_genkai.json.pre_pass2_backup` : conservé
- `research/scraper-corruption-report.md` : a regenerer apres re-aggregation
- `research/canon-completion-report.md` : a regenerer apres re-aggregation
