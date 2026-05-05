# Pass 2 — Post-mortem du batch test 50

> Diagnostic complet du run du batch 50 sur Groq gpt-oss-120b. Quatre
> findings, dont un beaucoup plus grave que les pannes Type 1/2.
> Aucun relancement tant que les fixes ne sont pas appliques.

---

## 0. TL;DR

1. **Type 1 (`json_validate_failed`) = concurrence instable**, pas la
   longueur d'output. Reproduit 3/3 en sequentiel : Hashirama, Madara,
   Hiruzen reussissent tous quand l'appel est isole. Failed_generation
   vide dans les rejets sous concurrence.
2. **Output cap 2000 tokens insuffisant** : Hiruzen genere 3190 tokens
   en sortie complete, Madara 2216. Le cap 2000 a probablement tronque
   certains outputs en plus de l'instabilite concurrence.
3. **Type 2 (HTTPError vide) = logging insuffisant** : str(exception)
   vide pour les timeouts httpx. Manque les status code + headers +
   body complet pour diagnostiquer.
4. **(LE PLUS GRAVE) Schema massivement sous-extrait sur les succes** :
   `response_format: json_object` chez Groq valide la SYNTAXE JSON,
   pas le SCHEMA. Le modele sort des JSON valides mais avec une
   fraction des champs requis. Naruto Groq = 9/21 fields, Itachi
   Groq = 2/21 fields, vs 21/21 chez CC. **Tout le schema riche
   (parents, sensei, jinchuuriki, etc.) est skippe par le modele**.
5. **Violations regle 11 (id format) sur 6/27 succes** : modele invente
   des prefixes clan (`chinoike_hidan`), confond role et clan
   (`kazekage_kankuro`), ou utilise format non-canonique
   (`white_zetsu`, `killer_b`). Bug structurel : la regle 11 imposait
   `clan_first` mais beaucoup d'ids canon ne suivent pas cette regle.
6. **Misses = paraphrase, pas typo** : 13/91 quotes failed avec
   edit_distance 10-33. Le modele paraphrase au lieu de copier
   verbatim.

**Conclusion** : pas un probleme de modele insuffisant, mais des
problemes cumulatifs de prompt + script + Groq json_object mode. Tous
fixables.

---

## 1. Type 1 — `json_validate_failed` est cause par la concurrence

### 1.1 Evidence

Dans le batch parallele (max_concurrency=10), 16/50 ont rendu cette
erreur :
```
HTTP 400: {"error":{"message":"Failed to validate JSON. Please adjust
your prompt. See 'failed_generation' for more details.",
"type":"invalid_request_error","code":"json_validate_failed",
"failed_generation":""}
```

Failed_generation **vide** : Groq ne renvoie meme pas le content qu'il
a genere. Suspect.

Tests sequentiels avec le meme prompt et le meme modele :

| char_id | max_tokens | Status | content_length | completion_tokens | JSON parse |
|---|---|---|---|---|---|
| `senju_hashirama` | 2000 | 200 | 1965 | 2000 | OK |
| `uchiha_madara` | 4000 | 200 | 2331 | 2216 | OK |
| `sarutobi_hiruzen` | 4000 | 200 | 5167 | 3190 | OK |

Les 3 reussissent en sequentiel. Donc la concurrence est le coupable.

### 1.2 Hypothese

Sous concurrence, le strict json validator de Groq (cote serveur,
implemente quand `response_format: json_object` est passe) a une
race condition ou rate limit interne qui drop le content du modele
et leve `json_validate_failed` avec failed_generation vide. Ce n'est
pas documente dans la doc Groq officielle.

### 1.3 Fix propose

- **`max_concurrency=3`** au lieu de 10. Marge de securite.
- **Retry sur HTTP 400 json_validate_failed** : si on hit l'erreur,
  attendre 2-5s puis retry. Si succes au retry, on confirme l'hypothese.

---

## 2. Output cap 2000 tokens insuffisant

### 2.1 Evidence

Sur les 27 succes, presque tous ont `out=2000` (cap atteint).
Distribution des `completion_tokens` :

```
out=2000  : 22/27 succes  (cap atteint)
out=1845  : fu
out=1916  : sasori
out=1918  : deidara
out=2000  : autres
```

22/27 cas ont hit le cap, ce qui suggere que le modele voulait generer
plus mais a ete tronque. Parmi les outputs cap-hit, certains ont reussi
le JSON parse par chance (le cap est tombe pile a un point parseable),
d'autres ont probablement contribue aux 16 Type 1.

Test sequentiel sur Hiruzen avec max_tokens=4000 : 3190 tokens
generes, JSON complet, 5167 chars de content riche (vs 1500-2000 chars
des outputs Groq cap-hit).

### 2.2 Fix propose

- **`max_tokens=4000`**. Coute potentiellement 2x plus en output
  ($0.60/M × 2 = $1.20/M sur l'output) mais reste largement sous le
  budget (cf. estimation recoutee plus bas).

Recoutage worst-case avec max_tokens=4000 sur 1359 persos :
- Output : 1359 × 4000 × $0.60/M = **$3.26**
- Input : ~$0.50 (inchange)
- Total worst case : ~$3.80, sous la limite $5

---

## 3. Type 2 — HTTPError sans message

### 3.1 Evidence

7/50 ont produit `HTTPError: ` (str vide) dans le log. Le script attrape
`httpx.HTTPError` et fait `f"HTTPError: {exc}"`. Pour certaines
exceptions httpx (notamment `ReadTimeout`, `ConnectTimeout`), `str(exc)`
est vide ou tres laconique.

Concentration en fin de batch :
- zetsu_black, hyuga_hinata (entre 30 et 31)
- namikaze_minato, nohara_rin (35-36)
- uchiha_obito, jugo, otsutsuki_isshiki (38-40)
- haku (44)

Pattern : 4 d'affilee entre les positions 35-40, suggere un transient
rate limit ou un blip reseau.

### 3.2 Fix propose

- Capturer le `type(exc).__name__` ET `repr(exc)` ET les attributs
  specifiques (`exc.request.url`, `exc.response.headers` si dispo).
- **Retry sur ReadTimeout / ConnectTimeout / ConnectError avec
  exponential backoff** (1s, 2s, 4s, max 3 retries).

---

## 4. (CRITIQUE) Schema massivement sous-extrait

### 4.1 Evidence

Cross-check sur les persos qui ont reussi en Groq ET dans le dry-run CC :

**Naruto** :

| Field | CC | Groq |
|---|---|---|
| birth_year | 0 | null |
| is_jinchuuriki | true | null |
| tailed_beast | kurama | null |
| parents | [minato, kushina] | (champ absent) |
| children | [boruto] | (champ absent) |
| sensei_id | hatake_kakashi | (champ absent) |
| team_name | team_7 | "Team 7" (NON-snake_case !) |
| Total fields with quotes | 12/21 | 2/9 |

**Itachi** :

| Field | CC | Groq |
|---|---|---|
| kekkei_genkai_possessed | [sharingan, mangekyo_sharingan] | (champ absent) |
| parents | [mikoto, fugaku] | (champ absent) |
| team_name | team_2 | (champ absent) |
| Total fields with quotes | 8/21 | 0/2 |

Le modele Groq sort des JSON SYNTAXIQUEMENT valides mais qui omettent
12-19 champs sur 21 du schema. Il s'arrete apres avoir rempli quelques
champs.

### 4.2 Cause

`response_format: {"type": "json_object"}` chez Groq impose juste que
la sortie soit un objet JSON parseable. Il ne valide PAS que tous les
champs du schema sont presents. Le modele decide librement quels champs
inclure.

Ajoute a ca :
- Le system prompt actuel decrit le schema mais ne dit pas explicitement
  "EVERY field is REQUIRED in your output, even with null".
- Le modele economise des tokens en omettant les champs qu'il juge
  non-applicables. C'est rationnel de son point de vue mais ca casse
  la spec.

### 4.3 Fixes proposes

**A. Renforcer le system prompt** :
```
RULE 13 (NEW): SCHEMA COMPLETENESS. Every field listed in the OUTPUT
SCHEMA MUST appear in your output, in the same order, with value=null
when you have no fact. Empty lists ([]) when no list items apply.
DO NOT skip fields under any circumstance. The schema is a contract :
the validator will reject any output missing a field.
```

**B. Tester `response_format: {"type": "json_schema", "json_schema": {...}}`** :
Groq documente le support de json_schema strict pour certains modeles
(Llama-3.3 confirme, gpt-oss-120b a verifier). Si supporte, ca force
le modele a remplir tous les champs.

**C. Validation post-extraction sur completeness** :
Le validateur Python rejette tout output qui a moins de N champs (par
ex N=15/21) avec un flag `incomplete_schema`. Forcer le retry du
modele dans ce cas.

---

## 5. Violations regle 11 (id format)

### 5.1 Evidence

6/27 outputs Groq ont un `character_id` non-canonique :
- `killer_b` (au lieu de `b_killer`)
- `chinoike_hidan` (clan **invente** : Hidan n'est pas Chinoike)
- `kazekage_kankuro` (Kankuro n'est PAS Kazekage)
- `uzumaki_nagato` (Nagato est canon Uzumaki MAIS l'id du dataset est `nagato`)
- `kurenai_yuhi` (au lieu de `yuhi_kurenai`)
- `white_zetsu` (au lieu de `zetsu_white`)

### 5.2 Cause

La regle 11 du system prompt impose `clan_lastname_firstname` partout.
Mais beaucoup d'ids canon dans `characters.json` n'ont **pas** de clan
prefix : `nagato`, `gaara`, `temari`, `kankuro`, `darui`, `karin`,
`konan`, `tenten`, `mitsuki`, `roshi`, `fu`, `kimimaro`, `hidan`,
`kakuzu`, `deidara`, `sasori`, `haku`, etc. La regle est mal alignee
avec les conventions du dataset.

Le modele applique la regle "trop" et invente des prefixes clan, ou
confond clan avec role (kazekage_kankuro).

### 5.3 Fix propose

**Reecrire la regle 11** :
```
RULE 11 (REVISED): CHARACTER ID FORMAT. Use canonical character ids
from characters.json. The format varies by character :
- Characters with a known clan : "clan_lastname_firstname" lowercase
  snake_case (e.g. "uchiha_sasuke", "senju_hashirama").
- Characters without a clan in their canonical id : just the
  lowercase romaji of their name (e.g. "nagato", "gaara", "konan",
  "deidara", "kankuro").
- DO NOT invent clan prefixes. DO NOT confuse roles (Kazekage,
  Hokage) with clans.
- If unsure : use the lowercase romaji of the character's full name
  with underscores, with the clan name FIRST if there is one.

When in doubt, the character_id at the root of YOUR output should be
EXACTLY the char_id provided in the user message. Do not modify it.
```

**Et fix script** :
- Le script doit verifier que `extraction.character_id == request_char_id`.
  Si different, log et reject.

---

## 6. Misses par paraphrase

### 6.1 Evidence

13/91 quotes failed le grep avec `edit_distance` 10-33. Echantillon :

| Field | quote (Groq) | source (probable) |
|---|---|---|
| `[deidara] team_members.sasori` ed=13 | "Deidara ... partnered with Sasori" | "Deidara was the partner of Sasori" |
| `[fu] is_jinchuuriki` ed=13 | "At some point, Fú became the jinchūriki..." | "At some point, Fū became the jinchūriki..." |
| `[kakuzu] suiton` ed=33 | "Kakuzu was able to use water-based techniques that didn't stem fro..." | (probable paraphrase de "Kakuzu's masks could use ...") |
| `[sasori] team_members.Deidara` ed=10 | "Sasori's second partner in Akatsuki, Deidara" | (probable substring different) |

Pattern : le modele paraphrase ou abridge au lieu de copier la quote
mot a mot. Le `Fú` vs `Fū` est juste une typo Unicode (1 char), pas une
paraphrase, donc ed=13 vient probablement de plus que ca.

### 6.2 Cause

Le system prompt regle 2 dit "verbatim source_quote" mais le modele
gpt-oss-120b est moins rigoureux sur ce point que CC. Probablement
parce que le mode strict json_object encourage la concision (le modele
veut economiser des tokens).

### 6.3 Fix propose

**Renforcer la regle 2** :
```
RULE 2 (REVISED): SOURCE_QUOTE STRICT VERBATIM. The source_quote MUST
be a CHARACTER-FOR-CHARACTER substring of the source text. Do NOT :
- Paraphrase or rephrase
- Combine two sentences into one
- Skip words for brevity
- Replace special characters (e.g. "Fū" must stay "Fū", not "Fu")

If the relevant fact is spread across multiple sentences, pick the
single sentence that most directly states it. If the source uses
unusual punctuation (em dashes, typographic apostrophes), copy them
EXACTLY as they appear.

A grep validator will check every quote against the source. Any quote
not found verbatim will mark the field as low_confidence.
```

---

## 7. Plan d'action propose

### 7.1 Patches script (`scripts/pass2_extract_canon.py`)

1. `max_tokens=4000` (au lieu de 2000)
2. `max_concurrency=3` (au lieu de 10)
3. Helper `make_request_with_retry()` qui fait :
   - 3 retries max sur ReadTimeout/ConnectTimeout/ConnectError
   - 2 retries max sur HTTP 400 `json_validate_failed`
   - Exponential backoff 1s, 2s, 4s
4. Logging detaille :
   - `type(exc).__name__` + `repr(exc)`
   - Status code, headers x-ratelimit-*, x-groq-region
   - Body brut complet pour les erreurs
5. Validation post-extraction :
   - Verifier `extraction.character_id == request_char_id`
   - Compter les fields presents, flag `incomplete_schema` si < 15/21
   - Conserve la grep validation actuelle

### 7.2 Patches system prompt

1. Reecrire **regle 11** (id format flexible, pas clan_first impose)
2. Reecrire **regle 2** (verbatim character-for-character)
3. Ajouter **regle 13** (schema completeness explicite)

### 7.3 Decision modele

- **Premier essai** : gpt-oss-120b avec patches 7.1 + 7.2.
- **Critere de succes** : >= 90% des outputs avec >= 15/21 fields
  remplis ET >= 95% des quotes en exact match.
- **Si echec** : bascule sur **llama-3.3-70b-versatile** chez Groq
  ($0.59/M input, $0.79/M output, soit ~5x plus cher mais reste sous
  $4 sur 1359 persos worst-case).

### 7.4 Tester `response_format: json_schema`

Verifier si Groq supporte `json_schema` strict mode pour gpt-oss-120b
(probablement oui, doc a confirmer). Si oui, c'est probablement le
fix le plus elegant pour le probleme #4 (schema completeness force
cote serveur).

### 7.5 Cleaning

- Effacer `data/canonical/_pass2_output/` (les 27 outputs sont a
  refaire avec les patches).
- Garder `_pass2_run.log` et `_pass2_debug/` comme archives.
- Garder `_pass2_output_dryrun/` (CC, sert de reference qualite).

---

## 8. Coût brule jusqu'ici

- Batch test 50 : $0.0578
- Diagnostic (3 calls) : ~$0.01

Total : ~$0.07. Largement sous le budget $5 hard limit.

---

## 9. Note securite : cle Groq exposee

La cle `gsk_REDACTED_OLD_KEY_ROTATE_ME`
etait presente en clair dans `.env.example` (visible via l'IDE selection
fournie). Si ce fichier est dans le repo public ou partage, **rotate
la cle immediatement** apres validation des fixes. `.env.example`
devrait contenir `gsk_xxx_REPLACE_ME`, jamais une vraie cle.

---

## 10. Decision attendue

Avant de patcher le script, confirme :

1. **OK pour les patches 7.1 + 7.2 (script + prompt)** ?
2. **Tester d'abord `json_schema` strict mode** avant de relancer ?
   Si oui, je fais 1 appel debug pour verifier que gpt-oss-120b
   supporte `response_format: json_schema`.
3. **Garder gpt-oss-120b** ou switcher direct sur llama-3.3-70b ?
   Mon avis : gpt-oss-120b a une chance de passer avec les patches,
   commencons par lui ; bascule llama-3.3-70b si echec persistant
   apres patches.
4. **Effacer `_pass2_output/` actuel** (les 27 outputs sont sous-extraits
   et doivent etre refaits) ?
5. **Rotater la cle Groq** apres validation des fixes ?
